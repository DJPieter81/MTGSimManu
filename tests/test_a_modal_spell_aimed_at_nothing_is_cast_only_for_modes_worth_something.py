"""A modal spell cast with no target performs only its modes that need none
(CR 700.2a): every cast path casts it then only when those modes are worth
something to its caster.

"Choose one -- ~ deals damage equal to twice the number of creatures you
control to target creature; destroy target enchantment; exile any number
of target players' graveyards": against an opponent whose only creatures
have hexproof, aimed at nothing, the charm can only exile graveyards --
and with nothing in them that is worth nothing.

The defect: the main-phase decision held such a cast (unit MS), but the
response decision did not. Its planner picked the charm as removal, found
no legal creature, and cast it with no target. The engine then chose the
only legal mode, which did nothing: Thraben Charm's `unhandled/mode`
findings in 16 rows of the mr-post arm.

Card names are fixture carriers: 111 pool modal spells mix targeted and
untargeted modes.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    if zone != "stack":
        getattr(game.players[controller], zone).append(card)
    return card


def _shrouded_board(card_db):
    """Player 1 holds the charm with mana open; player 0's only creatures
    have hexproof, and both graveyards are empty."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    for _ in range(3):
        _add(game, card_db, "Plains", 1, "battlefield")
    for _ in range(2):
        _add(game, card_db, "Ocelot Pride", 1, "battlefield")
    for _ in range(2):
        _add(game, card_db, "Slippery Bogle", 0, "battlefield")
    charm = _add(game, card_db, "Thraben Charm", 1, "hand")
    return game, charm


def test_aimed_at_nothing_a_modal_spell_is_worth_only_its_untargeted_modes(card_db):
    from ai.modal import worth_casting_without_targets
    game, charm = _shrouded_board(card_db)
    assert not worth_casting_without_targets(game, charm, 1)
    sweep = _add(game, card_db, "Avengers Disassembled", 1, "hand")
    for _ in range(2):
        _add(game, card_db, "Grizzly Bears", 0, "battlefield")
    assert worth_casting_without_targets(game, sweep, 1), \
        "a sweep needs no target: it kills four of theirs for two of ours"


def test_a_spell_with_no_targeted_mode_is_not_held_by_this_rule(card_db):
    from ai.modal import worth_casting_without_targets
    game, _ = _shrouded_board(card_db)
    edict = _add(game, card_db, "Sheoldred's Edict", 1, "hand")
    plain = _add(game, card_db, "Grizzly Bears", 1, "hand")
    assert worth_casting_without_targets(game, edict, 1)
    assert worth_casting_without_targets(game, plain, 1)


def test_a_response_does_not_cast_a_modal_spell_aimed_at_nothing(card_db):
    from ai.response import ResponseDecider
    from ai.turn_planner import TurnPlanner
    game, charm = _shrouded_board(card_db)
    bears = _add(game, card_db, "Grizzly Bears", 0, "stack")
    item = StackItem(item_type=StackItemType.SPELL, source=bears, controller=0)
    game.stack.push(item)
    decider = ResponseDecider(1, TurnPlanner(), strategic_logger=None)
    result = decider.decide_response(game, item)
    assert result is None or result[0] is not charm or result[1], result
