"""Phase 4C Week-4 — SB plan overlap acceptance gate.

Acceptance gate from ``docs/research/2026-05_phase_4c_slm_scoping.md``:

> | SB plan overlap | ``tests/test_llm_sb_plan_overlap.py`` — 16 matchups | < 60 sec (cache) / ~8 min (cold) |
>
> If SB advisor matches canonical plans on > 70% of matchups,
> promote to opt-in production via env var ``SB_SOLVER=slm``.

Mechanism (rule-phrased)
------------------------
For each canonical (deck1, deck2, sideboard, expected_swaps)
fixture line, invoke ``advise_sideboard`` against the configured
SLM backend. A "swap" is keyed by ``(card_name, sign(delta))`` —
a +2 of Wear//Tear matches a +1 of Wear//Tear (same card, same
direction); a -2 cut of Blood Moon does NOT match a +2 add of
Blood Moon. Overlap per matchup =
``|advisor ∩ golden| / |golden|``. The test asserts the average
overlap across all 16 fixtures is at least the threshold from the
scoping doc (70%).

Skip behavior
-------------
The SLM backend (``llama_cpp``) is optional infrastructure. When
no model is configured (``MTG_LLM_MODEL_PATH`` unset, or
``llama_cpp`` not installed, or backend invocation raises
``BackendUnavailable``), this gate skips with a clear reason.
That is the expected CI behavior; the gate runs only on machines
with a downloaded GGUF model.

Reference: docs/research/2026-05_phase_4c_slm_scoping.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, List, Set, Tuple

import pytest

from ai.llm.policy import BackendUnavailable, LLMPolicy
from ai.llm.sideboard_advisor import SwapDirective, advise_sideboard


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "sb_golden_plans.jsonl"
)

# Threshold from docs/research/2026-05_phase_4c_slm_scoping.md
# acceptance gate: "SLM output overlaps >= 70% of swaps with the
# canonical plan" / "If SB advisor matches canonical plans on
# > 70% of matchups, promote to opt-in production".
OVERLAP_THRESHOLD = 0.70  # magic-allow: scoping-doc acceptance gate


# ─── Fixture loader ──────────────────────────────────────────────────


def _load_fixture() -> List[dict]:
    """Read the JSONL golden-plan corpus into a list of dicts."""
    if not FIXTURE_PATH.exists():
        pytest.fail(
            f"Golden-plan fixture missing at {FIXTURE_PATH}. "
            "See docs/research/2026-05_phase_4c_slm_scoping.md "
            "deliverable #6."
        )
    entries: List[dict] = []
    with FIXTURE_PATH.open("r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


# ─── Backend availability ────────────────────────────────────────────


def _make_live_policy() -> LLMPolicy:
    """Build an ``LLMPolicy`` backed by the configured llama.cpp
    model. Raises ``BackendUnavailable`` (caught by the caller and
    converted to a pytest skip) when ``llama_cpp`` is not installed
    or no model file is configured.
    """
    # Local import: the llama_cpp_backend module imports cleanly
    # even without llama_cpp installed (lazy import inside
    # _ensure_loaded), but we want a clear skip path either way.
    from ai.llm.llama_cpp_backend import make_backend_from_env

    backend = make_backend_from_env()
    if backend is None:
        raise BackendUnavailable(
            "MTG_LLM_MODEL_PATH not set; SB advisor gate cannot "
            "exercise a live backend."
        )
    return LLMPolicy(backend=backend, cache_dir=Path(".cache/llm_responses"))


# ─── Overlap math ────────────────────────────────────────────────────


def _swap_keys(swaps: Iterable) -> Set[Tuple[str, int]]:
    """Project a swap list to a set of ``(card_name, sign)`` keys.

    The sign is what defines a "matching" swap: a golden +2 of
    Wear//Tear matches an advisor +1 of Wear//Tear (both adds of
    the same card), but a +2 add of Blood Moon does NOT match a
    -2 cut of Blood Moon. Magnitude is intentionally ignored — the
    Phase 4C gate measures *what* the advisor brings in/out, not
    *how many*.
    """
    keys: Set[Tuple[str, int]] = set()
    for s in swaps:
        # Tolerate dict-form (golden fixture) and SwapDirective
        # (advisor output) uniformly.
        if isinstance(s, SwapDirective):
            card, delta = s.card, s.delta
        else:
            card, delta = s["card"], int(s["delta"])
        if delta == 0:
            continue
        sign = 1 if delta > 0 else -1
        keys.add((card.strip(), sign))
    return keys


def _overlap_fraction(
    advisor_swaps: Iterable, golden_swaps: Iterable
) -> float:
    """|advisor ∩ golden| / |golden|. Returns 1.0 if golden is
    empty (vacuously satisfied) and 0.0 if advisor is empty but
    golden is not."""
    golden_keys = _swap_keys(golden_swaps)
    if not golden_keys:
        return 1.0
    advisor_keys = _swap_keys(advisor_swaps)
    return len(advisor_keys & golden_keys) / len(golden_keys)


# ─── Tests ───────────────────────────────────────────────────────────


def test_fixture_has_sixteen_matchups():
    """The fixture must cover all 16 canonical matchups declared
    in the scoping doc. Always-on contract test — runs even
    without the SLM."""
    entries = _load_fixture()
    assert len(entries) == 16, (
        f"Expected 16 canonical matchups per scoping doc, "
        f"got {len(entries)}"
    )
    # Schema sanity: every entry has the required keys.
    required = {"my_deck", "opp_deck", "sideboard", "canonical_swaps"}
    for entry in entries:
        missing = required - set(entry.keys())
        assert not missing, (
            f"Fixture entry {entry.get('my_deck')!r} vs "
            f"{entry.get('opp_deck')!r} missing keys: {missing}"
        )


def test_sb_plan_overlap_meets_threshold():
    """The acceptance gate: average overlap >= 70% across the 16
    canonical matchups. Skipped when the SLM backend is
    unavailable (no model configured / llama_cpp not installed).
    """
    try:
        policy = _make_live_policy()
    except BackendUnavailable as e:
        pytest.skip(
            f"SLM backend unavailable; skipping SB plan overlap "
            f"gate. Reason: {e}"
        )

    entries = _load_fixture()
    overlaps: List[float] = []
    per_matchup_detail: List[Tuple[str, str, float]] = []

    for entry in entries:
        try:
            plan = advise_sideboard(
                my_deck=entry["my_deck"],
                my_sideboard=entry["sideboard"],
                opponent_deck=entry["opp_deck"],
                policy=policy,
            )
        except BackendUnavailable as e:
            pytest.skip(
                f"SLM backend became unavailable mid-run "
                f"({entry['my_deck']} vs {entry['opp_deck']}): {e}"
            )

        overlap = _overlap_fraction(plan.swaps, entry["canonical_swaps"])
        overlaps.append(overlap)
        per_matchup_detail.append(
            (entry["my_deck"], entry["opp_deck"], overlap)
        )

    avg_overlap = sum(overlaps) / len(overlaps)

    # Build a diagnostic table the assertion message can dump on
    # failure — surfaces *which* matchups dragged the average down.
    table = "\n".join(
        f"  {d1:>22} vs {d2:<22} overlap={ov:.2%}"
        for d1, d2, ov in per_matchup_detail
    )

    assert avg_overlap >= OVERLAP_THRESHOLD, (
        f"SB plan overlap {avg_overlap:.2%} below threshold "
        f"{OVERLAP_THRESHOLD:.0%} (scoping-doc acceptance gate). "
        f"Per-matchup breakdown:\n{table}"
    )


# ─── Pure overlap-math unit tests (always-on) ────────────────────────


class TestOverlapMath:
    """The overlap helper has its own unit tests so the gate's
    measurement is itself audited. These run in CI even without
    the SLM."""

    def test_identical_plans_full_overlap(self):
        golden = [{"card": "Wear // Tear", "delta": 2},
                  {"card": "Blood Moon", "delta": -2}]
        advisor = [SwapDirective("Wear // Tear", 2),
                   SwapDirective("Blood Moon", -2)]
        assert _overlap_fraction(advisor, golden) == 1.0

    def test_disjoint_plans_zero_overlap(self):
        golden = [{"card": "A", "delta": 1}]
        advisor = [SwapDirective("B", 1)]
        assert _overlap_fraction(advisor, golden) == 0.0

    def test_magnitude_ignored_sign_respected(self):
        """A +1 advisor swap matches a +2 golden swap (same
        direction). A -1 advisor swap does NOT match a +2 golden
        swap (opposite direction)."""
        golden = [{"card": "Wear // Tear", "delta": 2}]
        # Same sign, different magnitude → match.
        advisor_match = [SwapDirective("Wear // Tear", 1)]
        assert _overlap_fraction(advisor_match, golden) == 1.0
        # Opposite sign → no match.
        advisor_miss = [SwapDirective("Wear // Tear", -1)]
        assert _overlap_fraction(advisor_miss, golden) == 0.0

    def test_partial_overlap(self):
        """3 of 6 golden swaps matched → 50% overlap."""
        golden = [
            {"card": "A", "delta": 1},
            {"card": "B", "delta": 1},
            {"card": "C", "delta": 1},
            {"card": "D", "delta": -1},
            {"card": "E", "delta": -1},
            {"card": "F", "delta": -1},
        ]
        advisor = [
            SwapDirective("A", 2),  # match
            SwapDirective("B", 1),  # match
            SwapDirective("D", -1),  # match
            SwapDirective("Z", 1),  # spurious extra (ignored)
        ]
        assert _overlap_fraction(advisor, golden) == 0.5

    def test_extra_advisor_swaps_dont_lower_overlap(self):
        """Overlap is fraction of *golden* covered, so extra
        advisor swaps neither help nor hurt. (They are noise.)"""
        golden = [{"card": "A", "delta": 1}]
        advisor = [
            SwapDirective("A", 1),
            SwapDirective("noise1", 1),
            SwapDirective("noise2", -1),
        ]
        assert _overlap_fraction(advisor, golden) == 1.0

    def test_empty_golden_vacuously_full(self):
        """A no-op golden plan is trivially matched."""
        assert _overlap_fraction([SwapDirective("A", 1)], []) == 1.0

    def test_zero_delta_swaps_dropped(self):
        """A delta=0 entry contributes nothing to either side."""
        golden = [
            {"card": "A", "delta": 1},
            {"card": "B", "delta": 0},
        ]
        advisor = [
            SwapDirective("A", 1),
            SwapDirective("C", 0),
        ]
        # Only A is in the golden set; advisor matches it.
        assert _overlap_fraction(advisor, golden) == 1.0
