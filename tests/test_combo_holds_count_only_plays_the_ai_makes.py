"""A combo hold waits only for a play the AI will make.

A hold is the opportunity cost of a later play: the storm finisher
waits while chain fuel would grow the count, a tutor waits while fuel
would grow the chain it closes, and a mid-chain ritual is admitted (at a
soft penalty, not the hard hold) while a dig could still find the closer.
Each counts a later play, so each must count only a play the AI's own
main phase will make:
- a cast the same-turn filter defers (`ev_evaluator.cast_is_deferred`,
  the filter `decide_main_phase` applies) is never made;
- a dig the ritual is to fund must be affordable from the mana that
  ritual leaves (`ai.effective_cmc`), this turn, since ritual mana
  empties at the end of the phase (CR 500.4, 106.4);
- a dig is a spell that puts new cards in hand or play
  (`ev_evaluator._is_real_dig`, the typed `has_draw_effect` /
  `is_tutor`), never a classifier tag;
- a ritual that digs is its own dig.

The defect: Ruby Storm at storm 14 with 7 mana held Grapeshot (15
damage) and a castable Wish (a second Grapeshot) because each counted a
deferred card as fuel, and passed. Rituals were cast because a ritual
that draws remained as a dig, and that dig was then hard-held as a
ritual with no other dig: the mana emptied unused.

Card names are fixture carriers; roles come from the deck's gameplan.
"""
from __future__ import annotations

import random

import pytest

from ai.clock import scarce_payoff_commit_ev
from ai.combo_calc import (STORM_HARD_HOLD, ComboAssessment,
                           _build_role_cache, card_combo_modifier)
from ai.ev_evaluator import cast_is_deferred, snapshot_from_game
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


def _chain(card_db, *, hand, storm, lands, opp_life=19, graveyard=(),
           sideboard=(), flashback_granted=False):
    """Our main phase mid-chain: `storm` spells cast this turn, `lands`
    untapped Mountains, nothing on the opponent's board.
    `flashback_granted`: a Past in Flames already resolved this turn."""
    g = GameState(rng=random.Random(0))
    for _ in range(lands):
        _add(g, card_db, "Mountain", "battlefield")
    cards = [_add(g, card_db, n, "hand") for n in hand]
    for n in graveyard:
        _add(g, card_db, n, "graveyard")
    for n in sideboard:
        _add(g, card_db, n, "sideboard")
    for _ in range(20):
        _add(g, card_db, "Mountain", "library")
    g.players[0].deck_name = DECK
    g.players[1].deck_name = "Dimir Midrange"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 7
    g.players[0].lands_played_this_turn = 1
    g.players[0].spells_cast_this_turn = storm
    g._global_storm_count = storm
    g.players[1].life = opp_life
    g.players[0].flashback_granted_this_turn = flashback_granted
    return g, cards


