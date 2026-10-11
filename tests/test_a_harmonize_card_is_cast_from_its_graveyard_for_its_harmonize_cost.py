"""Harmonize (CR 702.180): a card with harmonize may be cast from its
owner's graveyard by paying its harmonize cost instead of its mana cost.
As it is cast, its caster may tap an untapped creature they control; the
total cost is then reduced by an amount of generic mana equal to that
creature's power. If the harmonize cost was paid, the card is exiled
instead of going anywhere else as it leaves the stack.

The defect: harmonize was unmodelled (the keyword coverage census lists
it with no status). A harmonize card in a graveyard could not be cast.
Creatures Toolbox plays four Nature's Rhythm ("Search your library for a
creature card with mana value X or less, put it onto the battlefield.
Harmonize {X}{G}{G}{G}{G}"), whose second use never happened.

Card names are fixture carriers: 11 pool cards print harmonize.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _game(card_db, lands=5, land="Mountain"):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(lands):
        _add(game, card_db, land, 0, "battlefield")
    for _ in range(5):
        _add(game, card_db, "Island", 1, "library")
        _add(game, card_db, "Forest", 0, "library")
    return game


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


# ── The cost, typed at load ───────────────────────────────────────────

def test_the_harmonize_cost_is_typed_at_load(card_db):
    ride = card_db.get_card("Wild Ride").harmonize_cost
    assert (ride.generic, ride.red) == (4, 1)
    rhythm = card_db.get_card("Nature's Rhythm").harmonize_cost
    assert (rhythm.generic, rhythm.green) == (0, 4), "X rides on x_cost_data"
    assert card_db.get_card("Lightning Bolt").harmonize_cost is None


# ── The graveyard cast ────────────────────────────────────────────────

def test_a_harmonize_card_is_castable_from_its_graveyard(card_db):
    game = _game(card_db, lands=5)
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    ride = _add(game, card_db, "Wild Ride", 0, "graveyard")
    assert game.can_cast(0, ride)
    assert ride in game.get_legal_plays(0)
    assert game.cast_spell(0, ride, [bears.instance_id])
    _resolve(game)
    assert ride.zone == "exile", "exiled as it leaves the stack"
    assert bears.power == 5


def test_a_tapped_creatures_power_reduces_the_generic_cost(card_db):
    """{4}{R} with two lands and a 2-power creature to tap: not payable
    alone, payable with the tap -- and the creature is tapped."""
    game = _game(card_db, lands=3)
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    ride = _add(game, card_db, "Wild Ride", 0, "graveyard")
    assert game.can_cast(0, ride)
    assert game.cast_spell(0, ride, [bears.instance_id])
    assert bears.tapped
    assert all(land.tapped for land in game.players[0].lands)


def test_without_enough_mana_or_power_it_is_not_castable(card_db):
    game = _game(card_db, lands=2)
    _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    ride = _add(game, card_db, "Wild Ride", 0, "graveyard")
    assert not game.can_cast(0, ride)


def test_a_card_without_harmonize_is_not_cast_from_a_graveyard(card_db):
    game = _game(card_db, lands=5)
    bolt = _add(game, card_db, "Lightning Bolt", 0, "graveyard")
    assert not game.can_cast(0, bolt)


def test_x_is_chosen_against_the_harmonize_cost(card_db):
    """Harmonize {X}{G}{G}{G}{G} with six Forests and a 2-power creature
    to tap reaches X = 6 - 4 + 2 = 4: the tap's reduction beyond the
    cost's generic part lowers the X paid. A creature tutor picks the X its
    best reachable creature needs -- here the mana value 4 one, which the
    two Forests left alone could not reach."""
    game = _game(card_db, lands=6, land="Forest")
    _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    _add(game, card_db, "Ravenous Chupacabra", 0, "library")
    rhythm = _add(game, card_db, "Nature's Rhythm", 0, "graveyard")
    assert game.can_cast(0, rhythm)
    assert game.cast_spell(0, rhythm, [])
    assert game.stack.items[-1].x_value == 4
    _resolve(game)
    assert any(c.name == "Ravenous Chupacabra"
               for c in game.players[0].battlefield)
    assert rhythm.zone == "exile"


# ── The auditor (CR 702.180a) ─────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "702.180a/exiled"]


def test_the_audit_records_a_harmonized_spell_left_in_the_graveyard(
        card_db, audit):
    """Restated from the raw facts -- a harmonize card cast from a
    graveyard with no flashback or escape -- not from the routing flag."""
    game = _game(card_db, lands=5)
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    ride = _add(game, card_db, "Wild Ride", 0, "graveyard")
    assert game.cast_spell(0, ride, [bears.instance_id])
    ride._cast_with_harmonize = False            # the defect
    _resolve(game)
    assert ride.zone == "graveyard"
    assert _findings(audit)


def test_the_audit_is_silent_when_the_harmonized_spell_is_exiled(card_db,
                                                                 audit):
    game = _game(card_db, lands=5)
    bears = _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    ride = _add(game, card_db, "Wild Ride", 0, "graveyard")
    assert game.cast_spell(0, ride, [bears.instance_id])
    _resolve(game)
    assert not _findings(audit)
