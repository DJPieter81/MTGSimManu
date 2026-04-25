"""Tests for hypergeometric and Bayesian helpers in ai.outcome_ev.

Option C — these tests are written first as part of Phase 1.
"""
from __future__ import annotations

import pytest

from ai.outcome_ev import bayesian_update, p_draw_in_n_turns


def test_known_textbook_value():
    """K=4 in N=60, n=7 → 0.399 ± 0.005.

    Manual: P = 1 - C(56,7)/C(60,7) ≈ 1 - 0.6005 ≈ 0.3995.
    Standard 4-of in a 60-card deck on the opening 7 draws.
    """
    p = p_draw_in_n_turns(library_size=60, target_count=4, n_draws=7)
    assert abs(p - 0.3995) < 0.005


def test_zero_targets_zero_prob():
    """No copies of the target ⇒ probability 0."""
    assert p_draw_in_n_turns(library_size=60, target_count=0, n_draws=7) == 0.0


def test_zero_draws_zero_prob():
    """No draws ⇒ probability 0."""
    assert p_draw_in_n_turns(library_size=60, target_count=4, n_draws=0) == 0.0


def test_full_library_one():
    """Drawing the entire library ⇒ certainty (1.0)."""
    assert p_draw_in_n_turns(library_size=10, target_count=1, n_draws=10) == 1.0


def test_all_targets_one():
    """Every card in the library is a target ⇒ certainty (1.0)."""
    assert p_draw_in_n_turns(library_size=10, target_count=10, n_draws=1) == 1.0


def test_n_draws_exceeds_library_clamps_to_one():
    """Asking for more draws than the library has ⇒ clamped to 1.0."""
    assert p_draw_in_n_turns(library_size=10, target_count=2, n_draws=20) == 1.0


def test_handles_large_library_no_overflow():
    """Large hypergeometric values must not overflow.

    N=10000, K=100, n=50 → small but positive.
    Uses math.comb which is exact for arbitrary integers.
    """
    p = p_draw_in_n_turns(library_size=10000, target_count=100, n_draws=50)
    assert 0.0 < p < 1.0
    # Sanity: ~1 - (9900 choose 50) / (10000 choose 50) ≈ 0.39
    assert 0.2 < p < 0.6


def test_bayesian_update_neutral_evidence():
    """Likelihood ratio of 1 (P(E|T)==P(E|F)) leaves the prior unchanged."""
    assert abs(bayesian_update(0.3, 0.5, 0.5) - 0.3) < 1e-12


def test_bayesian_update_strong_positive_evidence():
    """Strong evidence (LR=9) lifts a 0.5 prior above 0.85."""
    posterior = bayesian_update(0.5, 0.9, 0.1)
    assert posterior > 0.85


def test_bayesian_update_clamps_at_extremes():
    """Priors of 0 and 1 are absorbing states."""
    assert bayesian_update(0.0, 0.9, 0.1) == 0.0
    assert bayesian_update(1.0, 0.9, 0.1) == 1.0
