"""A spell's own damage counts toward what its copies must deal.

The AI plans where K copies of a spell dealing N damage go
(`ai.damage_targets.plan_copy_damage`, CR 707.10c). The spell itself
deals its N to the target it was cast at, so:
- copies lethal *together with the spell* go to the face when the spell is
  aimed at the face;
- a permanent the spell is aimed at needs only the copies its own damage
  leaves short, and none when the spell alone destroys it.

The defect: the plan counted the copies alone. Ruby Storm vs Boros Energy
s50000, game 2, turn 5: Grapeshot at the face with 8 copies, Boros at 9.
1 + 8 was lethal, but the plan saw 8 < 9, sent 3 copies into a planeswalker
and Boros survived at 3, then won. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase

FACE = PLAYER_TARGET_OPPONENT


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, opposing, opp_life):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    theirs = [_add(game, card_db, n, 1) for n in opposing]
    game.players[1].life = opp_life
    return game, theirs


def _plan(game, card_db, copies, spell_target):
    from ai.damage_targets import plan_copy_damage
    from engine.cast_manager import CastManager
    shot = _add(game, card_db, "Grapeshot", 0, "hand")
    (req,) = CastManager.spell_requirements(shot)
    return plan_copy_damage(game, 0, shot, 1, copies, req,
                            spell_target=spell_target)


def test_copies_lethal_with_the_spell_at_the_face_all_go_to_the_face(
        card_db):
    game, theirs = _game(card_db, ["Teferi, Hero of Dominaria",
                                   "Guide of Souls"], opp_life=9)
    theirs[0].loyalty_counters = 3
    assert _plan(game, card_db, 8, FACE) == [FACE] * 8


def test_a_permanent_the_spell_is_aimed_at_needs_only_what_it_leaves_short(
        card_db):
    """A 1/2 the spell deals 1 to dies to one copy, not two."""
    game, theirs = _game(card_db, ["Guide of Souls"], opp_life=20)
    guide = theirs[0]
    plan = _plan(game, card_db, 3, guide.instance_id)
    assert plan.count(guide.instance_id) in (0, 1)


def test_no_copy_goes_to_a_permanent_the_spell_alone_destroys(card_db):
    game, theirs = _game(card_db, ["Memnite"], opp_life=20)
    memnite = theirs[0]
    assert memnite.instance_id not in _plan(game, card_db, 3,
                                            memnite.instance_id)


def test_through_the_engine_the_replayed_line_is_lethal(card_db):
    from engine.game_runner import AICallbacks
    game, theirs = _game(card_db, ["Teferi, Hero of Dominaria",
                                   "Guide of Souls"], opp_life=9)
    theirs[0].loyalty_counters = 3
    for _ in range(2):
        _add(game, card_db, "Mountain", 0)
    game.players[0].deck_name = "Ruby Storm"
    game.callbacks = AICallbacks()
    shot = _add(game, card_db, "Grapeshot", 0, "hand")
    game._global_storm_count = 8
    assert game.cast_spell(0, shot, [FACE])
    while not game.stack.is_empty and not game.game_over:
        game.resolve_stack()
    assert game.players[1].life <= 0
