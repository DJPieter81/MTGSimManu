"""Bug 5 — Cycle doesn't log the drawn card.

Evidence: replays/boros_vs_affinity_bo3.txt:108 (G1 T2 Affinity)
    T2 P2: Cycle Sojourner's Companion (pay 2 mana, draw a card)
    ... (no [Draw] line)
    T3 P2: Play Springleaf Drum   ← "appears from nowhere"

The cycle log line describes the cost paid but never names the card
drawn as a result. Pieter's #7 readability complaint traces to this
gap.

Conservation-invariant candidate: every change to hand size must be
attributed in the log with the specific card name drawn (or
discarded). Cycle is one instance — future draw/discard effects that
skip logging should fail the same shape of test.
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


def _put_in_hand(game, card_db, name, controller):
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


def _put_in_library(game, card_db, name, controller):
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


def _put_land_on_battlefield(game, card_db, name, controller):
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


class TestCycleLogNamesDrawnCard:
    """activate_cycling must include the drawn card's name in the log."""

    def test_cycle_log_includes_drawn_card_name(self, card_db):
        """Cycle Street Wraith (pay 2 life). The log must name the
        card drawn from the top of the library."""
        game = GameState(rng=random.Random(0))
        # Cyclable card in hand.
        wraith = _put_in_hand(game, card_db, "Street Wraith", 0)
        # Known top-of-library card. Use something recognisable.
        top_card = _put_in_library(game, card_db, "Lightning Bolt", 0)

        ok = game.activate_cycling(0, wraith)
        assert ok, "activate_cycling returned False on a legal cycle"

        # Find the cycle log line.
        cycle_lines = [l for l in game.log if "Cycle" in l]
        assert cycle_lines, f"no Cycle log line produced: {game.log}"
        cycle_line = cycle_lines[-1]
        assert top_card.name in cycle_line, (
            f"Cycle log line does not name the drawn card. "
            f"Drawn: {top_card.name!r}. Log: {cycle_line!r}. "
            f"Expected something like 'Cycle Street Wraith "
            f"(... draw: Lightning Bolt)'."
        )

    def test_cycle_log_includes_drawn_card_when_mana_cost(self, card_db):
        """Cycle with a mana cost — same invariant, different cost
        path.  Uses Censor (plain ``Cycling {U}``) to exercise the
        mana-cost branch; Lórien Revealed's Islandcycling now tutors
        instead of drawing, so it is not a fit for this invariant."""
        game = GameState(rng=random.Random(0))
        # Give controller a blue source to pay {U}.
        _put_land_on_battlefield(game, card_db, "Island", 0)
        censor = _put_in_hand(game, card_db, "Censor", 0)
        top_card = _put_in_library(game, card_db, "Counterspell", 0)

        ok = game.activate_cycling(0, censor)
        assert ok, "activate_cycling returned False on a legal cycle"

        cycle_lines = [l for l in game.log if "Cycle" in l]
        assert cycle_lines, f"no Cycle log line produced: {game.log}"
        cycle_line = cycle_lines[-1]
        assert top_card.name in cycle_line, (
            f"Cycle log line does not name the drawn card. "
            f"Drawn: {top_card.name!r}. Log: {cycle_line!r}."
        )
