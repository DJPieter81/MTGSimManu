"""A ritual with no chain begun is cast only for a dig its mana pays
(AI, from CR 106.4 and 500.4).

A ritual's mana empties at the end of the phase. With no chain begun this
turn (storm 0) and no closer in reach, the ritual's only gift is that
mana: the storm it adds closes nothing. So it is worth casting for a dig
only when the dig needs it, that is, when the ritual lets the AI pay for
more of the digs it will make than the mana it has without the ritual.
- A dig the lands already pay is cast from the lands. The ritual is kept
  for a turn its storm and mana can kill on.
- Mid-chain the ritual adds to a chain in progress as well, so that gate
  is unchanged (unit RG0).
- A ritual that digs itself (its own draw, or a drawing cast trigger) is
  its own dig, as before.

The defect (the cc-post replay: Ruby Storm vs 4c Omnath, seed 50500, game
1, turn 3): three lands paid for Ral and then Reckless Impulse at {R}.
Storm cast Desperate Ritual first anyway. Its extra mana went unused, and
the kill turn lost a spell of storm and its mana. The gate asked only
whether the ritual's mana reached a dig (`_digs_the_ai_will_make`), never
whether the mana without it already did.

Card names are fixture carriers. The class is every ritual (the typed
`ritual_mana` field) the storm-zone assessment gates.
"""
from __future__ import annotations

import random

import pytest

from ai.combo_calc import (STORM_HARD_HOLD, ComboAssessment,
                           _build_role_cache, card_combo_modifier)
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

DECK = "Ruby Storm"


def _add(game, card_db, name, zone, controller=0):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _main_phase(card_db, *, hand, storm, lands, opp_life=20):
    g = GameState(rng=random.Random(0))
    for _ in range(lands):
        _add(g, card_db, "Mountain", "battlefield")
    cards = [_add(g, card_db, n, "hand") for n in hand]
    for _ in range(20):
        _add(g, card_db, "Mountain", "library")
    g.players[0].deck_name = DECK
    g.players[1].deck_name = "4c Omnath"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
    g.players[0].lands_played_this_turn = 1
    g.players[0].spells_cast_this_turn = storm
    g._global_storm_count = storm
    g.players[1].life = opp_life
    return g, cards


def _modifier(game, card):
    player = EVPlayer(player_idx=0, deck_name=DECK, rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    a = ComboAssessment(resource_zone="storm", has_payoff=False,
                        combo_value=80.0,          # any kill value
                        payoff_names={"Grapeshot", "Empty the Warrens"},
                        _role_cache=_build_role_cache(player.goal_engine))
    return card_combo_modifier(card, a, snap, game.players[0], game, 0)


def test_with_no_chain_begun_a_ritual_whose_dig_the_lands_pay_is_held(
        card_db):
    """Two lands pay for Reckless Impulse (2) without Pyretic Ritual."""
    g, (ritual, _dig) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Reckless Impulse"], storm=0,
        lands=2)
    assert _modifier(g, ritual) == STORM_HARD_HOLD


def test_the_replays_turn_three_keeps_its_ritual(card_db):
    """Seed 50500's turn 3: three lands pay for Ral (2) and, after it,
    Reckless Impulse at {R}. Desperate Ritual adds nothing they need."""
    g, (ritual, _ral, _dig) = _main_phase(
        card_db, hand=["Desperate Ritual",
                       "Ral, Monsoon Mage // Ral, Leyline Prodigy",
                       "Reckless Impulse"], storm=0, lands=3)
    assert _modifier(g, ritual) == STORM_HARD_HOLD


def test_with_no_chain_begun_a_ritual_is_admitted_for_a_dig_only_its_mana_pays(
        card_db):
    """Three lands cannot pay for Big Score (4). Pyretic Ritual leaves
    four mana, so it funds the dig."""
    g, (ritual, _dig) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Big Score"], storm=0, lands=3)
    assert STORM_HARD_HOLD < _modifier(g, ritual) < 0


def test_a_ritual_that_pays_for_one_more_dig_is_admitted(card_db):
    """Three lands pay for one of Reckless Impulse and Wrenn's Resolve (2
    each). After Pyretic Ritual, four mana pays for both."""
    g, (ritual, _a, _b) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Reckless Impulse",
                       "Wrenn's Resolve"], storm=0, lands=3)
    assert STORM_HARD_HOLD < _modifier(g, ritual) < 0


@pytest.mark.parametrize("storm", [1, 3])
def test_mid_chain_a_ritual_feeds_the_chain_whatever_the_lands_pay(
        card_db, storm):
    """Mid-chain the ritual's storm adds to the chain a dig may still close
    (unit RG0's gate, unchanged): admitted although the lands pay for the
    dig."""
    g, (ritual, _dig) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Reckless Impulse"], storm=storm,
        lands=2)
    assert STORM_HARD_HOLD < _modifier(g, ritual) < 0
