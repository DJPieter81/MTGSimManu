"""The pre-combat "discard to pump" line fires only when the discard
ability itself pumps the creature.

`decide_attackers` gated its discard-for-+1/+1 routine on
``has_discard_effect and "+1/+1" in oracle`` — a whole-oracle substring
test. It fired for ANY discard-cost creature whose text merely CONTAINS
"+1/+1" somewhere, then discarded cards from hand and grew the creature.
Hardened Academic's discard ability grants LIFELINK ("Discard a card:
This creature gains lifelink until end of turn"); its "+1/+1" lives in a
separate graveyard trigger — so the AI fabricated permanent counters it
can never actually get (audit: Hollow One vs Boros Ponza, s58002 —
game-deciding: it manufactured the turn-5 lethal).

Rule under test: the discard-pump is considered only when a SINGLE
ability paragraph contains both the discard cost and the +1/+1 (the
creature's own "Discard a card: put a +1/+1 counter on this creature"
ability — Psychic Frog). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _bf(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    if t.is_creature:
        game.players[controller].creatures.append(c)
    return c


def _hand(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="hand")
    c._game_state = game
    game.players[controller].hand.append(c)
    return c


def _setup(card_db, attacker_name):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    game.players[0].deck_name = "Dimir Midrange"
    game.players[1].deck_name = "Boros Energy"
    # Three lands in play + two spare lands in hand → the pump routine's
    # "excess lands are discardable" path has fuel.
    for _ in range(3):
        _bf(game, card_db, "Island", 0)
    for _ in range(2):
        _hand(game, card_db, "Island", 0)
    attacker = _bf(game, card_db, attacker_name, 0)
    player = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                      rng=random.Random(0))
    return game, player, attacker


def test_discard_ability_that_does_not_pump_does_not_grow_the_creature(card_db):
    """Hardened Academic's discard ability grants lifelink, not +1/+1 —
    the pre-combat discard-pump must not fire, so no hand cards are lost
    and no counters appear."""
    game, player, academic = _setup(card_db, "Hardened Academic")
    hand_before = len(game.players[0].hand)
    counters_before = academic.plus_counters

    player.decide_attackers(game)

    assert academic.plus_counters == counters_before, (
        f"Hardened Academic gained {academic.plus_counters - counters_before} "
        f"fabricated +1/+1 counter(s) — its discard ability grants lifelink, "
        f"not +1/+1; the pump routine must not fire for it"
    )
    assert len(game.players[0].hand) == hand_before, (
        "no cards should have been discarded to a pump the creature "
        "cannot actually perform"
    )


def test_discard_ability_that_does_pump_still_grows_the_creature(card_db):
    """Regression: Psychic Frog's own 'Discard a card: put a +1/+1
    counter on this creature' must still fire — the fix scopes, it does
    not disable the routine."""
    game, player, frog = _setup(card_db, "Psychic Frog")
    counters_before = frog.plus_counters

    player.decide_attackers(game)

    assert frog.plus_counters > counters_before, (
        "Psychic Frog's real discard-pump ability must still add counters"
    )
