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

from math import comb
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


# Tag-bucket whitelist.  Same string keys produced by
# ``engine/card_database.py``'s tag pass.  Keeping the list explicit
# (rather than bucketing every tag we see) guarantees the histogram
# stays a fixed schema across decks and avoids leaking ad-hoc tags
# (e.g. ``"pay_life_draw_count_2"``) into the composition.
_TAG_BUCKETS: tuple[str, ...] = (
    "ritual",
    "cantrip",
    "tutor",
    "cost_reducer",
    "flashback",
    "reanimate",
    "discard",
    "cycling",
    "cascade",
    "card_advantage",
    "combo",
)

# Default reanim-target power floor when the gameplan JSON omits the
# field.  Anchored to a creature that meaningfully threatens 20 life
# in a single swing — the v2 simulator uses 4 as the implicit floor
# via the reanimation_density gameplan field, and that's what
# ``creature_threat_value`` treats as "high power".
_DEFAULT_REANIM_POWER_FLOOR = 4


def _tags_of(card: "CardInstance") -> set[str]:
    """Read ``card.template.tags`` with a safe fallback."""
    return set(getattr(card.template, "tags", set()) or set())


def _has_storm_keyword_v3(card: "CardInstance") -> bool:
    """Mirror of ``ai.finisher_simulator._has_storm_keyword``.

    Defined inline to avoid an import cycle with the v2 simulator
    (the parallel multi-turn stream wires v2 functions in too).
    """
    from engine.cards import Keyword as Kw

    return Kw.STORM in getattr(card.template, "keywords", set())


def _has_token_finisher_oracle_v3(card: "CardInstance") -> bool:
    """Mirror of ``ai.finisher_simulator._has_token_finisher_oracle``.

    Detection: oracle text contains 'create … tokens' + 'for each'.
    Matches the predicate at ``ai/combo_calc.py:514-516`` and at
    ``ai/finisher_simulator.py:424``.
    """
    oracle = (getattr(card.template, "oracle_text", "") or "").lower()
    return (
        "create" in oracle and "tokens" in oracle and "for each" in oracle
    )


def _is_cycling_payoff_v3(card: "CardInstance") -> bool:
    """Mirror of ``ai.finisher_simulator._is_cycling_payoff``.

    Oracle pattern: 'all creature cards' + 'graveyard' + 'to the
    battlefield'.  Living-End-style.
    """
    oracle = (getattr(card.template, "oracle_text", "") or "").lower()
    return (
        "all creature cards" in oracle
        and "graveyard" in oracle
        and "to the battlefield" in oracle
    )


def _is_cascade_payoff_v3(card: "CardInstance") -> bool:
    """Mirror of ``ai.finisher_simulator._is_cascade_payoff``.

    The 'combo' tag + non-creature filter.  Cascade enablers see
    only lower-cmc spells; the deck-construction guarantee is that
    a cascade-tagged combo payoff exists in the library.
    """
    tags = _tags_of(card)
    return "combo" in tags and not getattr(card.template, "is_creature", False)


def _is_reanim_target_v3(
    card: "CardInstance",
    *,
    power_floor: int,
) -> bool:
    """A creature whose power is at or above the reanim-target floor.

    Detection is by oracle/typeline + power threshold — no card
    names.  ``power_floor`` defaults from
    ``_DEFAULT_REANIM_POWER_FLOOR`` and can be raised per deck via
    the ``reanim_target_power_floor`` gameplan field.
    """
    tmpl = card.template
    if not getattr(tmpl, "is_creature", False):
        return False
    power = getattr(tmpl, "power", None)
    if power is None:
        return False
    try:
        return int(power) >= int(power_floor)
    except (TypeError, ValueError):
        return False