def _modifier(game, card):
    """The combo modifier with the deck's own roles (its gameplan)."""
    player = EVPlayer(player_idx=0, deck_name=DECK, rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    a = ComboAssessment(resource_zone="storm", has_payoff=False,
                        combo_value=80.0,          # any kill value
                        payoff_names={"Grapeshot", "Empty the Warrens"},
                        _role_cache=_build_role_cache(player.goal_engine))
    me = game.players[0]
    return card_combo_modifier(card, a, snap, me, game, 0), a, snap


# ── Finisher and tutor holds ──────────────────────────────────────

def test_a_finisher_hold_ignores_fuel_the_ai_defers(card_db):
    """A second Past in Flames after one resolved this turn grants nothing
    new: the main phase defers it, so it is no fuel, and the finisher's
    value is the commit-now comparison with nothing left to grow
    (storm >= 1)."""
    g, (grapeshot, pif) = _chain(card_db, hand=["Grapeshot", "Past in Flames"],
                                 storm=14, lands=7, flashback_granted=True)
    snap = snapshot_from_game(g, 0)
    assert cast_is_deferred(pif, snap, g, 0)
    mod, a, snap = _modifier(g, grapeshot)
    assert mod == pytest.approx(scarce_payoff_commit_ev(
        snap.storm_count + 1, snap.opp_life, a.combo_value, 0.0))
    assert mod > 0


def test_a_finisher_still_waits_for_fuel_the_ai_casts(card_db):
    g, (grapeshot, ritual) = _chain(card_db,
                                    hand=["Grapeshot", "Pyretic Ritual"],
                                    storm=14, lands=7)
    assert not cast_is_deferred(ritual, snapshot_from_game(g, 0), g, 0)
    mod, a, snap = _modifier(g, grapeshot)
    assert mod == pytest.approx(-1 / snap.opp_life * a.combo_value)


def test_a_tutor_hold_ignores_fuel_the_ai_defers(card_db):
    g, (wish, pif) = _chain(card_db, hand=["Wish", "Past in Flames"],
                            storm=14, lands=7, sideboard=["Grapeshot"],
                            flashback_granted=True)
    assert cast_is_deferred(pif, snapshot_from_game(g, 0), g, 0)
    mod, a, snap = _modifier(g, wish)
    assert mod == pytest.approx(scarce_payoff_commit_ev(
        snap.storm_count + 2, snap.opp_life, a.combo_value, 0.0))
    assert mod > 0


# ── The mid-chain ritual gate ─────────────────────────────────────

def test_a_ritual_that_draws_is_its_own_dig(card_db):
    """No finisher in reach: the gate admits a ritual on a dig only if it
    lets that dig through too -- a ritual that draws is the dig."""
    g, (morph, ritual, _r2) = _chain(
        card_db, hand=["Manamorphose", "Pyretic Ritual", "Pyretic Ritual"],
        storm=2, lands=2, opp_life=20)
    morph_mod, _, _ = _modifier(g, morph)
    ritual_mod, _, _ = _modifier(g, ritual)
    assert ritual_mod != STORM_HARD_HOLD
    assert morph_mod != STORM_HARD_HOLD


def test_the_ritual_gate_counts_only_spells_that_draw(card_db):
    """Past in Flames grants flashback; it draws nothing, so it is no dig
    (and with an empty graveyard no flashback line either)."""
    g, (ritual, _pif) = _chain(card_db,
                               hand=["Pyretic Ritual", "Past in Flames"],
                               storm=2, lands=2, opp_life=20)
    mod, _, _ = _modifier(g, ritual)
    assert mod == STORM_HARD_HOLD


def test_the_ritual_gate_counts_a_dig_only_when_this_rituals_mana_pays_for_it(
        card_db):
    """Big Score costs 4. Two lands, a 2-mana ritual adding 3: 3 mana
    after it, so no dig this turn. A third land pays for it."""
    g, (ritual, _dig) = _chain(card_db, hand=["Pyretic Ritual", "Big Score"],
                               storm=2, lands=2, opp_life=20)
    assert _modifier(g, ritual)[0] == STORM_HARD_HOLD
    g, (ritual, _dig) = _chain(card_db, hand=["Pyretic Ritual", "Big Score"],
                               storm=2, lands=3, opp_life=20)
    assert _modifier(g, ritual)[0] != STORM_HARD_HOLD


# ── The decision ──────────────────────────────────────────────────

def test_a_chain_with_its_closer_in_reach_does_not_pass(card_db):
    """Storm 14 against 19 life, 7 mana: Grapeshot (15) and a Wish for
    the sideboard Grapeshot are in reach, with a second Past in Flames
    after one resolved this turn. The main phase closes; it does not
    pass."""
    g, (grapeshot, wish, _pif) = _chain(
        card_db, hand=["Grapeshot", "Wish", "Past in Flames"], storm=14,
        lands=7, sideboard=["Grapeshot"], flashback_granted=True)
    player = EVPlayer(player_idx=0, deck_name=DECK, rng=random.Random(0))
    decision = player.decide_main_phase(g)
    assert decision is not None
    assert decision[1] in (grapeshot, wish)
