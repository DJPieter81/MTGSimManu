"""A permanent that adds mana on future turns is worth that mana.

`position_value` already prices mana available now (`mana_clock_impact`
per mana). A mana engine adds mana next turn instead; it is priced at the
same rate, discounted by `urgency_factor` (the share of future turns we
get). The expected mana is read from public information only — hand
size and library composition, never the hidden hand:

* an extra land drop adds one mana per extra drop when a land beyond the
  normal drop (CR 305.2) is expected in hand;
* a cost reducer saves its amount (capped at each spell's generic cost)
  on every expected matching spell in hand (the single matcher,
  `engine.oracle_resolver._cost_rule_applies`).

Rules pinned below; card types are synthetic fixtures.
"""
from __future__ import annotations

import random

import pytest

from ai.mana_engine import engine_mana_next_turn
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState


def _tmpl(name, types, generic=0, **kw):
    return CardTemplate(
        name=name, card_types=types, mana_cost=ManaCost(generic=generic),
        supertypes=[], subtypes=[], power=kw.pop("power", None),
        toughness=kw.pop("toughness", None), loyalty=None, keywords=set(),
        abilities=[], color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(), **kw)


def _card(game, tmpl, owner, zone):
    c = CardInstance(template=tmpl, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    getattr(game.players[owner], zone).append(c)
    return c


def _player(game, *, hand, lands, spells, spell_generic=2):
    land = _tmpl("Land", [CardType.LAND])
    spell = _tmpl("Spell", [CardType.INSTANT], generic=spell_generic)
    p = game.players[0]
    p.library.clear(); p.hand.clear()
    for _ in range(lands):
        _card(game, land, 0, "library")
    for _ in range(spells):
        _card(game, spell, 0, "library")
    for _ in range(hand):
        _card(game, spell, 0, "hand")
    return p


def test_a_player_without_engines_adds_no_future_mana():
    game = GameState(rng=random.Random(0))
    _player(game, hand=5, lands=10, spells=10)
    assert engine_mana_next_turn(game, 0) == 0


def test_an_extra_land_drop_adds_mana_when_a_land_beyond_the_normal_drop_is_expected():
    game = GameState(rng=random.Random(0))
    _player(game, hand=6, lands=10, spells=10)          # 3 lands expected in hand
    _card(game, _tmpl("Ramp", [CardType.ENCHANTMENT], extra_land_drops=1), 0, "battlefield")
    assert engine_mana_next_turn(game, 0) == pytest.approx(1)


def test_an_extra_land_drop_adds_only_the_expected_lands_beyond_the_normal_drop():
    game = GameState(rng=random.Random(0))
    _player(game, hand=3, lands=10, spells=10)          # 1.5 lands expected
    _card(game, _tmpl("Ramp", [CardType.ENCHANTMENT], extra_land_drops=1), 0, "battlefield")
    assert engine_mana_next_turn(game, 0) == pytest.approx(0.5)


def test_a_cost_reducer_saves_its_amount_on_each_expected_matching_spell():
    game = GameState(rng=random.Random(0))
    _player(game, hand=4, lands=10, spells=10)          # 2 matching spells expected
    rule = {"target": "instant_sorcery", "amount": 1, "color": None}
    _card(game, _tmpl("Reducer", [CardType.ARTIFACT], cost_reduction_rule=rule), 0, "battlefield")
    assert engine_mana_next_turn(game, 0) == pytest.approx(2)


def test_a_reduction_never_exceeds_a_spells_generic_cost():
    game = GameState(rng=random.Random(0))
    _player(game, hand=4, lands=10, spells=10, spell_generic=0)
    rule = {"target": "instant_sorcery", "amount": 1, "color": None}
    _card(game, _tmpl("Reducer", [CardType.ARTIFACT], cost_reduction_rule=rule), 0, "battlefield")
    assert engine_mana_next_turn(game, 0) == 0


def test_the_snapshot_and_position_value_price_engine_mana_like_mana_now():
    from ai.clock import mana_clock_impact, position_value
    from ai.ev_evaluator import snapshot_from_game
    game = GameState(rng=random.Random(0))
    _player(game, hand=4, lands=10, spells=10)
    base = snapshot_from_game(game, 0)
    rule = {"target": "instant_sorcery", "amount": 1, "color": None}
    _card(game, _tmpl("Reducer", [CardType.ARTIFACT], cost_reduction_rule=rule), 0, "battlefield")
    snap = snapshot_from_game(game, 0)
    assert snap.my_engine_mana == pytest.approx(2)
    gained = position_value(snap) - position_value(base)
    assert gained == pytest.approx(2 * mana_clock_impact(snap) * snap.urgency_factor)


def test_a_mana_engine_has_threat_and_a_vanilla_enchantment_has_none():
    from engine.card_effects import _threat_score
    game = GameState(rng=random.Random(0))
    _player(game, hand=4, lands=10, spells=10)
    rule = {"target": "instant_sorcery", "amount": 1, "color": None}
    engine = _card(game, _tmpl("Reducer", [CardType.ARTIFACT], cost_reduction_rule=rule),
                   0, "battlefield")
    vanilla = _card(game, _tmpl("Vanilla", [CardType.ENCHANTMENT]), 0, "battlefield")
    p = game.players[0]
    assert _threat_score(engine, game, p) > 0
    assert _threat_score(vanilla, game, p) == 0
