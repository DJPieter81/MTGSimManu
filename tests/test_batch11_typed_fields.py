"""Batch 11 typed-field migration tests.

# Mechanic: cast trigger (rule, not a card)

parse_has_cast_trigger fires for any oracle with 'when you cast' phrasing.

# Mechanic: recurring trigger (rule, not a card)

parse_has_recurring_trigger fires for 'whenever ...' or
'at the beginning of ...' triggered abilities.

# Mechanic: opponent spell timing restriction -- moved to the effect grammar
(tests/test_cast_timing_restrictions_from_text.py).

# Mechanic: charge-counter board wipe -- moved to the effect grammar
(tests/test_a_sweep_destroys_each_permanent_its_description_admits.py).

# Mechanic: mana-value wipe (rule, not a card)

parse_has_mana_value_wipe fires for X-cost destroy-by-mana-value wipes.

# Mechanic: sacrifice-for-damage outlet (rule, not a card)

parse_has_sacrifice_for_damage fires for Goblin Bombardment pattern.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_cast_trigger,
    parse_has_recurring_trigger,
    parse_has_mana_value_wipe,
    parse_has_sacrifice_for_damage,
)


class TestParseHasCastTrigger:
    def test_when_you_cast_is_cast_trigger(self):
        # Amulet of Vigor
        assert parse_has_cast_trigger(
            "Whenever a permanent enters the battlefield tapped, "
            "its controller untaps it."
        ) is False

    def test_exact_when_you_cast_phrasing(self):
        # Goblin Electromancer / Storm creatures
        assert parse_has_cast_trigger(
            "When you cast this spell, you may return target creature "
            "to its owner's hand."
        ) is True

    def test_cascade_cast_trigger(self):
        # Bloodbraid Elf
        assert parse_has_cast_trigger(
            "Cascade (When you cast this spell, exile cards from the top "
            "of your library until you exile a nonland card that costs "
            "less. You may cast it without paying its mana cost.)"
        ) is True

    def test_empty_oracle_is_false(self):
        assert parse_has_cast_trigger("") is False
        assert parse_has_cast_trigger(None) is False

    def test_non_cast_trigger_is_false(self):
        assert parse_has_cast_trigger("Counter target spell.") is False


class TestParseHasRecurringTrigger:
    def test_at_beginning_of_upkeep_is_recurring(self):
        assert parse_has_recurring_trigger(
            "At the beginning of your upkeep, draw a card."
        ) is True

    def test_whenever_creature_attacks_is_recurring(self):
        assert parse_has_recurring_trigger(
            "Whenever a creature attacks, it gets +1/+0 until end of turn."
        ) is True

    def test_whenever_you_cast_is_recurring(self):
        # Baral, Chief of Compliance
        assert parse_has_recurring_trigger(
            "Whenever you cast an instant or sorcery spell, "
            "you may draw a card."
        ) is True

    def test_no_trigger_is_false(self):
        assert parse_has_recurring_trigger("Counter target spell.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_recurring_trigger("") is False
        assert parse_has_recurring_trigger(None) is False


class TestParseHasManaValueWipe:
    def test_wrath_of_skies_pattern_is_wipe(self):
        # Wrath of the Skies / scaled EE variant
        assert parse_has_mana_value_wipe(
            "Destroy each artifact, creature, and enchantment with mana value "
            "less than or equal to the amount of {E} paid this way."
        ) is True

    def test_regular_wrath_is_false(self):
        # Standard Wrath of God doesn't have 'destroy each ... mana value'
        assert parse_has_mana_value_wipe(
            "Destroy all creatures. They can't be regenerated."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_mana_value_wipe("") is False
        assert parse_has_mana_value_wipe(None) is False


class TestParseHasSacrificeForDamage:
    def test_goblin_bombardment_pattern(self):
        # Goblin Bombardment
        assert parse_has_sacrifice_for_damage(
            "Sacrifice a creature: Goblin Bombardment deals 1 damage to "
            "any target."
        ) is True

    def test_blasting_station_pattern(self):
        # Blasting Station
        assert parse_has_sacrifice_for_damage(
            "{T}, Sacrifice a creature: Blasting Station deals 1 damage "
            "to any target."
        ) is True

    def test_sacrifice_without_damage_is_false(self):
        # Altar of Dementia sacrifices for mill, not damage
        assert parse_has_sacrifice_for_damage(
            "Sacrifice a creature: Target player mills cards equal to "
            "the sacrificed creature's power."
        ) is False

    def test_damage_without_sacrifice_is_false(self):
        assert parse_has_sacrifice_for_damage(
            "Deal 3 damage to any target."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_sacrifice_for_damage("") is False
        assert parse_has_sacrifice_for_damage(None) is False
