"""Information-Set Monte Carlo Tree Search (ISMCTS) — Phase 4A.

Imperfect-information game search for MTG decision points where the
single-ply heuristic scorer in ``ai/turn_planner.py`` saturates.

Public API:

    from ai.search.ismcts import ISMCTSPlanner, SearchConfig

The planner is a drop-in replacement for ``TurnPlanner.plan_turn``
behind an opt-in CLI flag (``--mcts``); the matrix-sim hot loop is
unchanged.

See:
- ``docs/research/2026-05_phase_4a_ismcts_scoping.md`` — design and
  acceptance gate.
- ``docs/research/2026-05_mtg_ai_landscape.md`` §1 — algorithm survey.
"""

from ai.search.uct_node import UCTNode
from ai.search.ismcts import ISMCTSPlanner, SearchConfig

__all__ = ["UCTNode", "ISMCTSPlanner", "SearchConfig"]
