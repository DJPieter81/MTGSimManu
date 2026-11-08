"""The line planner finds the best tutor line.

`ai.line_planner.best_tutor_line` reads the card graph's TUTORS edges (a
library search the engine's own matcher admits) and FETCHES_OUTSIDE edges
(a spell that brings a card from outside the game reaches the sideboard,
CR 400.11a). Its payoff is a creature or a storm spell:
- a search that puts the card onto the battlefield is the whole line, and
  an X bound ("mana value X or less") costs the payoff's mana value in X;
- a search into the hand, or a card played from outside the game, is
  followed by casting the payoff;
- a line completes this turn only when its first play is castable now
  (the engine's own check, colours included) and the mana pays the line.
Card names are fixture carriers.
"""
from __future__ import annotations

import random

from ai.line_planner import NEXT_TURN, THIS_TURN, best_tutor_line
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(card_db, deck, lands, hand=(), library=(), sideboard=()):
    g = GameState(rng=random.Random(0))
    g.players[0].deck_name = deck
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0

    def add(n, z):
        c = CardInstance(template=card_db.get_card(n), owner=0, controller=0,
                         instance_id=g.next_instance_id(), zone=z)
        c._game_state = g
        if z == "battlefield":
            c.enter_battlefield()
            c.summoning_sick = False
            c.tapped = False
        getattr(g.players[0], z).append(c)
    for n in lands:
        add(n, "battlefield")
    for n in hand:
        add(n, "hand")
    for n in library:
        add(n, "library")
    for n in sideboard:
        add(n, "sideboard")
    return g


def _names(line):
    return [c.name for c in line.plays]


def test_a_search_into_the_hand_is_followed_by_casting_the_payoff(card_db):
    g = _game(card_db, "Amulet Titan", ["Forest"] * 6,
              hand=["Summoner's Pact"], library=["Primeval Titan", "Forest"])
    line = best_tutor_line(g, 0)
    assert _names(line) == ["Summoner's Pact", "Primeval Titan"]
    assert line.completes == THIS_TURN


def test_a_search_onto_the_battlefield_pays_x_for_the_payoff(card_db):
    g = _game(card_db, "Amulet Titan", ["Forest"] * 7,
              hand=["Green Sun's Zenith"], library=["Primeval Titan"])
    line = best_tutor_line(g, 0)
    assert _names(line) == ["Green Sun's Zenith"]
    assert line.cost == 7 and line.completes == THIS_TURN


def test_too_little_mana_for_x_is_a_later_line(card_db):
    g = _game(card_db, "Amulet Titan", ["Forest"] * 4,
              hand=["Green Sun's Zenith"], library=["Primeval Titan"])
    assert best_tutor_line(g, 0).completes == NEXT_TURN


def test_a_storm_closer_from_outside_the_game_is_a_payoff_when_lethal(
        card_db):
    """The chain reaches lethal with the closer cast last: the sideboard
    Grapeshot is the line's payoff."""
    g = _game(card_db, "Ruby Storm", ["Mountain"] * 5, hand=["Wish"],
              library=["Mountain"], sideboard=["Grapeshot", "Prismatic Ending"])
    g.players[0].spells_cast_this_turn = g._global_storm_count = 12
    g.players[1].life = 5
    line = best_tutor_line(g, 0)
    assert _names(line) == ["Wish", "Grapeshot"]


def test_a_storm_closer_short_of_lethal_is_no_payoff_yet(card_db):
    """Below lethal the chain should grow first: the closer is no line."""
    g = _game(card_db, "Ruby Storm", ["Mountain"] * 5, hand=["Wish"],
              library=["Mountain"], sideboard=["Grapeshot"])
    g.players[1].life = 20
    assert best_tutor_line(g, 0) is None


def test_a_first_play_the_colours_cannot_pay_is_not_this_turn(card_db):
    """Six Islands: Summoner's Pact costs nothing, but Primeval Titan's
    green cannot be paid (the engine's own cast check reads colours)."""
    g = _game(card_db, "Amulet Titan", ["Island"] * 6,
              hand=["Green Sun's Zenith"], library=["Primeval Titan"])
    assert best_tutor_line(g, 0).completes == NEXT_TURN


def test_no_payoff_in_reach_is_no_line(card_db):
    g = _game(card_db, "Amulet Titan", ["Forest"] * 6,
              hand=["Summoner's Pact"], library=["Forest"])
    assert best_tutor_line(g, 0) is None
