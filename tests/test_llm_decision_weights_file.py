"""The decision scorer's weights can be committed as data, so every clone
and every CI run scores with the SAME model-derived weights without an
API key or a warmed cache.

`ai/llm_decision_scorer.weight(archetype, context)` resolves cache →
live call → `DEFAULT_WEIGHTS`. The cache is gitignored, the live call is
disabled in every sim (`MTG_LLM_DECISION_SCORER_OFFLINE=1`), so a model's
weights lived only on the box that warmed them. A committed
`ai/llm_decision_weights.json` (written by `tools/llm_cache_warm.py
--export`) now sits between the cache and the defaults table — the
"cached and committed" pattern `classify_oracle` already uses.

Rules pinned:
1. With the file present, a cold cache and the offline flag, `weight()`
   returns the file's row.
2. A pair absent from the file falls back to `DEFAULT_WEIGHTS` (and the
   "*" wildcard row), unchanged.
3. The file's keys are archetype + context only — never a deck or card
   name (the same contract probe the cache key carries).
4. Every weight in the file is a finite float.
"""
from __future__ import annotations

import json

import pytest

from ai import llm_decision_scorer as scorer


@pytest.fixture
def weights_file(tmp_path, monkeypatch):
    path = tmp_path / "llm_decision_weights.json"
    payload = {
        "model": "test:model",
        "prompt_version": "v2",
        "rows": [
            {"archetype": "ramp", "context": scorer.CTX_TRON_MANA_ADVANTAGE,
             "weight": 6.5, "confidence": 0.9, "rationale": "test row"},
        ],
    }
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    monkeypatch.setattr(scorer, "_WEIGHTS_FILE", path)
    scorer._reset_weights_file_cache()
    # A cold cache: make the SQLite lookup miss deterministically.
    monkeypatch.setattr(scorer, "_try_cache_only", lambda a, c: None)
    yield path
    scorer._reset_weights_file_cache()


def test_the_committed_file_row_is_used_with_a_cold_cache_and_the_offline_flag(weights_file):
    assert scorer.weight("ramp", scorer.CTX_TRON_MANA_ADVANTAGE) == 6.5


def test_a_pair_absent_from_the_file_falls_back_to_the_defaults_table(weights_file):
    assert scorer.weight("combo", scorer.CTX_AMULET_TITAN_MANA_BONUS) == \
        scorer.DEFAULT_WEIGHTS[("combo", scorer.CTX_AMULET_TITAN_MANA_BONUS)]
    assert scorer.weight("aggro", scorer.CTX_CASCADE_FREE_SPELL_VALUE) == \
        scorer.DEFAULT_WEIGHTS[("*", scorer.CTX_CASCADE_FREE_SPELL_VALUE)]


def test_a_missing_file_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    monkeypatch.setattr(scorer, "_WEIGHTS_FILE", tmp_path / "absent.json")
    scorer._reset_weights_file_cache()
    monkeypatch.setattr(scorer, "_try_cache_only", lambda a, c: None)
    try:
        assert scorer.weight("ramp", scorer.CTX_TRON_MANA_ADVANTAGE) == \
            scorer.DEFAULT_WEIGHTS[("ramp", scorer.CTX_TRON_MANA_ADVANTAGE)]
    finally:
        scorer._reset_weights_file_cache()


def test_the_export_writes_cached_rows_that_the_loader_reads_back(tmp_path, monkeypatch):
    """`tools/llm_cache_warm.py --export` round-trips: cached rows (weight,
    confidence, rationale) become the committed file, keyed by archetype
    and context, and the loader returns exactly those weights."""
    from types import SimpleNamespace
    from tools.llm_cache_warm import export_decision_scorer_weights

    def _fake_row(arch, ctx):
        if (arch, ctx) == ("ramp", scorer.CTX_TRON_MANA_ADVANTAGE):
            return SimpleNamespace(weight=7.0, confidence=0.8, rationale="seven lands of Tron")
        return None

    monkeypatch.setattr(scorer, "_try_cache_row", _fake_row)
    out = tmp_path / "weights.json"
    written = export_decision_scorer_weights(out)
    assert written["rows"] == 1 and written["model"] and written["prompt_version"]
    data = json.loads(out.read_text())
    assert data["rows"] == [{"archetype": "ramp", "context": scorer.CTX_TRON_MANA_ADVANTAGE,
                             "weight": 7.0, "confidence": 0.8,
                             "rationale": "seven lands of Tron"}]

    monkeypatch.setenv("MTG_LLM_DECISION_SCORER_OFFLINE", "1")
    monkeypatch.setattr(scorer, "_WEIGHTS_FILE", out)
    monkeypatch.setattr(scorer, "_try_cache_only", lambda a, c: None)
    scorer._reset_weights_file_cache()
    try:
        assert scorer.weight("ramp", scorer.CTX_TRON_MANA_ADVANTAGE) == 7.0
    finally:
        scorer._reset_weights_file_cache()


def test_the_decision_scorer_token_cap_covers_its_current_prompt():
    """The per-call input-token cap must exceed what the scorer's live
    prompt actually costs, or every warm call is refused and the warm
    silently skips (observed 2026-09-12: prompt v2 = 2951 input tokens
    against a 2500 cap sized for v1; 72 of 72 pairs skipped). The pin
    uses the measured figure; a prompt bump that crosses it must raise
    the cap in the same change."""
    from ai.llm_budgets import DEFAULT_TOKEN_CAPS
    MEASURED_PROMPT_V2_INPUT_TOKENS = 2951
    assert DEFAULT_TOKEN_CAPS["decision_scorer"] > MEASURED_PROMPT_V2_INPUT_TOKENS


def test_the_shipped_file_if_present_keys_by_archetype_and_context_only_and_is_finite():
    import math
    from decks.modern_meta import MODERN_DECKS
    path = scorer._WEIGHTS_FILE
    if not path.exists():
        pytest.skip("no committed weights file yet (warm run pending)")
    data = json.loads(path.read_text())
    assert data.get("model") and data.get("prompt_version")
    known_ctx = set(scorer.DEFAULT_WEIGHTS_CONTEXTS)
    for row in data["rows"]:
        assert set(row) >= {"archetype", "context", "weight"}
        assert row["context"] in known_ctx, row
        assert row["archetype"] not in MODERN_DECKS, "a deck name is not an archetype"
        assert math.isfinite(float(row["weight"])), row
