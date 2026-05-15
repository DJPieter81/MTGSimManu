"""Position-value sub-turn granularity at lethal — defensive-save EV.

Rule under test
---------------
When the controller is at or near lethal life, `position_value` must
distinguish "lethal NOW" from "lethal NEXT TURN" so a defensive-save
spell (board wipe, mass removal, life-gain combo) scores strictly
higher than a non-save play.

Mechanic, not card
------------------
This is the symmetric opponent-side EV-collapse identified in
`docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md`
(Component A). `combat_clock` floors at 1.0 turn, so any state where
opp_power ≥ my_life produces the SAME `opp_clock=1.0` value as a state
where opp_power kills me on the next turn (a 1-turn delay). When the
defending player has 1 life, the clock_diff term is therefore identical
before and after a save — the save scores essentially zero from the
position-value math.

The fix must make `position_value(after_save) > position_value(before)`
under the same near-lethal state. We measure this on the synthesized
g2t4d45 fixture (Boros 1 life, Affinity 14 power lethal next turn).
"""
from __future__ import annotations

import pytest

from ai.clock import position_value, combat_clock, NO_CLOCK
from ai.ev_evaluator import EVSnapshot


def _lethal_state_snap(my_life=1, opp_power=14, my_power=0, **overrides) -> EVSnapshot:
    """Snapshot mirroring g2t4d45: defender at lethal, attacker has wide board."""
    defaults = dict(
        my_life=my_life, opp_life=20,
        my_power=my_power, opp_power=opp_power,
        my_toughness=0, opp_toughness=opp_power,  # ~1:1 power/toughness
        my_creature_count=0, opp_creature_count=5,
        my_hand_size=4, opp_hand_size=2,
        my_mana=3, opp_mana=2,
        my_total_lands=3, opp_total_lands=3,
        turn_number=4,
    )
    defaults.update(overrides)
    return EVSnapshot(**defaults)


class TestSubTurnLethalGranularity:
    """The position-value math must respect that 'lethal now' (this turn,
    no answer) is strictly worse than 'lethal next turn' (one turn of
    breathing room from a defensive save)."""

    def test_position_value_distinguishes_lethal_now_from_lethal_next_turn(self):
        """Synthesize g2t4d45 (Boros 1 life vs Affinity 14 power, no
        Boros creatures). Compare the snapshot AFTER a defensive board
        wipe (opp_power → 2, leaving Frogmite 2/2) vs the snapshot
        AFTER a non-wipe play (opp_power unchanged at 14).

        Pre-fix: both states produce opp_clock=1.0 (combat_clock floors
        at 1.0), so `clock_diff` is identical and only `life_advantage`
        differs by a small life-as-resource term. Net delta < 1.0
        — the defensive save scores negligibly more than the non-save.

        Post-fix: the position-value math must credit "lethal next
        turn" strictly higher than "lethal now" by at least a survival
        turn's worth (the controller actually survives the upcoming
        attack step).
        """
        before = _lethal_state_snap(my_life=1, opp_power=14)
        # After non-wipe (Phlage gains 3 life, opp_power unchanged):
        after_non_wipe = _lethal_state_snap(my_life=4, opp_power=14)
        # After board wipe at X=0 (clears 4 of 5 Affinity creatures,
        # leaving Frogmite 2/2):
        after_wipe = _lethal_state_snap(
            my_life=1, opp_power=2,
            opp_creature_count=1, opp_toughness=2,
        )

        pv_before = position_value(before)
        pv_non_wipe = position_value(after_non_wipe)
        pv_wipe = position_value(after_wipe)

        # The board wipe MUST be valued strictly higher than the
        # non-wipe alternative — it removes the lethal threat entirely,
        # while the non-wipe at best buys ~1 hit of life cushion.
        assert pv_wipe > pv_non_wipe, (
            f"Defensive board wipe must score higher than non-wipe at "
            f"lethal. position_value(wipe)={pv_wipe:.3f}, "
            f"position_value(non_wipe)={pv_non_wipe:.3f}. The wipe "
            f"removes the entire lethal-threat clock; the non-wipe "
            f"only buys a few life. Failure indicates the clock_diff "
            f"term is collapsing at the combat_clock 1.0-floor."
        )

        # The wipe's delta over `before` must reflect that the
        # controller now actually survives the attack step.  Use one
        # life-point-equivalent (≈ life_as_resource of opp_life / opp_power
        # ≥ 1 survival turn) as the minimum-meaningful margin.
        delta = pv_wipe - pv_before
        assert delta > 1.0, (
            f"Defensive wipe at 1 life produced position_value delta "
            f"{delta:.3f}; expected strictly > 1.0 (at least one "
            f"survival-turn-equivalent improvement, since the wipe "
            f"removed lethal damage). Pre-fix this delta is ~0.43 "
            f"because clock_diff is unchanged (combat_clock floors at "
            f"1.0 turn for opp_power=14 AND opp_power=2 vs my_life=1)."
        )

    def test_combat_clock_below_one_turn_does_not_collapse_to_one(self):
        """combat_clock must encode 'lethal NOW' (power ≥ life) as a
        sub-1-turn value distinguishable from 'lethal in 1 attack step
        next turn' (power exactly equal to life). The current floor at
        1.0 collapses both to the same number."""
        # State A: opp will exactly lethal me next attack step
        #   (opp_power=2, my_life=2) → ceil(2/2) = 1 turn
        clock_lethal_next = combat_clock(power=2, opp_life=2)
        # State B: opp has lethal RIGHT NOW with massive overkill
        #   (opp_power=14, my_life=1) → ceil(1/14) = 1 turn (floored!)
        clock_lethal_now = combat_clock(power=14, opp_life=1)

        # The bug: both round to 1.0 because of the max(1.0, ...) floor.
        # Post-fix: the "lethal-now with overkill" case must be strictly
        # smaller (faster clock) than "lethal-next-turn".
        assert clock_lethal_now < clock_lethal_next, (
            f"combat_clock collapsed 'lethal now overkill' to "
            f"{clock_lethal_now:.3f} and 'lethal next turn' to "
            f"{clock_lethal_next:.3f} — these MUST differ to let "
            f"position_value distinguish 'I die this turn' from 'I die "
            f"next turn'. Failure indicates the 1.0 floor is still "
            f"collapsing sub-turn clocks."
        )


class TestSymmetricRegression:
    """Regression: a non-lethal state where both players have comfortable
    life totals must continue to produce monotonic position_value behaviour
    — the new sub-turn granularity should NOT change the sign of any
    standard mid-game evaluation."""

    def test_midgame_no_threat_neither_side_dying(self):
        """Mid-game state with neither side near lethal — position_value
        must still return finite, sensible numbers (no NaN, no extreme
        values)."""
        snap = EVSnapshot(
            my_life=15, opp_life=15,
            my_power=3, opp_power=3,
            my_toughness=4, opp_toughness=4,
            my_creature_count=2, opp_creature_count=2,
            my_hand_size=4, opp_hand_size=4,
            my_mana=4, opp_mana=4,
            my_total_lands=4, opp_total_lands=4,
            turn_number=4,
        )
        pv = position_value(snap)
        # Should be a small finite number, near zero (mirror state).
        assert abs(pv) < 20.0, (
            f"Mid-game mirror state produced position_value={pv:.3f}; "
            f"expected near-zero. The sub-turn granularity fix must "
            f"not blow up the standard-state evaluation."
        )
