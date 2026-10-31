"""A triggered ability's event binds what its text calls "that player"
(CR 603.2).

"Whenever an opponent draws a card, ~ deals 1 damage to that player" and
"..., they lose 2 life": the player is the one the trigger event names. The
carrier passes the event (`effect_resolver.TriggerEvent`), and the
dispatcher binds `Ref(EVENT_PLAYER)` to it -- as the acting player of a
life spec and as the recipient of a damage spec. With no event nothing is
bound, so nothing is dealt to a guessed player.
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


def _put(game, card_db, idx, name):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[idx].battlefield.append(c)
    return c


def _opponent_draw_host(card):
    from engine.effect_spec import EventHint
    return next(h for h in card.template.effects.walk(include_sub=False)
                if h.trigger is not None
                and EventHint.DRAW in h.trigger.event_hints
                and h.trigger.draw is not None
                and h.trigger.draw.drawer == "opponent")


@pytest.mark.parametrize("name,loss", [("Underworld Dreams", 1),
                                       ("Sheoldred, the Apocalypse", 2),
                                       ("Scrawling Crawler", 1)])
def test_that_player_is_the_player_the_trigger_event_names(card_db, name, loss):
    from engine import effect_resolver as er
    game = _game()
    src = _put(game, card_db, 0, name)
    host = _opponent_draw_host(src)
    assert er.can_execute(host, "damage")
    before = [p.life for p in game.players]
    assert er.resolve_ability(game, er.handle_of(src), 0, host, (),
                              family="damage",
                              event=er.TriggerEvent(player=1),
                              source_object=src)
    assert [p.life for p in game.players] == [before[0], before[1] - loss]


@pytest.mark.parametrize("name", ["Underworld Dreams", "Scrawling Crawler"])
def test_with_no_event_that_player_is_no_one(card_db, name):
    from engine import effect_resolver as er
    game = _game()
    src = _put(game, card_db, 0, name)
    before = [p.life for p in game.players]
    assert not er.resolve_ability(game, er.handle_of(src), 0,
                                  _opponent_draw_host(src), (),
                                  family="damage", source_object=src)
    assert [p.life for p in game.players] == before
