"""Payoff reach through digs is weighed, not assumed.

`ai.ev_evaluator._payoff_reachable_this_turn` decides whether a chain
spell -- a ritual, a cantrip, a flashback granter cast mid-chain -- has a
payoff this turn to spend itself on. Its clause (d) said yes whenever any
dig was in hand, however unlikely that dig was to find a route to the
closer. Unit RR replaced that boolean inside the ritual gate
(`combo_calc._digs_reach_today_beats_holding`); this is the same rule at
its other owner, so one comparison answers "do these digs reach the
closer?" everywhere: today's chance against holding a turn.

The defect (Ruby Storm vs Dimir Midrange s52000, Storm's turn 6): Past in
Flames was cast from hand at storm 2 with no finisher in hand, the library's
routes few, and its flashback spent on digs. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone):
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[0], zone).append(c)
    return c


def _game(card_db, *, routes, blanks):
    g = GameState(rng=random.Random(0))
    for _ in range(4):
        _add(g, card_db, "Mountain", "battlefield")
    pif = _add(g, card_db, "Past in Flames", "hand")
    _add(g, card_db, "Reckless Impulse", "hand")
    for n in ("Desperate Ritual", "Pyretic Ritual"):
        _add(g, card_db, n, "graveyard")
    for _ in range(routes):
        _add(g, card_db, "Grapeshot", "library")
    for _ in range(blanks):
        _add(g, card_db, "Mountain", "library")
    g.players[0].deck_name = "Ruby Storm"
    g.players[1].deck_name = "Dimir Midrange"
    g.current_phase = Phase.MAIN1
    g.active_player = 0
    g.players[0].spells_cast_this_turn = 2
    g._global_storm_count = 2
    return g, pif


def test_a_long_shot_dig_is_no_reach(card_db):
    from ai.ev_evaluator import _payoff_reachable_this_turn
    g, pif = _game(card_db, routes=2, blanks=44)
    assert not _payoff_reachable_this_turn(pif, g, 0)


def test_digs_certain_to_find_a_route_are_reach(card_db):
    from ai.ev_evaluator import _payoff_reachable_this_turn
    g, pif = _game(card_db, routes=10, blanks=0)
    assert _payoff_reachable_this_turn(pif, g, 0)


def test_a_finisher_in_hand_is_reach_whatever_the_library(card_db):
    from ai.ev_evaluator import _payoff_reachable_this_turn
    g, pif = _game(card_db, routes=0, blanks=40)
    _add(g, card_db, "Grapeshot", "hand")
    assert _payoff_reachable_this_turn(pif, g, 0)
