"""Cast prohibitions and permissions are continuous effects with a duration
(CR 101.2 / 611.2 / 702.8d) — `Effect(selector, modification, duration,
origin)` in the one registry — not player fields with a per-player reset.

Rules pinned:
* a "this turn" prohibition ends as the game turn ends, whoever's turn it
  was: one resolved during its target's own turn (a Scepter copy in their
  upkeep) no longer blocks that player's instant-speed casts on the next
  player's turn (the old per-player reset let it live a full round);
* one resolved on the caster's turn covers the opponent for the rest of that
  turn;
* an "until your next turn" flash permission survives the opponent's turn and
  ends as yours begins;
* the sorcery-speed lockout of a static ability is a static effect: it covers
  the source's opponents while the source is on the battlefield, and
  disappears with it;
* the legacy attribute setter registers a this-turn effect (fixtures).
Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine import rules_query
from engine.cards import CardInstance
from engine.effect_model import (THIS_TURN, permit_cast_as_flash,
                                 prohibit_cast, until_your_next_turn)
from engine.game_state import GameState, Phase


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
    game.current_phase = Phase.MAIN1
    for idx in (0, 1):
        for _ in range(3):
            _put(game, card_db, "Mountain", idx, "battlefield")
    return game


def _end_turn_and_begin(game, nxt):
    game.cleanup_step()
    game.active_player = nxt
    game.untap_step(nxt)


def test_a_prohibition_resolved_on_its_targets_own_turn_ends_with_that_turn(card_db):
    game = _game(card_db)
    bolt = card_db.get_card("Lightning Bolt")
    game.active_player = 1                       # P2's turn; P1's copy silences P2
    game.continuous_effects.register_effect(prohibit_cast(1, "all", THIS_TURN, controller=0))
    assert rules_query.cast_prohibited(game, 1, bolt)
    _end_turn_and_begin(game, 0)                 # P1's turn: P2 may cast instants again
    assert not rules_query.cast_prohibited(game, 1, bolt)


def test_a_prohibition_resolved_on_the_casters_turn_covers_the_rest_of_it(card_db):
    game = _game(card_db)
    bolt = card_db.get_card("Lightning Bolt")
    game.active_player = 0
    game.continuous_effects.register_effect(prohibit_cast(1, "all", THIS_TURN, controller=0))
    assert rules_query.cast_prohibited(game, 1, bolt)
    assert not rules_query.cast_prohibited(game, 0, bolt)


def test_a_flash_permission_lasts_until_your_next_turn(card_db):
    game = _game(card_db)
    coil = card_db.get_card("Lava Coil")
    game.active_player = 0
    game.continuous_effects.register_effect(
        permit_cast_as_flash(0, ("sorcery",), until_your_next_turn(0)))
    _end_turn_and_begin(game, 1)
    assert rules_query.cast_as_though_flash(game, 0, coil)
    _end_turn_and_begin(game, 0)
    assert not rules_query.cast_as_though_flash(game, 0, coil)


def test_a_sorcery_speed_lockout_is_a_static_effect_of_its_source(card_db):
    game = _game(card_db)
    teferi = _put(game, card_db, "Teferi, Time Raveler", 0, "battlefield")
    assert rules_query.sorcery_speed_only(game, 1)
    assert not rules_query.sorcery_speed_only(game, 0)
    game.players[0].battlefield.remove(teferi)
    teferi.zone = "graveyard"
    assert not rules_query.sorcery_speed_only(game, 1)


def test_the_legacy_setter_registers_a_this_turn_effect(card_db):
    game = _game(card_db)
    game.players[1].silenced_this_turn = True
    assert any(e.duration == THIS_TURN for e in game.continuous_effects.rule_effects(game))
    assert game.players[1].silenced_this_turn
