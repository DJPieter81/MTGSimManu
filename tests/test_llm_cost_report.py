"""CLI + aggregation contract for `tools.llm_cost_report`.

Each test seeds the JSONL log directly (no real LLM call) and
verifies the aggregator output.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai import llm_metrics
from tools import llm_cost_report


@pytest.fixture
def metrics_file(tmp_path, monkeypatch):
    target = tmp_path / "calls.jsonl"
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", target)
    return target


def _seed(metrics_file: Path, records: list[dict]) -> None:
    with metrics_file.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _record(
    *,
    task: str,
    model: str,
    cost: float,
    cache_hit: bool = False,
    tokens_in: int = 0,
    tokens_out: int = 0,
    duration_ms: int = 100,
) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "model": model,
        "prompt_version": 1,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "cache_hit": cache_hit,
        "input_hash": "sha256:x",
        "output_type": "X",
        "duration_ms": duration_ms,
        "success": True,
        "error": None,
    }


# ─── aggregate() -------------------------------------------------------

def test_report_aggregates_calls_correctly(metrics_file):
    """Per-task totals match seeded fixture."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.10, tokens_in=1000, tokens_out=500),
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.0, cache_hit=True),
        _record(task="audit_doc_freshness", model="anthropic:claude-haiku-4-5",
                cost=0.001, tokens_in=100, tokens_out=50),
        _record(task="diagnose_replay", model="anthropic:claude-sonnet-4-6",
                cost=0.05, tokens_in=2000, tokens_out=300),
        _record(task="audit_doc_freshness", model="anthropic:claude-haiku-4-5",
                cost=0.0, cache_hit=True),
    ])
    records = list(llm_metrics.iter_calls())
    summary = llm_cost_report.aggregate(records)

    by_task = summary["by_task"]
    assert by_task["synth_gameplan"]["calls"] == 2
    assert by_task["synth_gameplan"]["api_calls"] == 1
    assert by_task["synth_gameplan"]["cache_hits"] == 1
    assert by_task["synth_gameplan"]["cost_usd"] == pytest.approx(0.10)

    assert by_task["audit_doc_freshness"]["calls"] == 2
    assert by_task["audit_doc_freshness"]["cache_hits"] == 1
    assert by_task["audit_doc_freshness"]["cost_usd"] == pytest.approx(0.001)

    assert by_task["diagnose_replay"]["calls"] == 1

    assert summary["total"]["calls"] == 5
    assert summary["total"]["cost_usd"] == pytest.approx(0.151)


def test_report_per_model_breakdown(metrics_file):
    """`by_model` groups by model id and sums calls + cost."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.20),
        _record(task="diagnose_replay", model="anthropic:claude-sonnet-4-6",
                cost=0.10),
        _record(task="audit_doc_freshness", model="anthropic:claude-haiku-4-5",
                cost=0.01),
    ])
    summary = llm_cost_report.aggregate(list(llm_metrics.iter_calls()))
    assert summary["by_model"]["anthropic:claude-sonnet-4-6"]["calls"] == 2
    assert summary["by_model"]["anthropic:claude-sonnet-4-6"]["cost_usd"] == pytest.approx(0.30)
    assert summary["by_model"]["anthropic:claude-haiku-4-5"]["calls"] == 1


# ─── format_report() --------------------------------------------------

def test_report_hot_task_threshold_warning(metrics_file):
    """A task that exceeds the threshold triggers the hot-task line."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=6.0),  # > $5 threshold
        _record(task="audit_doc_freshness", model="anthropic:claude-haiku-4-5",
                cost=0.10),
    ])
    summary = llm_cost_report.aggregate(list(llm_metrics.iter_calls()))
    out = llm_cost_report.format_report(
        summary,
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        days=30,
    )
    assert "Hot tasks" in out
    assert "synth_gameplan" in out
    assert "exceeds" in out
    assert "rule-based path" in out


def test_report_no_hot_task_says_none(metrics_file):
    """No task above threshold → reports NONE."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.05),
    ])
    summary = llm_cost_report.aggregate(list(llm_metrics.iter_calls()))
    out = llm_cost_report.format_report(
        summary,
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        days=30,
    )
    assert "NONE" in out


def test_report_threshold_argument_changes_hotness(metrics_file):
    """Lowering the threshold flips a task from cold to hot."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=1.0),
    ])
    summary = llm_cost_report.aggregate(list(llm_metrics.iter_calls()))
    cold = llm_cost_report.format_report(
        summary,
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        days=30,
        threshold=5.0,
    )
    hot = llm_cost_report.format_report(
        summary,
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        days=30,
        threshold=0.5,
    )
    assert "NONE" in cold
    assert "exceeds" in hot


# ─── --json -----------------------------------------------------------

def test_report_json_output_shape(metrics_file, capsys):
    """`--json` emits a JSON object with the documented top-level keys."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.1),
    ])
    rc = llm_cost_report.main(["--json", "--days", "30"])
    assert rc == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    for key in ("window_start", "window_end", "days", "task_filter",
                "threshold_usd", "summary"):
        assert key in payload
    assert "by_task" in payload["summary"]
    assert "by_model" in payload["summary"]
    assert "total" in payload["summary"]


def test_report_task_filter(metrics_file, capsys):
    """`--task` restricts records to the named task."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.10),
        _record(task="audit_doc_freshness", model="anthropic:claude-haiku-4-5",
                cost=0.50),
    ])
    rc = llm_cost_report.main(["--json", "--task", "synth_gameplan"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["summary"]["by_task"].keys()) == {"synth_gameplan"}


def test_report_text_output_runs(metrics_file, capsys):
    """The default text path runs end-to-end without error."""
    _seed(metrics_file, [
        _record(task="synth_gameplan", model="anthropic:claude-sonnet-4-6",
                cost=0.10, tokens_in=1000, tokens_out=500),
    ])
    rc = llm_cost_report.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LLM cost report" in out
    assert "synth_gameplan" in out
    assert "Per-model:" in out


def test_report_handles_empty_log(tmp_path, monkeypatch, capsys):
    """An empty (or missing) log produces a zero-record report, no crash."""
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", tmp_path / "calls.jsonl")
    rc = llm_cost_report.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LLM cost report" in out
