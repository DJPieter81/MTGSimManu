"""A bounce is projected by the AI as what it does (CR 608.2b): the target
leaves the opponent's board and the card returns to its owner's hand. The
projector (`ai.ev_evaluator._project_spell`) credited nothing for a bounce
spell; it now removes the chosen legal creature's contribution (the removal
branch's power accounting) and gives the opponent the card back, so the
position value prices the tempo swing on its own card term.

Rules pinned:
* a creature bounce with a legal target has positive EV; with no legal
  target it is not credited;
* it is worth less than destroying the same creature (the card comes back);
* the projection reads the typed `bounce_target`, not oracle words.
Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import _project_spell, evaluate_board, snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone="battlefield"):
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
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    for _ in range(3):
        _put(game, card_db_global(), "Island", 0)
    return game


_DB = {}


def card_db_global():
    return _DB["db"]


def _delta(game, card):
    snap = snapshot_from_game(game, 0)
    return evaluate_board(_project_spell(card, snap, None, game, 0)) - evaluate_board(snap)


def test_a_creature_bounce_with_a_legal_target_projects_positive(card_db):
    _DB["db"] = card_db
    game = _game()
    _put(game, card_db, "Gurmag Angler", 1)
    unsummon = _put(game, card_db, "Unsummon", 0, zone="hand")
    empty = _game()
    unsummon_empty = _put(empty, card_db, "Unsummon", 0, zone="hand")
    assert _delta(game, unsummon) > _delta(empty, unsummon_empty)


def test_a_bounce_is_worth_less_than_destroying_the_same_creature(card_db):
    _DB["db"] = card_db
    game = _game()
    _put(game, card_db, "Gurmag Angler", 1)
    unsummon = _put(game, card_db, "Unsummon", 0, zone="hand")
    snap = snapshot_from_game(game, 0)
    p = _project_spell(unsummon, snap, None, game, 0)
    assert p.opp_creature_count == snap.opp_creature_count - 1
    assert p.opp_hand_size == snap.opp_hand_size + 1     # the card comes back


def test_a_hexproof_only_board_is_not_credited(card_db):
    import copy
    from engine.cards import Keyword
    _DB["db"] = card_db
    game = _game()
    shy = _put(game, card_db, "Gurmag Angler", 1)
    shy.template = copy.copy(shy.template)
    shy.template.keywords = set(shy.template.keywords) | {Keyword.HEXPROOF}
    unsummon = _put(game, card_db, "Unsummon", 0, zone="hand")
    snap = snapshot_from_game(game, 0)
    p = _project_spell(unsummon, snap, None, game, 0)
    assert p.opp_creature_count == snap.opp_creature_count


def test_casting_a_permanent_whose_ability_bounces_projects_no_bounce(card_db):
    _DB["db"] = card_db
    game = _game()
    _put(game, card_db, "Gurmag Angler", 1)
    walker = _put(game, card_db, "Teferi, Time Raveler", 0, zone="hand")
    assert walker.template.bounce_target is not None    # its -3 line
    snap = snapshot_from_game(game, 0)
    p = _project_spell(walker, snap, None, game, 0)
    assert p.opp_creature_count == snap.opp_creature_count
