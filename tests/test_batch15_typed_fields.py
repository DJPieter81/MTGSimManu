"""Batch 15 typed-field migration tests.

# Mechanic: scaling effect (rule, not a card)

has_scaling_effect replaces 'for each'/'for every' in oracle in
ev_player.py stacks check — scaling cards provide independent value
from each copy.

# Mechanic: self trigger (rule, not a card)

has_self_trigger replaces 'when this' in oracle in ev_player.py stacks
check — each copy fires its own 'when this enters/attacks/dies' trigger.

# Mechanic: recurring draw trigger (rule, not a card)

has_recurring_draw_trigger replaces 'whenever' + 'draw' in oracle in
evaluator.py _ability_bonus — draw engines with recurring triggers get
a bonus for repeatable card advantage.

# Mechanic: each-opponent effect (rule, not a card)

has_each_opponent_effect replaces 'each opponent'/'each player' in oracle
in evaluator.py _ability_bonus — effects that hit every opponent scale
better in multiplayer and are unconditionally efficient in 1v1.

# Mechanic: pump grant (rule, not a card)

has_pump_grant replaces 'gets +'/'additional +' in oracle in evaluator.py
_ability_bonus — cards that grant +X/+Y bonuses provide scaling utility.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_scaling_effect,
    parse_has_self_trigger,
    parse_has_recurring_draw_trigger,
    parse_has_each_opponent_effect,
    parse_has_pump_grant,
)


class TestHasScalingEffectStacksDetection:
    """Pins that has_scaling_effect detects 'for each'/'for every' patterns,
    replacing runtime oracle checks in ev_player.py stacks detection."""

    def test_for_each_permanent_is_scaling(self):
        # Goblin Grenade / Affinity / Tarmogoyf-style patterns
        assert parse_has_scaling_effect(
            "This creature gets +1/+1 for each artifact you control."
        ) is True

    def test_for_every_keyword(self):
        assert parse_has_scaling_effect(
            "Deals 1 damage for every creature that attacked this turn."
        ) is True

    def test_static_no_scaling(self):
        assert parse_has_scaling_effect(
            "Flying. When this creature enters the battlefield, draw a card."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_scaling_effect("") is False
        assert parse_has_scaling_effect(None) is False


class TestHasSelfTriggerStacksDetection:
    """Pins that has_self_trigger detects 'when this' self-referential trigger
    patterns, replacing runtime oracle check in ev_player.py stacks check."""

    def test_etb_trigger_is_self_trigger(self):
        # "When this enters" (ETB)
        assert parse_has_self_trigger(
            "When this creature enters the battlefield, create a 1/1 token."
        ) is True

    def test_when_this_attacks_is_self_trigger(self):
        assert parse_has_self_trigger(
            "When this creature attacks, draw a card."
        ) is True

    def test_when_this_dies_is_self_trigger(self):
        assert parse_has_self_trigger(
            "When this creature dies, return it to your hand."
        ) is True

    def test_flying_static_no_self_trigger(self):
        assert parse_has_self_trigger("Flying.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_self_trigger("") is False
        assert parse_has_self_trigger(None) is False


class TestHasRecurringDrawTriggerEvalBonus:
    """Pins the replacement of 'whenever' + 'draw' in oracle in evaluator.py
    _ability_bonus — repeatable draw engines get a recurring-draw bonus."""

    def test_whenever_you_cast_draw_is_recurring_draw(self):
        # Rhystic Study / Mystic Remora pattern
        assert parse_has_recurring_draw_trigger(
            "Whenever an opponent casts a spell, you may pay {1}. "
            "If you don't, draw a card."
        ) is True

    def test_whenever_attacks_draw_is_recurring_draw(self):
        assert parse_has_recurring_draw_trigger(
            "Whenever this creature attacks, draw a card."
        ) is True

    def test_draw_without_whenever_is_false(self):
        # One-shot draw (Divination)
        assert parse_has_recurring_draw_trigger("Draw two cards.") is False

    def test_whenever_without_draw_is_false(self):
        # Recurring trigger but no draw
        assert parse_has_recurring_draw_trigger(
            "Whenever you cast a spell, create a 1/1 token."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_recurring_draw_trigger("") is False
        assert parse_has_recurring_draw_trigger(None) is False


class TestHasEachOpponentEffectDamageBonus:
    """Pins the replacement of 'each opponent'/'each player' in oracle in
    evaluator.py _ability_bonus — effects that hit all opponents score higher."""

    def test_each_opponent_is_true(self):
        # Grapeshot / Guttersnipe pattern hitting each opponent
        assert parse_has_each_opponent_effect(
            "Deals 1 damage to each opponent."
        ) is True

    def test_each_player_is_true(self):
        # Symmetric effects (Burning of Xinye, etc.)
        assert parse_has_each_opponent_effect(
            "Each player draws a card."
        ) is True

    def test_single_target_damage_is_false(self):
        # Lightning Bolt — targets one player
        assert parse_has_each_opponent_effect(
            "Deals 3 damage to any target."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_each_opponent_effect("") is False
        assert parse_has_each_opponent_effect(None) is False


class TestHasPumpGrantAbiBonus:
    """Pins the replacement of 'gets +'/'additional +' in oracle in evaluator.py
    _ability_bonus pump detection — pump grants increase creature utility."""

    def test_gets_plus_pump_is_true(self):
        # Giant Growth / Blossoming Defense pattern
        assert parse_has_pump_grant(
            "Target creature gets +2/+2 until end of turn."
        ) is True

    def test_additional_plus_pump_is_true(self):
        # Double strike / ferocious "additional +3/+0" patterns
        assert parse_has_pump_grant(
            "If you control a creature with power 4 or greater, "
            "that creature gets an additional +3/+0."
        ) is True

    def test_static_toughness_no_pump_grant(self):
        # "+1/+1 counter" starts with + so not in this field's scope
        assert parse_has_pump_grant(
            "This creature enters with a +1/+1 counter."
        ) is False

    def test_flying_text_no_pump_grant(self):
        assert parse_has_pump_grant("Flying. Lifelink.") is False

    def test_empty_oracle_is_false(self):
        assert parse_has_pump_grant("") is False
        assert parse_has_pump_grant(None) is False
