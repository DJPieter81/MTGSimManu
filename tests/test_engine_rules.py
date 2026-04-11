"""
Engine layer tests — verify game rules work correctly.
"""
import random
import pytest
from engine.game_state import GameState, PlayerState
from engine.card_database import CardDatabase
from engine.constants import STARTING_LIFE, MAX_HAND_SIZE
from engine.callbacks import DefaultCallbacks


class TestGameStateBasics:
    """Basic GameState initialization and rules."""

    def test_initial_life_total(self):
        game = GameState()
        assert game.players[0].life == STARTING_LIFE
        assert game.players[1].life == STARTING_LIFE

    def test_initial_life_is_20(self):
        game = GameState()
        assert game.players[0].life == 20

    def test_game_not_over_at_start(self):
        game = GameState()
        assert game.game_over is False
        assert game.winner is None

    def test_turn_starts_at_1(self):
        game = GameState()
        assert game.turn_number == 1

    def test_two_players_created(self):
        game = GameState()
        assert len(game.players) == 2
        assert game.players[0].player_idx == 0
        assert game.players[1].player_idx == 1

    def test_default_callbacks(self):
        game = GameState()
        assert isinstance(game.callbacks, DefaultCallbacks)

    def test_custom_callbacks(self):
        class MyCallbacks(DefaultCallbacks):
            pass
        game = GameState(callbacks=MyCallbacks())
        assert isinstance(game.callbacks, MyCallbacks)

    def test_rng_seeded(self):
        rng = random.Random(42)
        game = GameState(rng=rng)
        assert game.rng is rng


class TestPlayerState:
    """PlayerState initialization."""

    def test_empty_zones(self):
        p = PlayerState(player_idx=0)
        assert len(p.hand) == 0
        assert len(p.library) == 0
        assert len(p.graveyard) == 0
        assert len(p.exile) == 0

    def test_starting_life(self):
        p = PlayerState(player_idx=0)
        assert p.life == STARTING_LIFE


class TestDeckLoading:
    """Verify deck loading and card database."""

    def test_card_database_loads(self, card_db):
        assert card_db is not None

    def test_all_decks_have_60_cards(self):
        from decks.modern_meta import MODERN_DECKS
        for name, deck in MODERN_DECKS.items():
            total = sum(deck["mainboard"].values())
            assert total == 60, f"{name} has {total} mainboard cards, expected 60"

    def test_all_decks_have_valid_sideboards(self):
        from decks.modern_meta import MODERN_DECKS
        for name, deck in MODERN_DECKS.items():
            if "sideboard" in deck:
                total = sum(deck["sideboard"].values())
                assert total <= 15, f"{name} has {total} sideboard cards, expected <= 15"

    def test_deck_names_match(self):
        from decks.modern_meta import get_all_deck_names
        names = get_all_deck_names()
        assert len(names) == 15
        assert "Domain Zoo" in names
        assert "Dimir Midrange" in names
