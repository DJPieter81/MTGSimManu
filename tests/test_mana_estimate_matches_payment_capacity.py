"""The AI's mana estimate must agree with what the engine can actually pay.

Root cause (Eldrazi ramp mana audit, 2026-08-25): two different answers to
"how much mana do I have" coexisted, and the AI read the wrong one.

  * `available_mana_estimate` = len(untapped_lands) + conditional bonus —
    counts LANDS ONLY, one mana each. This feeds `EVSnapshot.my_mana` (the
    whole clock/combo/mana-waste layer) and `GoalEngine`'s resource gate.
  * `untapped_mana_capacity()` = the real per-source unit sum, which is what
    the CASTING path checks.

So a board holding mana rocks, or a land that taps for two, or an
Aura-enchanted land, reported less mana to the AI than the engine would
actually let it spend. The deck's own goal gate therefore refused to advance
to "deploy the payoff" several turns after the payoff was genuinely castable.

Rule under test: the AI-facing estimate never under-reports what the payment
path can produce. Mechanic-driven (mana sources), no card names asserted.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 5
    game.players[0].deck_name = "Eldrazi Ramp"
    game.players[1].deck_name = "Dimir Midrange"
    return game


def test_estimate_counts_a_mana_rock():
    """A mana rock is spendable mana; the estimate must see it."""
    game = _game()
    p = game.players[0]
    for _ in range(3):
        _add(game, "Forest")
    lands_only = p.available_mana_estimate
    _add(game, "Talisman of Impulse")
    assert p.available_mana_estimate > lands_only, (
        "adding a mana rock must raise the AI's mana estimate — the engine "
        "will happily spend it")


def test_estimate_counts_a_double_mana_land_twice():
    """A land that taps for two units is two mana, not one."""
    game = _game()
    p = game.players[0]
    _add(game, "Eldrazi Temple")
    assert p.available_mana_estimate >= p.untapped_mana_capacity(), (
        "a land producing two units must not be counted as one")


def test_estimate_never_under_reports_payment_capacity():
    """The invariant: estimate >= what the payment path can produce."""
    game = _game()
    p = game.players[0]
    for nm in ("Forest", "Forest", "Eldrazi Temple", "Talisman of Impulse"):
        _add(game, nm)
    assert p.available_mana_estimate >= p.untapped_mana_capacity(), (
        f"the AI-facing estimate ({p.available_mana_estimate}) must not be "
        f"below the engine's real capacity ({p.untapped_mana_capacity()}); "
        f"under-reporting makes the AI refuse plays it could afford")
