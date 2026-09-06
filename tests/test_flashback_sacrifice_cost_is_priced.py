"""A Flashback cost that sacrifices a land is paid in full (CR 702.33a),
and the decision layer prices the land it gives up.

# Mechanic the tests name

The engine paid the additional cost ("Flashback—Sacrifice a Mountain.")
by a runtime regex at cast time; the AI priced a graveyard cast at its
mana only.  Once a one-damage burn spell reached the burn branch, Izzet
Prowess flashed it back on turn 2 by sacrificing its only untapped land
for one face damage at 17 life (Izzet Prowess vs Dimir Midrange s50500),
and stayed on one land for the game.  The land's value to its owner is
the own-side land-denial term — a land-drop turn of tempo while still
below the curve top, plus the colour pips stranded when it was the last
source — the same two terms the opponent-side valuation already
derives.  Both readers now share one typed field.

Class: every printed Flashback with a sacrifice rider; every spell cast
from a graveyard under such a cost.  Card names below are fixture
carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_flashback_sacrifice


def _put(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
        game.players[controller].battlefield.append(c)
    elif zone == "library":
        game.players[controller].library.append(c)
    elif zone == "graveyard":
        game.players[controller].graveyard.append(c)
    else:
        game.players[controller].hand.append(c)
    if 'flashback' in (tmpl.tags or set()):
        c.has_flashback = True      # what GameState sets at deck load
    return c


def _game(card_db, lands=2):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(lands):
        _put(game, card_db, "Mountain", 0, "battlefield")
    for _ in range(4):
        _put(game, card_db, "Mountain", 1, "library")
    return game


def test_the_sacrificed_land_subtype_is_a_parsed_field():
    assert parse_flashback_sacrifice("Lava Dart deals 1 damage to any target. / Flashback—Sacrifice a Mountain.") == "mountain"
    assert parse_flashback_sacrifice("Flashback {1}{W} (You may cast this card from your graveyard for its flashback cost.)") is None
    assert parse_flashback_sacrifice("") is None


def test_the_cast_path_pays_the_typed_sacrifice(card_db):
    game = _game(card_db, lands=2)
    dart = _put(game, card_db, "Lava Dart", 0, "graveyard")
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    assert card_db.get_card("Lava Dart").flashback_sacrifice_subtype == "mountain"
    lands_before = len(game.players[0].lands)
    assert game.can_cast(0, dart)
    assert game.cast_spell(0, dart)
    assert len(game.players[0].lands) == lands_before - 1


def test_a_flashback_cast_charges_the_land_it_sacrifices(card_db):
    from ai.ev_evaluator import snapshot_from_game
    from ai.ev_player import EVPlayer
    from ai.land_denial import own_land_loss_value
    game = _game(card_db, lands=2)
    # A library with a real land density — the replacement horizon is
    # derived from it.
    for _ in range(6):
        _put(game, card_db, "Mountain", 0, "library")
    for _ in range(12):
        _put(game, card_db, "Lightning Bolt", 0, "library")
    # Below the curve top: a two-drop still in hand.
    _put(game, card_db, "Slickshot Show-Off", 0, "hand")
    in_hand = _put(game, card_db, "Lava Dart", 0, "hand")
    in_gy = _put(game, card_db, "Lava Dart", 0, "graveyard")
    _put(game, card_db, "Quantum Riddler", 1, "battlefield")
    ai = EVPlayer(player_idx=0, deck_name="Izzet Prowess", rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me, opp = game.players[0], game.players[1]
    land = next(l for l in me.lands)
    loss = own_land_loss_value(game, 0, land, snap)
    assert loss > 0
    from_hand = ai._score_spell(in_hand, snap, game, me, opp)
    from_graveyard = ai._score_spell(in_gy, snap, game, me, opp)
    # The graveyard cast is charged exactly the land it gives up.
    assert from_hand - from_graveyard == pytest.approx(loss, abs=1e-6)


def test_a_flooded_caster_with_a_spare_source_pays_little(card_db):
    from ai.ev_evaluator import snapshot_from_game
    from ai.land_denial import own_land_loss_value
    game = _game(card_db, lands=6)               # far above a one-drop curve
    _put(game, card_db, "Lava Dart", 0, "hand")
    snap = snapshot_from_game(game, 0)
    land = next(l for l in game.players[0].lands)
    assert own_land_loss_value(game, 0, land, snap) == 0.0
