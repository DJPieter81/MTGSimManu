"""A "this turn" per-player event counter must reset at every turn
boundary — for the NON-active player too, not only the player whose
untap step is running.

Rule (CR 500.4 / the "this turn" window): counters that tally what
happened during a turn — a permanent left the battlefield this turn
(revolt, morbid), an opponent lost life this turn (spectacle), you
gained life this turn, you drew a card this turn — are scoped to the
current game turn. When a new turn begins, the window resets for every
player. A permanent that left under player B's control during player
B's turn must NOT still read as "left the battlefield this turn" while
player A is taking their subsequent turn.

The bug: `untap_step` reset only the active player's tracking, so the
non-active player's `creatures_died_this_turn` (the revolt tracker,
also set by fetchland cracks) leaked from their own prior turn straight
through the opponent's whole turn — an instant-speed effect read a
stale, turn-old value. Concretely (audit: Living End vs Dimir, s55610):
Dimir cracked a fetch on turn 3, then cast Fatal Push on turn 4 (Living
End's turn) and illegally got Revolt (MV<=4) off the turn-3 crack.

Card names below are fixture carriers; the rule is the counter's
lifetime, not any one card.
"""
from __future__ import annotations

import random

from engine.game_state import GameState
from engine.player_state import PlayerState


def _two_player_game() -> GameState:
    game = GameState(rng=random.Random(0))
    # GameState already constructs two PlayerStates; make sure both exist.
    assert len(game.players) == 2
    return game


def test_left_battlefield_counter_resets_for_nonactive_player_on_opponent_untap():
    """Revolt/morbid tracker set on a player's own turn must be gone by
    the time the OPPONENT untaps for their turn — otherwise it grants
    revolt at instant speed a full turn later."""
    game = _two_player_game()
    # Player 1 had a permanent leave the battlefield during their own turn.
    game.players[1].creatures_died_this_turn = 1

    # The turn passes to player 0; player 0's untap step runs.
    game.untap_step(0)

    assert game.players[1].creatures_died_this_turn == 0, (
        "the non-active player's 'left the battlefield this turn' counter "
        "must reset at the turn boundary, not persist into the opponent's turn"
    )


def test_active_player_counter_also_resets_on_own_untap():
    """Regression guard: the active player's own tracking still resets
    (the pre-existing behaviour must not break)."""
    game = _two_player_game()
    game.players[0].creatures_died_this_turn = 2
    game.players[0].life_gained_this_turn = 3

    game.untap_step(0)

    assert game.players[0].creatures_died_this_turn == 0
    assert game.players[0].life_gained_this_turn == 0


def test_event_counter_family_resets_for_nonactive_player():
    """The whole 'this turn' event-tally family — not just one field —
    must clear for the non-active player at the boundary."""
    game = _two_player_game()
    opp = game.players[1]
    opp.creatures_died_this_turn = 1
    opp.life_gained_this_turn = 4
    opp.life_lost_this_turn = 2
    opp.damage_dealt_this_turn = 5
    opp.cards_drawn_this_turn = 3

    game.untap_step(0)

    assert opp.creatures_died_this_turn == 0
    assert opp.life_gained_this_turn == 0
    assert opp.life_lost_this_turn == 0
    assert opp.damage_dealt_this_turn == 0
    assert opp.cards_drawn_this_turn == 0


def test_nonactive_silence_lifecycle_not_advanced_early():
    """The cross-turn reset must NOT run the non-active player's special
    'next turn' lifecycle (Orim's Chant silence): a silence queued for
    the opponent's OWN next turn must not be consumed a turn early when
    the active player untaps."""
    game = _two_player_game()
    opp = game.players[1]
    opp.silenced_next_turn = True

    game.untap_step(0)  # the active player's untap, NOT the opponent's

    # The queued silence still awaits the opponent's own untap.
    assert getattr(opp, "silenced_next_turn", False) is True, (
        "a 'next turn' silence on the non-active player must survive the "
        "active player's untap and fire at the opponent's own next turn"
    )
    assert getattr(opp, "silenced_this_turn", False) is False
