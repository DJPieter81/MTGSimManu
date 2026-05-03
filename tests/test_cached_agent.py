"""Tests for the `CachedAgent` wrapper produced by
`ai.llm_agents.build_agent(use_cache=True)`.

CI never makes a real API call.  We use `pydantic_ai.models.test.TestModel`
as the deterministic mock model and assert call counts via a shared
counter object that wraps the TestModel — pydantic-ai doesn't expose
a built-in counter, so we instrument with a thin wrapper.

The cache module is monkeypatched onto a tmp directory per test to
guarantee isolation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from ai import llm_cache
from ai.llm_agents import CachedAgent, build_agent
from ai.llm_schemas import SynthesizedGameplan


_VALID_PAYLOAD: dict = {
    "deck_name": "Mock LLM Deck",
    "archetype": "aggro",
    "goals": [
        {
            "goal_type": "CURVE_OUT",
            "description": "deploy",
            "card_roles": {"enablers": ["Goblin Guide"]},
        }
    ],
}


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint the LLM cache at a tmp directory so tests don't leak
    between each other or pollute the operator's local cache."""
    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    return cache_dir


class _CountingTestModel:
    """Wraps a `TestModel` and counts how many times the underlying
    agent dispatched a model call to it.

    Implementation note: pydantic-ai's `Agent.override(model=...)`
    expects a real model object; rather than monkey-patching its
    request method, we wrap the agent's `run_sync` itself in
    `_make_counted_run_sync` below, which is simpler and equally
    diagnostic for cache-hit/miss assertions.
    """

    def __init__(self, payload: Any) -> None:
        self.test_model = TestModel(custom_output_args=payload)
        self.calls = 0


def _make_counted_run_sync(agent: Any, counter: _CountingTestModel) -> None:
    """Wrap `agent.run_sync` so every dispatched call increments
    `counter.calls`.  We patch the wrapped agent (the raw pydantic-ai
    Agent inside the CachedAgent), not the CachedAgent itself — that
    way cache hits never increment the counter, which is exactly the
    behaviour we want to assert."""
    original_run_sync = agent.run_sync

    def counted_run_sync(*args: Any, **kwargs: Any) -> Any:
        counter.calls += 1
        return original_run_sync(*args, **kwargs)

    agent.run_sync = counted_run_sync  # type: ignore[method-assign]


# ─── Cache miss → underlying model called ───────────────────────────


def test_cache_miss_calls_underlying_agent() -> None:
    """First call with a never-seen input → cache miss → underlying
    agent runs once.  Output is a validated SynthesizedGameplan."""
    counter = _CountingTestModel(_VALID_PAYLOAD)

    agent = build_agent("synth_gameplan", use_cache=True)
    assert isinstance(agent, CachedAgent)

    _make_counted_run_sync(agent._agent, counter)

    with agent.override(model=counter.test_model):
        result = agent.run_sync({"deck_name": "miss-1", "mainboard": {"Bolt": 4}})

    assert counter.calls == 1
    assert isinstance(result.output, SynthesizedGameplan)
    assert result.output.deck_name == "Mock LLM Deck"


# ─── Cache hit → underlying model NOT called ────────────────────────


def test_cache_hit_skips_underlying_agent() -> None:
    """Second identical call → cache hit → underlying agent never
    invoked again, but result equivalent."""
    counter = _CountingTestModel(_VALID_PAYLOAD)
    agent = build_agent("synth_gameplan", use_cache=True)
    assert isinstance(agent, CachedAgent)
    _make_counted_run_sync(agent._agent, counter)

    payload = {"deck_name": "hit-1", "mainboard": {"Bolt": 4}}

    with agent.override(model=counter.test_model):
        r1 = agent.run_sync(payload)
        r2 = agent.run_sync(payload)

    # First call was a miss (underlying invoked once); second was a
    # hit (no additional underlying invocation).
    assert counter.calls == 1
    assert isinstance(r1.output, SynthesizedGameplan)
    assert isinstance(r2.output, SynthesizedGameplan)
    assert r1.output.deck_name == r2.output.deck_name
    assert r1.output.archetype == r2.output.archetype

    # Cache statistics confirm exactly one stored entry, one hit.
    stats = llm_cache.cache_stats(task="synth_gameplan")
    assert stats["entries"] == 1
    assert stats["total_hits"] == 1


def test_cache_hit_preserves_field_values() -> None:
    """A cache-hit result is byte-for-byte equivalent to the original
    via the schema's serialized form — proving the cache isn't
    silently mutating fields."""
    counter = _CountingTestModel(_VALID_PAYLOAD)
    agent = build_agent("synth_gameplan", use_cache=True)
    assert isinstance(agent, CachedAgent)
    _make_counted_run_sync(agent._agent, counter)

    payload = {"deck_name": "fields", "mainboard": {"Bolt": 4}}
    with agent.override(model=counter.test_model):
        r1 = agent.run_sync(payload)
        r2 = agent.run_sync(payload)

    assert r1.output.model_dump() == r2.output.model_dump()


# ─── use_cache=False bypasses ───────────────────────────────────────


def test_use_cache_false_bypasses() -> None:
    """`build_agent(..., use_cache=False)` returns the raw pydantic-ai
    Agent — every call hits the underlying model, no cache lookups."""
    counter = _CountingTestModel(_VALID_PAYLOAD)
    raw_agent = build_agent("synth_gameplan", use_cache=False)

    # Not a CachedAgent — explicit bypass requested.
    assert not isinstance(raw_agent, CachedAgent)
    _make_counted_run_sync(raw_agent, counter)

    payload = {"deck_name": "no-cache", "mainboard": {"Bolt": 4}}
    with raw_agent.override(model=counter.test_model):
        raw_agent.run_sync(payload)
        raw_agent.run_sync(payload)

    # Both calls hit the underlying agent.
    assert counter.calls == 2
    # And nothing was written to the cache.
    assert llm_cache.cache_stats()["entries"] == 0


# ─── override() forwards to wrapped agent ───────────────────────────


def test_override_propagates_to_underlying() -> None:
    """`CachedAgent.override(model=...)` must forward to the wrapped
    pydantic-ai Agent so test fixtures (TestModel injection) keep
    working unchanged."""
    counter = _CountingTestModel(_VALID_PAYLOAD)
    agent = build_agent("synth_gameplan", use_cache=True)
    assert isinstance(agent, CachedAgent)
    _make_counted_run_sync(agent._agent, counter)

    with agent.override(model=counter.test_model):
        result = agent.run_sync(
            {"deck_name": "override-test", "mainboard": {"Bolt": 4}}
        )

    # The override delivered our payload (proving forwarding worked).
    assert result.output.deck_name == "Mock LLM Deck"
    assert counter.calls == 1


# ─── different inputs do not collide in cache ──────────────────────


def test_different_inputs_produce_separate_entries() -> None:
    """Two calls with different inputs both miss, both run the
    underlying agent, and the cache ends with two rows."""
    counter = _CountingTestModel(_VALID_PAYLOAD)
    agent = build_agent("synth_gameplan", use_cache=True)
    assert isinstance(agent, CachedAgent)
    _make_counted_run_sync(agent._agent, counter)

    with agent.override(model=counter.test_model):
        agent.run_sync({"deck_name": "A", "mainboard": {"Bolt": 4}})
        agent.run_sync({"deck_name": "B", "mainboard": {"Bolt": 4}})

    assert counter.calls == 2
    assert llm_cache.cache_stats(task="synth_gameplan")["entries"] == 2
