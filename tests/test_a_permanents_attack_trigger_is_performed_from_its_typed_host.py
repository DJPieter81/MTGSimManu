"""A permanent's attack ability is performed from its typed host once
attackers are declared (CR 508.1m, 508.2, 603.2, 603.2c, 603.3b, 603.3d,
603.4).

As attackers are declared, every ability whose trigger event is that
declaration triggers: "whenever ~ attacks" for the source itself, "whenever
equipped / enchanted creature attacks" for the creature its source is
attached to, "whenever a creature you control attacks" once for each such
attacker, and "whenever you attack" once for the declaration (CR 603.2c).
They are put on the stack once every attacker is declared, in APNAP order,
so the non-active player's resolve first (CR 603.3b); an intervening "if"
is checked as each triggers and again as it resolves (CR 603.4).

The defect: the grammar typed every attack head and nothing read it. The
legacy pass read only the attacker's own oracle text with a handful of
regular expressions, in the middle of the declaration, so an Equipment's or
an Aura's head and every "whenever you attack" head did nothing, and
Tamiyo, Inquisitive Student never investigated.

Class: the closed attack table types 876 of the pool's 1168 attack heads;
the carrier takes 104 hosts of 102 cards whole. Registered: Tamiyo,
Inquisitive Student (Dimir Midrange x3). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType
from engine.combat_manager import CombatManager
from engine.game_state import GameState, Phase
from engine.mana import ManaCost

TAMIYO = "Tamiyo, Inquisitive Student // Tamiyo, Seasoned Scholar"


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    game.turn_number = 5
    return game


def _place(game, tmpl, zone, idx):
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _put(game, card_db, name, zone="battlefield", idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    return _place(game, tmpl, zone, idx)


def _synthetic(game, oracle, idx=0, types=(CardType.CREATURE,),
               subtypes=(), name="Attack Probe"):
    creature = CardType.CREATURE in types
    tmpl = CardTemplate(
        name=name, card_types=list(types), mana_cost=ManaCost(),
        supertypes=[], subtypes=list(subtypes),
        power=2 if creature else None, toughness=2 if creature else None,
        loyalty=None, keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False, oracle_text=oracle, tags=set())
    return _place(game, tmpl, "battlefield", idx)


def _attack(game, attackers, active=0):
    game.active_player = game.priority_player = active
    CombatManager().declare_attackers(game, list(attackers), active)


def _tokens(game, idx=0):
    return [c.name for c in game.players[idx].battlefield
            if getattr(c, "is_token", False)]


def _attack_head(template):
    from engine.effect_spec import EventHint
    (head,) = [h.trigger for h in template.effects.faces[0]
               if h.trigger is not None
               and {EventHint.SELF_ATTACKS, EventHint.ATTACKS_OTHER}
               & set(h.trigger.event_hints)]
    return head


# ── The typed head: a closed table of attack phrases ──────────────────

@pytest.mark.parametrize("name,attacker,per_combat", [
    (TAMIYO, "self", False),
    ("Shroudstomper", "self", False),               # "enters or attacks"
    ("Drossclaw", "equipped", False),
    ("Aqueous Form", "enchanted", False),
    ("Anthem of Rakdos", "yours", False),
    ("Battlesong Berserker", "yours", True),         # "whenever you attack"
    ("Ancestor Dragon", "yours", True),              # "one or more ... attack"
])
def test_an_attack_head_is_typed_by_who_attacks(card_db, name, attacker,
                                                 per_combat):
    head = _attack_head(card_db.get_card(name))
    assert head.attack is not None
    assert (head.attack.attacker, head.attack.per_combat) == (attacker,
                                                              per_combat)


@pytest.mark.parametrize("name", [
    "Bruna, Light of Alabaster",          # attacks or blocks
    "Grunn, the Lonely King",             # attacks alone
    "Alacrian Jaguar",                    # attacks while saddled
])
def test_an_attack_rider_the_table_does_not_read_is_left_untyped(card_db,
                                                                 name):
    assert _attack_head(card_db.get_card(name)).attack is None


# ── The carrier ───────────────────────────────────────────────────────

def test_an_attack_trigger_that_investigates_creates_a_clue(card_db):
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO)
    _attack(game, [tamiyo])
    assert _tokens(game) == ["Clue Token"]


def test_an_attack_trigger_is_performed_once_every_attacker_is_declared(
        card_db, monkeypatch):
    from engine import effect_resolver as er
    real = er.resolve_ability
    seen = []
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO)
    bears = _put(game, card_db, "Grizzly Bears")

    def spy(*a, **kw):
        seen.append((tamiyo.attacking, bears.attacking))
        return real(*a, **kw)
    monkeypatch.setattr(er, "resolve_ability", spy)
    _attack(game, [tamiyo, bears])
    assert seen == [(True, True)]


def test_a_creature_you_control_head_triggers_once_per_attacker(card_db):
    game = _game()
    _synthetic(game, "Whenever a creature you control attacks, you gain 1 "
               "life.", types=(CardType.ENCHANTMENT,))
    a, b = (_put(game, card_db, "Grizzly Bears") for _ in range(2))
    _attack(game, [a, b])
    assert game.players[0].life == 22


def test_a_you_attack_head_triggers_once_for_the_declaration(card_db):
    game = _game()
    _synthetic(game, "Whenever you attack, you gain 1 life.",
               types=(CardType.ENCHANTMENT,))
    a, b = (_put(game, card_db, "Grizzly Bears") for _ in range(2))
    _attack(game, [a, b])
    assert game.players[0].life == 21


def test_an_opponents_attack_does_not_trigger_your_heads(card_db):
    game = _game()
    _synthetic(game, "Whenever you attack, you gain 1 life.", idx=1,
               types=(CardType.ENCHANTMENT,))
    _attack(game, [_put(game, card_db, "Grizzly Bears")])
    assert game.players[1].life == 20


def test_an_equipped_creature_head_follows_its_attachment(card_db):
    game = _game()
    claw = _put(game, card_db, "Drossclaw")
    bears = _put(game, card_db, "Grizzly Bears")
    _attack(game, [bears])
    assert game.players[1].life == 20                # attached to nothing
    game.turn_number += 1
    bears.attacking = bears.tapped = False
    assert game.attach_equipment(0, claw, bears)
    _attack(game, [bears])
    assert game.players[1].life == 19


def test_an_enchanted_creature_head_triggers_for_its_auras_controller(
        card_db, monkeypatch):
    """An Aura the opponent controls on the attacker: its controller's
    trigger is the non-active player's, so it resolves first (CR 603.3b)."""
    from engine import effect_resolver as er
    real = er.resolve_ability
    order = []

    def spy(game, source, controller, *a, **kw):
        order.append(controller)
        return real(game, source, controller, *a, **kw)
    monkeypatch.setattr(er, "resolve_ability", spy)
    game = _game()
    probe = _synthetic(game, "Whenever this creature attacks, you gain 1 "
                       "life.")
    aura = _synthetic(game, "Enchant creature\nWhenever enchanted creature "
                      "attacks, you gain 2 life.", idx=1,
                      types=(CardType.ENCHANTMENT,), subtypes=("Aura",),
                      name="Aura Probe")
    aura.attached_to_id = probe.instance_id
    _attack(game, [probe])
    assert order == [1, 0]
    assert (game.players[0].life, game.players[1].life) == (21, 22)


