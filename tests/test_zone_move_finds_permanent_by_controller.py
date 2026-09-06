"""A permanent controlled by a player who is not its owner must still
be removed from the battlefield when it changes zones — and it goes to
its OWNER's zone.

Rule: control and ownership differ only on the battlefield/stack
(CR 108.4 / 110.2). A stolen or opponent-cast permanent physically sits
on its CONTROLLER's battlefield, but a card always returns to its
OWNER's graveyard/hand/library (CR 400.3). The zone funnel resolved the
source list from card.owner, so it never found an owner≠controller
permanent on the controller's battlefield: move_card returned False, the
permanent survived lethal damage, and the SBA fixpoint re-ran its death
every pass until the iteration cap (a near-infinite loop) — the
permanent stayed on the board forever (audit: Izzet Prowess vs Boros
Energy, s55633 — a Ragavan-cast Dragon's Rage Channeler "died" 20× and
kept attacking).

Class: every control-theft effect (Act of Treason, Threaten, Claim the
Firstborn, Zealous Conscripts, ...) and every cast-an-opponent's-card
effect (Ragavan, Etali, plunder/prowl, ...) — hundreds of cards.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _mk(game, card_db, name, owner, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def test_owner_differs_from_controller_permanent_moves_to_owner_graveyard(card_db):
    game = GameState(rng=random.Random(0))
    # owner = P0, controller = P1 — a stolen / opponent-cast creature,
    # physically on P1's battlefield.
    stolen = _mk(game, card_db, "Grizzly Bears", owner=0, controller=1,
                 zone="battlefield")
    stolen.enter_battlefield()
    game.players[1].battlefield.append(stolen)
    game.players[1].creatures.append(stolen)

    moved = game.zone_mgr.move_card_to_graveyard(game, stolen, cause="test death")

    assert moved is True, "the funnel must move an owner!=controller permanent"
    assert stolen not in game.players[1].battlefield, (
        "the permanent must leave the controller's battlefield")
    assert stolen in game.players[0].graveyard, (
        "a card returns to its OWNER's graveyard (CR 400.3), not the controller's")
    assert stolen not in game.players[1].graveyard


def test_normal_owned_permanent_still_moves(card_db):
    """Regression: an ordinary owner==controller permanent still moves."""
    game = GameState(rng=random.Random(0))
    c = _mk(game, card_db, "Grizzly Bears", owner=0, controller=0,
            zone="battlefield")
    c.enter_battlefield()
    game.players[0].battlefield.append(c)
    game.players[0].creatures.append(c)
    assert game.zone_mgr.move_card_to_graveyard(game, c, cause="test") is True
    assert c not in game.players[0].battlefield
    assert c in game.players[0].graveyard
