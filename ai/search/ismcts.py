"""Information-Set MCTS — Phase 4A skeleton.

This is the public entry point for opt-in tree search at decision
points where the heuristic scorer in ``ai/turn_planner.py``
saturates. The skeleton lands as a runnable framework with a
working bandit-level smoke test; full game-state integration is
Week 2 of the scoping doc.

Public API:

    from ai.search.ismcts import ISMCTSPlanner, SearchConfig
    planner = ISMCTSPlanner(config=SearchConfig(n_rollouts=1000))
    best = planner.search(root_state, action_enumerator,
                          rollout_policy, terminal_evaluator,
                          state_transition)

The four callables decouple the search algorithm from the engine,
making the skeleton testable on toy problems (multi-arm bandit,
simple MDPs) before wiring to ``GameState``. Game-state binding
happens in ``ai/search/game_adapter.py`` (Week 2 deliverable).

Reference:
- docs/research/2026-05_phase_4a_ismcts_scoping.md
- docs/research/2026-05_mtg_ai_landscape.md §1
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from ai.search.uct_node import UCTNode, DEFAULT_UCT_C


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchConfig:
    """Tunable search parameters.

    Defaults derived from the scoping doc's "Phase 4A pilot" budget
    (1000 rollouts × 50 ms / rollout = ~50 seconds per decision,
    appropriate for replay analysis / opt-in mode, NOT the matrix
    hot loop).
    """

    n_rollouts: int = 1000
    """Total number of select→expand→rollout→backpropagate cycles."""

    n_determinizations: int = 50
    """Maximum opponent-hand samples to try per root (information-set
    averaging). The actual count is ``min(n_determinizations,
    n_rollouts)``; each rollout uses one sampled determinization."""

    rollout_depth: int = 4
    """Maximum simulated turns during a rollout before the terminal
    evaluator is invoked. Lower = faster, less accurate. The
    scoping doc sets 4 as the sweet spot for Modern (most decisive
    plays land within 4 turns of the decision point)."""

    uct_c: float = DEFAULT_UCT_C
    """UCB1 exploration constant. ``sqrt(2)`` is theory-optimal for
    [0, 1]-bounded rewards."""

    seed: int = 0
    """Sampler seed for reproducibility. Matrix-sim reproducibility
    requires this to be deterministic per call."""


# Type aliases for the four callables. Generic over state type S
# and action type A so the skeleton tests can use simple
# tuples/ints without depending on engine.GameState.
ActionEnumerator = Callable[[Any], List[Any]]
"""``state -> list of legal actions``. Returns [] for terminal."""

RolloutPolicy = Callable[[Any, random.Random], Any]
"""``state, rng -> action``. Picks an action for the simulation
phase. Typically the existing heuristic scorer (fast, good
enough for rollouts)."""

TerminalEvaluator = Callable[[Any], float]
"""``state -> reward in [0, 1]``. Higher = better outcome for the
root player. Called when rollout exhausts ``rollout_depth`` or
the state is terminal."""

StateTransition = Callable[[Any, Any, random.Random], Any]
"""``state, action, rng -> next state``. May be stochastic
(opponent's hidden actions, drawn cards). For deterministic
games, ignore the rng arg."""


@dataclass
class ISMCTSPlanner:
    """ISMCTS top-level loop.

    For each rollout:
      1. SELECT: walk from root, picking ``best_child(uct_c)`` until
         hitting an unexpanded action or a leaf.
      2. EXPAND: pick one untried action, create the child.
      3. ROLLOUT: from the child, follow ``rollout_policy`` for up
         to ``rollout_depth`` turns, then call
         ``terminal_evaluator``.
      4. BACKPROPAGATE: add the rollout reward to the child and
         all ancestors.

    Information-set merging is per-determinization: each rollout
    samples a fresh hidden state via the ``StateTransition`` (which
    receives the rng), and statistics aggregate across
    determinizations naturally — visits and values are shared by
    the action-edge tree, not the underlying state.

    Production-integration shape (Phase 4A Week 4):
      The planner accepts an optional ``fallback`` — typically a
      ``TurnPlanner`` instance — which serves two purposes:

      1. Methods other than ``search`` that the caller expects on
         the planner (e.g. ``evaluate_response``, ``plan_attack``)
         are delegated to the fallback via ``__getattr__``. The
         heuristic planner's full API surface remains usable
         without the search wiring needing to mirror every method.
      2. ``plan_turn`` runs the heuristic plan via the fallback by
         default (the snapshot-adapter Week-2 scaffolding is not
         yet wired to GameState/VirtualBoard), so toggling the
         flag does not regress the production decision path.
    """

    config: SearchConfig = field(default_factory=SearchConfig)
    fallback: Optional[Any] = None
    """Optional heuristic planner used as the safety net. When MCTS
    is opt-in via the ``MTGSIM_USE_MCTS`` flag, the production code
    constructs ``ISMCTSPlanner(fallback=TurnPlanner())`` so that
    every API call the rest of the AI stack expects on the
    heuristic planner (e.g. ``evaluate_response``) keeps working.
    """

    def search(
        self,
        root_state: Any,
        enumerate_actions: ActionEnumerator,
        rollout_policy: RolloutPolicy,
        evaluate_terminal: TerminalEvaluator,
        transition: StateTransition,
    ) -> Any:
        """Run the search and return the best action from the root.

        The four callables are passed explicitly so this loop is
        engine-agnostic. Game-state callers wrap their existing
        primitives (legal_plays, score_play, clock-impact, snapshot
        replay) in matching signatures via the game adapter.
        """
        rng = random.Random(self.config.seed)
        root = UCTNode()
        root.untried_actions = list(enumerate_actions(root_state))

        if not root.untried_actions:
            raise ValueError(
                "search called from a terminal state — "
                "no legal actions"
            )

        for _ in range(self.config.n_rollouts):
            # SELECT phase: walk the tree.
            node = root
            state = root_state
            while node.is_fully_expanded and node.children:
                node = node.best_child(self.config.uct_c)
                state = transition(state, node.action, rng)

            # EXPAND phase: pick an untried action.
            if node.untried_actions:
                action = rng.choice(node.untried_actions)
                state = transition(state, action, rng)
                node = node.expand(action)
                # Populate the child's untried_actions so the next
                # cycle through this node's selection has options.
                node.untried_actions = list(enumerate_actions(state))

            # ROLLOUT phase: simulate to depth.
            reward = self._rollout(
                state,
                enumerate_actions,
                rollout_policy,
                evaluate_terminal,
                transition,
                rng,
            )

            # BACKPROPAGATE.
            node.backpropagate(reward)

        return root.best_action()

    def _rollout(
        self,
        state: Any,
        enumerate_actions: ActionEnumerator,
        rollout_policy: RolloutPolicy,
        evaluate_terminal: TerminalEvaluator,
        transition: StateTransition,
        rng: random.Random,
    ) -> float:
        """Simulate ``rollout_depth`` turns using the rollout policy
        (or stop early at a terminal state). Return the terminal
        evaluation."""
        for _ in range(self.config.rollout_depth):
            actions = enumerate_actions(state)
            if not actions:
                break
            action = rollout_policy(state, rng)
            state = transition(state, action, rng)
        return evaluate_terminal(state)

    # ─────────────────────────────────────────────────────────────
    # Production-path adapter (Phase 4A Week 4)
    # ─────────────────────────────────────────────────────────────

    def plan_turn(self, *args, **kwargs):
        """Adapter mirroring ``TurnPlanner.plan_turn``.

        The full GameState/VirtualBoard → MCTS wiring is the Week-3
        deliverable (action set via ``legal_plays``, determinizations
        via ``bhi``, rollout via ``score_play``). Until that wiring
        lands, this adapter delegates to the heuristic ``fallback``
        so the opt-in flag is safe to enable end-to-end without
        regressing the production decision path.

        If the fallback raises, callers see the same exception they
        would have with the bare ``TurnPlanner`` — the swap is
        transparent. If no fallback is configured, this is a
        programming error and we raise rather than silently
        returning a bogus plan.
        """
        if self.fallback is None:
            raise RuntimeError(
                "ISMCTSPlanner.plan_turn called without a fallback "
                "and without GameState→MCTS wiring (Week-3). "
                "Construct as ISMCTSPlanner(fallback=TurnPlanner())."
            )
        try:
            return self.fallback.plan_turn(*args, **kwargs)
        except Exception:
            logger.exception(
                "ISMCTSPlanner.plan_turn fallback raised — re-raising"
            )
            raise

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attribute lookups to the fallback.

        ``ResponseDecider``, ``TurnPlanner``-using callers, and
        legacy code paths reach for attributes like
        ``evaluate_response``, ``combat_planner``, etc. Dataclass
        fields are resolved via the standard attribute mechanism
        and never reach ``__getattr__``; only genuinely missing
        attributes do.

        ``__getattr__`` is invoked by Python only when the normal
        lookup fails, so recursion on ``self.fallback`` (a real
        attribute) is safe.
        """
        fallback = self.__dict__.get("fallback", None)
        if fallback is None:
            raise AttributeError(
                f"{type(self).__name__!r} has no attribute "
                f"{name!r} and no fallback is configured"
            )
        return getattr(fallback, name)
