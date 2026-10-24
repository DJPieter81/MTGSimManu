"""Hand-refill wheels, the draw-limit static, and "end the turn".

Rules under test (card names are fixture carriers only):

* A wheel — "each player shuffles their hand [and graveyard] into their
  library, then draws seven cards" / "each player discards their hand, then
  draws seven cards" — replaces every player's hand (parsed once into
  `CardTemplate.hand_refill`). 10 pool cards carry the shape; before this it
  resolved as nothing (census `unhandled/spell` for the 3 copies in Azorius
  Control's refreshed list).
* "Each opponent / each player can't draw more than one card each turn" is a
  draw rule (CR 121 + CR 101.2): draws past the limit do not happen, and a
  skipped draw is not a draw from an empty library.
* "If it's your turn, end the turn" (CR 723.1): the stack is exiled and every
  remaining step of the turn is skipped except cleanup.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_draw_limit, parse_hand_refill
from engine.stack import StackItem, StackItemType

WHEEL = "Day's Undoing"
LIMITER = "Narset, Parter of Veils"


def test_wheel_shapes_are_parsed():
    assert parse_hand_refill(
        "Each player shuffles their hand and graveyard into their library, "
        "then draws seven cards. If it's your turn, end the turn.") == {
        'mode': 'shuffle', 'graveyard': True, 'count': 7, 'ends_turn': True}
    assert parse_hand_refill(
        "Each player discards their hand, then draws seven cards.") == {
        'mode': 'discard', 'graveyard': False, 'count': 7, 'ends_turn': False}
    assert parse_hand_refill("Draw seven cards.") is None


def test_draw_limit_shapes_are_parsed():
    assert parse_draw_limit("Each opponent can't draw more than one card each turn.") == {
        'who': 'opponents', 'max': 1}
    assert parse_draw_limit("Each player can't draw more than one card each turn.") == {
        'who': 'all', 'max': 1}
    assert parse_draw_limit("Draw a card.") is None


def test_the_typed_fields_are_populated_across_the_class(card_db):
    wheels = [t.name for t in card_db.cards.values() if t.hand_refill]
    assert len(wheels) >= 4, wheels
    limits = [t.name for t in card_db.cards.values() if t.draw_limit]
    assert len(limits) >= 5, limits


def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for idx in (0, 1):
        for _ in range(20):
            _put(game, card_db, "Island", idx, "library")
    return game


def _resolve(game, card_db, name, controller=0):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller, instance_id=game.next_instance_id(),
                        zone="stack")
    card._game_state = game
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=card,
                              controller=controller, targets=[]))
    game.resolve_stack()


def test_a_wheel_gives_every_player_a_fresh_seven(card_db):
    game = _game(card_db)
    for _ in range(2):
        _put(game, card_db, "Lightning Bolt", 0, "hand")
    _put(game, card_db, "Lightning Bolt", 1, "hand")
    _put(game, card_db, "Lightning Bolt", 1, "graveyard")
    _resolve(game, card_db, WHEEL)
    assert len(game.players[0].hand) == 7
    assert len(game.players[1].hand) == 7
    # The graveyard (other than the resolved wheel itself) went back in.
    assert not [c for c in game.players[1].graveyard if c.name == "Lightning Bolt"]


def test_a_draw_limit_caps_a_limited_players_wheel_draw(card_db):
    game = _game(card_db)
    _put(game, card_db, LIMITER, 0, "battlefield")
    for _ in range(3):
        _put(game, card_db, "Lightning Bolt", 1, "hand")
    _resolve(game, card_db, WHEEL)
    assert len(game.players[0].hand) == 7   # the limiter's controller is unaffected
    assert len(game.players[1].hand) == 1   # the opponent draws one, not seven
    assert not game.game_over               # a skipped draw is not an empty-library draw


def test_the_draw_limit_counts_draws_already_taken_this_turn(card_db):
    game = _game(card_db)
    _put(game, card_db, LIMITER, 0, "battlefield")
    game.draw_cards(1, 1)
    assert game.draw_cards(1, 3) == []


def test_ending_the_turn_on_your_own_turn_requests_the_turn_end(card_db):
    game = _game(card_db)
    _resolve(game, card_db, WHEEL, controller=0)
    assert game.end_turn_requested


def test_a_wheel_on_the_opponents_turn_does_not_end_the_turn(card_db):
    game = _game(card_db)
    game.active_player = 1
    _resolve(game, card_db, WHEEL, controller=0)
    assert not game.end_turn_requested


def test_ending_the_turn_exiles_the_stack(card_db):
    game = _game(card_db)
    other = CardInstance(template=card_db.get_card("Lightning Bolt"), owner=1,
                         controller=1, instance_id=game.next_instance_id(),
                         zone="stack")
    other._game_state = game
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=other,
                              controller=1, targets=[]))
    game.end_the_turn(0)
    assert game.stack.is_empty
    assert other in game.players[1].exile


def test_a_cannot_draw_static_is_a_draw_limit_of_zero(card_db):
    assert parse_draw_limit("Players can't draw cards.") == {'who': 'all', 'max': 0}
    game = _game(card_db)
    _put(game, card_db, "Omen Machine", 0, "battlefield")
    assert game.draw_cards(0, 1) == []
    assert game.draw_cards(1, 1) == []
    assert not game.game_over
