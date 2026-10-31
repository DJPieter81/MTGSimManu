"""An attack trigger's "defending player" is the player the attack is made
against (CR 506.2, 508.5).

"Whenever this creature attacks, defending player loses 1 life and you gain
1 life": the defending player is the player being attacked, or the
controller of the planeswalker being attacked. In a two-player game it is
the attacking player's opponent, as the combat manager records it.

The defect: the attack carrier left the defending player unbound, so every
attack trigger naming it was refused and stayed on its legacy pass, which
for most of them did nothing (Agate-Blade Assassin, Nemesis of Reason) and
for Ulamog, the Ceaseless Hunger exiled nothing.

Class: 23 pool cards print an attack trigger naming the defending player.
Registered: Ulamog, the Ceaseless Hunger. Card names are fixture carriers.
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


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    game.turn_number = 5
    return game


def _attack(game, attackers):
    from engine.combat_manager import CombatManager
    CombatManager().declare_attackers(game, list(attackers), 0)


def test_the_defending_player_loses_life_and_the_controller_gains_it(
        card_db):
    game = _game()
    assassin = _add(game, card_db, "Agate-Blade Assassin", 0)
    life0, life1 = game.players[0].life, game.players[1].life
    _attack(game, [assassin])
    assert game.players[1].life == life1 - 1
    assert game.players[0].life == life0 + 1


def test_a_trigger_for_each_attacker_names_the_defending_player_each_time(
        card_db):
    game = _game()
    chief = _add(game, card_db, "Brutal Hordechief", 0)
    bears = _add(game, card_db, "Grizzly Bears", 0)
    life1 = game.players[1].life
    _attack(game, [chief, bears])
    assert game.players[1].life == life1 - 2


def test_the_defending_player_mills(card_db):
    game = _game()
    nemesis = _add(game, card_db, "Nemesis of Reason", 0)
    for _ in range(12):
        _add(game, card_db, "Island", 1, zone="library")
    _attack(game, [nemesis])
    assert len(game.players[1].library) == 2
    assert len(game.players[1].graveyard) == 10


def test_the_defending_player_exiles_the_top_of_their_library(card_db):
    game = _game()
    ulamog = _add(game, card_db, "Ulamog, the Ceaseless Hunger", 0)
    for _ in range(25):
        _add(game, card_db, "Island", 1, zone="library")
    for _ in range(3):
        _add(game, card_db, "Forest", 0, zone="library")
    _attack(game, [ulamog])
    assert len(game.players[1].library) == 5
    assert len(game.players[1].exile) == 20
    assert len(game.players[0].library) == 3


# ── Only an attack names a defending player (CR 506.2) ─────────────────

def _probe(text, types):
    from engine.cards import CardTemplate, CardType, ManaCost
    return CardTemplate(name="Probe Herald", mana_cost=ManaCost(),
                        card_types=[CardType(t) for t in types],
                        oracle_text=text)


@pytest.mark.parametrize("plan, types, text", [
    ("step_plan", ("enchantment",), "At the beginning of each opponent's "
     "upkeep, defending player mills two cards."),
    ("draw_plan", ("enchantment",), "Whenever an opponent draws a card, "
     "defending player loses 1 life."),
    ("etb_plan", ("creature",), "When this creature enters, defending "
     "player mills two cards."),
])
def test_a_trigger_whose_event_names_no_defending_player_is_not_taken(
        plan, types, text):
    # There is a defending player only once attackers are declared (CR
    # 506.2): no other carrier's event names one, so none takes the host.
    from engine import effect_carrier
    t = _probe(text, types)
    assert getattr(effect_carrier, plan)(t.effects.faces[0]) is None


def test_an_event_naming_only_that_player_binds_no_defending_player(card_db):
    # A step's or a draw's event names its own player ("that player"),
    # never a defending player: resolved under one, the attack trigger
    # makes no player lose life (nothing is bound to a guessed player).
    from engine import effect_resolver as er
    game = _game()
    assassin = _add(game, card_db, "Agate-Blade Assassin", 0)
    host = next(h for h in assassin.template.effects.faces[0]
                if h.trigger is not None and h.trigger.attack is not None)
    life1 = game.players[1].life
    er.resolve_ability(game, er.handle_of(assassin), 0, host, (),
                       family="attack_trigger",
                       event=er.TriggerEvent(player=1),
                       source_object=assassin)
    assert game.players[1].life == life1


# ── Auditor (CR 506.2, 508.5) ──────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_a_defending_player_who_is_not_attacked(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "defending_player",
                        lambda game, active: active)           # the defect
    game = _game()
    assassin = _add(game, card_db, "Agate-Blade Assassin", 0)
    _attack(game, [assassin])
    assert "508.5/defending_player" in _violations(audit)


def test_the_audit_is_silent_when_the_attacked_player_defends(card_db,
                                                              audit):
    game = _game()
    assassin = _add(game, card_db, "Agate-Blade Assassin", 0)
    _attack(game, [assassin])
    assert "508.5/defending_player" not in _violations(audit)
