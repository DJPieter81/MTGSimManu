"""A blink that would forfeit an attack waits for the second main phase.

A blink returns a new object (CR 400.7), summoning-sick and without a
granted haste. Cast in the first main phase, it costs the returned
creature's attack this turn, and the main phase charges exactly that
(`EVPlayer._forfeited_attack_charge`). After combat the same blink costs
no attack and does everything else it would do, so the second main phase
of the same turn is never worse. The charge was only a score, and a play
scoring slightly below zero still passed the play-value floor and was
cast. Such a blink is now deferred in the first main phase and made in
the second.

The defect: Goryo's Vengeance goldfish s70000, turn 4. Goryo's returned
Atraxa with haste. Ephemerate scored -4.09 before combat and was cast; the
new Atraxa could not attack, and 7 damage was lost. The original case of
the charge (Jeskai Blink vs Broodscale s50000, Ephemerate on a dashed
Ragavan) also scored -0.03 and was cast. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone, sick=False):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = sick
        c.tapped = False
    getattr(game.players[controller], zone).append(c)
    return c


def _decide(card_db, phase):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = phase
    game.turn_number = 5
    game.players[0].deck_name = "Jeskai Blink"
    game.players[1].deck_name = "Broodscale Bloodchief"
    for n in ("Hallowed Fountain", "Elegant Parlor"):
        _put(game, card_db, n, 0, "battlefield")
    for _ in range(8):
        _put(game, card_db, "Plains", 0, "library")
        _put(game, card_db, "Forest", 1, "library")
    attacker = _put(game, card_db, "Ragavan, Nimble Pilferer", 0,
                    "battlefield", sick=True)
    attacker._dashed = True                         # hasty this turn only
    _put(game, card_db, "Ephemerate", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Jeskai Blink",
                  rng=random.Random(0))
    return ai.decide_main_phase(game)


def _cast_name(choice):
    return choice[1].name if choice and choice[0] == "cast_spell" else None


def test_the_blink_is_not_cast_before_combat(card_db):
    assert _cast_name(_decide(card_db, Phase.MAIN1)) != "Ephemerate"


def test_the_same_blink_is_cast_after_combat(card_db):
    assert _cast_name(_decide(card_db, Phase.MAIN2)) == "Ephemerate"
