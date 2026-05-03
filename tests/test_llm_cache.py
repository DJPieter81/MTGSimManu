"""Tests for `ai.llm_cache` — SQLite-backed LLM response cache.

Phase I-1 of the cost-aware LLM strategy.  Every test here uses
`monkeypatch` to point `CACHE_DIR` / `CACHE_DB` at a tmp_path so
runs are isolated from each other and from any real cache the
operator has populated locally.

The schemas used as round-trip fixtures (`BugHypothesis`,
`SynthesizedGameplan`) are imported from `ai.llm_schemas` and used as
data only — no card-name conditionals enter the test logic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai import llm_cache
from ai.llm_schemas import BugHypothesis, SynthesizedGameplan


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint the cache at a unique tmp_path per test.

    `autouse=True` so every test in this module is automatically
    isolated — a forgotten fixture-application would otherwise let
    one test's writes leak into the next test's reads.
    """
    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    return cache_dir


def _hypothesis(symptom: str = "AI passed turn", confidence: float = 0.8) -> BugHypothesis:
    """Helper: build a minimal BugHypothesis for round-trip tests."""
    return BugHypothesis(
        observed_symptom=symptom,
        suspected_subsystem="ai.ev_player",
        failing_test_rule="main-phase action chosen when nonzero ev play exists",
        confidence=confidence,
    )


# ─── cache_key determinism ──────────────────────────────────────────


def test_cache_key_deterministic_across_dict_orders() -> None:
    """Two dicts with identical contents but different insertion
    order must produce the same cache key — order is not data."""
    a = {"deck": "Boros", "mainboard": {"Bolt": 4, "Guide": 4}}
    b = {"mainboard": {"Guide": 4, "Bolt": 4}, "deck": "Boros"}
    k_a = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, a)
    k_b = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, b)
    assert k_a == k_b


def test_cache_key_changes_on_input_change() -> None:
    """Different inputs must produce different keys."""
    k1 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, {"deck": "A"})
    k2 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, {"deck": "B"})
    assert k1 != k2


def test_cache_key_changes_on_model_change() -> None:
    """Same input, different model identifier → different key.
    Switching models must invalidate cache."""
    inp = {"deck": "Boros"}
    k1 = llm_cache.cache_key("synth_gameplan", "anthropic:haiku", 1, inp)
    k2 = llm_cache.cache_key("synth_gameplan", "anthropic:sonnet", 1, inp)
    assert k1 != k2


def test_cache_key_changes_on_prompt_version() -> None:
    """Bumping the prompt version must invalidate prior cache entries."""
    inp = {"deck": "Boros"}
    k1 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, inp)
    k2 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 2, inp)
    assert k1 != k2


def test_cache_key_changes_on_task() -> None:
    """Same input, different task label → different key.  Two agents
    that happen to be configured with the same model + version must
    not collide."""
    inp = {"x": 1}
    k1 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, inp)
    k2 = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, inp)
    assert k1 != k2


def test_cache_key_accepts_pydantic_model_input() -> None:
    """A pydantic model input round-trips through model_dump and
    produces the same key as the equivalent dict."""
    h = _hypothesis()
    k_model = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, h)
    k_dict = llm_cache.cache_key(
        "diagnose_replay", "anthropic:m", 1, h.model_dump()
    )
    assert k_model == k_dict


def test_cache_key_accepts_string_input() -> None:
    """A bare string prompt is wrapped consistently — two calls with
    the same string produce the same key."""
    k1 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, "hello")
    k2 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, "hello")
    assert k1 == k2
    k3 = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, "world")
    assert k1 != k3


# ─── get / store / hit-count behaviour ──────────────────────────────


def test_get_cached_miss_returns_none() -> None:
    """Fresh cache → look up any key → None."""
    key = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, {"x": 1})
    assert llm_cache.get_cached(key, BugHypothesis) is None


def test_store_then_get_cached_returns_same_object() -> None:
    """Round-trip: store a BugHypothesis, get it back, all fields
    match."""
    inp = {"replay": "log lines"}
    key = llm_cache.cache_key("diagnose_replay", "anthropic:haiku", 1, inp)
    out = _hypothesis(symptom="Spell countered with mana up", confidence=0.91)

    llm_cache.store(
        key,
        task="diagnose_replay",
        model="anthropic:haiku",
        prompt_version=1,
        input_hash=llm_cache._input_hash({"input": inp}),
        output=out,
    )
    got = llm_cache.get_cached(key, BugHypothesis)
    assert got is not None
    assert got.observed_symptom == out.observed_symptom
    assert got.confidence == pytest.approx(out.confidence)
    assert got.suspected_subsystem == out.suspected_subsystem
    assert got.failing_test_rule == out.failing_test_rule


def test_round_trip_synthesized_gameplan() -> None:
    """A more complex schema (SynthesizedGameplan with nested goals)
    round-trips through the cache without losing fields."""
    inp = {"deck_name": "Boros Mock", "mainboard": {"Lightning Bolt": 4}}
    key = llm_cache.cache_key("synth_gameplan", "anthropic:sonnet", 1, inp)
    plan = SynthesizedGameplan(
        deck_name="Boros Mock",
        archetype="aggro",
        goals=[
            {
                "goal_type": "CURVE_OUT",
                "description": "deploy",
                "card_roles": {"enablers": ["Goblin Guide"]},
            }
        ],  # type: ignore[arg-type]
    )
    llm_cache.store(
        key,
        task="synth_gameplan",
        model="anthropic:sonnet",
        prompt_version=1,
        input_hash=llm_cache._input_hash({"input": inp}),
        output=plan,
    )
    got = llm_cache.get_cached(key, SynthesizedGameplan)
    assert got is not None
    assert got.deck_name == "Boros Mock"
    assert got.archetype == "aggro"
    assert len(got.goals) == 1
    assert got.goals[0].goal_type == "CURVE_OUT"


