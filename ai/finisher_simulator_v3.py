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


def _zone_has_closer_target(zone: list["CardInstance"]) -> bool:
    """True iff ``zone`` contains at least one closer-category card.

    Closer categories are detected by keyword / oracle predicate —
    the same predicates ``build_library_composition`` uses to
    populate ``LibraryComposition.closer_categories``.  No card
    names enter or leave this function.

    Used to decide whether the sideboard contains a real fetch
    target for a tutor in hand.  Mirrors v2's
    ``_scan_zone_for_storm_closer`` and
    ``_tutor_has_payoff_access`` SB scan in
    ``ai/combo_calc.py:564-596``: STORM keyword OR token-finisher
    oracle.  The cycling/cascade/reanim closers are valid targets
    in principle but are not surfaced here in the v3-first
    implementation — the live storm test fixture only exercises
    storm/token; broader closer-zone discovery extends naturally
    by walking ``_closer_category_predicates`` if a deck pattern
    needs it.  Out-of-scope for v3 first cut; see design §4.1.
    """
    # The current SB-fetch fixture covers the two predicates the
    # legacy combo modifier and v2 sketch agree on.  Both are
    # already implemented as pure helpers above.
    for card in zone:
        if getattr(card, "template", None) is None:
            continue
        if _has_storm_keyword_v3(card):
            return True
        if _has_token_finisher_oracle_v3(card):
            return True
    return False


