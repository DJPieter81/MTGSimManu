"""Track H (2026-07-05 calibration wave) — activated win-condition
lines enter main-phase scoring: planeswalker ultimate lines.

Mechanic under test: a planeswalker loyalty ability that WINS or LOCKS
the game (an "I win" line or a repeating-emblem lock) is a win
condition. Its value must enter play scoring when the loyalty
trajectory reaches it — starting loyalty plus repeated plus-ability
ticks reach the ultimate's cost before the opponent's clock ends the
game. Pre-fix, planeswalkers scored ~0 in the generic spell scorer and
control decks never deployed their activated win conditions.

Rules, phrased on the mechanic (no card names):

1. **Reachable win/lock ultimate is valued** — loyalty trajectory
   reaches the ultimate inside the opponent-clock horizon → positive
   value.
2. **Unreachable ultimate is not valued** — the opponent's clock ends
   the game before the trajectory arrives → zero.
3. **Non-win ultimate is not valued by this term** — a plain
   value ultimate is not a win-condition line.
4. **Play scoring consumes the term** — with identical cards except
   the ultimate line, the win/lock walker outscores the plain one in
   ``_score_spell``.

All cards are synthetic oracle text — no real card names.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState, Phase


_WIN_LOCK_ORACLE = (
    "[+1]: Draw a card.\n"
    "[−3]: Return target creature to its owner's hand.\n"
    "[−8]: You get an emblem with \"Whenever you draw a card, "
    "exile target permanent an opponent controls.\""
)

_PLAIN_ULT_ORACLE = (
    "[+1]: Draw a card.\n"
    "[−3]: Return target creature to its owner's hand.\n"
    "[−8]: Draw three cards.\n"
)

_STARTING_LOYALTY = 4


def _pw_template(name: str, oracle_text: str) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.PLANESWALKER],
        mana_cost=ManaCost(generic=5),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=_STARTING_LOYALTY,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text=oracle_text,
        tags=set(),
    )


def _creature_template(name: str, power: int, toughness: int) -> CardTemplate:
    return CardTemplate(
        name=name,
        card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=power),
        supertypes=[], subtypes=[],
        power=power, toughness=toughness, loyalty=None,
        keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[],
        enters_tapped=False,
        oracle_text="",
        tags=set(),
    )


def _instance(game: GameState, tmpl: CardTemplate, controller: int,
              zone: str) -> CardInstance:
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _game_with_opp_clock(*, opp_power: int, my_life: int = 20) -> GameState:
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "SyntheticControl"
    game.players[1].deck_name = "SyntheticOpponent"
    game.players[0].life = my_life
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    if opp_power > 0:
        _instance(game,
                  _creature_template("Synthetic Attacker",
                                     opp_power, opp_power),
                  1, "battlefield")
    return game


# ─────────────────────────────────────────────────────────────
# 1. Reachable win/lock ultimate → positive value
# ─────────────────────────────────────────────────────────────

def test_win_lock_ultimate_valued_when_loyalty_trajectory_reaches_it():
    """No opposing pressure: the trajectory (4 loyalty, +1/turn,
    ultimate at 8) reaches the win line comfortably inside the
    opponent-clock horizon. The win-condition term must be positive."""
    from ai.pw_ability import ultimate_win_line_value

    game = _game_with_opp_clock(opp_power=0)
    snap = snapshot_from_game(game, 0)

    value = ultimate_win_line_value(
        _WIN_LOCK_ORACLE, _STARTING_LOYALTY, snap)
    assert value > 0, (
        "a win/lock ultimate whose loyalty trajectory arrives inside "
        "the opponent-clock horizon must contribute positive value"
    )


# ─────────────────────────────────────────────────────────────
# 2. Unreachable (opponent clock too fast) → zero
# ─────────────────────────────────────────────────────────────

def test_win_lock_ultimate_not_valued_when_controller_dies_first():
    """Opponent kills us in ~1 turn; the trajectory needs 4+ turns of
    plus-ticks. The line is unreachable — zero value."""
    from ai.pw_ability import ultimate_win_line_value

    game = _game_with_opp_clock(opp_power=6, my_life=6)
    snap = snapshot_from_game(game, 0)

    value = ultimate_win_line_value(
        _WIN_LOCK_ORACLE, _STARTING_LOYALTY, snap)
    assert value == 0, (
        "a win/lock ultimate the controller does not live to reach "
        "must contribute nothing"
    )


# ─────────────────────────────────────────────────────────────
# 3. Non-win ultimate → zero from this term
# ─────────────────────────────────────────────────────────────

def test_plain_value_ultimate_adds_no_win_condition_value():
    from ai.pw_ability import ultimate_win_line_value

    game = _game_with_opp_clock(opp_power=0)
    snap = snapshot_from_game(game, 0)

    value = ultimate_win_line_value(
        _PLAIN_ULT_ORACLE, _STARTING_LOYALTY, snap)
    assert value == 0, (
        "a plain value ultimate is not a win-condition line and must "
        "not receive the win-condition term"
    )


# ─────────────────────────────────────────────────────────────
# 4. Play scoring consumes the term
# ─────────────────────────────────────────────────────────────

def test_play_scoring_prefers_reachable_win_ultimate():
    """Two walkers identical except the ultimate line. With a slow
    opponent, the win/lock walker must outscore the plain one in
    ``_score_spell`` — the win-condition term is IN play scoring."""
    from ai.ev_player import EVPlayer

    game = _game_with_opp_clock(opp_power=1)
    me = game.players[0]
    opp = game.players[1]

    win_pw = _instance(
        game, _pw_template("Synthetic Win Walker", _WIN_LOCK_ORACLE),
        0, "hand")
    plain_pw = _instance(
        game, _pw_template("Synthetic Value Walker", _PLAIN_ULT_ORACLE),
        0, "hand")

    ai = EVPlayer(0, "SyntheticControl", rng=random.Random(0))
    snap = snapshot_from_game(game, 0)

    ev_win = ai._score_spell(win_pw, snap, game, me, opp)
    ev_plain = ai._score_spell(plain_pw, snap, game, me, opp)
    assert ev_win > ev_plain, (
        f"the reachable win/lock ultimate must be valued in play "
        f"scoring (win={ev_win:.2f} vs plain={ev_plain:.2f})"
    )
