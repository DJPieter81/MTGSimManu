"""Tests for the env-gated diagnostic trace in `ai/combo_evaluator`.

Per docs/PHASE_D_FOURTH_ATTEMPT.md, the live wire-up has collapsed
Storm five times.  The trace exposes per-card branch decisions so
the second gap can be located before a sixth wire-up attempt.

The trace flag is read at module import time from
`MTGSIM_COMBO_TRACE`.  These tests verify:

1. Default off → no stderr output, no behavior change.
2. When patched on → each return point in `card_combo_evaluation`
   emits a `COMBO_TRACE` line.

We patch the module-level `_TRACE` flag rather than restarting the
interpreter for each test — env-driven module-load behavior is
already verified by reading the flag at import.
"""
from __future__ import annotations

import os
import sys
from io import StringIO

import pytest


def _make_storm_hand_with_sb_closer():
    """Reusable fixture: tutor-only Storm hand with SB Grapeshot.
    This is the Wish→SB scenario the simulator now projects damage
    for (per commits 08c4e11, da16698, 30c7a18)."""
    from tests.test_finisher_simulator import _ritual, _tutor, _grapeshot
    return [_ritual(1), _ritual(2), _tutor(3)], [_grapeshot(99)]


def _make_minimal_game(hand, sb, library_size=40):
    """Minimal `me` + `game` namespaces for `card_combo_evaluation`."""
    from types import SimpleNamespace
    from ai.ev_evaluator import EVSnapshot

    snap = EVSnapshot(
        my_life=20, opp_life=20, my_mana=6, opp_mana=0,
        my_total_lands=6, opp_total_lands=0,
        my_hand_size=4, opp_hand_size=4,
        turn_number=4, storm_count=0,
    )
    me = SimpleNamespace(
        hand=hand, sideboard=sb,
        library=list(range(library_size)),  # dummy non-empty
        battlefield=[], graveyard=[],
        spells_cast_this_turn=0,
    )
    opp = SimpleNamespace(battlefield=[])
    game = SimpleNamespace(players=[me, opp])
    return snap, me, game


class TestTraceDefaultOff:
    """When MTGSIM_COMBO_TRACE is unset (default), the evaluator
    must not emit any stderr output.  Critical for production
    runs — instrumentation must have zero overhead by default."""

    def test_no_stderr_output_when_trace_off(self, capsys):
        """Score a card with the trace flag NOT patched on.
        capsys captures stderr; we assert it's empty."""
        from ai.combo_evaluator import (
            card_combo_evaluation, _BASELINE_CACHE)
        _BASELINE_CACHE.clear()

        hand, sb = _make_storm_hand_with_sb_closer()
        snap, me, game = _make_minimal_game(hand, sb)

        # Verify trace is off (default)
        import ai.combo_evaluator as ce
        assert ce._TRACE is False, \
            "test environment must not have MTGSIM_COMBO_TRACE set"

        card_combo_evaluation(hand[0], snap, me, game, 0,
                              archetype="storm")

        captured = capsys.readouterr()
        assert "COMBO_TRACE" not in captured.err
        assert "COMBO_TRACE" not in captured.out


class TestTraceOnEmitsBranchDecisions:
    """When _TRACE is patched on, each return-point in
    `card_combo_evaluation` emits a structured COMBO_TRACE line.
    This is what enables diagnostic-led wire-up attempts."""

    def test_chain_credit_branch_emits_line(self, capsys, monkeypatch):
        """Tutor-only hand with SB closer → 'chain_credit' branch
        fires.  Trace line should contain branch name + score."""
        import ai.combo_evaluator as ce
        monkeypatch.setattr(ce, "_TRACE", True)
        ce._BASELINE_CACHE.clear()

        hand, sb = _make_storm_hand_with_sb_closer()
        snap, me, game = _make_minimal_game(hand, sb)

        ce.card_combo_evaluation(hand[0], snap, me, game, 0,
                                  archetype="storm")

        captured = capsys.readouterr()
        assert "COMBO_TRACE" in captured.err
        assert "branch=chain_credit" in captured.err
        # Verify the structured fields we documented
        assert "card=" in captured.err
        assert "score=" in captured.err

    def test_zero_fire_value_emits_chain_credit_branch(self, capsys, monkeypatch):
        """A chain-fuel card in a hand WITH a ritual (so pattern
        is 'storm') but no closer projects expected_damage=0 →
        the 'chain_credit' branch fires with fire_value=0.

        This is the EMPIRICAL finding the trace surfaces: even
        though the chain isn't lethal, pattern detection succeeds
        via the ritual's presence, so we don't hit hard_hold.
        Surfacing this explicitly validates the trace's value —
        we'd have spent another wire-up attempt guessing wrong."""
        import ai.combo_evaluator as ce
        monkeypatch.setattr(ce, "_TRACE", True)
        ce._BASELINE_CACHE.clear()

        from tests.test_finisher_simulator import _ritual
        hand = [_ritual(1)]
        snap, me, game = _make_minimal_game(hand, sb=[], library_size=40)

        ce.card_combo_evaluation(hand[0], snap, me, game, 0,
                                  archetype="storm")

        captured = capsys.readouterr()
        assert "COMBO_TRACE" in captured.err
        # Storm pattern detected (ritual present), but
        # expected_damage = 0 (no closer)
        assert "branch=chain_credit" in captured.err
        assert "fire_value=0.0" in captured.err
        assert "score=0.00" in captured.err

    def test_truly_empty_hand_emits_no_chain_non_fuel(self, capsys, monkeypatch):
        """A non-fuel card (a creature) in a hand with NO chain
        pattern detected → 'no_chain_non_fuel' branch fires."""
        import ai.combo_evaluator as ce
        monkeypatch.setattr(ce, "_TRACE", True)
        ce._BASELINE_CACHE.clear()

        # Creature card — not chain fuel
        from tests.test_finisher_simulator import MockCard, MockTemplate
        creature = MockCard(
            template=MockTemplate(name="Bear", cmc=2, is_creature=True,
                                   power=2, toughness=2),
            instance_id=1,
        )
        hand = [creature]
        snap, me, game = _make_minimal_game(hand, sb=[], library_size=40)

        ce.card_combo_evaluation(creature, snap, me, game, 0,
                                  archetype="storm")

        captured = capsys.readouterr()
        assert "COMBO_TRACE" in captured.err
        assert "branch=no_chain_non_fuel" in captured.err

    def test_trace_format_has_required_fields(self, capsys, monkeypatch):
        """Validate the COMBO_TRACE line format: starts with the
        marker, has branch=X, card=Y, score=Z + per-branch fields."""
        import ai.combo_evaluator as ce
        monkeypatch.setattr(ce, "_TRACE", True)
        ce._BASELINE_CACHE.clear()

        hand, sb = _make_storm_hand_with_sb_closer()
        snap, me, game = _make_minimal_game(hand, sb)

        ce.card_combo_evaluation(hand[0], snap, me, game, 0,
                                  archetype="storm")

        captured = capsys.readouterr()
        lines = [l for l in captured.err.splitlines() if "COMBO_TRACE" in l]
        assert len(lines) == 1
        line = lines[0]
        # Required prefix and core fields
        assert line.startswith("COMBO_TRACE ")
        assert "branch=" in line
        assert "card=" in line
        assert "score=" in line