def _library_composition_has_closer(
    composition: LibraryComposition,
) -> bool:
    """True iff the composition declares at least one closer in any
    closer-category bucket.

    Library reachability is composition-driven (tag/predicate
    histogram), not card-list-driven — the composition is the v3
    truth-source for what's left in the library.  Per design §3
    and the abstraction contract: zero card names.
    """
    return int(composition.closer_count) > 0


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

    Design §8.6 — per-cast vs per-projection counter density:
    ``bhi_state.get_counter_probability()`` returns a single
    posterior P; v3 applies it once per projection rather than
    composing per-cast (tutor-cast then closer-cast).  The
    one-P approximation is conservative for the *closer* cast
    (it under-counts the joint hold-counter-for-closer-too risk)
    and lenient for the *tutor* cast (it treats opp's full
    counter inventory as available against the tutor, ignoring
    that the tutor often resolves on a different turn than the
    closer).  The floor at ``CHAIN_TUTOR_MIN_RESOLVE`` bounds
    the downside.  Per-cast decomposition is deferred until v3
    ships and live traces show the approximation hurts; the
    one-P entry point keeps the call site cheap.

    See ``docs/design/2026-05-10_simulator_v3.md`` §4.
    """
    # 1. Gather every 'tutor'-tagged card in hand.  Pure tag check —
    #    detection is identical for Wish, Burning Wish, Demonic Tutor,
    #    Eladamri's Call, Summoner's Pact: anything the engine tags
    #    as 'tutor' qualifies.  No card-name conditional.
    tutors_in_hand = [
        c for c in hand
        if getattr(c, "template", None) is not None
        and "tutor" in _tags_of(c)
    ]
    if not tutors_in_hand:
        return (None, 0, 0.0)

    # 2. Confirm a real target exists in SB OR library composition.
    #    Symmetric to v2's `_has_storm_finisher` SB-or-library scan:
    #    SB walk uses the oracle/keyword predicates directly;
    #    library reachability is composition-driven (the v3
    #    truth-source — no need to iterate the real list).
    sb_has_target = _zone_has_closer_target(sideboard)
    lib_has_target = _library_composition_has_closer(library_composition)
    if not (sb_has_target or lib_has_target):
        return (None, 0, 0.0)

    # 3. Pick the cheapest tutor.  Lowest CMC minimises the chain's
    #    extra mana cost so the projection prefers the
    #    cost-efficient access path.  Mirrors design §4.2's
    #    ``min(tutors_with_access, key=lambda c: c.template.cmc)``.
    best_tutor = min(
        tutors_in_hand,
        key=lambda c: int(getattr(c.template, "cmc", 0) or 0),
    )
    extra_cost = int(getattr(best_tutor.template, "cmc", 0) or 0)

    # 4. Resolve probability — query the BHI tracker once per
    #    projection (design §8.6 one-P approximation), floor at the
    #    rules-derived ``CHAIN_TUTOR_MIN_RESOLVE``.  Per CLAUDE.md
    #    "No magic numbers": this is a rules constant, not a tuning
    #    weight.
    p_counter = float(bhi_state.get_counter_probability())
    # Clamp the posterior into [0, 1] defensively — BHI's storage is
    # plain float and a stale write can drift outside the range.
    p_counter = max(0.0, min(1.0, p_counter))
    p_resolves = max(CHAIN_TUTOR_MIN_RESOLVE, 1.0 - p_counter)
    # Clamp again to [0, 1] for output safety (floor + max can't
    # exceed 1.0 but downstream Pydantic validators are strict).
    p_resolves = max(0.0, min(1.0, p_resolves))

    return (best_tutor, extra_cost, p_resolves)


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
    # Early-return guards: no actions possible. The "no closer in
    # hand" case is NOT an early return — the offset>0 projections
    # cover it via library draw + tutor access (the entire point
    # of v3).
    if not hand:
        return FinisherProjectionV3(pattern="none")

    # 1. Build the tag-indexed library histogram. Closer categories
    #    are detected via oracle/keyword/tag predicates inside
    #    build_library_composition (no card names).
    library_composition = build_library_composition(
        library, deck_gameplan=deck_gameplan,
    )

    # Empty-library guard — only short-circuit when ALL paths are
    # clearly cut. Hand-only, tutor+SB, and PiF-extender chains
    # must still reach _project_multi_turn so v2's arithmetic
    # can detect them (mirrors v2's `_is_pif_pattern` branch at
    # ai/finisher_simulator.py:253).
    if library_composition.total == 0:
        has_closer_in_hand = _closer_in_hand_probability(hand) > 0.0
        has_tutor_with_sb_access = any(
            'tutor' in getattr(c.template, 'tags', set())
            for c in hand
        ) and bool(sideboard)
        # Flashback-recursion pattern (PiF, Yawgmoth's Will, etc.):
        # generic oracle predicate shared with v2 via
        # ai/finisher_simulator.py:_has_flashback_recursion_pattern.
        # No card names — `flashback + graveyard + instant/sorcery`.
        from ai.finisher_simulator import _has_flashback_recursion_pattern
        has_flashback_recursion = any(
            _has_flashback_recursion_pattern(c) for c in hand
        )
        if (not has_closer_in_hand
                and not has_tutor_with_sb_access
                and not has_flashback_recursion):
            return FinisherProjectionV3(pattern="none")

    # 2. Build the per-turn-offset rollout. Each TurnOffsetProjection
    #    carries (offset, expected_damage, closer_reachable_p,
    #    survival_p, score). The rollout already calls v2's
    #    simulate_finisher_chain for damage at each offset, with
    #    storm_count reset (CR 500.4).
    turn_projections = _project_multi_turn(
        snap=snap,
        hand=hand,
        battlefield=battlefield,
        graveyard=graveyard,
        sideboard=sideboard,
        library_composition=library_composition,
        storm_count=storm_count,
        archetype=archetype,
        bhi_state=bhi_state,
        max_depth=CHAIN_MULTI_TURN_DEPTH,
    )

    if not turn_projections:
        # All offsets are dead-by-pressure or unreachable — surface
        # a no-pattern result rather than crash on the empty argmax.
        return FinisherProjectionV3(pattern="none")

    # 3. Pick the offset with the highest score. Ties broken by
    #    earliest offset (faster fire is preferred at parity — same
    #    tiebreak the v2 hold-vs-fire gate uses).
    best = max(
        turn_projections,
        key=lambda p: (p.score, -p.offset),
    )

    # 4. Pull v2-compat fields from the BEST offset's chain. Re-run
    #    the v2 simulator at the best offset's snapshot delta so we
    #    can read .pattern / .closer_name / .coverage_ratio / .mana_floor
    #    / .chain_length / .closer_in_zone — these are not on
    #    TurnOffsetProjection. Same arithmetic the rollout used
    #    internally; results are consistent by construction.
    from ai.finisher_simulator import simulate_finisher_chain

    best_offset = best.offset
    opp_pressure = max(0, int(snap.opp_power)) * best_offset
    future_life = max(0, int(snap.my_life) - opp_pressure)
    future_snap = snap.replace(
        my_mana=int(snap.my_mana) + best_offset,
        my_total_lands=int(snap.my_total_lands) + best_offset,
        my_life=future_life,
        turn_number=int(snap.turn_number) + best_offset,
        storm_count=0,
    )
    v2_at_best = simulate_finisher_chain(
        snap=future_snap,
        hand=hand,
        battlefield=battlefield,
        graveyard=graveyard,
        library_size=max(1, library_composition.total - best_offset),
        storm_count=0,
        archetype=archetype,
        sideboard=sideboard,
        library=None,
    )

    # 5. Hold-value semantic preserved: positive only when the best
    #    offset is in the future. v2's hold_value field carries the
    #    same meaning ("score if we hold for next turn").
    hold_value = best.score if best_offset > 0 else 0.0

    return FinisherProjectionV3(
        pattern=v2_at_best.pattern,
        expected_damage=best.expected_damage,
        success_probability=best.closer_reachable_p * best.survival_p,
        mana_floor=v2_at_best.mana_floor,
        chain_length=v2_at_best.chain_length,
        closer_name=v2_at_best.closer_name,
        hold_value=hold_value,
        next_turn_damage=(
            turn_projections[1].expected_damage
            if len(turn_projections) > 1 else 0.0
        ),
        coverage_ratio=v2_at_best.coverage_ratio,
        closer_in_zone=v2_at_best.closer_in_zone,
        library_composition=library_composition,
        turn_projections=turn_projections,
        best_turn_offset=best_offset,
        tutor_access_chains=(),  # populated by future PR if needed
        p_closer_by_turn=tuple(
            p.closer_reachable_p for p in turn_projections
        ),
    )
