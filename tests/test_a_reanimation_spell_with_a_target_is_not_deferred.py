"""A reanimation spell with a creature to return is not deferred.

The main phase defers a cast that delivers nothing this turn: no same-turn
signal fires (`ai.ev_evaluator._enumerate_this_turn_signals`), so
`decide_main_phase` passes rather than cast it. A spell that returns a
creature card from its caster's graveyard to the battlefield delivers that
creature this turn. Whether it can return a given card is the returner's
parsed graveyard requirement (`ai.card_classes.deck_can_return`, the owner
the card graph's RETURNS edges read). With such a card in the graveyard,
the cast is not deferrable.

The defect: no signal covered one-sided reanimation, so Persist and
Unburial Rites were deferred forever. Goryo's Vengeance and Unearth got
through only on unrelated signals. Grixis Reanimator vs Azorius Blink
s53000: with Archon of Cruelty in the graveyard, Persist scored +48.9 and
the AI cast Emperor of Bones at +1.8. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
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
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, graveyard):
    g = GameState(rng=random.Random(0))
    for _ in range(5):
        _add(g, card_db, "Swamp", "battlefield")
    for n in graveyard:
        _add(g, card_db, n, "graveyard")
    for _ in range(10):
        _add(g, card_db, "Swamp", "library")
        _add(g, card_db, "Island", "library", controller=1)
    g.players[0].deck_name = "Grixis Reanimator"
    g.players[1].deck_name = "Azorius Blink"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    return g


@pytest.mark.parametrize("spell", ["Persist", "Unburial Rites"])
def test_a_returnable_creature_in_the_graveyard_is_a_same_turn_signal(
        card_db, spell):
    g = _game(card_db, ["Archon of Cruelty"])
    c = _add(g, card_db, spell, "hand")
    assert _enumerate_this_turn_signals(c, snapshot_from_game(g, 0), g, 0)


def test_no_creature_it_can_return_is_no_signal(card_db):
    """Persist returns a NONlegendary creature; a legendary one is not
    its target."""
    g = _game(card_db, ["Griselbrand"])
    c = _add(g, card_db, "Persist", "hand")
    assert "reanimates_now" not in _enumerate_this_turn_signals(
        c, snapshot_from_game(g, 0), g, 0)


def test_the_main_phase_casts_the_reanimation(card_db):
    from ai.ev_player import EVPlayer
    g = _game(card_db, ["Archon of Cruelty"])
    _add(g, card_db, "Persist", "hand")
    player = EVPlayer(player_idx=0, deck_name="Grixis Reanimator",
                      rng=random.Random(0))
    chosen = player.decide_main_phase(g)
    assert chosen is not None and chosen[1].name == "Persist"
