"""Subtlety ETB stack scan crashes on `_items` private attribute.

Bug: `engine/card_effects.py:216-223` references `game.stack._items`
(private). The Stack class only exposes `.items` (public), so the
ETB raises ``AttributeError: 'Stack' object has no attribute
'_items'`` when:

  1. Subtlety enters the battlefield, AND
  2. The stack is non-empty (so the ETB scan engages)

Most games never trigger this path (Subtlety is a flash creature
typically cast in response to spells; if the responding spell is
the only stack item it has resolved by the time Subtlety's ETB
fires after Subtlety itself resolved → empty stack). It bites
when a longer chain is mid-resolution: cascade-into-Subtlety, or
when an opponent spell triggers cascading effects that leave items
on the stack while Subtlety's ETB processes.

Discovered during Phase 2b retry matrix run when distribution-based
combo scoring led the AI to interact with Goryo's Vengeance more
aggressively, sometimes returning Subtlety from graveyards via
Living End's mass reanimation. Fix is independent of the refactor
— pre-existing engine bug.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_zone(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def _push_creature_spell(game, card_db, name, controller):
    """Put a creature spell from `controller` onto the stack."""
    card = CardInstance(
        template=card_db.get_card(name),
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    card._game_state = game
    item = StackItem(
        item_type=StackItemType.SPELL,
        source=card,
        controller=controller,
    )
    game.stack.push(item)
    return card, item


class TestSubtletyStackScan:
    """Subtlety's ETB must use Stack's public `.items` attribute."""

    def test_subtlety_etb_with_creature_spell_on_stack(self, card_db):
        """Opponent has a creature spell on the stack; Subtlety enters
        and its ETB scans for it. Pre-fix this raises AttributeError
        because the handler reads game.stack._items (private)."""
        game = GameState(rng=random.Random(0))

        # P2 (opponent) has a creature spell on the stack.
        opp_creature, opp_item = _push_creature_spell(
            game, card_db, "Grizzly Bears", controller=1)
        assert not game.stack.is_empty
        assert len(game.stack.items) == 1

        # P1 plays Subtlety; resolve its ETB directly.
        subtlety = _add_to_zone(game, card_db, "Subtlety", 0, "battlefield")
        from engine.card_effects import subtlety_etb

        # Should NOT raise AttributeError; should put the targeted
        # creature spell on top of P2's library.
        subtlety_etb(game, subtlety, controller=0)

        # Opponent's creature spell removed from stack and now on
        # top of their library (Subtlety's effect).
        assert game.stack.is_empty, (
            f"Subtlety should have removed the creature spell from "
            f"the stack; remaining: {game.stack.items}")
        assert opp_creature in game.players[1].library, (
            f"Opponent's creature should be on their library; "
            f"library top: {game.players[1].library[:3] if game.players[1].library else 'empty'}")
        assert game.players[1].library[0] is opp_creature, (
            f"Should be on TOP of library, not bottom; "
            f"position: {game.players[1].library.index(opp_creature)}")

    def test_subtlety_etb_with_empty_stack_no_op(self, card_db):
        """Empty stack: ETB fizzles cleanly (oracle says 'up to one'
        target). No AttributeError, no crash."""
        game = GameState(rng=random.Random(0))
        assert game.stack.is_empty

        subtlety = _add_to_zone(game, card_db, "Subtlety", 0, "battlefield")
        from engine.card_effects import subtlety_etb

        # Should not raise.
        subtlety_etb(game, subtlety, controller=0)

        assert game.stack.is_empty
