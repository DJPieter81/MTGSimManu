"""Effective-CMC primitive (W0-F of the 2026-05-16 structural refactor).

The audit's M9 finding: `ai/ev_evaluator.py:_project_spell` currently
charges every spell its *printed* CMC, ignoring the cost-modifying
mechanics that Modern decks actually pay through.  Murktide Regent
scores its EV as a 7-mana spell even though the deck rarely pays
more than 2 for it (delve covers 5).  Ruby-Storm rituals score un-
discounted under Ruby Medallion.  Solitude scores as a 3WW creature
even when the caster has a white card to pitch.

The structural cure: ONE primitive that owns ALL cost modifications.
Wave 1 (M9) migrates the `_project_spell` call site to query this
primitive instead of reading `template.cmc` directly.  This module is
pure-addition; no existing call site changes until Wave 1.

Composition order matters:
  1. Cast mode short-circuits  — `'free'` returns 0 (cascade, suspend,
     wish-fetched), `'evoke'` returns the parsed evoke-cost CMC.
  2. Base CMC  — read from `card.template.cmc`.
  3. Cost reducers on board  — `count_cost_reducers` reads each
     permanent's oracle text via `parse_cost_reduction`; respects
     color and target restrictions.  Generic subtractor.
  4. Delve  — bounded by the *generic-mana* portion of the printed
     cost (a card cannot delve away its colored pips).  The cap is
     derived from the card's own `ManaCost`, never a hardcoded
     `MAX_DELVE` constant.
  5. Affinity-for-artifacts  — subtracts the controller's artifact
     count from the generic portion.
  6. Improvise  — subtracts the untapped-artifact count from the
     generic portion.
  7. Kicker  — never applied by default; the engine itself currently
     ignores kicker payments (`engine/card_effects.py:985`), so the
     primitive matches.  Future kicker-aware Wave-2 work can pass an
     explicit `with_kicker=True`.

All dispatch routes through:
  - `engine.oracle_resolver.count_cost_reducers` (oracle-text-only,
    no card names) for reducers
  - `template.has_delve`, `template.evoke_cost`, `Keyword.AFFINITY`
    (engine-parsed structural flags, oracle-derived at DB load)

Zero card-name checks, zero new magic numbers.  See CLAUDE.md
ABSTRACTION CONTRACT.

The primitive is read-only — it never mutates `card`, `snap`, or
`game`.

Forbidden in this module:
    * Card-name special-casing (e.g. comparing card.name to a
      string literal) — dispatch via tag / template flag instead.
    * Magic numbers like `MAX_DELVE = 7`  — derive from card.
    * Per-card EV tables — this primitive only reports COST, not value.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # avoid circular imports at runtime
    from engine.cards import CardInstance, CardTemplate
    from engine.game_state import GameState


# ─── Cast-mode constants ─────────────────────────────────────────────
#
# Module-level constants — these are sentinel STRINGS, not numeric
# thresholds, so the magic-number ratchet does not apply.  They are
# defined here (rather than imported from `ai.scoring_constants`)
# because they are the primitive's own protocol and have no use
# elsewhere.

CAST_MODE_NORMAL: str = "normal"
"""Standard hardcast — pay the printed mana cost, modified by
on-board cost-reducers and delve / affinity / improvise if the
spell carries those mechanics."""

CAST_MODE_EVOKE: str = "evoke"
"""Pay the alternative evoke cost (Solitude, Endurance, Grief, ...).
Returns `template.evoke_cost.cmc` or, if the evoke cost is not
mana-parsed, falls back to 0 (the cost is non-mana exile fodder).
The caller is responsible for verifying the non-mana evoke
predicate (a white card in hand, etc.) — the primitive only
reports the *mana* paid."""

CAST_MODE_FREE: str = "free"
"""Cascade-cast, suspend resolution, wish-fetched-and-cast — no
mana paid.  Always returns 0 regardless of the card's mechanics."""

CAST_MODE_FLASHBACK: str = "flashback"
"""Cast from graveyard for the flashback cost.  Not yet parsed at
runtime (template.flashback_cost is a Wave-2 addition); falls back
to printed CMC for now.  Documented here for forward compatibility."""


# ─── Helpers ─────────────────────────────────────────────────────────


def _generic_portion(template: "CardTemplate") -> int:
    """Return the printed generic-mana portion of `template.mana_cost`.

    Magic's delve / affinity / improvise rules all share the same
    cap: they reduce ONLY the generic portion of a spell's cost — a
    card's colored pips (`{U}{U}`) cannot be discounted to zero by
    these mechanics, only the `{N}` generic portion can.

    This helper centralises that derivation so callers compute the
    cap from the card itself, never from a hardcoded constant.  The
    formula mirrors `engine.cast_manager`'s line at the cost-
    calculation site (search for `colored_cost = ...`).
    """
    mc = template.mana_cost
    colored = mc.white + mc.blue + mc.black + mc.red + mc.green
    return max(0, (mc.cmc or 0) - colored)


