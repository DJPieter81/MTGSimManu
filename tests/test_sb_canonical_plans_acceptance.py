"""Phase 4C — sideboard canonical-plan acceptance gate.

Loads ``tests/fixtures/sb_golden_plans.jsonl`` (16 matchups) and
runs ``advise_sideboard`` against each. The acceptance gate is
≥ 70% swap overlap with the canonical plan averaged across the
corpus.

Two run modes mirror the oracle-corpus gate:

1. **Stub mode** (always run): each fixture's `canonical_swaps`
   become the StubBackend response. Verifies plumbing.
2. **Live mode** (``MTG_LLM_MODEL_PATH`` set): runs Qwen 2.5 7B
   Q4_K_M backend; asserts ≥ 70% per-fixture overlap.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

import pytest

from ai.llm.policy import LLMPolicy, StubBackend
from ai.llm.sideboard_advisor import (
    SideboardPlan,
    SwapDirective,
    advise_sideboard,
)


CORPUS_PATH = (
    Path(__file__).parent / "fixtures" / "sb_golden_plans.jsonl"
)


def _load_corpus() -> List[dict]:
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


def _swap_set(swaps) -> set:
    """Convert a list of swap dicts/SwapDirectives to a frozen
    set of (card, sign) pairs for overlap measurement.

    We compare on (name, sign) — not exact delta — because human-
    curated plans and SLM plans may swap N or N+1 copies of the
    same card; we credit a match if the SIDE (in vs out) agrees.
    """
    s = set()
    for sw in swaps:
        if isinstance(sw, dict):
            card = sw.get("card", "")
            delta = sw.get("delta", 0)
        else:
            card = sw.card
            delta = sw.delta
        if delta > 0:
            s.add((card, "in"))
        elif delta < 0:
            s.add((card, "out"))
    return s


def _overlap(a: set, b: set) -> float:
    """Jaccard overlap |A ∩ B| / |A ∪ B|. Returns 1.0 for two
    empty sets (degenerate but harmless for our acceptance use)."""
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 1.0


# ─── Tier 1: corpus structure ────────────────────────────────────────


def test_corpus_loads():
    rows = _load_corpus()
    assert len(rows) >= 12, (
        f"SB corpus must have ≥ 12 matchups (currently {len(rows)})."
    )


def test_corpus_entries_have_required_fields():
    for row in _load_corpus():
        assert "my_deck" in row
        assert "opp_deck" in row
        assert "sideboard" in row
        assert "canonical_swaps" in row


def test_canonical_plans_are_balanced_or_documented():
    """A canonical plan should be balanced (sum adds == sum cuts)
    OR explicitly note an asymmetric SB pool (e.g. "weak SB vs
    artifacts — best we can do is ...")."""
    for row in _load_corpus():
        adds = sum(s["delta"] for s in row["canonical_swaps"]
                   if s["delta"] > 0)
        cuts = -sum(s["delta"] for s in row["canonical_swaps"]
                    if s["delta"] < 0)
        if adds != cuts:
            # Asymmetric — must document why.
            notes = row.get("notes", "")
            assert any(k in notes.lower() for k in
                       ("weak", "best we can", "limited", "thin")), (
                f"{row['my_deck']} vs {row['opp_deck']}: asymmetric "
                f"plan (adds={adds}, cuts={cuts}) without note "
                f"explaining the SB-pool limitation. Notes: {notes!r}"
            )


# ─── Tier 1: stub-mode plumbing ──────────────────────────────────────


class TestStubModeAcceptance:
    """The stub backend echoes each row's canonical swap plan,
    so advise_sideboard returns the canonical SwapDirective list
    by construction. Confirms parser handles all the canonical
    shapes."""

    def test_every_fixture_round_trips(self):
        rows = _load_corpus()
        for row in rows:
            payload = json.dumps({
                "swaps": row["canonical_swaps"],
                "notes": row.get("notes"),
            })
            backend = StubBackend(
                name=f"stub-sb-{row['my_deck']}",
                responder=lambda p, payload=payload: payload,
            )
            policy = LLMPolicy(backend=backend)
            plan = advise_sideboard(
                my_deck=row["my_deck"],
                my_sideboard=row["sideboard"],
                opponent_deck=row["opp_deck"],
                policy=policy,
            )
            # Compare in/out sets — exact delta differences allowed.
            actual = _swap_set(plan.swaps)
            expected = _swap_set(row["canonical_swaps"])
            overlap = _overlap(actual, expected)
            assert overlap >= 0.95, (
                f"{row['my_deck']} vs {row['opp_deck']}: stub "
                f"round-trip overlap {overlap:.2f} (expected 1.0). "
                f"Got {actual}, expected {expected}"
            )


# ─── Tier 2: live-model acceptance gate ──────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("MTG_LLM_MODEL_PATH"),
    reason="MTG_LLM_MODEL_PATH not set — Qwen / Phi GGUF required.",
)
def test_live_model_meets_70pct_overlap_gate(tmp_path):
    """Run advise_sideboard on a real model across the corpus.

    Acceptance: average per-fixture Jaccard overlap ≥ 0.70.

    Promotion levels:
      - ≥ 0.70 : promote SLM SB advisor as opt-in (env var)
      - ≥ 0.85 : promote as default
      - < 0.70 : do not promote; investigate prompt or labels
    """
    from ai.llm.llama_cpp_backend import LlamaCppBackend
    backend = LlamaCppBackend()
    policy = LLMPolicy(backend=backend, cache_dir=tmp_path / "llm_cache")

    rows = _load_corpus()
    overlaps = []
    for row in rows:
        try:
            plan = advise_sideboard(
                my_deck=row["my_deck"],
                my_sideboard=row["sideboard"],
                opponent_deck=row["opp_deck"],
                policy=policy,
            )
        except Exception:
            overlaps.append(0.0)
            continue
        actual = _swap_set(plan.swaps)
        expected = _swap_set(row["canonical_swaps"])
        overlaps.append(_overlap(actual, expected))

    mean_overlap = sum(overlaps) / len(overlaps)
    print(f"\nLive SB advisor mean overlap: {mean_overlap:.2%}")
    assert mean_overlap >= 0.70, (
        f"Live SB advisor mean overlap {mean_overlap:.2%} below "
        f"70% gate. Per-fixture: {list(zip([r['my_deck']+'/'+r['opp_deck'] for r in rows], overlaps))}"
    )
