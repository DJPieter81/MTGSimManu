"""Bug C — Wrath of the Skies picks X by max-mana default, not by
marginal destruction value.

Design: docs/design/ev_correctness_overhaul.md §2.C

Wrath of the Skies: "You get X {E}, then you may pay any amount of
{E}.  Destroy each artifact, creature, and enchantment with mana
value less than or equal to the amount of {E} paid this way."

When every opponent permanent in range dies at X=0 anyway (all are
mana-value zero), spending extra X adds zero destruction and wastes
mana.  The engine's X selection at engine/game_state.py:1546-1600
has a Chalice-specific branch and falls through to "default to max
X" for Wrath — so the AI floods X whenever it can.

The right X is the lowest value that still destroys the maximum
threat-value on the opponent's board (minus own-board collateral),
subject to available mana.  For an opp board of 4× CMC-0 permanents
plus one CMC-7 creature that max-X can't touch, the answer is X=0.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _setup_main_phase(game):
    game.players[0].deck_name = "Boros Energy"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1


class TestWrathXOptimizesSweep:
    """Wrath of the Skies' X selection should maximise marginal
    destruction per mana, not default to max mana."""

    def test_x_zero_when_all_reachable_threats_are_cmc_zero(
            self, card_db):
        """Boros has 5 lands (enough to pay {W}{W} base + up to X=3).
        Opp has 4× CMC-0 permanents and one CMC-7 Thought Monitor
        that max-X (3) cannot reach.  Every X from 0 to 3 destroys
        exactly the same 4 permanents — so X=0 is the correct choice
        (saves 3 mana for other plays this or next turn)."""
        game = GameState(rng=random.Random(0))
        for _ in range(2):
            _add_to_battlefield(game, card_db, "Plains", controller=0)
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        wrath = _add_to_hand(game, card_db, "Wrath of the Skies",
                             controller=0)
        # Four CMC-0 permanents on opp board.
        _add_to_battlefield(game, card_db, "Ornithopter", controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _add_to_battlefield(game, card_db, "Welding Jar", controller=1)
        _add_to_battlefield(game, card_db, "Mox Opal", controller=1)
        # CMC-7 body out of reach for max-X (3) at our mana level.
        _add_to_battlefield(game, card_db, "Thought Monitor", controller=1)
        _setup_main_phase(game)

        success = game.cast_spell(0, wrath, targets=[])
        assert success, "Wrath cast should succeed — 5 lands ≥ {W}{W}"
        assert game.stack.items, "Stack should contain the Wrath spell"
        x_value = game.stack.items[-1].x_value

        assert x_value == 0, (
            f"Wrath of the Skies was cast with X={x_value}.  Every X "
            f"from 0 to 3 destroys exactly the same 4 CMC-0 permanents "
            f"(the CMC-7 Thought Monitor is out of reach at max-X=3), "
            f"so the marginal destruction gain of X>0 is zero.  The "
            f"correct X here is 0 — anything else burns mana for no "
            f"incremental value.  Fix: replace the 'default to max X' "
            f"branch at engine/game_state.py with a destroy-by-CMC "
            f"sweeper optimizer that picks the smallest X achieving "
            f"the best net threat reduction."
        )

    def test_x_equals_top_cmc_when_reachable_threat_exists(
            self, card_db):
        """Regression anchor: when the opponent has a CMC-3 threat we
        CAN kill at max-X, the optimizer should pick X=3 (not X=0).
        This ensures the fix doesn't over-correct and always pick X=0.

        Board: opp has Ornithopter (CMC 0) + Ragavan (CMC 1).  With
        5 lands we have max X=3.  Optimum X is 1: destroys both
        permanents (MV ≤ 1 covers both), and any X≥1 lands the same
        kill count while X=0 only kills Ornithopter.  So the optimal
        X should be ≥ 1."""
        game = GameState(rng=random.Random(0))
        for _ in range(2):
            _add_to_battlefield(game, card_db, "Plains", controller=0)
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        wrath = _add_to_hand(game, card_db, "Wrath of the Skies",
                             controller=0)
        _add_to_battlefield(game, card_db, "Ornithopter", controller=1)
        _add_to_battlefield(game, card_db, "Ragavan, Nimble Pilferer",
                             controller=1)
        _setup_main_phase(game)

        success = game.cast_spell(0, wrath, targets=[])
        assert success
        x_value = game.stack.items[-1].x_value

        assert x_value >= 1, (
            f"Regression: Wrath was cast with X={x_value} but opp has "
            f"a CMC-1 threat (Ragavan) that X=0 leaves alive.  The "
            f"optimizer should pick X ≥ 1 so Ragavan is destroyed."
        )
