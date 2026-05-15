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
from ai.search.cardinstance_proxy import (
    ProxyInsufficientMetadataError,
    make_full_production_picker,
    proxy_card_instance,
    proxy_game_state,
    score_action_via_production_scorer,
)

__all__ = [
    "UCTNode",
    "ISMCTSPlanner",
    "SearchConfig",
    "ProxyInsufficientMetadataError",
    "make_full_production_picker",
    "proxy_card_instance",
    "proxy_game_state",
    "score_action_via_production_scorer",
]
