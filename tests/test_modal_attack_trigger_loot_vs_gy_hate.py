"""Modal attack trigger: a rummage, or exile up to one card from a graveyard.

Some Modern cards print a modal attack trigger of the form:

    Whenever this creature attacks, choose one --
    * Discard a card. If you do, draw a card.
    * Exile up to one target card from a graveyard.

The attack carrier takes the modal host (unit MTR): its controller chooses
the mode as the trigger is put on the stack (CR 603.3c), the AI by
`ai.modal.select_trigger_modes`, and the executors perform it. The first
card in this category is Territorial Kavu (Domain Zoo, 4-of mainboard); the
tests name the rule, not the card. Its name-keyed attack handler, which
chose the mode in the engine, is retired.

Mode-selection rule under test (the AI's):

  - The graveyard-exile mode is chosen when an opposing graveyard holds a
    card worth denying its owner (`ai.graveyard_removal.worth_aiming_at`:
    a keystone of their plan, or a card still a resource to them there).
  - Otherwise the rummage is chosen: discard the card the discard owner
    ranks least needed, then draw one.
  - Both modes dead -> the trigger changes nothing.
"""
from __future__ import annotations

import random
import pytest

from engine.cards import CardInstance
from engine.game_state import GameState


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    if tmpl.is_creature and card not in game.players[controller].creatures:
        game.players[controller].creatures.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    game.players[controller].hand.append(card)
    return card


def _add_to_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="library",
    )
    game.players[controller].library.append(card)
    return card


def _add_to_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    game.players[controller].graveyard.append(card)
    return card


def _fire_attack_trigger(game, card_db, attacker, controller):
    """Declare `attacker` as its controller's only attacker, the AI making
    the trigger's choices, so the attack carrier puts the trigger on the
    stack and resolves it."""
    from engine.combat_manager import CombatManager
    from engine.game_runner import AICallbacks
    from engine.game_state import Phase
    game.callbacks = AICallbacks()
    # Domain: two basic land types, so the attacker survives as a 2/2
    # once state-based actions are checked after the trigger resolves.
    for land in ("Mountain", "Forest"):
        _add_to_battlefield(game, card_db, land, controller)
    game.active_player = game.priority_player = controller
    game.current_phase = Phase.DECLARE_ATTACKERS
    CombatManager().declare_attackers(game, [attacker], controller)


# ──────────────────────────────────────────────────────────────────
# The first card with a modal loot-vs-gy-hate attack trigger is
# Territorial Kavu. Use it as the canonical test fixture; future
# cards with the same template will pass the same tests.
# ──────────────────────────────────────────────────────────────────
KAVU_NAME = "Territorial Kavu"


class TestModalAttackTriggerLootMode:
    def test_loot_mode_fires_when_no_graveyard_targets(self, card_db):
        """Rule: with a non-land in hand and an empty opposing graveyard,
        the trigger discards (worst non-land) and draws one. The
        graveyard-exile mode is not chosen because there's nothing
        to exile.
        """
        game = GameState(rng=random.Random(42))

        attacker = _add_to_battlefield(game, card_db, KAVU_NAME, 0)

        # Hand: 1 land + 1 non-land. Discard prefers the worst card
        # (a low-priority spell or extra land); the implementation
        # is free to pick either, but at least one must move from
        # hand to graveyard, and the player must draw a fresh card.
        _add_to_hand(game, card_db, "Mountain", 0)
        _add_to_hand(game, card_db, "Lightning Bolt", 0)
        # Library has a card so draw works.
        _add_to_library(game, card_db, "Lightning Bolt", 0)

        hand_size_before = len(game.players[0].hand)
        gy_size_before = len(game.players[0].graveyard)

        # Opponent's graveyard is empty → graveyard-exile mode is
        # not the chosen mode.
        assert game.players[1].graveyard == []

        _fire_attack_trigger(game, card_db, attacker, 0)

        # Loot mode invariant: net hand size unchanged (-1 +1), and
        # exactly one card moved into graveyard.
        assert len(game.players[0].hand) == hand_size_before, (
            "loot mode missing: net hand size must be unchanged after "
            "discard + draw")
        assert len(game.players[0].graveyard) == gy_size_before + 1, (
            "loot mode missing: discard step must add one card to graveyard")


class TestModalAttackTriggerGraveyardMode:
    def test_graveyard_mode_fires_for_a_card_worth_denying(self, card_db):
        """Rule: when an opposing graveyard holds a card still a resource
        to its owner there -- a flashback spell -- the trigger exiles it
        rather than rummaging.
        """
        game = GameState(rng=random.Random(42))

        attacker = _add_to_battlefield(game, card_db, KAVU_NAME, 0)
        _add_to_hand(game, card_db, "Lightning Bolt", 0)
        _add_to_library(game, card_db, "Mountain", 0)

        target_in_gy = _add_to_graveyard(game, card_db, "Faithless Looting", 1)
        _add_to_graveyard(game, card_db, "Mountain", 1)

        hand_before = [c.name for c in game.players[0].hand]

        _fire_attack_trigger(game, card_db, attacker, 0)

        assert target_in_gy.zone == "exile", (
            "graveyard mode missing: the card worth denying must be exiled")
        assert [c.name for c in game.players[0].hand] == hand_before, (
            "the rummage must not also be performed: the header says one")


class TestModalAttackTriggerNoOp:
    def test_trigger_no_ops_when_both_modes_dead(self, card_db):
        """Rule: empty hand + empty opposing graveyard → trigger
        completes without error and without illegal state changes.
        """
        game = GameState(rng=random.Random(42))

        attacker = _add_to_battlefield(game, card_db, KAVU_NAME, 0)
        # Both modes dead: nothing in hand, nothing in opponent's
        # graveyard.
        assert game.players[0].hand == []
        assert game.players[1].graveyard == []

        # Must not raise.
        _fire_attack_trigger(game, card_db, attacker, 0)

        # No state changes that violate the dead-mode invariant.
        assert game.players[0].graveyard == []
        assert game.players[1].graveyard == []
