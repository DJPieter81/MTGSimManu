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
        ├── handler_audit/            (3 pairs)
        ├── audit_doc_freshness/      (3 pairs)
        ├── failing_test_spec/        (3 pairs)
        ├── classify_oracle/          (5 pairs)
        └── decision_scorer/          (6 pairs)

Every `ai.llm_models.LLMTask` has goldens (pinned by
`tests/test_llm_agent_contract.py`).

Each golden pair is `{ "input": {...}, "expected": {...} }`, plus an
optional `"bands": {field: [lo, hi]}` and a `"source"` note.  The
`input` validates against the task's typed input
(`ai.llm_inputs.TASK_INPUT_TYPES`) and is rendered by that task's
production renderer — the eval exercises the exact prompt the tools
send.  `expected` is compared against the run output in the task's
canonical schema (`ai.llm_agents.canonical_output`), so a decision
model's decision-shaped answer is scored like a language model's.

## Scoring

For each pair, score := structured score (exact closed-set fields,
numeric fields, set fields) + free-text similarity score (cosine over
normalized text), each half the weight when both are present.  The
pair "passes" when overall score >= the per-task threshold.

* Closed-set fields (`Subsystem`, `Severity`, `HandlerTiming`,
  `archetype`, `current_status`, `should_change_to`) must match
  exactly — these are the high-signal labels.
* Numeric fields (`decision_scorer.weight`) score 1 inside the pair's
  band (or on the exact value when the pair gives no band), else 0.
* Set fields (`classify_oracle.tags`) score by Jaccard overlap.
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

To run against each task's default model (`ai.llm_models`; needs the
provider keys — the decision scorer's default reads TYPESAFE_API_KEY):

    pytest tests/eval/ --run-eval -q

To pin a specific model for one task (or `MTG_LLM_MODEL` for all):

    MTG_LLM_MODEL_HANDLER_AUDIT=<provider:model-id> \\
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

from ai.llm_agents import build_agent, canonical_output
from ai.llm_inputs import task_input
from ai.llm_models import LLMTask
from ai.llm_schemas import (
    BugHypothesis,
    DecisionScoringWeights,
    DocFreshnessReport,
    FailingTestSpec,
    HandlerGapReport,
    OracleTagClassification,
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
    # classify_oracle outputs are closed-set tag lists; the exact-
    # match scoring path dominates and the cosine fallback rarely
    # fires.  Same 0.6 threshold as the other extraction tasks.
    "classify_oracle":     0.6,
    # decision_scorer goldens carry only the numeric `weight`, scored
    # in-band or not, so a pair's score is 0 or 1 and the threshold
    # only separates those two outcomes.
    "decision_scorer":     0.6,
}

# Closed-set field names per task — these get exact-match scoring.
# Free-text fields fall through to cosine similarity.
_CLOSED_FIELDS_BY_TASK: dict[str, set[str]] = {
    "synth_gameplan":      {"archetype", "archetype_subtype"},
    "diagnose_replay":     {"suspected_subsystem"},
    "audit_doc_freshness": {"current_status", "should_change_to", "doc_path",
                            "replacement_doc"},
    "handler_audit":       {"timing", "severity", "card_name"},
    # `test_file` is the naming rule applied to the hypothesis's rule
    # (the renderer states it) and `rule_name` must echo that rule, so
    # both are exact; fixture and assertion pseudocode are free text.
    "failing_test_spec":   {"expected_status_before_fix", "test_file",
                            "rule_name"},
    "classify_oracle":     {"card_name"},
    "decision_scorer":     set(),
}

# Numeric fields per task — in the pair's `bands[field]` = [lo, hi]
# scores 1, outside 0; with no band the value must match exactly.
_NUMERIC_FIELDS_BY_TASK: dict[str, set[str]] = {
    "decision_scorer":     {"weight"},
}

# Set-valued fields per task — scored by Jaccard overlap, so a missing
# or an extra tag costs proportionally and order does not matter.
_SET_FIELDS_BY_TASK: dict[str, set[str]] = {
    "classify_oracle":     {"tags"},
}

