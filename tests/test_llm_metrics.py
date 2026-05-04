"""Telemetry primitives — `ai.llm_metrics`.

Each test points the module-level `METRICS_FILE` at a tmp path so
JSONL writes never escape the test suite.  No real model is contacted.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai import llm_metrics


@pytest.fixture
def metrics_file(tmp_path, monkeypatch):
    """Redirect METRICS_FILE / METRICS_DIR to a tmp directory."""
    target = tmp_path / "calls.jsonl"
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", target)
    return target


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ─── log_call -----------------------------------------------------------

def test_log_call_appends_jsonl_record(metrics_file):
    """Every log_call writes one parseable JSON object terminated by \\n."""
    llm_metrics.log_call(
        task="synth_gameplan",
        model="anthropic:claude-sonnet-4-6",
        prompt_version=1,
        tokens_in=1000,
        tokens_out=500,
        cache_hit=False,
        input_hash="sha256:abc",
        output_type="SynthesizedGameplan",
        duration_ms=1234,
        success=True,
    )
    records = _read_jsonl(metrics_file)
    assert len(records) == 1
    r = records[0]
    assert r["task"] == "synth_gameplan"
    assert r["model"] == "anthropic:claude-sonnet-4-6"
    assert r["prompt_version"] == 1
    assert r["tokens_in"] == 1000
    assert r["tokens_out"] == 500
    assert r["cache_hit"] is False
    assert r["input_hash"] == "sha256:abc"
    assert r["output_type"] == "SynthesizedGameplan"
    assert r["duration_ms"] == 1234
    assert r["success"] is True
    assert r["error"] is None
    # Sonnet pricing: 1000 in × $3/Mtok + 500 out × $15/Mtok
    expected_cost = (1000 * 3.0 + 500 * 15.0) / 1_000_000.0
    assert r["cost_usd"] == pytest.approx(expected_cost)


def test_log_call_creates_metrics_dir(tmp_path, monkeypatch):
    """If the metrics dir is missing, log_call creates it on first write."""
    nested = tmp_path / "fresh" / "llm"
    target = nested / "calls.jsonl"
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", nested)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", target)
    assert not nested.exists()

    llm_metrics.log_call(
        task="synth_gameplan",
        model="test",
        prompt_version=1,
        tokens_in=0,
        tokens_out=0,
        cache_hit=False,
        input_hash="sha256:0",
        output_type="X",
        duration_ms=0,
        success=True,
    )
    assert nested.exists()
    assert target.exists()


def test_log_call_cache_hit_records_zero_cost(metrics_file):
    """Cache hits log cost_usd=0 even if token counts are nonzero."""
    llm_metrics.log_call(
        task="synth_gameplan",
        model="anthropic:claude-sonnet-4-6",
        prompt_version=1,
        tokens_in=1000,  # Should NOT generate cost since cache hit
        tokens_out=500,
        cache_hit=True,
        input_hash="sha256:cached",
        output_type="SynthesizedGameplan",
        duration_ms=2,
        success=True,
    )
    record = _read_jsonl(metrics_file)[0]
    assert record["cache_hit"] is True
    assert record["cost_usd"] == 0.0


# ─── estimate_cost_usd --------------------------------------------------

def test_estimate_cost_usd_known_model():
    """Sonnet 4.6: 1000 in / 500 out → exact USD computed from table."""
    cost = llm_metrics.estimate_cost_usd(
        "anthropic:claude-sonnet-4-6", 1000, 500
    )
    expected = (1000 * 3.0 + 500 * 15.0) / 1_000_000.0
    assert cost == pytest.approx(expected)


def test_estimate_cost_usd_unknown_model_returns_zero():
    """Unknown model id is treated as $0 — does not crash."""
    cost = llm_metrics.estimate_cost_usd("not-a-real-model", 999_999, 999_999)
    assert cost == 0.0


def test_estimate_cost_usd_test_model_is_zero():
    """The TestModel sentinel costs nothing — smoke runs are free."""
    assert llm_metrics.estimate_cost_usd("test", 1_000_000, 1_000_000) == 0.0


# ─── CallTimer ----------------------------------------------------------

def test_call_timer_records_duration(metrics_file):
    """A 50ms sleep inside the context produces duration_ms >= 50."""
    with llm_metrics.CallTimer(
        task="audit_doc_freshness",
        model="anthropic:claude-haiku-4-5",
        prompt_version=1,
        cache_hit=False,
        input_hash="sha256:dur",
    ) as timer:
        time.sleep(0.05)
        timer.set_tokens(100, 50)
        timer.set_output_type("DocFreshnessReport")

    record = _read_jsonl(metrics_file)[0]
    assert record["duration_ms"] >= 50
    assert record["tokens_in"] == 100
    assert record["tokens_out"] == 50
    assert record["success"] is True


def test_call_timer_logs_error_on_exception(metrics_file):
    """If the body raises, success=False and error is recorded; the
    exception then propagates."""
    with pytest.raises(RuntimeError, match="boom"):
        with llm_metrics.CallTimer(
            task="diagnose_replay",
            model="anthropic:claude-sonnet-4-6",
            prompt_version=1,
            cache_hit=False,
            input_hash="sha256:err",
        ):
            raise RuntimeError("boom")

    record = _read_jsonl(metrics_file)[0]
    assert record["success"] is False
    assert record["error"] is not None
    assert "RuntimeError" in record["error"]
    assert "boom" in record["error"]


# ─── iter_calls ---------------------------------------------------------

def test_iter_calls_filters_since(metrics_file):
    """`since` excludes records older than the threshold."""
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    today = datetime.now(timezone.utc)

    # Manually build records with explicit timestamps.
    rows = [
        {"ts": (yesterday - timedelta(days=2)).isoformat(),
         "task": "synth_gameplan", "model": "test", "prompt_version": 1,
         "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
         "cache_hit": False, "input_hash": "h1", "output_type": "X",
         "duration_ms": 0, "success": True, "error": None},
        {"ts": yesterday.isoformat(),
         "task": "synth_gameplan", "model": "test", "prompt_version": 1,
         "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
         "cache_hit": False, "input_hash": "h2", "output_type": "X",
         "duration_ms": 0, "success": True, "error": None},
        {"ts": today.isoformat(),
         "task": "synth_gameplan", "model": "test", "prompt_version": 1,
         "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
         "cache_hit": False, "input_hash": "h3", "output_type": "X",
         "duration_ms": 0, "success": True, "error": None},
    ]
    with metrics_file.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    threshold = today - timedelta(hours=1)
    seen = list(llm_metrics.iter_calls(since=threshold))
    assert len(seen) == 1
    assert seen[0]["input_hash"] == "h3"


def test_iter_calls_filters_task(metrics_file):
    """`task` filters records to one task literal."""
    for task, h in [
        ("synth_gameplan", "h1"),
        ("audit_doc_freshness", "h2"),
        ("diagnose_replay", "h3"),
    ]:
        llm_metrics.log_call(
            task=task, model="test", prompt_version=1,
            tokens_in=0, tokens_out=0, cache_hit=False,
            input_hash=h, output_type="X",
            duration_ms=0, success=True,
        )
    seen = list(llm_metrics.iter_calls(task="audit_doc_freshness"))
    assert len(seen) == 1
    assert seen[0]["input_hash"] == "h2"


def test_iter_calls_returns_empty_when_no_log(tmp_path, monkeypatch):
    """No file = no records, no crash."""
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", tmp_path / "missing.jsonl")
    assert list(llm_metrics.iter_calls()) == []


def test_iter_calls_skips_bad_lines(metrics_file):
    """Partially-flushed / corrupt lines do not crash iteration."""
    with metrics_file.open("w") as f:
        f.write("not-json\n")
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "task": "synth_gameplan", "model": "test", "prompt_version": 1,
            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0,
            "cache_hit": False, "input_hash": "h", "output_type": "X",
            "duration_ms": 0, "success": True, "error": None,
        }) + "\n")
        f.write("\n")  # blank line
    seen = list(llm_metrics.iter_calls())
    assert len(seen) == 1
    assert seen[0]["input_hash"] == "h"


def test_input_hash_for_metrics_is_stable():
    """Equivalent inputs hash to the same digest; different inputs differ."""
    a = llm_metrics._input_hash_for_metrics({"deck": "Burn", "size": 60})
    b = llm_metrics._input_hash_for_metrics({"size": 60, "deck": "Burn"})
    c = llm_metrics._input_hash_for_metrics({"deck": "Burn", "size": 61})
    assert a == b
    assert a != c
    assert a.startswith("sha256:")
