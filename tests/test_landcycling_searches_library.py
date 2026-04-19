"""Bug E.2 — Landcycling resolves as plain cycling; engine never
searches the library.

Design: docs/design/ev_correctness_overhaul.md §2.E + §5

Sojourner's Companion oracle: "Artifact landcycling {2} ({2},
Discard this card: Search your library for an artifact land card,
reveal it, put it into your hand, then shuffle.)"

Current engine at `engine/game_state.py:activate_cycling` uses one
uniform path for every cycling variant — pay cost, discard, draw a
card.  Landcycling/typecycling are meant to TUTOR a specific card
type from the library, not just draw the top card.

`grep -r 'landcycling' engine/ ai/` returns zero hits today —
landcycling is silently mis-handled as plain cycling.

Fix: route landcycling / typecycling through a tutor-style path
that (1) searches the library, (2) reveals a matching card, (3)
puts it in hand, (4) shuffles.  Regression: plain cycling (Street
Wraith, Architects of Will) still draws a random card.
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
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _add_to_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
    return card


class TestLandcyclingSearchesLibrary:
    """Artifact landcycling must tutor an artifact land out of the
    library, not draw the top card."""

    def test_sojourners_companion_puts_artifact_land_into_hand(
            self, card_db):
        """Library: 3 Lightning Bolt on top + 1 Darksteel Citadel
        buried at the bottom.  Activating Sojourner's Companion's
        artifact landcycling must put the Darksteel Citadel (the
        only artifact land in the library) into hand — regardless of
        the top-of-library order.  Current engine draws whatever is
        on top (Bolt), so the artifact land stays in the library."""
        game = GameState(rng=random.Random(42))
        # 2 Mountains to pay {2} cycling cost.
        for _ in range(2):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        sojourner = _add_to_hand(game, card_db, "Sojourner's Companion",
                                  controller=0)
        # Three top-of-library Bolts would get drawn by plain cycling.
        for _ in range(3):
            _add_to_library(game, card_db, "Lightning Bolt", controller=0)
        citadel = _add_to_library(game, card_db, "Darksteel Citadel",
                                    controller=0)

        success = game.activate_cycling(0, sojourner)
        assert success, "cycling should succeed — 2 Mountains pay {2}"

        hand_names = [c.name for c in game.players[0].hand]
        assert "Darksteel Citadel" in hand_names, (
            f"Sojourner's Companion artifact landcycling left the "
            f"library unsearched — hand after cycle = {hand_names!r}.  "
            f"The engine should tutor the Darksteel Citadel out of "
            f"the library (it is the only artifact land there) and "
            f"place it in hand, per oracle text: 'Search your library "
            f"for an artifact land card, reveal it, put it into your "
            f"hand, then shuffle.'  Current activate_cycling draws "
            f"the top card (a Lightning Bolt) instead."
        )
        # Sojourner itself went to graveyard (the discard half of
        # landcycling's cost).
        gy_names = [c.name for c in game.players[0].graveyard]
        assert "Sojourner's Companion" in gy_names, (
            f"Sojourner should have been discarded to the graveyard "
            f"as part of the cycling cost; graveyard = {gy_names!r}."
        )
        # Library should have shuffled — no strict order assertion
        # possible, but the citadel must no longer be in library.
        library_names = [c.name for c in game.players[0].library]
        assert "Darksteel Citadel" not in library_names, (
            f"Darksteel Citadel was tutored to hand but still appears "
            f"in the library; library = {library_names!r}."
        )

    def test_plain_cycling_still_draws_random_card(self, card_db):
        """Regression: Street Wraith's plain cycling ({Pay 2 life,
        Discard this card: Draw a card.}) must still draw a card
        from the top of the library — not tutor anything.  The fix
        differentiates landcycling/typecycling from plain cycling."""
        game = GameState(rng=random.Random(0))
        wraith = _add_to_hand(game, card_db, "Street Wraith", controller=0)
        bolt = _add_to_library(game, card_db, "Lightning Bolt",
                                controller=0)
        _add_to_library(game, card_db, "Plains", controller=0)

        success = game.activate_cycling(0, wraith)
        assert success, "Street Wraith cycles for 2 life"

        hand_names = [c.name for c in game.players[0].hand]
        # Plain cycling draws TOP of library → first-pushed = bolt is
        # actually at index 0, so with a 2-card library, top-of-deck
        # behaviour depends on draw semantics.  The weak spec: SOMETHING
        # from the library is in hand, not specifically the Plains.
        assert hand_names, (
            f"After plain cycling, hand should contain the drawn card "
            f"but is empty: {hand_names!r}."
        )
        assert any(n in ("Lightning Bolt", "Plains") for n in hand_names), (
            f"Plain cycling should draw one of the library cards "
            f"(Lightning Bolt / Plains).  Hand = {hand_names!r}."
        )
