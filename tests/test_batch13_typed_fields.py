"""Batch 13 typed-field migration tests.

# Mechanic: requires creature target (rule, not a card)

parse_requires_creature_target covers 'target creature' and 'creature
spell' oracle patterns — replaces runtime checks in board_eval.py.

# Mechanic: has discard effect (rule, not a card)

parse_has_discard_effect covers 'discard a card' pattern — replaces
runtime checks in ev_player.py (Psychic Frog pump path) and
finisher_simulator.py.

# Mechanic: alternate exile cost (rule, not a card)

parse_has_alternate_exile_cost covers 'exile a … rather than pay' pattern
— replaces runtime check in response.py _is_pitch_counter.

# Mechanic: recurring trigger + token_maker tag combination (rule, not a card)

parse_has_recurring_trigger + 'token_maker' in tags replaces the
'whenever' + ('create' or 'token') in oracle check in response.py
card_threat_value.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_requires_creature_target,
    parse_has_discard_effect,
    parse_has_alternate_exile_cost,
    parse_has_recurring_trigger,
)


class TestRequiresCreatureTargetBoardEval:
    """Pins the replacement of 'target creature' in oracle at board_eval.py."""

    def test_target_creature_returns_true(self):
        # Ephemerate ETB targeting a creature
        assert parse_requires_creature_target(
            "Exile target creature you control, then return it to the battlefield."
        ) is True

    def test_creature_spell_returns_true(self):
        # Essence Scatter
        assert parse_requires_creature_target(
            "Counter target creature spell."
        ) is True

    def test_removal_targeting_any_permanent_is_false(self):
        assert parse_requires_creature_target(
            "Destroy target nonland permanent."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_requires_creature_target("") is False
        assert parse_requires_creature_target(None) is False


class TestHasDiscardEffectEVPlayer:
    """Pins the replacement of 'discard a card' in oracle at ev_player.py and
    finisher_simulator.py — both paths gate the pump/discard interaction."""

    def test_discard_a_card_effect_is_true(self):
        # Psychic Frog / looter pump pattern
        assert parse_has_discard_effect(
            "Discard a card: this creature gets +1/+1 until end of turn."
        ) is True

    def test_player_discards_a_card_is_true(self):
        # "target player discards a card" wording (Hymn to Tourach-class)
        assert parse_has_discard_effect(
            "Target player discards a card at random."
        ) is True

    def test_pure_cantrip_no_discard(self):
        assert parse_has_discard_effect("Draw a card.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_discard_effect("") is False
        assert parse_has_discard_effect(None) is False


class TestHasAlternateExileCostResponsePitchCheck:
    """Pins the replacement of 'exile a … rather than pay' oracle check in
    response.py _is_pitch_counter — pitch counters have this alternative cost."""

    def test_force_of_will_pattern(self):
        # Force of Will / Force of Negation pattern
        assert parse_has_alternate_exile_cost(
            "If it's not your turn, you may exile a blue card from your hand "
            "rather than pay this spell's mana cost."
        ) is True

    def test_exile_without_rather_than_is_false(self):
        # Leyline Binding exiles but is not a pitch spell
        assert parse_has_alternate_exile_cost(
            "Exile target nonland nontoken permanent."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_alternate_exile_cost("") is False
        assert parse_has_alternate_exile_cost(None) is False


class TestRecurringTriggerTokenMakerCombination:
    """Pins the logic replacing 'whenever' + ('create'/'token') in oracle in
    response.py card_threat_value — token generators have both signals."""

    def test_whenever_with_token_creation(self):
        # Young Pyromancer / Saheeli Sublime Artificer pattern
        oracle = "Whenever you cast a noncreature spell, create a 1/1 red Elemental creature token."
        assert parse_has_recurring_trigger(oracle) is True

    def test_at_beginning_with_token_creation(self):
        # Midnight Guard / Intangible Virtue pattern
        oracle = "At the beginning of your upkeep, create a 1/1 white Soldier creature token."
        assert parse_has_recurring_trigger(oracle) is True

    def test_non_recurring_token_spell_no_recurring_trigger(self):
        # Raise the Alarm — creates tokens but no recurring trigger
        oracle = "Create two 1/1 white Soldier creature tokens."
        assert parse_has_recurring_trigger(oracle) is False

    def test_recurring_trigger_without_tokens(self):
        # Triggers that aren't token generators are still recurring_trigger=True
        oracle = "Whenever you cast a spell, draw a card."
        assert parse_has_recurring_trigger(oracle) is True