def _count_delve_fuel(game: "GameState", player_idx: int) -> int:
    """Count the cards in `player`'s graveyard that can pay for delve.

    Delve's printed rule is "exile any number of cards from your
    graveyard.  Each card exiled this way pays for {1}" — there is
    NO type restriction in the rules text; the engine's
    `cast_manager` exiles from the full graveyard (see
    `cast_manager.py:781`).  We mirror that here.

    Note that some downstream effects (Murktide Regent's +1/+1
    counters per delved instant/sorcery) DO care about the type
    of the exiled cards — that's a separate computation in the
    engine, not a delve-cost calculation.
    """
    if game is None:
        return 0
    player = game.players[player_idx]
    return len(player.graveyard)


def _count_artifacts(game: "GameState", player_idx: int) -> int:
    """Count non-land artifacts on `player`'s battlefield.

    Affinity-for-artifacts (`Keyword.AFFINITY` in Modern cards like
    Frogmite, Myr Enforcer, Thought Monitor) reads "this spell
    costs {1} less to cast for each artifact you control."  The
    "you control" predicate is the controller's battlefield; lands
    that happen to be artifact-typed (Treasure Vault, Inkmoth
    Nexus while animated) DO count per Magic's rules.

    We follow `engine.cast_manager.py:175`'s exact predicate:
    `CardType.ARTIFACT in c.template.card_types` — no extra
    is_land filter, because artifact lands are still artifacts.
    """
    if game is None:
        return 0
    from engine.cards import CardType

    player = game.players[player_idx]
    return sum(
        1
        for c in player.battlefield
        if CardType.ARTIFACT in c.template.card_types
    )


def _count_untapped_artifacts(
    game: "GameState", player_idx: int, exclude_card: "CardInstance" = None
) -> int:
    """Count untapped non-land artifacts on `player`'s battlefield.

    Improvise pays generic mana by TAPPING artifacts — already-tapped
    artifacts can't pay.  Mirrors `engine/cast_manager.py:270`.

    `exclude_card` is the spell being cast itself (you cannot tap
    yourself to pay your own cost).
    """
    if game is None:
        return 0
    from engine.cards import CardType

    player = game.players[player_idx]
    return sum(
        1
        for c in player.battlefield
        if CardType.ARTIFACT in c.template.card_types
        and not c.template.is_land
        and not getattr(c, "tapped", False)
        and c is not exclude_card
    )


def _count_cost_reducers(
    game: "GameState", player_idx: int, template: "CardTemplate"
) -> int:
    """Generic cost-reduction count.

    Routes through the engine-level `count_cost_reducers`, which
    parses each permanent's oracle text for "cost {N} less"
    patterns via `engine.oracle_parser.parse_cost_reduction`.  No
    card-name checks anywhere — Ruby Medallion, Sapphire
    Medallion, Goblin Electromancer, Baral, and any future
    cost-reduction permanent that uses the same oracle idiom are
    all handled by the same regex.

    The classifier (W0-A `Tag` enum) does NOT yet carry a
    `COST_REDUCER_*` tag; extending it would require an LLM call
    and a smoke-cache rebuild.  Since the engine-level oracle
    parser is ALREADY structural (zero card-name checks), we route
    through it — same structural guarantees, no LLM budget cost.
    """
    if game is None:
        return 0
    # Local import to keep the AI/engine boundary explicit and avoid
    # a hard import-time dependency on engine internals.
    from engine.oracle_resolver import count_cost_reducers as _engine_count

    return _engine_count(game, player_idx, template)


# ─── Public API ──────────────────────────────────────────────────────


