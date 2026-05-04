"""
AI Constants — re-export shim (kept for back-compat, see scoring_constants).

Historical home of structural limits + opponent-response coefficients.
After the abstraction-cleanup sweep these constants live in
`ai/scoring_constants.py` alongside every other AI scoring constant
(see CLAUDE.md ABSTRACTION CONTRACT).

Existing callers that import from `ai.constants` keep working via
this shim. New code should import directly from `ai.scoring_constants`.

Migration log:
- 2026-05-03: structural-limits + response-modeling constants moved
  to `ai/scoring_constants.py`. This module re-exports them so
  `ai/ev_evaluator.py` and `engine/game_runner.py` keep working
  without edits in this PR.
"""
from ai.scoring_constants import (  # noqa: F401  (re-export shim)
    MAX_ACTIONS_COMBO,
    MAX_ACTIONS_NORMAL,
    GAME_TIMEOUT_SECONDS,
    SHOCK_LETHAL_LIFE_THRESHOLD,
    NO_CLOCK,
    COUNTER_ESTIMATED_COST,
    REMOVAL_ESTIMATED_COST,
    DAMAGE_REMOVAL_EFF_HIGH_TOUGH,
    DAMAGE_REMOVAL_EFF_MID_TOUGH,
)
