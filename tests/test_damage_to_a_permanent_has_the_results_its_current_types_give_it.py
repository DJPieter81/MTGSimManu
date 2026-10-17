"""Damage dealt to a permanent has the results its current types give it
(CR 120.3c, 120.3e, 712.8, 613.1d).

Damage dealt to a planeswalker removes that many loyalty counters from it
(CR 120.3c); damage dealt to a creature is marked on it (CR 120.3e). A
permanent that is both takes both results. The types are the ones the
permanent has now: a transformed double-faced permanent has only its back
face's (CR 712.8), and an effect can make a permanent a creature (CR 613.1d;
an animated land, a type an effect adds).

The defect: `CardInstance.take_damage`, the damage target protocol
`engine/damage.deal_damage` calls, routed on `template.card_types` -- the
front face's printed types. A transformed Ajani, Nacatl Avenger took 3
damage as a creature (3 damage marked, 3 loyalty kept), so attacks and burn
never reduced a transformed walker; a creature back face over a noncreature
front (Reflection of Kiki-Jiki) and an animated land marked nothing and
never died to damage. The energy-damage clause (Galvanic Discharge) read the
printed type the same way.

Class: the 121 transforming double-faced cards whose faces differ in
creature or planeswalker type, every animated land, and every permanent an
effect makes a creature. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import (CardInstance, CardTemplate, CardType, ManaCost)
from engine.combat_manager import CombatManager
from engine.damage import deal_damage
from engine.game_state import GameState, Phase

AJANI = "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger"
TAMIYO = "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar"
RAL = "Ral, Monsoon Mage // Ral, Leyline Prodigy"
FABLE = "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _put(game, card_db, name, controller=0):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _creature(game, controller, power=3, toughness=3):
    tmpl = CardTemplate(
        name="Source", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None, keywords=set(),
        abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="battlefield")
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _transformed(game, card_db, name, controller=0):
    from engine.oracle_resolver import _transform_permanent
    perm = _put(game, card_db, name, controller)
    _transform_permanent(game, perm, controller)
    return perm


# ── A walker back face (CR 120.3c, 712.8) ─────────────────────────────

@pytest.mark.parametrize("name", [AJANI, TAMIYO, RAL],
                         ids=["ajani", "tamiyo", "ral"])
def test_damage_to_a_walker_back_face_removes_loyalty_and_marks_nothing(
        card_db, name):
    game = _game()
    walker = _transformed(game, card_db, name)
    loyalty = walker.loyalty_counters
    deal_damage(_creature(game, 1), walker, 1)
    assert walker.loyalty_counters == loyalty - 1
    assert walker.damage_marked == 0


def test_a_walker_back_face_dealt_its_loyalty_in_damage_dies(card_db):
    game = _game()
    walker = _transformed(game, card_db, AJANI)
    deal_damage(_creature(game, 1), walker, walker.loyalty_counters)
    game.check_state_based_actions()
    assert walker.zone == "graveyard"


def test_an_attacker_assigned_to_a_walker_back_face_removes_its_loyalty(
        card_db):
    game = _game()
    attacker = _creature(game, 0, power=2, toughness=2)
    walker = _transformed(game, card_db, AJANI, controller=1)
    loyalty = walker.loyalty_counters
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], 0,
                         attack_targets={attacker.instance_id: walker})
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert walker.loyalty_counters == loyalty - 2


def test_damage_to_a_front_face_creature_is_marked_as_before(card_db):
    game = _game()
    ajani = _put(game, card_db, AJANI)                  # front face up
    deal_damage(_creature(game, 1), ajani, 1)
    assert ajani.damage_marked == 1


# ── A creature back face, an animated land (CR 120.3e, 613.1d) ────────

def test_a_creature_back_face_over_a_noncreature_front_is_marked_and_dies(
        card_db):
    game = _game()
    reflection = _transformed(game, card_db, FABLE)
    assert reflection.effective_is_creature
    deal_damage(_creature(game, 1), reflection, 1)
    assert reflection.damage_marked == 1
    deal_damage(_creature(game, 1), reflection, 1)
    game.check_state_based_actions()
    assert reflection.zone == "graveyard"


def test_an_animated_land_is_marked_and_dies_to_lethal_damage(card_db):
    from engine.permanent_effects import PermanentEffects
    game = _game()
    _put(game, card_db, "Island")                        # pays the {1}
    land = _put(game, card_db, "Mutavault")
    assert PermanentEffects.animate_land(game, 0, land)
    deal_damage(_creature(game, 1), land, land.toughness)
    assert land.damage_marked == land.toughness
    game.check_state_based_actions()
    assert land.zone == "graveyard"


def test_a_permanent_that_is_both_a_creature_and_a_walker_takes_both_results(
        card_db):
    game = _game()
    walker = _put(game, card_db, "Teferi, Time Raveler")
    walker.loyalty_counters = 4
    walker.cem_types_added = {CardType.CREATURE}      # an effect made it one
    deal_damage(_creature(game, 1), walker, 2)
    assert walker.loyalty_counters == 2
    assert walker.damage_marked == 2


# ── The energy-damage clause reads the same types ─────────────────────

def test_the_energy_damage_clause_removes_a_walker_back_faces_loyalty(
        card_db):
    from engine.clause_resolver import resolve_clause
    game = _game()
    walker = _transformed(game, card_db, TAMIYO, controller=1)
    spell = CardInstance(template=card_db.get_card("Galvanic Discharge"),
                         owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    assert resolve_clause(game, spell, 0, targets=[walker.instance_id])
    game.check_state_based_actions()
    assert walker.zone == "graveyard"
    assert walker.damage_marked == 0


# ── Auditor (CR 120.3) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _printed_type_routing(self, amount, source):          # the defect
    if CardType.CREATURE in self.template.card_types:
        self.damage_marked += amount
        return
    if CardType.PLANESWALKER in self.template.card_types:
        self.loyalty_counters -= amount


def test_the_audit_records_damage_routed_by_the_printed_face(
        card_db, audit, monkeypatch):
    monkeypatch.setattr(CardInstance, "take_damage", _printed_type_routing)
    game = _game()
    walker = _transformed(game, card_db, AJANI)
    deal_damage(_creature(game, 1), walker, 1)
    assert "120.3/damage_result" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_damage_follows_the_current_types(
        card_db, audit):
    game = _game()
    deal_damage(_creature(game, 1), _transformed(game, card_db, AJANI), 1)
    deal_damage(_creature(game, 1), _transformed(game, card_db, FABLE), 1)
    deal_damage(_creature(game, 1), _put(game, card_db, AJANI), 1)
    assert "120.3/damage_result" not in [f["rule"] for f in audit.drain()]
