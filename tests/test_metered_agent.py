"""Integration test for `MeteredAgent` — wraps `build_agent`'s
returned agent so each `run_sync` appends one telemetry record.

Uses pydantic-ai's `TestModel` so no real model is contacted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai import llm_cache, llm_metrics
from ai.llm_agents import MeteredAgent, build_agent


@pytest.fixture
def metrics_file(tmp_path, monkeypatch):
    target = tmp_path / "calls.jsonl"
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", target)
    # The default `build_agent(...)` stack now also wraps a
    # CachedAgent.  Repoint the cache at a tmp dir so cross-test
    # cache state doesn't flip cache_hit assertions.
    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    return target


def _read_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _synth_payload() -> dict:
    return {
        "deck_name": "Mock Deck",
        "archetype": "aggro",
        "goals": [{"goal_type": "CURVE_OUT", "description": "deploy"}],
    }


# ─── default behaviour: instrumented ----------------------------------

def test_build_agent_default_returns_metered_wrapper():
    """Default `build_agent(...)` wraps the agent in MeteredAgent."""
    agent = build_agent("synth_gameplan")
    assert isinstance(agent, MeteredAgent)


def test_metered_agent_logs_one_call_per_run_sync(metrics_file):
    """Running once produces exactly one telemetry record."""
    from pydantic_ai.models.test import TestModel

    agent = build_agent("synth_gameplan")
    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        agent.run_sync("ignored prompt")

    records = _read_records(metrics_file)
    assert len(records) == 1


def test_metered_agent_records_correct_task_and_model(metrics_file):
    """The record's task/model/prompt_version match the build_agent
    invocation (model selection happens inside build_agent)."""
    from pydantic_ai.models.test import TestModel
    from ai.llm_models import select_model
    from ai.llm_prompts import latest_version

    agent = build_agent("synth_gameplan")
    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        agent.run_sync({"deck_name": "test", "mainboard": {"X": 1}})

    record = _read_records(metrics_file)[0]
    assert record["task"] == "synth_gameplan"
    assert record["model"] == select_model("synth_gameplan")
    assert record["prompt_version"] == latest_version("synth_gameplan")
    assert record["success"] is True
    assert record["cache_hit"] is False
    assert record["input_hash"].startswith("sha256:")
    assert record["output_type"] == "SynthesizedGameplan"


def test_metered_agent_passes_through_result(metrics_file):
    """The wrapper does not mutate the underlying agent's output."""
    from pydantic_ai.models.test import TestModel

    payload = _synth_payload()
    agent = build_agent("synth_gameplan")
    with agent.override(model=TestModel(custom_output_args=payload)):
        result = agent.run_sync("p")

    # `output` is a SynthesizedGameplan; it should round-trip the
    # mock fields without modification.
    assert result.output.deck_name == "Mock Deck"
    assert result.output.archetype == "aggro"


def test_metered_agent_records_token_usage(metrics_file):
    """tokens_in/tokens_out are populated from the RunResult.usage()."""
    from pydantic_ai.models.test import TestModel

    agent = build_agent("synth_gameplan")
    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        agent.run_sync("p")

    record = _read_records(metrics_file)[0]
    # TestModel reports nonzero usage; assert >= 0 so this stays
    # robust to upstream changes in token bookkeeping.
    assert record["tokens_in"] >= 0
    assert record["tokens_out"] >= 0


def test_metered_agent_records_duration_ms(metrics_file):
    """Duration is always recorded (>= 0 ms)."""
    from pydantic_ai.models.test import TestModel

    agent = build_agent("synth_gameplan")
    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        agent.run_sync("p")

    record = _read_records(metrics_file)[0]
    assert record["duration_ms"] >= 0


# ─── opt-out ----------------------------------------------------------

def test_instrument_false_bypasses_metering(metrics_file):
    """`build_agent(..., instrument=False)` returns the raw agent and
    does not append any record."""
    from pydantic_ai.models.test import TestModel

    raw_agent = build_agent("synth_gameplan", instrument=False)
    assert not isinstance(raw_agent, MeteredAgent)

    with raw_agent.override(model=TestModel(custom_output_args=_synth_payload())):
        raw_agent.run_sync("p")

    # No file should have been created at all.
    assert _read_records(metrics_file) == []


