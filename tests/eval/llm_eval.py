"""Golden-dataset eval harness for the LLM-driven tools.

Phase H of the abstraction-cleanup pass.  The harness scaffolds the
quality measurement that catches regressions when the upstream
foundation model changes.  At session-time we have only a small
seed corpus; the harness is built so additional pairs can be added
mechanically as data accumulates.

## Layout

    tests/eval/
    ├── __init__.py
    ├── llm_eval.py                       (this file)
    └── golden/
        ├── synth_gameplan/
        │   ├── boros_energy.json
        │   ├── ruby_storm.json
        │   ├── affinity.json
        │   └── azorius_control.json
        ├── diagnose_replay/
        │   ├── affinity_overperf_2026-04-21.json
        │   ├── living_end_underperf_2026-04-24.json
        │   └── ruby_storm_wasted_enablers_2026-04-28.json
        └── handler_audit/
            ├── thraben_charm.json
            ├── territorial_kavu.json
            └── pick_your_poison.json

Each golden pair is `{ "input": {...}, "expected": {...} }`.  The
`input` is the raw prompt input the agent receives; `expected` is a
schema-validated dict that the run output is compared against.

## Scoring

For each pair, score := closed-set exact-match score (per-field) +
free-text similarity score (cosine over normalized text).  The pair
"passes" when overall score >= the per-task threshold (default 0.6).

* Closed-set fields (`Subsystem`, `Severity`, `HandlerTiming`,
  `archetype`, `current_status`, `should_change_to`) must match
  exactly — these are the high-signal labels.
* Free-text fields (`failing_test_rule`, `reason`, descriptions) are
  compared via lowercased, punctuation-stripped, whitespace-normalized
  cosine similarity over a token-frequency vector (a degenerate but
  cheap stand-in for sentence-embedding similarity that requires no
  external model).

## Running

By default the eval harness is gated behind `--run-eval` so it does
not run on every commit (running against a real model is expensive).
The smoke tests in `tests/test_eval_harness_smoke.py` use
pydantic-ai's `TestModel` to verify the harness wires up correctly
without making any real API call.

To run against a real foundation model:

    MTG_LLM_MODEL=anthropic:claude-sonnet-4-6 \\
        pytest tests/eval/ --run-eval -q

To pin a specific model for one task:

    MTG_LLM_MODEL_HANDLER_AUDIT=anthropic:claude-haiku-4-5 \\
        pytest tests/eval/ --run-eval -q

## Adding a new golden pair

1. Pick a real artifact (an existing gameplan JSON, a diagnostic doc,
   or a modal handler in `engine/card_effects.py`).
2. Construct `{ "input": {...}, "expected": {...} }` where `expected`
   is what the LLM SHOULD have emitted given that input.
3. Save as `tests/eval/golden/<task>/<slug>.json`.
4. Re-run `pytest tests/eval/ -q` to confirm the harness picks it up.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai.llm_agents import build_agent
from ai.llm_models import LLMTask
from ai.llm_schemas import (
    BugHypothesis,
    DocFreshnessReport,
    FailingTestSpec,
    HandlerGapReport,
    SynthesizedGameplan,
    from_json_dict,
)


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# Per-task pass threshold.  Free-text similarity isn't a great
# absolute number; the threshold is the empirical "quality bar"
# we want every pair to clear before a model upgrade ships.
DEFAULT_THRESHOLD: dict[str, float] = {
    "synth_gameplan":      0.6,
    "diagnose_replay":     0.6,
    "audit_doc_freshness": 0.6,
    "handler_audit":       0.6,
    "failing_test_spec":   0.6,
}

# Closed-set field names per task — these get exact-match scoring.
# Free-text fields fall through to cosine similarity.
_CLOSED_FIELDS_BY_TASK: dict[str, set[str]] = {
    "synth_gameplan":      {"archetype", "archetype_subtype"},
    "diagnose_replay":     {"suspected_subsystem"},
    "audit_doc_freshness": {"current_status", "should_change_to", "doc_path"},
    "handler_audit":       {"timing", "severity", "card_name"},
    "failing_test_spec":   {"expected_status_before_fix"},
}

_OUTPUT_TYPES = {
    "synth_gameplan":      SynthesizedGameplan,
    "diagnose_replay":     list,  # list[BugHypothesis]; first element compared
    "audit_doc_freshness": DocFreshnessReport,
    "handler_audit":       HandlerGapReport,
    "failing_test_spec":   FailingTestSpec,
}


# ─── Public API ─────────────────────────────────────────────────────


@dataclass
class PairResult:
    """One pair's score."""
    pair_id: str
    overall_score: float
    closed_match_score: float
    free_text_score: float
    passed: bool
    notes: List[str] = field(default_factory=list)


