"""Per-game safety budget (rules infrastructure, zero scoring).

One place owns the valve that stops a runaway game from hanging the
simulator. Every engine loop head asks `expired(game)`; nothing else keeps a
deadline of its own.

The budget is CPU time (`time.process_time`), not wall-clock time. The
difference is the whole point:

  * A wall-clock deadline makes a seeded game's outcome a function of machine
    LOAD. A game that needs 1.7 s of work takes >8 s of wall time under three
    parallel workers or a busy CI runner, is cut off at a loop head, and is
    recorded as a draw. Observed: the WR anchor flipped a winner between a
    local run and CI on identical code (`083393b`); a whole `--field` run
    came back 0.0% on a contended container while the same seeds play to
    normal finishes on a quiet one (2026-09-06).
  * CPU time does not move while the process is starved, so contention
    cannot exhaust it; a genuinely spinning loop still burns it, so a hang
    is still caught. Machine SPEED still matters, which is why the budget
    is sized as a multiple of the slowest legitimate game ever measured
    (see `GAME_TIMEOUT_SECONDS`).

The budget value is read from `ai.constants` when the game is armed. That
is a contract, not an accident: `tests/test_wr_baseline_anchor.py::_replay`
and `tools/refresh_wr_baseline.py` neutralise the valve by rebinding
`ai.constants.GAME_TIMEOUT_SECONDS` around a replay.

When the budget is exhausted the game is flagged (`_budget_exhausted`) and
ended (`game_over`). `GameRunner.run_game` reports that as
`win_condition == "aborted"` — distinct from a CR 104.4 draw, so
aggregators can count it instead of crediting it to anyone.
"""
from __future__ import annotations

import time
from typing import Optional


def arm(game, budget_seconds: Optional[float] = None) -> None:
    """Start the game's CPU budget. Reads `ai.constants.GAME_TIMEOUT_SECONDS`
    at call time unless an explicit budget is given."""
    if budget_seconds is None:
        from ai.constants import GAME_TIMEOUT_SECONDS
        budget_seconds = GAME_TIMEOUT_SECONDS
    game._budget_deadline = time.process_time() + float(budget_seconds)
    game._budget_exhausted = False


def expired(game) -> bool:
    """True once the game's CPU budget is spent. Sticky: the first expiry
    marks the game exhausted and over, and every later call agrees.

    A game that was never armed has no budget and never expires."""
    if getattr(game, "_budget_exhausted", False):
        return True
    deadline = getattr(game, "_budget_deadline", None)
    if deadline is None:
        return False
    if time.process_time() > deadline:
        game._budget_exhausted = True
        game.game_over = True
        return True
    return False


def exhausted(game) -> bool:
    """Did this game end because its budget ran out? (Read-only.)"""
    return bool(getattr(game, "_budget_exhausted", False))
