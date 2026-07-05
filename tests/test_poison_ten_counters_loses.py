"""CR 704.5c — a player with ten or more poison counters loses the game.

The rule existed only in the dead SBAManager loop
(engine/sba_manager.py:91-100, zero live callers); the live SBA path in
game_state.check_state_based_actions never checked poison, even though
PlayerState.poison_counters exists (engine/player_state.py:41).

Ported per docs/proposals/resolver_sba_unification.md §5.4: one
implementation, SBAManager.perform_poison_check(game), called from both
SBA entry points; threshold is the POISON_COUNTER_LETHAL rules constant.
"""
from __future__ import annotations

import random

from engine.constants import POISON_COUNTER_LETHAL
from engine.game_state import GameState


def _fresh_game() -> GameState:
    return GameState(rng=random.Random(0))


def test_player_with_ten_or_more_poison_counters_loses():
    game = _fresh_game()
    game.players[1].poison_counters = POISON_COUNTER_LETHAL

    game.check_state_based_actions()

    assert game.game_over
    assert game.winner == 0
    assert any("704.5c" in line for line in game.log)


def test_player_below_poison_threshold_does_not_lose():
    game = _fresh_game()
    game.players[1].poison_counters = POISON_COUNTER_LETHAL - 1

    game.check_state_based_actions()

    assert not game.game_over
    assert game.winner is None
