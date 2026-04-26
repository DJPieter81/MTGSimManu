"""Unified decision primitive for AI choices.

Every decision the AI makes — cast a spell, pick a target, pay an
optional cost, choose blocks, mulligan — flows through `best_choice`.
Each call site builds a list of `Choice` objects describing its
options as snapshot deltas; the kernel scores them all via
`evaluate_board` on a projected EVSnapshot and returns the highest-
scoring option (or None if no option strictly beats the baseline).

Design rule: the kernel adds NO domain knowledge, NO magic
constants, NO archetype gates.  All semantics live in (a) the
`apply` lambdas the caller constructs and (b) the `evaluate_board`
function the kernel calls.

This is the single primitive that replaces every domain-named
`should_pay_X` / `pick_best_Y` callback in the codebase.
"""
from __future__ import annotations
from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from ai.schemas import Choice

if TYPE_CHECKING:
    from ai.ev_evaluator import EVSnapshot
    from engine.game_state import GameState


def best_choice(
    game: "GameState",
    player_idx: int,
    archetype: str,
    choices: list[Choice],
    *,
    baseline: Optional["EVSnapshot"] = None,
    snap_now: Optional["EVSnapshot"] = None,
) -> Optional[Choice]:
    """Return the choice whose projected snapshot scores highest.

    Parameters
    ----------
    game, player_idx :
        Used to derive the current snapshot when `snap_now` is not
        supplied.  Tests typically pass `snap_now` directly to keep
        the kernel pure.
    archetype :
        Forwarded to `evaluate_board` so combo / aggro / control
        decks score positions on their own clock terms.
    choices :
        Each Choice has an `apply(snap)` callable that mutates a
        snapshot copy and returns it.  Order matters only for tie-
        breaking — the FIRST option whose EV ties the running best
        is kept, so callers can place "do nothing" early to bias
        against unnecessary action.
    baseline :
        Optional reference snapshot for the "do nothing" comparison.
        Defaults to `snap_now`.  Useful when the caller wants to
        compare options against a hypothetical state (e.g., after
        opponent's projected response).
    snap_now :
        Optional pre-built snapshot.  When supplied, the kernel
        does not call `snapshot_from_game` and the `game` /
        `player_idx` parameters are ignored.

    Returns
    -------
    Choice or None
        The best-scoring choice, or None if no choice strictly beats
        the baseline.  A None return is the AI's signal to "hold".
    """
    from ai.ev_evaluator import snapshot_from_game, evaluate_board

    if snap_now is None:
        snap_now = snapshot_from_game(game, player_idx)
    base = baseline if baseline is not None else snap_now
    base_ev = evaluate_board(base, archetype)

    best_ev = base_ev
    best: Optional[Choice] = None
    for c in choices:
        snap_after = c.apply(replace(snap_now))
        ev = evaluate_board(snap_after, archetype)
        if ev > best_ev:
            best_ev = ev
            best = c
    return best
