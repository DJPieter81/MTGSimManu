"""The line planner finds the shortest reanimation line.

`ai.line_planner.best_line` reads the relations between the player's
cards (`ai.card_graph`) and finds the cheapest line to a payoff. Version 1
covers reanimation:
- RETURN: a returner the player may play, with a creature in the
  graveyard that it can return;
- FILL, then RETURN: a spell whose resolution lets its caster choose a
  card of its own hand to discard (a non-random loot, or a hand attack
  that may target its caster and admits the creature), then the returner.
A line the mana now pays completes this turn. A line whose first play
alone is paid completes next turn. With no path there is no line. Card
names are fixture carriers.
"""
from __future__ import annotations

import random

from ai.line_planner import NEXT_TURN, THIS_TURN, best_line
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone, controller=0):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False             # a land in play since an earlier turn
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, lands, hand=(), graveyard=()):
    g = GameState(rng=random.Random(0))
    for i in range(lands):          # pays U, B and W (the planner reads colours)
        _add(g, card_db, ("Watery Grave", "Hallowed Fountain")[i % 2],
             "battlefield")
    for n in hand:
        _add(g, card_db, n, "hand")
    for n in graveyard:
        _add(g, card_db, n, "graveyard")
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    return g


def _names(line):
    return [c.name for c in line.plays]


def test_a_returner_with_a_returnable_creature_in_the_graveyard(card_db):
    g = _game(card_db, 2, hand=["Persist"], graveyard=["Archon of Cruelty"])
    line = best_line(g, 0)
    assert _names(line) == ["Persist"]
    assert line.payoff.name == "Archon of Cruelty"
    assert line.completes == THIS_TURN


def test_fill_then_return_in_one_turn_when_the_mana_pays_both(card_db):
    g = _game(card_db, 4,
              hand=["Faithful Mending", "Persist", "Archon of Cruelty"])
    line = best_line(g, 0)
    assert _names(line) == ["Faithful Mending", "Persist"]
    assert line.payoff.name == "Archon of Cruelty"
    assert line.completes == THIS_TURN


def test_fill_now_return_next_turn_when_the_mana_pays_the_fill_only(card_db):
    g = _game(card_db, 2,
              hand=["Faithful Mending", "Persist", "Archon of Cruelty"])
    line = best_line(g, 0)
    assert _names(line) == ["Faithful Mending", "Persist"]
    assert line.completes == NEXT_TURN


def test_a_creature_no_returner_can_take_is_no_line(card_db):
    """Persist returns a nonlegendary creature only."""
    g = _game(card_db, 3, hand=["Persist"], graveyard=["Griselbrand"])
    assert best_line(g, 0) is None


def test_a_hand_attack_on_the_casters_own_hand_is_a_fill(card_db):
    g = _game(card_db, 3,
              hand=["Thoughtseize", "Persist", "Archon of Cruelty"])
    line = best_line(g, 0)
    assert _names(line) == ["Thoughtseize", "Persist"]


def test_a_random_discard_is_not_a_fill(card_db):
    """A loot whose discard is random does not let its caster choose."""
    g = _game(card_db, 4,
              hand=["Burning Inquiry", "Persist", "Archon of Cruelty"])
    assert best_line(g, 0) is None


def test_the_line_to_the_bigger_threat_beats_a_cheaper_line(card_db):
    """Reanimation is worth what it returns: Persist on the 7/7 flier, not
    the cheaper Unearth on the 3-mana creature."""
    g = _game(card_db, 3, hand=["Unearth", "Persist"],
              graveyard=["Abhorrent Oculus", "Archon of Cruelty"])
    line = best_line(g, 0)
    assert _names(line) == ["Persist"]
    assert line.payoff.name == "Archon of Cruelty"


def test_a_creature_cheaper_to_cast_than_to_bin_and_return_is_no_line(
        card_db):
    """Looting then Unearth (3 mana) for a 2-mana creature in hand: casting
    it is the cheaper way onto the battlefield."""
    g = _game(card_db, 4, hand=["Faithless Looting", "Unearth",
                                 "Psychic Frog"])
    assert best_line(g, 0) is None
