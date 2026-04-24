"""Regression test for Goryo's Vengeance EXECUTE_PAYOFF goal gates (GV-4).

Diagnostic: Goryo's at 24.9% flat WR. FILL_RESOURCE advances to
EXECUTE_PAYOFF as soon as a single 5+ CMC creature hits the graveyard
(resource_target=1), but there's no `min_turns` or `min_mana_for_payoff`
gate — so the AI fires Goryo's Vengeance on T3 when mana-light and
board-exposed.

GV-4: EXECUTE_PAYOFF must declare min_turns >= 2 and min_mana_for_payoff
      >= 2 so the AI waits for at least 2 turns in the payoff goal and
      has enough open mana (Goryo's costs {B}{B}) before firing.
"""
import json
import os

GAMEPLAN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "decks", "gameplans", "goryos_vengeance.json"
)


def _load_gameplan():
    with open(GAMEPLAN_PATH) as f:
        return json.load(f)


def _execute_payoff_goal(data):
    goals = data["goals"]
    payoff = [g for g in goals if g.get("goal_type") == "EXECUTE_PAYOFF"]
    assert payoff, "Goryo's Vengeance must have an EXECUTE_PAYOFF goal"
    return payoff[0]


def test_goryos_gameplan_parses():
    """The JSON file must parse cleanly."""
    data = _load_gameplan()
    assert data["deck_name"] == "Goryo's Vengeance"


def test_execute_payoff_min_turns_gate():
    """GV-4: EXECUTE_PAYOFF must require >= 2 turns in goal so the AI
    does not fire Goryo's the turn it enters the payoff phase."""
    data = _load_gameplan()
    goal = _execute_payoff_goal(data)
    min_turns = goal.get("min_turns")
    assert min_turns is not None, (
        "EXECUTE_PAYOFF goal must declare min_turns"
    )
    assert min_turns >= 2, (
        f"min_turns={min_turns} too low; AI fires payoff prematurely. "
        f"Required >= 2."
    )


def test_execute_payoff_min_mana_gate():
    """GV-4: EXECUTE_PAYOFF must require >= 2 open mana (Goryo's costs
    {B}{B}); without the gate the AI casts it on a 1-mana turn."""
    data = _load_gameplan()
    goal = _execute_payoff_goal(data)
    min_mana = goal.get("min_mana_for_payoff")
    assert min_mana is not None, (
        "EXECUTE_PAYOFF goal must declare min_mana_for_payoff"
    )
    assert min_mana >= 2, (
        f"min_mana_for_payoff={min_mana} too low; Goryo's costs 2 "
        f"and the AI will try to fire without enough mana. Required >= 2."
    )


def test_goal_dataclass_loads_min_mana_for_payoff():
    """The Goal dataclass + loader must round-trip the new field so the
    gameplan engine can actually read it at runtime."""
    from decks.gameplan_loader import load_gameplan, clear_cache
    clear_cache()
    plan = load_gameplan("Goryo's Vengeance")
    assert plan is not None, "Goryo's Vengeance gameplan must load"
    payoff_goals = [g for g in plan.goals
                    if g.goal_type.value == "execute_payoff"]
    assert payoff_goals, "EXECUTE_PAYOFF goal must be loaded"
    goal = payoff_goals[0]
    assert getattr(goal, "min_mana_for_payoff", None) == 2, (
        f"Loaded goal.min_mana_for_payoff={getattr(goal, 'min_mana_for_payoff', None)}; "
        f"loader must parse this field from JSON."
    )
    assert goal.min_turns == 2, (
        f"Loaded goal.min_turns={goal.min_turns}; loader must parse "
        f"min_turns for EXECUTE_PAYOFF."
    )


def test_is_ready_for_payoff_predicate_respects_mana():
    """The gameplan engine must expose a predicate that answers
    'is the payoff ready to fire given the current mana available'.
    With 1 mana open the predicate returns False; with 2 it returns True.
    """
    from ai.gameplan import Goal, GoalType, is_ready_for_payoff

    goal = Goal(
        goal_type=GoalType.EXECUTE_PAYOFF,
        description="test",
        min_turns=2,
        min_mana_for_payoff=2,
    )

    # 1 mana open, been in goal 5 turns — mana gate fails
    assert not is_ready_for_payoff(goal, turns_in_goal=5, mana_available=1)
    # 2 mana open, 5 turns — both gates pass
    assert is_ready_for_payoff(goal, turns_in_goal=5, mana_available=2)
    # 2 mana open, 1 turn in goal — turn gate fails
    assert not is_ready_for_payoff(goal, turns_in_goal=1, mana_available=2)
    # 2 mana open, 2 turns in goal — both gates pass (boundary)
    assert is_ready_for_payoff(goal, turns_in_goal=2, mana_available=2)
