"""UCT node — UCB1 statistics for ISMCTS.

The node holds the visit count and accumulated value for an
action edge. ``best_child`` returns the child maximizing the
UCB1 score:

    UCB1(child) = (child.total_value / child.visits)
                + c * sqrt(2 * ln(parent.visits) / child.visits)

The exploration constant ``c`` defaults to sqrt(2) (~1.41) per
the Kocsis & Szepesvári 2006 derivation, and is tunable via
``SearchConfig.uct_c``.

Information-set bookkeeping is intentionally NOT here — that
belongs in ``ismcts.py``'s top-level loop, which groups nodes
by visible-state hash before sharing statistics across
determinizations.

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Default exploration constant. UCB1's theoretical optimum
# c = sqrt(2) maximizes regret-bound tightness for [0,1]-bounded
# rewards. We rescale rewards to [0,1] (life-differential
# normalised by 20) so the default is appropriate.
DEFAULT_UCT_C: float = math.sqrt(2.0)


@dataclass
class UCTNode:
    """A node in the UCT search tree.

    Attributes:
      action: the action that led to this node (None for the root).
      parent: the node from which this node was expanded.
      children: dict mapping action → UCTNode.
      visits: number of times this node has been visited
        (incremented during backpropagation).
      total_value: cumulative reward from rollouts through this
        node. Mean value is total_value / max(1, visits).
      untried_actions: legal actions not yet expanded into a
        child. Populated at expansion time by the search loop.
    """

    action: Optional[Any] = None
    parent: Optional["UCTNode"] = None
    children: Dict[Any, "UCTNode"] = field(default_factory=dict)
    visits: int = 0
    total_value: float = 0.0
    untried_actions: List[Any] = field(default_factory=list)

    @property
    def is_fully_expanded(self) -> bool:
        """True if every legal action has been expanded into a child.

        ``untried_actions`` is set externally at the time the node is
        created (or first visited); ``is_fully_expanded`` is true when
        that list has been drained.
        """
        return len(self.untried_actions) == 0

    @property
    def is_terminal(self) -> bool:
        """True if no actions are available — the search must score
        this leaf with a terminal evaluator."""
        return len(self.untried_actions) == 0 and len(self.children) == 0

    @property
    def mean_value(self) -> float:
        """Average value across visits. Returns 0.0 for unvisited
        nodes (UCB1's standard treatment of unseen arms is
        ``+infinity``; the search handles that via
        ``untried_actions`` ordering, so the mean here is purely
        cosmetic)."""
        return self.total_value / self.visits if self.visits > 0 else 0.0

    def ucb1(self, c: float = DEFAULT_UCT_C) -> float:
        """UCB1 score for this node, using the parent's visit count.

        The root has no parent; its UCB1 is undefined (the root
        is selected unconditionally). Returns +inf for unvisited
        children to force exploration.
        """
        if self.visits == 0:
            return math.inf
        if self.parent is None or self.parent.visits == 0:
            return self.mean_value
        exploration = c * math.sqrt(
            2.0 * math.log(self.parent.visits) / self.visits
        )
        return self.mean_value + exploration

    def best_child(self, c: float = DEFAULT_UCT_C) -> "UCTNode":
        """Return the child with the highest UCB1 score.

        Raises ``ValueError`` if the node has no children — callers
        must ensure ``is_fully_expanded`` (or check in the search
        loop's selection step).
        """
        if not self.children:
            raise ValueError("best_child called on leaf with no children")
        return max(self.children.values(), key=lambda n: n.ucb1(c))

    def expand(self, action: Any) -> "UCTNode":
        """Move ``action`` from ``untried_actions`` into a new child
        node. Returns the newly created child.

        The caller is responsible for populating the child's
        ``untried_actions`` (the search loop knows the legal-actions
        set for the resulting state, this class doesn't).
        """
        if action not in self.untried_actions:
            raise ValueError(
                f"action {action!r} not in untried_actions "
                f"{self.untried_actions!r}"
            )
        self.untried_actions.remove(action)
        child = UCTNode(action=action, parent=self)
        self.children[action] = child
        return child

    def backpropagate(self, reward: float) -> None:
        """Walk from this node to the root, incrementing visits and
        adding ``reward`` to total_value. The reward is in
        [0, 1] — wins normalised."""
        node: Optional["UCTNode"] = self
        while node is not None:
            node.visits += 1
            node.total_value += reward
            node = node.parent

    def best_action(self) -> Any:
        """Return the action of the most-visited child (final answer
        of the search, distinct from selection during search).

        Convention: return the highest-visit-count child rather than
        the highest-mean-value child. Most-visits is more robust
        when the search budget is small (high-mean-but-few-visits
        children get demoted)."""
        if not self.children:
            raise ValueError("best_action called on leaf with no children")
        best = max(self.children.values(), key=lambda n: n.visits)
        return best.action
