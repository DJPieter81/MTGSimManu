"""Phase-2 commit-1: dispatcher OFF must not change behavior.

The dispatcher is added to `_score_spell` BEFORE the legacy projection.
When `OUTCOME_DIST_COMBO == False`, the dispatcher branch is dead and
spell scoring must be byte-equal to the pre-Phase-2 behavior.

We assert this in two ways:

1. The flag is `False` at module-load time.
2. `score_spell_via_outcome` (the existing stub) returns `None` so
   the legacy projection runs, which is the same path covered by the
   411-test baseline.
"""
from __future__ import annotations

import pytest

import ai.outcome_ev as outcome_ev


def test_outcome_dist_combo_flag_default_off():
    """Commit 1 must ship with the flag OFF. Commit 2 flips it ON."""
    assert outcome_ev.OUTCOME_DIST_COMBO is False, (
        "Phase-2 commit-1 ships flag OFF for zero behaviour change. "
        "If you flipped this to True, you are on the commit-2 step — "
        "delete this assertion or move to the live behaviour test."
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
