"""Attack-trigger drain: "opponent sacrifices, discards, loses N life;
you draw and gain N life" (Archon of Cruelty).

`resolve_attack_trigger` handled damage / lifegain / mobilize / token attack
clauses but not the drain class, so an attacking (e.g. reanimated) Archon of
Cruelty's attack half did nothing — a large slice of the payoff.

Rule under test: on attack, the defending opponent sacrifices a
creature/planeswalker, discards a card, and loses N life; the attacker's
controller draws a card and gains N life. N parsed from oracle. Generic
"enters or attacks" repeated trigger; no card names in the resolver.
"""
from __future__ import annotations

import random

from engine.callbacks import DefaultCallbacks
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def _mk(game, db, name, owner, zone):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[owner],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def test_archon_attack_drains_opponent_and_refuels_controller(card_db):
    game = GameState(rng=random.Random(0), callbacks=DefaultCallbacks())
    game.active_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    game.turn_number = 6
    me, opp = game.players[0], game.players[1]
    me.life = 20
    opp.life = 20

    archon = _mk(game, card_db, "Archon of Cruelty", 0, "battlefield")
    # Opponent has a creature to sacrifice and a card to discard.
    opp_creature = _mk(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    _mk(game, card_db, "Lightning Bolt", 1, "hand")
    # Controller needs a library to draw from.
    _mk(game, card_db, "Forest", 0, "library")

    opp_bf_before = len(opp.battlefield)
    opp_hand_before = len(opp.hand)
    me_hand_before = len(me.hand)

    from engine.oracle_resolver import resolve_attack_trigger
    resolve_attack_trigger(game, archon, 0)

    assert opp.life == 17, f"opponent should lose 3 life; got {opp.life}"
    assert me.life == 23, f"controller should gain 3 life; got {me.life}"
    assert len(opp.battlefield) == opp_bf_before - 1, \
        "opponent should sacrifice a creature/planeswalker"
    assert len(opp.hand) == opp_hand_before - 1, \
        "opponent should discard a card"
    assert len(me.hand) == me_hand_before + 1, \
        "controller should draw a card"


def test_attack_drain_no_targets_is_safe(card_db):
    """Opponent with empty board/hand: drain still applies life loss/gain and
    draw, and does not crash on the missing sacrifice/discard."""
    game = GameState(rng=random.Random(0), callbacks=DefaultCallbacks())
    game.active_player = 0
    game.current_phase = Phase.DECLARE_ATTACKERS
    me, opp = game.players[0], game.players[1]
    me.life = 20
    opp.life = 20
    archon = _mk(game, card_db, "Archon of Cruelty", 0, "battlefield")
    _mk(game, card_db, "Forest", 0, "library")

    from engine.oracle_resolver import resolve_attack_trigger
    resolve_attack_trigger(game, archon, 0)
    assert opp.life == 17
    assert me.life == 23
