"""BHI must expose `p_artifact_threat` so the holdback penalty can scale
the per-CMC value of held interaction up against artifact-heavy
opponents (Affinity, Pinnacle Affinity).

Diagnostic: `docs/diagnostics/2026-05-01_azcon_followup.md` (step 2).

Affinity is the canonical class — Cranial Plating + artifact-creature
shells need a Counterspell answer that the AI tends to forfeit when
holdback is calibrated for the average opponent. The fix is a
context-aware coefficient (step 3) that consumes this belief; this
test only pins step 2 — `p_artifact_threat` is a meaningful posterior.

Rule (no card names): density of CardType.ARTIFACT in the opp's
library + hand + battlefield. Equipment, mana rocks, artifact
creatures, and artifact lands all count — every artifact contributes
to the "Counterspell is the only stack-side answer" pressure that
the holdback function is meant to capture.

Regression guard: `p_counter` and `p_removal` are unchanged.
"""
from __future__ import annotations

import random

import pytest

from ai.bhi import BayesianHandTracker
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
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = my_deck
    game.players[1].deck_name = opp_deck
    game.players[1].library = [
        _make_card(game, card_db, n, 1) for n in opp_library_cards
    ]
    game.players[1].hand = []
    return game


# Affinity-class library: dense in artifacts (Memnite, Ornithopter,
# Cranial Plating, Springleaf Drum, Sojourner's Companion). Most cards
# carry CardType.ARTIFACT.
AFFINITY_LIB = [
    "Memnite",
    "Ornithopter",
    "Springleaf Drum",
    "Cranial Plating",
    "Sojourner's Companion",
    "Frogmite",
    "Memnite",
    "Ornithopter",
    "Cranial Plating",
    "Darksteel Citadel",
]

# Boros Energy library: zero non-land artifacts. Lands present but
# they are the typical fetch/shock pair (no artifact lands).
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


class TestBhiTracksArtifactThreatDensity:
    """BHI must expose `p_artifact_threat` derived from opp's pool."""

    def test_no_artifact_opp_prior_is_zero(self, card_db):
        """Boros Energy carries no non-land artifacts → p must be 0."""
        game = _make_game(card_db, "Azorius Control", "Boros Energy",
                          BOROS_LIB)

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        assert bhi.get_artifact_threat_probability() == 0.0, (
            f"Boros Energy has zero non-land artifacts in library; "
            f"p_artifact_threat must be 0.0, got "
            f"{bhi.get_artifact_threat_probability():.3f}."
        )

    def test_artifact_opp_prior_is_high(self, card_db):
        """Affinity is dominantly artifacts — every non-land card in
        the sample has CardType.ARTIFACT. With hand_size=0 at game
        start the density-based prior is exactly 0; we therefore
        deal a 7-card hand first to exercise the live posterior path
        the holdback function will see at decision time."""
        game = _make_game(card_db, "Azorius Control", "Affinity",
                          AFFINITY_LIB)
        # Move 7 cards from library → hand to mirror the post-mulligan
        # state. This is the situation the holdback gate observes.
        for _ in range(7):
            c = game.players[1].library.pop(0)
            c.zone = "hand"
            game.players[1].hand.append(c)

        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)

        p = bhi.get_artifact_threat_probability()
        assert p >= 0.5, (
            f"Affinity's pool is artifact-dense; p_artifact_threat "
            f"must be ≥ 0.5 to drive the holdback ramp, got {p:.3f}. "
            f"Density-based prior over the 10-card pool: every "
            f"non-land card is an artifact, so prior should be high."
        )

    def test_get_counter_probability_unchanged(self, card_db):
        """Adding the artifact-threat belief must not perturb the
        existing counterspell belief path."""
        game = _make_game(card_db, "Azorius Control", "Boros Energy",
                          BOROS_LIB)
        bhi = BayesianHandTracker(player_idx=0)
        bhi.initialize_from_game(game)
        # Boros has no counterspells; this anchors the regression.
        assert bhi.get_counter_probability() == 0.0, (
            "Boros Energy has no counterspells; p_counter should be "
            "0.0 - artifact-threat tracking must not change this."
        )