@dataclass
class EvalReport:
    """Aggregate result of an eval run for one task."""
    task: str
    model: str
    threshold: float
    pair_results: List[PairResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.pair_results:
            return 0.0
        return sum(1 for p in self.pair_results if p.passed) / len(self.pair_results)


def load_golden_pairs(task: LLMTask) -> List[Dict[str, Any]]:
    """Load every `tests/eval/golden/<task>/*.json` pair."""
    task_dir = GOLDEN_DIR / task
    if not task_dir.exists():
        return []
    pairs = []
    for path in sorted(task_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_pair_id"] = path.stem
        pairs.append(data)
    return pairs


def run_eval(
    task: LLMTask,
    *,
    model: Optional[str] = None,
    threshold: Optional[float] = None,
    test_model_payload: Optional[Any] = None,
) -> EvalReport:
    """Run every golden pair through `build_agent(task)` and score the
    output against the expected.

    `test_model_payload` is the smoke-test escape hatch: when set, the
    agent's model is overridden with `pydantic_ai.models.test.TestModel`
    that always emits the supplied payload.  This lets the smoke
    tests verify the harness end-to-end without an API call.
    """
    pairs = load_golden_pairs(task)
    threshold = threshold if threshold is not None else DEFAULT_THRESHOLD[task]
    agent = build_agent(task, model=model)
    chosen_model = _agent_model_id(agent)

    report = EvalReport(task=task, model=chosen_model, threshold=threshold)

    if test_model_payload is not None:
        from pydantic_ai.models.test import TestModel
        ctx_mgr = agent.override(model=TestModel(custom_output_args=test_model_payload))
    else:
        ctx_mgr = _NullContext()

    with ctx_mgr:
        for pair in pairs:
            pid = pair["_pair_id"]
            try:
                output = _run_one(agent, task, pair["input"])
                pr = _score_pair(task, output, pair["expected"], pair_id=pid)
            except Exception as exc:  # pragma: no cover — exercised only when a real model is used
                pr = PairResult(
                    pair_id=pid,
                    overall_score=0.0,
                    closed_match_score=0.0,
                    free_text_score=0.0,
                    passed=False,
                    notes=[f"exception: {exc!r}"],
                )
            pr.passed = pr.overall_score >= threshold
            report.pair_results.append(pr)

    return report


# ─── Internal scoring helpers ───────────────────────────────────────


class _NullContext:
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


def _agent_model_id(agent) -> str:
    """Best-effort model identifier extraction for reporting."""
    model = getattr(agent, "model", None)
    if model is None:
        return "<unknown>"
    return str(model)


def _run_one(agent, task: str, input_blob: Dict[str, Any]):
    """Run the agent against one pair's input.  Caller is responsible
    for the override context."""
    user_prompt = json.dumps(input_blob, indent=2, sort_keys=True)
    result = agent.run_sync(user_prompt)
    return result.output


def _score_pair(
    task: str, output: Any, expected: Dict[str, Any], *, pair_id: str
) -> PairResult:
    """Score one output vs. its expected dict.  Returns a PairResult.

    `output` is whatever the agent emitted (a pydantic model, a list,
    or — for TestModel runs against `list[BugHypothesis]` — a list of
    pydantic models).  Convert to dict via `from_json_dict` round
    trip and apply the per-task closed/free-text split."""
    output_dict = _to_comparable_dict(task, output)

    closed_fields = _CLOSED_FIELDS_BY_TASK[task]

    closed_score = _exact_match_score(output_dict, expected, closed_fields)
    free_text_score = _free_text_score(output_dict, expected, closed_fields)
    overall = 0.5 * closed_score + 0.5 * free_text_score

    return PairResult(
        pair_id=pair_id,
        overall_score=overall,
        closed_match_score=closed_score,
        free_text_score=free_text_score,
        passed=False,  # caller fills based on threshold
    )


def _to_comparable_dict(task: str, output: Any) -> Dict[str, Any]:
    """Reduce the agent's output to a dict shape comparable to the
    expected JSON.  For `diagnose_replay` we compare against the
    first hypothesis (highest-confidence) since the goldens are
    single-record."""
    if task == "diagnose_replay":
        if isinstance(output, list) and output:
            head = output[0]
            if hasattr(head, "model_dump"):
                return head.model_dump(mode="json")
            return dict(head)
        return {}
    if hasattr(output, "model_dump"):
        return output.model_dump(mode="json")
    if isinstance(output, dict):
        return output
    return {}


def _exact_match_score(
    output: Dict[str, Any], expected: Dict[str, Any], closed_fields: set
) -> float:
    """Fraction of `closed_fields` present in `expected` that match
    exactly between output and expected.  Returns 1.0 if `expected`
    has no closed fields (the test is then driven by free text only)."""
    relevant = [f for f in closed_fields if f in expected]
    if not relevant:
        return 1.0
    hits = sum(1 for f in relevant if output.get(f) == expected[f])
    return hits / len(relevant)


def _free_text_score(
    output: Dict[str, Any], expected: Dict[str, Any], closed_fields: set
) -> float:
    """Cosine similarity over token-frequency vectors of the
    free-text fields (everything in `expected` not in `closed_fields`).

    Returns 1.0 if `expected` has no free-text fields.  This is a
    cheap stand-in for sentence-embedding similarity — sufficient to
    catch obvious regressions ("the agent stopped producing reasons")
    without needing an external embedding model."""
    free_fields = [f for f in expected.keys() if f not in closed_fields]
    if not free_fields:
        return 1.0

    out_text = " ".join(_stringify(output.get(f)) for f in free_fields)
    exp_text = " ".join(_stringify(expected.get(f)) for f in free_fields)
    return _cosine(out_text, exp_text)


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_stringify(x) for x in v)
    if isinstance(v, dict):
        return " ".join(f"{k} {_stringify(val)}" for k, val in v.items())
    return str(v)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for tok in _TOKEN_RE.findall(text.lower()):
        counts[tok] = counts.get(tok, 0) + 1
    return counts


def _cosine(a: str, b: str) -> float:
    av = _tokens(a)
    bv = _tokens(b)
    if not av or not bv:
        # Both empty → trivially identical (1.0 for free-text smoke
        # cases); one empty → 0.0 mismatch.
        return 1.0 if not av and not bv else 0.0
    keys = set(av) | set(bv)
    dot = sum(av.get(k, 0) * bv.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in av.values()))
    nb = math.sqrt(sum(v * v for v in bv.values()))
    return dot / (na * nb) if na and nb else 0.0


__all__ = [
    "GOLDEN_DIR",
    "DEFAULT_THRESHOLD",
    "PairResult",
    "EvalReport",
    "load_golden_pairs",
    "run_eval",
]
