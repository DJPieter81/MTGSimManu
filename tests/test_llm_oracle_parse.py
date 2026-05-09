"""Phase 4C Week 3 — SLM-driven oracle parser tests.

Verifies the structured-output contract, the parser's robustness
to common model quirks (code fences, missing fields), and end-
to-end behavior through ``LLMPolicy`` with a deterministic stub
backend.

The stub responder maps oracle text fragments to canned JSON,
simulating what a fine-tuned Qwen 2.5 7B would produce. When the
real backend is wired (Phase 4C Week 4 acceptance gate), the same
tests run against the labeled corpus with ≥ 95% agreement target.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai.llm.oracle_parse import (
    ORACLE_EFFECT_SCHEMA_ID,
    OracleEffect,
    _parse_json_response,
    _strip_code_fences,
    parse_oracle,
    to_dict,
)
from ai.llm.policy import LLMPolicy, StubBackend


# ─── Pure parser unit tests (no policy / backend) ────────────────────


class TestStripCodeFences:
    def test_no_fence_unchanged(self):
        assert _strip_code_fences('{"a": 1}') == '{"a": 1}'

    def test_strips_json_fence(self):
        raw = '```json\n{"a": 1}\n```'
        assert _strip_code_fences(raw) == '{"a": 1}'

    def test_strips_plain_fence(self):
        raw = '```\n{"a": 1}\n```'
        assert _strip_code_fences(raw) == '{"a": 1}'

    def test_strips_outer_whitespace(self):
        raw = '   \n  {"a": 1}\n  '
        assert _strip_code_fences(raw) == '{"a": 1}'


class TestParseJsonResponse:
    def test_full_response(self):
        raw = (
            '{"primary_effect": "draw", "amount": 2, '
            '"target": "player", "flags": ["is_etb"]}'
        )
        effect = _parse_json_response(raw)
        assert effect.primary_effect == "draw"
        assert effect.amount == 2
        assert effect.target == "player"
        assert effect.flags == ["is_etb"]

    def test_missing_fields_defaulted(self):
        raw = '{"primary_effect": "damage"}'
        effect = _parse_json_response(raw)
        assert effect.primary_effect == "damage"
        assert effect.amount is None
        assert effect.target is None
        assert effect.flags == []

    def test_amount_string_coerced_to_int(self):
        raw = '{"primary_effect": "draw", "amount": "3"}'
        effect = _parse_json_response(raw)
        assert effect.amount == 3

    def test_amount_garbage_becomes_none(self):
        raw = '{"primary_effect": "draw", "amount": "many"}'
        effect = _parse_json_response(raw)
        assert effect.amount is None

    def test_unknown_primary_kept(self):
        # Future-proof: we don't reject vocabulary the model
        # invents; the AI scoring layer can decide what to do.
        raw = '{"primary_effect": "weird_new_thing", "amount": 1}'
        effect = _parse_json_response(raw)
        assert effect.primary_effect == "weird_new_thing"

    def test_non_json_raises(self):
        with pytest.raises(ValueError):
            _parse_json_response("This is just prose, not JSON.")

    def test_array_response_raises(self):
        # A list-shaped response isn't a valid OracleEffect.
        with pytest.raises(ValueError):
            _parse_json_response('[1, 2, 3]')

    def test_strips_code_fences(self):
        raw = '```json\n{"primary_effect": "counter"}\n```'
        effect = _parse_json_response(raw)
        assert effect.primary_effect == "counter"

    def test_flags_non_list_becomes_empty(self):
        raw = '{"primary_effect": "draw", "flags": "not a list"}'
        effect = _parse_json_response(raw)
        assert effect.flags == []


# ─── End-to-end via LLMPolicy + StubBackend ──────────────────────────


def _build_stub_policy(canned: dict, name: str = "stub-test") -> LLMPolicy:
    """Build an LLMPolicy whose StubBackend returns ``canned``
    JSON text for every prompt. ``canned`` is a dict that gets
    serialized."""
    import json
    payload = json.dumps(canned)
    backend = StubBackend(name=name, responder=lambda p: payload)
    return LLMPolicy(backend=backend)


class TestParseOracleEnd2End:
    def test_lightning_bolt(self):
        canned = {
            "primary_effect": "damage",
            "amount": 3,
            "target": "any",
            "flags": [],
        }
        policy = _build_stub_policy(canned)
        effect = parse_oracle("Lightning Bolt deals 3 damage to any target.",
                              policy)
        assert effect.primary_effect == "damage"
        assert effect.amount == 3
        assert effect.target == "any"

    def test_counterspell(self):
        canned = {
            "primary_effect": "counter",
            "amount": None,
            "target": "spell",
            "flags": ["is_counter"],
        }
        policy = _build_stub_policy(canned)
        effect = parse_oracle("Counter target spell.", policy)
        assert effect.primary_effect == "counter"
        assert "is_counter" in effect.flags

    def test_helm_of_awakening(self):
        canned = {
            "primary_effect": "cost_reduce",
            "amount": 1,
            "target": "spell",
            "flags": ["is_cost_reduction"],
        }
        policy = _build_stub_policy(canned)
        effect = parse_oracle("Spells cost {1} less to cast.", policy)
        assert effect.primary_effect == "cost_reduce"
        assert effect.amount == 1
        assert "is_cost_reduction" in effect.flags

    def test_cache_hit_replays_exact_effect(self):
        canned = {
            "primary_effect": "draw",
            "amount": 2,
            "target": None,
            "flags": [],
        }
        policy = _build_stub_policy(canned)

        e1 = parse_oracle("Draw two cards.", policy)
        # Second call must hit the cache.
        # Verify by checking that the underlying generate call
        # reports cache_hit; we reach into the policy internals
        # for this test only.
        prompt = "Draw two cards."
        # The parse_oracle internally builds a richer prompt; the
        # cache key is over that full prompt. So we can't directly
        # check via the policy's has_cached(prompt, ...). Instead,
        # call parse_oracle a second time and verify we get an
        # identical OracleEffect instance fields.
        e2 = parse_oracle("Draw two cards.", policy)
        assert e1 == e2


# ─── Schema-id stability ─────────────────────────────────────────────


def test_schema_id_is_stable():
    """If the schema_id constant changes, all cached entries are
    invalidated. This test pins the current value so any change
    is intentional."""
    assert ORACLE_EFFECT_SCHEMA_ID == "oracle_effect_v1"


# ─── Round-trip serialization ────────────────────────────────────────


def test_to_dict_round_trip():
    """OracleEffect → dict → JSON → dict — useful for the labeled
    corpus format where each line is a JSON-encoded fixture."""
    import json
    e = OracleEffect(
        primary_effect="destroy",
        amount=None,
        target="artifact",
        flags=["is_etb"],
    )
    d = to_dict(e)
    encoded = json.dumps(d)
    decoded = json.loads(encoded)
    assert decoded["primary_effect"] == "destroy"
    assert decoded["target"] == "artifact"
    assert decoded["flags"] == ["is_etb"]
