"""An effect that lasts "until your next turn" persists through the other
players' turns and ends as its controller's next turn begins (CR 611.2b).

The duration is the mechanic; the effects it wraps are the ones the engine
already owns, typed once at load (`CardTemplate.next_turn_effect`) by
running the existing parsers on the text with the duration phrase removed:

* a P/T modifier on a target or on the creatures you control (the pump /
  team-pump shapes, signed);
* a cost reduction (the `parse_cost_reduction` rule shape, counted by the
  one cost-reduction matcher);
* a permission to cast a spell type as though it had flash.

A wrapped effect the engine cannot run in full is refused, never
half-applied. Class: 36 pool loyalty lines print the duration (plus spells).
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

import pytest

from engine.cards import CardInstance
from engine.clause_resolver import resolve_clause
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_until_next_turn


# ── typed at load ────────────────────────────────────────────────────

def test_the_wrapped_shapes_are_typed_with_the_duration_removed():
    assert parse_until_next_turn(
        "Until your next turn, up to one target creature gets -3/-0.") == {
        'kind': 'pt_mod', 'scope': 'target', 'power': -3, 'toughness': 0,
        'keyword': ''}
    assert parse_until_next_turn(
        "Up to one target creature gets -2/-1 until your next turn.")['power'] == -2
    team = parse_until_next_turn(
        "Until your next turn, creatures you control get +1/+0 and gain lifelink.")
    assert team['kind'] == 'pt_mod' and team['scope'] == 'yours'
    assert team['keywords'] == ['lifelink']
    assert parse_until_next_turn(
        "Until your next turn, instant and sorcery spells you cast cost {1} less to cast.") == {
        'kind': 'cost_reduction', 'rule': {'target': 'instant_sorcery', 'amount': 1,
                                           'color': None}}
    assert parse_until_next_turn(
        "Until your next turn, you may cast sorcery spells as though they had flash.") == {
        'kind': 'flash_permission', 'types': ['sorcery']}


def test_compound_or_unowned_wrapped_effects_are_refused():
    assert parse_until_next_turn(
        "Until your next turn, up to one target creature gets -2/-0 and loses flying.") is None
    assert parse_until_next_turn(
        "Until your next turn, you may cast creature spells as though they had flash, "
        "and each creature you control enters with an additional +1/+1 counter on it.") is None
    assert parse_until_next_turn(
        "Until your next turn, whenever a creature attacks you, it gets -1/-0 until end of turn.") is None
    assert parse_until_next_turn("Target creature gets +2/+2 until end of turn.") is None


# ── fixtures ─────────────────────────────────────────────────────────

def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for idx in (0, 1):
        for _ in range(10):
            _put(game, card_db, "Island", idx, "library")
    return game


def _resolve(game, card_db, text, controller=0, targets=None):
    tpl = copy.copy(card_db.get_card("Opt"))
    tpl.oracle_text = text
    tpl.has_scry = False
    tpl.next_turn_effect = parse_until_next_turn(text)
    card = CardInstance(template=tpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    return resolve_clause(game, card, controller, targets or [])


def _turn_passes_to(game, idx):
    game.active_player = idx
    game.untap_step(idx)
    game.continuous_effects.recalculate(game)


# ── the duration ─────────────────────────────────────────────────────

def test_a_minus_modifier_lasts_through_the_opponents_turn_and_ends_at_yours(card_db):
    game = _game(card_db)
    bear = _put(game, card_db, "Grizzly Bears", 1, "battlefield")
    assert _resolve(game, card_db, "Until your next turn, up to one target creature gets -2/-0.",
                    targets=[bear.instance_id])
    game.continuous_effects.recalculate(game)
    assert bear.power == 0
    game.end_of_turn_cleanup()
    _turn_passes_to(game, 1)
    assert bear.power == 0            # still in effect on the opponent's turn
    _turn_passes_to(game, 0)
    assert bear.power == 2            # ends as the controller's turn begins


def test_a_minus_modifier_with_no_chosen_target_picks_an_opposing_creature(card_db):
    game = _game(card_db)
    mine = _put(game, card_db, "Grizzly Bears", 0, "battlefield")
    theirs = _put(game, card_db, "Grizzly Bears", 1, "battlefield")
    _resolve(game, card_db, "Up to one target creature gets -2/-0 until your next turn.")
    game.continuous_effects.recalculate(game)
    assert theirs.power == 0 and mine.power == 2


def test_a_team_modifier_lasts_until_your_next_turn(card_db):
    game = _game(card_db)
    a = _put(game, card_db, "Grizzly Bears", 0, "battlefield")
    _resolve(game, card_db, "Until your next turn, creatures you control get +1/+0 and gain lifelink.")
    game.continuous_effects.recalculate(game)
    assert a.power == 3
    game.end_of_turn_cleanup()
    _turn_passes_to(game, 1)
    game.continuous_effects.recalculate(game)
    assert a.power == 3
    _turn_passes_to(game, 0)
    assert a.power == 2


def test_a_cost_reduction_applies_to_its_spell_class_until_your_next_turn(card_db):
    from engine.oracle_resolver import count_cost_reducers
    game = _game(card_db)
    bolt = card_db.get_card("Lightning Bolt")
    bears = card_db.get_card("Grizzly Bears")
    _resolve(game, card_db, "Until your next turn, instant and sorcery spells you cast cost {1} less to cast.")
    assert count_cost_reducers(game, 0, bolt) == 1
    assert count_cost_reducers(game, 0, bears) == 0
    game.end_of_turn_cleanup()
    _turn_passes_to(game, 1)
    assert count_cost_reducers(game, 0, bolt) == 1
    _turn_passes_to(game, 0)
    assert count_cost_reducers(game, 0, bolt) == 0


def test_a_flash_permission_lets_a_sorcery_be_cast_on_the_opponents_turn(card_db):
    game = _game(card_db)
    for _ in range(3):
        _put(game, card_db, "Mountain", 0, "battlefield")
    _put(game, card_db, "Grizzly Bears", 1, "battlefield")   # a legal target
    sorcery = _put(game, card_db, "Lava Coil", 0, "hand")
    _resolve(game, card_db, "Until your next turn, you may cast sorcery spells as though they had flash.")
    game.end_of_turn_cleanup()
    game.active_player = 1
    game.current_phase = Phase.MAIN1
    assert game.can_cast(0, sorcery)
    _turn_passes_to(game, 0)
    game.active_player = 1
    assert not game.can_cast(0, sorcery)
