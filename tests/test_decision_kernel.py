"""Unit tests for ai.decision_kernel.best_choice.

These tests bypass `snapshot_from_game` by passing `snap_now` directly,
keeping the kernel test pure: it exercises the primitive's contract
(score each choice, return best or None) without coupling to engine
state construction.
"""
from __future__ import annotations

import pytest

from ai.decision_kernel import best_choice
from ai.ev_evaluator import EVSnapshot
from ai.schemas import Choice


def _baseline_snap(**overrides) -> EVSnapshot:
    """Default snapshot with a stable mid-game position."""
    base = dict(
        my_life=20, opp_life=20,
        my_power=2, opp_power=2,
        my_toughness=2, opp_toughness=2,
        my_creature_count=1, opp_creature_count=1,
        my_hand_size=4, opp_hand_size=4,
        my_mana=3, opp_mana=3,
        my_total_lands=3, opp_total_lands=3,
        turn_number=4,
    )
    base.update(overrides)
    return EVSnapshot(**base)


def test_returns_none_when_no_choice_beats_baseline():
    snap = _baseline_snap()

    def noop(s):
        return s

    choices = [
        Choice(name="a", apply=noop, source="cast"),
        Choice(name="b", apply=noop, source="cast"),
    ]
    assert best_choice(None, 0, "midrange", choices, snap_now=snap) is None


def test_picks_choice_that_kills_opponent():
    snap = _baseline_snap(opp_life=2)

    def lethal(s):
        s.opp_life = 0
        return s

    def noop(s):
        return s

    choices = [
        Choice(name="hold", apply=noop, source="cast"),
        Choice(name="lethal_burn", apply=lethal, source="cast"),
    ]
    pick = best_choice(None, 0, "midrange", choices, snap_now=snap)
    assert pick is not None and pick.name == "lethal_burn"


def test_avoids_choice_that_kills_self():
    snap = _baseline_snap(my_life=2)

    def suicide(s):
        s.my_life = 0
        return s

    def safe(s):
        # Tiny improvement: extra card in hand
        s.my_hand_size += 1
        return s

    choices = [
        Choice(name="kill_myself", apply=suicide, source="optional_cost"),
        Choice(name="draw_a_card", apply=safe, source="cast"),
    ]
    pick = best_choice(None, 0, "midrange", choices, snap_now=snap)
    assert pick is not None and pick.name == "draw_a_card"


def test_higher_ev_choice_wins():
    snap = _baseline_snap()

    def small(s):
        s.my_hand_size += 1
        return s

    def big(s):
        s.my_power += 5  # bigger swing in clock_diff
        return s

    choices = [
        Choice(name="small", apply=small, source="cast"),
        Choice(name="big", apply=big, source="cast"),
    ]
    pick = best_choice(None, 0, "midrange", choices, snap_now=snap)
    assert pick is not None and pick.name == "big"


def test_baseline_override_compares_against_alternative_state():
    snap = _baseline_snap()
    # Hypothetical "after opponent's turn" baseline — we've taken damage
    after_opp = snap.replace(my_life=snap.my_life - 5)

    def hold(s):
        return s

    choices = [Choice(name="hold", apply=hold, source="cast")]
    # Against the rosier `snap` baseline, "hold" doesn't beat it — None.
    assert best_choice(None, 0, "midrange", choices, snap_now=snap) is None
    # Against the worse `after_opp` baseline, "hold" (returning snap)
    # actually scores higher than the projected "after_opp" state.
    pick = best_choice(None, 0, "midrange", choices,
                       snap_now=snap, baseline=after_opp)
    assert pick is not None and pick.name == "hold"


def test_empty_choice_list_returns_none():
    snap = _baseline_snap()
    assert best_choice(None, 0, "midrange", [], snap_now=snap) is None


def test_apply_does_not_mutate_caller_snapshot():
    """The kernel must protect the caller's snapshot from mutation."""
    snap = _baseline_snap()

    def mutating(s):
        s.my_life = 1
        s.opp_life = 1
        return s

    choices = [Choice(name="mutating", apply=mutating, source="cast")]
    best_choice(None, 0, "midrange", choices, snap_now=snap)
    # Caller's snap untouched
    assert snap.my_life == 20
    assert snap.opp_life == 20


def test_first_winning_choice_wins_on_tie():
    """When two choices score identically, the first wins (stable order)."""
    snap = _baseline_snap()

    def boost(s):
        s.my_hand_size += 2
        return s

    choices = [
        Choice(name="first", apply=boost, source="cast"),
        Choice(name="second", apply=boost, source="cast"),
    ]
    pick = best_choice(None, 0, "midrange", choices, snap_now=snap)
    assert pick is not None and pick.name == "first"
