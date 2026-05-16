"""Contract tests for `ai.llm_decision_scorer.weight`.

Phase 1 of the project-direction refactor.  These tests pin the
rules of the new helper that replaces ~10 archetype-tied scaling
constants from ``ai/scoring_constants.py`` with cached LLM weights:

  1. Repeat calls with the same (archetype, context) hit the cache —
     determinism property.
  2. Budget exhaustion / LLM unavailability falls back to a neutral
     weight (or the offline default table); the sim never crashes.
  3. The cache key does NOT include the raw deck name — abstraction-
     contract probe.  Two decks of the same archetype share rows.
  4. The helper always returns a finite float (never NaN/Inf).

All tests use ``monkeypatch`` to repoint the SQLite cache at a
``tmp_path``; no test touches the operator's real cache.  No real
LLM call is made — we either use pydantic-ai's TestModel or directly
seed the cache via ``ai.llm_cache.store``.

These tests reference the rule the helper enforces, not the cards
or decks the rule will eventually score.  No card-name strings
appear in the test fixtures; archetype labels and context tags are
the only data.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from ai import llm_cache, llm_decision_scorer
from ai.llm_schemas import DecisionScoringWeights


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint the SQLite cache at a unique tmp_path for every test
    in this module.  Also forces the scorer into offline mode so the
    live LLM call is suppressed (deterministic CI).  ``autouse=True``
    so a forgotten fixture import does not leak cache state.
    """
    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    # Default: offline.  Tests that need to exercise the live-call
    # branch flip this env var off via their own monkeypatch.
    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    # Reset the module-level lazy agent cache so each test starts
    # fresh (otherwise a previous test's mock object leaks).
    monkeypatch.setattr(llm_decision_scorer, "_AGENT", None)
    monkeypatch.setattr(llm_decision_scorer, "_AGENT_BUILD_FAILED", False)
    return cache_dir


def _seed_cache(archetype: str, context: str, weight: float) -> None:
    """Store a deterministic ``DecisionScoringWeights`` row at the
    cache key the helper will look up for ``(archetype, context)``.

    Mirrors what ``tools/llm_cache_warm.py`` does in production when
    it pre-populates the cache from a real LLM call.
    """
    from ai.llm_models import select_model
    from ai.llm_prompts import latest_version
    model = select_model("decision_scorer")
    version = latest_version("decision_scorer")
    payload = {"archetype": archetype, "context": context}
    key = llm_cache.cache_key(
        "decision_scorer", model, version, payload,
    )
    obj = DecisionScoringWeights(
        weight=weight, confidence=0.9, rationale="seeded for test"
    )
    llm_cache.store(
        key,
        task="decision_scorer",
        model=model,
        prompt_version=version,
        input_hash=llm_cache._input_hash({"input": payload}),
        output=obj,
    )


# ─── 1. Repeat call hits cache ──────────────────────────────────────


def test_repeat_call_with_same_context_hits_cache() -> None:
    """Second call returns the byte-identical weight that the first
    call returned, without any live LLM round-trip.

    The cache is the determinism layer per the user directive:
    "Determinism via cache."  This test pins that property.
    """
    _seed_cache("storm", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST, 8.0)
    w1 = llm_decision_scorer.weight(
        "storm", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST
    )
    w2 = llm_decision_scorer.weight(
        "storm", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST
    )
    # Byte-identical — same float, not just approximate.
    assert w1 == w2 == 8.0


# ─── 2. Budget exhausted → fallback ─────────────────────────────────


