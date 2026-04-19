"""Target-fidelity invariant.

For every targeted spell or ability that resolves, the declared target
must actually receive the effect. A declared target cannot be silently
replaced by a different pick at resolution time.

This invariant was graduated from Bug 1 (Phlage ETB re-picked its damage
target via `_pick_damage_target` instead of honoring the AI's declared
target). Any regression where declared targets and resolved effects
disagree should be caught here.

Shape of a target-fidelity assertion:

    opp_life_before = game.players[1].life
    # Build Phlage stack item with explicit targets=[pest.instance_id].
    # Resolve.
    assert pest.damage_marked >= 3 or pest.zone == "graveyard"
    # Phlage always gains 3 life for controller; opp life must not drop
    # from damage being redirected to face.
    assert game.players[1].life == opp_life_before
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _mk_instance(game: GameState, template, owner: int, zone: str = "battlefield"):
    card = CardInstance(
        template=template,
        owner=owner,
        controller=owner,
        instance_id=game.next_instance_id(),
        zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        game.players[owner].battlefield.append(card)
    return card


class TestPhlageTargetFidelity:
    """Phlage's ETB damage must land on the declared target, not
    a re-picked one."""

    def _setup(self, card_db, opp_creature_name: str):
        game = GameState(rng=random.Random(0))
        phlage_tmpl = card_db.get_card("Phlage, Titan of Fire's Fury")
        opp_tmpl = card_db.get_card(opp_creature_name)
        assert phlage_tmpl is not None
        assert opp_tmpl is not None

        # Opponent has a single creature; we escape-cast Phlage targeting it.
        opp_creature = _mk_instance(game, opp_tmpl, owner=1)
        phlage = CardInstance(
            template=phlage_tmpl,
            owner=0,
            controller=0,
            instance_id=game.next_instance_id(),
            zone="stack",
        )
        phlage._game_state = game
        phlage._escaped = True  # so Phlage survives the ETB for a clean check

        game.stack.items.append(StackItem(
            item_type=StackItemType.SPELL,
            source=phlage,
            controller=0,
            targets=[opp_creature.instance_id],
        ))
        return game, phlage, opp_creature

    def test_phlage_damage_hits_declared_target_signal_pest(self, card_db):
        """Signal Pest (0/1, battle cry) should take the 3 damage and die."""
        game, phlage, pest = self._setup(card_db, "Signal Pest")
        opp_life_before = game.players[1].life

        game.resolve_stack()

        assert pest.zone == "graveyard" or pest.damage_marked >= 3, (
            f"Declared target Signal Pest was not hit: zone={pest.zone}, "
            f"damage_marked={pest.damage_marked}. "
            f"Engine silently re-picked the target."
        )
        assert game.players[1].life == opp_life_before, (
            f"Opponent life dropped {opp_life_before} → {game.players[1].life}: "
            f"Phlage damage was redirected to face instead of to the declared "
            f"creature target."
        )

    def test_phlage_damage_hits_declared_target_ornithopter(self, card_db):
        """Ornithopter (0/2) should take the 3 damage and die."""
        game, phlage, bird = self._setup(card_db, "Ornithopter")
        opp_life_before = game.players[1].life

        game.resolve_stack()

        assert bird.zone == "graveyard" or bird.damage_marked >= 3, (
            f"Declared target Ornithopter was not hit: zone={bird.zone}, "
            f"damage_marked={bird.damage_marked}."
        )
        assert game.players[1].life == opp_life_before, (
            f"Opponent life dropped {opp_life_before} → {game.players[1].life}: "
            f"Phlage damage was redirected to face instead of to the declared "
            f"creature target."
        )

    def test_phlage_controller_gains_life(self, card_db):
        """Regression check — Phlage's gain-3-life clause still fires."""
        game, _phlage, _pest = self._setup(card_db, "Signal Pest")
        my_life_before = game.players[0].life

        game.resolve_stack()

        assert game.players[0].life == my_life_before + 3
