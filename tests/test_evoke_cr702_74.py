"""CR 702.74 evoke mechanic compliance tests.

Verifies engine-level correctness of the evoke alternative-cast mechanic:

    CR 702.74a — You may cast a creature spell with evoke by paying its
    evoke cost instead of its mana cost.
    CR 702.74b — When the evoked creature enters the battlefield, its
    controller sacrifices it. The ETB ability triggers first; the
    sacrifice is a separate triggered ability that fires on ETB.

Covers four invariants:

1. Pitch-evoke exiles the pitched card to the exile zone (not graveyard).
2. The evoked creature is sacrificed after ETB — it ends up in the
   graveyard, not on the battlefield.
3. The ETB effect fires before the sacrifice — the effect resolves on a
   game state that still has the creature on the battlefield.
4. A hardcast creature (not evoked) is NOT sacrificed — it stays on the
   battlefield after ETB.

Plus one consistency invariant:

5. StackItem.evoked must be True when a spell is cast via evoke, so
   replay logging and any future code reading item.evoked sees the
   correct value.

Class size for evoke: Endurance, Solitude, Subtlety in the current DB
(Fury / Grief are not in ModernAtomic). All three share the same engine
path — these tests use Solitude (most observable ETB) and Endurance.

No card names appear in engine/ source code as a result of these tests.
The evoke mechanic is generic; these tests are diagnostic anchors.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.game_state import GameState


# ---------------------------------------------------------------------------
# Helpers shared across all tests
# ---------------------------------------------------------------------------

class _AlwaysEvokeCallbacks(DefaultCallbacks):
    """Stub AI that always opts to evoke — required for tests that exercise
    the evoke path through cast_spell(), which gates on should_evoke."""

    def should_evoke(self, game, player_idx, card):
        return True


def _make_game(callbacks=None):
    return GameState(
        rng=random.Random(42),
        callbacks=callbacks or _AlwaysEvokeCallbacks(),
    )


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"Card not found in DB: {name!r}"
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
    bucket = "library" if zone == "library" else zone
    getattr(game.players[controller], bucket).append(card)
    return card


def _untapped_land(game, card_db, name, controller):
    land = _add(game, card_db, name, controller, "battlefield")
    land.tapped = False
    return land


# ---------------------------------------------------------------------------
# 1. Pitch card goes to exile, not graveyard (CR 702.74a)
# ---------------------------------------------------------------------------

class TestPitchEvokeExilesCard:
    """The card exiled as pitch cost must land in the exile zone (CR 702.74a)."""

    def test_pitched_card_is_in_exile_after_evoke(self, card_db):
        """Pitch a white card to evoke Solitude — exile zone gains 1 card."""
        game = _make_game()
        # Opponent creature so Solitude has a valid ETB target.
        _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        pitch = _add(game, card_db, "Orim's Chant", 0, "hand")

        exile_before = len(game.players[0].exile)

        cast_ok = game.cast_spell(0, solitude)
        assert cast_ok, (
            "cast_spell must succeed for Solitude when a white pitch card "
            "and a valid creature target exist."
        )

        assert pitch.zone == "exile", (
            f"Orim's Chant pitched for Solitude evoke must be in zone "
            f"'exile', found {pitch.zone!r}. "
            f"CR 702.74a: the exile is the payment, not a discard."
        )
        exile_after = len(game.players[0].exile)
        assert exile_after == exile_before + 1, (
            f"Exile zone should have grown by 1 after the pitch, "
            f"was {exile_before}, now {exile_after}."
        )

    def test_pitched_card_is_not_in_hand_after_evoke(self, card_db):
        """The pitched card must leave the hand as part of paying evoke cost."""
        game = _make_game()
        _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        pitch = _add(game, card_db, "Orim's Chant", 0, "hand")

        game.cast_spell(0, solitude)

        assert pitch not in game.players[0].hand, (
            "Orim's Chant must leave P0's hand as part of the evoke cost. "
            "It is still in hand — the pitch payment was not taken."
        )


# ---------------------------------------------------------------------------
# 2. Evoked creature is sacrificed on ETB (CR 702.74b)
# ---------------------------------------------------------------------------

class TestEvokedCreatureIsSacrificed:
    """An evoked creature must be sacrificed after ETB, ending in the
    graveyard, not on the battlefield."""

    def test_evoked_solitude_is_in_graveyard_after_resolution(self, card_db):
        """Resolve an evoked Solitude; it must end in the graveyard."""
        game = _make_game()
        _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        _add(game, card_db, "Orim's Chant", 0, "hand")

        cast_ok = game.cast_spell(0, solitude)
        assert cast_ok, "cast_spell must succeed for evoke Solitude."

        while not game.stack.is_empty:
            game.resolve_stack()

        assert solitude.zone == "graveyard", (
            f"Evoked Solitude must be in the graveyard after resolution "
            f"(CR 702.74b: sacrifice on ETB). Found zone={solitude.zone!r}. "
            f"P0 battlefield: {[c.name for c in game.players[0].battlefield]}"
        )
        assert solitude not in game.players[0].battlefield, (
            "Evoked Solitude must NOT remain on the battlefield — it is "
            "sacrificed as a consequence of the evoke ETB trigger."
        )

    def test_evoked_endurance_is_in_graveyard_after_resolution(self, card_db):
        """Verify the same invariant holds for Endurance (green evoke)."""
        game = _make_game()
        # Seed graveyard so Endurance ETB has something to shuffle.
        _add(game, card_db, "Memnite", 1, "graveyard")
        endurance = _add(game, card_db, "Endurance", 0, "hand")
        # Pitch a green card for the evoke cost.
        _add(game, card_db, "Elvish Mystic", 0, "hand")

        cast_ok = game.cast_spell(0, endurance)
        assert cast_ok, "cast_spell must succeed for evoke Endurance."

        while not game.stack.is_empty:
            game.resolve_stack()

        assert endurance.zone == "graveyard", (
            f"Evoked Endurance must be in the graveyard after resolution "
            f"(CR 702.74b). Found zone={endurance.zone!r}."
        )


# ---------------------------------------------------------------------------
# 3. ETB effect fires before sacrifice (CR 702.74b ordering)
# ---------------------------------------------------------------------------

class TestEtbFiresBeforeSacrifice:
    """The ETB effect must resolve on a board state that still contains the
    evoked creature — both effects (ETB result + sacrifice) must be visible
    after the spell resolves.

    Solitude: ETB exiles a creature, sacrifice puts Solitude in graveyard.
    If sacrifice fired FIRST, the ETB would have no source and Memnite
    would survive.
    """

    def test_solitude_etb_exiles_creature_before_being_sacrificed(
            self, card_db):
        """Evoke Solitude → Memnite is exiled AND Solitude ends in graveyard.

        This pins the ETB-before-sacrifice ordering mandated by CR 702.74b.
        """
        game = _make_game()
        memnite = _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        _add(game, card_db, "Orim's Chant", 0, "hand")

        cast_ok = game.cast_spell(0, solitude)
        assert cast_ok

        while not game.stack.is_empty:
            game.resolve_stack()

        # ETB must have fired: Memnite exiled.
        assert memnite not in game.players[1].battlefield, (
            "Memnite must NOT be on the battlefield after Solitude resolves — "
            "its ETB (exile target creature) must fire before the sacrifice. "
            "If this fails, the sacrifice ran before the ETB."
        )
        assert memnite.zone == "exile", (
            f"Memnite should be in the exile zone (Solitude ETB), "
            f"found {memnite.zone!r}."
        )
        # Sacrifice must have fired: Solitude in graveyard.
        assert solitude.zone == "graveyard", (
            "Solitude must be in the graveyard after evoke resolution "
            "(sacrificed via CR 702.74b)."
        )


# ---------------------------------------------------------------------------
# 4. Hardcast creature is NOT sacrificed (CR 702.74 — only when evoked)
# ---------------------------------------------------------------------------

class TestHardcastCreatureNotSacrificed:
    """A creature cast for its normal mana cost must NOT be sacrificed.
    The evoke-sacrifice trigger fires ONLY when cast via evoke cost."""

    def test_hardcast_solitude_stays_on_battlefield(self, card_db):
        """Five Plains → hardcast Solitude → stays on the battlefield."""
        game = _make_game()
        # Opponent creature so Solitude ETB has a target.
        _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        # 5 untapped Plains — sufficient for WWWWW (hardcast).
        for _ in range(5):
            _untapped_land(game, card_db, "Plains", 0)

        cast_ok = game.cast_spell(0, solitude)
        assert cast_ok, (
            "cast_spell with 5 Plains and Solitude in hand must succeed "
            "via the hardcast path."
        )

        while not game.stack.is_empty:
            game.resolve_stack()

        assert solitude.zone == "battlefield", (
            f"Hardcast Solitude must stay on the battlefield (zone="
            f"{solitude.zone!r}). The evoke-sacrifice trigger fires only "
            f"when cast for the evoke cost — hardcast must never trigger "
            f"a sacrifice."
        )
        assert solitude in game.players[0].battlefield, (
            "Hardcast Solitude must be in P0's battlefield list."
        )

    def test_hardcast_endurance_stays_on_battlefield(self, card_db):
        """Hardcast Endurance (5 Forests) must not be sacrificed."""
        game = _make_game()
        _add(game, card_db, "Memnite", 1, "graveyard")
        endurance = _add(game, card_db, "Endurance", 0, "hand")
        for _ in range(5):
            _untapped_land(game, card_db, "Forest", 0)

        cast_ok = game.cast_spell(0, endurance)
        assert cast_ok, "Hardcast Endurance with 5 Forests must succeed."

        while not game.stack.is_empty:
            game.resolve_stack()

        assert endurance.zone == "battlefield", (
            f"Hardcast Endurance must stay on the battlefield, "
            f"found zone={endurance.zone!r}."
        )


# ---------------------------------------------------------------------------
# 5. StackItem.evoked flag consistency
# ---------------------------------------------------------------------------

class TestStackItemEvokedFlag:
    """StackItem.evoked must be True when a spell is cast via evoke.

    The field is declared on StackItem (stack.py) but was never set to
    True in cast_manager.py — the tracking used card._evoked instead.
    Any future code reading item.evoked (replay logging, triggered-ability
    dispatch) would silently see False. This test pins the contract.
    """

    def test_stack_item_evoked_flag_is_true_for_evoked_spell(self, card_db):
        """After cast_spell() for an evoke cast, the top StackItem must
        have evoked=True."""
        game = _make_game()
        _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        _add(game, card_db, "Orim's Chant", 0, "hand")

        cast_ok = game.cast_spell(0, solitude)
        assert cast_ok, "Evoke Solitude must cast successfully."

        assert not game.stack.is_empty, "Stack must be non-empty after cast."
        top = game.stack.top
        assert top is not None

        assert top.evoked is True, (
            f"StackItem.evoked must be True when Solitude was cast via "
            f"evoke. Found evoked={top.evoked!r}. "
            f"cast_manager.py sets card._evoked=True but does not propagate "
            f"the flag to the StackItem it pushes onto the stack — replay "
            f"logging and any future code reading item.evoked silently "
            f"sees False."
        )

    def test_stack_item_evoked_false_for_hardcast_spell(self, card_db):
        """After a hardcast, the StackItem must have evoked=False."""
        game = _make_game()
        _add(game, card_db, "Memnite", 1, "battlefield")
        solitude = _add(game, card_db, "Solitude", 0, "hand")
        for _ in range(5):
            _untapped_land(game, card_db, "Plains", 0)

        cast_ok = game.cast_spell(0, solitude)
        assert cast_ok, "Hardcast Solitude must succeed."

        top = game.stack.top
        assert top is not None

        assert top.evoked is False, (
            f"StackItem.evoked must be False for a hardcast spell, "
            f"found evoked={top.evoked!r}."
        )