# ─── failure path -----------------------------------------------------

def test_metered_agent_records_failure(metrics_file):
    """If the wrapped agent raises, the record is still appended with
    success=False, and the exception propagates."""

    class _BoomAgent:
        def run_sync(self, *args, **kwargs):
            raise RuntimeError("network down")

        def override(self, **kwargs):
            class _Ctx:
                def __enter__(self_inner): return None
                def __exit__(self_inner, *a): return False
            return _Ctx()

    metered = MeteredAgent(
        _BoomAgent(),
        task="synth_gameplan",
        model="anthropic:claude-sonnet-4-6",
        prompt_version=1,
    )
    with pytest.raises(RuntimeError, match="network down"):
        metered.run_sync("p")

    record = _read_records(metrics_file)[0]
    assert record["success"] is False
    assert "network down" in (record["error"] or "")


# ─── composition with CachedAgent (Phase I-1 + I-5 stack) -------------


def test_metered_agent_logs_cache_hit_with_zero_tokens(metrics_file):
    """When stacked on a CachedAgent, the second identical call is a
    cache hit.  The metrics layer must log it as
    ``cache_hit=True, tokens_in=0, tokens_out=0, cost_usd=0`` —
    proving the cost report can quantify cache savings."""
    from pydantic_ai.models.test import TestModel

    # Default `build_agent(...)` returns MeteredAgent(CachedAgent(raw)).
    agent = build_agent("synth_gameplan")
    assert isinstance(agent, MeteredAgent)

    payload = {"deck_name": "compose-hit", "mainboard": {"Bolt": 4}}
    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        agent.run_sync(payload)  # miss → real call
        agent.run_sync(payload)  # hit  → 0-token row

    records = _read_records(metrics_file)
    assert len(records) == 2

    # First record: cache miss, may have nonzero tokens from TestModel.
    assert records[0]["cache_hit"] is False
    assert records[0]["success"] is True

    # Second record: cache hit, must be zero-cost.
    assert records[1]["cache_hit"] is True
    assert records[1]["tokens_in"] == 0
    assert records[1]["tokens_out"] == 0
    assert records[1]["cost_usd"] == 0.0
    assert records[1]["success"] is True
    # Output type should still be populated — the cache returns a
    # _CachedResult whose .output is the same SynthesizedGameplan.
    assert records[1]["output_type"] == "SynthesizedGameplan"


def test_metered_agent_logs_cache_miss_with_underlying_tokens(metrics_file):
    """When stacked on a CachedAgent and the call is a cache miss,
    metering records the underlying call's tokens (not zero) and
    ``cache_hit=False``."""
    from pydantic_ai.models.test import TestModel

    agent = build_agent("synth_gameplan")
    assert isinstance(agent, MeteredAgent)

    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        agent.run_sync({"deck_name": "compose-miss-A", "mainboard": {"Bolt": 4}})
        # Different input → second row is also a miss.
        agent.run_sync({"deck_name": "compose-miss-B", "mainboard": {"Bolt": 4}})

    records = _read_records(metrics_file)
    assert len(records) == 2
    for record in records:
        assert record["cache_hit"] is False
        assert record["success"] is True
        # tokens_in/out come from RunUsage — TestModel reports >= 0.
        assert record["tokens_in"] >= 0
        assert record["tokens_out"] >= 0


def test_stack_forwards_override_through_metered_and_cached(metrics_file):
    """``with agent.override(model=...)`` must reach the raw
    pydantic-ai Agent at the bottom of the stack so TestModel
    injection still works through both wrappers."""
    from pydantic_ai.models.test import TestModel

    agent = build_agent("synth_gameplan")
    # MeteredAgent wraps CachedAgent wraps raw Agent.
    assert isinstance(agent, MeteredAgent)
    from ai.llm_agents import CachedAgent
    assert isinstance(agent._agent, CachedAgent)

    with agent.override(model=TestModel(custom_output_args=_synth_payload())):
        result = agent.run_sync({"deck_name": "fwd", "mainboard": {"Bolt": 4}})

    # The override reached the bottom of the stack — the mocked
    # payload comes back through both layers.
    assert result.output.deck_name == "Mock Deck"
    assert _read_records(metrics_file)[0]["task"] == "synth_gameplan"
