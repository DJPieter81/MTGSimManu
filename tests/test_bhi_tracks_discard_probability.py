"""S-3 (iter5 retry): BHI must expose a discard probability prior so the
combo-risk discount accounts for hand disruption (Thoughtseize /
Inquisition of Kozilek) - not just counter-magic.

Diagnostic (2026-04-24): Ruby Storm sits at 19.4% flat WR. The
combo_calc risk discount is `1.0 - P(counter)`, so combo_value is
overvalued versus discard-only opponents (Dimir Midrange, Goryo's
Vengeance) where the disruption is hand-targeted, not stack-targeted.

Fix requirement (no hardcoded card names):
    `BayesianHandTracker.initialize_from_game` must set a
    `p_discard` prior derived from the opponent's deck gameplan -
    specifically by scanning `mulligan_keys`, `critical_pieces`, and
    `always_early` for cards whose oracle text contains the
    "target player ... discards" pattern. If any such card is
    registered in the gameplan we set `p_discard = 0.5` (a
    documented prior: 50% chance opp is planning to deploy hand
    disruption). Otherwise `p_discard = 0.0`.

    `_compute_risk_discount` in `ai/combo_calc.py` must then use
    `1.0 - max(P(counter), P(discard) * 0.5)`. The `0.5` factor
    represents "discard hits a key piece ~50% of the time given the
    opponent picks one of our critical_pieces" - a documented
    Bayesian prior, not a magic number.

Regression guard: `get_counter_probability()` keeps its existing
behavior - discard tracking is additive only.
"""
from __future__ import annotations

import random

import pytest

from ai.bhi import BayesianHandTracker
from ai.combo_calc import _compute_risk_discount
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _make_card(game, card_db, name, controller):
    tmpl = card_db.cards.get(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="library",
    )
    card._game_state = game
    return card


def _make_game(card_db, my_deck, opp_deck, opp_library_cards):
    """Construct a minimal GameState with both players configured."""
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = my_deck
    game.players[1].deck_name = opp_deck
    game.players[1].library = [
        _make_card(game, card_db, n, 1) for n in opp_library_cards
    ]
    game.players[1].hand = []
    return game


# Library samples taken straight from the published gameplans.
# Boros Energy: no discard cards in the deck.
BOROS_LIB = [
    "Guide of Souls",
    "Ajani, Nacatl Pariah",
    "Phlage, Titan of Fire's Fury",
    "Lightning Bolt",
    "Galvanic Discharge",
    "Static Prison",
    "Plains",
    "Mountain",
    "Inspiring Vantage",
    "Sacred Foundry",
]

# Dimir Midrange: Thoughtseize is in mulligan_keys.
DIMIR_LIB = [
    "Thoughtseize",
    "Fatal Push",
    "Psychic Frog",
    "Orcish Bowmasters",
    "Murktide Regent",
    "Consider",
    "Watery Grave",
    "Polluted Delta",
    "Island",
    "Swamp",
]


class TestBhiTracksDiscardProbability:
    """S-3 - BHI must expose `p_discard` derived from opp's gameplan."""

    def test_no_discard_opp_prior_is_zero(self, card_db):
        """Boros Energy has no discard spells; p_discard must be 0.0
        because the gameplan declares none in mulligan_keys /
        critical_pieces / always_early."""
        game = _make_game(card_db, "Ruby Storm", "Boros Energy", BOROS_LIB)

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        assert bhi.get_discard_probability() == 0.0, (
            f"Boros Energy has no discard cards in the published "
            f"gameplan; p_discard must be 0.0, got "
            f"{bhi.get_discard_probability()}."
        )

    def test_discard_opp_prior_is_positive(self, card_db):
        """Dimir Midrange registers Thoughtseize in mulligan_keys.
        That card's oracle text contains 'target player ... discards
        that card', so p_discard must be > 0."""
        game = _make_game(card_db, "Ruby Storm", "Dimir Midrange", DIMIR_LIB)

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        assert bhi.get_discard_probability() > 0.0, (
            f"Dimir Midrange's gameplan declares Thoughtseize in "
            f"mulligan_keys; BHI must set p_discard > 0 so combo "
            f"risk discounting accounts for hand disruption. Got "
            f"{bhi.get_discard_probability()}."
        )

    def test_get_counter_probability_unchanged(self, card_db):
        """Regression guard - adding p_discard tracking must not
        perturb the existing counterspell prior path."""
        game = _make_game(card_db, "Ruby Storm", "Boros Energy", BOROS_LIB)
        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)
        assert bhi.get_counter_probability() == 0.0, (
            "Boros Energy has no counterspells; p_counter should be "
            "0.0 - discard tracking must not change this."
        )

    def test_risk_discount_lower_against_discard_opp(self, card_db):
        """The combo risk discount must be strictly lower against a
        discard opponent than against a clean board, holding
        counter-magic constant. Storm's combo_value should be
        discounted more vs Dimir than vs Boros."""
        game_boros = _make_game(card_db, "Ruby Storm", "Boros Energy", BOROS_LIB)
        bhi_boros = BayesianHandTracker(player_idx=0)
        bhi_boros.initialize_from_game(game_boros)
        opp_boros = game_boros.players[1]

        game_dimir = _make_game(card_db, "Ruby Storm", "Dimir Midrange", DIMIR_LIB)
        bhi_dimir = BayesianHandTracker(player_idx=0)
        bhi_dimir.initialize_from_game(game_dimir)
        opp_dimir = game_dimir.players[1]

        rd_boros = _compute_risk_discount(bhi_boros, opp_boros)
        rd_dimir = _compute_risk_discount(bhi_dimir, opp_dimir)

        assert rd_dimir < rd_boros, (
            f"Risk discount vs Dimir ({rd_dimir:.3f}) must be lower "
            f"than vs Boros ({rd_boros:.3f}) - discard probability "
            f"should reduce Storm's combo_value when facing hand "
            f"disruption. _compute_risk_discount is not consuming "
            f"p_discard."
        )
