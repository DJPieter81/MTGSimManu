"""
Shared test fixtures for the MTG simulator test suite.
"""
import random
import pytest
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS


@pytest.fixture(scope="session")
def card_db():
    """Load the card database once for all tests."""
    return CardDatabase()


@pytest.fixture
def game_runner(card_db):
    """Create a fresh GameRunner for each test."""
    return GameRunner(card_db)


@pytest.fixture
def zoo_deck():
    """Domain Zoo deck list."""
    return MODERN_DECKS["Domain Zoo"]


@pytest.fixture
def dimir_deck():
    """Dimir Midrange deck list."""
    return MODERN_DECKS["Dimir Midrange"]


def run_seeded_game(runner, deck1_name, deck2_name, seed=42):
    """Helper: run a single game with a fixed seed."""
    random.seed(seed)
    d1 = MODERN_DECKS[deck1_name]
    d2 = MODERN_DECKS[deck2_name]
    return runner.run_game(
        deck1_name, d1["mainboard"],
        deck2_name, d2["mainboard"],
        deck1_sideboard=d1.get("sideboard", {}),
        deck2_sideboard=d2.get("sideboard", {}),
    )
