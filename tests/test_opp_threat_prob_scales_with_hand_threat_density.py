"""`_estimate_opp_threat_prob` (BHI path) must scale with the threat
density of the opp's pool, not a flat 0.5 weight on hand size.

Diagnostic: this audit (claude/coefficient-to-function-audit).

The BHI branch in `ai/ev_player.py::_estimate_opp_threat_prob` used:

    p_action  = max(p_removal, p_counter, p_burn)
    hand_factor = min(1.0, snap.opp_hand_size / 7.0)
    return clamp(p_action + 0.5 * hand_factor, 0.1, 1.0)

The literal `0.5 * hand_factor` is a flat coefficient — it weights an
unknown-hand signal independently of the *actual* threat composition of
the opponent. Two opponents with identical 7-card hands but very
different decks (a counterspell-heavy control mirror vs a creature-
heavy aggro deck) get the same `0.5 * 1.0 = 0.5` boost, even though the
control opp is far less likely to deploy a follow-up creature threat
this turn.

Rule (no card names):

    P(opp threatens us next turn | unknown hand)
        = 1 - (1 - threat_density) ** opp_hand_size

where ``threat_density`` is the per-card probability that a card in the
opp's pool is a "threat we want to interact with" (creatures, burn,
planeswalkers, removal pointed at us). This is a BHI primitive
(`p_threat_in_hand_density`) computed from the opp's pool composition,
the same density-based prior used by every other belief in
`HandBeliefs`.

Tests:
1. The new BHI primitive returns 0.0 when opp pool has no threat cards.
2. The primitive returns near-saturation (>= 0.9) for a 7-card hand
   drawn from a creature-dense pool.
3. End-to-end: with the BHI tracker initialised, a 5-card opp hand
   from a high-threat-density deck yields a strictly higher
   `_estimate_opp_threat_prob` than the same hand size from a
   low-threat-density deck (counterspell-only). This proves the
   coefficient consumes the new BHI signal rather than the flat 0.5.
"""
from __future__ import annotations

import random

import pytest

from ai.bhi import BayesianHandTracker
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


# Creature-dense pool (Zoo-ish): every non-land is a creature, the
# canonical threat the holdback wants to anticipate. Crucially, we
# include NO burn, removal, or counters — that isolates the new
# density-derived signal from the existing `p_action` term in
# `_estimate_opp_threat_prob` (which already captures burn / removal
# / counter probabilities). With p_action = 0 in both HIGH and LOW
# pools, the only differentiator is the density-weighted hand term.
HIGH_THREAT_LIB = [
    "Memnite", "Memnite", "Ornithopter", "Ornithopter",
    "Tarmogoyf", "Tarmogoyf",
    "Llanowar Elves", "Llanowar Elves",
    "Scavenging Ooze", "Scavenging Ooze",
]

# Inert-pool: zero creatures, zero burn, zero counters, zero removal.
# These are utility artifacts and card-draw enchantments — none of
# them tagged as a threat the holdback wants to anticipate. With
# `p_counter`, `p_removal`, `p_burn` all 0.0, the only signal feeding
# `_estimate_opp_threat_prob` is the unknown-hand term, isolating
# the coefficient under test.
LOW_THREAT_LIB = [
    "Mishra's Bauble", "Mishra's Bauble",
    "Chromatic Sphere", "Chromatic Sphere",
    "Pithing Needle", "Pithing Needle",
    "Phyrexian Arena", "Phyrexian Arena",
    "Island", "Island",
]


class TestPThreatInHandDensityPrimitive:
    """The new BHI primitive returns the density-based prior over the
    opp pool — the probability that a randomly-drawn card from the
    pool is a threat we want to interact with (creatures, burn,
    planeswalkers)."""

    def _make_game(self, card_db, opp_lib):
        game = GameState(rng=random.Random(0))
        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "TestOpp"
        for n in opp_lib:
            _add(game, card_db, n, controller=1, zone="library")
        return game

    def test_no_threat_pool_density_is_zero(self, card_db):
        game = self._make_game(card_db, LOW_THREAT_LIB)
        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)
        # Counterspell-only opp: no creatures, no burn → density = 0.
        assert bhi.beliefs.p_threat_in_hand_density == 0.0, (
            f"Pool of pure counterspells has zero creature/burn density; "
            f"p_threat_in_hand_density must be 0.0, got "
            f"{bhi.beliefs.p_threat_in_hand_density:.3f}."
        )

    def test_full_threat_pool_density_is_high(self, card_db):
        game = self._make_game(card_db, HIGH_THREAT_LIB)
        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)
        # 100% of non-land cards are creatures or burn → density ≈ 1.0
        d = bhi.beliefs.p_threat_in_hand_density
        assert d >= 0.9, (
            f"Pool is fully creature/burn; density must be ≥ 0.9, "
            f"got {d:.3f}. Density-based prior over non-land pool."
        )


class TestOppThreatProbConsumesDensitySignal:
    """End-to-end: the BHI branch of `_estimate_opp_threat_prob` must
    return a strictly higher probability for a high-threat-density opp
    than a low-threat-density opp at identical hand size."""

    def _make_setup(self, card_db, opp_lib, opp_hand_size):
        game = GameState(rng=random.Random(0))
        # Player 0 has neutral state; we only care about the opp signal.
        for _ in range(3):
            _add(game, card_db, "Island", controller=0, zone="battlefield")

        # Move N cards from library → hand to set the unknown-hand
        # signal. The opp hand contents drive `snap.opp_hand_size`;
        # the *library* drives the BHI density posterior.
        for n in opp_lib:
            _add(game, card_db, n, controller=1, zone="library")
        for _ in range(opp_hand_size):
            if game.players[1].library:
                c = game.players[1].library.pop(0)
                c.zone = "hand"
                game.players[1].hand.append(c)

        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "TestOpp"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4
        return game

    def test_high_density_opp_has_higher_threat_prob(self, card_db):
        game_hi = self._make_setup(card_db, HIGH_THREAT_LIB,
                                    opp_hand_size=5)
        game_lo = self._make_setup(card_db, LOW_THREAT_LIB,
                                    opp_hand_size=5)

        def _prob(game):
            player = EVPlayer(player_idx=0,
                              deck_name="Azorius Control",
                              rng=random.Random(0))
            player.bhi.initialize_from_game(game)
            snap = snapshot_from_game(game, 0)
            opp = game.players[1]
            return player._estimate_opp_threat_prob(snap, opp)

        p_hi = _prob(game_hi)
        p_lo = _prob(game_lo)

        assert p_hi > p_lo, (
            f"_estimate_opp_threat_prob must scale with the BHI "
            f"threat-density signal. Got high-density={p_hi:.3f}, "
            f"low-density={p_lo:.3f}. The BHI branch is still using "
            f"the flat `0.5 * hand_factor` coefficient instead of the "
            f"density-derived `p_threat_in_hand_density`."
        )
