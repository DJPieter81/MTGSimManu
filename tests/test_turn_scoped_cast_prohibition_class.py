"""A resolved "<who> can't cast [<type>] spells this turn" effect prohibits
those casts for the rest of the turn (CR 101.2 — a "can't" effect).

The shape is parsed once into `CardTemplate.cast_prohibition`
({'who': 'target'|'opponents'|'all', 'filter': 'all'|'noncreature'|'creature'})
and applied by one generic resolver branch. Before this, only one card's
name-keyed handler ever set the flag: every other member of the class
resolved as nothing (the census recorded `unhandled/spell` for the 3 copies
in Azorius Control's refreshed list, 2026-09-27).

Class: 10 pool cards carry the clause; the three `who` scopes and three
filters here cover 5 of them (conditional scopes — "players dealt damage
this way", "its controller", "if mana was spent" — are refused).
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.card_effects import EFFECT_REGISTRY
from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_cast_prohibition
from engine.stack import StackItem, StackItemType

OPPONENTS_ALL = "Your opponents can't cast spells this turn."
TARGET_ALL = "Target player can't cast spells this turn."
OPPONENTS_NONCREATURE = "Your opponents can't cast noncreature spells this turn."
ALL_PLAYERS_NONCREATURE = "Players can't cast noncreature spells this turn."


def test_the_prohibition_shape_is_parsed_into_scope_and_filter():
    assert parse_cast_prohibition(OPPONENTS_ALL) == {'who': 'opponents', 'filter': 'all'}
    assert parse_cast_prohibition(TARGET_ALL) == {'who': 'target', 'filter': 'all'}
    assert parse_cast_prohibition(OPPONENTS_NONCREATURE) == {
        'who': 'opponents', 'filter': 'noncreature'}
    assert parse_cast_prohibition(ALL_PLAYERS_NONCREATURE) == {
        'who': 'all', 'filter': 'noncreature'}
    # Conditional scopes are refused rather than half-applied.
    assert parse_cast_prohibition(
        "Players dealt damage this way can't cast noncreature spells this turn.") is None
    assert parse_cast_prohibition("Draw a card.") is None


def test_the_typed_field_is_populated_across_the_class(card_db):
    typed = [t for t in card_db.cards.values() if t.cast_prohibition]
    assert len(typed) >= 5, [t.name for t in typed]


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
        for _ in range(3):
            _put(game, card_db, "Mountain", idx, "battlefield")
    return game


def _resolve_with_oracle(game, card_db, oracle, controller=0, kick_count=0):
    """Resolve an instant carrying `oracle` (a fixture template)."""
    tmpl = copy.copy(card_db.get_card("Silence"))
    tmpl.oracle_text = oracle
    tmpl.cast_prohibition = parse_cast_prohibition(oracle)
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=card,
                     controller=controller, targets=[])
    item.kick_count = kick_count
    game.stack.push(item)
    game.resolve_stack()


def test_an_opponents_prohibition_stops_the_opponent_and_not_the_caster(card_db):
    game = _game(card_db)
    opp_spell = _put(game, card_db, "Lightning Bolt", 1, "hand")
    own_spell = _put(game, card_db, "Lightning Bolt", 0, "hand")
    assert game.can_cast(0, own_spell)
    _resolve_with_oracle(game, card_db, OPPONENTS_ALL)
    game.active_player = 1
    assert not game.can_cast(1, opp_spell)
    game.active_player = 0
    assert game.can_cast(0, own_spell)


def test_a_real_member_of_the_class_resolves_through_the_generic_branch(card_db):
    game = _game(card_db)
    card = CardInstance(template=card_db.get_card("Silence"), owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=card,
                              controller=0, targets=[]))
    game.resolve_stack()
    assert game.players[1].silenced_this_turn
    assert not game.players[0].silenced_this_turn


def test_a_noncreature_filter_stops_instants_but_not_creature_spells(card_db):
    game = _game(card_db)
    bolt = _put(game, card_db, "Lightning Bolt", 1, "hand")
    goblin = _put(game, card_db, "Goblin Guide", 1, "hand")
    _resolve_with_oracle(game, card_db, OPPONENTS_NONCREATURE)
    game.active_player = 1
    assert not game.can_cast(1, bolt)
    assert game.can_cast(1, goblin)


def test_an_all_players_prohibition_stops_both_players(card_db):
    game = _game(card_db)
    b0 = _put(game, card_db, "Lightning Bolt", 0, "hand")
    b1 = _put(game, card_db, "Lightning Bolt", 1, "hand")
    _resolve_with_oracle(game, card_db, ALL_PLAYERS_NONCREATURE)
    assert not game.can_cast(0, b0)
    game.active_player = 1
    assert not game.can_cast(1, b1)


def test_the_prohibition_ends_at_the_turn_boundary(card_db):
    game = _game(card_db)
    bolt = _put(game, card_db, "Lightning Bolt", 1, "hand")
    _resolve_with_oracle(game, card_db, OPPONENTS_NONCREATURE)
    game.players[1].reset_turn_tracking()
    game.active_player = 1
    assert game.can_cast(1, bolt)


def test_an_unkicked_cast_prohibition_does_not_apply_its_kicked_attack_rider(card_db):
    # The kicker card's name-keyed handler is retired: the generic branch
    # resolves the base clause, and the kicked rider stays gated on kick_count.
    from engine.card_effects import EffectTiming
    assert not EFFECT_REGISTRY.has_handler("Orim's Chant", EffectTiming.SPELL_RESOLVE)
    game = _game(card_db)
    card = CardInstance(template=card_db.get_card("Orim's Chant"), owner=0,
                        controller=0, instance_id=game.next_instance_id(),
                        zone="stack")
    card._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=card,
                     controller=0, targets=[])
    item.kick_count = 0
    game.stack.push(item)
    game.resolve_stack()
    assert game.players[1].silenced_this_turn
    assert not any(p.cannot_attack_this_turn for p in game.players)


def test_a_free_cast_is_still_a_cast_under_a_prohibition(card_db):
    # CR 101.2: cascade / "cast without paying" routes are casts too.
    from engine.cast_manager import CastManager
    game = _game(card_db)
    bolt = _put(game, card_db, "Lightning Bolt", 1, "hand")
    _resolve_with_oracle(game, card_db, OPPONENTS_NONCREATURE)
    game.active_player = 1
    assert CastManager.cast_spell(game, 1, bolt, targets=[-1], free_cast=True) is False
    assert bolt in game.players[1].hand
