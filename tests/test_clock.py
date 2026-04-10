"""Tests for the clock-based position evaluation system."""
import pytest
from ai.ev_evaluator import EVSnapshot
from ai.clock import (
    combat_clock, life_as_resource, combo_clock,
    creature_clock_impact, position_value,
    card_clock_impact, mana_clock_impact, NO_CLOCK,
)


# ─── Combat Clock ───────────────────────────────────────────

class TestCombatClock:
    def test_basic_clock(self):
        """3 power vs 20 life = ceil(20/3) = 7 turns."""
        assert combat_clock(3, 20) == 7.0

    def test_lethal_clock(self):
        """20 power vs 5 life = ceil(5/20) = 1 turn."""
        assert combat_clock(20, 5) == 1.0

    def test_no_power_no_clock(self):
        """0 power = no clock."""
        assert combat_clock(0, 20) == NO_CLOCK

    def test_evasion_bypasses_blockers(self):
        """Evasion power isn't reduced by blocker toughness."""
        # 6 power total, 4 evasion, opponent has 9 toughness of blockers
        # With evasion: 4 evasion + max(0, 2 - 9/3) = 4 + 0 = 4, ceil(20/4) = 5
        # Without: max(0, 6 - 9/3) = max(0, 3) = 3, ceil(20/3) = 7
        clock_with_evasion = combat_clock(6, 20, evasion_power=4, opp_total_toughness=9)
        clock_ground_only = combat_clock(6, 20, evasion_power=0, opp_total_toughness=9)
        assert clock_with_evasion < clock_ground_only

    def test_more_power_faster_clock(self):
        assert combat_clock(6, 20) < combat_clock(3, 20)

    def test_lower_life_faster_clock(self):
        assert combat_clock(3, 10) < combat_clock(3, 20)


# ─── Life as Resource ───────────────────────────────────────

class TestLifeAsResource:
    def test_dead_is_terrible(self):
        assert life_as_resource(0, 5) == -100.0

    def test_low_life_is_dire(self):
        # 2 life with 5 incoming = 0.4 turns
        val = life_as_resource(2, 5)
        assert val < 1.0

    def test_high_life_is_comfortable(self):
        # 20 life with 5 incoming = 4.0 turns
        val = life_as_resource(20, 5)
        assert val >= 4.0

    def test_no_threats_is_safe(self):
        # 20 life with 0 incoming = luxury
        val = life_as_resource(20, 0)
        assert val >= 8.0

    def test_monotonic_in_life(self):
        """More life = more resource value."""
        assert life_as_resource(10, 3) > life_as_resource(5, 3)

    def test_monotonic_in_threat(self):
        """More incoming power = less safe."""
        assert life_as_resource(10, 5) < life_as_resource(10, 3)


# ─── Combo Clock ────────────────────────────────────────────

class TestComboClock:
    def test_high_storm_is_fast(self):
        snap = EVSnapshot(storm_count=10, my_hand_size=3, my_mana=3)
        assert combo_clock(snap) == 1.0

    def test_no_resources_is_slow(self):
        snap = EVSnapshot(storm_count=0, my_hand_size=1, my_mana=0)
        assert combo_clock(snap) > 5.0

    def test_more_fuel_faster(self):
        slow = EVSnapshot(storm_count=0, my_hand_size=2, my_mana=1)
        fast = EVSnapshot(storm_count=0, my_hand_size=5, my_mana=4)
        assert combo_clock(fast) < combo_clock(slow)

    def test_graveyard_helps_reanimate(self):
        no_gy = EVSnapshot(storm_count=0, my_hand_size=3, my_mana=3, my_gy_creatures=0)
        with_gy = EVSnapshot(storm_count=0, my_hand_size=3, my_mana=3, my_gy_creatures=2)
        assert combo_clock(with_gy) <= combo_clock(no_gy)


# ─── Creature Clock Impact ─────────────────────────────────

class TestCreatureClockImpact:
    def _snap(self, **kwargs):
        defaults = dict(opp_life=20, opp_power=3, opp_creature_count=1,
                        my_life=20, my_power=3, opp_evasion_power=0,
                        opp_toughness=3)
        defaults.update(kwargs)
        return EVSnapshot(**defaults)

    def test_more_power_more_impact(self):
        snap = self._snap()
        big = creature_clock_impact(5, 5, set(), snap)
        small = creature_clock_impact(2, 2, set(), snap)
        assert big > small

    def test_flying_improves_impact(self):
        snap = self._snap(opp_creature_count=2)
        with_fly = creature_clock_impact(3, 3, {"flying"}, snap)
        without = creature_clock_impact(3, 3, set(), snap)
        assert with_fly > without

    def test_haste_adds_immediate_value(self):
        snap = self._snap()
        with_haste = creature_clock_impact(3, 3, {"haste"}, snap)
        without = creature_clock_impact(3, 3, set(), snap)
        assert with_haste > without

    def test_lifelink_helps_when_pressured(self):
        snap = self._snap(opp_power=5)
        with_link = creature_clock_impact(3, 3, {"lifelink"}, snap)
        without = creature_clock_impact(3, 3, set(), snap)
        assert with_link > without

    def test_deathtouch_helps_vs_creatures(self):
        snap = self._snap(opp_creature_count=3, opp_power=6)
        with_dt = creature_clock_impact(1, 1, {"deathtouch"}, snap)
        without = creature_clock_impact(1, 1, set(), snap)
        assert with_dt > without

    def test_double_strike_doubles_clock(self):
        snap = self._snap()
        with_ds = creature_clock_impact(3, 3, {"double_strike"}, snap)
        without = creature_clock_impact(3, 3, set(), snap)
        assert with_ds > without * 1.5  # at least 50% better

    def test_hexproof_increases_reliability(self):
        snap = self._snap()
        with_hex = creature_clock_impact(4, 4, {"hexproof"}, snap)
        without = creature_clock_impact(4, 4, set(), snap)
        assert with_hex > without

    def test_zero_power_minimal_value(self):
        snap = self._snap()
        val = creature_clock_impact(0, 4, set(), snap)
        assert val < 0.5  # mostly just blocking value


