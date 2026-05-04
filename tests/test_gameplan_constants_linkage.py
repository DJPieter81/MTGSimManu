"""Link/smoke test: ai/gameplan.py uses the same constants advertised
in ai/scoring_constants.py.

The gameplan module's three scoring sites (goal-transition pacing,
generic combo-readiness confidences, and the mulligan ``card_keep_score``
weights) used to carry inline numeric literals. The cleanup pass
centralised them in ``ai/scoring_constants.py``; this test asserts:

1. Each rule-encoding constant exists in ``ai.scoring_constants``.
2. Its value matches the rule it encodes (the value it had as a literal
   in ``ai/gameplan.py``'s pre-refactor code path).
3. ``ai.gameplan`` re-exports each constant via the import chain at
   the top of the module — so a future inline-literal regression
   surfaces as a missing reference here.

If a future re-tune changes a value in ``scoring_constants.py``
without a matching update to the rule it encodes, this test names
the rule that broke.
"""
from __future__ import annotations

import pytest

from ai import gameplan, scoring_constants


# ─── Constants advertised in scoring_constants.py ────────────────────


@pytest.mark.parametrize("name, expected", [
    # Goal-transition pacing
    ("NO_CLOCK_SENTINEL", 999),
    ("DEPLOY_ENGINE_FORCE_ADVANCE_TURNS", 3),
    ("GENERIC_GOAL_TIMEOUT_TURNS", 2),
    # Resource-target fallbacks
    ("DEFAULT_FILL_RESOURCE_TARGET", 3),
    ("DEFAULT_STORM_RESOURCE_TARGET", 5),
    ("DEFAULT_MANA_RESOURCE_TARGET", 5),
    ("DEFAULT_RAMP_GOAL_MANA_TARGET", 6),
    # Generic combo-readiness confidences
    ("COMBO_FIRED_CONFIDENCE", 0.9),
    ("COMBO_MANA_FIRE_CONFIDENCE", 0.85),
    ("COMBO_GY_FIRE_CONFIDENCE", 0.8),
    ("COMBO_PROJECTED_FIRE_CONFIDENCE", 0.7),
    ("COMBO_BASE_CONFIDENCE", 0.6),
    ("COMBO_PIECE_CONFIDENCE_BONUS", 0.1),
    ("COMBO_NO_PAYOFF_CONFIDENCE", 0.1),
    ("COMBO_NO_PIECES_CONFIDENCE", 0.3),
    # Mulligan keep-score weights
    ("MULL_KEEP_LAND_TARGET", 3),
    ("MULL_KEEP_LAND_NEEDED", 10.0),
    ("MULL_KEEP_LAND_EXTRA", 2.0),
    ("MULL_KEEP_LAND_PRIORITY_SCALE", 0.5),
    ("MULL_KEEP_LAND_COLOR_PRODUCTION_SCALE", 0.5),
    ("MULL_KEEP_CMC_BUDGET", 5),
    ("MULL_KEEP_KEY_BONUS", 8.0),
    ("MULL_KEEP_REACTIVE_PENALTY", 8.0),
    ("MULL_KEEP_ROLE_DEFAULT", 4.0),
    ("MULL_KEEP_ALWAYS_EARLY_BONUS", 6.0),
    ("MULL_KEEP_REMOVAL_TEXT_BONUS", 4.0),
    ("MULL_KEEP_CRITICAL_SINGLETON_FLOOR", 20.0),
])
def test_scoring_constant_value(name: str, expected):
    """Each centralised gameplan constant has the value it encoded
    as a literal in the pre-refactor `ai/gameplan.py`."""
    actual = getattr(scoring_constants, name)
    assert actual == expected, (
        f"scoring_constants.{name} = {actual}, expected {expected}. "
        f"If this rule was deliberately re-tuned, update both this test "
        f"and the docstring on the constant."
    )


def test_role_weights_match_pre_refactor_table():
    """`MULL_KEEP_ROLE_WEIGHTS` table preserves the inline-dict mapping
    that previously lived in `card_keep_score`. Each role's weight
    encodes a tier in the mulligan-keep ranking; re-tuning any one of
    them should land in this test as a surfaced expectation."""
    expected = {
        "engines": 8.0,
        "payoffs": 7.0,
        "enablers": 6.0,
        "interaction": 5.0,
        "protection": 4.0,
        "fillers": 3.0,
    }
    assert scoring_constants.MULL_KEEP_ROLE_WEIGHTS == expected


