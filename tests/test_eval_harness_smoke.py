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

# `run_eval(..., test_model_payload=...)` builds a real
# `pydantic_ai.Agent` (overridden with `TestModel`), so the two
# harness-wiring smoke tests need the optional pydantic_ai dep.
# The golden-pair discovery tests above them do not — they must keep
# running in dep-free environments, so we mark only the TestModel
# tests instead of collect_ignore-ing the whole module (the pattern
# `tests/conftest.py` uses for modules that import pydantic_ai at
# module scope). Because `tests/eval/test_smoke.py` re-exports these
# functions, the marker travels with them and gates both collection
# points.
try:
    import pydantic_ai  # noqa: F401  (presence probe only)

    _HAS_PYDANTIC_AI = True
except ImportError:
    _HAS_PYDANTIC_AI = False

requires_pydantic_ai = pytest.mark.skipif(
    not _HAS_PYDANTIC_AI,
    reason="optional dep pydantic_ai absent — TestModel wiring smoke "
           "runs only when the LLM feature set is installed",
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


@requires_pydantic_ai
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


@requires_pydantic_ai
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


# ─── Every task's goldens through the harness (offline) ─────────────


def _language_model_for(task):
    """The task's model when it answers in the canonical schema — its
    language-model fallback if its default is a decision model."""
    from ai.llm_agents import is_decision_model
    from ai.llm_models import DEFAULT_MODELS, FALLBACK_MODELS
    model = DEFAULT_MODELS[task]
    return FALLBACK_MODELS.get(task, model) if is_decision_model(model) else model


def _echo_payload(task, expected):
    """TestModel output args for `expected`; a list output (ranked
    hypotheses) is a list of one."""
    from ai.llm_agents import _OUTPUT_TYPES
    import typing
    if typing.get_origin(_OUTPUT_TYPES[task]) is list:
        return [expected]
    return expected


@requires_pydantic_ai
@pytest.mark.parametrize("task", [
    "synth_gameplan", "diagnose_replay", "audit_doc_freshness",
    "handler_audit", "failing_test_spec", "classify_oracle",
    "decision_scorer",
])
def test_every_tasks_golden_passes_when_the_model_echoes_it(task):
    """The harness validates each golden input into its task's typed
    input, renders it with the production renderer, and scores the
    canonical output — so an output equal to `expected` gets every
    structured field right and passes.  It scores exactly 1.0 except
    where the schema fills nested defaults a partial golden omits
    (synth_gameplan's goals), which only the free-text half sees."""
    pairs = load_golden_pairs(task)
    first = pairs[0]
    report = run_eval(
        task,
        model=_language_model_for(task),
        test_model_payload=_echo_payload(task, first["expected"]),
    )
    assert len(report.pair_results) == len(pairs)
    (match,) = [r for r in report.pair_results if r.pair_id == first["_pair_id"]]
    assert match.closed_match_score == pytest.approx(1.0), match.notes
    assert match.passed, match
    if task != "synth_gameplan":
        assert match.overall_score == pytest.approx(1.0), match


@requires_pydantic_ai
def test_a_decision_models_pick_is_scored_against_the_weight_band():
    """On the decision model the scorer answers a pick on the weight
    grid; the harness maps it to the canonical weight and scores it
    against each pair's band — in band passes, out of band fails."""
    from ai.llm_models import DEFAULT_MODELS
    report = run_eval(
        "decision_scorer",
        model=DEFAULT_MODELS["decision_scorer"],
        test_model_payload={"weight": "4.0"},
    )
    by_id = {r.pair_id: r for r in report.pair_results}
    assert by_id["ramp__tron_mana_advantage"].passed      # band 3.0..5.0
    assert not by_id["aggro__tron_mana_advantage"].passed  # band 0.0..0.0


def test_numeric_fields_score_inside_their_band_or_on_the_exact_value():
    from tests.eval.llm_eval import _score_pair
    inside = _score_pair("decision_scorer", {"weight": 4.5}, {"weight": 4.0},
                         pair_id="x", bands={"weight": [3.0, 5.0]})
    outside = _score_pair("decision_scorer", {"weight": 5.5}, {"weight": 4.0},
                          pair_id="x", bands={"weight": [3.0, 5.0]})
    exact = _score_pair("decision_scorer", {"weight": 4.5}, {"weight": 4.0},
                        pair_id="x")
    assert (inside.overall_score, outside.overall_score, exact.overall_score) \
        == (1.0, 0.0, 0.0)


def test_set_fields_score_by_overlap_not_order():
    from tests.eval.llm_eval import _score_pair
    expected = {"card_name": "A", "tags": ["X", "Y"]}
    same = _score_pair("classify_oracle", {"card_name": "A", "tags": ["Y", "X"]},
                       expected, pair_id="x")
    half = _score_pair("classify_oracle", {"card_name": "A", "tags": ["X"]},
                       expected, pair_id="x")
    assert same.overall_score == 1.0
    assert half.overall_score == pytest.approx((1.0 + 0.5) / 2)


def test_audit_failing_test_classify_and_scorer_goldens_load():
    for task in ("audit_doc_freshness", "failing_test_spec",
                 "classify_oracle", "decision_scorer"):
        pairs = load_golden_pairs(task)
        assert len(pairs) >= 3, task
        for p in pairs:
            assert "input" in p and "expected" in p


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
