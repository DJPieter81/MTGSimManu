"""An enter trigger's card-flow effect resolves from the card's own text
(CR 603.2, 603.6a; surveil CR 701.42).

"When ~ enters, surveil N" is a triggered ability its permanent has because
its text says so (CR 113.1), whoever tagged it: the effect grammar types the
host (TRIGGERED, SELF_ENTERS) and its SURVEIL spec, and the enter-trigger
carrier resolves it through the effect dispatcher's card-flow family
(`GameState.surveil`, the owner). A classifier tag neither adds nor removes
the trigger.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _put(game, card_db, idx, name, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone if zone != "battlefield" else "battlefield").append(c)
    return c


def _library(game, card_db, n, idx=0):
    for _ in range(n):
        _put(game, card_db, idx, "Island", zone="library")


def _enters(game, card_db, name, idx=0):
    from engine.oracle_resolver import resolve_etb_from_oracle
    card = _put(game, card_db, idx, name)
    return card, resolve_etb_from_oracle(game, card, idx)


@pytest.mark.parametrize("name,n", [("Broodspinner", 2), ("Cephalid Inkmage", 3)])
def test_an_enter_trigger_surveils_as_printed_with_no_tag(card_db, name, n):
    game = _game()
    _library(game, card_db, 5)
    _, handled = _enters(game, card_db, name)
    assert handled
    assert len(game.players[0].graveyard) == n
    assert len(game.players[0].library) == 5 - n


def test_a_surveil_land_surveils_exactly_as_before(card_db):
    game = _game()
    _library(game, card_db, 3)
    _, handled = _enters(game, card_db, "Underground Mortuary")
    assert handled
    assert len(game.players[0].graveyard) == 1


def test_the_surveil_executor_is_the_card_flow_familys(card_db):
    """The dispatcher's card-flow family owns SURVEIL; damage ETBs stay on
    their own path (the carrier takes card-flow hosts only)."""
    from engine.effect_executors import FAMILIES
    from engine.effect_spec import Verb
    assert Verb.SURVEIL in FAMILIES["card_flow"]
