"""Phase 4C Week 4 — labeled oracle corpus acceptance gate.

Loads ``tests/fixtures/oracle_corpus_known_outputs.jsonl`` —
30 hand-labeled oracle texts spanning the Modern card pool —
and runs ``parse_oracle`` against each. The acceptance gate is
≥ 95% agreement on ``primary_effect`` (the most-load-bearing
field for downstream AI scoring).

Two run modes:

1. **Stub mode** (default, always run): uses StubBackend that
   returns the labeled `expected` JSON for each fixture. Verifies
   the corpus structure + the parser handles every shape we
   expect to see. This is a tier-1 contract test.

2. **Live mode** (conditional, ``MTG_LLM_MODEL_PATH`` set): runs
   the real Qwen 2.5 7B Q4_K_M backend. Verifies real-model
   accuracy meets the gate. This is the tier-2 acceptance test
   for promotion.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

import pytest

from ai.llm.oracle_parse import OracleEffect, parse_oracle, to_dict
from ai.llm.policy import LLMPolicy, StubBackend


CORPUS_PATH = (
    Path(__file__).parent / "fixtures" / "oracle_corpus_known_outputs.jsonl"
)


def _load_corpus() -> List[dict]:
    """Load the labeled corpus as a list of {name, oracle, expected}."""
    if not CORPUS_PATH.exists():
        return []
    rows = []
    with CORPUS_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ─── Tier 1: corpus structure and parser robustness ──────────────────


def test_corpus_loads():
    """The labeled corpus file exists and is non-trivial."""
    rows = _load_corpus()
    assert len(rows) >= 25, (
        f"Corpus must have at least 25 entries (currently "
        f"{len(rows)}). Acceptance gate scales with corpus size."
    )


def test_corpus_entries_have_required_fields():
    """Each row has name, oracle, expected fields."""
    for row in _load_corpus():
        assert "name" in row, row
        assert "oracle" in row, row
        assert "expected" in row, row
        exp = row["expected"]
        assert "primary_effect" in exp
        # amount, target, flags are optional / nullable


def test_corpus_expected_effects_are_in_vocabulary():
    """Every labeled primary_effect uses the canonical vocabulary."""
    valid = {
        "draw", "damage", "destroy", "exile", "counter", "tutor",
        "discard", "reanimate", "buff", "ramp", "cost_reduce",
        "lock", "passive", "unknown",
    }
    for row in _load_corpus():
        eff = row["expected"]["primary_effect"]
        assert eff in valid, (
            f"{row['name']} labeled with non-canonical effect "
            f"{eff!r}. Canonical: {valid}"
        )


# ─── Stub-mode acceptance: 100% trivially (uses labels as outputs) ────


class TestStubModeAcceptance:
    """Stub backend echoes the labeled `expected` for each card.

    Confirms:
      - Every fixture's `expected` is a valid JSON shape that
        round-trips through the parser
      - parse_oracle correctly recovers the same OracleEffect
      - The acceptance-gate plumbing works end-to-end
    """

    def test_every_fixture_round_trips(self):
        rows = _load_corpus()
        mismatches = []
        for row in rows:
            payload = json.dumps(row["expected"])
            backend = StubBackend(
                name=f"stub-{row['name']}",
                responder=lambda p, payload=payload: payload,
            )
            policy = LLMPolicy(backend=backend)
            parsed = parse_oracle(row["oracle"], policy)
            actual = to_dict(parsed)
            if actual["primary_effect"] != row["expected"]["primary_effect"]:
                mismatches.append((row["name"], actual, row["expected"]))
        assert not mismatches, (
            f"Round-trip mismatches: {mismatches[:5]}"
        )


# ─── Tier 2: live-model acceptance gate ──────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("MTG_LLM_MODEL_PATH"),
    reason="MTG_LLM_MODEL_PATH not set — Qwen / Phi GGUF required.",
)
def test_live_model_meets_acceptance_gate(tmp_path):
    """Run parse_oracle through a real model on the corpus.

    Acceptance: ≥ 95% agreement on primary_effect.

    Acceptance levels (per scoping doc):
      - 95% on Qwen 2.5 7B Q4_K_M : promote to default for
        review-flagged cards.
      - 90% : opt-in only.
      - <90% : do not promote; investigate prompt or labels.
    """
    from ai.llm.llama_cpp_backend import LlamaCppBackend
    backend = LlamaCppBackend()
    policy = LLMPolicy(backend=backend, cache_dir=tmp_path / "llm_cache")

    rows = _load_corpus()
    matches = 0
    mismatches = []
    for row in rows:
        try:
            parsed = parse_oracle(row["oracle"], policy)
        except Exception as e:
            mismatches.append((row["name"], "parse_error", str(e)))
            continue
        if parsed.primary_effect == row["expected"]["primary_effect"]:
            matches += 1
        else:
            mismatches.append((
                row["name"], parsed.primary_effect,
                row["expected"]["primary_effect"],
            ))

    rate = matches / len(rows)
    print(f"\nLive acceptance rate: {rate:.2%} "
          f"({matches}/{len(rows)})")
    if mismatches:
        print("First 5 mismatches:", mismatches[:5])
    assert rate >= 0.95, (
        f"Live model accuracy {rate:.2%} below 95% acceptance "
        f"gate. Mismatches: {mismatches[:10]}"
    )