def test_storm_target_matches_combo_force_threshold():
    """Sister-constant invariant: the storm-zone resource fallback
    should equal the same "we have enough storm fuel that even non-
    lethal payoffs close the game" threshold consumed by
    `ai/ev_player.py::decide_main_phase`. They sit on the same
    decision axis from two angles (readiness vs override) and must
    not drift."""
    assert (scoring_constants.DEFAULT_STORM_RESOURCE_TARGET
            == scoring_constants.COMBO_FORCE_PAYOFF_STORM_THRESHOLD)


def test_critical_singleton_floor_above_normal_keep_cap():
    """`MULL_KEEP_CRITICAL_SINGLETON_FLOOR` must dominate the
    realistic keep-score band so a singleton critical piece never
    bottoms — the floor docstring derives the 27-point cap from the
    sum of role + key + cmc + always-early bonuses, and this test
    pins the floor above the union of the two largest role/role-default
    weights to keep the invariant alive after a re-tune."""
    cap = (
        scoring_constants.MULL_KEEP_ROLE_WEIGHTS["engines"]
        + scoring_constants.MULL_KEEP_KEY_BONUS
        + scoring_constants.MULL_KEEP_CMC_BUDGET
        + scoring_constants.MULL_KEEP_ALWAYS_EARLY_BONUS
    )
    # Floor sits above the everyday keep weights but below the
    # synthetic max-stack cap, so a singleton critical reliably wins
    # the keep without saturating to "always max".
    assert scoring_constants.MULL_KEEP_CRITICAL_SINGLETON_FLOOR < cap
    assert (scoring_constants.MULL_KEEP_CRITICAL_SINGLETON_FLOOR
            > scoring_constants.MULL_KEEP_ROLE_WEIGHTS["engines"]
            + scoring_constants.MULL_KEEP_KEY_BONUS)


# ─── Linkage: ai/gameplan.py imports the constants ───────────────────


REQUIRED_IMPORTS = (
    "NO_CLOCK_SENTINEL",
    "DEPLOY_ENGINE_FORCE_ADVANCE_TURNS",
    "GENERIC_GOAL_TIMEOUT_TURNS",
    "DEFAULT_FILL_RESOURCE_TARGET",
    "DEFAULT_STORM_RESOURCE_TARGET",
    "DEFAULT_MANA_RESOURCE_TARGET",
    "DEFAULT_RAMP_GOAL_MANA_TARGET",
    "COMBO_FIRED_CONFIDENCE",
    "COMBO_MANA_FIRE_CONFIDENCE",
    "COMBO_GY_FIRE_CONFIDENCE",
    "COMBO_PROJECTED_FIRE_CONFIDENCE",
    "COMBO_BASE_CONFIDENCE",
    "COMBO_PIECE_CONFIDENCE_BONUS",
    "COMBO_NO_PAYOFF_CONFIDENCE",
    "COMBO_NO_PIECES_CONFIDENCE",
    "MULL_KEEP_LAND_TARGET",
    "MULL_KEEP_LAND_NEEDED",
    "MULL_KEEP_LAND_EXTRA",
    "MULL_KEEP_LAND_PRIORITY_SCALE",
    "MULL_KEEP_LAND_COLOR_PRODUCTION_SCALE",
    "MULL_KEEP_CMC_BUDGET",
    "MULL_KEEP_KEY_BONUS",
    "MULL_KEEP_REACTIVE_PENALTY",
    "MULL_KEEP_ROLE_WEIGHTS",
    "MULL_KEEP_ROLE_DEFAULT",
    "MULL_KEEP_ALWAYS_EARLY_BONUS",
    "MULL_KEEP_REMOVAL_TEXT_BONUS",
    "MULL_KEEP_CRITICAL_SINGLETON_FLOOR",
)


@pytest.mark.parametrize("name", REQUIRED_IMPORTS)
def test_gameplan_module_imports_constant(name: str):
    """Each centralised constant must be importable from ai.gameplan —
    we re-export via `from ai.scoring_constants import …` so a future
    inline-literal regression surfaces as a missing reference here.
    """
    assert hasattr(gameplan, name), (
        f"ai.gameplan is expected to import `{name}` from "
        f"ai.scoring_constants. If it was removed, the corresponding "
        f"literal probably crept back inline."
    )
    assert getattr(gameplan, name) == getattr(scoring_constants, name)
