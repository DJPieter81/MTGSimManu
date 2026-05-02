"""Spot-removal timing rule — derived from a pro-annotated misplay
(Affinity vs Boros, seed 60100, G1 T1):

    "Spot removal value depends on the best target available across
     the next 2 turns, not just the current best target."

When a 1-mana removal spell could hit a low-threat creature now (e.g.
a vanilla 1/1) but the opponent's deck profile predicts a much higher-
EV target arriving within 2 turns (an X/X Construct token, an equipped
carrier, a 4/4 effective body), the removal should be DEFERRED.  This
is a generic mechanism — no card names hardcoded.

Class size: every cheap removal in Modern × every opponent deck with
escalating threats (>50 cards × ~10 archetypes).

Subsystem boundary:
  * `ai/bhi.py` owns the future-threat probability primitive.
  * `ai/ev_player.py::_score_spell` consumes it as a deferral term on
    the removal-scoring branch — adds to existing logic, doesn't replace.

Failing-test spec:
  test_spot_removal_deferred_when_bhi_predicts_higher_target_within_2_turns
    Boros (Galvanic Discharge in hand) sees an Affinity board with only
    a Memnite (1/1, low threat) on the battlefield.  Affinity's library
    contains Cranial Plating, a Construct Token producer (Saga), and
    Sojourner's Companion — all higher-EV targets that arrive within 2
    turns.  The removal-deferral term must reduce the Discharge cast EV
    so it does NOT outrank a non-removal alternative (or, equivalently,
    the deferral must reduce removal EV by a margin > 0).

  test_spot_removal_fires_when_target_is_only_threat_in_window
    Regression: when the opponent's library contains NO higher-EV
    threats, the deferral term must be ~0 and removal scoring is
    unchanged.

  test_bhi_p_higher_threat_decreases_with_higher_current_target
    The BHI primitive itself: as `current_target_value` rises toward
    the top of the opp's deck profile, `p_higher_threat_in_n_turns`
    must decrease monotonically.
"""
from __future__ import annotations

import random

import pytest

from ai.bhi import BayesianHandTracker
from ai.ev_evaluator import creature_threat_value, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _add_to_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
    return card


# Affinity library samples.  The point of these fixtures is to expose
# the "much higher threat than Memnite" cards in the opp's pool so the
# BHI density estimate fires.  Names match the published Affinity
# decklist (decks/modern_meta.py).
AFFINITY_HIGH_THREAT_LIB = [
    # Equipment + the artifact-count enablers it scales on
    "Cranial Plating", "Cranial Plating", "Cranial Plating",
    "Nettlecyst", "Nettlecyst",
    # Construct token producer (Urza's Saga makes large Constructs)
    "Urza's Saga", "Urza's Saga",
    # Big bodies
    "Sojourner's Companion", "Sojourner's Companion",
    "Thought Monitor",
    # Filler — small artifacts and lands so density math is realistic
    "Springleaf Drum", "Springleaf Drum",
    "Mox Opal",
    "Darksteel Citadel", "Darksteel Citadel",
    "Tanglepool Bridge", "Razortide Bridge",
    "Mistvault Bridge", "Silverbluff Bridge",
    "Treasure Vault",
]

# A "no higher threat" library — every non-land card is at-or-below
# the threat value of the current target (Signal Pest, threat ≈ 2.15
# with battle cry virtual power).  Used as the regression anchor: the
# deferral signal must be ~0 against this profile because every
# arrival is dominated by the target already in front of us.
LOW_THREAT_LIB = [
    "Memnite", "Memnite", "Memnite",
    "Ornithopter", "Ornithopter", "Ornithopter",
    "Memnite", "Memnite",
    "Darksteel Citadel", "Darksteel Citadel",
    "Mistvault Bridge", "Razortide Bridge",
    "Tanglepool Bridge", "Silverbluff Bridge",
    "Spire of Industry", "Spire of Industry",
    "Mountain", "Plains",
]


