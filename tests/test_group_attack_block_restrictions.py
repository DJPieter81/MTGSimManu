"""A resolved "creatures [your opponents control | target player controls]
[without flying] can't block / attack this turn" restricts a CLASS of
creatures, not a set fixed at resolution: it is a rule-modifying effect,
so CR 611.2c's locked-in set does not apply — a creature that enters or
loses flying later in the turn is covered too. Modelled as a PROHIBIT
effect with a FILTER selector evaluated at each query.

Rules pinned:
* the group shape is typed once (`CardTemplate.group_restriction`);
  unqualified "creatures can't attack" stays with the combat-prevention
  class (a player-scoped attack lock) and is not re-typed here;
* the filter follows controller (relative to the effect's controller) and
  the "without flying" qualifier, re-evaluated each query;
* the effect ends with its duration.
Card names are fixture carriers only.
"""
from __future__ import annotations

import copy
import random

from engine.cards import CardInstance, Keyword
from engine.clause_resolver import resolve_clause
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_group_restriction


def test_group_shapes_are_typed():
    assert parse_group_restriction("Creatures without flying can't block this turn.") == {
        'actions': ('block',), 'controller': 'any', 'without_keyword': 'flying',
        'duration': 'this_turn'}
    assert parse_group_restriction(
        "Creatures your opponents control can't block this turn.")['controller'] == 'opponents'
    assert parse_group_restriction(
        "Creatures target player controls can't block this turn.")['controller'] == 'opponents'
    assert parse_group_restriction("Creatures can't block this turn.")['controller'] == 'any'


def test_other_shapes_are_left_to_their_owners():
    assert parse_group_restriction("Creatures can't attack this turn.") is None   # combat prevention
    assert parse_group_restriction("Target creature can't block this turn.") is None
    assert parse_group_restriction("Creatures without flying can't block.") is None


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


def _resolve(game, card_db, text, controller=0):
    tpl = copy.copy(card_db.get_card("Opt"))
    tpl.oracle_text = text
    tpl.has_scry = False
    tpl.group_restriction = parse_group_restriction(text)
    card = CardInstance(template=tpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone="stack")
    card._game_state = game
    return resolve_clause(game, card, controller, [])


def test_without_flying_covers_ground_creatures_on_both_sides(card_db):
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", 0)
    theirs = _put(game, card_db, "Grizzly Bears", 1)
    flier = _put(game, card_db, "Birds of Paradise", 1)
    assert _resolve(game, card_db, "Creatures without flying can't block this turn.")
    assert not mine.can_block and not theirs.can_block
    assert flier.can_block


def test_an_opponents_scope_covers_only_their_creatures_including_later_ones(card_db):
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", 0)
    _resolve(game, card_db, "Creatures your opponents control can't block this turn.")
    later = _put(game, card_db, "Grizzly Bears", 1)
    assert mine.can_block
    assert not later.can_block


def test_the_filter_is_re_evaluated_when_a_creature_gains_flying(card_db):
    game = _game()
    bear = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Creatures without flying can't block this turn.")
    assert not bear.can_block
    bear.temp_keywords.add(Keyword.FLYING)
    assert bear.can_block


def test_the_group_restriction_ends_with_the_turn(card_db):
    game = _game()
    bear = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Creatures your opponents control can't block this turn.")
    game.cleanup_step(); game.active_player = 1; game.untap_step(1)
    assert bear.can_block


def test_block_legality_audit_restates_a_group_restriction(card_db, monkeypatch):
    from engine import rules_audit, rules_query
    from engine.combat_manager import CombatManager
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    game = _game()
    attacker = _put(game, card_db, "Grizzly Bears", 0)
    blocker = _put(game, card_db, "Grizzly Bears", 1)
    _resolve(game, card_db, "Creatures your opponents control can't block this turn.")
    monkeypatch.setattr(rules_query, "object_prohibited", lambda g, c, a: False)
    cm = CombatManager()
    cm.declare_attackers(game, [attacker], active_player=0)
    cm.declare_blockers(game, {attacker.instance_id: [blocker.instance_id]})
    rules = {f["rule"] for f in rules_audit.drain() if f["kind"] == "violation"}
    rules_audit.reset()
    assert "509.1a/blocker_legal" in rules
