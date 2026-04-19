"""Invariants for the marginal-contribution threat formula.

    threat(P) = V_owner(B) - V_owner(B \\ {P})

These tests anchor the properties the plan claims fall out of the
definition — no per-synergy bolt-on, no anchor constants.
"""
from __future__ import annotations

import random

import pytest

from ai.clock import position_value
from ai.ev_evaluator import snapshot_from_game
from ai.permanent_threat import permanent_threat
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _mk(game, card_db, name, ctrl):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=ctrl,
        controller=ctrl,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[ctrl].battlefield.append(card)
    return card


def _attach(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


class TestMarginalIdentity:
    """threat(P) must equal V(B) - V(B \\ {P}) by definition."""

    def test_threat_equals_position_value_delta(self, card_db):
        game = GameState(rng=random.Random(0))
        goyf = _mk(game, card_db, "Tarmogoyf", 1)

        opp = game.players[1]
        v_full = position_value(snapshot_from_game(game, 1))
        threat = permanent_threat(goyf, opp, game)

        # Mirror what permanent_threat does: remove and re-evaluate.
        opp.battlefield.remove(goyf)
        v_partial = position_value(snapshot_from_game(game, 1))
        opp.battlefield.append(goyf)

        assert threat == pytest.approx(v_full - v_partial, abs=1e-9), (
            f"threat={threat} must equal V(B)-V(B\\{{P}})="
            f"{v_full - v_partial}"
        )

    def test_card_not_on_battlefield_returns_zero(self, card_db):
        """Cards in zones other than `owner.battlefield` have no threat."""
        game = GameState(rng=random.Random(0))
        _mk(game, card_db, "Ornithopter", 1)

        # Build a card that is not on anyone's battlefield.
        tmpl = card_db.get_card("Ornithopter")
        stray = CardInstance(
            template=tmpl, owner=1, controller=1,
            instance_id=game.next_instance_id(), zone="hand",
        )
        stray._game_state = game
        assert permanent_threat(stray, game.players[1], game) == 0.0


class TestScalingEquipment:
    """Test 2: With opp's board loaded with artifacts and Plating
    equipped, removing Plating strips the equipped power bonus.
    Its threat exceeds that of a generic mana rock."""

    def test_equipped_plating_threat_exceeds_plain_mana_rock(self, card_db):
        game = GameState(rng=random.Random(0))
        orn = _mk(game, card_db, "Ornithopter", 1)
        plating = _mk(game, card_db, "Cranial Plating", 1)
        _mk(game, card_db, "Memnite", 1)
        rock = _mk(game, card_db, "Springleaf Drum", 1)
        _mk(game, card_db, "Springleaf Drum", 1)
        _attach(plating, orn)

        opp = game.players[1]
        threat_plating = permanent_threat(plating, opp, game)
        threat_rock = permanent_threat(rock, opp, game)

        assert threat_plating > threat_rock, (
            f"Plating threat ({threat_plating}) must exceed plain "
            f"mana-rock threat ({threat_rock}) when Plating is "
            f"equipped and opp has a wide artifact board."
        )


class TestSynergyEnablement:
    """Test 3: Ornithopter in an artifact-synergy board has threat
    greater than Ornithopter alone, because removing it strips
    scaling bonuses from equipped creatures.  No bolt-on — this is
    a direct consequence of the marginal formula."""

    def test_ornithopter_threat_higher_in_scaling_board(self, card_db):
        # Rich board: Plating equipped to Ornithopter + padding artifacts
        rich = GameState(rng=random.Random(0))
        orn_rich = _mk(rich, card_db, "Ornithopter", 1)
        plating = _mk(rich, card_db, "Cranial Plating", 1)
        _mk(rich, card_db, "Memnite", 1)
        _mk(rich, card_db, "Springleaf Drum", 1)
        _mk(rich, card_db, "Springleaf Drum", 1)
        _attach(plating, orn_rich)
        threat_rich = permanent_threat(orn_rich, rich.players[1], rich)

        # Lean board: Ornithopter alone
        lean = GameState(rng=random.Random(0))
        orn_lean = _mk(lean, card_db, "Ornithopter", 1)
        threat_lean = permanent_threat(orn_lean, lean.players[1], lean)

        assert threat_rich > threat_lean + 2.0, (
            f"Ornithopter threat must jump in an artifact-synergy "
            f"board: rich={threat_rich}, lean={threat_lean}. The gap "
            f"reflects the +N/+0 Plating bonus vanishing when the "
            f"wearer is removed."
        )


class TestNonArtifactNeutrality:
    """Test 4: On a creatures-only board (no artifact scaling),
    Ornithopter's threat collapses to its intrinsic contribution.
    Proves the formula is not biased toward artifacts — it uses
    whatever scaling the board contains."""

    def test_ornithopter_low_threat_on_creature_only_board(self, card_db):
        game = GameState(rng=random.Random(0))
        orn = _mk(game, card_db, "Ornithopter", 1)
        _mk(game, card_db, "Goblin Guide", 1)
        _mk(game, card_db, "Monastery Swiftspear", 1)

        threat = permanent_threat(orn, game.players[1], game)
        # A 0/2 blocker on a non-scaling board should be a minor threat.
        # Compare to a 2/1 haster on the same board.
        goblin = game.players[1].battlefield[1]
        goblin_threat = permanent_threat(goblin, game.players[1], game)
        assert threat < goblin_threat, (
            f"Ornithopter (0/2) threat={threat} should be less than "
            f"Goblin Guide (2/1 haste) threat={goblin_threat} on a "
            f"non-scaling board."
        )


class TestArtifactRemovalStripsScaling:
    """Test 5: Removing any artifact on a Plating-equipped board
    drops the equipped creature's power by 1 (Plating scales with
    artifact count).  So a non-descript artifact like Memnite has
    HIGHER threat on a scaling board than on an isolated board —
    even though its own P/T is the same in both cases."""

    def test_memnite_threat_higher_when_plating_attached(self, card_db):
        # Scaling: Memnite is one of several artifacts, Plating is
        # equipped to Ornithopter and depends on the artifact count.
        scaling = GameState(rng=random.Random(0))
        orn = _mk(scaling, card_db, "Ornithopter", 1)
        plating = _mk(scaling, card_db, "Cranial Plating", 1)
        memnite_scaling = _mk(scaling, card_db, "Memnite", 1)
        _mk(scaling, card_db, "Springleaf Drum", 1)
        _mk(scaling, card_db, "Springleaf Drum", 1)
        _attach(plating, orn)
        threat_scaling = permanent_threat(
            memnite_scaling, scaling.players[1], scaling)

        # Isolated: Memnite alone on its board (no scaling).
        isolated = GameState(rng=random.Random(0))
        memnite_alone = _mk(isolated, card_db, "Memnite", 1)
        threat_isolated = permanent_threat(
            memnite_alone, isolated.players[1], isolated)

        assert threat_scaling > threat_isolated, (
            f"Memnite threat must rise when its removal drops opp's "
            f"artifact count and an equipped Plating loses a point of "
            f"scaling: scaling-board={threat_scaling}, "
            f"isolated={threat_isolated}."
        )


class TestCardParity:
    """Test 6: Two identical templates in the same board position
    produce identical threat values.  Card name is not a factor in
    scoring — only board contribution is."""

    def test_two_memnites_identical_threat(self, card_db):
        game = GameState(rng=random.Random(0))
        m1 = _mk(game, card_db, "Memnite", 1)
        m2 = _mk(game, card_db, "Memnite", 1)

        t1 = permanent_threat(m1, game.players[1], game)
        t2 = permanent_threat(m2, game.players[1], game)

        assert t1 == pytest.approx(t2, abs=1e-9), (
            f"Two Memnites on the same board must have identical "
            f"marginal threat: t1={t1}, t2={t2}."
        )
