"""A Saga's Chapter I is same-turn value (CR 714.3a / 714.2b).

As a Saga enters the battlefield its controller puts a lore counter on it,
which triggers chapter I at once. So casting a Saga this turn delivers
chapter I's effect this turn — it is never "the same as casting it next
turn". The deferral gate in `ai/ev_evaluator.compute_play_ev` found no
same-turn signal for a Saga (an enchantment with no "when … enters"
clause), returned the exposure cost before the projection ran, and the
existing Saga chapter projection was never reached: Jeskai Blink held
Fable of the Mirror-Breaker from the opening hand until turn 12 in the
s50000 replays (scratchpad `jb_vs_tron_50000.txt`) and cast Phelia into
removal twice instead against 4c Omnath.

Class: 183 Sagas in the pool. Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from ai.ev_evaluator import _enumerate_this_turn_signals, compute_play_ev, snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

FABLE = "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"


def _put(game, card_db, name, controller, zone):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
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
    game.turn_number = 3
    game.players[0].deck_name = "Jeskai Blink"
    game.players[1].deck_name = "4c Omnath"
    for n in ("Sacred Foundry", "Hallowed Fountain", "Steam Vents"):
        _put(game, card_db, n, 0, "battlefield")
    for n in ("Forest", "Hallowed Fountain"):
        _put(game, card_db, n, 1, "battlefield")
    for _ in range(8):
        _put(game, card_db, "Plains", 0, "library")
    return game


def test_a_saga_whose_first_chapter_has_a_material_effect_is_not_deferred(card_db):
    game = _game(card_db)
    fable = _put(game, card_db, FABLE, 0, "hand")
    snap = snapshot_from_game(game, 0)
    assert 'saga_chapter_one' in _enumerate_this_turn_signals(fable, snap, game, 0)
    ev, info = compute_play_ev(fable, snap, "midrange", game, 0, detailed=True)
    assert not info['deferral'], info
    assert ev > 0.0, ev


def test_the_chapter_one_field_is_typed_across_the_saga_class(card_db):
    sagas = [t for t in card_db.cards.values() if 'Saga' in (t.subtypes or ())]
    material = [t for t in sagas if t.saga_chapter_one_material]
    assert len(material) >= 100, len(material)
    assert not any(t.saga_chapter_one_material for t in card_db.cards.values()
                   if 'Saga' not in (t.subtypes or ()))


def test_a_saga_whose_first_chapter_is_vanilla_stays_deferred(card_db):
    game = _game(card_db)
    fable = _put(game, card_db, FABLE, 0, "hand")
    fable.template = copy.copy(fable.template)
    fable.template.oracle_text = (
        "(As this Saga enters and after your draw step, add a lore counter.)\n"
        "I — This chapter does nothing.\n"
        "II — You may discard up to two cards. If you do, draw that many "
        "cards.\n"
        "III — Exile this Saga, then return it to the battlefield "
        "transformed under your control.")
    fable.template.tags = set()
    from engine.oracle_parser import parse_saga_chapter_one_material
    fable.template.saga_chapter_one_material = parse_saga_chapter_one_material(
        fable.template.oracle_text, fable.template.subtypes)
    assert fable.template.saga_chapter_one_material is False
    snap = snapshot_from_game(game, 0)
    assert 'saga_chapter_one' not in _enumerate_this_turn_signals(
        fable, snap, game, 0)
