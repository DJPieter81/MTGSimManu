"""Phase 4D — MULLIGAN_ADVISOR=slm dispatch tests.

Verifies ai/mulligan.py routes through the SLM mulligan advisor
when MULLIGAN_ADVISOR=slm is set, falls back to heuristic on
backend unavailability, and stores reasoning into last_reason.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import sys
import types

import pytest

# Patch import path so ai.mulligan can be imported alone.
import ai.mulligan as mulligan_module
from ai.llm.policy import BackendUnavailable, LLMPolicy, StubBackend


class _StubArchetype:
    """Minimal stub for ArchetypeStrategy with .name."""
    name = "Test Deck"
    role = None
    archetype = None


class _StubCard:
    """Minimal stub for CardInstance with .name and .template."""
    def __init__(self, name, is_land=False):
        self.name = name
        self.template = types.SimpleNamespace(
            name=name, is_land=is_land, supertypes=[], cmc=1,
        )


def _make_decider():
    return mulligan_module.MulliganDecider(
        archetype=_StubArchetype(),
    )


# ─── Default behavior unchanged ──────────────────────────────────────


def test_default_uses_heuristic(monkeypatch):
    """Without MULLIGAN_ADVISOR=slm, decide() goes straight to the
    heuristic — no SLM import attempt."""
    monkeypatch.delenv("MULLIGAN_ADVISOR", raising=False)
    decider = _make_decider()
    # Patch _try_slm_decide so we can verify it's NOT called.
    called = []
    monkeypatch.setattr(
        decider, "_try_slm_decide",
        lambda h, c: (called.append(1), None)[-1],
    )
    # Heuristic path with a totally vanilla hand — we don't care
    # what it returns, only that _try_slm_decide wasn't invoked.
    hand = [_StubCard("Mountain", is_land=True)] * 7
    decider.decide(hand, cards_in_hand=7)
    assert not called, (
        "Default path must NOT invoke the SLM advisor."
    )


# ─── SLM dispatch routing ────────────────────────────────────────────


def test_slm_dispatch_used_when_env_set(monkeypatch):
    """With MULLIGAN_ADVISOR=slm, decide() routes through
    _try_slm_decide first."""
    monkeypatch.setenv("MULLIGAN_ADVISOR", "slm")
    decider = _make_decider()
    monkeypatch.setattr(
        decider, "_try_slm_decide", lambda h, c: True,
    )
    hand = [_StubCard("Lightning Bolt")] * 7
    result = decider.decide(hand, cards_in_hand=7)
    assert result is True


def test_slm_none_falls_back_to_heuristic(monkeypatch):
    """When _try_slm_decide returns None (backend unavailable),
    the heuristic runs."""
    monkeypatch.setenv("MULLIGAN_ADVISOR", "slm")
    decider = _make_decider()
    monkeypatch.setattr(
        decider, "_try_slm_decide", lambda h, c: None,
    )
    monkeypatch.setattr(
        decider, "_heuristic_decide",
        lambda h, c: "heuristic_called",
    )
    hand = [_StubCard("Lightning Bolt")] * 7
    result = decider.decide(hand, cards_in_hand=7)
    assert result == "heuristic_called"


# ─── _try_slm_decide returns None on backend errors ──────────────────


def test_try_slm_decide_returns_none_when_no_model_path(monkeypatch):
    """LlamaCppBackend raises BackendUnavailable when no model_path
    is set; _try_slm_decide must catch and return None."""
    monkeypatch.delenv("MTG_LLM_MODEL_PATH", raising=False)
    decider = _make_decider()
    hand = [_StubCard("Lightning Bolt")] * 7
    result = decider._try_slm_decide(hand, cards_in_hand=7)
    assert result is None


# ─── _try_slm_decide stores reasoning into last_reason ───────────────


def test_try_slm_decide_stores_reason(monkeypatch):
    """When the SLM returns a decision, _try_slm_decide must
    populate last_reason with [slm] tag + the model's reasoning."""
    decider = _make_decider()

    # Mock the entire SLM call chain via a patched import inside
    # _try_slm_decide. The cleanest way is to inject a fake module.
    fake_decision = types.SimpleNamespace(
        keep=True, confidence=0.92,
        reasoning="lands + threats look great",
    )

    def _fake_advise(*args, **kwargs):
        return fake_decision

    fake_module = types.ModuleType("ai.llm.mulligan_advisor")
    fake_module.advise_mulligan = _fake_advise

    # Also patch LlamaCppBackend so it doesn't try to load a model.
    fake_backend_module = types.ModuleType("ai.llm.llama_cpp_backend")

    class _FakeBackend:
        name = "fake"

        def generate(self, prompt, max_tokens=256):
            return "{}"
    fake_backend_module.LlamaCppBackend = _FakeBackend

    monkeypatch.setitem(
        sys.modules, "ai.llm.mulligan_advisor", fake_module,
    )
    monkeypatch.setitem(
        sys.modules, "ai.llm.llama_cpp_backend", fake_backend_module,
    )

    hand = [_StubCard("X")] * 7
    result = decider._try_slm_decide(hand, cards_in_hand=7)
    assert result is True
    assert "[slm]" in decider.last_reason
    assert "great" in decider.last_reason
    assert "0.92" in decider.last_reason


def test_try_slm_decide_handles_advisor_exception(monkeypatch):
    """If the SLM advisor raises any exception, _try_slm_decide
    returns None and prints a fallback note to stderr."""
    decider = _make_decider()

    fake_module = types.ModuleType("ai.llm.mulligan_advisor")
    def _raise(*a, **k):
        raise RuntimeError("synthetic failure")
    fake_module.advise_mulligan = _raise

    fake_backend_module = types.ModuleType("ai.llm.llama_cpp_backend")

    class _FakeBackend:
        name = "fake"

        def generate(self, prompt, max_tokens=256):
            return "{}"
    fake_backend_module.LlamaCppBackend = _FakeBackend

    monkeypatch.setitem(
        sys.modules, "ai.llm.mulligan_advisor", fake_module,
    )
    monkeypatch.setitem(
        sys.modules, "ai.llm.llama_cpp_backend", fake_backend_module,
    )

    hand = [_StubCard("X")] * 7
    result = decider._try_slm_decide(hand, cards_in_hand=7)
    assert result is None
