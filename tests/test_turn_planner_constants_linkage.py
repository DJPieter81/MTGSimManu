"""Link/smoke test: ai/turn_planner.py uses the same constants
advertised in ai/scoring_constants.py.

Several module-level constants (LETHAL_BONUS, TWO_TURN_LETHAL_BONUS,
TRADE_UP_BONUS, TRADE_DOWN_PENALTY, SHIELDS_DOWN_PENALTY,
MAX_ATTACK_CONFIGS, COUNTER_THRESHOLD, COUNTER_CHEAP_THRESHOLD,
REMOVAL_RESPONSE_THRESHOLD, BLINK_SAVE_THRESHOLD,
PRE_COMBAT_REMOVAL_BONUS, MANA_RESERVATION_WEIGHT, INFORMATION_BONUS)
were defined in `ai/turn_planner.py` itself with inline derivation
comments.  Plus several inline literals (5.0 no-interaction penalty,
10.0 lethal-push infeasible penalty, 2.5 card-in-hand value, 0.3
mana-available value, 5.0 life-score scale, 3 avg-incoming, 0.3
chip-damage value, 0.8/0.4/0.15 aggression-tiered bonuses, 4.0
double-block value threshold) were extracted in this pass.

This test asserts:

1. Each rule-encoding constant exists in `ai.scoring_constants`.
2. Its value matches the rule (the value it had as a literal /
   module-level constant in `ai/turn_planner.py`'s pre-refactor
   code path).
3. `ai.turn_planner` re-exports each constant so future inline
   literals trigger an import error, not silent shadow.
"""
from __future__ import annotations

import pytest

from ai import scoring_constants, turn_planner


@pytest.mark.parametrize("name, expected", [
    ("LETHAL_BONUS", 100.0),
    ("TWO_TURN_LETHAL_BONUS", 15.0),
    ("TRADE_UP_BONUS", 2.0),
    ("TRADE_DOWN_PENALTY", -2.5),
    ("SHIELDS_DOWN_PENALTY", -1.5),
    ("MAX_ATTACK_CONFIGS", 32),
    ("COUNTER_THRESHOLD", 5.0),
    ("COUNTER_CHEAP_THRESHOLD", 2.0),
    ("REMOVAL_RESPONSE_THRESHOLD", 4.0),
    ("BLINK_SAVE_THRESHOLD", 3.5),
    ("PRE_COMBAT_REMOVAL_BONUS", 2.5),
    ("MANA_RESERVATION_WEIGHT", 5.0),
    ("INFORMATION_BONUS", 0.3),
    ("NO_INTERACTION_PENALTY", 5.0),
    ("LETHAL_PUSH_INFEASIBLE_PENALTY", 10.0),
    ("CARD_IN_HAND_VALUE", 2.5),
    ("MANA_AVAILABLE_VALUE", 0.3),
    ("LIFE_SCORE_SCALE", 5.0),
    ("LIFE_SCORE_AVG_INCOMING", 3),
    ("CHIP_DAMAGE_VALUE", 0.3),
    ("AGGRESSION_BONUS_LIFE8", 0.8),
    ("AGGRESSION_BONUS_LIFE12", 0.4),
    ("AGGRESSION_BONUS_LIFE16", 0.15),
    ("DOUBLE_BLOCK_VALUE_THRESHOLD", 4.0),
])
def test_scoring_constant_value(name: str, expected):
    """Each centralised constant has the value it encoded as a literal."""
    actual = getattr(scoring_constants, name)
    assert actual == expected, (
        f"scoring_constants.{name} = {actual}, expected {expected}. "
        f"If this rule was deliberately re-tuned, update both this test "
        f"and the docstring on the constant."
    )


REQUIRED_IMPORTS = (
    "LETHAL_BONUS",
    "TWO_TURN_LETHAL_BONUS",
    "TRADE_UP_BONUS",
    "TRADE_DOWN_PENALTY",
    "SHIELDS_DOWN_PENALTY",
    "MAX_ATTACK_CONFIGS",
    "COUNTER_THRESHOLD",
    "COUNTER_CHEAP_THRESHOLD",
    "REMOVAL_RESPONSE_THRESHOLD",
    "BLINK_SAVE_THRESHOLD",
    "PRE_COMBAT_REMOVAL_BONUS",
    "MANA_RESERVATION_WEIGHT",
    "INFORMATION_BONUS",
    "NO_INTERACTION_PENALTY",
    "LETHAL_PUSH_INFEASIBLE_PENALTY",
    "CARD_IN_HAND_VALUE",
    "MANA_AVAILABLE_VALUE",
    "LIFE_SCORE_SCALE",
    "LIFE_SCORE_AVG_INCOMING",
    "CHIP_DAMAGE_VALUE",
    "AGGRESSION_BONUS_LIFE8",
    "AGGRESSION_BONUS_LIFE12",
    "AGGRESSION_BONUS_LIFE16",
    "DOUBLE_BLOCK_VALUE_THRESHOLD",
)


@pytest.mark.parametrize("name", REQUIRED_IMPORTS)
def test_turn_planner_module_imports_constant(name: str):
    """Each centralised constant must be importable from ai.turn_planner —
    we re-export via `from ai.scoring_constants import …` so a future
    inline-literal regression shows up as a missing reference here.
    """
    assert hasattr(turn_planner, name), (
        f"ai.turn_planner is expected to import `{name}` from "
        f"ai.scoring_constants. If it was removed, the corresponding "
        f"literal probably crept back inline."
    )
    assert getattr(turn_planner, name) == getattr(scoring_constants, name)
