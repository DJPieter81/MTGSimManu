"""Removal target picker must rank creature artifacts and non-creature
artifacts on the same threat scale.

Rule under test:
  When a removal effect can hit ANY artifact (e.g. Wear // Tear,
  Force of Vigor, Thraben Charm enchantment-mode-fallback in artifact
  contexts, Solitude-style "exile target creature" against a board
  with creature-artifacts vs accessory artifacts), the candidate
  ranking must put scoring on a single principled scale.

Why this matters:
  The legacy `engine.card_effects._threat_score` mixed two scales:
    * For a creature   → `ai.ev_evaluator.creature_threat_value`
                         (clock_impact * 20 — heuristic, oracle-driven)
    * For a non-creature → `ai.permanent_threat.permanent_threat`
                           (position-value DELTA — marginal contribution)
  These do not share units. On an artifact-dense Affinity board the
  creature-side score collapsed to a small number while the non-
  creature side reflected actual position-value swing, so removal
  preferred mana rocks (Springleaf Drum, Mox Opal) over the Plating-
  equipped artifact creature that was actually carrying the win.

Detection (no card names): targeting code must use a single threat
primitive — `permanent_threat` — for every candidate when the spell
can target both creature and non-creature artifacts. The marginal
formula already covers equipment carriers, scaling artifacts, and
mana rocks under one definition (V_owner(B) - V_owner(B \\ {P})).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.card_effects import _threat_score
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


def _attach_equipment(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


class TestRemovalTargetScaleConsistency:
    """`_threat_score` is the picker shared by Wear // Tear, Force of
    Vigor, Solitude, and Thraben Charm.  Creature and non-creature
    candidates must be scored on the same scale so the ranking is a
    true comparison and not a category error."""

    def test_equipped_carrier_outranks_attached_equipment_on_same_scale(
            self, card_db):
        """Plating equipped to Memnite. Both are artifacts so a Wear-
        // Tear-style picker considers them together.  Killing the
        carrier (Memnite) takes the body AND drops Plating off; killing
        Plating only takes the equipment.  The carrier's threat must
        therefore strictly exceed the bare equipment's threat under any
        principled definition.

        Pre-fix: `_threat_score` returns
        `creature_threat_value(memnite)` (clock units, ≈ 5) for the
        creature side and `permanent_threat(plating, ...)` (position-
        value delta, ≈ 11) for the equipment side.  The two numbers
        live in different unit systems, so `Memnite < Plating` even
        though removing Memnite is the strictly larger position swing.

        Post-fix: both go through `permanent_threat`, so
        `Memnite ≥ Plating` (Memnite's marginal includes the Plating
        drop because the equipment falls off when its host dies)."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)

        memnite = _add_to_battlefield(game, card_db, "Memnite",
                                       controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        # Pad the artifact count so Plating's "+X/+0 where X = artifacts
        # you control" is meaningful.
        _add_to_battlefield(game, card_db, "Springleaf Drum",
                             controller=1)
        _add_to_battlefield(game, card_db, "Mox Opal", controller=1)
        _attach_equipment(plating, memnite)

        assert memnite.power >= 4, (
            f"setup precondition: equipped Memnite power={memnite.power}, "
            f"expected ≥4 so removing the carrier is clearly the larger "
            f"position swing than removing the equipment alone."
        )

        opp = game.players[1]
        memnite_score = _threat_score(memnite, game, opp)
        plating_score = _threat_score(plating, game, opp)

        assert memnite_score >= plating_score, (
            f"Wear // Tear / Force of Vigor / Solitude target picker "
            f"ranked the bare equipment above its carrier: "
            f"Memnite={memnite_score:.3f} vs Plating={plating_score:.3f}. "
            f"Killing the carrier strictly dominates killing the "
            f"equipment alone (the equipment falls off on host death). "
            f"Both candidates must be scored on the same scale "
            f"(`permanent_threat`); mixing `creature_threat_value` "
            f"(clock units) with `permanent_threat` (position-value "
            f"delta) makes the cross-type comparison a category error."
        )

    def test_threat_score_uses_marginal_contribution_for_creatures(
            self, card_db):
        """Independent invariant: for any creature artifact on the
        battlefield, `_threat_score(creature, game, owner)` must equal
        `permanent_threat(creature, owner, game)`. The picker's
        creature branch must NOT short-circuit to a different unit
        system (`creature_threat_value` was the bug shape) when game
        + owner context is available."""
        from ai.permanent_threat import permanent_threat
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)

        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _attach_equipment(plating, ornithopter)

        opp = game.players[1]
        score = _threat_score(ornithopter, game, opp)
        marginal = permanent_threat(ornithopter, opp, game)
        assert score == pytest.approx(marginal, abs=1e-6), (
            f"_threat_score returned {score:.4f} but the marginal "
            f"contribution formula gives {marginal:.4f}.  When game "
            f"context is available, `_threat_score` must delegate to "
            f"`permanent_threat` for creatures and non-creatures alike "
            f"so cross-type comparisons (creature artifact vs mana "
            f"rock) are unit-consistent."
        )
