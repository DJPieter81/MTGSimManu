"""Amass (CR 701.47a): "Amass [subtype] N" -- if you control no Army
creature, create a 0/0 black [subtype] Army creature token; then put N +1/+1
counters on an Army creature you control.

One owner (`PermanentEffects.amass`, `GameState.amass`), performed by the
effect dispatcher's KEYWORD_ACTION executor for a typed "amass <subtype> N"
and by every other caller. The token enters through the token owner, and
the counters through the +1/+1 counter funnel.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    return game


def _armies(game, idx=0):
    return [c for c in game.players[idx].battlefield
            if "Army" in (c.template.subtypes or [])]


def test_amass_with_no_army_creates_a_zero_zero_army_token_with_n_counters():
    game = _game()
    army = game.amass(0, 2, "orc")
    assert _armies(game) == [army] and army.is_token
    assert (army.template.power, army.template.toughness) == (0, 0)
    assert army.plus_counters == 2
    assert (army.power, army.toughness) == (2, 2)
    assert {"Orc", "Army"} <= set(army.template.subtypes)


def test_amass_with_an_army_grows_it_and_creates_no_second_token():
    game = _game()
    first = game.amass(0, 1, "orc")
    again = game.amass(0, 1, "orc")
    assert again is first and _armies(game) == [first]
    assert first.plus_counters == 2 and (first.power, first.toughness) == (2, 2)
