"""Failing-test contract for `decks.gameplan_loader._derive_mulligan_keys`.

Phase 3 of the abstraction-cleanup pass: card-specific lists in
gameplan JSONs (`mulligan_keys`, `always_early`, `reactive_only`)
should derive from the goals' `card_roles` declarations rather than
being hand-maintained in parallel.  This commit adds derivation for
`mulligan_keys` only — the simplest case (no decklist access required,
purely goal-driven).

The contract this test locks in:
  1. A gameplan with `mulligan_keys: []` (or omitted) populates from
     the union of every goal's `enablers` / `payoffs` / `finishers`.
  2. An explicit JSON `mulligan_keys` list always wins (override
     semantics — the deck author has the final say).
  3. `interaction` / other roles do NOT contribute to `mulligan_keys`
     (a control deck's removal suite is not a mulligan key).
"""
from __future__ import annotations

import json

import pytest

from decks.gameplan_loader import _parse_gameplan, _derive_mulligan_keys
from ai.gameplan import Goal, GoalType


def _minimal_data(mulligan_keys=None) -> dict:
    """Build a minimal gameplan dict with one goal that declares
    enablers, payoffs, finishers, and interaction roles.  The
    interaction role exists to assert it is NOT included in the
    derived mulligan_keys set."""
    data = {
        "deck_name": "TestDerive",
        "goals": [
            {
                "goal_type": "CURVE_OUT",
                "card_priorities": {},
                "card_roles": {
                    "enablers": ["EnablerA", "EnablerB"],
                    "payoffs": ["PayoffA"],
                    "finishers": ["FinisherA"],
                    "interaction": ["RemovalA", "CounterA"],
                },
            }
        ],
    }
    if mulligan_keys is not None:
        data["mulligan_keys"] = mulligan_keys
    return data


def test_derive_mulligan_keys_from_goals_when_json_omits_field():
    """JSON omits `mulligan_keys` → derived from goal roles."""
    gp = _parse_gameplan(_minimal_data(mulligan_keys=None))
    assert gp.mulligan_keys == {
        "EnablerA", "EnablerB", "PayoffA", "FinisherA",
    }, (
        f"Expected derived mulligan_keys from enablers + payoffs + finishers, "
        f"got {gp.mulligan_keys!r}"
    )


def test_derive_mulligan_keys_from_goals_when_json_empty_list():
    """JSON sets `mulligan_keys: []` → derive (treat empty as missing)."""
    gp = _parse_gameplan(_minimal_data(mulligan_keys=[]))
    assert gp.mulligan_keys == {
        "EnablerA", "EnablerB", "PayoffA", "FinisherA",
    }


def test_explicit_mulligan_keys_overrides_derived_set():
    """JSON declares an explicit list → derived set is ignored entirely.
    The deck author may keep a card the goals don't classify as a key
    role (e.g. a sideboard pivot) or exclude one that the goals do
    classify (e.g. a redundant payoff)."""
    explicit = ["CustomKeyA", "CustomKeyB"]
    gp = _parse_gameplan(_minimal_data(mulligan_keys=explicit))
    assert gp.mulligan_keys == set(explicit), (
        f"Expected explicit override {set(explicit)}, got {gp.mulligan_keys!r}"
    )
    # Confirm derived cards are absent — override is total, not additive
    assert "EnablerA" not in gp.mulligan_keys


def test_interaction_role_does_not_contribute_to_mulligan_keys():
    """A control deck's removal/counter suite is in card_roles[interaction]
    but is NOT a mulligan key — the deck wins by interacting on the
    opp's clock, not by drawing interaction in the opener."""
    gp = _parse_gameplan(_minimal_data(mulligan_keys=None))
    assert "RemovalA" not in gp.mulligan_keys
    assert "CounterA" not in gp.mulligan_keys


def test_derive_helper_unioned_across_multiple_goals():
    """A multi-goal gameplan unions card_roles across every goal."""
    goals = [
        Goal(
            goal_type=GoalType.CURVE_OUT,
            description="",
            card_roles={"enablers": {"E1"}, "payoffs": {"P1"}},
        ),
        Goal(
            goal_type=GoalType.PUSH_DAMAGE,
            description="",
            card_roles={"finishers": {"F1"}, "payoffs": {"P2"}},
        ),
    ]
    derived = _derive_mulligan_keys(goals)
    assert derived == {"E1", "P1", "F1", "P2"}


def test_existing_gameplan_jsons_still_load_without_change():
    """Sanity: every shipped gameplan JSON still loads, and the
    explicit-override path means their mulligan_keys are unchanged
    (those JSONs all declare the field explicitly)."""
    from decks.gameplan_loader import load_all_gameplans, clear_cache
    clear_cache()
    plans = load_all_gameplans()
    assert len(plans) >= 16, f"Expected ≥16 gameplans, got {len(plans)}"
    # Sample-check: Boros Energy keeps its explicit mulligan_keys
    boros = plans.get("Boros Energy")
    assert boros is not None
    assert "Guide of Souls" in boros.mulligan_keys
