"""
CardTemplate.has_attack_trigger typed field (oracle ratchet batch 2).

Cards with a self-referential on-attack trigger contain one of:
  - "Whenever this creature attacks"  (generic self-referential form)
  - "Whenever [Card Name] attacks"    (legendary/self-named form)

Cards whose attack-adjacuent text belongs to *other* objects —
"Whenever a creature attacks", "Whenever a creature attacks you",
"Whenever you attack with a creature" — do NOT set this flag; those
trigger on any attacker, not specifically on the card itself.

Fixture card names appear in the module docstring only, never in
test function names (per repo convention).
"""
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


class TestHasAttackTrigger:
    def test_generic_self_referential_form_detected(self):
        # "Whenever this creature attacks" — covers Goblin Rabblemaster, Hero of Bladehold, etc.
        t = _template("Whenever this creature attacks, create a 1/1 red Goblin creature token.")
        assert t.has_attack_trigger is True

    def test_self_named_legendary_form_detected(self):
        # "Whenever [Name] attacks" — covers Ragavan, Satoru Umezawa, etc.
        t = _template(
            "Whenever Ragavan attacks, surveil 1.",
            name="Ragavan, Nimble Pilferer",
        )
        assert t.has_attack_trigger is True

    def test_self_named_dfc_first_face(self):
        # DFCs use 'name.split(" // ")[0]' for the self-name lookup
        t = _template(
            "Whenever Valki attacks, that player discards a card.",
            name="Valki, God of Lies // Tibalt, Cosmic Impostor",
        )
        assert t.has_attack_trigger is True

    def test_other_creature_attacks_trigger_excluded(self):
        # "Whenever a creature attacks you" is a defensive trigger — not self
        t = _template("Whenever a creature attacks you, gain 1 life.")
        assert t.has_attack_trigger is False

    def test_whenever_creature_attacks_without_this_excluded(self):
        # "Whenever a creature attacks" — triggers for all attackers, not self
        t = _template("Whenever a creature attacks, it gets +1/+0 until end of turn.")
        assert t.has_attack_trigger is False

    def test_no_attack_clause_is_false(self):
        t = _template("Whenever this creature dies, draw a card.")
        assert t.has_attack_trigger is False

    def test_empty_oracle_is_false(self):
        t = _template("")
        assert t.has_attack_trigger is False
