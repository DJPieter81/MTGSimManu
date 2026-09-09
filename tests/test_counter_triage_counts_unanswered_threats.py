"""Counter triage must account per unanswered threat, not per hand.

`ResponseDecider._has_post_resolution_creature_answer` decided "skip the
counter on this creature spell" whenever ANY flash-speed creature removal
sat in hand — a boolean over the hand.  It never asked whether that removal
was already owed to a creature the opponent had resolved earlier.  A
Control player holding Counterspell + one Solitude therefore let a second,
third and fourth creature resolve through open UU, each time "reserving"
the counter for a Solitude that could only ever answer one of them — and
then discarded the counter to hand size.

Observed 2026-09-08 (Broodscale Bloodchief vs Azorius Control (WST), seed
50001): four creatures resolved through two Counterspells in the opener;
Azorius Control vs Ruby Storm seed 50001: Counterspell discarded to hand
size four times while holding Solitude.

Rule: a counter may be reserved for a creature spell only when, after one
uncommitted flash-speed answer is set against each creature already on the
opponent's battlefield that is worth a removal spell, an answer that can
kill THIS creature is still uncommitted.

Class: every (counterspell x flash creature-removal) pairing — Counterspell,
Force of Negation, Consign to Memory, Spell Pierce, Mana Leak, No More Lies,
Dovin's Veto, Memory Lapse... against Solitude, Subtlety, March, Path, Push,
Bolt, Cut Down, Otawara...  Card names below are fixture carriers for the
shapes (a hard counter; a flash creature-exile; a vanilla body; a 4/4).
"""
from __future__ import annotations

import random

from ai.response import ResponseDecider
from ai.turn_planner import TurnPlanner
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _cast_onto_stack(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=card,
                     controller=controller, targets=[])
    game.stack.push(item)
    return item


def _control_vs_creatures(card_db, *, board):
    """Defender (idx 1) holds Counterspell + Solitude + a white pitch card
    with UU + WW open; the attacker (idx 0) has `board` on the battlefield
    and casts a 4/4."""
    game = GameState(rng=random.Random(0))
    attacker, defender = 0, 1
    game.active_player = attacker
    for _ in range(2):
        _add(game, card_db, "Island", defender, "battlefield")
        _add(game, card_db, "Plains", defender, "battlefield")
    for name in board:
        _add(game, card_db, name, attacker, "battlefield")
    _add(game, card_db, "Counterspell", defender, "hand")
    _add(game, card_db, "Solitude", defender, "hand")
    _add(game, card_db, "Orim's Chant", defender, "hand")
    item = _cast_onto_stack(game, card_db, "Sojourner's Companion", attacker)
    return game, item, ResponseDecider(defender, TurnPlanner(), strategic_logger=None)


def test_counter_fires_when_the_only_flash_answer_is_already_owed_to_a_board_threat(card_db):
    """One Solitude, one 4/4 already resolved, a second 4/4 on the stack:
    the removal is spoken for, so the counter is the only answer to the
    spell on the stack and must fire."""
    game, item, decider = _control_vs_creatures(
        card_db, board=["Sojourner's Companion"])
    result = decider.decide_response(game, item)
    assert result is not None, (
        "the defender passed with Counterspell in hand and its only flash "
        "removal already owed to the 4/4 on the battlefield")
    chosen, _ = result
    assert chosen.name == "Counterspell", (
        f"expected the counter (the removal is committed elsewhere); got {chosen.name}")


def test_counter_is_reserved_when_an_uncommitted_flash_answer_remains(card_db):
    """Guard for the triage rule itself: with nothing on the opponent's
    board worth a removal spell (a 1/1 vanilla), the flash exile is free to
    answer the incoming creature, so the counter is reserved."""
    game, item, decider = _control_vs_creatures(card_db, board=["Memnite"])
    result = decider.decide_response(game, item)
    assert result is None or result[0].name != "Counterspell", (
        "one uncommitted flash answer covers the spell on the stack; the "
        "counter must stay reserved for non-creature threats")


def test_two_flash_answers_cover_one_board_threat_and_the_stack_creature(card_db):
    """Per-threat accounting is a count, not a flag: two Solitudes against
    one board threat leave one uncommitted, so the counter is reserved."""
    game, item, decider = _control_vs_creatures(
        card_db, board=["Sojourner's Companion"])
    _add(game, card_db, "Solitude", 1, "hand")
    result = decider.decide_response(game, item)
    assert result is None or result[0].name != "Counterspell"


def test_burn_that_cannot_reach_a_board_threat_is_not_committed_to_it(card_db):
    """Reach is part of the accounting: a 3-damage burn cannot answer a 4/4
    on the battlefield, so it is not committed there — it stays free for a
    smaller creature on the stack, and the counter is reserved."""
    game = GameState(rng=random.Random(0))
    attacker, defender = 0, 1
    game.active_player = attacker
    for _ in range(3):
        _add(game, card_db, "Island", defender, "battlefield")
    _add(game, card_db, "Mountain", defender, "battlefield")
    _add(game, card_db, "Sojourner's Companion", attacker, "battlefield")  # 4/4
    _add(game, card_db, "Counterspell", defender, "hand")
    _add(game, card_db, "Lightning Bolt", defender, "hand")
    item = _cast_onto_stack(game, card_db, "Grizzly Bears", attacker)  # 2/2 on the stack
    decider = ResponseDecider(defender, TurnPlanner(), strategic_logger=None)
    result = decider.decide_response(game, item)
    assert result is None or result[0].name != "Counterspell", (
        "the Bolt cannot be owed to the 4/4 it cannot kill; it answers the "
        "2/2 on the stack, so the counter stays reserved")
