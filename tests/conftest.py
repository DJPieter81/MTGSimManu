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


# ─── Optional-dependency collection guard ────────────────────────────
#
# A few test modules exercise the optional LLM/embedding feature set
# and hard-import heavy third-party packages (numpy, pydantic_ai) at
# module scope. The offline simulator does not require those packages,
# and CI installs only pydantic-ai-slim (numpy is intentionally absent
# to keep the runner from pulling torch). Skip these modules at
# collection time when their dependency is missing so the full suite
# can run with `pytest tests/` regardless of which optional deps are
# present, instead of aborting the whole run on a collection ImportError.
_OPTIONAL_DEP_MODULES = {
    "numpy": ["test_llm_embeddings.py"],
    "pydantic_ai": [
        "test_cached_agent.py",
        "test_synth_gameplan_llm_backend.py",
        "test_audit_doc_freshness.py",
        "test_llm_agents.py",
        "test_llm_agents_with_budget.py",
        "test_metered_agent.py",
    ],
}
collect_ignore = []
for _dep, _modules in _OPTIONAL_DEP_MODULES.items():
    try:
        __import__(_dep)
    except ImportError:
        collect_ignore.extend(_modules)


@pytest.fixture(autouse=True)
def _restore_effect_registry():
    """Snapshot and restore the global EFFECT_REGISTRY around every test.

    EFFECT_REGISTRY is a process-wide singleton. Some tests register
    handlers at execution time (e.g. mock card handlers); without this
    guard those registrations leak into later tests, overriding real
    card behaviour and flipping deterministic game outcomes depending
    on collection order. Restoring the registry per test isolates that
    state so the full suite is order-independent.
    """
    from engine.card_effects import EFFECT_REGISTRY

    snapshot = {name: list(handlers) for name, handlers in EFFECT_REGISTRY._handlers.items()}
    yield
    EFFECT_REGISTRY._handlers.clear()
    EFFECT_REGISTRY._handlers.update(snapshot)


@pytest.fixture(scope="session")
def card_db():
    """Load the card database once for all tests."""
    from tests._card_db_cache import shared_card_database

    return shared_card_database()


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


# ─── LLM eval-harness opt-in flag ────────────────────────────────────
#
# `tests/eval/` runs against a real foundation model when
# `--run-eval` is passed.  Without the flag, only the smoke tests
# (which use `pydantic_ai.models.test.TestModel`) execute, so CI
# stays cheap and offline.  See `tests/eval/llm_eval.py` docstring.

def pytest_addoption(parser):
    parser.addoption(
        "--run-eval",
        action="store_true",
        default=False,
        help="Run LLM eval-harness tests against a real foundation model. "
             "Set MTG_LLM_MODEL or MTG_LLM_MODEL_<TASK> to pick the model.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-eval"):
        return
    skip_marker = pytest.mark.skip(
        reason="LLM eval test (real model) — pass --run-eval to run."
    )
    for item in items:
        if "llm_eval_real_model" in item.keywords:
            item.add_marker(skip_marker)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "llm_eval_real_model: marks LLM eval tests that hit a real model "
        "(skipped unless --run-eval is passed).",
    )
