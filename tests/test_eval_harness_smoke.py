"""Smoke tests for `tests.eval.llm_eval`.

These tests verify the harness wires up correctly without making any
real LLM API call.  They are NOT a quality gate — that is what
`pytest --run-eval` is for, gated behind the conftest flag.

What is exercised here:
  1. `load_golden_pairs(task)` finds and parses every checked-in pair.
  2. `run_eval(task, test_model_payload=expected)` produces a score
     of 1.0 when the TestModel echoes the expected payload (the
     output equals expected, so every field matches).
  3. The threshold gates pass/fail correctly.

When the foundation model upstream changes shape, these smoke tests
keep passing (they don't call the real model) but `pytest
--run-eval` will surface the regression."""
from __future__ import annotations

import pytest

from tests.eval.llm_eval import (
    DEFAULT_THRESHOLD,
    EvalReport,
    PairResult,
    load_golden_pairs,
    run_eval,
)


# ─── Golden-pair discovery ──────────────────────────────────────────


def test_synth_gameplan_goldens_load():
    pairs = load_golden_pairs("synth_gameplan")
    assert len(pairs) >= 4, "expected the 4 seed synth-gameplan pairs"
    for p in pairs:
        assert "input" in p and "expected" in p
        assert "deck_name" in p["input"]


def test_diagnose_replay_goldens_load():
    pairs = load_golden_pairs("diagnose_replay")
    assert len(pairs) >= 3, "expected the 3 seed diagnose-replay pairs"
    for p in pairs:
        assert "input" in p and "expected" in p


def test_handler_audit_goldens_load():
    pairs = load_golden_pairs("handler_audit")
    assert len(pairs) >= 3, "expected the 3 seed handler-audit pairs"
    for p in pairs:
        assert "input" in p and "expected" in p
        assert "card_name" in p["expected"]


# ─── Harness wires up via TestModel ──────────────────────────────────


def test_run_eval_passes_when_test_model_echoes_expected():
    """When TestModel emits exactly what's expected, every pair scores 1.0."""
    pairs = load_golden_pairs("handler_audit")
    assert pairs, "no goldens to evaluate"
    payload = pairs[0]["expected"]

    report = run_eval(
        "handler_audit",
        test_model_payload=payload,
        # Lower the threshold to ensure the smoke test isn't gated by it.
        threshold=0.0,
    )
    assert isinstance(report, EvalReport)
    assert report.task == "handler_audit"
    assert len(report.pair_results) == len(pairs)

    # The pair whose expected matches the TestModel's payload must score 1.0.
    matching = [r for r in report.pair_results if r.pair_id == pairs[0]["_pair_id"]]
    assert matching
    assert matching[0].overall_score == pytest.approx(1.0)
    assert matching[0].passed is True


def test_run_eval_threshold_gates_pass_fail():
    """A non-matching payload should score below 1.0; threshold gates pass."""
    pairs = load_golden_pairs("handler_audit")
    payload = pairs[0]["expected"].copy()
    # Corrupt closed-set field: severity flipped from P0 to P2.
    payload["severity"] = "P2" if payload["severity"] != "P2" else "P0"

    high_threshold = run_eval(
        "handler_audit",
        test_model_payload=payload,
        threshold=0.99,
    )
    matching = [r for r in high_threshold.pair_results if r.pair_id == pairs[0]["_pair_id"]]
    assert matching
    assert matching[0].passed is False, (
        "corrupted payload should fail the high threshold"
    )

    low_threshold = run_eval(
        "handler_audit",
        test_model_payload=payload,
        threshold=0.0,
    )
    matching_low = [r for r in low_threshold.pair_results if r.pair_id == pairs[0]["_pair_id"]]
    assert matching_low[0].passed is True, (
        "any non-zero score passes a 0.0 threshold"
    )


def test_default_threshold_table_has_every_task():
    """Every supported LLMTask must have a default threshold."""
    from ai.llm_models import DEFAULT_MODELS
    assert set(DEFAULT_THRESHOLD) >= set(DEFAULT_MODELS), (
        "DEFAULT_THRESHOLD must cover every LLMTask"
    )


def test_pair_result_dataclass_shape():
    """Light contract test on the result shape used by reports."""
    pr = PairResult(
        pair_id="x",
        overall_score=0.8,
        closed_match_score=1.0,
        free_text_score=0.6,
        passed=True,
    )
    assert pr.pair_id == "x"
    assert pr.notes == []  # default empty list
