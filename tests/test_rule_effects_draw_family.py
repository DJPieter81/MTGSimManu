"""Draw limits are continuous effects (CR 101.2 / 611.3a): a permanent's
"<players> can't draw more than N cards each turn" is a static LIMIT effect
of that permanent, read through the one path (`rules_query.draw_limit`).

Rules pinned:
* the limit is derived from the typed `CardTemplate.draw_limit` while its
  source is on the battlefield, and disappears with it;
* "each opponent" covers only the source's opponents, "each player" covers
  its controller too;
* with several limits, the tightest one binds;
* a resolved LIMIT effect is honoured by the same query as a static one.
Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine import rules_query
from engine.cards import CardInstance
from engine.effect_model import (DurationKind, ModKind, THIS_TURN,
                                 draw_limit_effect)
from engine.game_state import GameState


def _put(game, card_db, name, controller):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    return c


def test_an_opponents_draw_limit_is_a_static_limit_effect_of_its_source(card_db):
    game = GameState(rng=random.Random(0))
    narset = _put(game, card_db, "Narset, Parter of Veils", 0)
    assert rules_query.draw_limit(game, 1) == 1
    assert rules_query.draw_limit(game, 0) is None
    assert any(e.modification.kind is ModKind.LIMIT
               and e.modification.action == "draw"
               and e.duration.kind is DurationKind.WHILE_SOURCE_ON_BATTLEFIELD
               for e in game.continuous_effects.rule_effects(game))
    game.players[0].battlefield.remove(narset)
    assert rules_query.draw_limit(game, 1) is None


def test_an_each_player_limit_covers_its_controller_too(card_db):
    game = GameState(rng=random.Random(0))
    _put(game, card_db, "Spirit of the Labyrinth", 0)
    assert rules_query.draw_limit(game, 0) == 1
    assert rules_query.draw_limit(game, 1) == 1


def test_the_tightest_limit_binds(card_db):
    game = GameState(rng=random.Random(0))
    _put(game, card_db, "Narset, Parter of Veils", 0)
    game.continuous_effects.register_effect(draw_limit_effect(0, 'all', 0, THIS_TURN))
    assert rules_query.draw_limit(game, 1) == 0


def test_a_resolved_limit_is_read_by_the_same_query_and_ends_with_the_turn(card_db):
    game = GameState(rng=random.Random(0))
    game.continuous_effects.register_effect(draw_limit_effect(0, 'opponents', 1, THIS_TURN))
    assert rules_query.draw_limit(game, 1) == 1
    game.cleanup_step()
    assert rules_query.draw_limit(game, 1) is None


def test_the_battlefield_scan_is_gone():
    assert not hasattr(GameState, "_draw_limit_for")
