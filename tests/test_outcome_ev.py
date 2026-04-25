"""Tests for the OutcomeDistribution framework (Phase 1).

Option C — these tests are written first; Phase 1 only ships the
framework module + helpers. No callers wired in yet.
"""
from __future__ import annotations

import pytest

from ai.outcome_ev import (
    Outcome,
    OutcomeDistribution,
    score_spell_via_outcome,
)


def test_outcome_enum_has_5_active_plus_2_reserved():
    """Five active outcome categories + 2 reserved slots = 7 total."""
    members = list(Outcome)
    assert len(members) == 7

    # Five active outcomes are present
    active_names = {
        "COMPLETE_COMBO",
        "PARTIAL_ADVANCE",
        "FIZZLE",
        "DISRUPTED",
        "NEUTRAL",
    }
    actual = {m.name for m in members}
    assert active_names.issubset(actual)

    # Two reserved slots are present
    reserved = {"RESERVED_6", "RESERVED_7"}
    assert reserved.issubset(actual)


def test_dist_expected_value_zero_default():
    """Empty distribution returns EV 0.0 (all probs and values 0)."""
    d = OutcomeDistribution()
    assert d.expected_value() == 0.0


def test_dist_normalizes_to_unit():
    """Non-zero probs normalize so the new sum equals 1.0 within 1e-9."""
    d = OutcomeDistribution(
        probabilities={
            Outcome.COMPLETE_COMBO: 0.2,
            Outcome.FIZZLE: 0.1,
            Outcome.NEUTRAL: 0.1,
        },
        values={Outcome.COMPLETE_COMBO: 1.0},
    )
    n = d.normalize()
    total = sum(n.probabilities.values())
    assert abs(total - 1.0) < 1e-9
    # Original distribution untouched
    assert abs(sum(d.probabilities.values()) - 0.4) < 1e-9


def test_dist_normalize_zero_returns_neutral_one():
    """All-zero probabilities normalise to NEUTRAL=1.0."""
    d = OutcomeDistribution()
    n = d.normalize()
    assert n.probabilities[Outcome.NEUTRAL] == 1.0
    # Every other outcome remains zero
    for o in Outcome:
        if o is not Outcome.NEUTRAL:
            assert n.probabilities[o] == 0.0


def test_expected_value_linear_in_value():
    """Doubling all outcome values doubles the expected value."""
    probs = {
        Outcome.COMPLETE_COMBO: 0.5,
        Outcome.FIZZLE: 0.5,
    }
    values_a = {
        Outcome.COMPLETE_COMBO: 1.0,
        Outcome.FIZZLE: -0.5,
    }
    values_b = {k: 2.0 * v for k, v in values_a.items()}

    a = OutcomeDistribution(probabilities=dict(probs), values=values_a)
    b = OutcomeDistribution(probabilities=dict(probs), values=values_b)

    assert abs(b.expected_value() - 2.0 * a.expected_value()) < 1e-12


def test_two_outcome_complement():
    """{COMPLETE=0.6, FIZZLE=0.4, value=1.0/-1.0} → EV = 0.6 - 0.4 = 0.2."""
    d = OutcomeDistribution(
        probabilities={
            Outcome.COMPLETE_COMBO: 0.6,
            Outcome.FIZZLE: 0.4,
        },
        values={
            Outcome.COMPLETE_COMBO: 1.0,
            Outcome.FIZZLE: -1.0,
        },
    )
    assert abs(d.expected_value() - 0.2) < 1e-12


def test_is_well_formed_true_for_normalized():
    """A normalised distribution reports is_well_formed() True."""
    d = OutcomeDistribution(
        probabilities={
            Outcome.COMPLETE_COMBO: 0.3,
            Outcome.PARTIAL_ADVANCE: 0.2,
            Outcome.FIZZLE: 0.1,
            Outcome.DISRUPTED: 0.1,
            Outcome.NEUTRAL: 0.3,
        },
    )
    assert d.is_well_formed()


def test_is_well_formed_false_for_negative_prob():
    """A distribution with a negative probability fails is_well_formed."""
    d = OutcomeDistribution(
        probabilities={
            Outcome.COMPLETE_COMBO: -0.1,
            Outcome.NEUTRAL: 1.1,
        },
    )
    assert not d.is_well_formed()


def test_dispatcher_returns_none_in_phase_1():
    """Phase-1 dispatcher returns None for any input (no migrations yet)."""
    # Pass placeholders — Phase 1 stub ignores them and returns None.
    result = score_spell_via_outcome(
        card=None,
        snap=None,
        game=None,
        me=None,
        opp=None,
        bhi=None,
        archetype=None,
        profile=None,
    )
    assert result is None
