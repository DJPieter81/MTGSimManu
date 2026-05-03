"""Link/smoke test: ai/ev_player.py uses the same constants advertised
in ai/scoring_constants.py.

When `ai/ev_player.py` was refactored (track A continuation), several
inline literals (40.0 reanimate force-cast, 1.5 free-cast tempo,
15.0 evoke card-loss multiplier, 10.0 evoke desperate, 20.0 evoke no-
target, 3.0 PW survival floor, 6.0 / 8.0 / 2.0 / 10.0 game-horizon
clamps, 2.0 blink M1 hold, -3.0 dead-counter, 0.5 / 1.0 removal-
premium scales, 12.0 landfall deferral, -20.0 X-wipe waste, -50.0
blink fizzle, 999 chump sentinel, 0.1 no-clock face, 5 storm
threshold, 3.0 landfall trigger, 4.0 artifact synergy / Tron / Amulet,
8.0 / 6.0 / 10.0 cycling, 2.0 / 1.0 cycling cost bonus, 4.0 / 0.5
cycling GY reanimate) were duplicated between this module and the
constant table.  Centralising them in `ai/scoring_constants.py`
removed the duplication; this test asserts:

1. Each rule-encoding constant exists in `ai.scoring_constants`.
2. Its value matches the expected rule (the value it had as a literal
   in `ai/ev_player.py`'s pre-refactor code path).

If a future re-tune changes a value in scoring_constants.py without a
matching update to the rule it encodes, this test names the rule that
broke.  If the literal is reintroduced in ev_player.py, the value
mismatch in this test (or the abstraction-baseline ratchet) will
surface it.
"""
from __future__ import annotations

import pytest

from ai import ev_player, scoring_constants


# ─── Constants advertised in scoring_constants.py ────────────────────


@pytest.mark.parametrize("name, expected", [
    ("REANIMATE_OVERRIDE_BONUS", 40.0),
    ("FREE_CAST_TEMPO_BONUS", 1.5),
    ("EVOKE_CARD_LOSS_MULTIPLIER", 15.0),
    ("EVOKE_DESPERATE_BONUS", 10.0),
    ("EVOKE_NO_TARGET_PENALTY", 20.0),
    ("PLANESWALKER_SURVIVAL_FLOOR", 3.0),
    ("MIDGAME_HORIZON_TURNS", 6.0),
    ("GAME_HORIZON_MIN_TURNS", 2.0),
    ("GAME_HORIZON_MAX_COST_REDUCER", 8.0),
    ("MODERN_AVG_GAME_LENGTH", 8.0),
    ("GAME_HORIZON_MAX_TRON", 10.0),
    ("BLINK_M1_HOLD_PENALTY", 2.0),
    ("NONCREATURE_COUNTER_DEAD_FLOOR", -3.0),
    ("REMOVAL_THREAT_PREMIUM_SCALE", 0.5),
    ("CHEAP_REMOVAL_ACTION_BONUS", 1.0),
    ("LANDFALL_DEFERRAL_PENALTY", 12.0),
    ("X_BOARD_WIPE_WASTE_FLOOR", -20.0),
    ("BLINK_FIZZLE_FLOOR", -50.0),
    ("CHUMP_SENTINEL_VALUE", 999.0),
    ("NO_CLOCK_FACE_VAL_MULTIPLIER", 0.1),
    ("COMBO_FORCE_PAYOFF_STORM_THRESHOLD", 5),
    ("LANDFALL_TRIGGER_VALUE", 3.0),
    ("ARTIFACT_LAND_SYNERGY_BONUS", 4.0),
    ("TRON_MANA_ADVANTAGE", 4.0),
    ("AMULET_TITAN_MANA_BONUS", 4.0),
    ("CYCLING_CASCADE_BOOST", 8.0),
    ("CYCLING_GY_URGENCY", 6.0),
    ("CYCLING_GAMEPLAN_BOOST", 10.0),
    ("CYCLING_FREE_COST_BONUS", 2.0),
    ("CYCLING_CHEAP_COST_BONUS", 1.0),
    ("CYCLING_GY_REANIMATE_BASE", 4.0),
    ("CYCLING_GY_REANIMATE_PER_POWER", 0.5),
])
def test_scoring_constant_value(name: str, expected):
    """Each centralised constant has the value it encoded as a literal."""
    actual = getattr(scoring_constants, name)
    assert actual == expected, (
        f"scoring_constants.{name} = {actual}, expected {expected}. "
        f"If this rule was deliberately re-tuned, update both this test "
        f"and the docstring on the constant."
    )


# ─── Linkage: ai/ev_player.py imports the constants ──────────────────


REQUIRED_IMPORTS = (
    "REANIMATE_OVERRIDE_BONUS",
    "FREE_CAST_TEMPO_BONUS",
    "EVOKE_CARD_LOSS_MULTIPLIER",
    "EVOKE_DESPERATE_BONUS",
    "EVOKE_NO_TARGET_PENALTY",
    "PLANESWALKER_SURVIVAL_FLOOR",
    "MIDGAME_HORIZON_TURNS",
    "GAME_HORIZON_MIN_TURNS",
    "GAME_HORIZON_MAX_COST_REDUCER",
    "GAME_HORIZON_MAX_TRON",
    "MODERN_AVG_GAME_LENGTH",
    "BLINK_M1_HOLD_PENALTY",
    "NONCREATURE_COUNTER_DEAD_FLOOR",
    "REMOVAL_THREAT_PREMIUM_SCALE",
    "CHEAP_REMOVAL_ACTION_BONUS",
    "LANDFALL_DEFERRAL_PENALTY",
    "X_BOARD_WIPE_WASTE_FLOOR",
    "BLINK_FIZZLE_FLOOR",
    "CHUMP_SENTINEL_VALUE",
    "NO_CLOCK_FACE_VAL_MULTIPLIER",
    "COMBO_FORCE_PAYOFF_STORM_THRESHOLD",
    "LANDFALL_TRIGGER_VALUE",
    "ARTIFACT_LAND_SYNERGY_BONUS",
    "TRON_MANA_ADVANTAGE",
    "AMULET_TITAN_MANA_BONUS",
    "CYCLING_CASCADE_BOOST",
    "CYCLING_GY_URGENCY",
    "CYCLING_GAMEPLAN_BOOST",
    "CYCLING_FREE_COST_BONUS",
    "CYCLING_CHEAP_COST_BONUS",
    "CYCLING_GY_REANIMATE_BASE",
    "CYCLING_GY_REANIMATE_PER_POWER",
    "CLOCK_IMPACT_LIFE_SCALING",
)


@pytest.mark.parametrize("name", REQUIRED_IMPORTS)
def test_ev_player_module_imports_constant(name: str):
    """Each centralised constant must be importable from ai.ev_player —
    we re-export via `from ai.scoring_constants import …` so a future
    inline-literal regression shows up as a missing reference here.
    """
    assert hasattr(ev_player, name), (
        f"ai.ev_player is expected to import `{name}` from "
        f"ai.scoring_constants. If it was removed, the corresponding "
        f"literal probably crept back inline."
    )
    assert getattr(ev_player, name) == getattr(scoring_constants, name)
