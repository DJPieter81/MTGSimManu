"""A chain is started on digs only when today reaches more than holding.

Decision principle (AI, from CR 106.4 and 500.4: a ritual's mana empties at
the end of the phase). With no chain begun and no closer in reach, a ritual
was admitted whenever its mana reached any dig (`_digs_the_ai_will_make`),
however unlikely the dig was to find the closer. Starting the chain spends
the ritual for the chance the digs find a route to the closer today. Holding
keeps it for next turn, when the same digs see one card more, unless the
opponent's discard strips a piece first (`ComboAssessment.strip_risk`, BHI's
discard belief times the documented hit rate). Both chances are
hypergeometric over the library's routes to the closer: storm payoffs and
the tutors that reach one. The chain starts when today's chance is at least
the held one, and never on digs that can reach no closer.

The defect (Bo3 replay Ruby Storm vs Dimir Midrange s50000, game 1, Storm's
turn 4): with no finisher in hand and 2 Wish in a 46-card library, Storm
cast two Desperate Rituals at -0.1, then flashed back Past in Flames, and
ended at storm 4 with nothing left. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from ai.combo_calc import (STORM_HARD_HOLD, ComboAssessment,
                           _build_role_cache, card_combo_modifier)
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

DECK = "Ruby Storm"


def _add(game, card_db, name, zone, controller=0):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _main_phase(card_db, *, routes, blanks, hand=("Pyretic Ritual",
                                                    "Big Score"), storm=0):
    """Three lands; Pyretic Ritual funds Big Score (a dig of two cards)."""
    g = GameState(rng=random.Random(0))
    for _ in range(3):
        _add(g, card_db, "Mountain", "battlefield")
    cards = [_add(g, card_db, n, "hand") for n in hand]
    for _ in range(routes):
        _add(g, card_db, "Grapeshot", "library")
    for _ in range(blanks):
        _add(g, card_db, "Mountain", "library")
    g.players[0].deck_name = DECK
    g.players[1].deck_name = "Dimir Midrange"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
    g.players[0].lands_played_this_turn = 1
    g.players[0].spells_cast_this_turn = storm
    g._global_storm_count = storm
    return g, cards


def _modifier(game, card, strip_risk=0.0):
    player = EVPlayer(player_idx=0, deck_name=DECK, rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    a = ComboAssessment(resource_zone="storm", has_payoff=False,
                        combo_value=80.0,
                        payoff_names={"Grapeshot", "Empty the Warrens"},
                        strip_risk=strip_risk,
                        _role_cache=_build_role_cache(player.goal_engine))
    return card_combo_modifier(card, a, snap, game.players[0], game, 0)


def test_digs_that_can_reach_no_closer_hold_the_ritual(card_db):
    g, (ritual, _dig) = _main_phase(card_db, routes=0, blanks=40)
    assert _modifier(g, ritual) == STORM_HARD_HOLD


def test_a_long_shot_today_holds_when_nothing_strips_the_hand(card_db):
    """2 routes in 46: a two-card dig finds one 8.5% of the time today, more
    after another draw. The replayed shape."""
    g, (ritual, _dig) = _main_phase(card_db, routes=2, blanks=44)
    assert _modifier(g, ritual) == STORM_HARD_HOLD


def test_a_hand_likely_to_be_stripped_starts_the_chain_today(card_db):
    g, (ritual, _dig) = _main_phase(card_db, routes=2, blanks=44)
    assert STORM_HARD_HOLD < _modifier(g, ritual, strip_risk=0.5) < 0


def test_digs_certain_to_find_a_closer_start_the_chain(card_db):
    """Every library card is a route: today is as good as it gets."""
    g, (ritual, _dig) = _main_phase(card_db, routes=10, blanks=0)
    assert STORM_HARD_HOLD < _modifier(g, ritual) < 0


def test_the_strip_risk_is_the_discard_belief_times_the_hit_rate():
    from ai.combo_calc import _DISCARD_HIT_RATE, _strip_risk

    class _Beliefs:
        p_discard = 0.4

    class _Tracker:
        _initialized = True
        beliefs = _Beliefs()

        def get_discard_probability(self):
            return self.beliefs.p_discard
    assert _strip_risk(_Tracker()) == 0.4 * _DISCARD_HIT_RATE
    assert _strip_risk(None) == 0.0


def test_with_no_later_turn_to_chain_on_the_long_shot_is_taken(card_db):
    """The opponent's board kills within two turns: holding is no option,
    so the replayed long shot starts the chain."""
    g, (ritual, _dig) = _main_phase(card_db, routes=2, blanks=44)
    g.players[0].life = 4
    _add(g, card_db, "Grizzly Bears", "battlefield", controller=1)
    assert snapshot_from_game(g, 0).opp_clock_discrete <= 2
    assert STORM_HARD_HOLD < _modifier(g, ritual) < 0


def test_a_spell_already_cast_this_turn_does_not_commit_the_chain(card_db):
    """The replay's turn: a Ruby Medallion was cast (and countered) first,
    so the storm count was 1. What is spent is spent either way; the next
    ritual is weighed on what it can still reach."""
    g, (ritual, _dig) = _main_phase(card_db, routes=2, blanks=44, storm=1)
    assert _modifier(g, ritual) == STORM_HARD_HOLD
