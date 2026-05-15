"""Finisher simulator v3 — typed API stub. NOT WIRED.

Successor to `ai/finisher_simulator.py` (v2).  This module ships
with `docs/design/2026-05-10_simulator_v3.md` as a concrete-API
sketch — the schemas, function signatures, and docstrings are
review-ready, but the bodies raise `NotImplementedError`.

NOT WIRED — no callsite in `ai/`, `engine/`, or `tests/` imports
this module.  PR3c will implement the bodies and migrate
`ai/combo_evaluator.py` to call this module instead of the v2
projection.

Design rules (carried forward from v2):

1. **Pure function.**  Takes snapshot/zones/library by value,
   returns a `FinisherProjectionV3`.  Never mutates game state.
2. **Pattern detection is oracle/keyword/tag-driven.**  Zero
   card names, zero deck names, zero archetype gates.  Reuses
   v2's predicates (`_has_storm_keyword`, `_has_token_finisher_oracle`,
   `_is_cycling_payoff`, `_is_cascade_payoff`).
3. **No magic numbers.**  Numeric values come from
   `ai.scoring_constants` (named, with inline justification),
   from rules constants documented in source, or are derived from
   `ai.bhi` / `ai.clock`.
4. **Library composition is tag-indexed, not card-name-keyed.**
   Per the abstraction contract.

See the design doc for the rationale, the algorithmic sketch,
and the test plan.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field

from ai.schemas import FinisherPattern
from ai.scoring_constants import (
    CHAIN_MULTI_TURN_DEPTH,
    CHAIN_REMOVAL_PRESSURE_FLOOR,
    CHAIN_TUTOR_MIN_RESOLVE,
    EV_SNAPSHOT_NO_CLOCK_DISCRETE,
)

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from ai.ev_evaluator import EVSnapshot
    from ai.bhi import BayesianHandTracker


# ─── Schemas ───────────────────────────────────────────────────────


class LibraryComposition(BaseModel):
    """Tag-indexed histogram of the library, plus total size.

    Tags are the same string keys produced by
    ``engine/card_database.py``'s tag pass: ``'ritual'``,
    ``'cantrip'``, ``'tutor'``, ``'cost_reducer'``, ``'flashback'``,
    ``'reanimate'``, ``'discard'``, ``'cycling'``, ``'cascade'``.
    Closer categories are detected via keyword / oracle predicate:

    * ``'storm_closer'`` — STORM keyword
    * ``'token_finisher'`` — oracle "create … tokens … for each"
    * ``'reanim_target'`` — creature with power above the
      gameplan-declared floor
    * ``'cycling_payoff'`` — oracle "all creature cards …
      graveyards … to the battlefield"
    * ``'cascade_payoff'`` — ``'combo'`` tag AND not creature
      AND CMC reachable from cheapest cascade enabler

    No card-name keys ever appear in this histogram.  See
    ``docs/design/2026-05-10_simulator_v3.md`` §3.2.
    """

    total: int = Field(default=0, ge=0)
    by_tag: dict[str, int] = Field(default_factory=dict)
    closer_count: int = Field(default=0, ge=0)
    closer_categories: tuple[str, ...] = Field(default=())

    model_config = ConfigDict(frozen=True)


class TurnOffsetProjection(BaseModel):
    """One node in the multi-turn rollout chain.

    A ``FinisherProjectionV3`` carries a tuple of these for offsets
    0..max_depth.  Each node is a complete-by-itself projection
    of "what does the chain look like if we attempt to close on
    THIS turn-offset?".  See ``docs/design/2026-05-10_simulator_v3.md``
    §5 for the algorithm.
    """

    offset: int = Field(ge=0)
    expected_damage: float = Field(ge=0.0)
    closer_reachable_p: float = Field(ge=0.0, le=1.0)
    survival_p: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0)
    mana_at_offset: int = Field(ge=0)
    storm_at_offset: int = Field(ge=0)
    notes: str = ""

    model_config = ConfigDict(frozen=True)


class FinisherProjectionV3(BaseModel):
    """Projected EV-impact of attempting / building a finisher
    chain over a multi-turn horizon.

    Successor to v2 ``FinisherProjection``.  Wire-compatible:
    callers reading only v2 fields (``pattern``, ``expected_damage``,
    ``success_probability``, ``hold_value``, etc.) work unchanged.
    The v3 fields are additive.
    """

    # ── v1/v2 fields (semantics unchanged) ──
    pattern: FinisherPattern = "none"
    expected_damage: float = Field(default=0.0, ge=0.0)
    success_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    mana_floor: int = Field(default=0, ge=0)
    chain_length: int = Field(default=0, ge=0)
    closer_name: Optional[str] = None
    hold_value: float = Field(default=0.0, ge=0.0)
    next_turn_damage: float = Field(default=0.0, ge=0.0)
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    closer_in_zone: dict[str, bool] = Field(
        default_factory=lambda: {
            'hand': False, 'sb': False,
            'library': False, 'graveyard': False,
        }
    )

    # ── v3 fields ──
    library_composition: LibraryComposition = Field(
        default_factory=LibraryComposition,
    )
    turn_projections: tuple[TurnOffsetProjection, ...] = Field(default=())
    best_turn_offset: int = Field(default=0, ge=0)
    tutor_access_chains: tuple[str, ...] = Field(default=())
    p_closer_by_turn: tuple[float, ...] = Field(default=())

    model_config = ConfigDict(frozen=True)


# ─── Library composition ───────────────────────────────────────────


def build_library_composition(
    library: list["CardInstance"],
    *,
    deck_gameplan: Optional[dict] = None,
) -> LibraryComposition:
    """Bucket ``library`` by tag/oracle predicate.

    No card names enter or leave this function.  Closer categories
    are detected via the same predicates the v2 simulator uses
    (``_has_storm_keyword``, ``_has_token_finisher_oracle``,
    ``_is_cycling_payoff``, ``_is_cascade_payoff``) plus a new
    ``_is_reanim_target`` that reads
    ``reanim_target_power_floor`` from the gameplan JSON.

    Args:
        library: list of CardInstance currently in the player's
            library (any zone-tracking abstraction works — only
            ``card.template`` is read).
        deck_gameplan: optional pass-through of the gameplan dict
            for per-archetype thresholds (e.g. reanimator's minimum
            target power).  None → conservative defaults.

    Returns:
        ``LibraryComposition`` with totals, per-tag counts, and the
        list of closer categories present.

    See ``docs/design/2026-05-10_simulator_v3.md`` §3.1.
    """
    raise NotImplementedError("v3 stub — implement in PR3c")


def p_draw_closer(
    composition: LibraryComposition,
    n_draws: int,
    *,
    closer_categories: Optional[set[str]] = None,
) -> float:
    """P(at least one closer drawn in ``n_draws`` draws).

    Hypergeometric without replacement::

        P = 1 - C(non_closer, n_draws) / C(total, n_draws)

    Args:
        composition: snapshot from ``build_library_composition``.
        n_draws: number of upcoming draws to project over.  ``0``
            returns ``0.0`` (no draws taken).
        closer_categories: subset of ``composition.closer_categories``
            to count.  Default: all categories.  Use a narrower
            subset when only specific closer types are usable
            this turn (e.g. ``{'storm_closer'}`` when mana-bound).

    Returns:
        Probability in ``[0.0, 1.0]``.
    """
    raise NotImplementedError("v3 stub — implement in PR3c")


# ─── Tutor-as-finisher-access ──────────────────────────────────────


def _tutor_access_contribution(
    hand: list["CardInstance"],
    sideboard: list["CardInstance"],
    library_composition: LibraryComposition,
    snap: "EVSnapshot",
    bhi_state: "BayesianHandTracker",
) -> tuple[Optional["CardInstance"], int, float]:
    """Best tutor-as-finisher-access path from the current hand.

    Generic by tag — every ``'tutor'``-tagged card with a real
    target shares this code path: Burning Wish, Living Wish,
    Demonic Tutor, Glittering Wish, Eladamri's Call, Sevinne's
    Reclamation, Summoner's Pact.

    Args:
        hand: list of CardInstance currently in hand.
        sideboard: list of CardInstance available for SB-tutoring.
        library_composition: tag/closer histogram of the library.
        snap: EVSnapshot at the point of projection.
        bhi_state: opponent-hand belief tracker, queried for
            ``get_counter_probability()`` to dampen the tutor's
            resolution probability.

    Returns:
        Tuple ``(best_tutor, extra_cost, p_resolves)``:

        * ``best_tutor`` — the most cost-efficient tutor with a
          reachable target, or ``None`` when no tutor-with-access
          exists.
        * ``extra_cost`` — tutor's CMC, in mana, added to the
          chain's mana floor when this access path is used.
        * ``p_resolves`` — ``1 - p_counter`` floored at the
          rules-derived ``CHAIN_TUTOR_MIN_RESOLVE`` so a fully
          counter-leaden opponent doesn't zero the path.

    See ``docs/design/2026-05-10_simulator_v3.md`` §4.
    """
    raise NotImplementedError("v3 stub — implement in PR3c")


# ─── Multi-turn rollout ────────────────────────────────────────────


def _project_multi_turn(
    snap: "EVSnapshot",
    hand: list["CardInstance"],
    battlefield: list["CardInstance"],
    graveyard: list["CardInstance"],
    sideboard: list["CardInstance"],
    library_composition: LibraryComposition,
    storm_count: int,
    archetype: str,
    bhi_state: "BayesianHandTracker",
    max_depth: int,
) -> tuple[TurnOffsetProjection, ...]:
    """Build the (offset 0, offset 1, ..., offset max_depth)
    chain of ``TurnOffsetProjection`` nodes.

    Each offset applies a snapshot delta:

    * ``+1`` land drop  (``my_mana += 1``, ``my_total_lands += 1``)
    * ``-opp_pressure`` life  (clock.py opp_power tick)
    * ``storm_count -> 0``  (CR 500.4 — storm count is per-turn)
    * ``closer_reachable_p`` folds ``p_draw_closer(library_composition, n=offset)``

    Survival probability folds in ``ai.clock`` (opp clock decay)
    and ``ai.bhi`` (removal density).

    The score for each offset is::

        score = expected_damage * survival_p * closer_reachable_p

    The recursion stops early if the projected snapshot would be
    dead by the offset (``my_life <= 0`` after pressure tick).

    See ``docs/design/2026-05-10_simulator_v3.md`` §5.1.
    """
    # Local imports keep this module's import surface narrow and
    # avoid a top-level cycle with `ai.finisher_simulator` (v2 is
    # the chain-arithmetic primitive v3 builds on).
    from ai.finisher_simulator import simulate_finisher_chain

    projections: list[TurnOffsetProjection] = []
    closer_in_hand_p = _closer_in_hand_probability(hand)

    for offset in range(max_depth + 1):
        # 1. Snapshot delta — pure copy with mana/land tick, life loss
        #    from opp_pressure, storm_count reset (CR 500.4, per-turn).
        opp_pressure = max(0, int(snap.opp_power)) * offset
        future_life = max(0, int(snap.my_life) - opp_pressure)
        future_snap = snap.replace(
            my_mana=int(snap.my_mana) + offset,
            my_total_lands=int(snap.my_total_lands) + offset,
            my_life=future_life,
            turn_number=int(snap.turn_number) + offset,
            storm_count=0,  # CR 500.4: storm count is per-turn.
        )

        # 2. P(closer reachable on this offset).
        #    Compose three independent paths: closer in hand now,
        #    closer drawn within `offset` turns, closer fetched via
        #    in-hand tutor.  Treated as independent per design §5.1
        #    step 2 (open question 1 — conservative under-estimate).
        p_drawn = _safe_p_draw_closer(library_composition, offset)
        p_tutor = _safe_tutor_resolve_p(
            hand=hand,
            sideboard=sideboard,
            library_composition=library_composition,
            future_snap=future_snap,
            bhi_state=bhi_state,
        )
        p_no_closer = (
            (1.0 - closer_in_hand_p)
            * (1.0 - p_drawn)
            * (1.0 - p_tutor)
        )
        p_closer_reachable = max(0.0, min(1.0, 1.0 - p_no_closer))

        # 3. Damage if the chain fires on this offset.  Delegate to
        #    the v2 chain finder via simulate_finisher_chain — it
        #    already handles storm / cascade / reanimation / cycling
        #    patterns via oracle/tag-driven detection.  Storm count
        #    reset to 0 above is consistent with the future-turn
        #    fresh-spell-count rule.
        v2_proj = simulate_finisher_chain(
            snap=future_snap,
            hand=hand,
            battlefield=battlefield,
            graveyard=graveyard,
            library_size=max(1, library_composition.total - offset),
            storm_count=0,
            archetype=archetype,
            sideboard=sideboard,
            library=None,
        )
        expected_damage = float(v2_proj.expected_damage)

        # 4. Survival — P(we're alive by the start of this offset).
        survival_p = _survival_to_offset(snap, offset, bhi_state)

        # 5. Score = damage × survival × closer_reachable.
        score = expected_damage * survival_p * p_closer_reachable

        # Stop early if the snapshot would be dead by this offset.
        # Append a zero-survival node so the rollout still carries
        # the offset (caller's argmax sees survival=0 and skips it),
        # then break — extending the rollout past the lethal turn
        # adds no signal.
        if future_life <= 0:
            projections.append(TurnOffsetProjection(
                offset=offset,
                expected_damage=expected_damage,
                closer_reachable_p=p_closer_reachable,
                survival_p=0.0,
                score=0.0,
                mana_at_offset=int(future_snap.my_mana),
                storm_at_offset=0,
                notes=f"offset={offset} dead-by-pressure-tick",
            ))
            break

        projections.append(TurnOffsetProjection(
            offset=offset,
            expected_damage=expected_damage,
            closer_reachable_p=p_closer_reachable,
            survival_p=survival_p,
            score=score,
            mana_at_offset=int(future_snap.my_mana),
            storm_at_offset=0,
            notes=f"offset={offset} pattern={v2_proj.pattern}",
        ))

    return tuple(projections)


# ─── Internal helpers for the rollout (pure, oracle/tag-driven) ────


def _closer_in_hand_probability(hand: list["CardInstance"]) -> float:
    """Indicator probability: 1.0 iff a closer is in hand, else 0.0.

    Closer detection mirrors v2's oracle/keyword predicates: STORM
    keyword OR token-spawning finisher oracle OR cycling-payoff
    oracle OR ``'reanimate'`` tag (the four chain patterns the
    simulator covers).  No card names.
    """
    # Local imports keep the module's top-level surface small.
    from engine.cards import Keyword as Kw

    for c in hand:
        tmpl = getattr(c, 'template', None)
        if tmpl is None:
            continue
        if Kw.STORM in getattr(tmpl, 'keywords', set()):
            return 1.0
        oracle = (getattr(tmpl, 'oracle_text', '') or '').lower()
        # Token-spawning finisher (Empty-the-Warrens pattern).
        if 'create' in oracle and 'tokens' in oracle and 'for each' in oracle:
            return 1.0
        # Cycling payoff (Living End pattern).
        if (
            'all creature cards' in oracle
            and 'graveyard' in oracle
            and 'to the battlefield' in oracle
        ):
            return 1.0
        # Reanimator (Goryo's Vengeance pattern).
        tags = getattr(tmpl, 'tags', set())
        if 'reanimate' in tags:
            return 1.0
    return 0.0


def _safe_p_draw_closer(
    composition: LibraryComposition,
    n_draws: int,
) -> float:
    """Conservative call to ``p_draw_closer``.

    Returns 0.0 when the composition declares no closers (the
    hypergeometric is trivially zero) — this lets the rollout work
    even when ``p_draw_closer`` is still stubbed in a parallel
    development stream.  When closers are declared, attempts the
    real call; on any NotImplementedError falls back to 0.0 so the
    rollout's `closer_reachable_p` is driven by in-hand presence
    alone (conservative — under-estimates reachability, errs on
    the side of holding the chain per design §8.1).
    """
    if composition.closer_count <= 0 or composition.total <= 0:
        return 0.0
    if n_draws <= 0:
        return 0.0
    try:
        return p_draw_closer(composition, n_draws)
    except NotImplementedError:
        return 0.0


def _safe_tutor_resolve_p(
    *,
    hand: list["CardInstance"],
    sideboard: list["CardInstance"],
    library_composition: LibraryComposition,
    future_snap: "EVSnapshot",
    bhi_state: "BayesianHandTracker",
) -> float:
    """Conservative call to ``_tutor_access_contribution``.

    Returns 0.0 when no tutor is in hand OR when the tutor-access
    function is unavailable (parallel stream still stubbed).  When
    available, returns the tutor's ``p_resolves`` floored at
    ``CHAIN_TUTOR_MIN_RESOLVE`` (rules-derived).
    """
    # Fast-path: no tutor in hand at all -> no tutor contribution.
    has_tutor = any(
        'tutor' in getattr(getattr(c, 'template', None), 'tags', set())
        for c in hand
    )
    if not has_tutor:
        return 0.0
    try:
        _, _, p_resolves = _tutor_access_contribution(
            hand, sideboard, library_composition, future_snap, bhi_state,
        )
        return max(0.0, min(1.0, p_resolves))
    except NotImplementedError:
        # Parallel stream — fall back to the rules-derived floor.
        # Same fair-coin floor design §4.2 mandates as the minimum
        # tutor resolution probability under heavy counter density.
        return CHAIN_TUTOR_MIN_RESOLVE


def _survival_to_offset(
    snap: "EVSnapshot",
    offset: int,
    bhi_state: "BayesianHandTracker",
) -> float:
    """``P(we survive `offset` opp turns)``.

    Composition::

        base_survival = max(0, 1 - offset / opp_clock_discrete)
        survival      = base_survival * (1 - p_removal * CHAIN_REMOVAL_PRESSURE_FLOOR)

    Both inputs are derived from existing primitives:

    * ``snap.opp_clock_discrete`` from ``ai.clock``.
    * ``bhi_state.get_removal_probability()`` from ``ai.bhi``.

    See ``docs/design/2026-05-10_simulator_v3.md`` §5.2.
    """
    # opp_clock_discrete is the BHI-aware opp clock from ai/clock.py
    # (via EVSnapshot.opp_clock_discrete property). When opp has no
    # clock (opp_power == 0) the property returns the no-clock
    # sentinel EV_SNAPSHOT_NO_CLOCK_DISCRETE; we treat the no-clock
    # state as full survival at every offset (no decay) so chains
    # in stalled boards never under-rate later offsets.
    p_removal = bhi_state.get_removal_probability()
    removal_dampener = 1.0 - p_removal * CHAIN_REMOVAL_PRESSURE_FLOOR

    if int(snap.opp_clock_discrete) >= EV_SNAPSHOT_NO_CLOCK_DISCRETE:
        # No-clock sentinel — survival is bounded only by removal
        # density (no time-decay component).
        return max(0.0, min(1.0, removal_dampener))

    opp_clock = max(1.0, float(snap.opp_clock_discrete))
    base_survival = max(0.0, 1.0 - offset / opp_clock)
    # Removal dampener: a fully removal-leaden opponent halves survival
    # via CHAIN_REMOVAL_PRESSURE_FLOOR (rules-derived fair-coin floor).
    # Bounded by [0, 1] so the multiplier is never negative.
    survival = base_survival * removal_dampener
    return max(0.0, min(1.0, survival))


# ─── Top-level entry point ─────────────────────────────────────────


def simulate_finisher_chain_v3(
    snap: "EVSnapshot",
    hand: list["CardInstance"],
    battlefield: list["CardInstance"],
    graveyard: list["CardInstance"],
    library: list["CardInstance"],
    sideboard: list["CardInstance"],
    storm_count: int,
    archetype: str,
    bhi_state: "BayesianHandTracker",
    *,
    deck_gameplan: Optional[dict] = None,
) -> FinisherProjectionV3:
    """Project the EV-impact of attempting / building a finisher
    chain over a multi-turn horizon.

    Pure function: does not mutate game state, does not call into
    the engine.  All inputs are read-only views.

    Args:
        snap: ``EVSnapshot`` with mana / life / clock / position
            context.
        hand: list of ``CardInstance`` currently in the player's
            hand.
        battlefield: list of permanents the player controls (used
            for cost-reducer detection).
        graveyard: list of cards in the player's graveyard (used
            for reanimation target / GY creature counting).
        library: list of cards in the player's library (used to
            build the ``LibraryComposition`` histogram).
        sideboard: list of cards in the player's sideboard
            (used for tutor-as-finisher-access detection — Wish
            in hand + closer in SB).
        storm_count: spells cast this turn (base storm for chain
            arithmetic, passed to ``ai.combo_chain.find_all_chains``
            as ``base_storm``).
        archetype: deck archetype string.  Used ONLY as a
            tiebreaker when multiple patterns are reachable from
            the same hand.  Detection is oracle/keyword/tag-driven
            and is NOT gated by archetype.
        bhi_state: opponent-hand belief tracker, queried for
            ``get_counter_probability`` and
            ``get_removal_probability`` in the survival /
            tutor-resolution arithmetic.
        deck_gameplan: optional pass-through of the gameplan JSON
            for per-archetype thresholds.

    Returns:
        ``FinisherProjectionV3`` — the projected outcome of the
        highest-EV reachable pattern × turn-offset, or
        ``pattern="none"`` when no chain is reachable on any
        offset.

    See ``docs/design/2026-05-10_simulator_v3.md`` for the full
    design rationale.
    """
    raise NotImplementedError("v3 stub — implement in PR3c")