def _make_game_for_boros_vs_affinity_t1(card_db, opp_lib_names):
    """Build a minimal GameState mirroring seed 60100 G1 T1 P2:

      P0 (Boros, on the draw): one Sacred Foundry untapped, Galvanic
      Discharge in hand.

      P1 (Affinity, played first): Memnite + Signal Pest on board, plus
      whatever opp-library composition the test wants (used by BHI to
      assess "higher threat available within 2 turns").
    """
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Boros Energy"
    game.players[1].deck_name = "Affinity"

    # Boros side (P0, the player making the decision).  One untapped
    # red-source land — exactly enough mana to cast Galvanic Discharge.
    land = _add_to_battlefield(game, card_db, "Sacred Foundry", controller=0)
    land.tapped = False
    _add_to_hand(game, card_db, "Galvanic Discharge", controller=0)
    # A second card in hand so "no candidate" doesn't trigger an early
    # return path; we want the AI to actually rank Discharge vs. pass.
    _add_to_hand(game, card_db, "Phlage, Titan of Fire's Fury", controller=0)

    # Affinity side (P1).  Memnite is a vanilla 1/1 — the pro-annotated
    # misplay target.  Signal Pest is a 0/1 with battle cry — useful as
    # a second low-threat option.
    _add_to_battlefield(game, card_db, "Memnite", controller=1)
    _add_to_battlefield(game, card_db, "Signal Pest", controller=1)

    # Opp library composition feeds BHI's prior.
    for n in opp_lib_names:
        _add_to_library(game, card_db, n, controller=1)
    # Ensure Affinity has at least an empty hand so BHI hand_size=0
    # logic doesn't suppress the prior. The misplay is on T1, opp has
    # cards in hand — model with a couple of unknown cards.
    _add_to_hand(game, card_db, "Frogmite", controller=1)
    _add_to_hand(game, card_db, "Sojourner's Companion", controller=1)

    return game


def _galvanic_discharge_score(game, player: EVPlayer):
    """Score Galvanic Discharge under the current snapshot. Returns the
    raw EV used by `decide_main_phase` to rank the cast."""
    me = game.players[0]
    opp = game.players[1]
    snap = snapshot_from_game(game, 0)
    # Locate the Discharge in our hand
    discharge = next(c for c in me.hand
                     if c.template.name == "Galvanic Discharge")
    # Initialise BHI from current pools
    player.bhi.initialize_from_game(game)
    return player._score_spell(discharge, snap, game, me, opp)


