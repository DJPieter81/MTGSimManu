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
    raise NotImplementedError("v3 stub — implement in PR3c")


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
    raise NotImplementedError("v3 stub — implement in PR3c")


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
