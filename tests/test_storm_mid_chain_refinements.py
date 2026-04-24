"""S-5 mid-chain patience refinements (Iter4).

Refinements to the mid-chain patience gate added in PR #142
(`ai/ev_player.py:1182-1218`):

A. **Storm-coverage escalation.** When `storm / opp_life > 0.5` (the
   chain has already covered more than half the gap to lethal), each
   missed draw is increasingly catastrophic — failing to close now
   wastes a chain that's *almost* there. Escalate the soft penalty:

       storm_coverage = storm / max(1, opp_life)
       escalation     = 1.0 + max(0, storm_coverage - 0.5)
       mod -= (storm + 2) / opp_life * 5.0 * escalation

   `0.5` is a CR-derived sentinel ("over halfway to lethal"), not a
   tuning knob.

B. **Draw-miss cascade risk.** When `has_draw=True` and `storm >= 3`
   AND only 1 draw remains in hand, the chain is one bad top-deck
   away from collapse.  Apply additional risk penalty proportional
   to library miss probability and lethal gap:

       miss_risk = min(1.0, (opp_life - storm) / max(1, len(library)))
       mod -= miss_risk * (storm / opp_life) * 3.0

Test scenarios (Option C — write failing test first):

1. Low-coverage soft penalty (storm=2 vs 20): refinement A dormant.
2. High-coverage escalation (storm=8 vs 15, coverage 0.53):
   penalty must exceed the non-escalated baseline.
3. Cascade risk fires (storm=4 vs 15, only 1 draw in hand).
4. Cascade risk dormant (storm=4 vs 15, 3 draws in hand) — sufficient
   digging power, no extra penalty.

Diagnostic anchor: Ruby Storm at 22.8% WR.  Audit identified that
the existing soft penalty is unit-flat regardless of coverage and
ignores draw-pipeline depth, so the AI continues digging through
chains it can no longer realistically close.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


# card_db fixture is provided by tests/conftest.py (session-scoped,
# with a resilient fallback to parts-based reconstruction if the
# shared ModernAtomic.json is mid-write).


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
    getattr(game.players[controller], 'library' if zone == 'library'
            else zone).append(card)
    return card


def _build_storm_scenario(card_db, *, storm, opp_life, draws_in_hand,
                          library_size=30, my_life=15):
    """Build a mid-chain Storm scenario with specified parameters.

    Returns (game, ritual_card).  No finisher, no PiF — triggers the
    mid-chain gate at ai/ev_player.py:1182.  Includes `draws_in_hand`
    cantrip cards in hand (Reckless Impulse), and pads the library
    with Mountains so `len(library) == library_size`.
    """
    game = GameState(rng=random.Random(0))
    # Storm side: 2 Mountains + 2 Medallions on battlefield (cost reducers).
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0, zone="battlefield")
    _add(game, card_db, "Ruby Medallion", controller=0, zone="battlefield")

    # Hand: rituals (the candidate + a second so _has_finisher stays False
    # but the chain has fuel) and N draws.
    ritual = _add(game, card_db, "Desperate Ritual", controller=0,
                  zone="hand")
    _add(game, card_db, "Pyretic Ritual", controller=0, zone="hand")
    for _ in range(draws_in_hand):
        _add(game, card_db, "Reckless Impulse", controller=0, zone="hand")

    # Library padding — controls the miss_risk denominator.
    for _ in range(library_size):
        _add(game, card_db, "Mountain", controller=0, zone="library")

    # Opp board: Boros pressure (so opp_clock_discrete stays > 2 normally).
    _add(game, card_db, "Guide of Souls", controller=1, zone="battlefield")

    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = storm
    game._global_storm_count = storm
    game.players[0].life = my_life
    game.players[1].life = opp_life

    return game, ritual


def _score(game, ritual):
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]
    return player._score_spell(ritual, snap, game, me, opp)


class TestStormCoverageEscalation:
    """Refinement A — penalty escalates when storm / opp_life > 0.5."""

    def test_low_coverage_soft_penalty_unchanged(self, card_db):
        """storm=2 vs opp_life=20 → coverage=0.10, far below 0.5
        sentinel.  The original soft penalty applies; ritual EV must
        remain in the soft-penalty band (above the hard clamp at
        pass_threshold - 10.0 but below positive scoring)."""
        game, ritual = _build_storm_scenario(
            card_db, storm=2, opp_life=20, draws_in_hand=2,
        )
        ev = _score(game, ritual)
        # Hard clamp lives at pass_threshold - 10 = -15.0 (STORM
        # profile pass_threshold = -5.0). At low coverage with draws
        # available the score should sit above the clamp — escalation
        # must not fire here.
        STORM_PASS_THRESHOLD = -5.0
        assert ev > STORM_PASS_THRESHOLD - 10.0 + 1.0, (
            f"Low-coverage scenario (storm=2/opp_life=20, coverage=0.10) "
            f"scored EV={ev:.2f} at or below the hard clamp.  The "
            f"escalation refinement must not fire when coverage <= 0.5."
        )

    def test_high_coverage_escalation_exceeds_baseline(self, card_db):
        """storm=8 vs opp_life=15 → coverage=0.533, just over the 0.5
        sentinel.  The escalated penalty must exceed the equivalent
        non-escalated penalty.

        Compare to a baseline computed manually using the unescalated
        formula `(storm+2)/opp_life * 5.0`:

            baseline = (8+2)/15 * 5.0 = 3.33
            escalation = 1 + (0.533 - 0.5) = 1.033
            escalated = 3.33 * 1.033 = 3.44

        So the actual penalty applied must be at least 3.44, i.e. the
        ritual's EV is at least 0.11 lower than it would have been
        without escalation (small but observable).  We assert the EV
        is below the *unescalated* projected score by at least 0.05."""
        game_high, ritual_high = _build_storm_scenario(
            card_db, storm=8, opp_life=15, draws_in_hand=2,
        )
        ev_high = _score(game_high, ritual_high)

        # Baseline: same scenario but constructed so coverage <= 0.5
        # (storm=7 / opp_life=15 → coverage=0.467, just under threshold).
        # Both scenarios trigger the soft-penalty branch (storm >= 1,
        # ritual, no finisher, has_draw).  Escalation must distinguish
        # them by storm-coverage alone.
        game_low, ritual_low = _build_storm_scenario(
            card_db, storm=7, opp_life=15, draws_in_hand=2,
        )
        ev_low = _score(game_low, ritual_low)

        # Escalated penalty: (storm+2)/opp_life * 5.0 * (1 + (cov-0.5))
        # storm=8: 10/15 * 5.0 * 1.033 = 3.44
        # storm=7: 9/15 * 5.0          = 3.00 (no escalation, cov<0.5)
        # The high-coverage EV must reflect the additional escalation
        # delta — at minimum, the high-storm penalty (which is already
        # larger by the (storm+2)/opp_life unescalated piece) must be
        # at least 0.4 below the low-coverage baseline EV.
        delta = ev_low - ev_high
        assert delta >= 0.4, (
            f"High-coverage escalation refinement did not fire.  "
            f"storm=8/opp_life=15 (coverage=0.533) EV={ev_high:.2f}; "
            f"storm=7/opp_life=15 (coverage=0.467) EV={ev_low:.2f}; "
            f"delta={delta:.2f}.  Expected the escalation multiplier "
            f"to widen the penalty gap beyond the unescalated "
            f"(storm+2)/opp_life increment."
        )


class TestDrawCascadeRisk:
    """Refinement B — extra penalty when storm >= 3 AND draw_count <= 1."""

    def test_cascade_risk_fires_with_one_draw_left(self, card_db):
        """storm=4, opp_life=15, only 1 draw in hand → cascade penalty
        applies.  The miss_risk term `(opp_life - storm) / library`
        and the (storm/opp_life) coverage scale jointly subtract from
        the ritual's EV.

        Compared to a baseline with the cascade gate inactive
        (draw_count > 1 — see test_cascade_dormant_with_three_draws),
        the EV must be measurably lower for the 1-draw scenario."""
        game_one, ritual_one = _build_storm_scenario(
            card_db, storm=4, opp_life=15, draws_in_hand=1,
            library_size=30,
        )
        ev_one = _score(game_one, ritual_one)

        game_three, ritual_three = _build_storm_scenario(
            card_db, storm=4, opp_life=15, draws_in_hand=3,
            library_size=30,
        )
        ev_three = _score(game_three, ritual_three)

        # miss_risk = (15-4)/30 = 0.367
        # extra_penalty = 0.367 * (4/15) * 3.0 = 0.293
        # We expect ev_one < ev_three by at least 0.2 (allowing for
        # any incidental score deltas from the extra hand cards).
        delta = ev_three - ev_one
        assert delta >= 0.2, (
            f"Draw-cascade refinement did not fire.  "
            f"storm=4/opp_life=15 with 1 draw EV={ev_one:.2f}; "
            f"with 3 draws EV={ev_three:.2f}; delta={delta:.2f}.  "
            f"Expected the 1-draw scenario to score measurably lower "
            f"due to miss-risk * coverage * 3.0 cascade penalty."
        )

    def test_cascade_dormant_with_three_draws(self, card_db):
        """Regression: storm=4, opp_life=15, 3 draws in hand → no
        cascade penalty.  Sufficient digging power means the chain
        has multiple chances to find a finisher; the extra penalty
        would over-fire and cause the AI to pass with a still-live
        chain.

        We assert by comparing to a baseline with storm=4/opp_life=15
        but only 1 draw — the cascade-dormant case must be strictly
        higher EV than the cascade-fired case (already covered above)
        AND must remain above the hard clamp at pass_threshold - 10."""
        game, ritual = _build_storm_scenario(
            card_db, storm=4, opp_life=15, draws_in_hand=3,
            library_size=30,
        )
        ev = _score(game, ritual)
        STORM_PASS_THRESHOLD = -5.0
        assert ev > STORM_PASS_THRESHOLD - 10.0 + 1.0, (
            f"Cascade-dormant scenario (3 draws in hand) scored "
            f"EV={ev:.2f} at or below hard clamp — refinement B "
            f"must not fire when draw_count > 1."
        )