def _closer_category_predicates(power_floor: int):
    """Return the ordered (category_name, predicate) tuples.

    A card can match multiple categories (e.g. a STORM-keyword
    spell with token-creating text is unusual but possible — count
    it as ``storm_closer`` since storm is the more specific
    pattern).  The build loop adds every matching category to
    ``closer_categories`` but only counts the card ONCE toward
    ``closer_count``.
    """
    return (
        ("storm_closer", _has_storm_keyword_v3),
        ("token_finisher", _has_token_finisher_oracle_v3),
        ("cycling_payoff", _is_cycling_payoff_v3),
        ("cascade_payoff", _is_cascade_payoff_v3),
        ("reanim_target", lambda c: _is_reanim_target_v3(c, power_floor=power_floor)),
    )


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
    # Per-archetype threshold for reanimation targets.  Read by tag,
    # not by deck name.
    if deck_gameplan is not None:
        power_floor = int(
            deck_gameplan.get(
                "reanim_target_power_floor",
                _DEFAULT_REANIM_POWER_FLOOR,
            )
        )
    else:
        power_floor = _DEFAULT_REANIM_POWER_FLOOR

    by_tag: dict[str, int] = {tag: 0 for tag in _TAG_BUCKETS}
    category_predicates = _closer_category_predicates(power_floor)
    category_counts: dict[str, int] = {cat: 0 for cat, _ in category_predicates}
    closer_count = 0
    total = 0

    for card in library:
        if getattr(card, "template", None) is None:
            continue
        total += 1
        tags = _tags_of(card)

        # Tag-bucket pass — count whichever of the documented tag
        # categories the card has.  A card with multiple tags adds
        # to multiple buckets (intentional — buckets are not a
        # partition; e.g. Manamorphose is both ``ritual`` and
        # ``cantrip``).
        for tag in _TAG_BUCKETS:
            if tag in tags:
                by_tag[tag] += 1

        # Closer-category pass — predicates are oracle/keyword/
        # power-based.  A card matching ANY closer predicate is
        # counted ONCE in ``closer_count`` but may register in
        # multiple ``category_counts`` buckets.
        is_closer = False
        for cat_name, predicate in category_predicates:
            try:
                hit = predicate(card)
            except Exception:
                hit = False
            if hit:
                category_counts[cat_name] += 1
                is_closer = True
        if is_closer:
            closer_count += 1

    # Mirror closer-category counts into the by_tag map so the
    # histogram stays self-describing (the design doc's §3.2 sample
    # state shows ``storm_closer``, ``token_finisher`` etc. as keys
    # alongside ``ritual``, ``cantrip``).  This costs nothing and
    # makes ``by_tag.get('storm_closer')`` work for downstream
    # consumers without a separate lookup.
    for cat_name, count in category_counts.items():
        by_tag[cat_name] = count

    closer_categories: tuple[str, ...] = tuple(
        cat for cat, count in category_counts.items() if count > 0
    )

    return LibraryComposition(
        total=total,
        by_tag=by_tag,
        closer_count=closer_count,
        closer_categories=closer_categories,
    )


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
    # Boundary: zero draws → no closer can be drawn.
    if n_draws <= 0:
        return 0.0

    total = int(composition.total)
    if total <= 0:
        return 0.0

    # Resolve the closer count.  When the caller narrows
    # ``closer_categories``, count only the requested categories
    # from ``composition.by_tag``.  When None, fall back to the
    # composition's full ``closer_count``.
    if closer_categories is None:
        closer_count = int(composition.closer_count)
    else:
        closer_count = 0
        for cat in closer_categories:
            closer_count += int(composition.by_tag.get(cat, 0))

    if closer_count <= 0:
        return 0.0

    non_closer = total - closer_count
    if non_closer < 0:
        # Defensive: shouldn't happen, but treat as "every card is
        # a closer" → certainty.
        return 1.0

    # Ceiling: more draws than the library has → we'd draw every
    # card, including at least one closer.  This is also what the
    # hypergeometric arithmetic implies but ``math.comb`` returns
    # 0 when k > n, which would produce 1 - 0/anything = 1.0
    # naturally for the numerator path AS LONG AS we clamp the
    # denominator to a valid n_draws.  Clamp here for clarity.
    effective_draws = min(n_draws, total)
    if effective_draws > non_closer:
        # All non-closer cards exhausted → closer guaranteed.
        return 1.0

    numerator = comb(non_closer, effective_draws)
    denominator = comb(total, effective_draws)
    if denominator == 0:
        # Pathological: total == 0 path already returned above, so
        # this branch is unreachable in practice; guard anyway.
        return 0.0

    p = 1.0 - (numerator / denominator)
    # Clamp into [0, 1] to absorb any float-rounding drift.
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


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
