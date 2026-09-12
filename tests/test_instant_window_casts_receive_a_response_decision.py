"""A spell cast in an instant-speed window can be responded to (CR 117.3d).

`GameRunner._cast_instant_removal` — the begin-combat and end-step windows
in which the non-active player casts removal and flash creatures — put the
spell on the stack and resolved it immediately.  The active player was
never offered priority, so nothing cast in those windows could ever be
countered, however many counters they held with mana open.  The main-phase
cast path (`_execute_main_phase`) offers `decide_response`; this one did
not.

Observed 2026-09-08 (Broodscale Bloodchief replays): 3 / 2 / 27 instant-
window casts per match, none of them answerable.

Rule: every spell cast in an instant window is offered to the opposing
player's `decide_response` before it resolves, and a returned response is
cast onto the stack above it.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _window(card_db):
    """Active player (0) has a 4/4 and Counterspell with UU open; the
    non-active player (1) holds Path to Exile with a Plains open."""
    from ai.ev_player import EVPlayer
    from engine.game_runner import GameRunner

    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    threat = _add(game, card_db, "Sojourner's Companion", 0, "battlefield")
    for _ in range(2):
        _add(game, card_db, "Island", 0, "battlefield")
    counter = _add(game, card_db, "Counterspell", 0, "hand")
    _add(game, card_db, "Plains", 1, "battlefield")
    _add(game, card_db, "Plains", 1, "battlefield")
    removal = _add(game, card_db, "Path to Exile", 1, "hand")

    active_ai = EVPlayer(player_idx=0, deck_name="Azorius Control", rng=random.Random(0))
    responder_ai = EVPlayer(player_idx=1, deck_name="Azorius Control", rng=random.Random(0))
    runner = GameRunner(card_db=card_db)
    return game, runner, active_ai, responder_ai, threat, counter, removal


def test_instant_window_cast_is_offered_to_the_active_players_response_decider(card_db):
    game, runner, active_ai, responder_ai, threat, counter, removal = _window(card_db)
    offered = []

    def _record(g, stack_item):
        offered.append(stack_item.source.name)
        return None

    active_ai.decide_response = _record
    runner._cast_instant_removal(game, responder_ai, active_ai,
                                 context="pre_combat", max_instants=3)

    assert removal not in game.players[1].hand, "fixture: the window must cast the removal"
    assert offered == ["Path to Exile"], (
        "the active player was never offered a response to a spell cast in "
        f"the instant window (offered: {offered})")


def test_a_response_returned_in_the_window_is_cast_and_counters_the_spell(card_db):
    game, runner, active_ai, responder_ai, threat, counter, removal = _window(card_db)

    def _counter_it(g, stack_item):
        return (counter, [stack_item.source.instance_id])

    active_ai.decide_response = _counter_it
    runner._cast_instant_removal(game, responder_ai, active_ai,
                                 context="pre_combat", max_instants=3)

    assert counter not in game.players[0].hand, "the returned response must be cast"
    assert threat in game.players[0].battlefield, (
        "the countered removal must not resolve — the creature survives")
