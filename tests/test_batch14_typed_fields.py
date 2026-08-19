"""Batch 14 typed-field migration tests.

# Mechanic: cost reducer (rule, not a card)

is_cost_reducer replaces 'cost {' in oracle in ev_player.py stacks
check — cost-reduction effects make duplicate copies useful.

# Mechanic: recurring trigger (rule, not a card)

has_recurring_trigger replaces 'whenever' in oracle in ev_player.py
stacks check — triggered permanents generate value from every copy.

# Mechanic: token creation (rule, not a card)

'token_maker' in tags replaces 'create'/'token' in oracle in
evaluator.py _ability_bonus — cards tagged token_maker create tokens
and the tag is derived from the oracle text at load time.

Card names appear only as fixture carriers in comments.
"""
from __future__ import annotations
import pytest
from engine.oracle_parser import (
    parse_has_recurring_trigger,
)


class TestIsCostReducerTagDrivenField:
    """Pins that is_cost_reducer is set from the 'cost_reducer' tag (derived
    from oracle at load time) — it replaces 'cost {' in oracle in ev_player.py
    stacks check."""

    def test_is_cost_reducer_true_on_cost_reducing_cards(self):
        """Cards that reduce spell costs should have is_cost_reducer=True via
        the cost_reducer tag, which is set by the oracle tagger."""
        from engine.card_database import CardDatabase
        db = CardDatabase('ModernAtomic.json')
        reducers = [
            t for t in db.cards.values()
            if t and getattr(t, 'is_cost_reducer', False)
        ]
        assert len(reducers) > 20, (
            f"Expected dozens of cost-reducer cards in Modern; got {len(reducers)}"
        )

    def test_is_cost_reducer_false_on_plain_creature(self):
        from engine.card_database import CardDatabase
        db = CardDatabase('ModernAtomic.json')
        bolt = db.cards.get('Lightning Bolt')
        if bolt:
            assert not getattr(bolt, 'is_cost_reducer', False)


class TestHasRecurringTriggerStacksDetection:
    """Pins the replacement of 'whenever' in oracle in ev_player.py stacks
    check — triggered permanents give value from each additional copy."""

    def test_whenever_triggers(self):
        assert parse_has_recurring_trigger(
            "Whenever you cast a noncreature spell, this creature gets +1/+1 "
            "until end of turn."
        ) is True

    def test_at_beginning_triggers(self):
        assert parse_has_recurring_trigger(
            "At the beginning of your upkeep, draw a card."
        ) is True

    def test_static_ability_no_trigger(self):
        assert parse_has_recurring_trigger(
            "Creatures you control get +1/+1."
        ) is False

    def test_empty_oracle_is_false(self):
        assert parse_has_recurring_trigger("") is False
        assert parse_has_recurring_trigger(None) is False


class TestTokenMakerTagReplacesOracleCheck:
    """Documents that 'token_maker' tag replaces 'create' + 'token' in oracle
    in evaluator._ability_bonus — the tag is the pre-parsed form."""

    def test_token_creation_oracle_is_tagged(self):
        """Cards with 'create ... token' oracle are tagged 'token_maker'
        by the tagger at load time — the tag IS the oracle check."""
        from engine.card_database import CardDatabase
        db = CardDatabase('ModernAtomic.json')
        token_creators = [
            t for t in db.cards.values()
            if t and 'create' in (t.oracle_text or '').lower()
            and 'token' in (t.oracle_text or '').lower()
        ]
        # At least some must have the token_maker tag
        tagged = [t for t in token_creators if 'token_maker' in (t.tags or set())]
        assert len(tagged) > 100, (
            "Expected hundreds of token creators tagged token_maker; "
            f"got {len(tagged)}"
        )

    def test_non_token_cards_not_tagged(self):
        from engine.card_database import CardDatabase
        db = CardDatabase('ModernAtomic.json')
        # A card without 'create...token' pattern should not have token_maker
        bolt = db.cards.get('Lightning Bolt')
        if bolt:
            assert 'token_maker' not in (bolt.tags or set())
