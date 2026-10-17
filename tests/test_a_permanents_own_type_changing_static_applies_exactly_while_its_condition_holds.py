"""A permanent's own static ability that changes its card types applies
exactly while its condition holds (CR 613.1d, 613.4b, 611.3a, 205.1a-b).

The ability is a characteristic-changing effect on the permanent itself. It
sets the permanent's card types, and with them its subtypes, its base power
and toughness (layer 7b) and the abilities it lists; or it adds a type; or it
says the permanent isn't a type. A type it sets replaces the printed ones
unless the text says the permanent is "still" one (CR 205.1a, 205.1b). It
applies only on the battlefield and only while its printed condition holds:
during its controller's turn, while it has counters, while its controller
controls enough of something, while its controller's devotion is below a
number (CR 700.5).

The defect: no part of the layer system read these abilities. Kaito, Bane of
Nightmares was never the 3/4 hexproof Ninja his text makes him during his
controller's turn, so he never attacked (the Dimir vs Azorius Control (WST
v2) trace, s50000 game 1). A god was a creature whatever its controller's
devotion.

Class: the self type-setting statics the grammar types (Kaito, Grand Master
of Flowers, Ezio, Skyknight Squire, Phoenix Fleet Airship, Rusted Relic),
Gideon Blackblade's "that's still a planeswalker", and the 22 gods'
"as long as your devotion to <colour> is less than N, ~ isn't a creature".
Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import (CardInstance, CardTemplate, CardType, Keyword,
                          ManaCost)
from engine.combat_manager import CombatManager
from engine.damage import deal_damage
from engine.game_state import GameState, Phase

KAITO = "Kaito, Bane of Nightmares"
GIDEON = "Gideon Blackblade"
HELIOD = "Heliod, God of the Sun"


def _game(active=0):
    game = GameState(rng=random.Random(0))
    game.active_player = active
    game.current_phase = Phase.MAIN1
    return game


def _put(game, card_db, name, controller=0, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        if card.template.loyalty:
            card.loyalty_counters = card.template.loyalty
    getattr(game.players[controller], zone).append(card)
    return card


def _permanent(game, controller, *, white=0, card_types=(CardType.ARTIFACT,),
               hybrid=()):
    tmpl = CardTemplate(
        name="Pips", card_types=list(card_types),
        mana_cost=ManaCost(white=white, hybrid=list(hybrid)), supertypes=[],
        subtypes=[], power=None, toughness=None, loyalty=None, keywords=set(),
        abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="battlefield")
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _recalc(game):
    game.continuous_effects.recalculate(game)


# ── During its controller's turn (Kaito) ──────────────────────────────

def test_during_its_controllers_turn_it_has_the_types_and_base_pt_it_sets(
        card_db):
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    assert kaito.effective_card_types == [CardType.CREATURE]
    assert "Ninja" in kaito.effective_subtypes
    assert (kaito.power, kaito.toughness) == (3, 4)
    assert Keyword.HEXPROOF in kaito.keywords
    assert not kaito.effective_is_planeswalker


def test_during_another_players_turn_it_has_its_printed_types(card_db):
    game = _game(active=1)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    assert kaito.effective_card_types == [CardType.PLANESWALKER]
    assert not kaito.effective_is_creature
    assert Keyword.HEXPROOF not in kaito.keywords


def test_a_condition_on_whose_turn_it_is_changes_as_the_next_turn_begins(
        card_db):
    """CR 611.3a, 502: the effect follows its condition at all times, and
    the turn changes as the next turn's untap step begins -- before any
    player could see the old one."""
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    assert kaito.effective_is_creature
    game.active_player = 1
    game.untap_step(1)
    assert not kaito.effective_is_creature
    assert kaito.effective_is_planeswalker


def test_it_applies_only_while_every_part_of_its_condition_holds(card_db):
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    kaito.loyalty_counters = 0            # "one or more loyalty counters"
    _recalc(game)
    assert not kaito.effective_is_creature


def test_off_the_battlefield_the_ability_changes_nothing(card_db):
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO, zone="hand")
    _recalc(game)
    assert kaito.effective_card_types == [CardType.PLANESWALKER]


def test_a_permanent_it_makes_a_creature_attacks_once_controlled_since_its_turn_began(
        card_db):
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    assert kaito.can_attack
    life = game.players[1].life
    cm = CombatManager()
    cm.declare_attackers(game, [kaito], 0)
    cm.declare_blockers(game, {})
    cm.resolve_combat_damage(game)
    assert game.players[1].life == life - 3


def test_damage_to_it_while_it_is_only_a_creature_is_marked_and_removes_no_loyalty(
        card_db):
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    loyalty = kaito.loyalty_counters
    deal_damage(_permanent(game, 1), kaito, 2)
    assert kaito.damage_marked == 2
    assert kaito.loyalty_counters == loyalty


def test_its_loyalty_abilities_can_still_be_activated(card_db):
    from engine.planeswalker_manager import PlaneswalkerManager
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    assert kaito.effective_is_creature
    assert kaito in PlaneswalkerManager.loyalty_permanents(game.players[0])


# ── A type it is still (Gideon), a type it isn't (gods) ───────────────

def test_a_type_the_text_says_it_is_still_is_kept(card_db):
    game = _game(active=0)
    gideon = _put(game, card_db, GIDEON)
    _recalc(game)
    assert set(gideon.effective_card_types) == {CardType.CREATURE,
                                                CardType.PLANESWALKER}
    assert (gideon.power, gideon.toughness) == (4, 4)
    assert Keyword.INDESTRUCTIBLE in gideon.keywords


def test_a_permanent_isnt_a_creature_while_its_devotion_is_below_the_number(
        card_db):
    game = _game(active=0)
    heliod = _put(game, card_db, HELIOD)                 # {3}{W}: devotion 1
    _recalc(game)
    assert not heliod.effective_is_creature
    assert CardType.ENCHANTMENT in heliod.effective_card_types
    _permanent(game, 0, white=4)                          # devotion 5
    _recalc(game)
    assert heliod.effective_is_creature


def test_devotion_counts_every_pip_of_the_colour_hybrid_and_phyrexian_included():
    from engine.effect_conditions import devotion
    game = _game(active=0)
    _permanent(game, 0, white=2)                         # {W}{W}
    _permanent(game, 0, hybrid=[("W", "U")])             # {W/U}
    _permanent(game, 0, hybrid=[("W", "2")])             # {2/W}
    _permanent(game, 1, white=3)                         # not controlled
    assert devotion(game, 0, frozenset({"W"})) == 4
    assert devotion(game, 0, frozenset({"U"})) == 1
    assert devotion(game, 0, frozenset({"W", "U"})) == 4


# ── Counts and counters ───────────────────────────────────────────────

def test_a_count_condition_sets_the_types_while_it_holds(card_db):
    game = _game(active=0)
    relic = _put(game, card_db, "Rusted Relic")
    _permanent(game, 0)
    _recalc(game)
    assert not relic.effective_is_creature                # two artifacts
    _permanent(game, 0)
    _recalc(game)
    assert relic.effective_is_creature                    # three: metalcraft
    assert CardType.ARTIFACT in relic.effective_card_types
    assert (relic.power, relic.toughness) == (5, 5)


def test_a_counter_condition_adds_a_type_and_a_keyword(card_db):
    game = _game(active=0)
    squire = _put(game, card_db, "Skyknight Squire")
    squire.add_plus_counters(2)
    _recalc(game)
    assert "Knight" not in squire.effective_subtypes
    squire.add_plus_counters(1)
    _recalc(game)
    assert "Knight" in squire.effective_subtypes
    assert "Cat" in squire.effective_subtypes             # in addition
    assert Keyword.FLYING in squire.keywords


# ── The grammar types the shapes ──────────────────────────────────────

def _static_specs(text):
    from engine.effect_grammar import parse_effects
    from engine.effect_spec import HostKind, iter_specs
    return list(iter_specs(parse_effects(text.lower(), HostKind.STATIC)))


def test_the_grammar_types_a_permanent_that_isnt_a_type():
    from engine.effect_model import ModKind
    (spec,) = _static_specs("During your turn, ~ isn't a creature.")
    assert spec.payload.kind is ModKind.REMOVE_TYPES
    assert spec.payload.get("types") == ("creature",)


def test_the_grammar_types_a_type_the_permanent_is_still():
    (spec,) = _static_specs(
        "During your turn, ~ is a 4/4 Human Soldier creature with "
        "indestructible that's still a planeswalker.")
    assert spec.payload.get("types") == ("creature",)
    assert spec.payload.get("still") == ("planeswalker",)


def test_the_grammar_types_a_devotion_condition():
    (spec,) = _static_specs(
        "As long as your devotion to white and blue is less than seven, "
        "~ isn't a creature.")
    cond = spec.condition
    assert (cond.pred, cond.op, cond.n.n) == ("devotion", "<", 7)
    assert cond.filter.colors == frozenset({"W", "U"})


# ── Auditor (CR 613.1d) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_held_condition_whose_types_were_not_applied(
        card_db, audit, monkeypatch):
    from engine import continuous_effects
    monkeypatch.setattr(continuous_effects, "_printed_self_type_changes",
                        lambda perm: ())                  # the defect
    game = _game(active=0)
    _put(game, card_db, KAITO)
    _recalc(game)
    assert "613.1d/self_type_change" in [f["rule"] for f in audit.drain()]


def test_the_audit_is_silent_when_the_types_follow_the_condition(
        card_db, audit):
    game = _game(active=0)
    _put(game, card_db, KAITO)
    _put(game, card_db, HELIOD)
    _put(game, card_db, KAITO, controller=1)
    _recalc(game)
    assert "613.1d/self_type_change" not in [f["rule"] for f in audit.drain()]


# ── The damage-result audit reads the types an effect sets or removes ─
# (CR 120.3c, 120.3e, 613.1d): its restated types are the ones the
# permanent has now, so a walker its own static makes only a creature takes
# damage as a creature, and a god that isn't a creature takes none.

def _printed_type_routing(self, amount, source):          # the defect
    if CardType.PLANESWALKER in self.template.card_types:
        self.loyalty_counters -= amount
        return
    if CardType.CREATURE in self.template.card_types:
        self.damage_marked += amount


def test_the_damage_audit_is_silent_for_the_types_a_static_sets_or_removes(
        card_db, audit):
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    heliod = _put(game, card_db, HELIOD)
    _recalc(game)
    assert not heliod.effective_is_creature
    deal_damage(_permanent(game, 1), kaito, 2)
    deal_damage(_permanent(game, 1), heliod, 2)
    assert "120.3/damage_result" not in [f["rule"] for f in audit.drain()]


def test_the_damage_audit_records_damage_routed_by_the_printed_types(
        card_db, audit, monkeypatch):
    monkeypatch.setattr(CardInstance, "take_damage", _printed_type_routing)
    game = _game(active=0)
    kaito = _put(game, card_db, KAITO)
    _recalc(game)
    deal_damage(_permanent(game, 1), kaito, 2)
    assert "120.3/damage_result" in [f["rule"] for f in audit.drain()]