def effective_cmc(
    card: "CardInstance",
    snap=None,
    *,
    game: "GameState" = None,
    player_idx: int = 0,
    cast_mode: str = CAST_MODE_NORMAL,
) -> int:
    """Return the mana actually paid for `card` given current state.

    This is the W0-F primitive — the single point where cost-
    modifying mechanics compose.  Wave 1 (M9) will route
    `_project_spell`'s spending arithmetic through this function so
    EV scoring reflects what the deck *actually pays*, not what's
    printed on the card.

    Composition order (each step subtracts from `paid`):

    1. **Cast mode short-circuit** — `'free'` → 0, `'evoke'` →
       `template.evoke_cost.cmc` (or 0 if non-mana evoke).
    2. **Base** — `template.cmc`.
    3. **On-board cost reducers** — `count_cost_reducers` parses
       each permanent's oracle for "cost {N} less" patterns.
       Respects color/type restrictions.
    4. **Delve** — caps at the printed generic-mana portion (a
       card cannot delve away its colored pips).
    5. **Affinity-for-artifacts** — `Keyword.AFFINITY`-tagged
       cards subtract the controller's artifact count, also
       capped at the generic portion.
    6. **Improvise** — same shape as affinity but using
       untapped artifacts.

    Result is clamped to ≥ 0 (a spell can never have negative cost
    after discounts — Magic's "cost reductions cannot reduce a
    spell's cost below {0}" rule).

    Parameters
    ----------
    card : CardInstance
        The card whose cost is being computed.  Read-only.
    snap : EVSnapshot, optional
        The snapshot the cost is being computed against.  Currently
        the snap is not directly consumed by this function (cost
        depends on game-state battlefield contents which the snap
        summarises only as counts, not as the typed permanent list
        the cost-reducer parser needs).  Reserved for Wave-2
        thresholds (e.g. snap-based discount caps).  Pass `None`
        when calling outside an EV context.
    game : GameState, optional
        Live game state.  Required for cost-reducer detection,
        delve, affinity, and improvise — all of which read the
        battlefield or graveyard.  When `None`, the primitive
        falls back to the printed CMC modified only by cast-mode
        (a "no context" upper bound).
    player_idx : int
        Controller of the spell.  Indexes `game.players`.
    cast_mode : str
        One of `CAST_MODE_NORMAL`, `CAST_MODE_EVOKE`,
        `CAST_MODE_FREE`, `CAST_MODE_FLASHBACK`.  Unknown values
        are treated as normal (defensive).

    Returns
    -------
    int
        Mana paid, in generic-equivalent units.  Colored-mana
        availability is enforced elsewhere (`engine.mana_payment`).
    """
    template = card.template

    # ── Step 1: cast-mode short-circuits ────────────────────────────
    if cast_mode == CAST_MODE_FREE:
        # Cascade / suspend / wish — no mana paid by definition.
        return 0

    if cast_mode == CAST_MODE_EVOKE:
        evoke = template.evoke_cost
        if evoke is not None:
            return int(evoke.cmc or 0)
        # Card claimed evoke but the DB didn't parse a cost — fall
        # through to the printed cost.  This is the safe path; a
        # Wave-2 follow-up can warn-log here once every relevant
        # evoke card has a parsed cost.

    # ── Step 2: base printed CMC ────────────────────────────────────
    paid = int(template.cmc or 0)

    # Generic portion — used as the cap for delve / affinity /
    # improvise discounts (all three discount generic only).
    generic = _generic_portion(template)

    # ── Step 3: on-board cost reducers ──────────────────────────────
    reducers = _count_cost_reducers(game, player_idx, template)
    if reducers > 0:
        # Cost reducers can drive a spell to {0} (Storm rituals
        # become free under enough medallions).  Clamp at 0.
        paid = max(0, paid - reducers)

    # Recompute the generic-portion-remaining after reducers ate
    # into it.  Delve/affinity/improvise can't drive paid below
    # the colored cost.
    colored_cost = (paid > 0) and (template.mana_cost.white
                                   + template.mana_cost.blue
                                   + template.mana_cost.black
                                   + template.mana_cost.red
                                   + template.mana_cost.green)
    # Maximum that generic-only mechanics can still subtract.
    remaining_generic = max(0, paid - (colored_cost or 0))

    # ── Step 4: delve ───────────────────────────────────────────────
    if template.has_delve and game is not None:
        gy_fuel = _count_delve_fuel(game, player_idx)
        # Cap at the *current* generic portion (post-reducer), not
        # the printed generic portion — if reducers already ate the
        # generic, delve has nothing left to subtract.
        delve_reduction = min(gy_fuel, remaining_generic)
        paid = max(0, paid - delve_reduction)
        remaining_generic = max(0, remaining_generic - delve_reduction)

    # ── Step 5: affinity-for-artifacts ──────────────────────────────
    from engine.cards import Keyword

    if Keyword.AFFINITY in template.keywords and game is not None:
        artifacts = _count_artifacts(game, player_idx)
        affinity_reduction = min(artifacts, remaining_generic)
        paid = max(0, paid - affinity_reduction)
        remaining_generic = max(0, remaining_generic - affinity_reduction)

    # ── Step 6: improvise ───────────────────────────────────────────
    oracle_lower = (template.oracle_text or "").lower()
    if "improvise" in oracle_lower and game is not None:
        untapped_artifacts = _count_untapped_artifacts(
            game, player_idx, exclude_card=card
        )
        improvise_reduction = min(untapped_artifacts, remaining_generic)
        paid = max(0, paid - improvise_reduction)

    # ── Step 7: kicker is opt-in; default ignores it ────────────────
    # No code: the engine itself does not auto-pay kicker
    # (engine/card_effects.py:985 "every cast resolves as the base
    # (unkicked) cost").  When Wave-2 introduces explicit kicker
    # support, add a `with_kicker: bool = False` parameter and
    # branch here.

    return int(paid)


__all__ = [
    "effective_cmc",
    "CAST_MODE_NORMAL",
    "CAST_MODE_EVOKE",
    "CAST_MODE_FREE",
    "CAST_MODE_FLASHBACK",
]
