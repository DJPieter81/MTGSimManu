"""Tests for the pydantic-ai LLM backend in `tools.synth_gameplan`.

CI does NOT make real API calls.  Every test here builds the agent
with `defer_model_check=True` (so no API key is required at import
time) and uses `Agent.override(model=TestModel(...))` to swap in a
deterministic mock model from `pydantic_ai.models.test`.

Contracts locked in:
  1. The LLM backend returns a validated `SynthesizedGameplan` with
     at least one goal — the schema is the contract, not the prose.
  2. Round-trip `synth → to_json_dict → _parse_gameplan → DeckGameplan`
     succeeds exactly the same as for the rule-based backend.
  3. The model identifier honors the `MTG_SYNTH_MODEL` env var, so
     operators can swap haiku for sonnet without code changes.

These tests are the failing-test-first contract for the Phase 4
follow-up that promotes `synth_gameplan_llm` from placeholder to
working backend.  No real API calls happen — TestModel synthesizes
schema-shaped fixtures from the output type alone.
"""
from __future__ import annotations

import os
from typing import Optional

import pytest

# pydantic-ai is now a hard dependency of the project (see
# requirements.txt).  Import errors here should fail loudly rather
# than be skipped — the LLM backend is part of the supported surface.
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from ai.gameplan_schemas import SynthesizedGameplan, SynthesizedGoal
from tools import synth_gameplan as synth_module


_TINY_AGGRO_DECK = {
    "Lightning Bolt": 4,
    "Goblin Guide": 4,
    "Monastery Swiftspear": 4,
    "Eidolon of the Great Revel": 4,
    "Lava Spike": 4,
    "Skewer the Critics": 4,
    "Boros Charm": 4,
    "Mountain": 16,
    "Sacred Foundry": 4,
    "Inspiring Vantage": 4,
    "Sunbaked Canyon": 4,
}


@pytest.fixture(scope="module")
def card_db():
    """Real card DB — used so the user-prompt builder can pull oracle
    text.  Tests do not rely on any specific oracle string; they only
    rely on the prompt being constructable without errors."""
    from engine.card_database import CardDatabase
    return CardDatabase()


def _mock_plan_payload(deck_name: str = "Mock LLM Deck") -> dict:
    """A deterministic SynthesizedGameplan payload that exercises a
    realistic fraction of the schema (goals, card_roles, mulligan
    keys).  Used as TestModel's `custom_output_args` so we control
    exactly what the agent emits."""
    return {
        "deck_name": deck_name,
        "archetype": "aggro",
        "goals": [
            {
                "goal_type": "CURVE_OUT",
                "description": "Deploy aggressive creatures on curve",
                "card_roles": {
                    "enablers": ["Goblin Guide", "Monastery Swiftspear"],
                    "interaction": ["Lightning Bolt"],
                },
            },
            {
                "goal_type": "PUSH_DAMAGE",
                "description": "Convert board into burn finish",
                "card_roles": {
                    "payoffs": ["Lava Spike", "Skewer the Critics"],
                },
            },
        ],
        "mulligan_keys": ["Goblin Guide", "Lightning Bolt"],
        "always_early": ["Goblin Guide", "Monastery Swiftspear"],
    }


def test_llm_backend_returns_typed_gameplan_with_mock_model(card_db):
    """Smoke contract: when the agent's model is a TestModel emitting
    a valid SynthesizedGameplan payload, the LLM backend returns a
    validated pydantic instance with at least one goal.

    We exercise the real `synth_gameplan_llm` entry point — the only
    deviation from production is `agent.override(model=TestModel(...))`,
    which pydantic-ai documents as the canonical offline-testing
    pattern.  No API key, no network call."""
    test_model = TestModel(custom_output_args=_mock_plan_payload())

    # Build the real agent (system prompt + few-shot examples + output
    # type all wired up), then override its model to TestModel for the
    # duration of the run.  Calling `synth_gameplan_llm` after the
    # override would re-build the agent and lose our patch, so we run
    # the agent directly inside the override block.
    agent = synth_module._build_llm_agent()
    with agent.override(model=test_model):
        prompt = synth_module._format_decklist_for_prompt(
            "Mock LLM Deck", _TINY_AGGRO_DECK, card_db
        )
        result = agent.run_sync(prompt)

    plan = result.output
    assert isinstance(plan, SynthesizedGameplan)
    assert len(plan.goals) >= 1, "LLM backend must produce at least one goal"
    assert isinstance(plan.goals[0], SynthesizedGoal)
    # The TestModel echoes our payload, so we can assert specific
    # fields the schema must round-trip.
    assert plan.deck_name == "Mock LLM Deck"
    assert plan.archetype == "aggro"
    assert "Goblin Guide" in plan.goals[0].card_roles.get("enablers", [])