def test_budget_exhausted_falls_back_to_neutral_weight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the live LLM call would raise ``BudgetExceededError`` AND
    no default-table entry matches the requested ``(archetype,
    context)`` pair, the helper returns ``NEUTRAL_WEIGHT`` (1.0) and
    does NOT propagate the exception.

    Sim continuity is load-bearing here: a budget block must never
    crash the sim — it should degrade gracefully to neutral scoring.
    """
    # Force the live-call branch on (override the autouse offline env).
    monkeypatch.delenv("MTG_LLM_DECISION_SCORER_OFFLINE", raising=False)

    # Install a mock agent whose run_sync raises a budget error.
    from ai.llm_budgets import BudgetExceededError

    class _RaisingAgent:
        def run_sync(self, prompt, **kwargs):
            raise BudgetExceededError("decision_scorer", 1.99, 2.00, 0.05)

    monkeypatch.setattr(llm_decision_scorer, "_AGENT", _RaisingAgent())

    # Use an archetype + context pair NOT in the DEFAULT_WEIGHTS
    # table — so the only remaining floor is the neutral fallback.
    result = llm_decision_scorer.weight(
        "midrange", "context_with_no_default_entry_xyz"
    )
    assert result == llm_decision_scorer.NEUTRAL_WEIGHT == 1.0


# ─── 3. Cache key excludes raw deck name ─────────────────────────────


def test_deck_name_does_not_appear_in_cache_key() -> None:
    """Abstraction-contract probe: the cache key must derive from
    ``(archetype, decision_context)`` ONLY — never the raw deck name.

    Why this matters: if the key included deck names, we'd silently
    re-encode the deck-name-conditional anti-pattern (the same one
    the abstraction contract bans in `if deck_name == "X"` form).
    Two decks of the same archetype must share one cache row, so the
    LLM-tuned weights generalise across decks within an archetype.

    Operationally: we seed the cache for one archetype, then assert
    that calling the helper with the same archetype + context but
    with the deck name as a side-channel (e.g. by spinning up the
    helper from a different "deck context") still resolves to the
    same cache row.
    """
    _seed_cache("ramp", llm_decision_scorer.CTX_TRON_MANA_ADVANTAGE, 4.0)
    # Both calls pass the same archetype + context; if a hidden deck
    # name were salting the key, the second call would miss and fall
    # through to a different value.
    w1 = llm_decision_scorer.weight(
        "ramp", llm_decision_scorer.CTX_TRON_MANA_ADVANTAGE
    )
    w2 = llm_decision_scorer.weight(
        "ramp", llm_decision_scorer.CTX_TRON_MANA_ADVANTAGE
    )
    assert w1 == w2 == 4.0

    # Also assert that the cache key the helper builds — when
    # introspected directly — is computed only from
    # ``{"archetype": ..., "context": ...}`` and nothing else.  This
    # makes the contract structural, not behavioural-only.
    inp = llm_decision_scorer._cache_input(
        "ramp", llm_decision_scorer.CTX_TRON_MANA_ADVANTAGE
    )
    assert set(inp.keys()) == {"archetype", "context"}, (
        "Cache-key input has extra fields — only (archetype, context) "
        "may participate in the key, per the abstraction contract."
    )


# ─── 4. Always returns a finite float ───────────────────────────────


def test_decision_scorer_called_with_valid_archetype_returns_finite_float() -> None:
    """Basic contract: the helper never returns NaN or +/-Inf.

    Every code path — cache hit, live LLM, default-table lookup,
    neutral fallback — must yield a finite float, because the call
    site multiplies the return value into an EV score.  Non-finite
    arithmetic would NaN-propagate through the rest of the scoring
    pipeline.
    """
    import math
    # Cache miss + no LLM + no default-table entry → neutral fallback.
    result = llm_decision_scorer.weight("aggro", "no_such_context_xyz")
    assert isinstance(result, float)
    assert math.isfinite(result)

    # Cache hit path.
    _seed_cache("storm", llm_decision_scorer.CTX_CYCLING_GY_URGENCY, 6.0)
    result = llm_decision_scorer.weight(
        "storm", llm_decision_scorer.CTX_CYCLING_GY_URGENCY
    )
    assert isinstance(result, float)
    assert math.isfinite(result)
    assert result == 6.0

    # Default-table fallback (cache empty, no LLM).
    result = llm_decision_scorer.weight(
        "combo", llm_decision_scorer.CTX_AMULET_TITAN_MANA_BONUS
    )
    assert isinstance(result, float)
    assert math.isfinite(result)
    # The default-table value comes from DEFAULT_WEIGHTS; that's an
    # implementation detail, but the *finiteness* property is the
    # one being pinned here.


def test_helper_handles_nan_or_inf_from_llm_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a mis-configured LLM ever returns NaN or Inf, the helper
    must reject it and fall through to the default / neutral floor
    rather than poisoning downstream arithmetic.
    """
    import math
    monkeypatch.delenv("MTG_LLM_DECISION_SCORER_OFFLINE", raising=False)

    class _NaNAgent:
        class _Result:
            class _Out:
                weight = float("nan")
                confidence = 0.5
                rationale = ""
            output = _Out()

        def run_sync(self, prompt, **kwargs):
            return self._Result()

    monkeypatch.setattr(llm_decision_scorer, "_AGENT", _NaNAgent())

    # The (archetype, context) pair has no default-table entry, so
    # rejection of NaN must surface NEUTRAL_WEIGHT, not NaN.
    result = llm_decision_scorer.weight(
        "midrange", "context_with_no_default_entry_xyz"
    )
    assert math.isfinite(result)
    assert result == llm_decision_scorer.NEUTRAL_WEIGHT
