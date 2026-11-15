"""A resolved "target creature can't attack / block [this turn | until
your next turn]" is a continuous effect on the chosen object (CR 508.1c /
509.1b / 611.2c): a PROHIBIT effect whose selector is that object and whose
duration is the printed one.

Rules pinned:
* the clause is typed once (`CardTemplate.object_restriction`): actions,
  target count, duration; untyped shapes are refused;
* the chosen creature can't attack (or block) while the effect lasts;
  other creatures are unaffected;
* "this turn" ends as the game turn ends; "until your next turn" survives
  the opponent's turn and ends as the controller's turn begins;
* the effect is on the object: a blinked creature is free (CR 400.7);
* with no chosen target, the resolver picks legal opposing creatures, up to
  the printed count.
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine import rules_audit
from engine.cards import CardInstance
from engine.clause_resolver import resolve_clause
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_object_restriction


# ── the typed shape ──────────────────────────────────────────────────

def test_the_restriction_shapes_are_typed():
    assert parse_object_restriction("Target creature can't block this turn.") == {
        'actions': ('block',), 'count': 1, 'duration': 'this_turn'}
    assert parse_object_restriction(
        "Up to two target creatures can't block this turn.")['count'] == 2
    assert parse_object_restriction(
        "One or two target creatures can't block this turn.")['count'] == 2
    assert parse_object_restriction(
        "Target creature can't attack or block until your next turn.") == {
        'actions': ('attack', 'block'), 'count': 1, 'duration': 'until_next_turn'}
    assert parse_object_restriction(
        "Target creature an opponent controls can't block this turn.")['actions'] == ('block',)
    assert parse_object_restriction(
        "Draw a card.\nUp to one target creature can't attack or block until your next turn."
    ) == {'actions': ('attack', 'block'), 'count': 1, 'duration': 'until_next_turn'}


def test_group_and_static_shapes_are_refused():
    assert parse_object_restriction("Creatures without flying can't block this turn.") is None
    assert parse_object_restriction("Enchanted creature can't block.") is None
    assert parse_object_restriction("Target creature can't block.") is None


# ── fixtures ─────────────────────────────────────────────────────────

def _put(game, card_db, name, controller):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _resolve(game, card_db, text, targets=None, controller=0):
    tpl = copy.copy(card_db.get_card("Opt"))
    tpl.oracle_text = text
    tpl.has_scry = False
    tpl.object_restriction = parse_object_restriction(text)
    card = CardInstance(template=tpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    return resolve_clause(game, card, controller, targets or [])


def _next_turn(game, idx):
    game.cleanup_step()
    game.active_player = idx
    game.untap_step(idx)


# ── the effect ───────────────────────────────────────────────────────

def test_the_chosen_creature_cant_block_this_turn_and_others_can(card_db):
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", 1)
    b = _put(game, card_db, "Grizzly Bears", 1)
    assert _resolve(game, card_db, "Target creature can't block this turn.",
                    targets=[a.instance_id])
    assert not a.can_block and b.can_block
    assert a.can_attack


def test_this_turn_ends_with_the_game_turn(card_db):
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Target creature can't attack or block this turn.",
             targets=[a.instance_id])
    assert not a.can_attack and not a.can_block
    _next_turn(game, 1)
    assert a.can_attack and a.can_block


def test_until_your_next_turn_survives_the_opponents_turn(card_db):
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Target creature can't attack or block until your next turn.",
             targets=[a.instance_id])
    _next_turn(game, 1)
    assert not a.can_attack
    _next_turn(game, 0)
    assert a.can_attack and a.can_block


def test_the_restriction_does_not_follow_a_blinked_creature(card_db):
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Target creature can't block this turn.", targets=[a.instance_id])
    game._blink_permanent(a, 1)
    a.summoning_sick = False
    assert a.can_block


def test_with_no_chosen_target_up_to_the_count_of_opposing_creatures(card_db):
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", 0)
    theirs = [_put(game, card_db, "Grizzly Bears", 1) for _ in range(3)]
    _resolve(game, card_db, "Up to two target creatures can't block this turn.")
    assert mine.can_block
    assert sum(not c.can_block for c in theirs) == 2


def _block(game, attacker, blocker):
    from engine.combat_manager import CombatManager
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=0)
    cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
    return cm._assignments[0].blocker_ids


def test_a_restricted_creatures_block_is_dropped(card_db):
    game = _game()
    attacker = _put(game, card_db, "Grizzly Bears", 0)
    blocker = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Target creature can't block this turn.",
             targets=[blocker.instance_id])
    assert _block(game, attacker, blocker) == []


def _audited_block(game, attacker, blocker):
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    _block(game, attacker, blocker)
    rules = {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    rules_audit.reset()
    return rules


def test_block_legality_audit_restates_the_restriction(card_db, monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    game = _game()
    attacker = _put(game, card_db, "Grizzly Bears", 0)
    blocker = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Target creature can't block this turn.",
             targets=[blocker.instance_id])
    # Break the enforcement: the audit must still see the illegal block.
    from engine import rules_query
    monkeypatch.setattr(rules_query, "object_prohibited", lambda g, c, a: False)
    assert "509.1a/blocker_legal" in _audited_block(game, attacker, blocker)


def test_block_legality_audit_is_silent_without_a_restriction(card_db, monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    game = _game()
    attacker = _put(game, card_db, "Grizzly Bears", 0)
    blocker = _put(game, card_db, "Grizzly Bears", 1)
    assert "509.1a/blocker_legal" not in _audited_block(game, attacker, blocker)
