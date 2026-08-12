"""Batch 7 typed-field oracle-parser unit tests.

Pure-parser tests for five new CardTemplate fields introduced in batch 7:
  has_token_effect          -- create-a-token detection
  has_graveyard_recursion   -- return-from-graveyard detection
  has_discard_effect        -- discard-a-card detection
  is_storm_spell            -- Storm keyword detection
  has_charge_counter_ability -- charge-counter detection

Card examples (by field, for reference only):
  has_token_effect:          Young Pyromancer, Monastery Mentor, Emry
  has_graveyard_recursion:   Goryo's Vengeance, Unburial Rites, Eternal Witness
  has_discard_effect:        Thoughtseize, Inquisition of Kozilek, Liliana of the Veil
  is_storm_spell:            Grapeshot, Empty the Warrens, Brain Freeze
  has_charge_counter_ability: Chalice of the Void, Aether Vial

Each function is tested in isolation (no CardDatabase load).
Pattern C: card names appear only in module docstrings and fixture comments.
"""
import pytest
from engine.oracle_parser import (
    parse_has_token_effect,
    parse_has_graveyard_recursion,
    parse_has_discard_effect,
    parse_is_storm_spell,
    parse_has_charge_counter_ability,
)


class TestHasTokenEffect:
    """parse_has_token_effect covers 'create a token' and related phrasings."""

    def test_create_token_detected(self):
        assert parse_has_token_effect("Create a 1/1 white Soldier creature token.") is True

    def test_creates_multiple_tokens(self):
        assert parse_has_token_effect("Create two 2/2 black Zombie creature tokens.") is True

    def test_put_a_token_detected(self):
        assert parse_has_token_effect(
            "Put a 1/1 red Goblin creature token onto the battlefield."
        ) is True

    def test_no_token_returns_false(self):
        assert parse_has_token_effect("Destroy target creature.") is False

    def test_empty_returns_false(self):
        assert parse_has_token_effect("") is False

    def test_none_like_empty_returns_false(self):
        assert parse_has_token_effect(None) is False  # type: ignore[arg-type]

    def test_token_without_create_or_put_returns_false(self):
        assert parse_has_token_effect("Target token gains flying until end of turn.") is False

    def test_create_without_token_returns_false(self):
        assert parse_has_token_effect("Create three copies of target instant.") is False


class TestHasGraveyardRecursion:
    """parse_has_graveyard_recursion detects return-from-graveyard effects."""

    def test_from_your_graveyard_detected(self):
        assert parse_has_graveyard_recursion(
            "Return target creature card from your graveyard to the battlefield."
        ) is True

    def test_from_their_graveyard_detected(self):
        assert parse_has_graveyard_recursion(
            "Return target card from their graveyard to your hand."
        ) is True

    def test_from_a_graveyard_detected(self):
        assert parse_has_graveyard_recursion(
            "Return target card from a graveyard to its owner's hand."
        ) is True

    def test_from_opponents_graveyard_detected(self):
        assert parse_has_graveyard_recursion(
            "Return target creature card from an opponent's graveyard to the battlefield."
        ) is True

    def test_draw_not_recursion(self):
        assert parse_has_graveyard_recursion("Draw a card.") is False

    def test_graveyard_mention_without_return_not_matched(self):
        assert parse_has_graveyard_recursion(
            "Put the top five cards of your library into your graveyard."
        ) is False

    def test_empty_returns_false(self):
        assert parse_has_graveyard_recursion("") is False

    def test_none_like_empty_returns_false(self):
        assert parse_has_graveyard_recursion(None) is False  # type: ignore[arg-type]


class TestHasDiscardEffect:
    """parse_has_discard_effect detects hand-disruption phrasing."""

    def test_discard_a_card_detected(self):
        assert parse_has_discard_effect("Target player discards a card.") is True

    def test_discards_a_card_third_person_detected(self):
        assert parse_has_discard_effect(
            "Target opponent discards a card, then draws a card."
        ) is True

    def test_discard_your_hand_detected(self):
        assert parse_has_discard_effect("Each player discards their hand.") is True

    def test_discard_two_cards_detected(self):
        assert parse_has_discard_effect(
            "Target player discards two cards at random."
        ) is True

    def test_draw_not_discard(self):
        assert parse_has_discard_effect("Draw a card.") is False

    def test_counter_not_discard(self):
        assert parse_has_discard_effect("Counter target spell.") is False

    def test_empty_returns_false(self):
        assert parse_has_discard_effect("") is False

    def test_none_like_empty_returns_false(self):
        assert parse_has_discard_effect(None) is False  # type: ignore[arg-type]


class TestIsStormSpell:
    """parse_is_storm_spell detects the Storm keyword (CR 702.39)."""

    def test_storm_keyword_in_reminder_text_detected(self):
        assert parse_is_storm_spell(
            "Storm (When you cast this spell, copy it for each spell cast "
            "before it this turn. You may choose new targets for the copies.)"
            "\nDeal 1 damage to any target."
        ) is True

    def test_bare_storm_line_detected(self):
        assert parse_is_storm_spell("Storm\nDeal 1 damage to any target.") is True

    def test_no_storm_returns_false(self):
        assert parse_is_storm_spell("Deal 3 damage to any target.") is False

    def test_empty_returns_false(self):
        assert parse_is_storm_spell("") is False

    def test_none_like_empty_returns_false(self):
        assert parse_is_storm_spell(None) is False  # type: ignore[arg-type]

    def test_brainstorm_substring_not_storm_spell(self):
        # "brainstorm" contains "storm" as a substring but is not the Storm keyword
        assert parse_is_storm_spell(
            "Look at the top three cards of your library, then put two back."
        ) is False


class TestHasChargeCounterAbility:
    """parse_has_charge_counter_ability detects 'charge counter' oracle text."""

    def test_enters_with_charge_counters_detected(self):
        assert parse_has_charge_counter_ability(
            "Chalice of the Void enters the battlefield with X charge counters on it."
        ) is True

    def test_put_charge_counter_detected(self):
        assert parse_has_charge_counter_ability(
            "At the beginning of your upkeep, you may put a charge counter on this artifact."
        ) is True

    def test_remove_charge_counter_detected(self):
        assert parse_has_charge_counter_ability(
            "{T}, Remove a charge counter from this artifact: Return target creature "
            "card from your graveyard to the battlefield."
        ) is True

    def test_no_charge_counter_returns_false(self):
        assert parse_has_charge_counter_ability("Draw a card.") is False

    def test_plus_one_counter_not_charge(self):
        assert parse_has_charge_counter_ability(
            "Put a +1/+1 counter on target creature."
        ) is False

    def test_energy_counter_not_charge(self):
        assert parse_has_charge_counter_ability(
            "You get {E}{E}{E}. Pay {E}: Add {C}."
        ) is False

    def test_empty_returns_false(self):
        assert parse_has_charge_counter_ability("") is False

    def test_none_like_empty_returns_false(self):
        assert parse_has_charge_counter_ability(None) is False  # type: ignore[arg-type]