def test_an_intervening_if_is_checked_as_the_ability_triggers(card_db):
    """Delta Bloodflies: "if you control a creature with a counter on it"."""
    game = _game()
    flies = _put(game, card_db, "Delta Bloodflies")
    _attack(game, [flies])
    assert game.players[1].life == 20
    game.turn_number += 1
    flies.attacking = flies.tapped = False
    flies.add_plus_counters(1, game)
    _attack(game, [flies])
    assert game.players[1].life == 19


def test_a_taken_card_is_not_also_performed_by_the_legacy_pass(card_db):
    game = _game()
    probe = _synthetic(game, "Whenever this creature attacks, you gain 1 "
                       "life.")
    _attack(game, [probe])
    assert game.players[0].life == 21


def test_a_card_the_carrier_cannot_take_keeps_its_legacy_pass(card_db):
    """A second attack head the carrier does not read refuses the card
    whole: neither head is performed twice, the legacy pass keeps its
    life gain."""
    from engine.effect_carrier import attack_taken
    game = _game()
    probe = _synthetic(game, "Whenever this creature attacks, you gain 1 "
                       "life.\nWhenever this creature attacks or blocks, "
                       "it gets +1/+0 until end of turn.")
    assert attack_taken([probe]) == frozenset()
    _attack(game, [probe])
    assert game.players[0].life == 21


def test_an_attack_trigger_triggers_from_the_face_shown(card_db):
    """Erupting Dreadwolf's "whenever ~ attacks, it deals 2 damage to any
    target" is the back face's: it triggers only while that face is up."""
    game = _game()
    wolf = _put(game, card_db, "Smoldering Werewolf // Erupting Dreadwolf")
    _attack(game, [wolf])
    assert game.players[1].life == 20
    game.turn_number += 1
    wolf.attacking = wolf.tapped = False
    wolf.is_transformed = True
    _attack(game, [wolf])
    assert game.players[1].life == 18