def test_get_cached_increments_hit_count() -> None:
    """Each successful lookup must increment hit_count.  Inspect via
    cache_stats."""
    key = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, {"x": 1})
    llm_cache.store(
        key,
        task="diagnose_replay",
        model="anthropic:m",
        prompt_version=1,
        input_hash="h",
        output=_hypothesis(),
    )
    # initial total_hits == 0 (created by store, never read)
    assert llm_cache.cache_stats()["total_hits"] == 0
    llm_cache.get_cached(key, BugHypothesis)
    assert llm_cache.cache_stats()["total_hits"] == 1
    llm_cache.get_cached(key, BugHypothesis)
    assert llm_cache.cache_stats()["total_hits"] == 2


def test_store_idempotent_on_same_key() -> None:
    """Two stores with the same cache_key produce one row, not two.
    The first stored output wins (ON CONFLICT DO NOTHING)."""
    key = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, {"x": 1})
    h1 = _hypothesis(symptom="first", confidence=0.5)
    h2 = _hypothesis(symptom="second", confidence=0.99)

    llm_cache.store(
        key,
        task="diagnose_replay",
        model="anthropic:m",
        prompt_version=1,
        input_hash="h",
        output=h1,
    )
    llm_cache.store(
        key,
        task="diagnose_replay",
        model="anthropic:m",
        prompt_version=1,
        input_hash="h",
        output=h2,
    )
    stats = llm_cache.cache_stats()
    assert stats["entries"] == 1
    got = llm_cache.get_cached(key, BugHypothesis)
    assert got is not None
    # First write wins.
    assert got.observed_symptom == "first"


# ─── clear_cache ────────────────────────────────────────────────────


def test_clear_cache_by_task_preserves_others() -> None:
    """`clear_cache(task='X')` removes only X's rows; other tasks'
    entries remain intact."""
    k_synth = llm_cache.cache_key("synth_gameplan", "anthropic:m", 1, {"x": 1})
    k_diag = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, {"x": 1})

    plan = SynthesizedGameplan(
        deck_name="X",
        archetype="aggro",
        goals=[{"goal_type": "CURVE_OUT", "description": ""}],  # type: ignore[arg-type]
    )
    llm_cache.store(
        k_synth,
        task="synth_gameplan",
        model="anthropic:m",
        prompt_version=1,
        input_hash="h",
        output=plan,
    )
    llm_cache.store(
        k_diag,
        task="diagnose_replay",
        model="anthropic:m",
        prompt_version=1,
        input_hash="h",
        output=_hypothesis(),
    )

    deleted = llm_cache.clear_cache(task="synth_gameplan")
    assert deleted == 1
    # diag entry still present.
    assert llm_cache.get_cached(k_diag, BugHypothesis) is not None
    # synth entry gone.
    assert llm_cache.get_cached(k_synth, SynthesizedGameplan) is None


def test_clear_cache_all() -> None:
    """`clear_cache()` (no arg) removes every row."""
    key = llm_cache.cache_key("diagnose_replay", "anthropic:m", 1, {"x": 1})
    llm_cache.store(
        key,
        task="diagnose_replay",
        model="anthropic:m",
        prompt_version=1,
        input_hash="h",
        output=_hypothesis(),
    )
    deleted = llm_cache.clear_cache()
    assert deleted == 1
    assert llm_cache.cache_stats()["entries"] == 0


# ─── cache_stats aggregation ───────────────────────────────────────


def test_cache_stats_aggregates() -> None:
    """Populate entries across two tasks and two models, verify
    by_model / by_task aggregation matches."""
    plan = SynthesizedGameplan(
        deck_name="X",
        archetype="aggro",
        goals=[{"goal_type": "CURVE_OUT", "description": ""}],  # type: ignore[arg-type]
    )
    # synth: two rows, both haiku.
    for i, model_id in enumerate(["anthropic:haiku", "anthropic:haiku"]):
        k = llm_cache.cache_key("synth_gameplan", model_id, 1, {"i": i})
        llm_cache.store(
            k,
            task="synth_gameplan",
            model=model_id,
            prompt_version=1,
            input_hash="h",
            output=plan,
        )
    # diagnose: one row, sonnet.
    k = llm_cache.cache_key("diagnose_replay", "anthropic:sonnet", 1, {"r": 1})
    llm_cache.store(
        k,
        task="diagnose_replay",
        model="anthropic:sonnet",
        prompt_version=1,
        input_hash="h",
        output=_hypothesis(),
    )

    stats_all = llm_cache.cache_stats()
    assert stats_all["entries"] == 3
    assert stats_all["by_task"] == {"synth_gameplan": 2, "diagnose_replay": 1}
    assert stats_all["by_model"] == {
        "anthropic:haiku": 2,
        "anthropic:sonnet": 1,
    }

    # Filter by task — entries narrows, but by_task still reports the
    # whole-cache distribution per the documented contract.
    stats_synth = llm_cache.cache_stats(task="synth_gameplan")
    assert stats_synth["entries"] == 2
    assert stats_synth["by_model"] == {"anthropic:haiku": 2}
    assert stats_synth["by_task"] == {"synth_gameplan": 2, "diagnose_replay": 1}


def test_cache_stats_empty_cache() -> None:
    """Stats over an empty cache return zeros, not crashes."""
    stats = llm_cache.cache_stats()
    assert stats == {
        "entries": 0,
        "total_hits": 0,
        "by_model": {},
        "by_task": {},
    }
