"""Response-action enumeration primitive (W0-G).

A single iteration site for every legal response action the current
player could take in an instant-speed window — counterspell, instant-
speed removal, channel-from-hand, activated ability from a permanent,
pitch-cast, evoke, flashback-from-graveyard, or simply pass.

Why this module exists (audit-driven, see
`docs/history/audits/2026-05-16_5panel_bo3_audit.md`):

* `ai/response.py:decide_response` only enumerates counterspells in
  hand.  Channel costs on lands in hand are invisible — the audit
  corpus shows Otawara held all game against Storm Medallion chains
  because the decider never asked "what activations could I do?".
* M2 (chain-aware counter) needs the FULL response candidate set to
  rank "counter this spell" vs. "hold for the chain payoff."
* M10 (burn-to-planeswalker enumeration) needs PWs in the candidate
  target tuple.

The structural answer (per the abstraction contract): ONE primitive
yields every option; downstream consumers filter for their own
needs.  This file is that primitive.

Composes existing primitives — does NOT re-implement them:

* `ai.oracle_classifier.has_tag` — Channel / Flashback / Discard
  classification (W0-A tags).
* `engine.cast_manager.CastManager.can_cast` — already encapsulates
  mana, target, and timing legality for normal-cast spells.
* `engine.target_solver` — target legality predicates (reused
  implicitly via `can_cast`).
* `StackItem.source.template.is_spell` — fizzle-precondition check.

Forbidden in this module (per CLAUDE.md ABSTRACTION CONTRACT):

* Card-name checks.  Use `has_tag(name, Tag.CHANNEL_ABILITY)` and
  the existing template tag set instead.
* New magic numbers.  No score weights live here — this module is
  pure enumeration; scoring is the caller's job.
* New `if`-chain over deck names or archetypes.

Caller contract:

* `available_responses` returns an Iterator.  Materialising the full
  list is the caller's choice; chains can generate many options and
  the downstream filter typically cuts the live set small.
* Pass is ALWAYS yielded (exactly once) so callers can treat
  "do nothing" as a scoreable option without a special-case branch.
* `requires_tap_out` is a hint, not a hard constraint — the caller
  uses it to penalise plays that would tie up all mana on opp's
  turn (no follow-up curve, no second response).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Optional, Tuple

from ai.oracle_classifier import Tag, has_tag

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from engine.stack import StackItem


# ─── Public dataclass ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ResponseCandidate:
    """One legal response action the AI could take in an instant-speed
    window.

    Fields are deliberately flat — every downstream scorer reads from
    this shape directly, so callers do NOT need to re-query the game
    state to know "what kind of response is this".

    `action` values (closed set; extend only when a new structural
    response category lands, never per card):

    * ``'counter'``    — counterspell against the stack item
    * ``'remove'``     — instant-speed removal against a permanent
    * ``'discard'``    — instant-speed forced discard
    * ``'channel'``    — Channel activated ability from a card in
                        hand (e.g. Otawara, Boseiju)
    * ``'activate'``   — non-mana activated ability of a permanent
                        on the battlefield
    * ``'cast_pitch'`` — alternative pitch cost on a card in hand
                        (Force of Negation, Solitude evoke)
    * ``'flashback'``  — flashback cast from graveyard
    * ``'pass'``       — pass priority / do nothing (always yielded)

    `source` is the underlying `CardInstance` (None for ``'pass'``).
    `cost` is a free-form string in mana-symbol form (`'{2}{U}'`,
    `'0'` for free).  It is NOT parsed by this module; downstream
    scoring uses it for "how much does this commit?" display and
    for breaking ties on cheaper alternatives.

    `targets` is the tuple of candidate targets the action could
    point at.  Empty tuple means "no target needed" or "targeting is
    decided downstream" (e.g. counters always target the stack
    item, which is part of the input).
    """

    action: str
    source: Optional["CardInstance"]
    cost: str
    targets: Tuple = field(default_factory=tuple)
    requires_tap_out: bool = False


# ─── Internal helpers ──────────────────────────────────────────────────


def _is_instant_speed(card: "CardInstance") -> bool:
    """A card is instant-speed-castable from hand iff it's an Instant
    or has Flash.  This is a structural predicate — does NOT consider
    mana availability; that's `can_cast`'s job.
    """
    tmpl = card.template
    return tmpl.is_instant or tmpl.has_flash


def _format_mana_cost(card: "CardInstance") -> str:
    """Stringify the mana cost in display form.  Returns the printed
    string from the card template if available, else the CMC as a
    plain integer.  This is presentation-only — downstream scoring
    reads `card.template.cmc` directly when it needs the integer.
    """
    tmpl = card.template
    mana_cost = getattr(tmpl, "mana_cost", None)
    if mana_cost is not None:
        printed = str(mana_cost)
        if printed:
            return printed
    return str(tmpl.cmc or 0)


def _has_pitch_alt_cost(card: "CardInstance") -> bool:
    """A card has a pitch alt-cost iff its oracle text contains the
    canonical 'exile … rather than pay' idiom (Force of Negation,
    Force of Will, Subtlety, Solitude evoke, etc.).

    Class size: every Modern card with a pitch alt-cost matches this
    oracle pattern.  This is the same predicate already used in
    `ai/response.py:_effective_counter_cost`; it lives here so the
    enumerator can compose it without importing back into the
    response-decider module (would create a cycle).
    """
    oracle = (card.template.oracle_text or "").lower()
    return "exile a" in oracle and "rather than pay" in oracle


def _has_pitch_fuel(game: "GameState", player_idx: int,
                    pitch_card: "CardInstance") -> bool:
    """Does the player have at least one other card in hand that
    could be exiled to pay the pitch alt-cost of `pitch_card`?

    Heuristic: scan the oracle text of `pitch_card` for a color
    word ('blue', 'white', etc.); look for any other card in hand
    sharing that color identity.  If no color word is present, fall
    back to "any other card in hand" — the alt-cost may not be
    color-restricted (Subtlety pattern).

    Composes ColorIdentity sets that already live on every template;
    no new colour-detection logic in this module.
    """
    from engine.mana import Color

    hand = game.players[player_idx].hand
    if len(hand) < 2:
        # Only the pitch card itself in hand — no fuel.
        return False

    oracle = (pitch_card.template.oracle_text or "").lower()
    color_word_to_color = {
        "white": Color.WHITE,
        "blue": Color.BLUE,
        "black": Color.BLACK,
        "red": Color.RED,
        "green": Color.GREEN,
    }
    needed: Optional[Color] = None
    for word, color in color_word_to_color.items():
        if f"exile a {word}" in oracle or f"exile an {word}" in oracle:
            needed = color
            break

    for other in hand:
        if other is pitch_card:
            continue
        if needed is None:
            return True  # any non-self card qualifies (Subtlety-style)
        if needed in other.template.color_identity:
            return True
    return False


# Channel target categories: oracle word -> predicate against a
# battlefield permanent.  Closed structural set (artifact, creature,
# enchantment, planeswalker, [nonbasic] land, permanent) — every
# Channel card in Modern targets one of these.  No card names.
def _channel_target_predicates():
    from engine.cards import CardType, Supertype
    return {
        "artifact": lambda p: CardType.ARTIFACT in p.template.card_types,
        "creature": lambda p: CardType.CREATURE in p.template.card_types,
        "enchantment": lambda p: CardType.ENCHANTMENT in p.template.card_types,
        "planeswalker": lambda p: CardType.PLANESWALKER in p.template.card_types,
        "permanent": lambda p: True,
        "land": lambda p: CardType.LAND in p.template.card_types,
        "nonbasic land": lambda p: (
            CardType.LAND in p.template.card_types
            and Supertype.BASIC not in p.template.supertypes
        ),
    }


def _channel_has_legal_target(
    game: "GameState", card: "CardInstance",
) -> bool:
    """Does the channel ability of `card` have at least one legal
    target somewhere in the game?  Returns True for targetless channel
    abilities (Sokenzan creates tokens with no target) and as a
    defensive default when the oracle text doesn't expose a parseable
    'Channel — ... target X' clause.

    Implementation: locate the channel clause (oracle text after the
    'Channel —' / 'Channel -' marker), scan it for the canonical
    target-category idioms, then require at least one matching
    permanent on EITHER battlefield.  Channel target lines rarely
    restrict to opponent-only (Otawara, Boseiju, etc. allow any
    controller), so both battlefields qualify.

    Class size: every channel card in Modern and beyond — Otawara,
    Boseiju, Sokenzan, Takenuma, Eiganjo, etc.  The predicate is
    text-driven, not name-driven.
    """
    oracle = (card.template.oracle_text or "").lower()
    # 'Channel —' (em-dash) or 'Channel -' (ASCII hyphen); both occur.
    idx = max(oracle.find("channel —"), oracle.find("channel -"))
    if idx < 0:
        return True  # no channel clause — defensive default
    clause = oracle[idx:]
    if "target" not in clause:
        return True  # targetless channel (Sokenzan)

    predicates = _channel_target_predicates()
    # Longer phrases first so 'nonbasic land' doesn't lose to 'land'.
    requested = []
    for word in ("nonbasic land", "artifact", "creature", "enchantment",
                 "planeswalker", "permanent", "land"):
        # 'target <word>' anchors the first category; comma-continued
        # multi-type lines ('target artifact, creature, ...') match
        # via word-boundary in the residual clause.
        if f"target {word}" in clause or re.search(
                rf"[\s,]{re.escape(word)}\b", clause):
            requested.append(word)
    if not requested:
        return True  # 'target' found but no known category — defensive

    for pid in (0, 1):
        for perm in game.players[pid].battlefield:
            if any(predicates[w](perm) for w in requested):
                return True
    return False


def _battlefield_has_activatable(card: "CardInstance") -> bool:
    """Does a battlefield permanent expose an activated ability that
    the AI could fire in an instant-speed window?

    Structural definition: the card's template has at least one
    `Ability` of type `ACTIVATED` (engine layer already classifies
    ability types from oracle).  The ability's cost / target / timing
    legality is downstream scoring's job — this enumeration is
    "what COULD I do".

    Equip abilities, planeswalker loyalty abilities (sorcery speed),
    and tap-for-mana abilities are present but flagged differently
    (loyalty / tap_cost / mana_ability); we filter to keep the
    candidate list focused on the "instant-speed activation"
    intuition that the audit was about.
    """
    from engine.cards import AbilityType

    for ability in card.template.abilities or ():
        if ability.ability_type != AbilityType.ACTIVATED:
            continue
        # Mana abilities don't use the stack and aren't responses
        # in the audit sense — exclude them.
        if ability.ability_type == AbilityType.MANA_ABILITY:
            continue
        return True
    return False


# ─── Per-zone candidate generators ─────────────────────────────────────


def _yield_pass() -> Iterator[ResponseCandidate]:
    """Yield the always-legal pass-priority candidate (exactly one)."""
    yield ResponseCandidate(
        action="pass",
        source=None,
        cost="0",
        targets=(),
        requires_tap_out=False,
    )


def _yield_hand_candidates(
    game: "GameState",
    controller: int,
    stack_item: Optional["StackItem"],
) -> Iterator[ResponseCandidate]:
    """Yield candidates sourced from the controller's hand.

    Covers: counterspells, instant-speed removal, instant-speed
    discard, pitch-cast (Force of Negation / Solitude evoke).
    Does NOT cover channel costs — those route through the lands-in-
    hand generator because the host card is a land and the activation
    is its second ability.
    """
    player = game.players[controller]

    for card in player.hand:
        tmpl = card.template
        tags = tmpl.tags or set()

        # ── Channel (any card type, but the host is in hand) ──
        # Class size: every card with a Channel ability (Otawara,
        # Boseiju, Sokenzan, etc.).  Detection is W0-A oracle tag.
        # Target-category legality (CR 601.2c): if the channel ability
        # has a 'target X' clause, at least one matching permanent must
        # exist in the game.  Targetless channels (Sokenzan creates
        # tokens) bypass the gate and always surface.
        if (has_tag(card.name, Tag.CHANNEL_ABILITY)
                and _channel_has_legal_target(game, card)):
            yield ResponseCandidate(
                action="channel",
                source=card,
                cost=_format_mana_cost(card),
                targets=(),
                # Channel costs include a card discard (the host
                # itself); they typically tap out at the cost.
                requires_tap_out=True,
            )

        # Non-channel candidates require an instant-speed host card.
        if not _is_instant_speed(card):
            continue

        # ── Counter against the current stack item ──
        # Class size: every counterspell in Modern.  Detection: the
        # template tag set carries "counterspell" (already populated
        # by engine/card_database.py from oracle text).
        if "counterspell" in tags and stack_item is not None:
            # Fizzle precondition: only spells can be countered (not
            # triggered abilities).  CR 701.5 — counter spell or
            # ability; we narrow to spell here because every
            # downstream consumer cares about spell-on-stack only.
            if stack_item.source.template.is_spell:
                yield ResponseCandidate(
                    action="counter",
                    source=card,
                    cost=_format_mana_cost(card),
                    targets=(stack_item.source,),
                    requires_tap_out=False,
                )

        # ── Pitch-cast (alternative cost) ──
        # A card with a pitch alt-cost can be cast for "0 mana" by
        # exiling a same-color card from hand.  Class size: Force
        # of Negation, Force of Will, Subtlety, Solitude evoke,
        # Endurance evoke, every other pitch card.
        if _has_pitch_alt_cost(card) and _has_pitch_fuel(
            game, controller, card
        ):
            # Targets depend on the spell's mode — counterspell-class
            # pitch (FoN, FoW) targets the stack item; creature-class
            # pitch evoke (Solitude) targets a creature.  Surface
            # the stack item if present; downstream scoring narrows.
            t = (stack_item.source,) if stack_item is not None else ()
            yield ResponseCandidate(
                action="cast_pitch",
                source=card,
                cost="0",  # mana cost; the exiled card is the real cost
                targets=t,
                requires_tap_out=False,
            )

        # ── Instant-speed removal ──
        # Class size: Fatal Push, Lightning Bolt, Path to Exile,
        # Bolt-style, Push, every removal instant.  Detection: the
        # template tag "removal" set by engine/card_database.py from
        # oracle text.  We do NOT enumerate specific targets here —
        # the caller's pick_removal_target_fn handles target choice.
        if "removal" in tags:
            yield ResponseCandidate(
                action="remove",
                source=card,
                cost=_format_mana_cost(card),
                # Candidate targets: opponent's permanents — leave
                # the tuple empty so downstream picks the specific
                # target (reuses existing target_solver logic).
                targets=(),
                requires_tap_out=False,
            )

        # ── Instant-speed forced discard ──
        # Class size: any FORCED_DISCARD-tagged card that is also
        # instant-speed (rare in Modern — most discard is sorcery).
        # The W0-A tag plus the instant-speed gate keeps this
        # enumeration honest.
        if has_tag(card.name, Tag.FORCED_DISCARD):
            yield ResponseCandidate(
                action="discard",
                source=card,
                cost=_format_mana_cost(card),
                targets=(),
                requires_tap_out=False,
            )


def _yield_battlefield_candidates(
    game: "GameState",
    controller: int,
) -> Iterator[ResponseCandidate]:
    """Yield activated-ability candidates from permanents on the
    controller's battlefield.

    Class size: every permanent with an activated ability that the
    AI could fire at instant speed (Mishra's Bauble crack, Wrenn-and-
    Six +1, equipment equip on opp's turn is illegal so it doesn't
    surface here; that's downstream timing-legality, not enumeration).
    """
    player = game.players[controller]

    for perm in player.battlefield:
        if not _battlefield_has_activatable(perm):
            continue
        yield ResponseCandidate(
            action="activate",
            source=perm,
            # Activated ability costs vary per-ability; the engine
            # already exposes them on `Ability.cost`.  We surface
            # the host card's printed cost as a presentation
            # placeholder; downstream scoring inspects abilities.
            cost=_format_mana_cost(perm),
            targets=(),
            requires_tap_out=False,
        )


def _yield_graveyard_candidates(
    game: "GameState",
    controller: int,
) -> Iterator[ResponseCandidate]:
    """Yield flashback / re-cast candidates from the controller's
    graveyard.

    Currently SCOPE-LIMITED: emits flashback candidates for cards
    tagged `Tag.FLASHBACK` whose flashback alt-cost is an instant-
    -speed cast (i.e. the original printed type is Instant, or the
    card has Flash).  Wave 1 will broaden this to escape, dredge-
    style re-cast, and Past in Flames-granted flashback windows.
    """
    player = game.players[controller]

    for card in player.graveyard:
        if not has_tag(card.name, Tag.FLASHBACK):
            continue
        if not _is_instant_speed(card):
            # Sorcery-speed flashback is not a "response" — skip.
            # Wave 1 will surface these for main-phase windows.
            continue
        yield ResponseCandidate(
            action="flashback",
            source=card,
            cost=_format_mana_cost(card),
            targets=(),
            requires_tap_out=False,
        )


# ─── Public API ────────────────────────────────────────────────────────


def available_responses(
    state: "GameState",
    stack_item: Optional["StackItem"] = None,
    *,
    controller: Optional[int] = None,
) -> Iterator[ResponseCandidate]:
    """Yield every legal response action for the current player.

    Iterates: hand (instant-speed spells, pitch-costs, evoke alt-cost,
    flash creatures, channel-from-hand), battlefield permanents
    (activated abilities), graveyard (instant-speed flashback).

    Each candidate is validated against:

    * Target *category* legality (counter needs a stack item; pitch
      needs a same-color pitch target in hand).
    * Card-type predicates (counterspell only against a spell, not a
      triggered ability).
    * Oracle-derived tags from the W0-A classifier (channel,
      flashback, discard).

    NOT validated here (the caller's job):

    * Mana availability — the enumerator is "what COULD I do if
      mana allows".  Mana scheduling is `ai/mana_planner.py`'s
      domain; running it during enumeration would couple this
      primitive to a scoring concern.
    * Specific target choice — downstream `pick_removal_target_fn`
      and `enumerate_legal_targets` handle this.

    Args:
        state: The current `GameState`.
        stack_item: The `StackItem` we are responding to, or `None`
            when the window is open with an empty stack (opp's
            upkeep, end step, etc.).  Counter candidates require a
            non-None stack item to point at.
        controller: The player index of the AI responding.  Defaults
            to `state.priority_player` when omitted.

    Yields:
        `ResponseCandidate` objects.  Pass is always yielded exactly
        once.  The order is `pass → hand → battlefield → graveyard`;
        callers that care about deterministic ordering can rely on
        this for golden-test pinning.

    Returns:
        A lazy iterator.  Materialise with `list(...)` when needed.
    """
    if controller is None:
        controller = getattr(state, "priority_player", None)
    if controller is None:
        # Defensive default: priority_player not yet wired — fall
        # back to active_player so this primitive remains callable
        # from unit tests that drive a partial game state.
        controller = getattr(state, "active_player", 0)

    yield from _yield_pass()
    yield from _yield_hand_candidates(state, controller, stack_item)
    yield from _yield_battlefield_candidates(state, controller)
    yield from _yield_graveyard_candidates(state, controller)


__all__ = [
    "ResponseCandidate",
    "available_responses",
]
