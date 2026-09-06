"""A permanent (non-Aura) spell resolves onto the battlefield even when
a target stashed on its stack item is now illegal.

A "When you cast this spell, exile target permanent that's one or more
colors" trigger (Devourer of Destiny / Ugin) is a SEPARATE object on
the stack (CR 603.3); its target is not the creature spell's target.
But the chosen permanent was recorded in the creature spell's
``item.targets``, and the trigger exiled it — so the CR 608.2b
resolution re-check saw "all targets illegal" and fizzled the whole
creature spell, and the body never entered (audit: Boros Energy vs
Eldrazi Tron, s58001; Broodscale vs Eldrazi Ramp).

Rule: CR 608.2b fizzling applies to a spell's OWN targets. A permanent
spell (creature/artifact/enchantment/planeswalker) that is not an Aura
enters the battlefield on resolution regardless of any targets carried
for a cast trigger; only instants, sorceries, and Auras fizzle when
their targets are all illegal. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _mk(game, card_db, name, controller, zone):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    getattr(game.players[controller], zone).append(c)
    return c


def test_creature_spell_resolves_when_its_cast_trigger_target_is_gone(card_db):
    game = GameState(rng=random.Random(0))
    # The colored permanent the cast trigger exiled — now in exile, but
    # still referenced by the creature spell's stashed targets + zone
    # snapshot (battlefield at cast time).
    victim = _mk(game, card_db, "Griselbrand", 1, "exile")

    spell = CardInstance(template=card_db.get_card("Devourer of Destiny"),
                         owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="stack")
    spell._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=spell,
                     controller=0, targets=[victim.instance_id])
    item.target_zones = {victim.instance_id: "battlefield"}
    game.stack.push(item)

    game.resolve_stack()

    bf_names = [c.name for c in game.players[0].battlefield]
    assert "Devourer of Destiny" in bf_names, (
        f"the creature spell fizzled instead of entering — a cast-trigger "
        f"target that left its zone must not fizzle a permanent spell. "
        f"Battlefield: {bf_names}"
    )


def test_instant_still_fizzles_when_all_targets_illegal(card_db):
    """Regression: an instant/sorcery whose only target is now illegal
    still fizzles (the guard is scoped to permanent non-Aura spells)."""
    game = GameState(rng=random.Random(0))
    victim = _mk(game, card_db, "Griselbrand", 1, "graveyard")  # left battlefield

    bolt = CardInstance(template=card_db.get_card("Lightning Bolt"),
                        owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone="stack")
    bolt._game_state = game
    item = StackItem(item_type=StackItemType.SPELL, source=bolt,
                     controller=0, targets=[victim.instance_id])
    item.target_zones = {victim.instance_id: "battlefield"}
    game.stack.push(item)

    life_before = game.players[1].life
    game.resolve_stack()

    assert game.players[1].life == life_before, (
        "Lightning Bolt should fizzle (its target left the battlefield) "
        "and deal no damage"
    )
