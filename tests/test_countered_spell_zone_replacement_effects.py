"""A countered spell obeys the same alternate-cast zone-replacement
effects as a spell that resolves normally.

Rule under test
----------------
CR 702.33a (flashback): a card cast for its flashback cost that would
be put into a graveyard from anywhere is exiled instead — this is a
zone-replacement tied to HOW it was cast, not to how it left the
stack. `ResolutionManager.resolve_stack` (engine/spell_resolution.py)
already implements this correctly for the "spell resolves normally"
path (checks `card._cast_with_flashback` before choosing graveyard vs
exile). The generic counterspell branch inside `_execute_spell_effects`
(`elif "counter" in desc:`) does not: both its "counter the named
target" and "counter the top of stack" arms do a raw
``countered_card.zone = "graveyard"; graveyard.append(...)`` with no
flashback/rebound/copy check — so a flashbacked spell that gets
countered incorrectly returns to the graveyard where its owner could
mill/reanimate/flashback it again, instead of being exiled.

Same rule, same asymmetry class as the rebound (CR 702.86) and
spell-copy (CR 707.10a) zone-fates already handled on the normal-
resolution path only.

Class size: every counterspell in the pool interacting with every
flashback/rebound spell in the pool.
"""
from __future__ import annotations

import random

from engine.cards import (
    Ability, AbilityType, CardInstance, CardTemplate, CardType, ManaCost,
)
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


def _counterspell_template():
    return CardTemplate(
        name="Test Fixture: Generic Counterspell",
        card_types=[CardType.INSTANT],
        mana_cost=ManaCost(generic=1, blue=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(),
        abilities=[Ability(ability_type=AbilityType.TRIGGERED,
                           description="Counter target spell.")],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="Counter target spell.", tags={"counterspell"},
    )


def _flashback_burn_template():
    return CardTemplate(
        name="Test Fixture: Flashback Burn Spell",
        card_types=[CardType.SORCERY],
        mana_cost=ManaCost(generic=1, red=1),
        supertypes=[], subtypes=[],
        power=None, toughness=None, loyalty=None,
        keywords=set(), abilities=[], color_identity=set(),
        produces_mana=[], enters_tapped=False,
        oracle_text="Deal 3 damage to any target. Flashback {2}{R}",
        tags=set(),
    )


def test_countered_flashback_spell_is_exiled_not_returned_to_graveyard(card_db):
    """Countering a spell cast via flashback must exile it (CR
    702.33a), mirroring the already-correct normal-resolution case —
    not return it to the graveyard where it could be flashed back
    again."""
    game = GameState(rng=random.Random(0))

    target_template = _flashback_burn_template()
    target_card = CardInstance(
        template=target_template, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="stack",
    )
    target_card._game_state = game
    target_card._cast_with_flashback = True
    target_card.has_flashback = True

    counter_template = _counterspell_template()
    counter_card = CardInstance(
        template=counter_template, owner=1, controller=1,
        instance_id=game.next_instance_id(), zone="stack",
    )
    counter_card._game_state = game

    target_item = StackItem(
        item_type=StackItemType.SPELL, source=target_card, controller=0,
        targets=[], effect=None, description="",
    )
    counter_item = StackItem(
        item_type=StackItemType.SPELL, source=counter_card, controller=1,
        targets=[target_card.instance_id], effect=None,
        description="Counter target spell.",
    )
    game.stack.push(target_item)
    game.stack.push(counter_item)

    game.resolve_stack()  # resolves the counterspell, countering target_item

    assert target_card.zone == "exile", (
        f"expected a countered flashback spell to be exiled (CR 702.33a "
        f"zone-replacement applies regardless of how it left the stack), "
        f"got zone={target_card.zone!r}"
    )
    assert target_card not in game.players[0].graveyard, (
        "countered flashback spell ended up in the graveyard — it could "
        "be flashed back again"
    )
    assert target_card in game.players[0].exile


def test_countered_normal_spell_still_goes_to_graveyard(card_db):
    """Regression guard: a spell with no alternate-cast flag keeps the
    existing correct behavior — countered spells go to the
    graveyard."""
    game = GameState(rng=random.Random(0))

    target_template = _flashback_burn_template()
    target_card = CardInstance(
        template=target_template, owner=0, controller=0,
        instance_id=game.next_instance_id(), zone="stack",
    )
    target_card._game_state = game

    counter_template = _counterspell_template()
    counter_card = CardInstance(
        template=counter_template, owner=1, controller=1,
        instance_id=game.next_instance_id(), zone="stack",
    )
    counter_card._game_state = game

    target_item = StackItem(
        item_type=StackItemType.SPELL, source=target_card, controller=0,
        targets=[], effect=None, description="",
    )
    counter_item = StackItem(
        item_type=StackItemType.SPELL, source=counter_card, controller=1,
        targets=[target_card.instance_id], effect=None,
        description="Counter target spell.",
    )
    game.stack.push(target_item)
    game.stack.push(counter_item)

    game.resolve_stack()

    assert target_card.zone == "graveyard"
    assert target_card in game.players[0].graveyard
