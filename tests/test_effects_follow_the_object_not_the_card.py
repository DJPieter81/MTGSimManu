"""A continuous effect from a resolved spell or ability that affects a
chosen object affects only that object (CR 611.2c). A permanent that leaves
the battlefield and returns is a new object with no memory of its previous
existence (CR 400.7), so the effect no longer applies to it.

Rules pinned:
* an "until your next turn" P/T modification on a creature ends for that
  creature when it is blinked (the returned object has its printed P/T);
* an end-of-turn P/T modification likewise;
* the effect still applies to the object while it stays on the battlefield.
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.cards import CardInstance
from engine.clause_resolver import resolve_clause
from engine.continuous_effects import create_pump_spell_effect
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_until_next_turn


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


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _resolve(game, card_db, text, targets):
    tpl = copy.copy(card_db.get_card("Opt"))
    tpl.oracle_text = text
    tpl.has_scry = False
    tpl.next_turn_effect = parse_until_next_turn(text)
    card = CardInstance(template=tpl, owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    return resolve_clause(game, card, 0, targets)


def test_an_until_next_turn_modifier_does_not_follow_a_blinked_creature(card_db):
    game = _game()
    bear = _put(game, card_db, "Grizzly Bears", 1, "battlefield")
    assert _resolve(game, card_db, "Until your next turn, up to one target creature gets -2/-0.",
                    targets=[bear.instance_id])
    game.continuous_effects.recalculate(game)
    assert bear.power == 0
    game._blink_permanent(bear, 1)
    game.continuous_effects.recalculate(game)
    assert bear.power == 2


def test_an_end_of_turn_modifier_does_not_follow_a_blinked_creature(card_db):
    game = _game()
    bear = _put(game, card_db, "Grizzly Bears", 0, "battlefield")
    for ce in create_pump_spell_effect(0, "Pump", bear.instance_id, 3, 3,
                                       target_seq=bear.battlefield_entry_seq):
        game.continuous_effects.register(ce)
    game.continuous_effects.recalculate(game)
    assert (bear.power, bear.toughness) == (5, 5)
    game._blink_permanent(bear, 0)
    game.continuous_effects.recalculate(game)
    assert (bear.power, bear.toughness) == (2, 2)


def test_the_modifier_still_applies_while_the_object_stays(card_db):
    game = _game()
    bear = _put(game, card_db, "Grizzly Bears", 1, "battlefield")
    _resolve(game, card_db, "Until your next turn, up to one target creature gets -2/-0.",
             targets=[bear.instance_id])
    for _ in range(3):
        game.continuous_effects.recalculate(game)
    assert bear.power == 0
