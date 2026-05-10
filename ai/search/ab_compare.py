"""Phase 5 — A/B comparison harness for ISMCTS vs heuristic.

Closes the Phase 4A Week-4 acceptance gate placeholder. Given a
SearchState fixture and two planners (heuristic + ISMCTS), runs
forward simulation from each picked action across N reseeded
rollouts and reports the win-rate difference with a p-value
proxy.

Why this is its own module
--------------------------
The ``apply_action`` function in ``snapshot_adapter.py`` is the
forward-simulation primitive — calling it repeatedly with the
heuristic_rollout policy gives us a Monte Carlo estimate of the
position's terminal reward. To compare two planners' picks, we
fix the post-pick state and roll forward the same number of
times.

This is a Phase-5 utility: callers (acceptance tests, replay
analysis) decide when to invoke it. It does NOT block matrix
sims; the heuristic path stays the default.

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md §
acceptance gate.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Callable, List

from ai.search.ismcts import (
    ActionEnumerator,
    ISMCTSPlanner,
    RolloutPolicy,
    SearchConfig,
    StateTransition,
    TerminalEvaluator,
)


@dataclass(frozen=True)
class ABResult:
    """Result of comparing two planners' picks on the same state.

    Attributes:
      heuristic_action: action picked by the heuristic planner.
      ismcts_action: action picked by the ISMCTS planner.
      heuristic_mean_reward: mean terminal reward across N
        forward sims rolled out from the heuristic's pick.
      ismcts_mean_reward: same for ISMCTS.
      mean_diff: ``ismcts_mean_reward - heuristic_mean_reward``.
        Positive = ISMCTS wins.
      p_value_proxy: rough Wilcoxon-ish p-value via the
        rank-sum signed mean / sqrt(N) shape. NOT a real
        statistical test; for orderings only.
      n_rollouts: number of forward sims per planner.
    """

    heuristic_action: object
    ismcts_action: object
    heuristic_mean_reward: float
    ismcts_mean_reward: float
    mean_diff: float
    p_value_proxy: float
    n_rollouts: int

    @property
    def ismcts_strict_win(self) -> bool:
        """True if ISMCTS strictly dominates at p_value_proxy < 0.10
        (the acceptance-gate threshold from the scoping doc).
        Same-action picks count as ties (no strict win, no
        regression)."""
        if self.heuristic_action == self.ismcts_action:
            return False
        return self.mean_diff > 0 and self.p_value_proxy < 0.10

    @property
    def heuristic_strict_win(self) -> bool:
        """Same shape inverted. Phase 4A acceptance: 0 of these."""
        if self.heuristic_action == self.ismcts_action:
            return False
        return self.mean_diff < 0 and self.p_value_proxy < 0.10


def _forward_sim_reward(
    state,
    chosen_action,
    enumerate_actions: ActionEnumerator,
    rollout_policy: RolloutPolicy,
    evaluate_terminal: TerminalEvaluator,
    transition: StateTransition,
    rng: random.Random,
    sim_depth: int,
) -> float:
    """Apply chosen_action then roll out for sim_depth more
    transitions, returning the terminal evaluation."""
    state = transition(state, chosen_action, rng)
    for _ in range(sim_depth):
        actions = enumerate_actions(state)
        if not actions:
            break
        next_action = rollout_policy(state, rng)
        state = transition(state, next_action, rng)
    return evaluate_terminal(state)


def compare_planners(
    state_factory: Callable[[], object],
    enumerate_actions: ActionEnumerator,
    rollout_policy: RolloutPolicy,
    evaluate_terminal: TerminalEvaluator,
    transition: StateTransition,
    heuristic_picker: Callable[[object], object],
    ismcts_config: SearchConfig,
    n_rollouts: int = 20,
    sim_depth: int = 4,
    base_seed: int = 0,
) -> ABResult:
    """A/B compare a heuristic picker vs ISMCTS on a fixture.

    Args:
      state_factory: callable returning a fresh root state. Must
        return a deep-cloneable state — we call it once per A
        and once per B to avoid cross-run state contamination.
      enumerate_actions / rollout_policy / evaluate_terminal /
      transition: the four ISMCTS callables, also used by the
        forward-sim harness.
      heuristic_picker: ``state -> action``. The non-MCTS planner
        being compared against. Typically wraps the existing
        single-ply scorer.
      ismcts_config: SearchConfig for the ISMCTS pick. Seed is
        applied to the planner's internal rng.
      n_rollouts: forward sims per side.
      sim_depth: turns to simulate forward after applying the pick.
      base_seed: seed for the forward-sim rng. Per-rollout rngs
        derive from base_seed + rollout index.

    Returns: ``ABResult``.
    """
    # 1. Pick.
    h_state = state_factory()
    h_pick = heuristic_picker(h_state)

    m_state = state_factory()
    planner = ISMCTSPlanner(config=ismcts_config)
    m_pick = planner.search(
        root_state=m_state,
        enumerate_actions=enumerate_actions,
        rollout_policy=rollout_policy,
        evaluate_terminal=evaluate_terminal,
        transition=transition,
    )

    # 2. Forward-sim each pick from a fresh state.
    h_rewards: List[float] = []
    m_rewards: List[float] = []
    for i in range(n_rollouts):
        rng_h = random.Random(base_seed + i)
        rng_m = random.Random(base_seed + i)
        h_rewards.append(_forward_sim_reward(
            state_factory(), h_pick, enumerate_actions,
            rollout_policy, evaluate_terminal, transition,
            rng_h, sim_depth,
        ))
        m_rewards.append(_forward_sim_reward(
            state_factory(), m_pick, enumerate_actions,
            rollout_policy, evaluate_terminal, transition,
            rng_m, sim_depth,
        ))

    h_mean = statistics.fmean(h_rewards)
    m_mean = statistics.fmean(m_rewards)
    diff = m_mean - h_mean

    # 3. Cheap p-value proxy: |mean_diff| / (pooled_std/sqrt(N)).
    # Map the t-statistic to a rough p via the normal-tail
    # approximation. Not a rigorous test; see scoping doc note
    # that the acceptance gate uses this for orderings only.
    pooled_var = (
        statistics.pvariance(h_rewards)
        + statistics.pvariance(m_rewards)
    ) / 2.0
    if pooled_var <= 0:
        # Both sides have zero variance. If their means agree, the
        # rollouts are unanimous on the same outcome → tie. If
        # their means disagree, the rollouts are unanimous on
        # different outcomes → maximally decisive evidence.
        if abs(diff) < 1e-9:
            p_value = 1.0
        else:
            p_value = 0.0
    else:
        import math
        se = math.sqrt(2.0 * pooled_var / n_rollouts)
        if se == 0:
            p_value = 1.0
        else:
            t_stat = abs(diff) / se
            # Two-sided normal-tail approximation.
            p_value = 2.0 * (1.0 - _phi(t_stat))

    return ABResult(
        heuristic_action=h_pick,
        ismcts_action=m_pick,
        heuristic_mean_reward=h_mean,
        ismcts_mean_reward=m_mean,
        mean_diff=diff,
        p_value_proxy=p_value,
        n_rollouts=n_rollouts,
    )


def _phi(x: float) -> float:
    """Normal-distribution CDF via the erf-based formula. Pure
    Python, no scipy dependency."""
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def acceptance_gate(
    fixtures: List[dict],
    ismcts_config: SearchConfig,
    heuristic_picker_factory: Callable[[dict], Callable],
    enumerate_actions: ActionEnumerator,
    rollout_policy: RolloutPolicy,
    evaluate_terminal: TerminalEvaluator,
    transition: StateTransition,
    state_factory_for: Callable[[dict], Callable[[], object]],
    n_rollouts: int = 20,
    sim_depth: int = 4,
) -> dict:
    """Run the Phase 4A acceptance gate across a fixture set.

    Returns a dict::
        {
            "results": [ABResult, ...],   # one per fixture
            "ismcts_strict_wins": int,
            "heuristic_strict_wins": int,
            "ties": int,                  # same-action picks
            "passed": bool,               # ≥4 strict ISMCTS wins
                                          # AND 0 heuristic wins
        }

    Caller wires ``state_factory_for(fixture) -> () -> SearchState``
    and ``heuristic_picker_factory(fixture) -> state -> action``.
    """
    results = []
    for fix in fixtures:
        state_factory = state_factory_for(fix)
        heuristic_picker = heuristic_picker_factory(fix)
        result = compare_planners(
            state_factory=state_factory,
            enumerate_actions=enumerate_actions,
            rollout_policy=rollout_policy,
            evaluate_terminal=evaluate_terminal,
            transition=transition,
            heuristic_picker=heuristic_picker,
            ismcts_config=ismcts_config,
            n_rollouts=n_rollouts,
            sim_depth=sim_depth,
            base_seed=fix.get("id", 0) * 1000,
        )
        results.append(result)

    ismcts_wins = sum(1 for r in results if r.ismcts_strict_win)
    heuristic_wins = sum(1 for r in results if r.heuristic_strict_win)
    ties = sum(1 for r in results
               if r.heuristic_action == r.ismcts_action)

    return {
        "results": results,
        "ismcts_strict_wins": ismcts_wins,
        "heuristic_strict_wins": heuristic_wins,
        "ties": ties,
        "passed": ismcts_wins >= 4 and heuristic_wins == 0,
    }
