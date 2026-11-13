"""A creature's own printed static restriction on blocking or attacking
applies to it while it is on the battlefield (CR 509.1a-b, 508.1c, 611.3a).

"This creature can't block" makes it no legal blocker; "this creature can't
attack or block" makes it neither attacker nor blocker; "this creature can
block only creatures with flying" makes it a legal blocker of a creature with
flying and of no other creature (CR 509.1b: a restriction is an effect that
says a creature can't block, or can't block unless some condition is met).
A token has the abilities its creator gives it (CR 111.3), so the Drone
"with flying and 'This token can block only creatures with flying'" blocks
only flyers.

The defect: the grammar typed "~ can't block" as a prohibition on the object
itself, but nothing read it, and "can block only creatures with flying" had
no lemma. Every such creature blocked anything. The token owner refused a
token granted the ability, so the cast trigger that makes the Drone stayed
on its legacy branch, whose Drone had no text. The AI's block filters copied
only the flying rule.

Class: the unconditional restrictions a creature prints on itself, typed at
load: "~ can't block" (70 pool cards), "~ can't attack or block" (1), "~ can
block only creatures with flying" (20); 30 cards grant one of them to a token
they create. Registered: Pinnacle Emissary (Affinity x4, Pinnacle Affinity
x4). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, Keyword
from engine.game_state import GameState, Phase
from engine.mana import ManaCost


def _template(oracle, *, power=2, toughness=2, keywords=()):
    return CardTemplate(
        name="Restriction Probe", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(), supertypes=[], subtypes=[], power=power,
        toughness=toughness, loyalty=None, keywords=set(keywords),
        abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text=oracle, tags=set())


def _put(game, template, controller, zone="battlefield"):
    card = CardInstance(template=template, owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _add(game, card_db, name, controller, zone="battlefield"):
    return _put(game, card_db.get_card(name), controller, zone)


def _game(active=0, phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = active
    game.current_phase = phase
    game.turn_number = 5
    return game


CANT_BLOCK = "This creature can't block."
CANT_ATTACK_OR_BLOCK = "This creature can't attack or block."
ONLY_FLYERS = "Flying\nThis creature can block only creatures with flying."


# ── Typed at load ──────────────────────────────────────────────────────

def test_can_block_only_creatures_with_a_keyword_is_typed_as_a_prohibition():
    from engine.effect_model import Modification, ModKind
    from engine.effect_spec import HostKind, RefKind, Verb, iter_specs
    tmpl = _template(ONLY_FLYERS, keywords={Keyword.FLYING})
    (spec,) = [s for h in tmpl.effects.front() if h.kind is HostKind.STATIC
               for s in iter_specs(h.specs)]
    assert spec.verb is Verb.CONTINUOUS and not spec.residue
    assert isinstance(spec.payload, Modification)
    assert spec.payload.kind is ModKind.PROHIBIT
    assert spec.payload.action == "block"
    assert spec.payload.get("only_with") == ("flying",)
    assert spec.ref.kind is RefKind.SELF


@pytest.mark.parametrize("oracle, records", [
    (CANT_BLOCK, (("block", ()),)),
    (CANT_ATTACK_OR_BLOCK, (("attack", ()), ("block", ()))),
    (ONLY_FLYERS, (("block", (Keyword.FLYING,)),)),
    # A condition, or another object's restriction, is not this reader's.
    ("This creature can't block unless you control another Wolf.", ()),
    ("Enchant creature\nEnchanted creature can't attack or block.", ()),
])
def test_a_permanents_own_restrictions_are_read_from_its_text(oracle,
                                                              records):
    from engine.continuous_effects import printed_object_restrictions
    game = _game()
    card = _put(game, _template(oracle), 0)
    assert printed_object_restrictions(card) == records


# ── The engine ─────────────────────────────────────────────────────────

def test_a_creature_that_cant_block_is_no_legal_blocker(card_db):
    from engine.combat_manager import CombatManager
    game = _game()
    ghast = _add(game, card_db, "Bloodghast", 0)
    assert ghast.can_block is False
    assert ghast not in CombatManager.valid_blockers(game, 0)


def test_a_creature_that_cant_attack_or_block_does_neither():
    game = _game()
    card = _put(game, _template(CANT_ATTACK_OR_BLOCK), 0)
    assert card.can_attack is False
    assert card.can_block is False


def test_a_creature_that_blocks_only_flyers_blocks_a_flyer_and_nothing_else(
        card_db):
    from engine.combat_manager import CombatManager
    game = _game()
    elemental = _add(game, card_db, "Cloud Elemental", 0)
    angel = _add(game, card_db, "Serra Angel", 1)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    assert elemental.can_block
    assert CombatManager._can_block(angel, elemental)
    assert not CombatManager._can_block(bears, elemental)


def _combat(game, attackers, blocks):
    from engine.combat_manager import CombatManager
    cm = CombatManager()
    game.current_phase = Phase.DECLARE_ATTACKERS
    cm.declare_attackers(game, list(attackers), game.active_player)
    cm.declare_blockers(game, blocks)
    return cm


def test_declare_blockers_drops_a_block_the_restriction_forbids(card_db):
    game = _game(active=1)
    elemental = _add(game, card_db, "Cloud Elemental", 0)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    cm = _combat(game, [bears], {bears.instance_id: [elemental.instance_id]})
    (assignment,) = cm._assignments
    assert assignment.blocker_ids == []
    assert assignment.is_blocked is False


def test_a_token_granted_the_restriction_blocks_only_flyers(card_db):
    from engine.combat_manager import CombatManager
    game = _game()
    _add(game, card_db, "Pinnacle Emissary", 0)
    ornithopter = _add(game, card_db, "Ornithopter", 0, zone="hand")
    assert game.cast_spell(0, ornithopter)
    game.resolve_stack()
    drones = [c for c in game.players[0].battlefield
              if "Drone" in (c.template.subtypes or ())]
    assert len(drones) == 1
    (drone,) = drones
    assert CardType.ARTIFACT in drone.template.card_types
    assert Keyword.FLYING in drone.keywords
    bears = _add(game, card_db, "Grizzly Bears", 1)
    angel = _add(game, card_db, "Serra Angel", 1)
    assert not CombatManager._can_block(bears, drone)
    assert CombatManager._can_block(angel, drone)


# ── Auditor (CR 509.1a-b) ──────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f for f in audit.drain() if f["kind"] == "violation"
            and f["rule"] == "509.1a/blocker_legal"]


@pytest.mark.parametrize("name", ["Cloud Elemental", "Bloodghast"])
def test_the_audit_records_a_block_its_restriction_forbids(card_db, audit,
                                                           monkeypatch,
                                                           name):
    from engine import continuous_effects
    monkeypatch.setattr(continuous_effects, "printed_object_restrictions",
                        lambda perm: ())                       # the defect
    game = _game(active=1)
    blocker = _add(game, card_db, name, 0)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    _combat(game, [bears], {bears.instance_id: [blocker.instance_id]})
    assert _violations(audit)


def test_the_audit_is_silent_when_the_restriction_holds(card_db, audit):
    game = _game(active=1)
    elemental = _add(game, card_db, "Cloud Elemental", 0)
    angel = _add(game, card_db, "Serra Angel", 1)
    _combat(game, [angel], {angel.instance_id: [elemental.instance_id]})
    assert not _violations(audit)


# ── The AI reads the rule ──────────────────────────────────────────────

def test_the_blocker_choice_proposes_only_blocks_the_engine_admits(card_db):
    from ai.ev_player import EVPlayer
    from engine.combat_manager import CombatManager
    game = _game(active=1, phase=Phase.DECLARE_BLOCKERS)
    game.players[0].life = 2
    elemental = _add(game, card_db, "Cloud Elemental", 0)
    bears = _add(game, card_db, "Grizzly Bears", 1)
    blocks = EVPlayer(0, "Affinity").decide_blockers(game, [bears])
    for attacker_id, blocker_ids in blocks.items():
        attacker = game.get_card_by_id(attacker_id)
        for bid in blocker_ids:
            assert CombatManager._can_block(attacker,
                                            game.get_card_by_id(bid))
    assert elemental.instance_id not in {b for ids in blocks.values()
                                         for b in ids}


def _attack(card_db, opp_creature):
    from ai.ev_player import EVPlayer
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0)
    if opp_creature is not None:
        _add(game, card_db, opp_creature, 1)
    return sorted(c.name for c in
                  EVPlayer(0, "Boros Energy").decide_attackers(game))


@pytest.mark.parametrize("name", ["Cloud Elemental", "Ashenmoor Gouger"])
def test_the_attack_planner_counts_no_blocker_its_restriction_forbids(
        card_db, name):
    """A creature that cannot block the attacker leaves the attack as it
    would be with no creature there."""
    assert _attack(card_db, name) == _attack(card_db, None)
