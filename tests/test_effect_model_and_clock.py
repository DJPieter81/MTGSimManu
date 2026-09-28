"""The event clock and the continuous-effect model (CR 611–613).

Rules pinned:
* every turn-boundary reset / expiry is a clock subscriber, run in a fixed
  order; a direct `untap_step` still performs them;
* a Duration is a predicate over clock events: "this turn" ends with the
  game turn (cleanup or the next turn beginning), "until your next turn"
  ends only as that player's turn begins, a static's effect never expires
  by time, "until it leaves" ends on that object's event;
* selectors cover players by scope; modifications fall into the
  characteristic (layered) or rule (queried) family.
"""
from __future__ import annotations

import random

from engine.effect_model import (Duration, DurationKind, ModFamily, ModKind,
                                 Modification, PERMANENT, Selector, SelectorKind,
                                 THIS_TURN, WHILE_SOURCE, until_your_next_turn)
from engine.game_state import GameState
from engine.turn_clock import Clock, ClockEvent, subscribers


def test_turn_boundary_resets_are_clock_subscribers_in_order():
    assert subscribers(Clock.TURN_BEGINS) == [
        "player_turn_state_reset", "until_next_turn_expiry",
        "until_next_turn_audit", "cross_turn_event_counters_reset"]
    assert subscribers(Clock.CLEANUP) == ["end_of_turn_effects_expiry"]
    assert subscribers(Clock.UPKEEP) == ["delayed_triggers_upkeep"]
    assert subscribers(Clock.END_STEP) == ["delayed_triggers_end_step"]


def test_a_direct_untap_step_still_runs_the_turn_resets():
    game = GameState(rng=random.Random(0))
    game.players[0].cards_drawn_this_turn = 3
    game.players[1].life_lost_this_turn = 2
    game.untap_step(0)
    assert game.players[0].cards_drawn_this_turn == 0
    assert game.players[1].life_lost_this_turn == 0


def test_this_turn_ends_with_the_game_turn():
    assert THIS_TURN.expired_by(ClockEvent(Clock.CLEANUP, 0))
    assert THIS_TURN.expired_by(ClockEvent(Clock.TURN_BEGINS, 1))
    assert not THIS_TURN.expired_by(ClockEvent(Clock.END_STEP, 0))


def test_until_your_next_turn_ends_only_as_that_players_turn_begins():
    d = until_your_next_turn(0)
    assert not d.expired_by(ClockEvent(Clock.CLEANUP, 0))
    assert not d.expired_by(ClockEvent(Clock.TURN_BEGINS, 1))
    assert d.expired_by(ClockEvent(Clock.TURN_BEGINS, 0))


def test_statics_and_permanent_effects_never_expire_by_time():
    for ev in (ClockEvent(Clock.CLEANUP, 0), ClockEvent(Clock.TURN_BEGINS, 0)):
        assert not WHILE_SOURCE.expired_by(ev)
        assert not PERMANENT.expired_by(ev)


def test_until_leaves_ends_on_that_objects_event_only():
    d = Duration(DurationKind.UNTIL_LEAVES, obj=(7, 1))

    class _ObjEvent:
        kind = None

        def __init__(self, obj):
            self.obj = obj

    assert d.expired_by(_ObjEvent((7, 1)))
    assert not d.expired_by(_ObjEvent((7, 2)))   # the same card, a new object (CR 400.7)


def test_selectors_cover_players_by_scope():
    assert Selector(SelectorKind.PLAYER, player=1).covers_player(1)
    assert not Selector(SelectorKind.PLAYER, player=1).covers_player(0)
    assert Selector(SelectorKind.OPPONENTS, player=0).covers_player(1)
    assert not Selector(SelectorKind.OPPONENTS, player=0).covers_player(0)
    assert Selector(SelectorKind.ALL_PLAYERS).covers_player(0)


def test_modifications_fall_into_two_families():
    assert Modification(ModKind.MODIFY_PT).family is ModFamily.CHARACTERISTIC
    assert Modification(ModKind.ADD_KEYWORDS).family is ModFamily.CHARACTERISTIC
    for k in (ModKind.PROHIBIT, ModKind.PERMIT, ModKind.LIMIT, ModKind.COST_DELTA,
              ModKind.PREVENT_DAMAGE, ModKind.OBSERVE):
        assert Modification(k).family is ModFamily.RULE
