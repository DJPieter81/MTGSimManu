"""Target-fidelity invariant.

For every targeted spell or ability that resolves, the declared target
must actually receive the effect. A declared target cannot be silently
replaced by a different pick at resolution time.

This invariant was graduated from Bug 1 (Phlage ETB re-picked its damage
target via `_pick_damage_target` instead of honoring the AI's declared
target). Any regression where declared targets and resolved effects
disagree should be caught here.

Fixture note: Phlage was banned 2026-05-19 and removed from the card
database entirely (not merely flagged banned-but-present), so it can no
longer be looked up. The test fixture below is a synthetic card
registered with the SAME handler shape as Phlage's real ETB (deal 3
damage to the declared target via `_pick_damage_target`, gain 3 life)
— exercising the identical engine code path this invariant guards,
independent of any one real card's continued legality/DB presence.

Shape of a target-fidelity assertion:

    opp_life_before = game.players[1].life
    # Build the fixture creature's stack item with explicit
    # targets=[pest.instance_id]. Resolve.
    assert pest.damage_marked >= 3 or pest.zone == "graveyard"
    # The fixture always gains 3 life for controller; opp life must not
    # drop from damage being redirected to face.
    assert game.players[1].life == opp_life_before
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType

FIXTURE_NAME = "Test Fixture: ETB Damage-and-Lifegain Creature"


def _register_fixture_handler():
    """Same shape as the real Phlage ETB handler this invariant was
    graduated from: honour a declared target first, deal 3 damage,
    gain 3 life. Registered fresh per test via the autouse
    _restore_effect_registry fixture (conftest.py), so no cross-test
    leakage."""
    @EFFECT_REGISTRY.register(FIXTURE_NAME, EffectTiming.ETB,
                              description="test fixture: ETB deal 3 damage "
                                          "to declared target, gain 3 life")
    def _fixture_etb(game, card, controller, targets=None, item=None):
        from engine.oracle_resolver import _pick_damage_target
        opponent = 1 - controller
        target = None
        if targets:
            for tid in targets:
                if tid is None or tid < 0:
                    continue
                cand = game.get_card_by_id(tid)
                if cand is not None and cand.zone == "battlefield":
                    target = cand
                    break
        if target is None and not targets:
            target = _pick_damage_target(game, controller, 3)
        if target is not None:
            target.damage_marked = getattr(target, 'damage_marked', 0) + 3
            game.check_state_based_actions()
        else:
            game.players[opponent].life -= 3
        game.players[controller].life += 3


def _fixture_template():
    return CardTemplate(
        name=FIXTURE_NAME, card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1, red=1), supertypes=[], subtypes=[],
        power=3, toughness=3, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="When this creature enters, it deals 3 damage to any "
                    "target and you gain 3 life.",
        tags=set(),
    )


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
    """ETB damage-and-lifegain must land on the declared target, not
    a re-picked one."""

    def _setup(self, card_db, opp_creature_name: str):
        _register_fixture_handler()
        game = GameState(rng=random.Random(0))
        fixture_tmpl = _fixture_template()
        opp_tmpl = card_db.get_card(opp_creature_name)
        assert opp_tmpl is not None

        # Opponent has a single creature; we cast the fixture targeting it.
        opp_creature = _mk_instance(game, opp_tmpl, owner=1)
        fixture = CardInstance(
            template=fixture_tmpl,
            owner=0,
            controller=0,
            instance_id=game.next_instance_id(),
            zone="stack",
        )
        fixture._game_state = game

        game.stack.items.append(StackItem(
            item_type=StackItemType.SPELL,
            source=fixture,
            controller=0,
            targets=[opp_creature.instance_id],
        ))
        return game, fixture, opp_creature

    def test_phlage_damage_hits_declared_target_signal_pest(self, card_db):
        """Signal Pest (0/1, battle cry) should take the 3 damage and die."""
        game, fixture, pest = self._setup(card_db, "Signal Pest")
        opp_life_before = game.players[1].life

        game.resolve_stack()

        assert pest.zone == "graveyard" or pest.damage_marked >= 3, (
            f"Declared target Signal Pest was not hit: zone={pest.zone}, "
            f"damage_marked={pest.damage_marked}. "
            f"Engine silently re-picked the target."
        )
        assert game.players[1].life == opp_life_before, (
            f"Opponent life dropped {opp_life_before} → {game.players[1].life}: "
            f"damage was redirected to face instead of to the declared "
            f"creature target."
        )

    def test_phlage_damage_hits_declared_target_ornithopter(self, card_db):
        """Ornithopter (0/2) should take the 3 damage and die."""
        game, fixture, bird = self._setup(card_db, "Ornithopter")
        opp_life_before = game.players[1].life

        game.resolve_stack()

        assert bird.zone == "graveyard" or bird.damage_marked >= 3, (
            f"Declared target Ornithopter was not hit: zone={bird.zone}, "
            f"damage_marked={bird.damage_marked}."
        )
        assert game.players[1].life == opp_life_before, (
            f"Opponent life dropped {opp_life_before} → {game.players[1].life}: "
            f"damage was redirected to face instead of to the declared "
            f"creature target."
        )

    def test_phlage_controller_gains_life(self, card_db):
        """Regression check — the fixture's gain-3-life clause still fires."""
        game, fixture, _pest = self._setup(card_db, "Signal Pest")
        my_life_before = game.players[0].life

        game.resolve_stack()

        assert game.players[0].life == my_life_before + 3
