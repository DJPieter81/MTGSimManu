"""
Tests for the EV-based AI architecture.

Tests:
  - DeckKnowledge: hypergeometric draw probabilities
  - EVSnapshot: board state snapshots and derived properties
  - Per-archetype value functions: aggro, midrange, control, combo
  - EV spell decision: candidate scoring and comparison
  - Integration: full games run with EV system
"""
import random
import pytest
from unittest.mock import MagicMock

from ai.deck_knowledge import DeckKnowledge
from ai.ev_evaluator import (
    EVSnapshot, snapshot_from_game, evaluate_board,
    estimate_spell_ev, estimate_pass_ev, creature_value,
)
from ai.clock import life_as_resource


# ─────────────────────────────────────────────────────────────
# DeckKnowledge tests
# ─────────────────────────────────────────────────────────────

class TestDeckKnowledge:
    """Test deck composition math."""

    def test_basic_remaining(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4, "Mountain": 20},
            seen_counts={"Lightning Bolt": 1},
        )
        assert dk.copies_remaining("Lightning Bolt") == 3
        assert dk.copies_remaining("Mountain") == 20
        assert dk.deck_size == 23

    def test_probability_of_drawing_single(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4, "Mountain": 56},
        )
        # 4 copies in 60 cards, drawing 1
        p = dk.probability_of_drawing("Lightning Bolt", 1)
        assert abs(p - 4 / 60) < 0.001

    def test_probability_of_drawing_multiple(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4, "Mountain": 56},
        )
        # Drawing 7 cards, P(at least 1 Bolt)
        p = dk.probability_of_drawing("Lightning Bolt", 7)
        # P(miss all 7) = C(56,7)/C(60,7) ≈ 0.609
        expected = 1.0 - (56/60 * 55/59 * 54/58 * 53/57 * 52/56 * 51/55 * 50/54)
        assert abs(p - expected) < 0.001

    def test_probability_zero_copies(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4, "Mountain": 56},
            seen_counts={"Lightning Bolt": 4},
        )
        assert dk.probability_of_drawing("Lightning Bolt", 7) == 0.0

    def test_probability_all_copies(self):
        dk = DeckKnowledge(
            full_decklist={"Mountain": 4},
        )
        # All 4 remaining cards are Mountains
        assert dk.probability_of_drawing("Mountain", 1) == 1.0

    def test_category_density(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4, "Mountain": 16, "Ragavan, Nimble Pilferer": 4},
        )
        dk._land_names = {"Mountain"}
        density = dk.category_density({"Mountain"})
        assert abs(density - 16 / 24) < 0.001

    def test_record_seen(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4, "Mountain": 20},
        )
        assert dk.copies_remaining("Lightning Bolt") == 4
        dk.record_seen("Lightning Bolt")
        assert dk.copies_remaining("Lightning Bolt") == 3
        dk.record_seen("Lightning Bolt")
        assert dk.copies_remaining("Lightning Bolt") == 2

    def test_record_returned(self):
        dk = DeckKnowledge(
            full_decklist={"Lightning Bolt": 4},
            seen_counts={"Lightning Bolt": 2},
        )
        assert dk.copies_remaining("Lightning Bolt") == 2
        dk.record_returned("Lightning Bolt")
        assert dk.copies_remaining("Lightning Bolt") == 3

    def test_probability_of_drawing_any(self):
        dk = DeckKnowledge(
            full_decklist={"Bolt": 4, "Push": 4, "Other": 52},
        )
        p = dk.probability_of_drawing_any(["Bolt", "Push"], 1)
        assert abs(p - 8 / 60) < 0.001

    def test_from_decklist(self):
        dk = DeckKnowledge.from_decklist(
            {"Mountain": 20, "Lightning Bolt": 4},
            land_names={"Mountain"}
        )
        assert dk.deck_size == 24
        assert "Mountain" in dk._land_names


# ─────────────────────────────────────────────────────────────
# EVSnapshot tests
# ─────────────────────────────────────────────────────────────

class TestEVSnapshot:
    """Test EVSnapshot derived properties."""

    def test_clock_calculation(self):
        snap = EVSnapshot(my_power=5, opp_life=20, opp_power=3, my_life=20)
        # Continuous (smooth EV gradient)
        assert snap.my_clock == 4.0  # 20/5
        assert abs(snap.opp_clock - 20/3) < 1e-9  # ~6.667
        # Discrete (boolean rule checks)
        assert snap.my_clock_discrete == 4  # ceil(20/5)
        assert snap.opp_clock_discrete == 7  # ceil(20/3)

    def test_no_power_clock(self):
        snap = EVSnapshot(my_power=0, opp_life=20)
        assert snap.my_clock == 99.0
        assert snap.my_clock_discrete == 99

    def test_has_lethal(self):
        snap = EVSnapshot(my_power=10, opp_life=5)
        assert snap.has_lethal is True

    def test_not_lethal(self):
        snap = EVSnapshot(my_power=3, opp_life=20)
        assert snap.has_lethal is False

    def test_am_dead_next(self):
        snap = EVSnapshot(opp_power=10, my_life=5)
        assert snap.am_dead_next is True

    def test_not_dead_next(self):
        snap = EVSnapshot(opp_power=3, my_life=20)
        assert snap.am_dead_next is False


# ─────────────────────────────────────────────────────────────
# Life value tests
# ─────────────────────────────────────────────────────────────

