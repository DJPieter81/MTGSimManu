"""
Shared test fixtures for the MTG simulator test suite.
"""
import json
import os
import random
import pytest
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS


def _ensure_fallback_db():
    """Always build a worktree-local sidecar from ModernAtomic_part*
    and monkey-patch CardDatabase() to fall back to it whenever the
    shared ModernAtomic.json fails to parse.

    Multi-worktree runs share a single ModernAtomic.json in the top
    repo; concurrent reassembly across agents leaves the file
    mid-write and any test that loads the DB during that window
    flakes with JSONDecodeError.  The sidecar is a test-runner-only
    safety net — production callers always receive the canonical
    shared file via the default auto-discovery in CardDatabase.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    merged = {}
    for i in range(1, 20):
        part = os.path.join(project_root, f"ModernAtomic_part{i}.json")
        if not os.path.exists(part):
            continue
        try:
            with open(part) as f:
                merged.update(json.load(f).get("data", {}))
        except (json.JSONDecodeError, ValueError):
            # Part files are immutable per commit — a parse failure
            # here is unrecoverable; skip to give the shared file
            # the best chance to work.
            return
    if not merged:
        return
    sidecar = os.path.join(project_root, ".pytest_atomic_fallback.json")
    with open(sidecar, "w") as f:
        json.dump({"meta": {}, "data": merged}, f)
    _orig_init = CardDatabase.__init__

    def _patched_init(self, json_path=None):
        if json_path is None:
            try:
                return _orig_init(self, None)
            except (json.JSONDecodeError, ValueError):
                return _orig_init(self, sidecar)
        try:
            return _orig_init(self, json_path)
        except (json.JSONDecodeError, ValueError):
            return _orig_init(self, sidecar)

    CardDatabase.__init__ = _patched_init


_ensure_fallback_db()


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
