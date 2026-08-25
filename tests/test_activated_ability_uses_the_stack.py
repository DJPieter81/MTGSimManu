"""An activated ability goes on the stack and resolves before the next play.

CR 602.2a: activating an ability puts it on the stack; the effect happens on
RESOLUTION, not at activation time.

The second test here is the important one. The runner's action loop makes each
decision against the current board, so an arm that pushes a stack item without
draining it leaves the ability unresolved — every subsequent decision that turn
is then made against a board that has not yet changed, and the effect may never
apply at all. The cast arm drains the stack; the activate arm must too. This is
the failure the design review flagged as the largest hole in the original
design, so it is pinned directly rather than inferred from a win-rate number.

Rules-phrased; card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.activation import ActivationManager
from engine.cards import (ActivatedAbility, ActivationCost,
                          ActivationEffectKind, CardInstance)
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from engine.mana import ManaCost
from engine.stack import StackItemType

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    game.players[0].deck_name = "Eldrazi Tron"
    game.players[1].deck_name = "Dimir Midrange"
    for _ in range(4):
        _add(game, "Island")
    for _ in range(6):
        t = _DB.get_card("Island")
        c = CardInstance(template=t, owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="library")
        c._game_state = game
        game.players[0].library.append(c)
    return game


def _draw_ability():
    return ActivatedAbility(
        index=0,
        cost=ActivationCost(mana=ManaCost(generic=1)),
        effect_text="Draw a card.",
        effect_kind=ActivationEffectKind.DRAW_N,
        amount=1,
    )


def test_activation_pushes_an_activated_ability_item_and_defers_the_effect():
    game = _game()
    ab = _draw_ability()
    perm = _add(game, "Wall of Omens")
    perm.template.activated_abilities = [ab]

    hand_before = len(game.players[0].hand)
    assert ActivationManager.activate(game, 0, perm, ab, [])

    assert not game.stack.is_empty, "the ability must be ON THE STACK"
    assert game.stack.top.item_type is StackItemType.ACTIVATED_ABILITY, (
        "the stack object is an activated ability, not a spell — this is the "
        "first ACTIVATED_ABILITY item this engine constructs")
    assert len(game.players[0].hand) == hand_before, (
        "CR 602.2a — the effect must NOT have applied at activation time")


def test_effect_applies_on_resolution():
    game = _game()
    ab = _draw_ability()
    perm = _add(game, "Wall of Omens")
    perm.template.activated_abilities = [ab]

    hand_before = len(game.players[0].hand)
    ActivationManager.activate(game, 0, perm, ab, [])
    game.resolve_stack()

    assert game.stack.is_empty, "the stack must drain"
    assert len(game.players[0].hand) == hand_before + 1, (
        "the draw applies when the ability RESOLVES")


def test_runner_activate_arm_leaves_no_unresolved_ability():
    """The hole this pins: pushing without draining.

    If the activate arm returns with the item still on the stack, every later
    decision that turn is made against a stale board and the effect may never
    apply.
    """
    from engine.game_runner import GameRunner
    from ai.ev_player import EVPlayer

    game = _game()
    ab = _draw_ability()
    perm = _add(game, "Wall of Omens")
    perm.template.activated_abilities = [ab]

    ai0 = EVPlayer(player_idx=0, deck_name="Eldrazi Tron",
                   rng=random.Random(0))
    ai1 = EVPlayer(player_idx=1, deck_name="Dimir Midrange",
                   rng=random.Random(0))
    runner = GameRunner.__new__(GameRunner)
    runner._execute_main_phase(game, ai0, ai1)

    assert game.stack.is_empty, (
        "after the main phase the stack must be empty — an activate arm that "
        "pushes without draining leaves the ability unresolved and every "
        "later decision is made against a board that never changed")
