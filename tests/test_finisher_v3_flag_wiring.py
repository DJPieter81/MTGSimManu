"""Failing-first tests for `MTGSIM_USE_FINISHER_V3` opt-in flag
(sim v3 PR3).

The flag predicate ``_use_finisher_v3()`` mirrors the MCTS pattern
at ``ai/ev_player.py:158``. Default OFF — production behaviour
unchanged unless the user opts in.

Tests pin:
  - Predicate semantics (env var off-tokens recognized)
  - Routing: flag off → v2 simulator called; flag on → v3 called
  - Default behaviour unchanged (env unset → v2)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────
# Predicate semantics
# ──────────────────────────────────────────────────────────────────

def test_use_finisher_v3_returns_false_when_env_unset(monkeypatch):
    """Env var unset → predicate False (default OFF)."""
    monkeypatch.delenv("MTGSIM_USE_FINISHER_V3", raising=False)
    from ai.combo_evaluator import _use_finisher_v3
    assert _use_finisher_v3() is False


def test_use_finisher_v3_returns_true_when_env_truthy(monkeypatch):
    """Truthy env var → predicate True."""
    monkeypatch.setenv("MTGSIM_USE_FINISHER_V3", "1")
    from ai.combo_evaluator import _use_finisher_v3
    assert _use_finisher_v3() is True


@pytest.mark.parametrize("off_token", ["", "0", "false", "no", "off", "FALSE", "Off"])
def test_use_finisher_v3_recognises_off_tokens(monkeypatch, off_token):
    """All off-tokens map to False, case-insensitive."""
    monkeypatch.setenv("MTGSIM_USE_FINISHER_V3", off_token)
    from ai.combo_evaluator import _use_finisher_v3
    assert _use_finisher_v3() is False, (
        f"Off-token {off_token!r} should map to False"
    )


@pytest.mark.parametrize("on_token", ["1", "true", "yes", "on", "True", "ON"])
def test_use_finisher_v3_recognises_truthy_tokens(monkeypatch, on_token):
    """All non-off tokens map to True."""
    monkeypatch.setenv("MTGSIM_USE_FINISHER_V3", on_token)
    from ai.combo_evaluator import _use_finisher_v3
    assert _use_finisher_v3() is True, (
        f"On-token {on_token!r} should map to True"
    )


# ──────────────────────────────────────────────────────────────────
# Routing — flag off → v2, flag on → v3
# ──────────────────────────────────────────────────────────────────

def test_project_baseline_routes_to_v2_when_flag_off(monkeypatch):
    """With the flag off (default), `_project_baseline` calls the
    v2 simulator. v3 must NOT be called."""
    monkeypatch.delenv("MTGSIM_USE_FINISHER_V3", raising=False)

    # Patch BOTH simulators to detect call-counts.
    with patch("ai.finisher_simulator.simulate_finisher_chain") as v2_mock, \
         patch("ai.finisher_simulator_v3.simulate_finisher_chain_v3") as v3_mock:
        # v2 returns a minimal-but-valid FinisherProjection.
        from ai.schemas import FinisherProjection
        v2_mock.return_value = FinisherProjection(pattern="none")

        from ai.combo_evaluator import _project_baseline
        _project_baseline(
            snap=None, hand=[], battlefield=[], graveyard=[],
            library_size=50, storm_count=0, archetype="combo",
            sideboard=[], library=[],
        )

        assert v2_mock.call_count == 1, (
            f"v2 simulator must be called when flag is off; "
            f"got v2.calls={v2_mock.call_count}, v3.calls={v3_mock.call_count}"
        )
        assert v3_mock.call_count == 0, "v3 must NOT be called when flag off"


def test_project_baseline_routes_to_v3_when_flag_on(monkeypatch):
    """With the flag on, `_project_baseline` calls v3 instead."""
    monkeypatch.setenv("MTGSIM_USE_FINISHER_V3", "1")

    with patch("ai.finisher_simulator.simulate_finisher_chain") as v2_mock, \
         patch("ai.finisher_simulator_v3.simulate_finisher_chain_v3") as v3_mock:
        # v3 must return a wire-compatible FinisherProjectionV3 so
        # the chain_card_ids extraction below doesn't crash.
        from ai.finisher_simulator_v3 import FinisherProjectionV3
        v3_mock.return_value = FinisherProjectionV3(pattern="none")

        from ai.combo_evaluator import _project_baseline
        _project_baseline(
            snap=None, hand=[], battlefield=[], graveyard=[],
            library_size=50, storm_count=0, archetype="combo",
            sideboard=[], library=[],
        )

        assert v3_mock.call_count == 1, (
            f"v3 simulator must be called when flag is on; "
            f"got v2.calls={v2_mock.call_count}, v3.calls={v3_mock.call_count}"
        )
        assert v2_mock.call_count == 0, "v2 must NOT be called when flag on"
