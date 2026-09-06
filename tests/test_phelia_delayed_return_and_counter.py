"""A "blink until the next end step" attack trigger returns the exiled
permanent, and its self-counter lands at the return, not on attack.

Phelia, Exuberant Shepherd: "Whenever Phelia attacks, exile up to one
other target nonland permanent. At the beginning of the next end step,
return that card to the battlefield under its owner's control. If it
entered under your control, put a +1/+1 counter on Phelia."

Two defects (Jeskai Blink vs 4c Omnath audit, seed 57009):
  A. The end-step return never fired: the game-runner gate that
     dispatches the return required the literal word "exiled" in the
     oracle, but Phelia's text says "return that card" — so every
     Phelia exile was PERMANENT (silent hard removal), not a blink.
  B. The +1/+1 counter was applied immediately in the attack handler
     instead of at the next end step when the card returns — Phelia
     attacked as a 4/4 a full turn before she legally grows.

Rules-phrased; Phelia is the fixture carrier for the delayed-return /
delayed-self-counter trigger shape.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from engine.game_state import GameState

_DB = CardDatabase()


def _bf(game, name, controller):
    tmpl = _DB.get_card(name)
    assert tmpl is not None, f"missing {name}"
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    if tmpl.is_creature:
        game.players[controller].creatures.append(c)
    return c


def _phelia_attacks(game, phelia, controller):
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    EFFECT_REGISTRY.execute("Phelia, Exuberant Shepherd", EffectTiming.ATTACK,
                            game, phelia, controller)


def test_delayed_return_actually_returns_the_exiled_permanent():
    """After Phelia exiles an opponent's permanent on attack, the
    end-step return must bring it back — it must not be stranded in
    exile because the dispatch gate demanded the word 'exiled'."""
    game = GameState(rng=random.Random(0))
    phelia = _bf(game, "Phelia, Exuberant Shepherd", 0)
    victim = _bf(game, "Griselbrand", 1)  # opponent's nonland permanent

    _phelia_attacks(game, phelia, 0)
    assert victim in game.players[1].exile, "attack should exile the target"

    # The attacker's end step processes the scheduled return.
    runner = GameRunner(_DB, rng=random.Random(0))
    runner._process_end_step_returns(game, 0)

    assert victim in game.players[1].battlefield, (
        "the exiled permanent must return to its owner's battlefield at "
        "the next end step — a Phelia exile is a blink, not removal"
    )
    assert victim not in game.players[1].exile


def test_self_counter_lands_at_return_not_on_attack():
    """Exiling her controller's own permanent, Phelia's +1/+1 is applied
    when the card returns (end step), not immediately on attack."""
    game = GameState(rng=random.Random(0))
    phelia = _bf(game, "Phelia, Exuberant Shepherd", 0)
    # An own ETB-value creature is the permanent Phelia blinks for value.
    own = _bf(game, "Wall of Omens", 0)

    base_power = phelia.power
    _phelia_attacks(game, phelia, 0)

    assert phelia.power == base_power, (
        f"Phelia grew to {phelia.power} ON ATTACK — the +1/+1 must wait "
        f"for the end-step return (she should still be {base_power})"
    )

    runner = GameRunner(_DB, rng=random.Random(0))
    runner._process_end_step_returns(game, 0)

    assert phelia.power == base_power + 1, (
        f"after her own permanent returns under her control, Phelia must "
        f"have a +1/+1 counter (power {base_power + 1}), got {phelia.power}"
    )


def test_returning_opponents_permanent_gives_no_counter():
    """Regression: the +1/+1 only triggers when the returned card enters
    under the controller's control — exiling an OPPONENT's permanent
    grows nothing."""
    game = GameState(rng=random.Random(0))
    phelia = _bf(game, "Phelia, Exuberant Shepherd", 0)
    _bf(game, "Griselbrand", 1)  # opponent's — only legal target

    base_power = phelia.power
    _phelia_attacks(game, phelia, 0)
    runner = GameRunner(_DB, rng=random.Random(0))
    runner._process_end_step_returns(game, 0)

    assert phelia.power == base_power, (
        f"exiling an opponent's permanent must not grow Phelia; power "
        f"is {phelia.power}, expected {base_power}"
    )