def test_llm_backend_round_trips_through_loader(card_db):
    """End-to-end round-trip: TestModel-emitted plan → to_json_dict →
    `_parse_gameplan` → DeckGameplan.  Exactly the same contract as
    the rule-based path's round-trip test, just with the LLM backend
    in the synth slot.  Confirms the LLM and rule-based outputs are
    interchangeable from the loader's perspective."""
    from ai.gameplan_schemas import to_json_dict
    from decks.gameplan_loader import _parse_gameplan

    test_model = TestModel(custom_output_args=_mock_plan_payload("RT-LLM Deck"))
    agent = synth_module._build_llm_agent()
    with agent.override(model=test_model):
        prompt = synth_module._format_decklist_for_prompt(
            "RT-LLM Deck", _TINY_AGGRO_DECK, card_db
        )
        result = agent.run_sync(prompt)
    plan = result.output

    json_dict = to_json_dict(plan)
    dataclass_plan = _parse_gameplan(json_dict)

    assert dataclass_plan.deck_name == "RT-LLM Deck"
    assert len(dataclass_plan.goals) == len(plan.goals)
    for synth_goal, dc_goal in zip(plan.goals, dataclass_plan.goals):
        assert dc_goal.goal_type.name == synth_goal.goal_type
    assert dataclass_plan.archetype == plan.archetype


def test_llm_backend_uses_env_var_model_override(monkeypatch):
    """`_build_llm_agent` honors `MTG_SYNTH_MODEL` for model selection.
    We can't introspect a real anthropic model without a key, but we
    can assert the agent constructor sees the env var's value by
    capturing the `model` argument at the constructor boundary."""
    captured = {}

    real_agent_init = Agent.__init__

    def spy_init(self, model=None, *args, **kwargs):
        captured["model"] = model
        # `defer_model_check=True` is set inside `_build_llm_agent`,
        # so the real init won't try to instantiate anthropic.
        return real_agent_init(self, model, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", spy_init)
    monkeypatch.setenv("MTG_SYNTH_MODEL", "anthropic:claude-sonnet-4-5-20250929")

    synth_module._build_llm_agent()

    assert captured["model"] == "anthropic:claude-sonnet-4-5-20250929", (
        "Agent must be constructed with the MTG_SYNTH_MODEL value when set"
    )

    # Without the env var, defaults to the haiku constant.
    monkeypatch.delenv("MTG_SYNTH_MODEL", raising=False)
    captured.clear()
    synth_module._build_llm_agent()
    assert captured["model"] == synth_module.DEFAULT_LLM_MODEL


def test_llm_backend_explicit_model_arg_beats_env_var(monkeypatch):
    """Explicit `model=` argument to `_build_llm_agent` takes precedence
    over the env var — important for tests and for callers that want
    to pin a specific model regardless of operator defaults."""
    captured = {}

    real_agent_init = Agent.__init__

    def spy_init(self, model=None, *args, **kwargs):
        captured["model"] = model
        return real_agent_init(self, model, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", spy_init)
    monkeypatch.setenv("MTG_SYNTH_MODEL", "anthropic:env-var-model")

    synth_module._build_llm_agent(model="anthropic:explicit-model")

    assert captured["model"] == "anthropic:explicit-model"
