"""Single-source-of-truth predicates for card evaluation.

Every site in `ai/` that asks "is this card a ritual?" / "is this
card chain-extending fuel?" / "how many lands are in this collection?"
/ "what is this card's first-turn value?" should call into this
module rather than re-implementing the check inline.

Why this exists
---------------
The 2026-04-26 Storm pro-player audit found the SAME fuel-counting
bug copy-pasted in two adjacent functions of `ai/combo_calc.py`
(F2.1 + F2.1b).  Both branches had `count_every_non_storm_spell()`
when they meant `count_chain_extending_spells()`.  Fixing one
branch alone regressed Storm WR by 0.8pp; fixing both together
lifted it +4.0pp.

That class of bug — predicate copy-pasted, drifts independently —
is exactly what this module prevents.  Centralizing the predicates
ensures every call site agrees on what counts as "fuel" / "ritual"
/ "draw engine", and a future bug fixed in one place is fixed
everywhere.

Design rules
------------
1. Each predicate is a pure function of its arguments.  No side
   effects, no game-state mutation.
2. Tag-membership predicates use frozenset constants so the
   "what tags qualify" definition lives in one place.
3. Negative cases are tested explicitly (see
   `tests/test_ai_predicates.py`) — predicates designed only for
   the positive case were the source of multiple audit findings
   (F4.1 "always pay shock", F5.1 "no signal for cost reducer").
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from engine.cards import CardInstance


# ─── Tag set constants ────────────────────────────────────────────

# Spells that EXTEND a combo chain — either by producing more mana
# than they cost (rituals) or by drawing/digging for more spells
# (cantrips, draw, card_advantage).  Cards without one of these
# tags add 1 to storm count if cast but don't enable additional
# spells.  See F2.1+F2.1b in 2026-04-26 audit.
CHAIN_FUEL_TAGS: frozenset[str] = frozenset({
    'ritual', 'cantrip', 'draw', 'card_advantage',
})

# Spells that produce card advantage (draw, dig, exile-and-may-play).
# Subset of CHAIN_FUEL_TAGS — excludes pure mana-positive rituals.
DRAW_ENGINE_TAGS: frozenset[str] = frozenset({
    'cantrip', 'draw', 'card_advantage',
})


# ─── Card-level predicates ────────────────────────────────────────

def is_chain_fuel(card: "CardInstance") -> bool:
    """Card extends a spell chain (mana-positive OR card-positive).

    Returns True when the card has at least one of `CHAIN_FUEL_TAGS`
    set on its template.  Used by the storm-finisher and
    tutor-as-finisher branches in `ai/combo_calc.py` to decide
    whether to hold a finisher for more chain growth or fire it
    now.
    """
    return bool(CHAIN_FUEL_TAGS & getattr(card.template, 'tags', set()))


def is_ritual(card: "CardInstance") -> bool:
    """Card is tagged 'ritual' (mana production).

    Rituals cost N mana and produce M > N mana on resolution
    (Pyretic Ritual: pay 1R, get RRR; Manamorphose: pay 1R, get
    2 of any color).  Detection is tag-driven; the tag is set in
    `engine/card_database.py` based on oracle text patterns.
    """
    return 'ritual' in getattr(card.template, 'tags', set())


def is_draw_engine(card: "CardInstance") -> bool:
    """Card produces card advantage (cantrip / draw / card_advantage).

    Reckless Impulse, Wrenn's Resolve, Glimpse the Impossible,
    Past in Flames, etc.  Excludes pure mana rituals (which are
    chain fuel via `is_chain_fuel` but don't draw cards).
    """
    return bool(DRAW_ENGINE_TAGS & getattr(card.template, 'tags', set()))


def spell_draws(template) -> bool:
    """Casting the spell makes its controller draw (CR 121.1): a DRAW in
    the spell's typed effects with no other drawer printed, whatever the
    count's wording ("draw four cards", "draw that many cards"). Read from
    the parsed spell, never a substring list. An impulse draw is no draw
    (CR 121.1c; `is_impulse_draw`), and a permanent's later ability is not
    what casting it draws."""
    from engine.effect_spec import Verb, iter_specs
    effects = getattr(template, 'effects', None)
    host = effects.spell(0) if effects is not None else None
    if host is None:
        return False
    return any(s.verb is Verb.DRAW and s.actor is None
               for s in iter_specs(host.specs))


def is_impulse_draw(template) -> bool:
    """The spell exiles the top of its controller's library (an impulse
    draw: "exile the top N cards of your library ... you may play those
    cards"). Its cards go to exile, never into the hand, so it is no draw
    (CR 121.1c) and no on-draw trigger counts it. Read from the parsed
    spell (the effect grammar's library-position exile), never a tag."""
    from engine.effect_spec import Verb, iter_specs
    effects = getattr(template, 'effects', None)
    host = effects.spell(0) if effects is not None else None
    if host is None:
        return False
    return any(s.verb is Verb.EXILE
               and (getattr(s.filter, 'zone', None),
                    getattr(s.filter, 'owner', None),
                    getattr(s.filter, 'position', None))
               == ('library', 'you', 'top')
               for s in iter_specs(host.specs))


def impulse_cards_held(host) -> int:
    """The cards an impulse-draw host -- an EXILE of the top N cards of its
    controller's own library and a permission to play them -- lets the
    controller play past this turn: N when the permission outlasts the
    turn ("until the end of your next turn"), 0 when it ends this turn
    (cards that expire at cleanup are not held, `ai.playable_cards`) or
    the host is no impulse draw with a printed count. Read from the typed
    host, never a tag."""
    from engine.effect_model import DurationKind
    from engine.effect_spec import AmountKind, Verb, iter_specs
    if host is None:
        return 0
    specs = list(iter_specs(host.specs))
    exiles = [s for s in specs if s.verb is Verb.EXILE
              and (getattr(s.filter, 'zone', None),
                   getattr(s.filter, 'owner', None),
                   getattr(s.filter, 'position', None))
              == ('library', 'you', 'top')]
    if len(exiles) != 1 or exiles[0].amount is None \
            or exiles[0].amount.kind is not AmountKind.LITERAL:
        return 0
    lasting = any(s.verb is Verb.CONTINUOUS and s.duration is not None
                  and s.duration.kind is DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN
                  for s in specs)
    return int(exiles[0].amount.n or 0) if lasting else 0


def is_storm_payoff(card: "CardInstance") -> bool:
    """Card is a chain-payoff finisher — its effect scales with the
    storm count or its damage/token output ends the chain.

    Detection is tag-driven (`storm_payoff` is set by
    `engine/card_database.py` for every card whose oracle has the
    storm effect type).  This is the chain BOTTLENECK predicate:
    countering one removes the chain's payoff path; holding a
    counter for it is the correct play when chain fuel is on the
    stack with a payoff coming.

    Generic by construction — no card names.  Class size: every
    `storm_payoff`-tagged card in the catalog (Grapeshot, Empty the
    Warrens, Brain Freeze, Tendrils of Agony, and any future Modern
    storm finisher).
    """
    return 'storm_payoff' in getattr(card.template, 'tags', set())


def is_chain_payoff_accessor(card: "CardInstance") -> bool:
    """Card grants access to a chain payoff — direct STORM_PAYOFF or
    a tutor / flashback-combo card that surfaces a payoff from the
    sideboard / graveyard / library.

    A tutor (`Wish`, `Burning Wish`) reaches into SB / library; a
    flashback-combo card (`Past in Flames`) reanimates the chain
    out of the graveyard.  Both are payoff-access bottlenecks — the
    counter that lands on one shuts the chain down regardless of
    fuel density.

    Used by the chain-aware counter triage in `ai/response.py` to
    distinguish a payoff-access cast (counter it) from chain-fuel
    cast (hold).
    """
    tags = getattr(card.template, 'tags', set())
    return ('storm_payoff' in tags
            or 'tutor' in tags
            or ('flashback' in tags and 'combo' in tags))


# ─── Collection-level counts ──────────────────────────────────────

def count_lands(cards: "Iterable[CardInstance]") -> int:
    """Count land cards in a collection (any iterable of CardInstance)."""
    return sum(1 for c in cards if c.template.is_land)


def count_gy_creatures(graveyard: "Iterable[CardInstance]") -> int:
    """Count creature cards in a graveyard slice.

    Used by snapshot computation, gameplan goal evaluation, and
    reanimator readiness checks. Same identical formula was
    duplicated in 3 sites prior to centralization.
    """
    return sum(1 for c in graveyard if c.template.is_creature)


# ─── Graveyard worth (what graveyard hate actually removes) ─────────
#
# The value of exiling a graveyard is NOT its card count. It is how much
# of that graveyard is still a RESOURCE to its owner: a Boros Energy
# graveyard of spent burn spells is worth nothing, while a two-card
# graveyard holding an escape threat is worth a great deal. Every test
# below reads a PARSE-ONCE typed field — the oracle-runtime-parse ratchet
# stays at 0.

# `power_scales_with` values that read the graveyard as a resource. A
# body whose size is the graveyard's size is a size CONSUMER: shrinking
# the graveyard shrinks it.
_GRAVEYARD_SIZE_SCALINGS: frozenset[str] = frozenset(
    {'graveyard', 'delirium', 'tarmogoyf'})


def _consumes_graveyard_size(template) -> bool:
    """Does this card turn graveyard SIZE into value? Delve, delirium and
    a graveyard-scaling body read it; a cost that exiles cards from its
    owner's graveyard spends it -- an activated ability's (CR 602.2b, the
    typed `exile_from_graveyard_cards`) and an escape's other cards (CR
    702.138a)."""
    if (getattr(template, 'has_delve', False)
            or getattr(template, 'has_delirium', False)
            or getattr(template, 'power_scales_with', '')
            in _GRAVEYARD_SIZE_SCALINGS
            or getattr(template, 'escape_exile_count', 0)):
        return True
    return any(getattr(getattr(a, 'cost', None),
                       'exile_from_graveyard_cards', 0)
               for a in getattr(template, 'activated_abilities', None) or ())


def _graveyard_consumers(zones) -> "tuple[bool, bool]":
    """(size_matters, recursion) over the cards of `zones`: does any turn
    graveyard size into value (`_consumes_graveyard_size`), and does any
    put a card from a graveyard onto the battlefield?

    For an opponent the zones are PUBLIC ones -- their battlefield and
    their own graveyard; a reanimation spell already sitting in their
    graveyard is evidence the deck plays them. Their hand is hidden, and
    guessing at it would be a belief model, which is `ai.bhi`'s job, not
    a predicate's.
    """
    cards = [card for zone in zones for card in zone]
    return (any(_consumes_graveyard_size(c.template) for c in cards),
            any(getattr(c.template, 'reanimates_from_graveyard', False)
                for c in cards))


def _castable_from_graveyard(template) -> bool:
    """Does this card's own printed permission let it be cast from the
    graveyard? Flashback (CR 702.34), Escape (CR 702.138), Unearth (CR
    702.84) and Harmonize (CR 702.180) each need no second card, so a
    graveyard holding one is live no matter what else its owner has.
    """
    from engine.cards import Keyword

    return bool(getattr(template, 'flashback_cost', None) is not None
                or getattr(template, 'escape_cost', None) is not None
                or getattr(template, 'harmonize_cost', None) is not None
                or Keyword.UNEARTH in (getattr(template, 'keywords', None)
                                       or set()))


def is_graveyard_resource_itself(template, recursion: bool) -> bool:
    """Is this card, in its owner's graveyard, a resource there by itself
    (tiers 1 and 2 of `graveyard_fuel`): castable from the graveyard by its
    own permission, or a creature its owner can reanimate?"""
    return bool(_castable_from_graveyard(template)
                or (recursion and template.is_creature))


def _is_graveyard_fuel(template, size_matters: bool,
                       recursion: bool) -> bool:
    """The three tiers of `graveyard_fuel`, for one card."""
    return bool(size_matters
                or is_graveyard_resource_itself(template, recursion))


def own_graveyard_consumers(game, owner_idx: int) -> "tuple[bool, bool]":
    """(size_matters, recursion) as a player knows its own deck: read from
    every zone it holds (its hand and library included -- the pilot knows
    its decklist)."""
    player = game.players[owner_idx]
    return _graveyard_consumers((player.hand, player.library,
                                 player.battlefield, player.graveyard))


def graveyard_fuel(game, owner_idx: int) -> "list":
    """Cards in ``owner_idx``'s graveyard that are still a resource TO
    THEM — i.e. exactly what graveyard hate takes away.

    Three tiers, each derived from printed card data:

    1. cards castable from the graveyard by their own permission
       (flashback / escape / unearth / harmonize) — live unconditionally;
    2. creature cards, once their owner has revealed a graveyard-to-
       battlefield recursion source (they are reanimation targets);
    3. every card, once their owner has revealed a consumer of graveyard
       SIZE (delve / delirium / a graveyard-scaling body / a cost that
       exiles cards from their graveyard).

    Returned as a list of instances rather than a count so a targeted
    exile can pick WHICH cards to hit. Order follows the graveyard.
    """
    player = game.players[owner_idx]
    size_matters, recursion = _graveyard_consumers(
        (player.battlefield, player.graveyard))
    return [card for card in player.graveyard
            if _is_graveyard_fuel(card.template, size_matters, recursion)]


def graveyard_fuel_share(game, viewer_idx: int, owner_idx: int) -> float:
    """The expected share of the cards a mill moves from ``owner_idx``'s
    library to their graveyard that are graveyard fuel to them (the tiers
    of `graveyard_fuel`), as ``viewer_idx`` knows that library.

    - Its own: the pilot knows its decklist, so the share is exact over
      the library's cards (their order stays hidden), and its consumers
      are read from every zone it holds.
    - An opponent's: estimated from the cards they have revealed (their
      battlefield and graveyard, tokens aside), the viewer's sample of
      their deck, with the consumers that sample reveals.

    0.0 when the viewer holds no sample.
    """
    player = game.players[owner_idx]
    if viewer_idx == owner_idx:
        sample = list(player.library)
        known = own_graveyard_consumers(game, owner_idx)
    else:
        public = (player.battlefield, player.graveyard)
        sample = [c for zone in public for c in zone
                  if c.owner == owner_idx
                  and not getattr(c, 'is_token', False)]
        known = _graveyard_consumers(public)
    if not sample:
        return 0.0
    size_matters, recursion = known
    fuel = sum(1 for c in sample
               if _is_graveyard_fuel(c.template, size_matters, recursion))
    return fuel / len(sample)


# ─── First-turn value (mulligan land-slack replacement) ─────────────
# Tag families consulted by ``first_turn_value``.  Each frozenset is
# a set of real classifier tags (set in ``engine/card_database.py``
# from oracle text) that signal a particular kind of T1-T2 impact:
# combo payoff, forced discard, cost reduction, interaction, or
# efficient early threats.  ``CHAIN_FUEL_TAGS`` (imported above)
# covers ritual / cantrip / draw / card_advantage.
_PAYOFF_TAGS: frozenset[str] = frozenset({'storm_payoff'})
_DISRUPTION_TAGS: frozenset[str] = frozenset({'discard'})
_REDUCER_TAGS: frozenset[str] = frozenset({'cost_reducer'})
_INTERACTION_TAGS: frozenset[str] = frozenset({'removal', 'counterspell'})
_EARLY_THREAT_TAGS: frozenset[str] = frozenset({'early_play', 'efficient_threat'})


_EARLY_TAG_FAMILIES: tuple[frozenset[str], ...] = (
    CHAIN_FUEL_TAGS, _PAYOFF_TAGS, _DISRUPTION_TAGS,
    _REDUCER_TAGS, _INTERACTION_TAGS, _EARLY_THREAT_TAGS,
)
_ANY_EARLY_TAG: frozenset[str] = frozenset().union(*_EARLY_TAG_FAMILIES)


def first_turn_value(card: "CardInstance",
                     hand_context: dict | None = None) -> int:
    """Per-card T1-T2 value in {0, 1, 2}, derived from template tags
    and primitives (CMC, creature stats). Replaces the flat
    ``mulligan_max_lands + 2`` slack in ``ai/mulligan.py``.

    Banding:
      * 0 — land, OR CMC>2 with no early-impact tag, OR cheap non-
        creature with no early-impact tag (inert in the early game).
      * 2 — 1-drop creature with positive power (prototypical T1
        play that also enables T2 attacks).
      * 1 — every other castable-on-T1-or-T2 play.

    ``hand_context`` is reserved for future cross-card adjustments.
    """
    _ = hand_context  # reserved for hand-aware adjustments
    tmpl = card.template
    if tmpl.is_land:
        return 0
    tags = getattr(tmpl, 'tags', set()) or set()
    cmc = tmpl.cmc or 0
    early_tag_hit = bool(tags & _ANY_EARLY_TAG)
    if cmc > 2 and not early_tag_hit:
        return 0
    if cmc <= 2 and not tmpl.is_creature and not early_tag_hit:
        return 0
    if cmc <= 1 and tmpl.is_creature and (tmpl.power or 0) > 0:
        return 2
    return 1


def hand_first_turn_value(cards: "Iterable[CardInstance]") -> int:
    """Sum of ``first_turn_value`` across a hand slice — used by
    the mulligan land-slack predicate."""
    return sum(first_turn_value(c) for c in cards)


def is_land_option(card) -> bool:
    """Can this card be played as a land: a land, or a modal double-faced
    card with a land face (CR 712, 305.1)? The keep and the land drop both
    count it."""
    t = card.template
    if getattr(t, "playable_land_face", None) is not None:
        return True
    return bool(getattr(t, "is_land", False))


def graveyard_target_removal(host):
    """``(slot, by_name)`` when a typed host's resolution removes from its
    graveyard the card chosen for its target ``slot`` -- the target a
    spell aimed at a graveyard card needs to stay legal (CR 608.2b): an
    exile of that target (``by_name`` False), or a search of its owner's
    zones, the graveyard among them, for the cards sharing its name, then
    "exile them" (``by_name`` True: every card of that name in that
    graveyard goes too). None for any other host."""
    from engine.effect_spec import Ref, RefKind, Verb, iter_specs
    if host is None:
        return None
    targets = list(getattr(host, "targets", ()) or ())
    specs = list(iter_specs(getattr(host, "specs", ()) or ()))

    def graveyard_slot(k) -> bool:
        return isinstance(k, int) and k < len(targets) \
            and targets[k].zone == "graveyard"

    exiled = {s.ref.index for s in specs
              if s.verb is Verb.EXILE and isinstance(s.ref, Ref)
              and s.ref.kind is RefKind.RESULT}
    for s in specs:
        if s.verb is Verb.EXILE and graveyard_slot(s.target_slot):
            return s.target_slot, False
        f = s.filter
        if s.verb is not Verb.SEARCH or f is None or s.seq not in exiled \
                or "graveyard" not in (getattr(f, "zones", ()) or ()):
            continue
        named, owner = f.named, f.owner
        if isinstance(named, Ref) and named.kind is RefKind.TARGET \
                and graveyard_slot(named.index) \
                and isinstance(owner, Ref) \
                and owner.kind is RefKind.OWNER_OF \
                and isinstance(owner.of, Ref) \
                and owner.of.kind is RefKind.TARGET \
                and owner.of.index == named.index:
            return named.index, True
    return None
