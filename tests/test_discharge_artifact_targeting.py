"""Bug 4 — Galvanic Discharge targets face when removal target exists.

Evidence: replays/boros_vs_affinity_bo3.txt:79-80 (G1 T1 Boros)
    T1 P1: Cast Galvanic Discharge (R)
    [Target] → face (3 dmg): no clock yet — build pressure
    T1 P1: Galvanic Discharge deals 3 to opponent

Affinity has Ornithopter (0/2) on board. The AI correctly notes
"no clock yet — build pressure" but fails to recognise that
Ornithopter is a strategic piece of an artifact-synergy engine:
killing it denies Mox Opal metalcraft, a future Cranial Plating
target, and one point of affinity cost-reduction across the
opposing deck.

The fix must be oracle-driven (no card names). Signal: when any
opposing permanent carries text that *scales with artifact count*
(Cranial Plating / Nettlecyst "for each artifact", metalcraft,
affinity-for-artifacts cost reducers), any opposing artifact
creature is a strategic target — its removal denies scaling.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
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
    card.summoning_sick = False  # already resolved on a prior turn
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


class TestGalvanicDischargeArtifactTargeting:
    """When opp board shows artifact-synergy signal, Discharge picks
    the opposing artifact creature over face even at full life."""

    def _setup(self, card_db):
        """T1 Boros: Sacred Foundry in play, Discharge in hand. Opp
        (Affinity) has Ornithopter + Cranial Plating on the board."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        # Cranial Plating's oracle scales equipped creature with artifact
        # count — the textual signal we want the targeting heuristic to
        # pick up.
        _add_to_battlefield(game, card_db, "Cranial Plating", controller=1)

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        return game, player, discharge, ornithopter

    def test_discharge_targets_ornithopter_when_opp_has_artifact_synergy(
            self, card_db):
        game, player, discharge, ornithopter = self._setup(card_db)
        targets = player._choose_targets(game, discharge)
        assert targets == [ornithopter.instance_id], (
            f"Discharge targeted {targets} instead of Ornithopter "
            f"(instance_id={ornithopter.instance_id}). Opp's Cranial "
            f"Plating signals artifact synergy; removing their "
            f"Ornithopter denies a future Plating target and a "
            f"metalcraft count. Face damage is worth less than "
            f"denying those synergies."
        )

    def test_discharge_targets_face_when_opp_has_no_artifact_synergy(
            self, card_db):
        """Regression anchor — the fix must NOT break the existing
        'go face when there's no strategic reason to aim at a weak
        body' behaviour. With no synergy signal on opp's board, a
        0/2 Ornithopter is a bad target."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        _add_to_battlefield(game, card_db, "Ornithopter", controller=1)
        # No Cranial Plating, no other artifact-synergy permanents.

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        targets = player._choose_targets(game, discharge)
        assert targets == [-1], (
            f"Without an artifact-synergy signal on opp's board, "
            f"Discharge should still prefer face over a 0/2 body. "
            f"Got targets={targets}."
        )
