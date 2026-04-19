"""Bug E.2 — Engine does not implement typecycling / landcycling.

Sojourner's Companion oracle text:

    Affinity for artifacts
    Artifact landcycling {2} ({2}, Discard this card: Search your library
    for an artifact land card, reveal it, put it into your hand, then
    shuffle.)

`grep -r landcycling engine/ ai/` returns zero hits.  The engine's
cycling path (`engine/game_state.py:activate_cycling`) treats every
cycling variant as plain cycling — pay cost, discard, draw the top
card — regardless of the specific `<type>cycling` ability.  This means
Sojourner's "Search your library for an artifact land card" effectively
becomes "draw a random card," which (a) is a rules violation and (b)
misinforms the AI's cycle-EV model (the action's value comes from
tutoring a specific land type, not from a random draw).

Fix direction (see `docs/design/ev_correctness_overhaul.md` §5):
parse the cycling variant from oracle text; route typecycling to a
distinct resolver that searches the library, reveals a matching card,
adds it to hand, and shuffles.  Plain cycling still draws a random
card (regression guarded by existing `tests/test_cycle_log.py`).

This test exercises the rules path directly: library is arranged so
the top card is NOT an artifact land, while an artifact land is
buried beneath it.  Plain-cycling (the current bug) draws the top
card; landcycling (the fix) finds the buried artifact land.
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
    card._game_state = game
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
    card._game_state = game
    game.players[controller].library.append(card)
    return card


class TestLandcyclingSearchesLibrary:
    """Landcycling / typecycling tutors from the library — not a random
    draw off the top."""

    def test_sojourners_companion_finds_artifact_land_from_library(
            self, card_db):
        """Sojourner's Companion is in hand.  Library top is a
        Lightning Bolt (NOT an artifact land); an artifact land
        (Darksteel Citadel) is buried in the middle of the library.

        Plain cycling (current behaviour) draws the Lightning Bolt.
        Landcycling (the rules-correct behaviour) finds the Darksteel
        Citadel and puts it into hand.

        Assertion: post-activation the artifact land is in hand AND
        no Lightning Bolt has been drawn.  Sojourner itself ends up in
        the graveyard (discarded as part of the cycling cost).
        """
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Mountain", 0)
        _add_to_battlefield(game, card_db, "Mountain", 0)
        sojourner = _add_to_hand(game, card_db, "Sojourner's Companion", 0)

        # Library layout (top is index 0 — see engine draws with
        # `player.library.pop(0)`):
        #   0: Lightning Bolt   ← plain cycle would draw this
        #   1: Lightning Bolt
        #   2: Darksteel Citadel ← artifact landcycling must find this
        #   3: Lightning Bolt
        #   4: Lightning Bolt
        _add_to_library(game, card_db, "Lightning Bolt", 0)
        _add_to_library(game, card_db, "Lightning Bolt", 0)
        _add_to_library(game, card_db, "Darksteel Citadel", 0)
        _add_to_library(game, card_db, "Lightning Bolt", 0)
        _add_to_library(game, card_db, "Lightning Bolt", 0)

        assert game.can_cycle(0, sojourner), (
            "Precondition: Sojourner must be cycle-able with 2 Mountains."
        )

        result = game.activate_cycling(0, sojourner)
        assert result is True, "activate_cycling should succeed."

        me = game.players[0]
        hand_names = [c.name for c in me.hand]

        # Landcycling MUST have put an artifact land into hand.
        assert "Darksteel Citadel" in hand_names, (
            f"Sojourner's Companion uses 'Artifact landcycling {{2}}' — "
            f"per oracle it searches the library for an artifact land "
            f"and puts it into hand. Post-cycle hand = {hand_names}. "
            f"The engine treated it as plain cycling (drew a random "
            f"top card) — typecycling / landcycling is not implemented."
        )
        # And Lightning Bolt must NOT have been drawn — plain cycling
        # would have fetched the top card (Lightning Bolt) instead.
        assert "Lightning Bolt" not in hand_names, (
            f"Plain cycling fired instead of landcycling: drew Lightning "
            f"Bolt from the top of library instead of tutoring for an "
            f"artifact land. Post-cycle hand = {hand_names}."
        )

        # Sojourner itself should be in the graveyard (discarded as
        # part of cycling cost).
        gy_names = [c.name for c in me.graveyard]
        assert "Sojourner's Companion" in gy_names, (
            f"Sojourner should be in graveyard after cycling. "
            f"Graveyard = {gy_names}."
        )
