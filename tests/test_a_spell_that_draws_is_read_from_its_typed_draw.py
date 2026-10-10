"""A spell that makes its caster draw cards draws, whatever its count's
wording (CR 121.1): "draw four cards", "draw seven cards", "draw that many
cards".

The AI asks two questions of a spell:
- does casting it deliver card draw this turn (the same-turn signal the
  main phase's deferral filter reads)?
- is it a dig (a later play that can find the closer)?

Both read the spell's typed DRAW, the effect grammar's verb with the
controller drawing. The legacy substring field stopped at "draw three
cards", so "Draw four cards" drew nothing as far as the AI could see, and
"draw that many cards" (Hex Magic) was deferred forever.

An impulse draw (exile the top N, may play them) is no draw (CR 121.1c),
and a permanent's later ability is not what casting it draws this turn.

Card names are fixture carriers: 20 pool spells' resolution draws for the
caster in a form the substring list misses; Hex Magic (Ruby Storm x4) is
the registered one.
"""
from __future__ import annotations

import random

import pytest

from engine.game_state import GameState, Phase


@pytest.mark.parametrize("name", ["Tidings", "Into the Story", "Hex Magic",
                                  "Divination", "Opt"])
def test_a_spell_whose_resolution_draws_for_its_caster_draws(card_db, name):
    from ai.predicates import spell_draws
    assert spell_draws(card_db.get_card(name))


@pytest.mark.parametrize("name", [
    "Lightning Bolt",
    "Reckless Impulse",                                # impulse: no draw
    "Ral, Monsoon Mage // Ral, Leyline Prodigy",       # a later ability
    "Griselbrand",                                     # an activation
    "Vision Skeins",                                   # each player draws
])
def test_no_other_spell_draws(card_db, name):
    from ai.predicates import spell_draws
    assert not spell_draws(card_db.get_card(name))


def _game():
    g = GameState(rng=random.Random(0))
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
    return g


def _hand(game, card_db, name):
    from engine.cards import CardInstance
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="hand")
    c._game_state = game
    game.players[0].hand.append(c)
    return c


@pytest.mark.parametrize("name", ["Tidings", "Hex Magic"])
def test_a_spell_that_draws_has_the_same_turn_draw_signal(card_db, name):
    from ai.ev_evaluator import (_enumerate_this_turn_signals,
                                 cast_is_deferred, snapshot_from_game)
    game = _game()
    card = _hand(game, card_db, name)
    snap = snapshot_from_game(game, 0)
    assert "card_draw" in _enumerate_this_turn_signals(card, snap, game, 0)
    assert not cast_is_deferred(card, snap, game, 0)


def test_a_draw_that_many_spell_is_a_dig(card_db):
    from ai.ev_evaluator import _is_real_dig
    game = _game()
    assert _is_real_dig(_hand(game, card_db, "Hex Magic"))
