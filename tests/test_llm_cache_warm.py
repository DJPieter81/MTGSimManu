"""Phase 4C — cache pre-warm tool tests.

Verifies tools/llm_cache_warm.py can iterate the SB and oracle
corpus, write cache entries via StubBackend, and is idempotent
(re-runs skip already-cached entries).

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai.llm.policy import LLMPolicy, StubBackend
from tools.llm_cache_warm import (
    warm_oracle_parser,
    warm_sb_advisor,
    _iter_oracle_corpus,
)


def _stub_policy(tmp_path: Path) -> LLMPolicy:
    """Build a policy whose stub backend always returns a valid
    JSON shape for both schemas (oracle and SB)."""
    canned = '{"swaps": [], "primary_effect": "unknown", "amount": null, "target": null, "flags": []}'
    backend = StubBackend(
        name="stub-warm-test",
        responder=lambda p: canned,
    )
    return LLMPolicy(backend=backend, cache_dir=tmp_path)


# ─── Oracle parser warming ───────────────────────────────────────────


class TestWarmOracleParser:
    def test_iterates_corpus(self):
        rows = list(_iter_oracle_corpus())
        assert len(rows) >= 25, (
            f"Oracle corpus must have ≥ 25 entries; got {len(rows)}"
        )

    def test_warm_writes_cache_entries(self, tmp_path: Path):
        policy = _stub_policy(tmp_path)
        counts = warm_oracle_parser(policy)
        assert counts["total"] >= 25
        # First run: every entry warmed (cold cache).
        assert counts["warmed"] >= 25
        assert counts["skipped"] == 0
        assert counts["errors"] == 0

    def test_idempotent_second_run(self, tmp_path: Path):
        policy = _stub_policy(tmp_path)
        first = warm_oracle_parser(policy)
        # Re-run with a fresh policy pointing at the same cache.
        policy2 = _stub_policy(tmp_path)
        second = warm_oracle_parser(policy2)
        # Second run: every entry skipped (warm cache).
        assert second["skipped"] == first["total"]
        assert second["warmed"] == 0
        assert second["errors"] == 0


# ─── SB advisor warming ──────────────────────────────────────────────


class TestWarmSBAdvisor:
    def test_warm_writes_cache_entries(self, tmp_path: Path):
        policy = _stub_policy(tmp_path)
        # Use a small subset so the test stays fast.
        from decks.modern_meta import MODERN_DECKS
        names = list(MODERN_DECKS.keys())[:3]
        counts = warm_sb_advisor(policy, deck_names=names)
        # 3 decks × 2 opponents (excluding self) = 6 matchups.
        assert counts["total"] == 6
        assert counts["warmed"] == 6
        assert counts["errors"] == 0

    def test_idempotent_second_run(self, tmp_path: Path):
        from decks.modern_meta import MODERN_DECKS
        names = list(MODERN_DECKS.keys())[:3]
        policy = _stub_policy(tmp_path)
        first = warm_sb_advisor(policy, deck_names=names)
        policy2 = _stub_policy(tmp_path)
        second = warm_sb_advisor(policy2, deck_names=names)
        assert second["skipped"] == first["total"]
        assert second["warmed"] == 0
