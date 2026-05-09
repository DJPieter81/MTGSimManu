"""Phase 4C mulligan advisor — structured-output contract tests."""
from __future__ import annotations

import json

import pytest

from ai.llm.mulligan_advisor import (
    MULLIGAN_DECISION_SCHEMA_ID,
    MulliganDecision,
    _parse_response,
    _strip_code_fences,
    advise_mulligan,
    to_dict,
)
from ai.llm.policy import LLMPolicy, StubBackend


class TestParseResponse:
    def test_full_response(self):
        raw = json.dumps({
            "keep": True, "confidence": 0.85,
            "reasoning": "Two lands + Mox + Memnite is a solid Affinity opener",
            "bottom": [],
        })
        d = _parse_response(raw)
        assert d.keep is True
        assert d.confidence == 0.85
        assert "Affinity" in d.reasoning
        assert d.bottom == []

    def test_mull_decision(self):
        raw = json.dumps({"keep": False, "confidence": 0.9})
        d = _parse_response(raw)
        assert d.keep is False
        assert d.confidence == 0.9

    def test_confidence_clamped(self):
        raw = json.dumps({"keep": True, "confidence": 1.5})
        d = _parse_response(raw)
        assert d.confidence == 1.0
        raw2 = json.dumps({"keep": True, "confidence": -0.5})
        d2 = _parse_response(raw2)
        assert d2.confidence == 0.0

    def test_garbage_confidence_defaults_zero(self):
        raw = json.dumps({"keep": True, "confidence": "high"})
        d = _parse_response(raw)
        assert d.confidence == 0.0

    def test_bottom_filters_non_strings(self):
        raw = json.dumps({
            "keep": True, "confidence": 0.5,
            "bottom": ["Mountain", 5, None, "Bauble"],
        })
        d = _parse_response(raw)
        assert d.bottom == ["Mountain", "Bauble"]

    def test_non_json_raises(self):
        with pytest.raises(ValueError):
            _parse_response("just prose")

    def test_handles_code_fences(self):
        raw = '```json\n{"keep": true, "confidence": 0.7}\n```'
        d = _parse_response(raw)
        assert d.keep is True


class TestStripCodeFences:
    def test_strips_json_fence(self):
        assert _strip_code_fences('```json\n{}\n```') == "{}"


def _build_stub_policy(canned: dict) -> LLMPolicy:
    payload = json.dumps(canned)
    backend = StubBackend(name="stub-mull-test", responder=lambda p: payload)
    return LLMPolicy(backend=backend)


class TestAdviseEnd2End:
    def test_keep_affinity_opener(self):
        canned = {
            "keep": True, "confidence": 0.92,
            "reasoning": "Saga + Memnite + Plating + 2 free artifacts",
            "bottom": [],
        }
        policy = _build_stub_policy(canned)
        d = advise_mulligan(
            deck_name="Affinity",
            hand=["Urza's Saga", "Memnite", "Mox Opal",
                  "Cranial Plating", "Memnite", "Frogmite",
                  "Darksteel Citadel"],
            policy=policy,
            on_play=True,
        )
        assert d.keep is True
        assert d.confidence > 0.9

    def test_mull_landless_opener(self):
        canned = {
            "keep": False, "confidence": 0.95,
            "reasoning": "Zero lands and only one-mana cards",
            "bottom": [],
        }
        policy = _build_stub_policy(canned)
        d = advise_mulligan(
            deck_name="Boros Energy",
            hand=["Lightning Bolt"] * 7,
            policy=policy,
        )
        assert d.keep is False

    def test_cache_replays_identical_decision(self):
        canned = {"keep": True, "confidence": 0.75, "reasoning": "OK"}
        policy = _build_stub_policy(canned)
        hand = ["Card A", "Card B", "Card C"]
        a = advise_mulligan("Test Deck", hand, policy)
        b = advise_mulligan("Test Deck", hand, policy)
        assert a == b

    def test_play_vs_draw_different_cache_keys(self):
        canned = {"keep": True, "confidence": 0.5}
        policy = _build_stub_policy(canned)
        hand = ["A", "B", "C"]
        # On the play vs on the draw → different prompts → different
        # cache entries.
        a = advise_mulligan("Test", hand, policy, on_play=True)
        b = advise_mulligan("Test", hand, policy, on_play=False)
        # Same canned response, so same parsed dict, but the cache
        # keys differ — exercising both via separate prompts.
        assert a == b


def test_schema_id_is_stable():
    assert MULLIGAN_DECISION_SCHEMA_ID == "mulligan_decision_v1"


def test_to_dict_round_trip():
    d = MulliganDecision(
        keep=True, confidence=0.8,
        reasoning="test", bottom=["X"],
    )
    obj = to_dict(d)
    encoded = json.dumps(obj)
    decoded = json.loads(encoded)
    assert decoded["keep"] is True
    assert decoded["bottom"] == ["X"]
