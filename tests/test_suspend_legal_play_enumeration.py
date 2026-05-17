"""Suspend must be a source of legal plays in get_legal_plays.

Living End loop break (2026-05-09): the engine implements
CastManager.can_suspend / suspend_card and GameState exposes them
(engine/cast_manager.py:404-505, engine/game_state.py:292-296), but
GameState.get_legal_plays only enumerates four play sources: land
drops, hand-castable spells, flashback/escape from graveyard, and
cycling activations.  Suspend is absent.

Effect on every Modern suspend deck: EVPlayer pulls its candidate
set from get_legal_plays (ai/ev_player.py:348), so the AI never
sees "suspend X" as a legal action and cannot score it.  Living
End sits in hand all game; if no cascade enabler is drawn by T4
the deck dies without ever resolving.

Class size: every Modern suspend printing — Living End, Ancestral
Vision, Crashing Footfalls, Restore Balance, Wheel of Fate, Lotus
Bloom, Greater Gargadon, plus future printings.  Mechanic-driven
fix; no card names.

These tests are RED pre-fix.  The fix extends get_legal_plays with
a fifth source: hand cards where can_suspend returns True.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


class TestSuspendLegalPlayEnumeration:
    """A suspend-keyword card in hand whose suspend cost is payable
    must be returned by get_legal_plays.  This is the engine-level
    contract — without it, no AI scoring layer can ever propose a
    suspend play."""

    def test_living_end_with_mana_is_legal_play(self, card_db):
        """Living End in hand, 4 Swamps untapped, MAIN1, empty stack
        → Living End must appear in legal plays.  Pre-fix: returns
        empty list because Living End is CMC 0 (not castable from
        hand) and suspend is not enumerated.  Post-fix: Living End
        appears via the suspend branch."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield")
        living_end = _add(game, card_db, "Living End",
                          controller=0, zone="hand")
        # Sanity: card has SUSPEND keyword and can_suspend returns True.
        assert Keyword.SUSPEND in living_end.template.keywords, (
            "Living End must have SUSPEND keyword for this test to "
            "be meaningful — fix the card database, not the test.")
        assert game.can_suspend(0, living_end), (
            "Living End with 4 Swamps must be suspend-payable. If "
            "this assertion fails, fix CastManager.can_suspend "
            "before this test.")
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        legal = game.get_legal_plays(0)
        assert living_end in legal, (
            "Living End is suspend-payable in this position but "
            "get_legal_plays does not return it.  Suspend is the "
            "missing fifth source of legal plays — extend "
            "GameState.get_legal_plays with a hand-loop calling "
            "self.can_suspend.")

    def test_living_end_without_mana_not_legal(self, card_db):
        """Living End in hand, only 1 Swamp on battlefield → cannot
        pay {2}{B}{B}, must NOT appear in legal plays.  Regression
        guard: the suspend enumeration must respect can_suspend's
        mana-availability check."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Swamp", controller=0, zone="battlefield")
        living_end = _add(game, card_db, "Living End",
                          controller=0, zone="hand")
        assert not game.can_suspend(0, living_end), (
            "Sanity: 1 land cannot pay {2}{B}{B}; can_suspend must "
            "return False.")
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        legal = game.get_legal_plays(0)
        assert living_end not in legal, (
            "1 Swamp is insufficient for Living End's {2}{B}{B} "
            "suspend cost.  Enumeration must gate on can_suspend, "
            "not just on the SUSPEND keyword.")

    def test_already_suspended_card_not_re_enumerated(self, card_db):
        """A card already in exile with time counters is not in
        hand and must not be returned by get_legal_plays.  Suspend
        enumeration must read from player.hand only."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield")
        living_end = _add(game, card_db, "Living End",
                          controller=0, zone="hand")
        # Manually move to exile with counters as if suspend was paid.
        game.players[0].hand.remove(living_end)
        living_end.zone = "exile"
        living_end.suspended = True
        living_end.suspend_counters = 3
        game.players[0].exile.append(living_end)
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        legal = game.get_legal_plays(0)
        assert living_end not in legal, (
            "A card already exiled and suspended must not be "
            "re-enumerated as suspendable.  The hand-zone check in "
            "can_suspend handles this; the test guards regression.")

    def test_ancestral_vision_suspend_legal_uses_keyword(
            self, card_db):
        """Generic-class proof: Ancestral Vision is a different
        suspend card (cost {U}, suspend 4) and must also be
        enumerated.  The fix must be keyword-driven, not Living-End
        specific."""
        tmpl = card_db.get_card("Ancestral Vision")
        if tmpl is None:
            pytest.skip("Ancestral Vision not in local card DB")
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Island", controller=0,
             zone="battlefield")
        vision = _add(game, card_db, "Ancestral Vision",
                      controller=0, zone="hand")
        assert Keyword.SUSPEND in vision.template.keywords, (
            "Ancestral Vision must carry SUSPEND in the test DB.")
        assert game.can_suspend(0, vision), (
            "1 Island pays {U}; can_suspend must return True.")
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        legal = game.get_legal_plays(0)
        assert vision in legal, (
            "Ancestral Vision is suspend-payable; get_legal_plays "
            "must enumerate it.  The fix is the SAME line of code "
            "that enumerates Living End — generic across the suspend "
            "class, not deck-specific.")
