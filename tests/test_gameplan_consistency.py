"""Gameplan consistency — pytest integration.

Wraps tools/check_gameplan_consistency.py so `pytest tests/ -q` catches
gameplan/decklist drift before CI does: every card a
`decks/gameplans/*.json` names must be in that deck's mainboard ∪
sideboard in `decks/modern_meta.MODERN_DECKS`.

Why: gameplans are hand-authored, decklists are refreshed from tournament
data, and nothing tied them together. A refreshed list left the Affinity
gameplan naming nine cards the deck no longer played — the mulligan
bottoming protection (`ai/discard_advisor._declared_keystones`) then
guarded phantoms and bottomed the real payoffs. No exception, no log line.

The logic lives in the tool; this file is the adapter plus a few
rule-phrased unit tests on the walker so the check itself is pinned.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_gameplan_consistency.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "check_gameplan_consistency", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return _load_tool()


# A minimal registry shaped like MODERN_DECKS: {deck: {zone: {card: n}}}.
_DECKS = {
    "Test Deck": {
        "mainboard": {"Real Payoff": 4, "Real Enabler": 4, "Real Land": 20},
        "sideboard": {"Real Hate": 2},
    },
}


def test_check_script_exists():
    assert SCRIPT.exists(), f"Missing {SCRIPT.relative_to(ROOT)}"


def test_gameplan_card_in_mainboard_or_sideboard_is_clean(tool):
    """A gameplan that names only cards in main ∪ side has no violations —
    sideboard references are legitimate (post-board goals, Wish targets)."""
    plan = {
        "deck_name": "Test Deck",
        "goals": [{
            "goal_type": "CURVE_OUT",
            "card_priorities": {"Real Payoff": 20.0, "Real Hate": 5.0},
            "card_roles": {"payoffs": ["Real Payoff"],
                           "enablers": ["Real Enabler"]},
        }],
        "mulligan_keys": ["Real Payoff"],
        "land_priorities": {"Real Land": 2.0},
    }
    assert tool.check_gameplan("Test Deck", plan, _DECKS) == []


def test_every_card_bearing_field_is_walked(tool):
    """A phantom card is reported no matter which card-bearing field
    carries it — the walk must cover every field the loader reads."""
    phantom_by_field = {
        "mulligan_keys": ["Phantom"],
        "always_early": ["Phantom"],
        "reactive_only": ["Phantom"],
        "critical_pieces": ["Phantom"],
        "land_priorities": {"Phantom": 1.0},
        "mulligan_combo_sets": [["Real Payoff", "Phantom"]],
        "mulligan_combo_paths": [{"enablers": ["Phantom"],
                                  "payoffs": ["Real Payoff"]}],
        "goals": [{"goal_type": "X", "card_priorities": {"Phantom": 1.0}}],
        "fallback_goals": [{"goal_type": "X",
                            "card_roles": {"payoffs": ["Phantom"]}}],
    }
    for field, value in phantom_by_field.items():
        plan = {"deck_name": "Test Deck", field: value}
        violations = tool.check_gameplan("Test Deck", plan, _DECKS)
        assert any("'Phantom' not in list" in v for v in violations), (
            f"phantom in {field!r} was not detected: {violations}"
        )


def test_unclassified_field_fails_instead_of_being_skipped(tool):
    """A field the walker does not recognise is a violation — otherwise a
    new card-bearing schema field would silently escape the check."""
    plan = {"deck_name": "Test Deck", "brand_new_card_list": ["Phantom"]}
    violations = tool.check_gameplan("Test Deck", plan, _DECKS)
    assert any("unclassified" in v for v in violations), violations

    goal_plan = {"deck_name": "Test Deck",
                 "goals": [{"goal_type": "X", "new_goal_cards": ["Phantom"]}]}
    violations = tool.check_gameplan("Test Deck", goal_plan, _DECKS)
    assert any("unclassified" in v for v in violations), violations


def test_orphaned_gameplan_is_a_violation(tool):
    """`load_gameplan` matches on the JSON `deck_name`, so a gameplan whose
    deck_name is in no registry entry is never loaded — report it."""
    plan = {"deck_name": "Renamed Deck", "mulligan_keys": ["Real Payoff"]}
    violations = tool.check_gameplan("Renamed Deck", plan, _DECKS)
    assert any("orphaned" in v for v in violations), violations


def test_matching_is_exact_string(tool):
    """The loader and the AI compare names exactly; a near-miss (case,
    split-card face) is drift the check must surface, not forgive."""
    decks = {"D": {"mainboard": {"Fire // Ice": 4}, "sideboard": {}}}
    plan = {"deck_name": "D", "mulligan_keys": ["Fire", "fire // ice"]}
    violations = tool.check_gameplan("D", plan, decks)
    assert len(violations) == 2, violations


def test_all_gameplans_consistent_with_decklists():
    """The real check: every decks/gameplans/*.json names only cards its
    MODERN_DECKS entry plays. Runs the script the way CI does."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        raise AssertionError(
            f"Gameplan/decklist drift:\n{msg}\n\n"
            f"Correct the gameplan JSON to the deck's real cards — see the "
            f"docstring of tools/check_gameplan_consistency.py."
        )
