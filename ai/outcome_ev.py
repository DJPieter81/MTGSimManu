"""OutcomeDistribution framework for principled per-spell EV.

Replaces the patchwork of patience-gate clamps with probability-
weighted outcome aggregation. All outcome values in Δ(P_win) units.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Dict, Optional
import math


# Phase-2c.1 dispatcher flag.  When True, `ev_player._score_spell`
# routes lethal-this-turn combo chains through `build_combo_distribution`
# (state-query gate via `combo_chain.find_all_chains`).  Non-lethal-now
# combo cards fall through to the legacy `compute_play_ev` +
# `_combo_modifier` path unchanged, preserving Storm/Goryo's multi-turn
# evaluation that the single-turn distribution model can't capture.
#
# Phase-2a shipped with this OFF (parity).  Phase-2b flipped it ON
# without the gate and regressed Storm 24%->3.8%.  Phase-2c.1 ships
# with the gate AND the flag ON.
OUTCOME_DIST_COMBO = True  # Phase 2c.1 — state-query routing


class Outcome(Enum):
    """Five outcome categories for any spell cast.

    Maps onto every spell type:
    - Combo enabler: full distribution used
    - Creature: COMPLETE=lands+survives+swings, PARTIAL=lands but answered,
                FIZZLE=no legal target, DISRUPTED=countered, NEUTRAL=traded
    - Removal: COMPLETE=kills intended, PARTIAL=kills lesser,
               FIZZLE=no target, DISRUPTED=countered, NEUTRAL=trades 1-for-1
    - Cantrip: COMPLETE=draws enabler, PARTIAL=draws land needed,
               FIZZLE/DISRUPTED used; NEUTRAL=pure cycling
    """
    COMPLETE_COMBO = auto()
    PARTIAL_ADVANCE = auto()
    FIZZLE = auto()
    DISRUPTED = auto()
    NEUTRAL = auto()
    # Reserved for future cardinality growth without enum breakage
    RESERVED_6 = auto()
    RESERVED_7 = auto()


@dataclass
class OutcomeDistribution:
    """Probability distribution over Outcomes; values in Δ(P_win)."""
    probabilities: Dict[Outcome, float] = field(default_factory=dict)
    values: Dict[Outcome, float] = field(default_factory=dict)

    def __post_init__(self):
        # Default missing outcomes to (0.0, 0.0)
        for o in Outcome:
            self.probabilities.setdefault(o, 0.0)
            self.values.setdefault(o, 0.0)

    def expected_value(self) -> float:
        """Σ P(o) × value(o)."""
        return sum(self.probabilities[o] * self.values[o]
                   for o in Outcome)

    def normalize(self) -> 'OutcomeDistribution':
        """Return new distribution with probabilities summing to 1.

        If all probabilities are zero, returns a NEUTRAL=1 distribution
        (no-effect prediction).
        """
        s = sum(self.probabilities.values())
        if s <= 0.0:
            new_probs = {o: 0.0 for o in Outcome}
            new_probs[Outcome.NEUTRAL] = 1.0
            return OutcomeDistribution(
                probabilities=new_probs,
                values=dict(self.values),
            )
        return OutcomeDistribution(
            probabilities={o: self.probabilities[o] / s for o in Outcome},
            values=dict(self.values),
        )

    def is_well_formed(self, tol: float = 1e-9) -> bool:
        """All probabilities in [0,1] and sum within tol of 1."""
        if not all(0.0 <= self.probabilities[o] <= 1.0 + tol
                   for o in Outcome):
            return False
        return abs(sum(self.probabilities.values()) - 1.0) < tol


def p_draw_in_n_turns(library_size: int, target_count: int,
                      n_draws: int) -> float:
    """Exact hypergeometric P(at least one of K targets in n draws).

    Math: P = 1 - C(N-K, n) / C(N, n)

    Where N=library_size, K=target_count, n=n_draws.
    Uses math.comb (Python 3.8+).

    Edge cases:
    - target_count == 0 → 0.0 (impossible)
    - n_draws == 0 → 0.0 (no draws)
    - n_draws >= library_size → 1.0 (will see whole library)
    - target_count >= library_size → 1.0 (every card is a target)
    """
    if target_count <= 0 or n_draws <= 0:
        return 0.0
    if target_count >= library_size:
        return 1.0
    if n_draws >= library_size:
        return 1.0
    miss = math.comb(library_size - target_count, n_draws)
    total = math.comb(library_size, n_draws)
    return 1.0 - (miss / total)


def bayesian_update(prior: float, p_E_given_T: float,
                    p_E_given_F: float) -> float:
    """Bayesian posterior given an observation.

    P(T | E) = P(E | T) × P(T) / [P(E | T) × P(T) + P(E | F) × P(F)]

    Re-export wrapping `ai.bhi.BayesianHandTracker._bayesian_update`
    semantics for callers that don't hold a BHI handle.

    Edge cases:
    - prior == 0.0 → 0.0
    - prior == 1.0 → 1.0
    - denominator == 0 → return prior unchanged (no information)
    """
    if prior <= 0.0:
        return 0.0
    if prior >= 1.0:
        return 1.0
    numerator = p_E_given_T * prior
    denominator = numerator + p_E_given_F * (1.0 - prior)
    if denominator <= 0.0:
        return prior
    return numerator / denominator


# Phase 2 dispatcher entry point. Phase 1 ships only this stub
# returning None (so unmigrated calls fall through to legacy logic).
def score_spell_via_outcome(card, snap, game, me, opp, bhi,
                            archetype, profile) -> Optional[float]:
    """Phase-2-onwards dispatcher.

    Phase 1: returns None (no spells migrated yet).
    Phase 2 will route ritual/cascade/reanimate/finisher/cantrip here.
    Phase 3 adds creature/removal/cantrip.
    """
    return None


# ──────────────────────────────────────────────────────────────────
# Phase 2a builder — combo categories (rituals / cascade / reanimate
# / finishers / combo-tutors).  Returns a 5-outcome distribution keyed
# off principled subsystems (combo_chain, p_draw_in_n_turns,
# combo_calc._compute_risk_discount, win_probability.p_win_delta).
# No card names, no magic numbers — every quantity is derived.
# ──────────────────────────────────────────────────────────────────


def _classify_combo_category(card) -> Optional[str]:
    """Oracle/tag-driven category detection.  Returns one of
    'ritual', 'cascade', 'reanimate', 'finisher', 'tutor', or None.

    Detection is exclusively from `template.tags` (set by
    `engine/card_database.py`) and `template.keywords`.  No card name
    matching — categories must hold for any new card the deck has
    never seen before.
    """
    from engine.cards import Keyword
    t = card.template
    tags = getattr(t, 'tags', set())
    keywords = getattr(t, 'keywords', set())

    # Finisher = STORM keyword (Grapeshot, Empty the Warrens).  Checked
    # FIRST because storm finishers also carry the 'combo' tag and we
    # want their kill-on-success math, not generic combo treatment.
    if Keyword.STORM in keywords:
        return 'finisher'
    # Cascade enabler (Violent Outburst, Shardless Agent, ...).
    if Keyword.CASCADE in keywords:
        return 'cascade'
    # Reanimate (Goryo's Vengeance, Persist, Reanimate, ...).
    if 'reanimate' in tags:
        return 'reanimate'
    # Ritual (Desperate Ritual, Pyretic Ritual, Manamorphose, ...).
    if 'ritual' in tags:
        return 'ritual'
    # Combo tutor (Gifts Ungiven, Wish, Unmarked Grave) — restricted
    # to cards the gameplan has tagged BOTH 'tutor' and 'combo' so
    # generic search effects (Stoneforge Mystic) don't get routed here.
    if 'tutor' in tags and 'combo' in tags:
        return 'tutor'
    return None


def _count_finishers_in_zones(me, payoff_names) -> int:
    """How many copies of the deck-declared payoffs are in hand?"""
    return sum(1 for c in me.hand if c.template.name in payoff_names)


def _count_finishers_in_library(me, payoff_names) -> int:
    """How many payoffs remain in the library?  Used for hypergeometric
    finisher-reachable probability when none are visible in hand."""
    return sum(1 for c in me.library if c.template.name in payoff_names)


def _best_creature_power_in_gy(me) -> int:
    """For reanimate sizing: largest creature in graveyard is the
    expected reanimation target.  Returns 0 if none."""
    best = 0
    for c in me.graveyard:
        t = c.template
        if t.is_creature and (t.power or 0) > best:
            best = t.power or 0
    return best


def build_combo_distribution(card, snap, game, me, opp, bhi,
                             archetype, profile):
    """Phase-2a: produce an OutcomeDistribution for combo spells.

    Returns None when the card is not in a combo category — caller
    falls through to the legacy projection in `_score_spell`.

    Probability priors:
      * `p_chain_resolves` and `p_chain_advances` come from
        `combo_chain.find_all_chains` over the live hand and mana.
      * `p_finisher_reachable` is a hand-check first; otherwise a
        hypergeometric `p_draw_in_n_turns(library, payoffs, n=2)`.
      * `p_disrupted` = `1 - _compute_risk_discount(bhi, opp)` from
        `ai/combo_calc.py` (counters + discard).

    Outcome values are Δ(P_win) units sourced from
    `win_probability.p_win_delta(before, after)`.  The "after" snapshot
    for each category is built via `dataclasses.replace` so we never
    recurse through `compute_play_ev`.

    Returned distribution is always normalised so probabilities sum
    to exactly 1.0 (modulo float epsilon).
    """
    category = _classify_combo_category(card)
    if category is None:
        return None

    # Lazy imports to avoid circular dependencies (these modules import
    # from ai.outcome_ev for the OutcomeDistribution type).
    from ai.combo_chain import find_all_chains
    from ai.combo_calc import _collect_payoff_names, _compute_risk_discount
    from ai.win_probability import p_win_delta

    # Goal-engine-derived payoff names.  When no goal_engine exists
    # (test fixtures, decks without a gameplan), fall back to an empty
    # set — the chain solver will then only return fuel-only chains.
    goal_engine = None
    deck_name = getattr(me, 'deck_name', None)
    if deck_name:
        try:
            from ai.gameplan import create_goal_engine
            goal_engine = create_goal_engine(deck_name)
        except Exception:
            goal_engine = None
    payoff_names = (_collect_payoff_names(goal_engine)
                    if goal_engine is not None else set())

    # ── Probability primitives ───────────────────────────────────
    medallions = sum(1 for c in me.battlefield
                     if 'cost_reducer' in getattr(c.template, 'tags', set()))
    mana = snap.my_mana
    storm = me.spells_cast_this_turn

    chains = find_all_chains(me.hand, mana, medallions, payoff_names, storm)
    chains_with_payoff = [c for c in chains if c.payoff_name is not None]
    fuel_only_chains = [c for c in chains if c.payoff_name is None]

    # `p_chain_resolves` — probability the chain finishes the game now.
    # Saturates at 1.0 when any chain we already see deals lethal.
    opp_life = max(1, snap.opp_life)
    lethal_chain = any(c.storm_damage >= opp_life
                       for c in chains_with_payoff)
    if lethal_chain:
        p_chain_resolves = 1.0
    elif chains_with_payoff:
        # Best damage / opp_life as a soft probability proxy — pure
        # arithmetic, no constants.
        best_damage = max(c.storm_damage for c in chains_with_payoff)
        p_chain_resolves = min(1.0, best_damage / opp_life)
    else:
        p_chain_resolves = 0.0

    # `p_chain_advances` — fuel-only chain exists AND a finisher is
    # reachable in the near future.  Without a reachable finisher, an
    # "advancing" cast is just burning a card with no plan to convert
    # it to lethal — that's a fizzle, not progress.  We compute the
    # finisher reachability prior here (it's reused below).
    finisher_in_hand = _count_finishers_in_zones(me, payoff_names)
    if finisher_in_hand > 0:
        p_finisher_reachable = 1.0
    else:
        finisher_in_lib = _count_finishers_in_library(me, payoff_names)
        p_finisher_reachable = p_draw_in_n_turns(
            library_size=max(1, len(me.library)),
            target_count=finisher_in_lib,
            n_draws=2,
        )
    p_chain_advances = (1.0 if fuel_only_chains else 0.0) * p_finisher_reachable

    # `p_finisher_reachable` was computed above for chain-advance gating.

    # `p_disrupted` — invert the safety discount from combo_calc.
    safety = _compute_risk_discount(bhi, opp)
    p_disrupted = max(0.0, min(1.0, 1.0 - safety))

    # ── Per-category overrides for non-storm shells ──────────────
    # Reanimate doesn't go through combo_chain; its readiness depends
    # on (a) a legal target sitting in the graveyard and (b) the spell
    # itself being castable.  We override the chain-derived priors
    # accordingly so test fixtures with no chain solver still produce
    # the right distribution.
    if category == 'reanimate':
        target_power = _best_creature_power_in_gy(me)
        if target_power > 0:
            # Reanimation has a target → high COMPLETE_COMBO prior.
            # 1.0 if the spell is also castable, scaled otherwise.
            cmc = card.template.cmc or 0
            castable = 1.0 if mana >= cmc else 0.0
            p_chain_resolves = max(p_chain_resolves, castable)
            # The "finisher" for reanimate is the creature itself — it's
            # already in our zones, so reachability is 1.
            p_finisher_reachable = max(p_finisher_reachable, 1.0)
            p_chain_advances = 0.0
        else:
            # No legal target → guaranteed fizzle.
            p_chain_resolves = 0.0
            p_chain_advances = 0.0
            p_finisher_reachable = 0.0
    elif category == 'cascade':
        # Cascade priors: needs deck-specific shell support (Living End
        # needs cycling fuel in GY).  Without a gameplan-derived
        # readiness signal we keep the chain-based prior but cap
        # PARTIAL_ADVANCE low for empty-shell tests.
        if not chains_with_payoff and not fuel_only_chains:
            p_chain_resolves = 0.0
            p_chain_advances = 0.0
    # 'ritual', 'finisher', and 'tutor' rely entirely on the
    # combo_chain priors computed above.

    # ── Distribution math ────────────────────────────────────────
    p_complete = p_chain_resolves * p_finisher_reachable * (1.0 - p_disrupted)
    p_partial = p_chain_advances * (1.0 - p_complete)
    p_fizzle = ((1.0 - p_chain_resolves - p_chain_advances)
                * (1.0 - p_disrupted))
    p_disrupt = p_disrupted

    # Clamp negatives that arise when chain/advance probabilities push
    # the residual sum slightly above 1 (e.g. p_chain_resolves +
    # p_chain_advances both = 1).  Compute NEUTRAL last so it absorbs
    # only positive residue — never inflates the total above 1.
    p_complete = max(0.0, p_complete)
    p_partial = max(0.0, p_partial)
    p_fizzle = max(0.0, p_fizzle)
    p_disrupt = max(0.0, p_disrupt)
    # NEUTRAL absorbs whatever residue is left to keep total = 1.0.
    p_neutral = max(0.0, 1.0 - (p_complete + p_partial + p_fizzle + p_disrupt))

    # ── Outcome values (Δ(P_win) units) ──────────────────────────
    my_arch = archetype or 'midrange'
    opp_arch = getattr(opp, 'deck_name', None) or 'midrange'
    # Map opponent deck_name to archetype string when possible.
    try:
        from ai.ev_player import _get_archetype
        opp_arch = _get_archetype(opp_arch) or opp_arch
    except Exception:
        pass

    # COMPLETE: kill-on-success for storm/finisher/cascade; for
    # reanimate, project a board with the reanimated creature.
    if category == 'reanimate':
        target_power = _best_creature_power_in_gy(me)
        after = replace(
            snap,
            my_power=snap.my_power + target_power,
            my_creature_count=snap.my_creature_count + 1,
        )
    else:
        # Lethal-on-success: zero opponent's life, p_win saturates near 1.
        after = replace(snap, opp_life=0)
    v_complete = p_win_delta(snap, after, my_arch, opp_arch)

    # PARTIAL_ADVANCE: cantrip-equivalent — credit one extra card seen.
    after_partial = replace(
        snap, cards_drawn_this_turn=snap.cards_drawn_this_turn + 1)
    v_partial = p_win_delta(snap, after_partial, my_arch, opp_arch)

    # FIZZLE / DISRUPTED / NEUTRAL: zero swing in P_win units.  The
    # legacy heuristic clamp handles tempo loss; the OutcomeDistribution
    # framework only credits real position changes.
    v_fizzle = 0.0
    v_disrupt = 0.0
    v_neutral = 0.0

    dist = OutcomeDistribution(
        probabilities={
            Outcome.COMPLETE_COMBO: p_complete,
            Outcome.PARTIAL_ADVANCE: p_partial,
            Outcome.FIZZLE: p_fizzle,
            Outcome.DISRUPTED: p_disrupt,
            Outcome.NEUTRAL: p_neutral,
        },
        values={
            Outcome.COMPLETE_COMBO: v_complete,
            Outcome.PARTIAL_ADVANCE: v_partial,
            Outcome.FIZZLE: v_fizzle,
            Outcome.DISRUPTED: v_disrupt,
            Outcome.NEUTRAL: v_neutral,
        },
    )
    return dist.normalize()
