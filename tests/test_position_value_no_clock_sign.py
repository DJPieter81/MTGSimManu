"""With no clock of my own and an opponent who has one, a SLOWER opposing
clock is never a worse position than a faster one.

`position_value`'s "I have no clock, opponent does" branch scored
`-opp_clock`: the exact inverse of its own comment ("worse as opp gets
faster"). Lengthening the opponent's clock — deploying a blocker, casting
removal — read as a downgrade, so a creatureless board never valued its
own defence and a zero-power mana creature projected at -34 (Creatures
Toolbox T4, seed 50001: Devoted Druid raised my_toughness 0 -> 2, opp_clock
17 -> 51 turns, position_value -17 -> -51). The 2026-08-30 diagnostic
confirmed the inversion as a real defect and falsified only the prediction
that fixing it lifts creature-light CONTROL; this pins the correctness rule
itself and mirrors the winning branch's existing saturating form.
"""
from __future__ import annotations

from ai.clock import position_value, NO_CLOCK
from ai.ev_evaluator import EVSnapshot


def _snap(**kw):
    base = dict(
        my_life=20, opp_life=20, my_power=0, opp_power=2,
        my_toughness=0, opp_toughness=0, my_creature_count=0,
        opp_creature_count=1, my_hand_size=5, opp_hand_size=5,
        my_mana=0, opp_mana=0, my_total_lands=3, opp_total_lands=3,
        turn_number=4, storm_count=0, my_gy_creatures=0, my_energy=0,
        my_evasion_power=0, my_lifelink_power=0, opp_evasion_power=0,
        cards_drawn_this_turn=0,
    )
    base.update(kw)
    return EVSnapshot(**base)


def test_slower_opposing_clock_is_not_worse_when_i_have_no_clock():
    fast = _snap(opp_power=6)     # opp kills in ~4
    slow = _snap(opp_power=1)     # opp kills in 20
    assert position_value(slow) > position_value(fast)


def test_deploying_a_pure_blocker_does_not_lower_position_value():
    before = _snap(my_toughness=0)
    after = _snap(my_toughness=2)  # a 0/2 stretches the opposing clock
    assert position_value(after) >= position_value(before)


def test_losing_branch_is_the_mirror_of_the_winning_branch():
    """Symmetric race, mirrored boards: my position must be exactly the
    negative of theirs (every other term is antisymmetric by construction,
    so only the two sentinel branches can break the mirror)."""
    import pytest
    mine = _snap(my_power=4, opp_power=0, my_creature_count=1,
                 opp_creature_count=0)
    theirs = _snap(my_power=0, opp_power=4, my_creature_count=0,
                   opp_creature_count=1)
    assert position_value(mine) == pytest.approx(-position_value(theirs))