# ─── Position Value ─────────────────────────────────────────

class TestPositionValue:
    def test_more_power_better_position(self):
        strong = EVSnapshot(my_power=8, my_life=20, opp_life=20, opp_power=3)
        weak = EVSnapshot(my_power=2, my_life=20, opp_life=20, opp_power=3)
        assert position_value(strong) > position_value(weak)

    def test_lower_opp_life_better(self):
        close = EVSnapshot(my_power=3, my_life=20, opp_life=5, opp_power=3)
        far = EVSnapshot(my_power=3, my_life=20, opp_life=20, opp_power=3)
        assert position_value(close) > position_value(far)

    def test_more_opp_power_worse(self):
        safe = EVSnapshot(my_power=3, my_life=20, opp_life=20, opp_power=1)
        threatened = EVSnapshot(my_power=3, my_life=20, opp_life=20, opp_power=8)
        assert position_value(safe) > position_value(threatened)

    def test_more_cards_better(self):
        full = EVSnapshot(my_power=3, my_life=20, opp_life=20, opp_power=3,
                          my_hand_size=7, opp_hand_size=2, my_mana=3)
        empty = EVSnapshot(my_power=3, my_life=20, opp_life=20, opp_power=3,
                           my_hand_size=1, opp_hand_size=5, my_mana=3)
        assert position_value(full) > position_value(empty)

    def test_dead_is_minimum(self):
        dead = EVSnapshot(my_life=0, opp_life=20, my_power=10, opp_power=0)
        assert position_value(dead) == -100.0

    def test_opp_dead_is_maximum(self):
        won = EVSnapshot(my_life=20, opp_life=0, my_power=0, opp_power=10)
        assert position_value(won) == 100.0

    def test_symmetric_is_near_zero(self):
        """Equal board should be near zero."""
        sym = EVSnapshot(my_power=3, opp_power=3, my_life=20, opp_life=20,
                         my_hand_size=4, opp_hand_size=4, my_mana=3, opp_mana=3)
        val = position_value(sym)
        assert -2.0 < val < 2.0  # roughly balanced

    def test_combo_archetype_uses_combo_clock(self):
        """Combo decks with storm count should have better position."""
        storming = EVSnapshot(storm_count=8, my_hand_size=3, my_mana=2,
                              my_life=15, opp_life=20, opp_power=3)
        no_storm = EVSnapshot(storm_count=0, my_hand_size=3, my_mana=2,
                              my_life=15, opp_life=20, opp_power=3)
        assert position_value(storming, "combo") > position_value(no_storm, "combo")

    def test_aggro_and_midrange_same_function(self):
        """Aggro and midrange use the same unified function."""
        snap = EVSnapshot(my_power=5, opp_power=3, my_life=20, opp_life=15)
        # Both should return values (they use the same combat clock)
        assert position_value(snap, "aggro") == position_value(snap, "midrange")

    def test_deploying_creature_improves_position(self):
        """Adding a creature to my board should improve position."""
        before = EVSnapshot(my_power=3, opp_power=3, my_life=20, opp_life=20)
        after = EVSnapshot(my_power=6, opp_power=3, my_life=20, opp_life=20)
        assert position_value(after) > position_value(before)

    def test_removing_opp_creature_improves_position(self):
        """Removing an opponent creature should improve position."""
        before = EVSnapshot(my_power=3, opp_power=6, my_life=20, opp_life=20)
        after = EVSnapshot(my_power=3, opp_power=3, my_life=20, opp_life=20)
        assert position_value(after) > position_value(before)

    def test_evasion_improves_position(self):
        """Evasion power should improve position vs blockers."""
        ground = EVSnapshot(my_power=5, my_evasion_power=0,
                            opp_power=3, opp_toughness=6,
                            my_life=20, opp_life=20)
        evasive = EVSnapshot(my_power=5, my_evasion_power=5,
                             opp_power=3, opp_toughness=6,
                             my_life=20, opp_life=20)
        assert position_value(evasive) > position_value(ground)
