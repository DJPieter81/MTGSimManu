"""Budget-gating integration: `MeteredAgent` calls `check_budget`
before every API request, passes a per-call input-token cap to the
underlying pydantic-ai agent, and refuses to dispatch when the
budget is exhausted.

Tests use TestModel for the underlying agent so no real network call
is made; the metrics file is redirected to a tmp path.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai import llm_agents, llm_budgets, llm_metrics


_MOCK_SYNTH_PAYLOAD = {
    "deck_name": "Mock Deck",
    "archetype": "aggro",
    "goals": [{"goal_type": "CURVE_OUT", "description": "deploy"}],
}


@pytest.fixture
def metrics_file(tmp_path, monkeypatch):
    target = tmp_path / "calls.jsonl"
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", target)
    return target


@pytest.fixture
def cache_disabled():
    """Build agents with cache disabled — keeps the inner call path
    deterministic for budget-gating assertions (no SQLite touched)."""
    yield {"use_cache": False}


# ─── check_budget is invoked before run_sync ────────────────────────────


def test_run_sync_calls_check_budget_before_api(metrics_file, cache_disabled, monkeypatch):
    """MeteredAgent.run_sync must call check_budget exactly once before
    delegating to the wrapped agent."""
    from pydantic_ai.models.test import TestModel

    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "100.00")
    test_model = TestModel(custom_output_args=_MOCK_SYNTH_PAYLOAD)
    agent = llm_agents.build_agent("synth_gameplan", **cache_disabled)

    captured: dict = {}
    real_check = llm_budgets.check_budget

    def spy(task, model, input_tokens_est, *, budget_override=None):
        captured.setdefault("calls", 0)
        captured["calls"] += 1
        captured["last"] = (task, model, input_tokens_est, budget_override)
        return real_check(task, model, input_tokens_est, budget_override=budget_override)

    # Patch the symbol *as imported inside MeteredAgent.run_sync* —
    # since the import is local, patching the module attribute works.
    monkeypatch.setattr(llm_budgets, "check_budget", spy)

    with agent.override(model=test_model):
        agent.run_sync("hello world")

    assert captured["calls"] == 1
    task, model, est_tokens, override = captured["last"]
    assert task == "synth_gameplan"
    assert isinstance(est_tokens, int) and est_tokens >= 1
    assert override is None


# ─── budget exhausted → no API call ─────────────────────────────────────


def test_run_sync_raises_budget_exceeded_before_calling_api(metrics_file, cache_disabled, monkeypatch):
    """A tiny cap (and no prior spend) still gates a synth_gameplan call
    if the estimated cost exceeds the cap.  The wrapped agent's
    run_sync must NEVER be called."""
    from pydantic_ai.models.test import TestModel

    # Cap is so small even an estimate hits it — Sonnet pricing means
    # 8000 input tokens × $3/Mtok already exceeds $0.000001.
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "0.000001")

    test_model = TestModel(custom_output_args=_MOCK_SYNTH_PAYLOAD)
    agent = llm_agents.build_agent("synth_gameplan", **cache_disabled)

    # Spy on the wrapped agent's run_sync — must not be called.
    inner = agent._agent  # the raw pydantic-ai Agent
    call_count = {"n": 0}
    real_inner_run = inner.run_sync

    def spy_inner(*args, **kwargs):
        call_count["n"] += 1
        return real_inner_run(*args, **kwargs)

    monkeypatch.setattr(inner, "run_sync", spy_inner)

    with agent.override(model=test_model):
        with pytest.raises(llm_budgets.BudgetExceededError):
            agent.run_sync("a relatively long prompt " * 200)

    assert call_count["n"] == 0, "API path must not be entered when budget is exhausted"


def test_run_sync_logs_budget_block_to_metrics(metrics_file, cache_disabled, monkeypatch):
    """When the budget gate blocks a call, the failure is appended to
    the metrics file (success=False) so cost reports see budget blocks."""
    from pydantic_ai.models.test import TestModel

    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "0.000001")
    test_model = TestModel(custom_output_args=_MOCK_SYNTH_PAYLOAD)
    agent = llm_agents.build_agent("synth_gameplan", **cache_disabled)

    with agent.override(model=test_model):
        with pytest.raises(llm_budgets.BudgetExceededError):
            agent.run_sync("a long prompt " * 500)

    records = [json.loads(line) for line in metrics_file.read_text().splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["success"] is False
    assert "BudgetExceededError" in (records[0]["error"] or "")


# ─── Token cap reaches the underlying agent ─────────────────────────────


def test_token_cap_passed_to_underlying_agent(metrics_file, cache_disabled, monkeypatch):
    """`usage_limits=UsageLimits(input_tokens_limit=...)` must be
    injected into the call to the wrapped agent."""
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import UsageLimits

    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "100.00")
    monkeypatch.setenv("MTG_LLM_TOKEN_CAP_SYNTH_GAMEPLAN", "5555")

    test_model = TestModel(custom_output_args=_MOCK_SYNTH_PAYLOAD)
    agent = llm_agents.build_agent("synth_gameplan", **cache_disabled)
    inner = agent._agent

    captured: dict = {}
    real_run = inner.run_sync

    def spy_run(prompt, **kwargs):
        captured["usage_limits"] = kwargs.get("usage_limits")
        return real_run(prompt, **kwargs)

    monkeypatch.setattr(inner, "run_sync", spy_run)

    with agent.override(model=test_model):
        agent.run_sync("hello")

    ul = captured["usage_limits"]
    assert isinstance(ul, UsageLimits)
    assert ul.input_tokens_limit == 5555


def test_caller_supplied_usage_limits_is_preserved(metrics_file, cache_disabled, monkeypatch):
    """If the caller already passes `usage_limits=`, the wrapper must
    NOT clobber it."""
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import UsageLimits

    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "100.00")
    test_model = TestModel(custom_output_args=_MOCK_SYNTH_PAYLOAD)
    agent = llm_agents.build_agent("synth_gameplan", **cache_disabled)
    inner = agent._agent

    captured: dict = {}
    real_run = inner.run_sync

    def spy_run(prompt, **kwargs):
        captured["usage_limits"] = kwargs.get("usage_limits")
        return real_run(prompt, **kwargs)

    monkeypatch.setattr(inner, "run_sync", spy_run)

    # Use a deliberately distinctive limit large enough that the
    # TestModel's system-prompt + few-shot block doesn't exceed it.
    explicit = UsageLimits(input_tokens_limit=99999)
    with agent.override(model=test_model):
        agent.run_sync("hello", usage_limits=explicit)

    assert captured["usage_limits"] is explicit
    assert captured["usage_limits"].input_tokens_limit == 99999
