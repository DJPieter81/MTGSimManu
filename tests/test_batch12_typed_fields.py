"""Batch 12 typed-field migration tests.

# Mechanic: graveyard ETB prevention (rule, not a card)

parse_prevents_graveyard_etb fires for Grafdigger's Cage pattern:
'creature cards in graveyards' + "can't enter the battlefield".

# Mechanic: creature targeting requirement (rule, not a card)

parse_requires_creature_target fires when oracle says 'target creature'
or 'creature spell'.

# Mechanic: alternate exile cost (rule, not a card)

parse_has_alternate_exile_cost fires for 'exile a' + 'rather than pay'
alternate-cost pattern (Grief / Solitude / Ephemerate elementals).

# Existing fields used by consumers (no new parse functions)

- `produces_mana` (engine/callbacks.py mana heuristic)
- `equip_cost` (engine/callbacks.py artifact-scaler heuristic)
- `has_graveyard_hate` (engine/game_state.py cast-from-GY ban source)

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_prevents_graveyard_etb,
    parse_requires_creature_target,
    parse_has_alternate_exile_cost,
)


class TestParsePreventsGraveyardEtb:
    def test_grafdigger_cage_pattern(self):
        # Grafdigger's Cage
        assert parse_prevents_graveyard_etb(
            "Creature cards in graveyards and libraries can't enter "
            "the battlefield."
        ) is True

    def test_creature_cards_in_graveyards_cant_etb(self):
        assert parse_prevents_graveyard_etb(
            "Creature cards in graveyards can't enter the battlefield."
        ) is True

    def test_gy_exile_without_etb_block_is_false(self):
        # Relic of Progenitus -- exiles graveyard but no ETB prevention
        assert parse_prevents_graveyard_etb(
            "Each player can exile a card from their graveyard."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_prevents_graveyard_etb("") is False
        assert parse_prevents_graveyard_etb(None) is False


class TestParseRequiresCreatureTarget:
    def test_target_creature_requires_target(self):
        assert parse_requires_creature_target(
            "Destroy target creature."
        ) is True

    def test_creature_spell_requires_target(self):
        # Essence Scatter
        assert parse_requires_creature_target(
            "Counter target creature spell."
        ) is True

    def test_target_player_does_not_trigger(self):
        assert parse_requires_creature_target(
            "Counter target spell."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_requires_creature_target("") is False
        assert parse_requires_creature_target(None) is False


class TestParseHasAlternateExileCost:
    def test_force_of_virtue_pattern_detected(self):
        # Force of Virtue
        assert parse_has_alternate_exile_cost(
            "Exile a white card from your hand rather than pay this spell's "
            "mana cost."
        ) is True

    def test_force_of_negation_pattern_detected(self):
        # Force of Negation
        assert parse_has_alternate_exile_cost(
            "If it's not your turn, you may exile a blue card from your hand "
            "rather than pay this spell's mana cost."
        ) is True

    def test_regular_spell_is_false(self):
        assert parse_has_alternate_exile_cost(
            "Destroy target creature."
        ) is False

    def test_exile_without_rather_than_is_false(self):
        # Leyline Binding exiles, but not as an alternate cost
        assert parse_has_alternate_exile_cost(
            "Exile target nonland nontoken permanent."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_alternate_exile_cost("") is False
        assert parse_has_alternate_exile_cost(None) is False
