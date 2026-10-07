"""Ward counters an ABILITY that targets the permanent, not only a spell
(CR 702.21a: "a spell or ability an opponent controls").

A permanent's targeted ETB trigger (exile / destroy / bounce / ping on
entering) takes its target as the trigger goes on the stack; ward on that
target then counters the trigger unless its controller pays. The engine
carries an ETB trigger's target on the permanent spell's stack item, and
the ward check skipped permanent spells (which do not target themselves),
so an ETB trigger got through ward for free: a 0-mana Leyline Binding
exiled a Ward {4} permanent in an anchor game.

Rules pinned: an unpaid ward counters the targeted ETB ability (the
permanent itself stays); a paid ward lets it resolve and the cost is paid;
a player's own warded permanent never triggers ward. Card names are
fixture carriers (one real member of each side of the class).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType
from tests.test_ward_framework import (
    _AlwaysPayCallbacks, _AssertNotOfferedCallbacks, _land,
)


def _setup(card_db, callbacks, *, caster_lands=0, warded_controller=1):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    warded_t = card_db.get_card("Kappa Cannoneer")            # Ward {4}
    warded = CardInstance(template=warded_t, owner=warded_controller,
                          controller=warded_controller,
                          instance_id=game.next_instance_id(), zone="battlefield")
    warded._game_state = game
    warded.enter_battlefield()
    game.players[warded_controller].battlefield.append(warded)
    if caster_lands:
        _land(game, controller=0, n=caster_lands)
    binding_t = card_db.get_card("Leyline Binding")           # ETB: exile target
    binding = CardInstance(template=binding_t, owner=0, controller=0,
                           instance_id=game.next_instance_id(), zone="stack")
    binding._game_state = game
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=binding,
                              controller=0, targets=[warded.instance_id],
                              effect=None, description="Leyline Binding"))
    return game, binding, warded


def test_an_unpaid_ward_counters_the_targeted_etb_trigger(card_db):
    game, binding, warded = _setup(card_db, _AssertNotOfferedCallbacks(),
                                   caster_lands=0)       # cannot pay {4}
    game.resolve_stack()
    assert binding.zone == "battlefield", "the permanent itself still enters"
    assert warded.zone == "battlefield", "its ETB trigger was countered by ward"
    assert any("countered" in line and "ward" in line for line in game.log)


def test_a_paid_ward_lets_the_targeted_etb_trigger_resolve(card_db):
    game, binding, warded = _setup(card_db, _AlwaysPayCallbacks(), caster_lands=4)
    game.resolve_stack()
    assert warded.zone == "exile"
    assert all(c.tapped for c in game.players[0].battlefield if c.template.is_land)


def test_a_players_own_warded_permanent_never_triggers_ward(card_db):
    game, binding, warded = _setup(card_db, _AssertNotOfferedCallbacks(),
                                   warded_controller=0)
    game.resolve_stack()
    assert not any("'s ward" in line for line in game.log)
