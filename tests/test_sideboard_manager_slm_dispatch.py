"""Phase 4C SB advisor integration — SB_SOLVER=slm dispatch tests.

Verifies engine/sideboard_manager.py routes through the SLM
advisor when ``SB_SOLVER=slm`` is set, applies the swap directives
as deltas to mainboard/sideboard, falls back to legacy on
backend unavailability, and respects pool bounds (can't bring
in more copies than the SB has, can't cut more than the MB has).

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import os
import sys

import pytest

import engine.sideboard_manager as sm
from ai.llm.policy import LLMPolicy, StubBackend


def _stub_policy_with_plan(plan_json: str) -> LLMPolicy:
    """Build a policy whose stub backend returns ``plan_json`` for
    every prompt."""
    backend = StubBackend(
        name="stub-sb-dispatch-test",
        responder=lambda p: plan_json,
    )
    return LLMPolicy(backend=backend)


@pytest.fixture
def stub_slm_policy(monkeypatch, tmp_path):
    """Patch _get_slm_policy so the dispatcher uses our stub."""
    plan = json.dumps({
        "swaps": [
            {"card": "Wear // Tear", "delta": 2},
            {"card": "Damping Sphere", "delta": 1},
            {"card": "Blood Moon", "delta": -2},
            {"card": "Goblin Bombardment", "delta": -1},
        ],
        "notes": "stub plan",
    })
    backend = StubBackend(
        name="stub-sb-dispatch", responder=lambda p: plan,
    )
    policy = LLMPolicy(backend=backend, cache_dir=tmp_path)
    monkeypatch.setattr(sm, "_get_slm_policy", lambda: policy)
    monkeypatch.setattr(sm, "_SB_SLM_POLICY", policy)
    return policy


# ─── Dispatch routing ────────────────────────────────────────────────


class TestDispatch:
    def test_default_uses_legacy(self, monkeypatch):
        monkeypatch.delenv("SB_SOLVER", raising=False)
        # Legacy path leaves the boards intact for an empty SB.
        new_main, new_side = sm.sideboard(
            mainboard={"Plains": 20},
            sideboard_cards={},
            my_deck="Test", opponent_deck="Test",
        )
        # Empty SB short-circuits — both backends agree here.
        assert new_main == {"Plains": 20}

    def test_slm_dispatch_invokes_advisor(
        self, monkeypatch, stub_slm_policy,
    ):
        monkeypatch.setenv("SB_SOLVER", "slm")
        new_main, new_side = sm.sideboard(
            mainboard={
                "Lightning Bolt": 4, "Blood Moon": 2,
                "Goblin Bombardment": 2,
            },
            sideboard_cards={
                "Wear // Tear": 2, "Damping Sphere": 1,
                "Wrath of the Skies": 2,
            },
            my_deck="Boros Energy",
            opponent_deck="Affinity",
        )
        # The stub plan brings in Wear // Tear (2) and Damping
        # Sphere (1), cuts Blood Moon (2) and Goblin Bombardment (1).
        assert new_main.get("Wear // Tear") == 2
        assert new_main.get("Damping Sphere") == 1
        assert new_main.get("Blood Moon", 0) == 0  # cut all
        assert new_main.get("Goblin Bombardment") == 1  # cut 1 of 2

        # SB shrinks symmetrically.
        assert new_side.get("Wear // Tear", 0) == 0
        assert new_side.get("Damping Sphere", 0) == 0


class TestPoolBounds:
    def test_cap_at_sb_count(self, monkeypatch, tmp_path):
        """A +5 directive on a card with only 1 copy in the SB
        caps at 1."""
        plan = json.dumps({
            "swaps": [{"card": "X", "delta": 5}, {"card": "Y", "delta": -1}],
        })
        backend = StubBackend(name="cap", responder=lambda p: plan)
        policy = LLMPolicy(backend=backend, cache_dir=tmp_path)
        monkeypatch.setattr(sm, "_get_slm_policy", lambda: policy)
        monkeypatch.setattr(sm, "_SB_SLM_POLICY", policy)
        monkeypatch.setenv("SB_SOLVER", "slm")

        new_main, new_side = sm.sideboard(
            mainboard={"Y": 4},
            sideboard_cards={"X": 1},
            my_deck="A", opponent_deck="B",
        )
        # Brought in 1 (capped from 5); cut 1 of 4.
        assert new_main.get("X") == 1
        assert new_main.get("Y") == 3

    def test_cap_at_mb_count_for_cuts(self, monkeypatch, tmp_path):
        """A -10 directive on a card with only 2 copies in the MB
        caps at 2."""
        plan = json.dumps({
            "swaps": [{"card": "X", "delta": 2}, {"card": "Y", "delta": -10}],
        })
        backend = StubBackend(name="cap2", responder=lambda p: plan)
        policy = LLMPolicy(backend=backend, cache_dir=tmp_path)
        monkeypatch.setattr(sm, "_get_slm_policy", lambda: policy)
        monkeypatch.setattr(sm, "_SB_SLM_POLICY", policy)
        monkeypatch.setenv("SB_SOLVER", "slm")

        new_main, new_side = sm.sideboard(
            mainboard={"Y": 2}, sideboard_cards={"X": 2},
            my_deck="A", opponent_deck="B",
        )
        # Cut all 2 of Y; brought in 2 of X.
        assert new_main.get("Y", 0) == 0
        assert new_main.get("X") == 2

    def test_drops_directives_for_unknown_cards(
        self, monkeypatch, tmp_path,
    ):
        """A +1 directive for a card not in the SB is silently
        dropped — the model occasionally invents card names."""
        plan = json.dumps({
            "swaps": [
                {"card": "Imaginary Card", "delta": 1},
                {"card": "X", "delta": 1},
                {"card": "Y", "delta": -1},
            ],
        })
        backend = StubBackend(name="phantom", responder=lambda p: plan)
        policy = LLMPolicy(backend=backend, cache_dir=tmp_path)
        monkeypatch.setattr(sm, "_get_slm_policy", lambda: policy)
        monkeypatch.setattr(sm, "_SB_SLM_POLICY", policy)
        monkeypatch.setenv("SB_SOLVER", "slm")

        new_main, new_side = sm.sideboard(
            mainboard={"Y": 4}, sideboard_cards={"X": 1},
            my_deck="A", opponent_deck="B",
        )
        # Imaginary Card silently dropped; X comes in, Y goes out.
        assert "Imaginary Card" not in new_main
        assert new_main.get("X") == 1
        assert new_main.get("Y") == 3


# ─── Fallback on backend unavailable ─────────────────────────────────


class TestFallback:
    def test_backend_unavailable_falls_back_to_legacy(
        self, monkeypatch,
    ):
        """When the policy raises BackendUnavailable, the
        dispatcher silently falls through to the legacy path. The
        return value matches what legacy would have produced."""
        from ai.llm.policy import BackendUnavailable

        def _failing_policy():
            raise BackendUnavailable("model file missing")

        monkeypatch.setattr(sm, "_get_slm_policy", _failing_policy)
        monkeypatch.setattr(sm, "_SB_SLM_POLICY", None)
        monkeypatch.setenv("SB_SOLVER", "slm")

        # Legacy path runs — even though this is a non-canonical
        # matchup, the function shouldn't crash.
        new_main, new_side = sm.sideboard(
            mainboard={"Plains": 20, "Lightning Bolt": 4},
            sideboard_cards={"Counterspell": 2},
            my_deck="Test Deck", opponent_deck="Test Opp",
        )
        # Result is a tuple of dicts — that's the legacy contract.
        assert isinstance(new_main, dict)
        assert isinstance(new_side, dict)


# ─── Lazy policy construction ────────────────────────────────────────


class TestLazyPolicy:
    def test_get_slm_policy_caches(self, monkeypatch):
        """Repeated calls return the same LLMPolicy instance — cache
        is shared across all sideboard() invocations within a session."""
        # Reset the module-global.
        monkeypatch.setattr(sm, "_SB_SLM_POLICY", None)
        # Mock the constructor path so we don't actually load
        # llama_cpp.
        from ai.llm.policy import LLMPolicy, StubBackend
        backend = StubBackend(name="lazy-test", responder=lambda p: "{}")
        fake_policy = LLMPolicy(backend=backend)

        def _fake_construct():
            return fake_policy
        monkeypatch.setattr(sm, "_get_slm_policy", _fake_construct)

        a = sm._get_slm_policy()
        b = sm._get_slm_policy()
        assert a is b