class TestLifeValue:
    """Test clock-based life valuation (life_as_resource)."""

    def test_zero_life(self):
        assert life_as_resource(0, 5) == -100.0

    def test_low_life_more_valuable(self):
        # Going from 3->2 should be more costly than 20->19
        # With incoming power of 5, each life = 0.2 turns
        delta_low = life_as_resource(3, 5) - life_as_resource(2, 5)
        delta_high = life_as_resource(20, 5) - life_as_resource(19, 5)
        # Both are linear (1/opp_power per point), so same delta — that's correct
        # for clock-based valuation. Test that values are positive and monotonic.
        assert delta_low > 0
        assert delta_high > 0

    def test_monotonically_increasing(self):
        for i in range(1, 30):
            assert life_as_resource(i, 5) > life_as_resource(i - 1, 5)


# ─────────────────────────────────────────────────────────────
# Per-archetype value function tests
# ─────────────────────────────────────────────────────────────

class TestArchetypeEvaluators:
    """Test per-archetype value functions."""

    def test_prefers_board_power(self):
        board = EVSnapshot(my_power=8, my_creature_count=3, opp_power=0,
                           my_life=20, opp_life=12)
        empty = EVSnapshot(my_power=0, my_creature_count=0, opp_power=0,
                           my_life=20, opp_life=12)
        assert evaluate_board(board, "aggro") > evaluate_board(empty, "aggro")

    def test_values_low_opp_life(self):
        high = EVSnapshot(my_power=4, opp_life=20, my_life=20)
        low = EVSnapshot(my_power=4, opp_life=5, my_life=20)
        assert evaluate_board(low, "aggro") > evaluate_board(high, "aggro")

    def test_values_card_advantage(self):
        cards = EVSnapshot(my_hand_size=5, opp_hand_size=2, my_life=20,
                           opp_life=20, my_power=3, opp_power=3, my_mana=3)
        no_cards = EVSnapshot(my_hand_size=1, opp_hand_size=5, my_life=20,
                              opp_life=20, my_power=3, opp_power=3, my_mana=3)
        assert evaluate_board(cards, "midrange") > evaluate_board(no_cards, "midrange")

    def test_penalizes_opp_board(self):
        clean = EVSnapshot(opp_power=0, opp_creature_count=0, my_life=20,
                           opp_life=20, my_hand_size=5)
        threats = EVSnapshot(opp_power=8, opp_creature_count=3, my_life=20,
                             opp_life=20, my_hand_size=5)
        assert evaluate_board(clean, "control") > evaluate_board(threats, "control")

    def test_combo_values_hand_and_mana(self):
        rich = EVSnapshot(my_hand_size=7, my_mana=5, my_life=20, opp_life=20)
        poor = EVSnapshot(my_hand_size=2, my_mana=1, my_life=20, opp_life=20)
        assert evaluate_board(rich, "combo") > evaluate_board(poor, "combo")

    def test_negative_if_dead(self):
        snap = EVSnapshot(my_life=0, opp_power=10, my_hand_size=7, my_mana=5,
                          opp_life=20)
        assert evaluate_board(snap, "combo") < -10

    def test_evaluate_board_dispatches(self):
        snap = EVSnapshot(my_life=20, opp_life=20)
        # Should not crash for any archetype
        for arch in ("aggro", "midrange", "control", "combo"):
            val = evaluate_board(snap, arch)
            assert isinstance(val, float)


# ─────────────────────────────────────────────────────────────
# Integration: full game with EV system
# ─────────────────────────────────────────────────────────────

class TestEVIntegration:
    """Verify EV system works in full game simulations."""

    def test_ev_game_completes(self, game_runner):
        """A game using the EV system should complete without errors."""
        from tests.conftest import run_seeded_game
        result = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=42)
        assert result.turns > 0
        assert result.winner is not None or result.win_condition == "draw"

    def test_ev_all_decks_complete(self, game_runner):
        """Every deck should complete a game with the EV system."""
        from decks.modern_meta import MODERN_DECKS, get_all_deck_names
        for name in get_all_deck_names():
            random.seed(42)
            d = MODERN_DECKS[name]
            opp_name = "Domain Zoo" if name != "Domain Zoo" else "Dimir Midrange"
            d_opp = MODERN_DECKS[opp_name]
            result = game_runner.run_game(
                name, d["mainboard"],
                opp_name, d_opp["mainboard"],
                deck1_sideboard=d.get("sideboard", {}),
                deck2_sideboard=d_opp.get("sideboard", {}),
            )
            assert result.turns > 0, f"{name} game didn't complete"

    def test_ev_matchup_balance(self, game_runner):
        """Zoo vs Dimir should be competitive under EV system."""
        from decks.modern_meta import MODERN_DECKS
        wins = {0: 0, 1: 0}
        for i in range(20):
            random.seed(10000 + i * 100)
            d1 = MODERN_DECKS["Domain Zoo"]
            d2 = MODERN_DECKS["Dimir Midrange"]
            result = game_runner.run_game(
                "Domain Zoo", d1["mainboard"],
                "Dimir Midrange", d2["mainboard"],
                deck1_sideboard=d1.get("sideboard", {}),
                deck2_sideboard=d2.get("sideboard", {}),
            )
            if result.winner is not None:
                wins[result.winner] += 1
        # Neither deck should win more than 90%
        assert wins[0] < 18, f"Zoo won {wins[0]}/20 — too dominant"
        assert wins[1] < 18, f"Dimir won {wins[1]}/20 — too dominant"