_OUTPUT_TYPES = {
    "synth_gameplan":      SynthesizedGameplan,
    "diagnose_replay":     list,  # list[BugHypothesis]; first element compared
    "audit_doc_freshness": DocFreshnessReport,
    "handler_audit":       HandlerGapReport,
    "failing_test_spec":   FailingTestSpec,
    "classify_oracle":     OracleTagClassification,
    "decision_scorer":     DecisionScoringWeights,
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
    # When `test_model_payload` is supplied (smoke-test harness path),
    # disable cache + metrics: the cache key is `(task, model,
    # prompt_version, input_blob)` and does NOT include the override
    # payload, so a previously-stored TestModel response would be
    # returned instead of the current override.  That would silently
    # break the threshold-gate smoke test (and any future smoke test
    # that uses `test_model_payload` to drive specific outputs).
    use_cache_and_metrics = test_model_payload is None
    agent = build_agent(
        task,
        model=model,
        use_cache=use_cache_and_metrics,
        instrument=use_cache_and_metrics,
    )
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
                pr = _score_pair(task, output, pair["expected"], pair_id=pid,
                                 bands=pair.get("bands"))
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
    for the override context.

    The input is validated into the task's typed input and rendered by
    the production renderer; the output is mapped to the task's
    canonical schema (a decision model answers in a decision shape)."""
    inp = task_input(task, input_blob)
    result = agent.run_sync(inp.render())
    return canonical_output(task, result.output, inp)


def _score_pair(
    task: str,
    output: Any,
    expected: Dict[str, Any],
    *,
    pair_id: str,
    bands: Optional[Dict[str, List[float]]] = None,
) -> PairResult:
    """Score one output vs. its expected dict.  Returns a PairResult.

    `output` is whatever the agent emitted (a pydantic model, a list,
    or — for TestModel runs against `list[BugHypothesis]` — a list of
    pydantic models).  Convert to a dict and apply the per-task split:
    structured fields (closed-set, numeric, set) and free text.  When
    a pair's `expected` has both kinds, each is half the score; when it
    has only one kind, that kind is the whole score."""
    output_dict = _to_comparable_dict(task, output)

    closed_fields = _CLOSED_FIELDS_BY_TASK[task]
    numeric_fields = _NUMERIC_FIELDS_BY_TASK.get(task, set())
    set_fields = _SET_FIELDS_BY_TASK.get(task, set())
    structured = closed_fields | numeric_fields | set_fields

    parts: List[float] = []
    parts += [1.0 if output_dict.get(f) == expected[f] else 0.0
              for f in closed_fields if f in expected]
    parts += [_numeric_score(output_dict.get(f), expected[f],
                             (bands or {}).get(f))
              for f in numeric_fields if f in expected]
    parts += [_jaccard(output_dict.get(f), expected[f])
              for f in set_fields if f in expected]
    has_free = any(f not in structured for f in expected)

    closed_score = sum(parts) / len(parts) if parts else 1.0
    free_text_score = _free_text_score(output_dict, expected, structured)
    if parts and has_free:
        overall = 0.5 * closed_score + 0.5 * free_text_score
    elif parts:
        overall = closed_score
    else:
        overall = free_text_score

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


def _numeric_score(out: Any, exp: Any, band: Optional[List[float]]) -> float:
    """1.0 when `out` lies in `band` ([lo, hi], inclusive) — or equals
    `exp` when no band is given — else 0.0."""
    try:
        value = float(out)
    except (TypeError, ValueError):
        return 0.0
    if band is not None:
        lo, hi = band
        return 1.0 if lo <= value <= hi else 0.0
    return 1.0 if value == float(exp) else 0.0


def _jaccard(out: Any, exp: Any) -> float:
    """|out ∩ exp| / |out ∪ exp| over the two collections; two empty
    collections agree completely."""
    a, b = set(out or ()), set(exp or ())
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


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
