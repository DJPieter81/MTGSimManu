"""Phase-2b: dispatcher ON, OutcomeDistribution is the source of truth.

After Phase-2b the flag is True — combo spells (ritual / cascade /
reanimate / finisher / combo-tutor) are routed through
`build_combo_distribution`.  The flag is now pinned ON; Phase 3 will
expand the dispatcher to creatures / removal / cantrips.
"""
from __future__ import annotations

import pytest

import ai.outcome_ev as outcome_ev


def test_outcome_dist_combo_flag_on_phase2b():
    """Phase-2b ships with the flag ON. Combo spells go through the
    distribution dispatcher; legacy patience clamps are deleted."""
    assert outcome_ev.OUTCOME_DIST_COMBO is True, (
        "Phase-2b ships flag ON. If you flipped it OFF you have "
        "regressed past the Phase-2b switch — restore True or move "
        "the regression behind a separate feature flag."
    )


def test_score_spell_via_outcome_stub_returns_none():
    """The Phase-1 dispatcher stub still returns None (only the
    builder is wired in commit 1). This pins the public surface."""
    # No real arguments needed — the Phase-1 stub is unconditionally
    # `return None`.  We just verify the function exists and the
    # stub semantics are unchanged so importing it is safe.
    result = outcome_ev.score_spell_via_outcome(
        None, None, None, None, None, None, None, None,
    )
    assert result is None
