"""OutcomeDistribution framework for principled per-spell EV.

Replaces the patchwork of patience-gate clamps with probability-
weighted outcome aggregation. All outcome values in Δ(P_win) units.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Optional
import math


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
