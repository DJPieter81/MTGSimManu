"""Typed fields must detect ETB effects that target spells on the stack.

Rule: any permanent whose ETB targets a creature spell or planeswalker spell
on the stack must be represented by pre-computed typed fields rather than
runtime oracle-text substring checks.

The mechanic under test is generic: any card that says 'target creature spell'
in oracle text has targets_creature_spell=True; any card that says
'target planeswalker spell' or 'or planeswalker spell' (chained-clause form)
has targets_planeswalker_spell=True.

Fixture carrier note: the 'or planeswalker spell' form is the chained-clause
variant used by evoke elementals whose ETB reads
"target creature spell or planeswalker spell" (one 'target' keyword, two
spell types). The chained form is structurally different from a card that
says 'target planeswalker spell' in isolation; both must set the flag.
"""
from __future__ import annotations
import pytest
from engine.cards import CardTemplate, CardType
from engine.mana import ManaCost


def _template(oracle: str, name: str = "__test__") -> CardTemplate:
    return CardTemplate(
        name=name,
        oracle_text=oracle,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1),
        supertypes=[],
        subtypes=[],
        keywords=set(),
        abilities=[],
        color_identity=set(),
        produces_mana=[],
        enters_tapped=False,
        tags=set(),
    )


class TestTargetsCreatureSpell:
    def test_target_creature_spell_phrase_sets_flag(self):
        # "Counter target creature spell." — Essence Scatter / Bone to Ash pattern
        t = _template("Counter target creature spell.")
        assert t.targets_creature_spell is True

    def test_chained_clause_creature_spell_sets_flag(self):
        # "target creature spell or planeswalker spell" — evoke ETB chained form
        t = _template(
            "When this creature enters, choose up to one target creature spell "
            "or planeswalker spell. Its owner puts it on their choice of the top "
            "or bottom of their library."
        )
        assert t.targets_creature_spell is True

    def test_plain_target_creature_without_spell_does_not_set_flag(self):
        # "target creature" hits the battlefield, not the stack
        t = _template("When this creature enters, destroy target creature.")
        assert t.targets_creature_spell is False

    def test_no_oracle_matching_is_false(self):
        t = _template("Flying.\nWhenever this creature attacks, draw a card.")
        assert t.targets_creature_spell is False

    def test_empty_oracle_is_false(self):
        t = _template("")
        assert t.targets_creature_spell is False


class TestTargetsPlaneswalkerSpell:
    def test_target_planeswalker_spell_phrase_sets_flag(self):
        # Hypothetical: "Counter target planeswalker spell." (direct form)
        t = _template("Counter target planeswalker spell.")
        assert t.targets_planeswalker_spell is True

    def test_chained_or_planeswalker_spell_sets_flag(self):
        # "or planeswalker spell" — Subtlety-style chained clause:
        # "target creature spell or planeswalker spell"
        t = _template(
            "When this creature enters, choose up to one target creature spell "
            "or planeswalker spell. Its owner puts it on their choice of the top "
            "or bottom of their library."
        )
        assert t.targets_planeswalker_spell is True

    def test_plain_target_planeswalker_without_spell_does_not_set_flag(self):
        # "target creature or planeswalker" hits the battlefield
        t = _template("This spell deals 3 damage to target creature or planeswalker.")
        assert t.targets_planeswalker_spell is False

    def test_target_creature_spell_only_does_not_set_planeswalker_flag(self):
        # "target creature spell" alone should not set the planeswalker flag
        t = _template("Counter target creature spell.")
        assert t.targets_planeswalker_spell is False

    def test_no_oracle_matching_is_false(self):
        t = _template("Flying.\nWhenever this creature attacks, draw a card.")
        assert t.targets_planeswalker_spell is False

    def test_empty_oracle_is_false(self):
        t = _template("")
        assert t.targets_planeswalker_spell is False
