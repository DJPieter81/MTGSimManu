"""Regression test for Living End gameplan config (LE-G1 + LE-G3 bundle).

Diagnostic: docs/diagnostics/2026-04-24_living_end_consolidated_findings.md

LE-G1: Violent Outburst must be listed as a critical piece (it's the third
       cascade enabler alongside Shardless Agent and Demonic Dread).
LE-G3: FILL_RESOURCE goal's resource_target must be at least 4 so the
       half-target fallback (ai/gameplan.py:524) requires >=2 GY creatures
       before advancing to EXECUTE_PAYOFF — cascading with a near-empty
       graveyard bricks the combo.
"""
import json
import os

GAMEPLAN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "decks", "gameplans", "living_end.json"
)

# Minimum GY creatures that the half-target fallback must enforce before
# Living End advances to EXECUTE_PAYOFF. With resource_target = N, the
# fallback requires resource_progress >= max(1, N // 2), so to require
# at least 2 creatures we need N >= 4.
MIN_GY_CREATURES_BEFORE_CASCADE = 2
MIN_RESOURCE_TARGET = MIN_GY_CREATURES_BEFORE_CASCADE * 2


def _load_gameplan():
    with open(GAMEPLAN_PATH) as f:
        return json.load(f)


def test_living_end_gameplan_parses():
    """The JSON file must parse cleanly."""
    data = _load_gameplan()
    assert data["deck_name"] == "Living End"


def test_violent_outburst_in_critical_pieces():
    """LE-G1: Violent Outburst is the suspend-cascade enabler and must be
    tracked as a critical piece alongside the other cascade spells."""
    data = _load_gameplan()
    critical = data.get("critical_pieces", [])
    assert "Violent Outburst" in critical, (
        f"Violent Outburst missing from critical_pieces: {critical}"
    )


def test_fill_resource_target_gates_cascade_on_gy_fuel():
    """LE-G3: resource_target must be high enough that the half-target
    fallback (max(1, target // 2)) requires at least 2 GY creatures.
    This prevents Living End from cascading into an empty graveyard."""
    data = _load_gameplan()
    goals = data["goals"]
    fill_goals = [g for g in goals if g.get("goal_type") == "FILL_RESOURCE"]
    assert fill_goals, "Living End must have a FILL_RESOURCE goal"
    fill = fill_goals[0]
    target = fill.get("resource_target")
    assert target is not None, "FILL_RESOURCE goal must declare resource_target"
    assert target >= MIN_RESOURCE_TARGET, (
        f"resource_target={target} too low; half-target fallback would "
        f"fire at {max(1, target // 2)} GY creatures, bricking the cascade. "
        f"Required >= {MIN_RESOURCE_TARGET}."
    )
