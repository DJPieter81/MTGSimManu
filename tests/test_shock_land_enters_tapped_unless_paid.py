"""A shock land enters tapped unless its controller pays the life.

Shock lands read "As this land enters, you may pay 2 life. If you
don't, it enters tapped." — so the DEFAULT (no payment) is tapped;
paying flips it untapped. The enters-tapped detector returned False
for this clause ("the default is untapped"), so every shock entered
UNTAPPED for free — free turn-one colored mana and no life paid
(audit: Izzet Prowess vs Amulet Titan, s59003 — Izzet stayed at 20
life with untapped shocks all game).

Rule: the shock clause enters tapped by default; the untap_life_cost
machinery flips it untapped only when the payment is made. Class: all
10 Ravnica shock lands. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase, OracleTextParser
from engine.cards import CardInstance
from engine.game_state import GameState

_DB = CardDatabase()

_SHOCKS = ["Steam Vents", "Sacred Foundry", "Watery Grave", "Blood Crypt",
           "Breeding Pool", "Godless Shrine", "Hallowed Fountain",
           "Overgrown Tomb", "Stomping Ground", "Temple Garden"]


def test_detect_enters_tapped_true_for_shock_clause():
    oracle = ("({T}: Add {U} or {R}.)\n"
              "As this land enters, you may pay 2 life. If you don't, it "
              "enters tapped.")
    assert OracleTextParser.detect_enters_tapped(oracle) is True


@pytest.mark.parametrize("name", _SHOCKS)
def test_every_shock_is_flagged_enters_tapped_with_untap_cost(name):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    assert t.enters_tapped is True, f"{name} must default to entering tapped"
    assert t.untap_life_cost == 2, f"{name} must carry the 2-life untap cost"


def test_shock_enters_tapped_by_default_on_the_battlefield():
    """enter_battlefield() taps a shock (the untap_life_cost path flips
    it untapped only when the life is actually paid)."""
    game = GameState(rng=random.Random(0))
    t = _DB.get_card("Steam Vents")
    card = CardInstance(template=t, owner=0, controller=0,
                        instance_id=game.next_instance_id(),
                        zone="battlefield")
    card._game_state = game
    card.enter_battlefield()
    assert card.tapped is True, (
        "a shock land enters tapped by default — only the life payment "
        "untaps it"
    )
