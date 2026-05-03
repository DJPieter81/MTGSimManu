"""Modal attack trigger — loot vs graveyard-hate is the missing oracle pair.

Some Modern cards print a modal attack trigger of the form:

    Whenever this creature attacks, choose one —
    • Discard a card. If you do, draw a card.
    • Exile up to one target card from a graveyard.

The engine's `EffectTiming.ATTACK` registry path must implement both
modes when a card with that oracle is registered. The first card in
this category is Territorial Kavu (Domain Zoo, 4-of mainboard), but
the test names the **rule** (modal-attack-trigger choosing between
loot and graveyard hate), not the card, so future cards with the
same template are covered by the same suite.

Mode-selection rule under test:

  - Loot mode fires when controller has at least one non-land card
    in hand AND opponent has no high-CMC graveyard target. Equivalent
    of "discard the worst card, draw one".
  - Graveyard-exile mode fires when opponent's graveyard contains at
    least one non-token card with CMC ≥ 1 AND the loot would be
    hollow (controller's hand has no non-land discard candidate, or
    the GY target is more valuable than a single card swap).
  - Both modes dead → trigger is a no-op (logs only).
"""
from __future__ import annotations

import random
import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


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


def _fire_attack_trigger(game, attacker, controller):
    """Fire the registered attack trigger directly via EFFECT_REGISTRY,
    matching the call site in `engine/triggers.py`."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    EFFECT_REGISTRY.execute(
        attacker.template.name, EffectTiming.ATTACK,
        game, attacker, controller, targets=None, item=None,
    )


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

        _fire_attack_trigger(game, attacker, 0)

        # Loot mode invariant: net hand size unchanged (-1 +1), and
        # exactly one card moved into graveyard.
        assert len(game.players[0].hand) == hand_size_before, (
            "loot mode missing: net hand size must be unchanged after "
            "discard + draw")
        assert len(game.players[0].graveyard) == gy_size_before + 1, (
            "loot mode missing: discard step must add one card to graveyard")


class TestModalAttackTriggerGraveyardMode:
    def test_graveyard_mode_fires_when_loot_is_hollow(self, card_db):
        """Rule: when the controller has nothing useful to discard
        (only lands in hand) AND opponent's graveyard has a non-trivial
        target, the trigger picks the graveyard-exile mode.
        """
        game = GameState(rng=random.Random(42))

        attacker = _add_to_battlefield(game, card_db, KAVU_NAME, 0)

        # Loot is hollow: empty hand on controller side.
        # (No need to add lands; empty is a strict superset of "nothing
        # useful to discard".)
        assert game.players[0].hand == []

        # Opponent's graveyard has a juicy target — Murktide Regent
        # is a 5+ CMC reanimation/delve enabler; exiling it strips a
        # known threat / fuel.
        target_in_gy = _add_to_graveyard(game, card_db, "Murktide Regent", 1)
        # And a couple of lower-value cards to verify selection.
        _add_to_graveyard(game, card_db, "Mountain", 1)

        gy_size_before = len(game.players[1].graveyard)

        _fire_attack_trigger(game, attacker, 0)

        # Graveyard mode invariant: at least one card was exiled from
        # opponent's graveyard, and the high-CMC card is preferred.
        assert len(game.players[1].graveyard) < gy_size_before, (
            "graveyard mode missing: trigger must exile at least one card "
            "from opponent's graveyard when loot mode is hollow")
        assert target_in_gy not in game.players[1].graveyard, (
            "graveyard mode chose the wrong target: high-CMC card should "
            "be preferred over basic land")
        assert target_in_gy.zone == "exile", (
            "graveyard mode missing: exiled card's zone must be 'exile'")


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
        _fire_attack_trigger(game, attacker, 0)

        # No state changes that violate the dead-mode invariant.
        assert game.players[0].graveyard == []
        assert game.players[1].graveyard == []
