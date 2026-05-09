"""Phase 4A — UCT correctness on toy problems.

These smoke tests run the ISMCTS skeleton against deterministic
MDPs and stochastic bandits to verify:

  1. UCB1 selection converges on the highest-mean arm of a bandit
     given enough rollouts.
  2. Tree search prefers the action leading to the highest-reward
     terminal in a 2-step deterministic MDP.
  3. The skeleton handles stochastic transitions without crashing
     (correctness is verified by the reward distribution).

These tests run in milliseconds — appropriate for the inner-loop
verification protocol established in Phase 1+2 (golden fixtures
only, no matrix runs).

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
acceptance criteria.
"""
from __future__ import annotations

import random

import pytest

from ai.search.ismcts import ISMCTSPlanner, SearchConfig
from ai.search.uct_node import UCTNode


# ─── UCTNode unit tests ──────────────────────────────────────────────


class TestUCTNode:
    def test_root_starts_unvisited_with_zero_value(self):
        n = UCTNode()
        assert n.visits == 0
        assert n.total_value == 0.0
        assert n.mean_value == 0.0

    def test_unvisited_node_has_infinite_ucb1(self):
        # An unvisited child must be picked first by best_child —
        # that's the standard "UCB1 forces exploration of unseen
        # arms" semantics.
        n = UCTNode()
        assert n.ucb1() == float("inf")

    def test_expand_moves_action_to_children(self):
        n = UCTNode()
        n.untried_actions = ["a", "b", "c"]
        child = n.expand("b")
        assert "b" not in n.untried_actions
        assert "b" in n.children
        assert n.children["b"] is child
        assert child.parent is n
        assert child.action == "b"

    def test_expand_rejects_unknown_action(self):
        n = UCTNode()
        n.untried_actions = ["a"]
        with pytest.raises(ValueError):
            n.expand("z")

    def test_backpropagate_walks_to_root(self):
        root = UCTNode()
        root.untried_actions = ["a"]
        child = root.expand("a")
        child.untried_actions = ["b"]
        grandchild = child.expand("b")

        grandchild.backpropagate(0.7)

        assert grandchild.visits == 1 and grandchild.total_value == 0.7
        assert child.visits == 1 and child.total_value == 0.7
        assert root.visits == 1 and root.total_value == 0.7

    def test_best_action_returns_most_visited(self):
        n = UCTNode()
        n.untried_actions = ["a", "b"]
        ca = n.expand("a")
        cb = n.expand("b")
        # Visit 'b' more often even though 'a' has slightly higher
        # mean. best_action prefers visit count.
        ca.backpropagate(0.9)         # a: 1 visit, value 0.9
        for _ in range(5):
            cb.backpropagate(0.5)     # b: 5 visits, value 2.5
        assert n.best_action() == "b"


# ─── Multi-arm bandit smoke test ─────────────────────────────────────


class TestMultiArmBandit:
    """Toy: 4-arm Bernoulli bandit with means [0.1, 0.3, 0.5, 0.7].

    The state is an integer step count. Each "action" is an arm.
    Transition: state → state + 1. Terminal: state >= depth.
    Reward: arm-mean draw stored on the path.

    ISMCTS should converge on arm 3 (the 0.7-mean arm) given
    enough rollouts.
    """

    def _enumerate(self, state):
        # 4 arms always available until terminal. State is the
        # encoded transition tuple (step, reward) or 0 at root.
        if isinstance(state, tuple):
            return []  # post-pull = terminal
        return list(range(4)) if state < 1 else []

    def _rollout(self, state, rng):
        return rng.randrange(4)

    def _terminal(self, state):
        # The reward is summed during transitions; we read the
        # last-recorded sample here. This toy uses a simple
        # mapping: state-encoded reward.
        if isinstance(state, tuple):
            return state[1]
        return 0.0

    def _transition(self, state, action, rng):
        # Sample the Bernoulli and encode (next_step, reward).
        means = [0.1, 0.3, 0.5, 0.7]
        sample = 1.0 if rng.random() < means[action] else 0.0
        return (1, sample)

    def test_ismcts_converges_on_high_mean_arm(self):
        """With 2000 rollouts on a 4-arm Bernoulli bandit, the
        highest-mean arm (arm 3, mean 0.7) should win the
        search."""
        config = SearchConfig(
            n_rollouts=2000,
            rollout_depth=1,
            seed=42,
        )
        planner = ISMCTSPlanner(config=config)
        chosen = planner.search(
            root_state=0,
            enumerate_actions=self._enumerate,
            rollout_policy=self._rollout,
            evaluate_terminal=self._terminal,
            transition=self._transition,
        )
        assert chosen == 3, (
            f"UCB1 should converge on arm 3 (mean 0.7) with 2000 "
            f"rollouts. Got chosen={chosen}."
        )


