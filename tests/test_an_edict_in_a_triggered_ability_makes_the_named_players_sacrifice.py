"""An edict in a triggered ability makes the players it names sacrifice
permanents of their choice (CR 701.21a, 603.2, 101.4).

"When this creature enters, each player sacrifices a creature of their
choice": as the trigger resolves, each player chooses a creature they
control, in APNAP order, and all are sacrificed at once. "Whenever this
creature enters or attacks, target opponent sacrifices a creature or
planeswalker of their choice, discards a card, and loses 3 life. You draw a
card and gain 3 life": the attack trigger performs every instruction. A
player who controls nothing the edict describes sacrifices nothing.

The defect: the edict executor ran only for spells (unit ED). The trigger
body the attack and cast carriers share refused SACRIFICE, and the
enter-trigger carrier's families held no edict. Fleshbag Marauder's enter
trigger did nothing, and Archon of Cruelty's attack did nothing (its enter
trigger is a name-keyed handler).

Class: 38 pool cards print an edict in a triggered ability; the enter
carrier takes the player-set ones and the attack carrier Archon of
Cruelty's. Registered: Archon of Cruelty (Goryo's Vengeance x3, Grixis
Reanimator x4). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = phase
    game.turn_number = 5
    return game


def _creatures(game, player):
    return sorted(c.name for c in game.players[player].battlefield
                  if c.template.is_creature)


def _cast(game, card_db, name, controller, lands="Swamp", n=6):
    for _ in range(n):
        _add(game, card_db, lands, controller)
    card = _add(game, card_db, name, controller, zone="hand")
    assert game.cast_spell(controller, card, [])
    game.resolve_stack()
    return card


# ── Enter triggers ─────────────────────────────────────────────────────

def test_an_enter_trigger_makes_each_player_sacrifice_a_creature(card_db):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0)
    _add(game, card_db, "Grizzly Bears", 1)
    _add(game, card_db, "Memnite", 1)
    _cast(game, card_db, "Fleshbag Marauder", 0)
    # Each player sacrificed one creature of their own choice.
    assert len(_creatures(game, 0)) == 1
    assert len(_creatures(game, 1)) == 1


def test_an_enter_trigger_makes_only_the_opponents_sacrifice(card_db):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0)
    _add(game, card_db, "Grizzly Bears", 1)
    _cast(game, card_db, "Oath of Liliana", 0)
    assert _creatures(game, 0) == ["Grizzly Bears"]
    assert _creatures(game, 1) == []


def test_a_player_with_nothing_the_edict_describes_sacrifices_nothing(
        card_db):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 0)
    _cast(game, card_db, "Oath of Liliana", 0)
    assert _creatures(game, 0) == ["Grizzly Bears"]


def test_the_sacrificed_creature_is_its_controllers_choice(card_db,
                                                          monkeypatch):
    from engine.callbacks import DefaultCallbacks
    game = _game()
    _add(game, card_db, "Grizzly Bears", 1)
    keep = _add(game, card_db, "Serra Angel", 1)
    picked = []

    def choose(self, g, player, options):
        pick = next(c for c in options if c is not keep)
        picked.append((player, pick.name))
        return pick
    monkeypatch.setattr(DefaultCallbacks, "choose_sacrifice", choose)
    game.callbacks = DefaultCallbacks()
    _cast(game, card_db, "Oath of Liliana", 0)
    assert picked == [(1, "Grizzly Bears")]
    assert _creatures(game, 1) == ["Serra Angel"]


# ── Attack triggers ────────────────────────────────────────────────────

def test_an_attack_trigger_performs_its_edict_and_the_rest_of_its_body(
        card_db):
    from engine.combat_manager import CombatManager
    game = _game(Phase.DECLARE_ATTACKERS)
    archon = _add(game, card_db, "Archon of Cruelty", 0)
    _add(game, card_db, "Grizzly Bears", 1)
    _add(game, card_db, "Lightning Bolt", 1, zone="hand")
    _add(game, card_db, "Island", 0, zone="library")
    life0, life1 = game.players[0].life, game.players[1].life
    hand0 = len(game.players[0].hand)
    CombatManager().declare_attackers(game, [archon], 0)
    assert _creatures(game, 1) == []
    assert game.players[1].hand == []
    assert game.players[1].life == life1 - 3
    assert game.players[0].life == life0 + 3
    assert len(game.players[0].hand) == hand0 + 1


def test_the_attack_triggers_victim_is_the_target_opponents_choice(
        card_db, monkeypatch):
    from engine.callbacks import DefaultCallbacks
    from engine.combat_manager import CombatManager
    game = _game(Phase.DECLARE_ATTACKERS)
    archon = _add(game, card_db, "Archon of Cruelty", 0)
    _add(game, card_db, "Grizzly Bears", 1)
    _add(game, card_db, "Serra Angel", 1)
    _add(game, card_db, "Island", 0, zone="library")

    def choose(self, g, player, options):
        return next(c for c in options if c.name == "Serra Angel")
    monkeypatch.setattr(DefaultCallbacks, "choose_sacrifice", choose)
    game.callbacks = DefaultCallbacks()
    CombatManager().declare_attackers(game, [archon], 0)
    assert _creatures(game, 1) == ["Grizzly Bears"]


def test_the_attack_carrier_takes_the_edict_trigger(card_db):
    from engine.effect_carrier import attack_taken
    game = _game(Phase.DECLARE_ATTACKERS)
    archon = _add(game, card_db, "Archon of Cruelty", 0)
    assert archon.instance_id in attack_taken([archon])


# ── Auditor (CR 701.21a) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_an_edict_that_sacrifices_nothing(card_db, audit,
                                                            monkeypatch):
    from engine import effect_executors
    monkeypatch.setattr(effect_executors, "_sacrifice",
                        lambda ctx, perm: None)                # the defect
    game = _game()
    _add(game, card_db, "Grizzly Bears", 1)
    _cast(game, card_db, "Oath of Liliana", 0)
    assert "701.21a/edict" in _violations(audit)


def test_the_audit_is_silent_when_the_edict_is_performed(card_db, audit):
    game = _game()
    _add(game, card_db, "Grizzly Bears", 1)
    _cast(game, card_db, "Oath of Liliana", 0)
    assert "701.21a/edict" not in _violations(audit)
