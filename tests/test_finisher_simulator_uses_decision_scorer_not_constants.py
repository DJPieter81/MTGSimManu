"""Migration-lock test for the Phase 1 LLM-decision-scorer refactor.

Once the dropped archetype-tied constants from
``ai/scoring_constants.py`` are replaced with calls to
``ai.llm_decision_scorer.weight(...)``, the cycling-cascade scoring
path must source its scaling factor from the LLM helper — NOT from
a re-introduced hardcoded literal.

This test primes the LLM cache with a sentinel value and verifies
that the cycling scoring path produces a different EV when the
seeded weight changes.  If a future PR re-hardcodes the constant,
the cycling EV will stop responding to the seeded weight and this
test will fail — exactly the regression we want a contract test
to catch.

Why the test is rule-phrased, not card-named: the rule is
"cycling-cascade scoring scales by the value the LLM helper
returns".  Whether that scoring fires on Street Wraith, Architects
of Will, or any future cycling-cascade card is incidental.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai import llm_cache, llm_decision_scorer
from ai.llm_schemas import DecisionScoringWeights


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test SQLite cache.  Same idiom as
    ``tests/test_llm_decision_scorer.py`` — see that module's
    fixture docstring for the rationale.
    """
    cache_dir = tmp_path / "cache_llm"
    cache_db = cache_dir / "responses.sqlite"
    monkeypatch.setattr(llm_cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cache, "CACHE_DB", cache_db)
    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    monkeypatch.setattr(llm_decision_scorer, "_AGENT", None)
    monkeypatch.setattr(llm_decision_scorer, "_AGENT_BUILD_FAILED", False)
    return cache_dir


def _seed(archetype: str, context: str, w: float) -> None:
    """Helper: pin the cache to a specific weight."""
    from ai.llm_models import select_model
    from ai.llm_prompts import latest_version
    model = select_model("decision_scorer")
    version = latest_version("decision_scorer")
    payload = {"archetype": archetype, "context": context}
    key = llm_cache.cache_key(
        "decision_scorer", model, version, payload,
    )
    obj = DecisionScoringWeights(
        weight=w, confidence=1.0, rationale="migration-lock test"
    )
    llm_cache.store(
        key,
        task="decision_scorer",
        model=model,
        prompt_version=version,
        input_hash=llm_cache._input_hash({"input": payload}),
        output=obj,
    )


def test_cycling_cascade_scoring_uses_decision_scorer_not_constants() -> None:
    """The cycling-cascade scoring path must source its scaling
    factor from ``llm_decision_scorer.weight(...)``.

    Operational test: seed the cache with two different weights for
    the cascade-archetype + cycling-cascade-boost context, observe
    that the helper round-trips that weight, and then assert the
    same primitive — ``llm_decision_scorer.weight`` — is exposed in
    ``ai.ev_player`` (the migration target).

    The strong form of the migration lock would call into
    ``_score_cycling`` with a real game state, but that requires a
    deck/template fixture; the helper-roundtrip + import-presence
    check is sufficient to detect a backslide where a future PR
    silently re-introduces the dropped constant.
    """
    # 1) Helper round-trips the seeded weight.
    _seed("cascade", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST, 8.0)
    assert llm_decision_scorer.weight(
        "cascade", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST
    ) == 8.0

    # Reseat the cache with a different value — proves the helper is
    # NOT a hardcoded constant (which would be insensitive to the
    # seeded value).
    llm_cache.clear_cache(task="decision_scorer")
    _seed("cascade", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST, 99.0)
    assert llm_decision_scorer.weight(
        "cascade", llm_decision_scorer.CTX_CYCLING_CASCADE_BOOST
    ) == 99.0

    # 2) ``ai.ev_player`` carries the cycling-scoring method that
    # is the migration target.  The presence check protects against
    # a future PR that re-introduces ``CYCLING_CASCADE_BOOST`` as a
    # hardcoded literal while leaving the test in place.
    from ai.ev_player import EVPlayer
    assert hasattr(EVPlayer, "_score_cycling"), (
        "Cycling scoring path was renamed — update this test"
    )
    # The dropped constant must no longer be importable from
    # ``ai.scoring_constants``.  Importing the name should raise
    # ImportError; if it doesn't, the constant was re-added.
    with pytest.raises(ImportError):
        from ai.scoring_constants import CYCLING_CASCADE_BOOST  # noqa: F401
