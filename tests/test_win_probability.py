"""Tests for ai.win_probability — calibrated P(I win game) ∈ [0,1].

Option-C: failing-first. These exercise the runtime API
`p_win(snap, my_arch, opp_arch)` documented in Phase 0.
"""
from __future__ import annotations

import random

import pytest

from ai import win_probability as wp
from ai.ev_evaluator import EVSnapshot


def _make_snap(**kw) -> EVSnapshot:
    """Build a minimal EVSnapshot, allowing keyword overrides."""
    base = dict(
        my_life=20, opp_life=20,
        my_hand_size=7, opp_hand_size=7,
        my_mana=0, opp_mana=0,
        my_total_lands=0, opp_total_lands=0,
        turn_number=1,
        my_gy_creatures=0, opp_gy_creatures=0,
        my_power=0, opp_power=0,
    )
    base.update(kw)
    return EVSnapshot(**base)


def test_extreme_my_life_zero():
    snap = _make_snap(my_life=0)
    p = wp.p_win(snap, "Boros Energy", "Affinity")
    assert 0.0 <= p < 0.05, f"my_life=0 should be near-loss; got {p:.3f}"


def test_extreme_opp_life_zero():
    snap = _make_snap(opp_life=0)
    p = wp.p_win(snap, "Boros Energy", "Affinity")
    assert 0.95 < p <= 1.0, f"opp_life=0 should be near-win; got {p:.3f}"


def test_symmetric_state_near_half():
    """Symmetric state at turn 1 with same archetype should be near 0.5."""
    snap = _make_snap()
    p = wp.p_win(snap, "Boros Energy", "Boros Energy")
    assert 0.4 <= p <= 0.6, (
        f"Symmetric mirror should be near 0.5; got {p:.3f}"
    )


def test_returns_unit_interval():
    """100 random snapshots all lie strictly inside (0, 1)."""
    rng = random.Random(20260425)
    archs = [
        "Boros Energy", "Affinity", "Ruby Storm", "Dimir Midrange",
        "Eldrazi Tron", "Living End", "Jeskai Blink", "Domain Zoo",
    ]
    for _ in range(100):
        snap = _make_snap(
            my_life=rng.randint(1, 30),
            opp_life=rng.randint(1, 30),
            my_hand_size=rng.randint(0, 10),
            opp_hand_size=rng.randint(0, 10),
            my_mana=rng.randint(0, 10),
            opp_mana=rng.randint(0, 10),
            my_total_lands=rng.randint(0, 10),
            opp_total_lands=rng.randint(0, 10),
            turn_number=rng.randint(1, 15),
            my_gy_creatures=rng.randint(0, 6),
            opp_gy_creatures=rng.randint(0, 6),
            my_power=rng.randint(0, 12),
            opp_power=rng.randint(0, 12),
        )
        p = wp.p_win(snap, rng.choice(archs), rng.choice(archs))
        assert 0.0 < p < 1.0, f"p_win must be in (0,1); got {p}"


def test_coeffs_loaded_from_json():
    """Importing the module loads coefficients without crashing."""
    # Module should expose the loaded artifact for inspection
    assert hasattr(wp, "_COEFFS")
    assert wp._COEFFS is not None
    assert "feature_names" in wp._COEFFS
    assert "coeffs" in wp._COEFFS
    assert "intercept" in wp._COEFFS
