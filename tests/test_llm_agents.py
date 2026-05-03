"""Build + override contract for `ai.llm_agents.build_agent`.

For each supported task this asserts:
  1. `build_agent(task)` constructs without crashing in CI (no API
     key required — `defer_model_check=True` is set).
  2. The returned agent's `output_type` matches the schema for the
     task.
  3. `agent.override(model=TestModel(...))` works and the agent's
     `run_sync(...)` returns a parsed instance of the right schema.

These tests use pydantic-ai's `TestModel` for output, so no real
LLM call is made.  The hardcoded card-name strings in the mock
payloads are test fixtures only; they are not in `engine/` or `ai/`
source and therefore don't trip the abstraction ratchet."""
from __future__ import annotations

from typing import Any, get_args

import pytest

from ai.llm_agents import build_agent, supported_tasks
from ai.llm_models import LLMTask
from ai.llm_schemas import (
    BugHypothesis,
    DocFreshnessReport,
    FailingTestSpec,
    HandlerGapReport,
    SynthesizedGameplan,
)


# Per-task mock payloads — minimal shapes that satisfy each schema.
# These exist only as TestModel `custom_output_args`; they never feed
# real game logic.
_MOCK_OUTPUTS: dict[str, Any] = {
    "synth_gameplan": {
        "deck_name": "Mock Deck",
        "archetype": "aggro",
        "goals": [{"goal_type": "CURVE_OUT", "description": "deploy"}],
    },
    "diagnose_replay": [
        {
            "observed_symptom": "AI passed turn with mana up",
            "suspected_subsystem": "ai.ev_player",
            "failing_test_rule": "main-phase action chosen when nonzero ev play exists",
            "confidence": 0.9,
        }
    ],
    "audit_doc_freshness": {
        "doc_path": "docs/diagnostics/example.md",
        "current_status": "active",
        "should_change_to": None,
        "replacement_doc": None,
        "reason": "Doc is current; no superseder.",
    },
    "handler_audit": {
        "card_name": "Mock Card",
        "timing": "ETB",
        "printed_modes": ["effect"],
        "handler_modes": ["effect"],
        "missing_modes": [],
        "fabricated_modes": [],
        "severity": "P2",
    },
    "failing_test_spec": {
        "test_file": "tests/test_x.py",
        "rule_name": "x mechanic enforces y",
        "fixture_setup": "...",
        "assertion": "...",
        "expected_status_before_fix": "fail",
    },
}


# Expected output type per task (matches `_OUTPUT_TYPES` in the module).
_EXPECTED_TYPES: dict[str, type] = {
    "synth_gameplan":      SynthesizedGameplan,
    "diagnose_replay":     list,  # list[BugHypothesis]
    "audit_doc_freshness": DocFreshnessReport,
    "handler_audit":       HandlerGapReport,
    "failing_test_spec":   FailingTestSpec,
}


def test_supported_tasks_matches_llmtask_literal():
    """`supported_tasks()` enumerates every LLMTask literal value."""
    assert set(supported_tasks()) == set(get_args(LLMTask))


@pytest.mark.parametrize("task", list(_MOCK_OUTPUTS.keys()))
def test_build_agent_constructs_without_api_key(task):
    """Construction is deferred (`defer_model_check=True`), so no API
    key is required in CI.  Each task must build cleanly."""
    agent = build_agent(task)
    assert agent is not None


@pytest.mark.parametrize("task", list(_MOCK_OUTPUTS.keys()))
def test_build_agent_runs_via_test_model_override(task):
    """`agent.override(model=TestModel(...))` swaps in a deterministic
    mock model.  The agent then runs without contacting the network
    and emits the right schema."""
    from pydantic_ai.models.test import TestModel

    payload = _MOCK_OUTPUTS[task]
    test_model = TestModel(custom_output_args=payload)

    agent = build_agent(task)
    with agent.override(model=test_model):
        result = agent.run_sync("ignored prompt — TestModel echoes the payload")

    output = result.output
    expected = _EXPECTED_TYPES[task]
    if expected is list:
        assert isinstance(output, list)
        assert len(output) >= 1
        assert isinstance(output[0], BugHypothesis)
    else:
        assert isinstance(output, expected)


def test_build_agent_explicit_model_override(monkeypatch):
    """Passing `model=...` to `build_agent` flows down to the agent's
    constructor, beating any env-var override."""
    captured: dict = {}

    from pydantic_ai import Agent
    real_init = Agent.__init__

    def spy_init(self, model=None, *args, **kwargs):
        captured["model"] = model
        return real_init(self, model, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", spy_init)
    monkeypatch.setenv("MTG_LLM_MODEL", "anthropic:from-env")

    build_agent("synth_gameplan", model="anthropic:explicit-arg")

    assert captured["model"] == "anthropic:explicit-arg"


def test_build_agent_uses_explicit_prompt_version():
    """Passing `prompt_version=1` reads the v1 file even if higher
    versions are available later — pinning experiments."""
    # We can't easily stage a v2 file in CI, so this asserts the
    # explicit-version code path runs and produces an agent.  If the
    # file doesn't exist `build_agent` would raise FileNotFoundError.
    agent = build_agent("synth_gameplan", prompt_version=1)
    assert agent is not None
