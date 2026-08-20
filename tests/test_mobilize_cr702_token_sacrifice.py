"""CR 702 Mobilize mechanic — tapped-and-attacking token sacrifice compliance.

Mobilize N (CR 702 — keyword ability appearing on cards like Voice of Victory):
"Whenever this creature attacks, create N tapped and attacking 1/1 Warrior
creature tokens. Sacrifice them at the beginning of the next end step."

Two invariants under test:

1. ``test_mobilize_tokens_sacrifice_at_end_step``
   After a turn in which a Mobilize creature attacked, ALL Warrior tokens
   created by that trigger must be gone from the battlefield (moved to the
   graveyard via sacrifice) at the start of the next end step.  The engine
   must not let them persist into subsequent turns, where they would
   contribute permanently to the attacking army.

2. ``test_mobilize_tokens_enter_as_tapped_and_attacking``
   The tokens are created already tapped; they should not arrive as
   untapped, fresh blockers.

Class size: any card with the 'mobilize' keyword shares this engine path
(Avenger of the Fallen, Bone-Cairn Butcher, Dalkovan Packbeasts, Dragonback
Lancer, Nightblade Brigade, Reigning Victor, Shock Brigade, Stadium
Headliner, Venerated Stormsinger, Voice of Victory — 10 cards in the DB as
of 2026-08).  Tests use Voice of Victory (most explicit oracle wording) as
the diagnostic anchor; the fix is mechanic-level, not card-specific.

No card names appear in engine/ source code as a result of these tests.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.game_state import GameState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_game() -> GameState:
    return GameState(rng=random.Random(42), callbacks=DefaultCallbacks())


def _add(game: GameState, card_db, name: str, controller: int, zone: str) -> CardInstance:
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


def _warrior_tokens_on_battlefield(game: GameState, controller: int) -> int:
    """Count Warrior tokens on the battlefield for the given controller."""
    return sum(
        1
        for c in game.players[controller].battlefield
        if getattr(c, 'is_token', False)
        and 'warrior' in (c.template.name or '').lower()
    )


def _warrior_tokens_in_graveyard(game: GameState, controller: int) -> int:
    """Count Warrior tokens in the graveyard for the given controller."""
    return sum(
        1
        for c in game.players[controller].graveyard
        if getattr(c, 'is_token', False)
        and 'warrior' in (c.template.name or '').lower()
    )


# ---------------------------------------------------------------------------
# 1. Mobilize tokens are sacrificed at the beginning of the next end step
# ---------------------------------------------------------------------------

class TestMobilizeTokenSacrificeAtEndStep:
    """Warrior tokens created by Mobilize must not survive past the end step
    of the turn they were created in (CR 702 Mobilize reminder text:
    'Sacrifice them at the beginning of the next end step.')."""

    def test_mobilize_tokens_sacrifice_at_end_step(self, card_db):
        """Warrior tokens from a Mobilize trigger are gone after end_step fires.

        Uses Voice of Victory (Mobilize 2) as the diagnostic card;
        the mechanic is generic and the fix must cover all Mobilize cards.
        """
        game = _make_game()

        # Place a Mobilize creature on P0's battlefield.
        voice = _add(game, card_db, "Voice of Victory", 0, "battlefield")
        # Ensure it has the mobilize attribute
        assert getattr(voice.template, 'has_mobilize', False), (
            "Voice of Victory template must carry has_mobilize=True "
            "so the oracle resolver can detect the mechanic."
        )

        # Simulate the attack trigger: fire on_attack_trigger directly so we
        # isolate Mobilize token creation from full combat machinery.
        from engine.oracle_resolver import resolve_attack_trigger
        resolve_attack_trigger(game, voice, 0)

        tokens_after_attack = _warrior_tokens_on_battlefield(game, 0)
        assert tokens_after_attack == 2, (
            f"Voice of Victory Mobilize 2 should create 2 Warrior tokens "
            f"during attack; found {tokens_after_attack}."
        )

        # Now fire the end step — tokens must be sacrificed.
        game.turn_mgr.end_of_turn_cleanup(game)

        tokens_after_end = _warrior_tokens_on_battlefield(game, 0)
        assert tokens_after_end == 0, (
            f"Mobilize Warrior tokens must be sacrificed at the beginning "
            f"of the next end step (CR 702 Mobilize). "
            f"Found {tokens_after_end} tokens still on the battlefield after "
            f"end_step — the sacrifice rider was never registered or processed."
        )

    def test_mobilize_sacrifice_does_not_affect_non_mobilize_tokens(self, card_db):
        """End-step sacrifice must only remove Mobilize-registered tokens,
        not any other token a player controls.

        Verifies the fix is scoped: only tokens explicitly registered by the
        Mobilize handler are sacrificed at end step.
        """
        game = _make_game()

        # Create a generic creature token that should survive (not from Mobilize).
        from engine.permanent_effects import PermanentEffects
        other_tokens = PermanentEffects.create_token(
            game, 0, "soldier", count=1, power=1, toughness=1
        )
        assert len(other_tokens) == 1

        # Now fire a Mobilize trigger.
        voice = _add(game, card_db, "Voice of Victory", 0, "battlefield")
        from engine.oracle_resolver import resolve_attack_trigger
        resolve_attack_trigger(game, voice, 0)

        # Confirm warrior tokens exist.
        assert _warrior_tokens_on_battlefield(game, 0) == 2

        # Fire end step.
        game.turn_mgr.end_of_turn_cleanup(game)

        # Warrior tokens are gone; the unrelated Soldier token persists.
        assert _warrior_tokens_on_battlefield(game, 0) == 0, (
            "Warrior tokens from Mobilize must be sacrificed at end step."
        )
        soldier_on_btl = sum(
            1 for c in game.players[0].battlefield
            if getattr(c, 'is_token', False)
            and 'soldier' in (c.template.name or '').lower()
        )
        assert soldier_on_btl == 1, (
            "Non-Mobilize tokens (Soldier) must NOT be sacrificed by the "
            "Mobilize end-step handler."
        )


# ---------------------------------------------------------------------------
# 2. Mobilize tokens enter tapped (tapped-and-attacking)
# ---------------------------------------------------------------------------

class TestMobilizeTokensEnterTapped:
    """Mobilize tokens are described as 'tapped and attacking' — they must
    arrive on the battlefield in a tapped state."""

    def test_mobilize_tokens_enter_as_tapped(self, card_db):
        """Warrior tokens created by Mobilize must be tapped on entry.

        'Tapped and attacking' means the tokens enter tapped; the engine
        should not leave them as fresh untapped blockers.
        """
        game = _make_game()

        voice = _add(game, card_db, "Voice of Victory", 0, "battlefield")
        battlefield_before = set(id(c) for c in game.players[0].battlefield)

        from engine.oracle_resolver import resolve_attack_trigger
        resolve_attack_trigger(game, voice, 0)

        # Find the newly-created Warrior tokens.
        new_tokens = [
            c for c in game.players[0].battlefield
            if id(c) not in battlefield_before
            and getattr(c, 'is_token', False)
        ]
        assert len(new_tokens) == 2, (
            f"Expected 2 new Warrior tokens; found {len(new_tokens)}."
        )
        for token in new_tokens:
            assert token.tapped, (
                f"Mobilize token '{token.template.name}' must enter tapped "
                f"(rule: 'tapped and attacking'). Found tapped=False."
            )
