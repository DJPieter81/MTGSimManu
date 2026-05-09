"""ISMCTS determinizer — Phase 4A Week 3.

Imperfect-information game search needs a way to sample concrete
"what could the opponent have" hypotheses. The determinizer
produces fresh search states with perturbed opponent-side fields
on each call; ISMCTS averages search statistics across
determinizations to converge on the imperfect-information optimum.

This is **PIMC-style** (Perfect-Information MCTS via
determinization) — the simpler cousin of full ISMCTS. It loses
some exploitability bound for stochastic games but is
substantially simpler to implement and debug. The Week-3
deliverable lands PIMC; full information-set merging is Phase 5+.

Reuse:
- ``ai/bhi.py`` for the prior over opponent threats (informs how
  much to perturb).
- ``ai/search/snapshot_adapter.py`` for the SearchState wrapper.

Reference:
- docs/research/2026-05_phase_4a_ismcts_scoping.md
- docs/research/2026-05_mtg_ai_landscape.md §1
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from ai.ev_evaluator import EVSnapshot
from ai.search.snapshot_adapter import SearchState


@dataclass(frozen=True)
class PerturbationConfig:
    """How much to vary each opp-side field per determinization.

    The opponent-side state in EVSnapshot is the AI's *belief*
    about what the opponent has — counts derived from observed
    plays + Bayesian priors. The actual opp state may differ in:
      - opp_hand_size: 0 (counted) but unknown contents
      - opp_creature_count: known (visible)
      - opp_power / opp_toughness: known if creatures visible

    Hidden information lives in the *contents* of opp's hand and
    library, not in the counts. The Phase 4A Week-3 determinizer
    perturbs the opp_threat_density input — what fraction of the
    unknown hand is a "live threat" (creature, removal, burn).

    Per the bhi.py p_threat_in_hand_density field:
      - 0.0 = opp has no threats
      - 0.5 = balanced (default prior for unknown decks)
      - 1.0 = every hand card is a threat
    """

    threat_density_sigma: float = 0.15
    """Standard deviation of the Gaussian noise added to the
    p_threat_in_hand_density estimate per determinization.
    0.15 = ±30% perturbation at the 2σ tail."""

    counter_prob_sigma: float = 0.10
    """Same shape for opp_p_counter (P(opp has a counterspell))."""

    removal_prob_sigma: float = 0.10
    """Same shape for opp_p_removal."""


@dataclass
class Determinizer:
    """Generates perturbed SearchStates for ISMCTS rollouts.

    Stateless except for the rng — call ``sample(state, rng)``
    repeatedly to get fresh determinizations. The underlying
    snapshot is cloned via fast_replace so the original state
    is never mutated.

    Currently perturbs three opp-side belief fields. Future
    Phase-5 ISMCTS variants would also resample concrete hand
    contents (which cards specifically), but the EVSnapshot
    abstraction holds counts not contents — so the Week-3 PIMC
    operates entirely at the count / probability level.

    Note: the current EVSnapshot model does not expose
    p_threat_in_hand_density / opp_p_counter as fields directly;
    those live in the BHI tracker. The Week-3 deliverable
    perturbs the snapshot fields that ARE present (opp_power,
    opp_creature_count) within rules-feasible bounds, simulating
    "what if the opp has one more / one fewer threat than I
    counted." Phase 5 wires real BHI prior pulls.
    """

    config: PerturbationConfig = field(default_factory=PerturbationConfig)

    def sample(
        self,
        state: SearchState,
        rng: random.Random,
    ) -> SearchState:
        """Return a fresh SearchState with opp-side fields
        perturbed within rules-feasible bounds.

        Bounds:
          - opp_creature_count: stays ≥ 0
          - opp_power: stays ≥ 0
          - opp_hand_size: stays ≥ 0

        The perturbation is a small integer drawn from a Gaussian
        with σ=1, clamped at 0. With probability 0.5, no
        perturbation is applied (preserves the "true" state in
        half the rollouts; cuts variance in the search).
        """
        snap = state.snapshot

        # 50% no-op: keep some rollouts on the actual visible
        # state.
        if rng.random() < 0.5:
            return state.clone()

        delta_creatures = self._gaussian_int(rng, sigma=1.0)
        delta_power = self._gaussian_int(rng, sigma=1.5)
        delta_hand = self._gaussian_int(rng, sigma=0.7)

        new_creature_count = max(0, snap.opp_creature_count + delta_creatures)
        new_power = max(0, snap.opp_power + delta_power)
        new_hand = max(0, snap.opp_hand_size + delta_hand)

        new_snap = snap.fast_replace(
            opp_creature_count=new_creature_count,
            opp_power=new_power,
            opp_hand_size=new_hand,
        )
        return SearchState(
            snapshot=new_snap,
            plays_this_turn=list(state.plays_this_turn),
            available=list(state.available),
        )

    @staticmethod
    def _gaussian_int(rng: random.Random, sigma: float) -> int:
        """Draw an integer perturbation ~ N(0, sigma), rounded."""
        return round(rng.gauss(0.0, sigma))


def make_determinized_transition(
    determinizer: Determinizer,
    base_transition,  # Callable[(state, action, rng), state]
):
    """Wrap a transition function so the FIRST call per rollout
    samples a determinization, subsequent calls within the rollout
    use the determinized state.

    ISMCTS calls transition many times per rollout (one per ply).
    We want determinization to happen ONCE per rollout, at the
    root, not at every ply. This wrapper memoizes the
    determinization per "rollout id" (tracked via the rng object's
    identity).
    """
    seen_rollouts: dict = {}

    def transition(state: SearchState, action, rng: random.Random):
        rollout_id = id(rng)
        if rollout_id not in seen_rollouts:
            # First transition in this rollout — apply
            # determinization to the state, THEN apply the action.
            determinized = determinizer.sample(state, rng)
            seen_rollouts[rollout_id] = True
            return base_transition(determinized, action, rng)
        return base_transition(state, action, rng)

    return transition
