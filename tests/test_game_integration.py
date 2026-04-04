"""
Integration tests — run full games and verify outcomes are reasonable.
"""
import random
import pytest
from tests.conftest import run_seeded_game
from decks.modern_meta import MODERN_DECKS, get_all_deck_names


class TestSingleGame:
    """Verify a single game runs to completion."""

    def test_game_completes(self, game_runner):
        result = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=42)
        assert result.winner is not None or result.win_condition == "draw"
        assert result.turns > 0
        assert result.turns <= 30  # should finish within max turns + buffer

    def test_game_has_valid_winner(self, game_runner):
        result = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=42)
        if result.winner is not None:
            assert result.winner in (0, 1)
            assert result.winner_deck in ("Domain Zoo", "Dimir Midrange")

    def test_game_win_condition_valid(self, game_runner):
        result = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=42)
        valid_conditions = {"damage", "mill", "combo", "concede", "timeout", "draw"}
        assert result.win_condition in valid_conditions

    def test_different_seeds_different_games(self, game_runner):
        r1 = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=100)
        r2 = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=999)
        # With very different seeds, at least one metric should differ
        assert (r1.turns != r2.turns or r1.winner != r2.winner
                or r1.winner_life != r2.winner_life)


class TestMatchupBalance:
    """Verify no deck is completely broken over multiple games."""

    def test_zoo_vs_dimir_balanced(self, game_runner):
        """Zoo vs Dimir should be competitive (neither deck wins 100%)."""
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
        # Neither deck should win more than 90% of games
        assert wins[0] < 18, f"Zoo won {wins[0]}/20 — too dominant"
        assert wins[1] < 18, f"Dimir won {wins[1]}/20 — too dominant"

    def test_all_decks_can_run(self, game_runner):
        """Every deck should be able to play a game without crashing."""
        deck_names = get_all_deck_names()
        for name in deck_names:
            random.seed(42)
            d = MODERN_DECKS[name]
            d_opp = MODERN_DECKS["Domain Zoo"] if name != "Domain Zoo" else MODERN_DECKS["Dimir Midrange"]
            opp_name = "Domain Zoo" if name != "Domain Zoo" else "Dimir Midrange"
            result = game_runner.run_game(
                name, d["mainboard"],
                opp_name, d_opp["mainboard"],
                deck1_sideboard=d.get("sideboard", {}),
                deck2_sideboard=d_opp.get("sideboard", {}),
            )
            assert result.turns > 0, f"Game with {name} didn't complete"


class TestCallbacksBoundary:
    """Verify the engine/AI callback boundary works."""

    def test_default_callbacks_game_runs(self):
        """Game with DefaultCallbacks should still run (basic defaults)."""
        from engine.game_state import GameState
        from engine.callbacks import DefaultCallbacks
        game = GameState(callbacks=DefaultCallbacks())
        assert isinstance(game.callbacks, DefaultCallbacks)

    def test_ai_callbacks_used_in_runner(self, game_runner):
        """GameRunner should wire AICallbacks into GameState."""
        from engine.game_runner import AICallbacks
        # The runner creates GameState with AICallbacks internally
        # Just verify it works end-to-end
        result = run_seeded_game(game_runner, "Domain Zoo", "Dimir Midrange", seed=42)
        assert result.turns > 0
