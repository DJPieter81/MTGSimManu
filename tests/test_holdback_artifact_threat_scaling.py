"""Holdback per-CMC value must scale with `bhi.p_artifact_threat`.

Diagnostic: `docs/diagnostics/2026-05-01_azcon_followup.md` (step 3).

The flat coefficient `HELD_RESPONSE_VALUE_PER_CMC = 4.0` is calibrated
against the average opponent. It is too soft for control decks facing
artifact-equipment archetypes (Affinity-class), where the held
Counterspell is the *only* answer to Cranial Plating once it resolves.
A flat 4.0 lets a CMC-3 spell tap out under holdback and forfeit the
counter on the same turn equipment would land lethal.

Rule (no card names):

    held_response_value_per_cmc(p_artifact_threat) =
        max(BASE, 2.0 + p_artifact_threat * 4.0)

with BASE = 4.0 (the Iter-2 calibration, preserved as a floor for
non-artifact opponents). At p=0.5 the formula returns 4.0 (floor
binds — existing behavior). At p=1.0 it returns 6.0 (ramp at full
artifact saturation — Affinity-class).

Tests:
1. p=0.0 (no artifacts) → 4.0 (base preserved).
2. p=0.5 (mixed)        → 4.0 (floor binds, regression guard).
3. p=1.0 (Affinity-class) → 6.0 (ramp).
4. End-to-end: same hand, single Counterspell + tight U mana, the
   holdback penalty is strictly more negative against an artifact
   opp than a non-artifact opp — proving the function is wired
   into `_holdback_penalty`.

Regression guard for the AzCon vs Affinity tap-out is end-to-end
against the BHI prior; we do not pin a specific replay here because
the upstream evoke-budget bug (step 1 in the diagnostic) is still
present and would mask any change.
"""
from __future__ import annotations

import math
import random

import pytest

from ai.bhi import BayesianHandTracker
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import (
    HELD_RESPONSE_VALUE_PER_CMC,
    held_response_value_per_cmc,
)
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add(game, card_db, name, controller, zone, summoning_sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


class TestHeldResponseValuePerCmcFunction:
    """The function-form must preserve the Iter-2 base for low-artifact
    opponents and ramp to 6.0 against Affinity-class."""

    def test_no_artifact_threat_returns_base(self):
        v = held_response_value_per_cmc(p_artifact_threat=0.0)
        assert math.isclose(v, 4.0, abs_tol=1e-9), (
            f"At p_artifact_threat=0 the function must return the "
            f"Iter-2 base (4.0) so non-artifact matchups behave "
            f"identically to before; got {v:.3f}."
        )

    def test_mid_artifact_threat_floor_binds(self):
        """At p=0.5 the linear term `2.0 + 0.5*4.0 = 4.0` matches
        the floor exactly. No regression on mixed opponents."""
        v = held_response_value_per_cmc(p_artifact_threat=0.5)
        assert math.isclose(v, 4.0, abs_tol=1e-9), (
            f"At p_artifact_threat=0.5 the floor (4.0) binds; got "
            f"{v:.3f}. Mid-density opponents must not see a softer "
            f"holdback than the Iter-2 base."
        )

    def test_full_artifact_threat_ramps_up(self):
        v = held_response_value_per_cmc(p_artifact_threat=1.0)
        assert math.isclose(v, 6.0, abs_tol=1e-9), (
            f"At p_artifact_threat=1.0 the function must ramp to "
            f"6.0 (= 2.0 + 1.0*4.0); got {v:.3f}. Affinity-class "
            f"matchups need the steeper holdback to keep counters "
            f"available for equipment threats."
        )

    def test_module_constant_matches_base(self):
        """The constant `HELD_RESPONSE_VALUE_PER_CMC` must remain
        importable and equal to the function's base value — every
        existing call site reads it directly today and we do not
        want to change that contract in this diff."""
        assert HELD_RESPONSE_VALUE_PER_CMC == held_response_value_per_cmc(0.0)


class TestHoldbackPenaltyScalesAgainstArtifactOpp:
    """End-to-end: same hand and mana, two opps with different
    p_artifact_threat. The penalty against the artifact opp must be
    strictly more negative."""

    def _make_holdback_setup(self, card_db, opp_deck, opp_lib_cards):
        """3 Islands + Counterspell + Augur (CMC-2) for player 0; opp
        has a live creature board so threat_prob ≈ 1.0. Opp's library
        composition drives p_artifact_threat (and hence the
        coefficient)."""
        game = GameState(rng=random.Random(0))
        for _ in range(3):
            _add(game, card_db, "Island", controller=0, zone="battlefield")

        augur = _add(game, card_db, "Augur of Bolas", controller=0,
                     zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        # Opp board so estimate_opp_threat_prob fires.
        _add(game, card_db, "Memnite", controller=1, zone="battlefield")
        _add(game, card_db, "Ornithopter", controller=1, zone="battlefield")
        for _ in range(3):
            _add(game, card_db, "Memnite", controller=1, zone="hand")

        # Opp library — composition drives the BHI prior.
        for n in opp_lib_cards:
            _add(game, card_db, n, controller=1, zone="library")

        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = opp_deck
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4

        return game, augur

    def test_artifact_opp_penalty_stricter_than_non_artifact(self, card_db):
        # Non-artifact opp library: zero artifacts and zero
        # removal/counter/burn so opp_threat_prob is dominated by the
        # shared on-board creature pressure (Memnite + Ornithopter).
        # That isolates the artifact-threat ramp as the sole
        # differentiator between the two scenarios.
        non_art_lib = [
            "Llanowar Elves", "Llanowar Elves",
            "Tarmogoyf", "Tarmogoyf",
            "Scavenging Ooze", "Scavenging Ooze",
            "Forest", "Forest", "Forest", "Forest",
        ]
        # Artifact-dense library — every non-land card carries
        # CardType.ARTIFACT.
        art_lib = [
            "Cranial Plating", "Cranial Plating",
            "Springleaf Drum", "Springleaf Drum",
            "Memnite", "Memnite", "Ornithopter",
            "Frogmite", "Sojourner's Companion",
            "Darksteel Citadel",
        ]

        game_a, augur_a = self._make_holdback_setup(
            card_db, "Boros Energy", non_art_lib)
        game_b, augur_b = self._make_holdback_setup(
            card_db, "Affinity", art_lib)

        def _penalty(game, augur):
            player = EVPlayer(player_idx=0,
                              deck_name="Azorius Control",
                              rng=random.Random(0))
            # Force-initialise BHI from the game so the holdback
            # function reads p_artifact_threat from a live posterior.
            # In sim runs this happens on first observe_* call.
            player.bhi.initialize_from_game(game)
            snap = snapshot_from_game(game, 0)
            me, opp = game.players[0], game.players[1]
            return player._holdback_penalty(
                me, opp, snap, cost=augur.template.cmc or 0,
                exclude_instance_id=augur.instance_id,
            )

        p_non_art = _penalty(game_a, augur_a)
        p_art = _penalty(game_b, augur_b)

        assert p_art < p_non_art, (
            f"Holdback penalty should be MORE negative against an "
            f"artifact-dense opp. Got non-artifact={p_non_art:.2f}, "
            f"artifact={p_art:.2f}. The coefficient is not consuming "
            f"p_artifact_threat — `_holdback_penalty` must read "
            f"`held_response_value_per_cmc(p)` rather than the flat "
            f"constant."
        )
