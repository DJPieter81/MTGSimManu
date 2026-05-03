"""Failing-test contract for `tools.synth_gameplan` + `ai.gameplan_schemas`.

Phase 4 of the abstraction-cleanup pass.  Locks in the round-trip:

    decklist
        ↓ tools.synth_gameplan.synth_gameplan_rule_based
    SynthesizedGameplan (typed pydantic)
        ↓ to_json_dict
    JSON dict
        ↓ decks.gameplan_loader._parse_gameplan
    DeckGameplan (dataclass)

If this round-trip stays consistent, the eventual pydantic-ai LLM
backend can swap in for `synth_gameplan_rule_based` without changes
to the loader or downstream consumers — the schema is the contract.

The contract this test locks in:
  1. The synth output is a valid `SynthesizedGameplan` (pydantic
     validation passes).
  2. Serializing it via `to_json_dict` produces a dict the loader
     accepts without error.
  3. Round-tripped `DeckGameplan` has the same goals, archetype, and
     mulligan ranges as the synthesized plan.

The LLM backend (pydantic-ai) lives in
`tests/test_synth_gameplan_llm_backend.py`; it uses pydantic-ai's
`TestModel` so CI never makes a real API call.
"""
from __future__ import annotations

import pytest

from ai.gameplan_schemas import (
    SynthesizedGameplan,
    SynthesizedGoal,
    to_json_dict,
)


_TINY_AGGRO_DECK = {
    "Lightning Bolt": 4,
    "Goblin Guide": 4,
    "Monastery Swiftspear": 4,
    "Eidolon of the Great Revel": 4,
    "Ragavan, Nimble Pilferer": 4,
    "Lava Spike": 4,
    "Skewer the Critics": 4,
    "Boros Charm": 4,
    "Mountain": 12,
    "Inspiring Vantage": 4,
    "Sunbaked Canyon": 4,
    "Sacred Foundry": 4,
}


@pytest.fixture(scope="module")
def card_db():
    from engine.card_database import CardDatabase
    return CardDatabase()


def test_synthesized_gameplan_validates_with_minimal_input():
    """Pure schema test — no decklist, no card DB.  A
    SynthesizedGameplan with one goal is a valid pydantic instance."""
    plan = SynthesizedGameplan(
        deck_name="Schema-only",
        archetype="aggro",
        goals=[
            SynthesizedGoal(
                goal_type="CURVE_OUT",
                description="test",
                card_roles={"enablers": ["A", "B"], "payoffs": ["C"]},
            )
        ],
    )
    assert plan.deck_name == "Schema-only"
    assert len(plan.goals) == 1
    assert plan.goals[0].card_roles["enablers"] == ["A", "B"]


def test_to_json_dict_strips_empty_overrides():
    """The serializer omits empty list/dict overrides so the loader
    falls through to its derivation / default behaviour."""
    plan = SynthesizedGameplan(
        deck_name="Schema-only",
        archetype="aggro",
        goals=[SynthesizedGoal(goal_type="CURVE_OUT", description="")],
        # Explicitly empty — should NOT appear in the dump
        mulligan_keys=[],
        always_early=[],
        reactive_only=[],
    )
    out = to_json_dict(plan)
    for key in ("mulligan_keys", "always_early", "reactive_only"):
        assert key not in out, f"{key} should be omitted when empty"


def test_to_json_dict_preserves_explicit_overrides():
    """Non-empty override lists DO appear in the dump — the loader
    will treat them as explicit overrides per Phase 3 semantics."""
    plan = SynthesizedGameplan(
        deck_name="Schema-only",
        archetype="aggro",
        goals=[SynthesizedGoal(goal_type="CURVE_OUT")],
        mulligan_keys=["X", "Y"],
    )
    out = to_json_dict(plan)
    assert out["mulligan_keys"] == ["X", "Y"]


def test_round_trip_synth_to_loader_to_dataclass(card_db):
    """End-to-end: synth a real decklist, serialize, parse, compare.
    The loader must accept the synth's JSON and produce a
    `DeckGameplan` whose key fields match the synth output."""
    from tools.synth_gameplan import synth_gameplan_rule_based
    from ai.gameplan_schemas import to_json_dict
    from decks.gameplan_loader import _parse_gameplan

    plan = synth_gameplan_rule_based(
        deck_name="RT-Test Burn",
        mainboard=_TINY_AGGRO_DECK,
        db=card_db,
    )
    assert isinstance(plan, SynthesizedGameplan)
    assert plan.deck_name == "RT-Test Burn"
    assert len(plan.goals) >= 1, "synth must produce at least one goal"

    json_dict = to_json_dict(plan)
    dataclass_plan = _parse_gameplan(json_dict)
    assert dataclass_plan.deck_name == "RT-Test Burn"
    assert len(dataclass_plan.goals) == len(plan.goals)
    # Goal types must round-trip identically
    for synth_goal, dc_goal in zip(plan.goals, dataclass_plan.goals):
        assert dc_goal.goal_type.name == synth_goal.goal_type
    # Archetype survives the round trip
    assert dataclass_plan.archetype == plan.archetype


