"""The AI's cost estimate never reduces coloured pips (CR 601.2f).

A generic cost reduction ("red spells you cast cost {1} less") reduces only
the generic component of a spell's total cost; its coloured, colourless and
hybrid pips remain owed. The engine charges exactly that
(`CastManager.can_cast` floors the quantity at `ManaCost.non_generic_pips`).
The AI's estimate (`ai/effective_cmc.effective_cmc`) is the owner the AI's
cost reads go through. It subtracted the reductions from the whole mana
value, so a {1}{R} ritual read as free under two reducers, and a {R} spell
under one: the chain arithmetic counted mana that does not exist.

The defect: docs/diagnostics/2026-10-15_ruby_storm_goldfish_speed.md
(secondary findings, AI). Card names are fixture carriers: the class is
every spell with pips cast under generic reductions that exceed its
generic part.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, reducers):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(reducers):
        _add(game, card_db, "Ruby Medallion", 0)
    for _ in range(5):
        _add(game, card_db, "Mountain", 0, "library")
    game.continuous_effects.recalculate(game)
    return game


def _estimate(game, card):
    from ai.effective_cmc import effective_cmc
    return effective_cmc(card, game=game, player_idx=0)


@pytest.mark.parametrize("name, reducers, due", [
    ("Pyretic Ritual", 2, 1),      # {1}{R}: the {R} remains
    ("Lightning Bolt", 1, 1),      # {R}: nothing to reduce
    ("Manamorphose", 2, 1),        # {1}{R/G}: a hybrid pip remains
    ("Past in Flames", 2, 2),      # {3}{R}: two generic off
    ("Past in Flames", 1, 3),      # within the generic part: unchanged
])
def test_a_generic_reduction_stops_at_the_coloured_pips(card_db, name,
                                                        reducers, due):
    game = _game(card_db, reducers)
    spell = _add(game, card_db, name, 0, "hand")
    assert _estimate(game, spell) == due


@pytest.mark.parametrize("name, reducers", [
    ("Pyretic Ritual", 2), ("Lightning Bolt", 1), ("Manamorphose", 2),
    ("Past in Flames", 2)])
def test_the_estimate_is_what_the_engine_charges(card_db, name, reducers):
    """One fewer land than the estimate cannot cast the spell; the
    estimate's lands can."""
    game = _game(card_db, reducers)
    spell = _add(game, card_db, name, 0, "hand")
    due = _estimate(game, spell)
    lands = [_add(game, card_db, "Mountain", 0) for _ in range(due)]
    targets = [-2] if name == "Lightning Bolt" else []
    assert game.can_cast(0, spell)
    lands[0].tapped = True
    assert not game.can_cast(0, spell), (
        f"{name} is castable with {due - 1} lands: the estimate is high")
    lands[0].tapped = False
    assert game.cast_spell(0, spell, targets)
    assert all(l.tapped for l in lands)