class TestSpotRemovalDefersForHigherThreat:
    """The rule encoded by these tests is timing — when (not whether)
    to spend a piece of cheap removal.  Generalises across every cheap
    spot-removal × every opp deck with escalating threats."""

    def test_spot_removal_deferred_when_bhi_predicts_higher_target_within_2_turns(
            self, card_db):
        """Boros holds Galvanic Discharge.  Opp board has only a 1/1
        Memnite.  Opp library contains Cranial Plating, Saga, equipped
        carriers — much higher-EV targets within 2 turns.

        Assert: the EV of casting Discharge in this state must be
        STRICTLY LOWER than the EV in a parallel state where opp's
        library has no higher-threat cards.  The two states are
        identical except for opp library composition — so any EV gap
        is the deferral signal we are introducing.
        """
        # State A: opp has lots of higher-EV targets in library
        game_high = _make_game_for_boros_vs_affinity_t1(
            card_db, AFFINITY_HIGH_THREAT_LIB)
        player_high = EVPlayer(player_idx=0, deck_name="Boros Energy",
                               rng=random.Random(0))
        ev_high = _galvanic_discharge_score(game_high, player_high)

        # State B: opp's library has nothing bigger than Memnite
        game_low = _make_game_for_boros_vs_affinity_t1(
            card_db, LOW_THREAT_LIB)
        player_low = EVPlayer(player_idx=0, deck_name="Boros Energy",
                              rng=random.Random(0))
        ev_low = _galvanic_discharge_score(game_low, player_low)

        # The deferral signal must REDUCE removal EV against the high-
        # threat opp.  A strict inequality is required — equality would
        # mean BHI is ignored, which is the bug.
        assert ev_high < ev_low, (
            f"EV of casting Galvanic Discharge against an opp with "
            f"higher-EV future threats ({ev_high:.2f}) must be lower "
            f"than the EV against an opp with no higher-EV future "
            f"threats ({ev_low:.2f}).  The deferral signal "
            f"(BHI.p_higher_threat_in_n_turns) is not reducing the "
            f"removal score.  Pro-annotated misplay: seed 60100 G1 T1, "
            f"Boros burned 1-mana Discharge on a 1/1 Memnite while "
            f"Cranial Plating + Construct token sat in Affinity's deck."
        )

    def test_spot_removal_fires_when_target_is_only_threat_in_window(
            self, card_db):
        """Regression anchor.  When opp's library has NO cards more
        threatening than the current target, the deferral term must be
        approximately zero — removal scoring is unchanged.

        This is the symmetric guard against over-applying the deferral:
        if there's nothing better coming, kill the thing in front of you.
        """
        game_low = _make_game_for_boros_vs_affinity_t1(
            card_db, LOW_THREAT_LIB)
        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        # Initialise BHI and query the primitive directly.  When all of
        # opp's library is at-or-below the current target's threat
        # value, the probability of a higher-threat arrival should be
        # very small (≤ 0.10).
        player.bhi.initialize_from_game(game_low)
        # Pick the highest-threat creature on opp's board as the
        # "current target value" reference — this is what the removal-
        # decision path will use when ranking deferral.
        snap = snapshot_from_game(game_low, 0)
        opp_creatures = game_low.players[1].battlefield
        target_value = max(
            creature_threat_value(c, snap)
            for c in opp_creatures if c.template.is_creature)
        p_better = player.bhi.beliefs.p_higher_threat_in_n_turns(
            current_target_value=target_value, turns=2,
            opp_library=game_low.players[1].library,
            opp_hand_size=len(game_low.players[1].hand))
        assert 0.0 <= p_better <= 0.10, (
            f"Against a low-threat opp library (no scaling equipment, "
            f"no large creatures), p_higher_threat_in_n_turns must be "
            f"≤ 0.10 so the deferral term effectively vanishes.  Got "
            f"{p_better:.3f}."
        )

    def test_bhi_p_higher_threat_decreases_with_higher_current_target(
            self, card_db):
        """The primitive must be monotone: if I'm already considering
        a high-value target, the probability of an even higher one
        coming is lower than if I'm considering a low-value target.
        """
        game = _make_game_for_boros_vs_affinity_t1(
            card_db, AFFINITY_HIGH_THREAT_LIB)
        player = EVPlayer(player_idx=0, deck_name="Boros Energy",
                          rng=random.Random(0))
        player.bhi.initialize_from_game(game)
        opp_lib = game.players[1].library
        opp_hand_size = len(game.players[1].hand)

        # Three increasing target values spanning typical Modern
        # creature_threat_value range (Memnite ≈ 1, mid-creature ≈ 4,
        # premium threat ≈ 8).
        p_low = player.bhi.beliefs.p_higher_threat_in_n_turns(
            current_target_value=1.0, turns=2,
            opp_library=opp_lib, opp_hand_size=opp_hand_size)
        p_mid = player.bhi.beliefs.p_higher_threat_in_n_turns(
            current_target_value=4.0, turns=2,
            opp_library=opp_lib, opp_hand_size=opp_hand_size)
        p_high = player.bhi.beliefs.p_higher_threat_in_n_turns(
            current_target_value=8.0, turns=2,
            opp_library=opp_lib, opp_hand_size=opp_hand_size)

        assert p_low >= p_mid >= p_high, (
            f"p_higher_threat_in_n_turns must be monotone in "
            f"current_target_value: got p(>1)={p_low:.3f}, "
            f"p(>4)={p_mid:.3f}, p(>8)={p_high:.3f}.  A higher current "
            f"target makes 'an even higher target arrives' less likely, "
            f"not more."
        )
        # Sanity: at least one value should be non-trivial against the
        # high-threat library, otherwise the prior never fires.
        assert p_low > 0.10, (
            f"Against an Affinity-class library with Plating + Saga + "
            f"Sojourner's Companion, p_higher_threat_in_n_turns at the "
            f"Memnite-tier target value (1.0) must be > 0.10.  Got "
            f"{p_low:.3f}."
        )
