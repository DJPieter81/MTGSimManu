"""The per-game safety budget must measure the game's own work, not the clock.

`engine/game_runner` arms a safety valve so a runaway game cannot hang the
simulator. It used to be a WALL-CLOCK deadline (`monotonic() + GAME_TIMEOUT_
SECONDS`), which makes a seeded game's outcome a function of machine load: a
1.7 s game on an idle box takes >8 s under three parallel workers or a loaded
CI runner, is cut off at a loop head, and is recorded as a draw. Observed
twice: the anchor flipped a winner between local and CI on identical code
(`083393b`), and a whole `--field` run of Ruby Storm came back 0.0% (every
game "drawn at turn 6") on a contended container while the same seeds play
to normal finishes on a quiet one (2026-09-06).

Rules these tests name:

  1. A seeded outcome is invariant to wall-clock time. The budget is CPU
     time (`time.process_time`): a starved process accrues none, a spinning
     one accrues it, so the valve keeps catching hangs and stops reacting to
     load.
  2. An exhausted budget is reported as an ABORT, distinct from a CR 104.4
     draw, so aggregators can refuse to treat it as a game result.
  3. The budget is read from `ai.constants` when the game is armed, which is
     the contract the WR anchor's neutralisation relies on.
  4. Every engine loop head reads ONE budget through `engine.game_budget`;
     the activation gate has no private deadline of its own.

Card/deck names below are fixture carriers for a short, deterministic game,
nothing more.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tests._card_db_cache import shared_card_database

_D1, _D2, _SEED = "Amulet Titan", "Jeskai Blink", 50000  # ~0.2 s idle, ends T8


@pytest.fixture(scope="module")
def runner():
    from engine.game_runner import GameRunner
    return GameRunner(shared_card_database())


def _play(runner):
    from run_meta import _run_pair
    r = _run_pair(runner, _D1, _D2, seed=_SEED, bo1=True)
    return (r.winner_deck, r.turns, r.win_condition)


def test_seeded_outcome_is_unchanged_when_wall_clock_jumps(runner, monkeypatch):
    """Advancing the wall clock by 100 s per reading must not change a
    seeded game: the budget is not a function of `time.monotonic`."""
    baseline = _play(runner)
    assert baseline[2] not in ("draw", "aborted"), baseline

    real_monotonic = time.monotonic
    offset = [0.0]

    def _jumping_monotonic():
        offset[0] += 100.0
        return real_monotonic() + offset[0]

    monkeypatch.setattr(time, "monotonic", _jumping_monotonic)
    assert _play(runner) == baseline


def test_exhausted_budget_is_reported_as_aborted_not_draw(runner, monkeypatch):
    """When the CPU budget is genuinely exhausted the game ends with
    `win_condition == "aborted"` and no winner — never the CR 104.4 label."""
    real_process_time = time.process_time
    calls = [0]

    def _exhausting_process_time():
        calls[0] += 1
        # Let the arming read pass, then report the budget blown.
        return real_process_time() + (0.0 if calls[0] <= 2 else 1e6)

    monkeypatch.setattr(time, "process_time", _exhausting_process_time)
    winner, turns, cond = _play(runner)
    assert cond == "aborted", (winner, turns, cond)
    assert winner == "draw"  # GameResult.winner_deck for winner=None


def test_budget_is_read_from_ai_constants_at_arm_time(runner, monkeypatch):
    """`ai.constants.GAME_TIMEOUT_SECONDS` patched to 0 aborts the very next
    game — the contract `tests/test_wr_baseline_anchor.py::_replay` and
    `tools/refresh_wr_baseline.py` rely on to neutralise the valve."""
    import ai.constants as ai_constants

    monkeypatch.setattr(ai_constants, "GAME_TIMEOUT_SECONDS", 0.0)
    winner, turns, cond = _play(runner)
    assert cond == "aborted", (winner, turns, cond)


def test_activation_gate_shares_the_game_budget():
    """`ActivationManager.can_activate` refuses once the shared budget is
    exhausted, before it touches the ability at all — it consults
    `engine.game_budget`, not a deadline field of its own."""
    from engine import game_budget
    from engine.activation import ActivationManager

    game = SimpleNamespace(game_over=False)
    game_budget.arm(game, budget_seconds=0.0)
    assert game_budget.expired(game) is True
    assert game.game_over is True

    assert ActivationManager.can_activate(game, 0, None, None) is False
