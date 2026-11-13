"""Each spell is offered to the other player before it resolves
(CR 117.3b, 117.3c, 117.4).

After a spell is cast, and after any object on the stack resolves, players
receive priority again; the top object resolves only once every player has
passed in succession. So a spell put on the stack while another object
resolves, a response cast in a window, and a spell left on top once the
objects above it have resolved are each, in turn, the top object while the
other player holds priority -- before any of them resolves.

The engine models this as: the player who doesn't control the top spell is
offered a response (`decide_response`) once while that spell is the top
object, before it resolves.

The defect: `GameRunner._resolve_stack_loop` resolved the whole stack with
no priority between resolutions. Only a main-phase cast (and the instant
windows' first cast) was ever offered, so a response could never be
answered, and a spell cast while another object resolved (a rebound recast,
a cascade hit) or left on top beneath a resolved ability was never offered
at all.

Class: every spell that reaches the top of the stack. Card names are
fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_runner import AICallbacks, GameRunner
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType


class _Seat:
    """A stand-in for a seat's AI: records each offer, and answers each
    with the next scripted reply (none: pass)."""

    def __init__(self, idx, replies=()):
        self.player_idx = idx
        self.offers = []
        self.replies = list(replies)

    def decide_response(self, game, item):
        self.offers.append((item, item.source.zone))
        if self.replies:
            return self.replies.pop(0)(game, item)
        return None


def _game(seats=None):
    game = GameState(rng=random.Random(0),
                     callbacks=AICallbacks() if seats is not None else None)
    game.current_phase = Phase.MAIN1
    game.active_player = game.priority_player = 0
    game.turn_number = 5
    if seats is not None:
        game.callbacks.bind_players(seats)
    return game


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _lands(game, card_db, name, n, idx):
    for _ in range(n):
        _put(game, card_db, name, "battlefield", idx)


@pytest.fixture
def runner(card_db):
    return GameRunner(card_db)


def _bolt_face(game, card, idx=0):
    assert game.cast_spell(idx, card, targets=[PLAYER_TARGET_OPPONENT])
    return game.stack.top


def _sources(seat):
    return [item.source.name for item, _ in seat.offers]


# ── A spell put on the stack while another object resolves ─────────────

def test_a_spell_put_on_the_stack_while_another_resolves_is_offered_before_it_resolves(
        card_db, runner):
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    _lands(game, card_db, "Mountain", 2, 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)

    def cast_bolt(game, source, controller, targets):
        game.cast_spell(controller, bolt, targets=[PLAYER_TARGET_OPPONENT])

    game.stack.push(StackItem(item_type=StackItemType.TRIGGERED_ABILITY,
                              source=bolt, controller=0, effect=cast_bolt))
    runner._resolve_stack_loop(game)
    assert _sources(seats[1]) == ["Lightning Bolt"]
    assert seats[1].offers[0][1] == "stack"         # asked while on the stack
    assert game.players[1].life == 17               # and then it resolved
    assert seats[0].offers == []                    # its own spell: not asked


# ── A response is answered ─────────────────────────────────────────────

def test_a_response_is_offered_to_the_player_it_answers(card_db, runner):
    game = _game([])
    _lands(game, card_db, "Mountain", 1, 0)
    _lands(game, card_db, "Island", 2, 1)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    counter = _put(game, card_db, "Counterspell", "hand", 1)
    seats = [_Seat(0), _Seat(1, [lambda g, item: (counter, [bolt.instance_id])])]
    game.callbacks.bind_players(seats)
    _bolt_face(game, bolt)
    runner._offer_response_window(game, caster_ai=seats[0],
                                  responder_ai=seats[1])
    runner._resolve_stack_loop(game)
    assert _sources(seats[0]) == ["Counterspell"]   # the caster is asked back
    assert game.players[1].life == 20               # the counter resolved
    assert bolt.zone == "graveyard"


# ── A spell left on top once an ability above it resolves ──────────────

def test_a_spell_beneath_an_ability_is_offered_once_it_is_on_top_again(
        card_db, runner):
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    _lands(game, card_db, "Mountain", 1, 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    _bolt_face(game, bolt)
    game.stack.push(StackItem(item_type=StackItemType.TRIGGERED_ABILITY,
                              source=bolt, controller=0,
                              effect=lambda *a: None))
    runner._resolve_stack_loop(game)
    assert _sources(seats[1]) == ["Lightning Bolt"]
    assert game.players[1].life == 17


def test_each_spell_is_offered_exactly_once(card_db, runner):
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    _lands(game, card_db, "Mountain", 1, 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    _bolt_face(game, bolt)
    runner._offer_response_window(game, caster_ai=seats[0],
                                  responder_ai=seats[1])   # the main phase's
    runner._resolve_stack_loop(game)
    assert _sources(seats[1]) == ["Lightning Bolt"]       # not asked again


# ── Pins ───────────────────────────────────────────────────────────────

def test_a_player_restricted_to_sorcery_timing_holds_priority_but_casts_nothing(
        card_db, runner, monkeypatch):
    from engine import rules_audit, rules_query
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    seats = [_Seat(0), _Seat(1)]
    game = _game(seats)
    _lands(game, card_db, "Mountain", 1, 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    monkeypatch.setattr(rules_query, "sorcery_speed_only",
                        lambda g, idx: idx == 1)          # a Teferi-held seat
    _bolt_face(game, bolt)
    runner._resolve_stack_loop(game)
    assert seats[1].offers == []                    # no decision to make
    assert game.players[1].life == 17
    assert "117.4/priority_before_resolution" not in [
        f["rule"] for f in rules_audit.drain()]     # but it held priority
    rules_audit.reset()


def test_the_drain_ends_when_every_window_answers(card_db, runner):
    game = _game([])
    _lands(game, card_db, "Mountain", 3, 0)
    _lands(game, card_db, "Mountain", 3, 1)
    bolts = {i: [_put(game, card_db, "Lightning Bolt", "hand", i)
                 for _ in range(3)] for i in (0, 1)}

    def answer(idx):
        def reply(g, item):
            left = [b for b in bolts[idx] if b.zone == "hand"]
            return (left[0], [PLAYER_TARGET_OPPONENT]) if left else None
        return reply

    seats = [_Seat(0, [answer(0)] * 3), _Seat(1, [answer(1)] * 3)]
    game.callbacks.bind_players(seats)
    _bolt_face(game, bolts[0][0])
    runner._resolve_stack_loop(game)
    assert game.stack.is_empty
    assert game.players[0].life == 20 - 3 * 3
    assert game.players[1].life == 20 - 3 * 3


def test_without_players_to_ask_the_drain_is_unchanged(card_db, runner):
    game = _game(None)
    _lands(game, card_db, "Mountain", 1, 0)
    bolt = _put(game, card_db, "Lightning Bolt", "hand", 0)
    _bolt_face(game, bolt)
    runner._resolve_stack_loop(game)
    assert game.stack.is_empty and game.players[1].life == 17


# ── Auditor (CR 117.4) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def test_the_audit_records_a_spell_resolved_without_a_window(card_db, audit):
    game = _game([_Seat(0), _Seat(1)])
    _lands(game, card_db, "Mountain", 1, 0)
    _bolt_face(game, _put(game, card_db, "Lightning Bolt", "hand", 0))
    game.resolve_stack()                           # a drain outside the loop
    assert "117.4/priority_before_resolution" in [f["rule"]
                                                  for f in audit.drain()]


def test_the_audit_is_silent_after_the_loop(card_db, audit, runner):
    game = _game([_Seat(0), _Seat(1)])
    _lands(game, card_db, "Mountain", 1, 0)
    _bolt_face(game, _put(game, card_db, "Lightning Bolt", "hand", 0))
    runner._resolve_stack_loop(game)
    assert "117.4/priority_before_resolution" not in [f["rule"]
                                                      for f in audit.drain()]
