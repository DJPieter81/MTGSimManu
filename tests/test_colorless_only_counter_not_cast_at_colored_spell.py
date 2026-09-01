"""A "counter target colorless spell" counterspell is not offered
against a colored spell.

Consign to Memory ("Counter target triggered ability or colorless
spell") carries the counterspell tag but has no parsed
counter_target_kind, so the AI's counter-candidate filter admitted it
against ANY spell. It was cast at a black Orcish Bowmasters, where it
does nothing, and fizzled — a wasted card (audit: Azorius Blink vs
Dimir Midrange, s59004, G1T2).

Rule: a colorless-only counter cannot target a colored spell. Class:
any "counter target colorless spell" effect. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

import pytest

from ai.response import ResponseDecider
from ai.turn_planner import TurnPlanner
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _add(game, card_db, name, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[controller], zone).append(c)
    return c


def test_colorless_only_counter_flag_parsed():
    from engine.card_database import CardDatabase
    t = CardDatabase().get_card("Consign to Memory")
    assert getattr(t, "counters_colorless_only", False) is True


def test_consign_not_cast_at_a_colored_spell(card_db):
    game = GameState(rng=random.Random(0))
    attacker_idx, defender_idx = 0, 1
    game.active_player = attacker_idx
    # Defender mana for Consign ({1}{U}).
    for _ in range(3):
        _add(game, card_db, "Island", defender_idx, "battlefield")
    _add(game, card_db, "Consign to Memory", defender_idx, "hand")

    # Opp casts a COLORED spell — Consign can never counter it.
    spell = CardInstance(template=card_db.get_card("Orcish Bowmasters"),
                         owner=attacker_idx, controller=attacker_idx,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=attacker_idx, targets=[])
    game.stack.push(item)

    decider = ResponseDecider(defender_idx, TurnPlanner(),
                              strategic_logger=None)
    result = decider.decide_response(game, item)
    # decide_response returns None (pass) or (response_card, targets);
    # it must not be Consign countering this colored spell.
    if result is not None:
        card, _targets = result
        assert card is None or card.name != "Consign to Memory", (
            "Consign to Memory must not be cast at a colored spell it "
            "cannot counter"
        )
