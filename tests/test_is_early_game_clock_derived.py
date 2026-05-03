"""Failing-test contract for `ai.clock.is_early_game(snap)`.

The predicate is *clock-derived*, not turn-counter-driven, so it must
correctly reclassify boards where the actual game pace has diverged
from the turn number:

1. An aggro board where opp's turns-to-lethal is 2 returns False
   even on game turn 2 — the clock has already collapsed.
2. A Tron-style board with no creatures returns True even on game
   turn 6 — neither side is pressuring.

These two cases falsify both directions of the old `turn_number <= 4`
heuristic and lock in the clock-derived semantics for downstream
consumers (bhi.py hold-rate, evaluator.py discard bonus).
"""
from __future__ import annotations
from ai.clock import is_early_game
from ai.ev_evaluator import EVSnapshot


def _aggro_board_collapsed_clock_t2() -> EVSnapshot:
    """Game turn 2, opp has Goblin Guide + Monastery Swiftspear (5 power)
    swinging at our 20 life — opp_clock = 4 from opp's POV but my_clock
    is non-existent because we have no power on our side. Symmetric:
    min(99, 4) = 4 — exactly at the threshold. Push to clearly inside
    by giving opp 6 power (clock = 20/6 ≈ 3.33 < 4).
    """
    return EVSnapshot(
        turn_number=2,
        my_life=20, opp_life=20,
        my_power=0,            # we haven't deployed yet
        opp_power=6,           # opp has lethal in ~4 swings
    )


def _slow_board_t6_no_pressure() -> EVSnapshot:
    """Game turn 6, both Tron / Eldrazi mode — neither side has dropped
    a clock-relevant body yet. min(99, 99) > 4 ⇒ still early.
    """
    return EVSnapshot(
        turn_number=6,
        my_life=20, opp_life=20,
        my_power=0,
        opp_power=0,
    )


def test_is_early_game_false_when_aggro_clock_collapsed_on_t2():
    """An aggro board collapses early-game classification regardless of
    turn number. The old `turn_number <= 4` heuristic mis-classified
    this as early-game."""
    snap = _aggro_board_collapsed_clock_t2()
    assert is_early_game(snap) is False, (
        "Aggro board (opp clock < 4) on T2 should NOT be classified as "
        "early-game, but is_early_game returned True. The predicate is "
        "clock-derived, not turn-counter-driven."
    )


def test_is_early_game_true_when_no_pressure_on_t6():
    """A no-pressure board (Tron / control mirror) stays early-game past
    T6 because neither side's clock has materialised. The old
    `turn_number <= 4` heuristic mis-classified this as mid-game."""
    snap = _slow_board_t6_no_pressure()
    assert is_early_game(snap) is True, (
        "Slow board (no creatures) on T6 should still be classified as "
        "early-game (no clock from either side), but is_early_game "
        "returned False."
    )


def test_is_early_game_symmetric_in_clock():
    """Roles reversed: same predicate, swap which side has the clock.
    Symmetry locks the contract that early-game = neither side has
    a clock."""
    me_clocking = EVSnapshot(
        turn_number=3,
        my_life=20, opp_life=20,
        my_power=6, opp_power=0,   # I have lethal in ~4
    )
    opp_clocking = EVSnapshot(
        turn_number=3,
        my_life=20, opp_life=20,
        my_power=0, opp_power=6,   # opp has lethal in ~4
    )
    assert is_early_game(me_clocking) is False
    assert is_early_game(opp_clocking) is False
