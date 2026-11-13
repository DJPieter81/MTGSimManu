"""A spell whose every target is a card in a graveyard is answered by
exiling that card in response (AI, from CR 608.2b).

A spell does not resolve when every target it has is illegal (CR 608.2b);
a card that left the graveyard it was chosen in is no longer that target
(CR 400.7). So an instant whose own resolution removes the targeted card
from its graveyard -- an exile of that target, or a search exiling every
card sharing its name from its owner's graveyard -- answers a reanimation
spell the way a counter does. The AI answers when the spell threatens more
than holding the answer is worth: the counter triage's currency
(`held_counter_floor_ev`).

The defect: the AI never cast its graveyard answers. Surgical Extraction,
in six registered sideboards against the graveyard decks, sat in hand
while Goryo's Vengeance returned Griselbrand.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    """Player 1 (active) holds Goryo's Vengeance with Griselbrand in their
    graveyard; player 0 holds Surgical Extraction with a Swamp untapped."""
    game = GameState(rng=random.Random(0))
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Swamp", 0, "battlefield")
        _add(game, card_db, "Swamp", 1, "battlefield")
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    gris = _add(game, card_db, "Griselbrand", 1, "graveyard")
    goryos = _add(game, card_db, "Goryo's Vengeance", 1, "hand")
    surgical = _add(game, card_db, "Surgical Extraction", 0, "hand")
    return game, gris, goryos, surgical


def _decider():
    from ai.response import ResponseDecider
    return ResponseDecider(player_idx=0)


# ── The answer's shape ────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Surgical Extraction", "Extirpate"])
def test_a_same_name_exile_removes_its_chosen_graveyard_card(card_db, name):
    from ai.predicates import graveyard_target_removal
    host = card_db.get_card(name).effects.spell(0)
    assert graveyard_target_removal(host) == (0, True)


def test_a_spell_that_removes_no_graveyard_card_is_no_answer(card_db):
    from ai.predicates import graveyard_target_removal
    assert graveyard_target_removal(
        card_db.get_card("Lightning Bolt").effects.spell(0)) is None


# ── The response ──────────────────────────────────────────────────────

def test_a_reanimation_spell_is_answered_by_exiling_its_target(card_db):
    game, gris, goryos, surgical = _game(card_db)
    assert game.cast_spell(1, goryos, [gris.instance_id])
    response = _decider().decide_response(game, game.stack.top)
    assert response is not None
    card, targets = response
    assert card is surgical and targets == [gris.instance_id]


def test_the_answered_spell_does_nothing(card_db):
    """CR 608.2b: Griselbrand left the graveyard; Goryo's Vengeance does
    not resolve."""
    game, gris, goryos, surgical = _game(card_db)
    assert game.cast_spell(1, goryos, [gris.instance_id])
    card, targets = _decider().decide_response(game, game.stack.top)
    assert game.cast_spell(0, card, targets)
    while not game.stack.is_empty:
        game.resolve_stack()
    assert gris.zone == "exile"
    assert all(c is not gris for c in game.players[1].battlefield)


def test_a_spell_aimed_at_the_battlefield_is_not_answered_this_way(card_db):
    game, gris, goryos, surgical = _game(card_db)
    bear = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    _add(game, card_db, "Mountain", 1, "battlefield")
    bolt = _add(game, card_db, "Lightning Bolt", 1, "hand")
    assert game.cast_spell(1, bolt, [bear.instance_id])
    response = _decider().decide_response(game, game.stack.top)
    assert response is None or response[0] is not surgical


def test_it_answers_exactly_when_the_threat_outweighs_holding_the_card(
        card_db):
    """The decision rule, from the same primitives: answer iff the stack
    threat exceeds the held-answer floor."""
    from ai.clock import card_clock_impact
    from ai.ev_evaluator import snapshot_from_game
    from ai.scoring_constants import CLOCK_IMPACT_LIFE_SCALING
    game, gris, goryos, surgical = _game(card_db)
    assert game.cast_spell(1, goryos, [gris.instance_id])
    decider = _decider()
    threat = decider.evaluate_stack_threat(game, game.stack.top)
    floor = card_clock_impact(snapshot_from_game(game, 0)) \
        * CLOCK_IMPACT_LIFE_SCALING
    response = decider.decide_response(game, game.stack.top)
    answered = response is not None and response[0] is surgical
    assert answered == (threat > floor)
    assert answered            # a reanimated Griselbrand clears the floor
