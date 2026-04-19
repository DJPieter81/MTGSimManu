"""Bug 4 — Galvanic Discharge threat-targets artifact-synergy creatures.

Under the marginal-contribution formulation, a creature's threat is
the drop in opponent's position value that removing it causes:

    threat(P) = V_opp(B) - V_opp(B \\ {P})

When Cranial Plating is equipped to an Ornithopter in an artifact-
dense board, Ornithopter's effective power scales with opp's artifact
count.  Removing it strips the entire equipped power bonus.  The
marginal formula captures this naturally — no per-synergy bolt-on is
required.

Regression: on an isolated board with no scaling in play (unattached
Plating, no artifact amplifiers), Ornithopter's marginal contribution
is tiny and Discharge still goes face.
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


def _attach_equipment(equipment, creature):
    """Attach `equipment` to `creature` via instance-tag convention."""
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")


class TestGalvanicDischargeArtifactTargeting:
    """When the opponent's board has scaling synergies currently in
    play, Discharge picks the creature whose removal strips the most
    position value.  Otherwise it goes face."""

    def test_discharge_targets_scaling_creature_when_plating_is_equipped(
            self, card_db):
        """Plating equipped to Ornithopter in a multi-artifact board:
        Ornithopter's effective power scales with opp's artifact count.
        Removing it strips every point of that bonus + the body."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)

        ornithopter = _add_to_battlefield(game, card_db, "Ornithopter",
                                           controller=1)
        plating = _add_to_battlefield(game, card_db, "Cranial Plating",
                                       controller=1)
        # Pad opp's artifact count so the scaling clause is meaningful.
        _add_to_battlefield(game, card_db, "Memnite", controller=1)
        _add_to_battlefield(game, card_db, "Springleaf Drum", controller=1)
        _add_to_battlefield(game, card_db, "Springleaf Drum", controller=1)

        _attach_equipment(plating, ornithopter)

        # Sanity: Ornithopter is now a multi-power threat thanks to
        # the equipped Plating scaling with artifact count.
        assert ornithopter.power >= 4, (
            f"test setup wrong: Ornithopter power={ornithopter.power}, "
            f"expected ≥4 after Plating + artifacts"
        )

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        targets = player._choose_targets(game, discharge)
        assert targets == [ornithopter.instance_id], (
            f"Discharge targeted {targets} instead of Ornithopter "
            f"(instance_id={ornithopter.instance_id}). The marginal "
            f"formula should rank Ornithopter highly because removing "
            f"it strips Plating's +N power bonus + the body. "
            f"Ornithopter effective power was {ornithopter.power}."
        )

    def test_discharge_targets_face_when_opp_has_no_scaling_on_board(
            self, card_db):
        """Regression anchor — with no synergy currently in play, a
        0/2 Ornithopter is a low-threat target and face damage is
        worth more."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
        discharge = _add_to_hand(game, card_db, "Galvanic Discharge",
                                  controller=0)
        _add_to_battlefield(game, card_db, "Ornithopter", controller=1)
        # No Plating, no scaling creatures, no metalcraft activation.

        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        targets = player._choose_targets(game, discharge)
        assert targets == [-1], (
            f"Without a scaling synergy on opp's board, a 0/2 body is "
            f"a worse target than face.  Got targets={targets}."
        )
