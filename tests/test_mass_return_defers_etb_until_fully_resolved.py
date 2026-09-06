"""A mass symmetric exile-and-return spell must finish exiling and
returning EVERY creature before any returned creature's ETB trigger
resolves.

Rule (CR 608.2 / 614): the whole spell resolves as one event — exile all
creatures, return all graveyard creatures — and only then do the returned
permanents' ETB triggers go on the stack. A returned creature's ETB must
see the finished board (every original creature already exiled, every
graveyard creature already returned), not a half-resolved one. The engine
processed each player fully (exile then return, firing ETBs inline)
before the next, so an ETB fired for player 0 saw player 1's not-yet-
exiled original creatures as legal targets (audit: Jeskai Blink vs Living
End, s55640 — a returned Solitude exiled a creature Living End was still
about to exile, wasting its removal and handing the opponent life).

Card names are fixture carriers; the mechanic is deferred-ETB ordering
for mass return (Living End / Living Death / Persist / Twilight's Call).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _mk(game, card_db, name, owner, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    return c


def test_returned_etb_sees_the_fully_settled_board(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0

    # P0 graveyard: an evoke incarnation whose ETB exiles a creature.
    solitude = _mk(game, card_db, "Solitude", 0, "graveyard")
    game.players[0].graveyard = [solitude]

    # P1 has an ORIGINAL battlefield creature (Living End will exile it)
    # and a graveyard creature (Living End will return it).
    original = _mk(game, card_db, "Grizzly Bears", 1, "battlefield")
    original.enter_battlefield()
    game.players[1].battlefield.append(original)
    returnee = _mk(game, card_db, "Memnite", 1, "graveyard")
    game.players[1].graveyard = [returnee]

    game._resolve_living_end(0)

    # The original creature was exiled by Living End itself (not left for
    # Solitude to waste its ETB on).
    assert original not in game.players[1].battlefield, (
        "Living End must exile the original battlefield creature")

    # Solitude's ETB, firing only AFTER the board settled, had just one
    # legal other target — the RETURNED creature — so the returnee is
    # exiled, not left on the battlefield while Solitude wasted its exile
    # on an already-doomed original.
    assert returnee not in game.players[1].battlefield, (
        "the returned creature must be the one the deferred ETB exiled — "
        "the ETB fired after every original was already gone, so it could "
        "not target a to-be-exiled original")
