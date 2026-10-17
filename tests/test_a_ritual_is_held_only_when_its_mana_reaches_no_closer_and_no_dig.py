"""A ritual is held only when its mana reaches neither a closer nor a dig
(AI, from CR 500.4 and 106.4).

Ritual mana empties at the end of the phase. So a ritual cast with no
finisher in reach is wasted unless the mana it leaves pays for a dig the AI
will make this turn: a spell that draws, or the ritual's own draw. With a
dig in reach the ritual is admitted at the soft penalty for a chain without
its closer. With no dig it is held. One rule, one owner
(`combo_calc._ritual_without_closer`), at every storm count.

A ritual is its own dig when its text draws, or when its cast triggers a
cast trigger the cast carrier performs that draws: a rummage on "whenever
you cast a noncreature spell" (unit CT).

The defect (docs/diagnostics/2026-10-13_ruby_storm_ritual_hold_root_cause.md):
the storm-0 gate hard-held every ritual when no finisher was in hand. It
never asked whether the ritual's mana reached a dig; the mid-chain gate
beside it did. Ruby Storm vs Living End s50000 G1 T5: five lands, Ruby
Medallion and Artist's Talent in play, two rituals and Valakut Awakening in
hand, both rituals at -990. Storm passed and died.

Card names are fixture carriers; roles come from the deck's gameplan.
"""
from __future__ import annotations

import random

import pytest

from ai.combo_calc import (STORM_HARD_HOLD, ComboAssessment,
                           _build_role_cache, card_combo_modifier)
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import COMBO_RITUAL_MISSED_FINISHER_SCALE
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

DECK = "Ruby Storm"
VALAKUT = "Valakut Awakening // Valakut Stoneforge"


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


def _main_phase(card_db, *, hand, storm, lands, in_play=(), opp_life=20):
    g = GameState(rng=random.Random(0))
    for _ in range(lands):
        _add(g, card_db, "Mountain", "battlefield")
    for n in in_play:
        _add(g, card_db, n, "battlefield")
    cards = [_add(g, card_db, n, "hand") for n in hand]
    for _ in range(20):
        _add(g, card_db, "Mountain", "library")
    g.players[0].deck_name = DECK
    g.players[1].deck_name = "Living End"
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


def _soft_penalty(storm, opp_life):
    """The mid-chain gate's penalty below half lethal: (storm + 2) / life."""
    return -((storm + 2) / opp_life * COMBO_RITUAL_MISSED_FINISHER_SCALE)


def test_at_storm_zero_a_ritual_whose_mana_reaches_a_dig_is_not_held(card_db):
    """Three lands: Pyretic Ritual (2) leaves 4 mana, enough for Big Score
    (4), which digs and which the lands alone cannot pay for. (A dig the
    lands already pay for funds no ritual at storm 0: unit RW,
    `tests/test_a_ritual_with_no_chain_begun_is_cast_only_for_a_dig_its_
    mana_pays.py`.)"""
    g, (ritual, _dig) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Big Score"], storm=0, lands=3)
    assert _modifier(g, ritual) == pytest.approx(_soft_penalty(0, 20))


def test_at_storm_zero_a_dig_the_rituals_mana_cannot_pay_for_is_no_dig(
        card_db):
    """Big Score costs 4: Pyretic Ritual on two lands leaves 3."""
    g, (ritual, _dig) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Big Score"], storm=0, lands=2)
    assert _modifier(g, ritual) == STORM_HARD_HOLD


def test_at_storm_zero_a_ritual_with_no_closer_and_no_dig_is_held(card_db):
    g, (ritual, _r2) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Pyretic Ritual"], storm=0, lands=2)
    assert _modifier(g, ritual) == STORM_HARD_HOLD


@pytest.mark.parametrize("storm", [0, 2])
def test_a_cast_trigger_that_draws_makes_the_ritual_its_own_dig(card_db,
                                                                storm):
    """Artist's Talent rummages whenever its controller casts a noncreature
    spell: the ritual digs as it is cast."""
    g, (ritual, _r2) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Pyretic Ritual"], storm=storm,
        lands=2, in_play=["Artist's Talent"])
    assert _modifier(g, ritual) == pytest.approx(_soft_penalty(storm, 20))


def test_the_dig_is_what_the_cast_carrier_performs(card_db):
    """A cast trigger whose head the ritual does not meet digs nothing:
    "whenever you cast a creature spell" (Hazoret's Monument)."""
    g, (ritual, _r2) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Pyretic Ritual"], storm=0,
        lands=2, in_play=["Hazoret's Monument"])
    assert _modifier(g, ritual) == STORM_HARD_HOLD


def test_the_living_end_turn_five_board_admits_its_rituals(card_db):
    """The replay's T5 (s50000 G1): five lands, Ruby Medallion and Artist's
    Talent in play, two rituals and Valakut Awakening in hand, no payoff."""
    g, (pyretic, desperate, _v) = _main_phase(
        card_db, hand=["Pyretic Ritual", "Desperate Ritual", VALAKUT],
        storm=0, lands=5, in_play=["Ruby Medallion", "Artist's Talent"],
        opp_life=19)
    assert _modifier(g, pyretic) != STORM_HARD_HOLD
    assert _modifier(g, desperate) != STORM_HARD_HOLD