# ─── Deterministic 2-step MDP ────────────────────────────────────────


class TestDeterministicMDP:
    """Toy: a state is a path. Two actions at each step ('left',
    'right'). After 2 steps we evaluate. Path 'left,right' has the
    highest reward (1.0); other paths are 0.5 or 0.0.

    With 200 rollouts the search must pick 'left' at the root
    (because exploring 'left' then 'right' yields the maximum)."""

    REWARDS = {
        ("left", "left"): 0.5,
        ("left", "right"): 1.0,
        ("right", "left"): 0.0,
        ("right", "right"): 0.5,
    }

    def _enumerate(self, state):
        # State is a tuple of action history.
        if len(state) >= 2:
            return []
        return ["left", "right"]

    def _rollout(self, state, rng):
        return rng.choice(["left", "right"])

    def _terminal(self, state):
        if len(state) < 2:
            return 0.0
        return self.REWARDS.get(tuple(state), 0.0)

    def _transition(self, state, action, rng):
        return tuple(list(state) + [action])

    def test_root_picks_left_to_reach_optimal_path(self):
        config = SearchConfig(
            n_rollouts=200,
            rollout_depth=2,
            seed=7,
        )
        planner = ISMCTSPlanner(config=config)
        chosen = planner.search(
            root_state=(),
            enumerate_actions=self._enumerate,
            rollout_policy=self._rollout,
            evaluate_terminal=self._terminal,
            transition=self._transition,
        )
        assert chosen == "left", (
            f"Optimal path is 'left,right' (reward 1.0). The root "
            f"should pick 'left'. Got chosen={chosen}."
        )


# ─── Determinism guarantee ───────────────────────────────────────────


def test_search_is_deterministic_under_fixed_seed():
    """Two runs with the same SearchConfig.seed must return the
    same action. This is required for matrix-sim reproducibility
    when ISMCTS is used as the planner."""
    bandit = TestMultiArmBandit()
    config = SearchConfig(n_rollouts=500, rollout_depth=1, seed=99)

    planner_a = ISMCTSPlanner(config=config)
    chosen_a = planner_a.search(
        0, bandit._enumerate, bandit._rollout,
        bandit._terminal, bandit._transition,
    )
    planner_b = ISMCTSPlanner(config=config)
    chosen_b = planner_b.search(
        0, bandit._enumerate, bandit._rollout,
        bandit._terminal, bandit._transition,
    )
    assert chosen_a == chosen_b, (
        f"Same seed must produce same answer. Got chosen_a="
        f"{chosen_a}, chosen_b={chosen_b}."
    )


def test_search_raises_on_terminal_root():
    """Calling search on a terminal root (no legal actions) is a
    programming error — must raise ValueError clearly."""
    config = SearchConfig(n_rollouts=10, seed=0)
    planner = ISMCTSPlanner(config=config)
    with pytest.raises(ValueError):
        planner.search(
            root_state=0,
            enumerate_actions=lambda s: [],  # terminal
            rollout_policy=lambda s, rng: None,
            evaluate_terminal=lambda s: 0.0,
            transition=lambda s, a, rng: s,
        )