def test_the_runner_dispatches_attack_triggers_once_a_turn(card_db,
                                                           monkeypatch):
    import engine.effect_carrier as ec
    from decks.modern_meta import MODERN_DECKS
    from engine.game_runner import GameRunner
    seen = []
    real = ec.dispatch_attack_triggers

    def spy(game, attackers, active):
        seen.append((game.turn_number, active, len(attackers)))
        return real(game, attackers, active)
    monkeypatch.setattr(ec, "dispatch_attack_triggers", spy)
    runner = GameRunner(card_db)
    a, b = MODERN_DECKS["Boros Energy"], MODERN_DECKS["Domain Zoo"]
    random.seed(50000)
    runner.rng.seed(50000)
    runner.run_game("Boros Energy", a["mainboard"], "Domain Zoo",
                    b["mainboard"])
    assert seen
    turns = [t for t, _active, _n in seen]
    assert len(turns) == len(set(turns))
    assert all(n > 0 for _t, _active, n in seen)


# ── AI: where a beneficial change goes ────────────────────────────────

def _ai_game():
    from engine.game_runner import AICallbacks
    game = _game()
    game.callbacks = AICallbacks()
    return game


def test_a_triggered_pump_goes_on_the_controllers_attacking_creature(
        card_db):
    """Burrenton Shield-Bearers' "target creature gets +0/+3" admits the
    opponent's creature too; the pump goes on the attacker the owner
    ranks first (`ai.pump_target.own_pump_target`)."""
    from ai.pump_target import own_pump_target
    game = _ai_game()
    bearers = _put(game, card_db, "Burrenton Shield-Bearers")
    _put(game, card_db, "Grizzly Bears", idx=1)
    other = _put(game, card_db, "Grizzly Bears")
    _attack(game, [bearers])
    want = own_pump_target(game, 0, [bearers, other])
    assert want is bearers                           # the attacking one
    assert bearers.toughness == bearers.template.toughness + 3


def test_a_triggered_shrink_keeps_the_engines_aim_at_an_opponent(card_db):
    """Binding Geist's "target creature an opponent controls gets -2/-0" is
    no pump: the default aim (the opponent's creature) stands."""
    game = _ai_game()
    geist = _put(game, card_db, "Binding Geist // Spectral Binding")
    theirs = _put(game, card_db, "Grizzly Bears", idx=1)
    _attack(game, [geist])
    assert theirs.power == theirs.template.power - 2


def test_beneficial_change_reads_the_typed_modification(card_db):
    from ai.pump_target import beneficial_change
    from engine.effect_spec import Verb, iter_specs

    def change(name):
        return next(s for h in card_db.get_card(name).effects.faces[0]
                    for s in iter_specs(h.specs)
                    if s.verb is Verb.CONTINUOUS)
    assert beneficial_change(change("Burrenton Shield-Bearers"))
    assert beneficial_change(change("Chasm Drake"))            # a keyword
    assert not beneficial_change(change("Binding Geist // Spectral Binding"))


# ── Auditor (CR 603.2, 603.2c), restated from raw fields ──────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_census_records_an_attack_trigger_the_carrier_cannot_take(
        card_db, audit):
    game = _game()
    _attack(game, [_put(game, card_db, "Bruna, Light of Alabaster")])
    assert "603.2/attack_trigger_unresolved" in _rules(audit)


def test_the_census_is_silent_for_an_attack_trigger_it_takes(card_db, audit):
    game = _game()
    _attack(game, [_put(game, card_db, TAMIYO)])
    assert "603.2/attack_trigger_unresolved" not in _rules(audit)


def test_the_audit_records_a_declaration_dispatched_twice(card_db, audit):
    from engine.effect_carrier import dispatch_attack_triggers
    game = _game()
    tamiyo = _put(game, card_db, TAMIYO)
    _attack(game, [tamiyo])
    dispatch_attack_triggers(game, [tamiyo], 0)                # the defect
    assert "603.2/attack_trigger_once" in _rules(audit)


def test_the_audit_records_a_head_triggered_by_an_attacker_it_does_not_name(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "attack_matches",
                        lambda *a, **k: True)                  # the defect
    game = _game()
    _put(game, card_db, "Drossclaw")                           # unattached
    _attack(game, [_put(game, card_db, "Grizzly Bears")])
    assert "603.2/attack_trigger_once" in _rules(audit)


def test_the_audit_is_silent_over_a_turns_declarations(card_db, audit):
    game = _game()
    claw = _put(game, card_db, "Drossclaw")
    bears = _put(game, card_db, "Grizzly Bears")
    tamiyo = _put(game, card_db, TAMIYO)
    _synthetic(game, "Whenever you attack, you gain 1 life.",
               types=(CardType.ENCHANTMENT,))
    assert game.attach_equipment(0, claw, bears)
    _attack(game, [bears, tamiyo])
    game.turn_number += 1
    _attack(game, [_put(game, card_db, "Grizzly Bears", idx=1)], active=1)
    rules = _rules(audit)
    assert "603.2/attack_trigger_once" not in rules
    assert "603.2/attack_trigger_unresolved" not in rules
