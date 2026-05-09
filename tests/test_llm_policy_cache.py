"""Phase 4C Week 1 — LLMPolicy cache + schema contract tests.

Verifies the cache infrastructure and structured-output contract
without requiring a real model. Tests use a deterministic
StubBackend that maps prompt → canned response, so the cache
layer can be exercised in isolation.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.llm.policy import (
    BackendUnavailable,
    LLMPolicy,
    LLMResponse,
    StubBackend,
)


def _stub_responder(prompt: str) -> str:
    """Echo a canned JSON for any prompt — for deterministic tests."""
    if "oracle:" in prompt.lower():
        return '{"effect": "draw", "amount": 1}'
    if "sideboard:" in prompt.lower():
        return '{"plus": ["Wear // Tear"], "minus": ["Blood Moon"]}'
    return '{"ok": true}'


def _json_parser(raw: str):
    return json.loads(raw)


# ─── Cache-key determinism ───────────────────────────────────────────


class TestCacheKey:
    def test_same_prompt_same_schema_same_backend_same_key(self):
        backend = StubBackend(name="stub-v1", responder=_stub_responder)
        p = LLMPolicy(backend=backend)
        k1 = p._cache_key("oracle: bolt", "oracle_v1")
        k2 = p._cache_key("oracle: bolt", "oracle_v1")
        assert k1 == k2

    def test_different_prompt_different_key(self):
        backend = StubBackend(name="stub-v1", responder=_stub_responder)
        p = LLMPolicy(backend=backend)
        k1 = p._cache_key("oracle: bolt", "oracle_v1")
        k2 = p._cache_key("oracle: counterspell", "oracle_v1")
        assert k1 != k2

    def test_different_schema_different_key(self):
        backend = StubBackend(name="stub-v1", responder=_stub_responder)
        p = LLMPolicy(backend=backend)
        k1 = p._cache_key("same prompt", "schema_a")
        k2 = p._cache_key("same prompt", "schema_b")
        assert k1 != k2

    def test_different_backend_different_key(self):
        b1 = StubBackend(name="stub-v1", responder=_stub_responder)
        b2 = StubBackend(name="stub-v2", responder=_stub_responder)
        p1 = LLMPolicy(backend=b1)
        p2 = LLMPolicy(backend=b2)
        k1 = p1._cache_key("p", "s")
        k2 = p2._cache_key("p", "s")
        assert k1 != k2, (
            "Different backend names must produce different cache "
            "keys — required for safe model upgrades (old cache "
            "entries stay valid for old runs)."
        )


# ─── Memory cache ────────────────────────────────────────────────────


class TestMemoryCache:
    def test_first_call_is_miss_second_is_hit(self):
        backend = StubBackend(name="stub", responder=_stub_responder)
        policy = LLMPolicy(backend=backend)
        r1 = policy.generate(
            prompt="oracle: bolt", schema_id="oracle_v1",
            parser=_json_parser,
        )
        assert r1.cache_hit is False
        r2 = policy.generate(
            prompt="oracle: bolt", schema_id="oracle_v1",
            parser=_json_parser,
        )
        assert r2.cache_hit is True

    def test_cache_replays_exact_parsed_object(self):
        backend = StubBackend(name="stub", responder=_stub_responder)
        policy = LLMPolicy(backend=backend)
        r1 = policy.generate(
            "oracle: bolt", "oracle_v1", _json_parser,
        )
        r2 = policy.generate(
            "oracle: bolt", "oracle_v1", _json_parser,
        )
        assert r1.parsed == r2.parsed
        assert r1.raw_text == r2.raw_text


# ─── Disk cache ──────────────────────────────────────────────────────


class TestDiskCache:
    def test_disk_cache_persists_across_policies(self, tmp_path: Path):
        # First policy writes the cache.
        b = StubBackend(name="stub", responder=_stub_responder)
        p1 = LLMPolicy(backend=b, cache_dir=tmp_path)
        r1 = p1.generate(
            "oracle: bolt", "oracle_v1", _json_parser,
        )
        assert r1.cache_hit is False

        # Second policy (fresh memory cache, same disk dir) reads it.
        b2 = StubBackend(
            name="stub",
            responder=lambda p: '{"WRONG": "would-fail-test"}',
        )
        p2 = LLMPolicy(backend=b2, cache_dir=tmp_path)
        r2 = p2.generate(
            "oracle: bolt", "oracle_v1", _json_parser,
        )
        assert r2.cache_hit is True
        assert r2.parsed == {"effect": "draw", "amount": 1}, (
            "Disk cache hit must replay the original response, "
            "not invoke the (now-different) backend."
        )

    def test_corrupted_disk_entry_falls_through(self, tmp_path: Path):
        # Pre-seed a corrupted file at the right cache key.
        b = StubBackend(name="stub", responder=_stub_responder)
        p = LLMPolicy(backend=b, cache_dir=tmp_path)
        key = p._cache_key("oracle: bolt", "oracle_v1")
        path = tmp_path / f"{key}.json"
        path.write_text("not valid json {{{")

        # Should fall through to the backend, not crash.
        r = p.generate("oracle: bolt", "oracle_v1", _json_parser)
        assert r.cache_hit is False
        assert r.parsed == {"effect": "draw", "amount": 1}

    def test_has_cached_reports_status(self, tmp_path: Path):
        b = StubBackend(name="stub", responder=_stub_responder)
        p = LLMPolicy(backend=b, cache_dir=tmp_path)
        assert not p.has_cached("oracle: bolt", "oracle_v1")
        p.generate("oracle: bolt", "oracle_v1", _json_parser)
        assert p.has_cached("oracle: bolt", "oracle_v1")


# ─── Schema enforcement ──────────────────────────────────────────────


class TestSchemaEnforcement:
    def test_invalid_output_raises_clean_error(self):
        bad = StubBackend(
            name="stub-bad",
            responder=lambda p: "not json — should fail",
        )
        p = LLMPolicy(backend=bad)
        with pytest.raises(ValueError) as exc_info:
            p.generate(
                "oracle: bolt", "oracle_v1", _json_parser,
            )
        assert "stub-bad" in str(exc_info.value)
        assert "oracle_v1" in str(exc_info.value)


# ─── Backend failure handling ────────────────────────────────────────


class TestBackendFailure:
    def test_backend_exception_raises_unavailable(self):
        class FailingBackend:
            name = "failing"

            def generate(self, prompt, max_tokens=256):
                raise OSError("model file missing")

        p = LLMPolicy(backend=FailingBackend())
        with pytest.raises(BackendUnavailable) as exc_info:
            p.generate("any", "any", _json_parser)
        assert "failing" in str(exc_info.value)


# ─── End-to-end: oracle parse mock ───────────────────────────────────


def test_e2e_oracle_parser_via_stub():
    """Demonstrate the intended oracle-parse usage. Real Phase 4C
    Week 2 deliverable replaces StubBackend with a llama.cpp
    backend for Qwen 2.5 7B; the schema contract and parser
    stay the same.
    """
    backend = StubBackend(name="stub", responder=_stub_responder)
    policy = LLMPolicy(backend=backend)
    r = policy.generate(
        prompt="oracle: Draw a card.",
        schema_id="oracle_effect_v1",
        parser=_json_parser,
    )
    assert r.parsed == {"effect": "draw", "amount": 1}
    assert r.cache_hit is False
    # Re-issue → cache hit, identical parsed object.
    r2 = policy.generate(
        prompt="oracle: Draw a card.",
        schema_id="oracle_effect_v1",
        parser=_json_parser,
    )
    assert r2.cache_hit is True
    assert r2.parsed == r.parsed
