"""Regression: a damage spell resolving onto a creature/planeswalker
target must route through the `deal_damage` primitive with a valid
signature.

Rule-phrased: "spell-damage resolution deals N damage to a permanent
target". This pins the call boundary between the registered
SPELL_RESOLVE handlers (and the generic damage-ability resolver in
`spell_resolution.py`) and the `deal_damage(source, target, amount)`
primitive in `engine/damage.py`.

Failing-test-first protocol: before the fix, the handlers call
`deal_damage(game, target, N, source_controller=controller)` — `game`
is not the damage source and `source_controller` is not a parameter of
`deal_damage`, so any burn spell aimed at a creature raises
`TypeError` and crashes the game mid-resolution. The bug never
surfaced in CI because full-game integration tests were not run.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.game_state import GameState


def _put_creature_in_play(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def _make_spell(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card not in DB: {name}"
    return CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )


def test_burn_spell_damage_resolves_on_creature_target(card_db):
    """A 3-damage burn spell aimed at an opposing creature marks 3
    damage on that creature without raising — exercises the
    handler → deal_damage boundary."""
    game = GameState(rng=random.Random(0))
    target = _put_creature_in_play(game, card_db, "Tarmogoyf", 1)
    spell = _make_spell(game, card_db, "Lightning Bolt", 0)

    fired = EFFECT_REGISTRY.execute(
        "Lightning Bolt", EffectTiming.SPELL_RESOLVE,
        game, spell, 0, targets=[target.instance_id],
    )

    assert fired, "Lightning Bolt SPELL_RESOLVE handler did not fire"
    assert target.damage_marked == 3, (
        f"expected 3 damage marked on creature, got {target.damage_marked}"
    )


def test_burn_spell_damage_resolves_to_face(card_db):
    """tid == -1 routes to player damage without raising."""
    game = GameState(rng=random.Random(0))
    opp = game.players[1]
    life_before = opp.life
    spell = _make_spell(game, card_db, "Lightning Bolt", 0)

    fired = EFFECT_REGISTRY.execute(
        "Lightning Bolt", EffectTiming.SPELL_RESOLVE,
        game, spell, 0, targets=[-1],
    )

    assert fired
    assert opp.life == life_before - 3, (
        f"expected opponent life {life_before - 3}, got {opp.life}"
    )
