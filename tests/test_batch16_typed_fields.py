"""Batch 16 typed-field migration tests.

# Mechanic: X-counter scaling (rule, not a card)

has_x_counter_scaling replaces 'x +1/+1 counter'/'x +1/+1 counters' in
oracle in response.py card_threat_value — X-cost creatures that enter
with counters scale with the mana paid to cast them.

# Mechanic: lifegain equal to power (rule, not a card)

has_lifegain_equal_power replaces 'gains life' + 'power' in oracle in
board_eval.py evoke-scoring — removal ETBs that heal the opponent for
the removed creature's power are penalised against small targets.

# Mechanic: general lifegain effect (rule, not a card)

has_lifegain_effect replaces 'gain' + 'life' in oracle in ev_evaluator.py
creature_value ETB-lifegain projection — ETB life totals are used to
project an adjusted life total for clock computations.

# Mechanic: exile own creature (rule, not a card)

has_exile_own_creature replaces 'exile target creature you control' in
oracle in ev_player.py blink-fizzle gate — blink spells with no legal
targets are prevented from scoring positive EV.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_x_counter_scaling,
    parse_has_lifegain_equal_power,
    parse_has_lifegain_effect,
    parse_has_exile_own_creature,
)


class TestHasXCounterScalingThreatValue:
    """Pins the replacement of 'x +1/+1 counter'/'x +1/+1 counters' in oracle
    in response.py card_threat_value — X-cost creatures scale with mana paid."""

    def test_x_plus_one_counter_singular(self):
        # Walking Ballista / Hangarback Walker pattern
        assert parse_has_x_counter_scaling(
            "Walking Ballista enters with X +1/+1 counters on it."
        ) is True

    def test_x_plus_one_counter_plural(self):
        assert parse_has_x_counter_scaling(
            "This creature enters the battlefield with X +1/+1 counters on it."
        ) is True

    def test_static_counter_is_false(self):
        # A +1/+1 counter without the X is NOT X-scaling
        assert parse_has_x_counter_scaling(
            "Proliferate. (Choose any number of permanents and/or players "
            "with counters on them, then give each another counter of a kind "
            "already there.)"
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_x_counter_scaling("") is False
        assert parse_has_x_counter_scaling(None) is False


class TestHasLifegainEqualPowerBoardEval:
    """Pins the replacement of 'gains life' + 'power' in oracle in
    board_eval.py — removal ETBs that heal the opponent for the removed
    creature's power are penalised against small targets (Solitude/Fury)."""

    def test_gains_life_equal_to_power(self):
        # Solitude / Fury evoke: controller gains life = exiled creature's power
        assert parse_has_lifegain_equal_power(
            "If a white card was exiled this way, its controller "
            "gains life equal to its power."
        ) is True

    def test_gains_life_power_phrasing(self):
        assert parse_has_lifegain_equal_power(
            "Target player gains life equal to the greatest power among "
            "creatures you control."
        ) is True

    def test_flat_lifegain_no_power(self):
        # Thragtusk — gains flat life, not power-dependent
        assert parse_has_lifegain_equal_power("You gain 5 life.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_lifegain_equal_power("") is False
        assert parse_has_lifegain_equal_power(None) is False


class TestHasLifegainEffectETBProjection:
    """Pins the replacement of 'gain' + 'life' in oracle in ev_evaluator.py
    creature_value — ETB lifegain is projected into the clock computation."""

    def test_you_gain_life_is_true(self):
        # Thragtusk / Kitchen Finks / Lone Missionary pattern
        assert parse_has_lifegain_effect("When this creature enters, you gain 5 life.") is True

    def test_gains_life_is_true(self):
        assert parse_has_lifegain_effect("Lifelink (Damage dealt by this creature also "
                                         "causes you to gain that much life.)") is True

    def test_pure_draw_no_lifegain(self):
        assert parse_has_lifegain_effect("Draw two cards.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_lifegain_effect("") is False
        assert parse_has_lifegain_effect(None) is False


class TestHasExileOwnCreatureBlinkGate:
    """Pins the replacement of 'exile target creature you control' in oracle
    in ev_player.py blink-fizzle gate — blink with no creatures is blocked."""

    def test_exile_target_creature_you_control(self):
        # Ephemerate / Restoration Angel / Flickerwisp pattern
        assert parse_has_exile_own_creature(
            "Exile target creature you control, then return it to the "
            "battlefield under its owner's control."
        ) is True

    def test_exile_target_permanent_is_false(self):
        # Generic exile (not specifically own creature)
        assert parse_has_exile_own_creature(
            "Exile target nonland permanent."
        ) is False

    def test_exile_opponent_creature_is_false(self):
        # Removal spell exiles opponent's creature
        assert parse_has_exile_own_creature(
            "Exile target creature an opponent controls."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_exile_own_creature("") is False
        assert parse_has_exile_own_creature(None) is False
