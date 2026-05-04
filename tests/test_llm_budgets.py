"""Per-task USD budget caps + token caps — `ai.llm_budgets`.

Tests use a tmp-path metrics file via monkeypatch so no real
``cache/llm/calls.jsonl`` is touched.  No real LLM call is made.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai import llm_budgets, llm_metrics


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def metrics_file(tmp_path, monkeypatch):
    """Redirect the metrics module's METRICS_FILE/DIR to a tmp path so
    every test starts from an empty log."""
    target = tmp_path / "calls.jsonl"
    monkeypatch.setattr(llm_metrics, "METRICS_DIR", tmp_path)
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", target)
    return target


def _write_record(
    path: Path,
    *,
    task: str,
    cost_usd: float,
    ts: datetime,
    cache_hit: bool = False,
) -> None:
    record = {
        "ts": ts.isoformat(),
        "task": task,
        "model": "anthropic:claude-sonnet-4-6",
        "prompt_version": 1,
        "tokens_in": 1000,
        "tokens_out": 500,
        "cost_usd": cost_usd,
        "cache_hit": cache_hit,
        "input_hash": "sha256:test",
        "output_type": "X",
        "duration_ms": 100,
        "success": True,
        "error": None,
    }
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ─── select_budget_usd ──────────────────────────────────────────────────


def test_select_budget_usd_explicit_override_wins(monkeypatch):
    """Explicit override beats env vars and defaults."""
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "99")
    monkeypatch.setenv("MTG_LLM_BUDGET_USD", "50")
    assert llm_budgets.select_budget_usd(
        "synth_gameplan", override=2.5
    ) == 2.5


def test_select_budget_usd_task_env_beats_global(monkeypatch):
    """Per-task env var wins over global env var."""
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "7.5")
    monkeypatch.setenv("MTG_LLM_BUDGET_USD", "3.0")
    assert llm_budgets.select_budget_usd("synth_gameplan") == 7.5


def test_select_budget_usd_global_env_beats_default(monkeypatch):
    """Global env var wins over the built-in default for unset tasks."""
    monkeypatch.delenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", raising=False)
    monkeypatch.setenv("MTG_LLM_BUDGET_USD", "12.34")
    assert llm_budgets.select_budget_usd("synth_gameplan") == 12.34


def test_select_budget_usd_default_fallback(monkeypatch):
    """With no overrides, the table default is returned verbatim."""
    monkeypatch.delenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", raising=False)
    monkeypatch.delenv("MTG_LLM_BUDGET_USD", raising=False)
    assert (
        llm_budgets.select_budget_usd("synth_gameplan")
        == llm_budgets.DEFAULT_BUDGETS_USD["synth_gameplan"]
    )


def test_select_budget_usd_unparseable_env_falls_through(monkeypatch):
    """A garbage env value should not silently bypass the budget gate;
    it falls through to the next layer (here: the default)."""
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "not-a-float")
    monkeypatch.delenv("MTG_LLM_BUDGET_USD", raising=False)
    assert (
        llm_budgets.select_budget_usd("synth_gameplan")
        == llm_budgets.DEFAULT_BUDGETS_USD["synth_gameplan"]
    )


# ─── select_token_cap ───────────────────────────────────────────────────


def test_select_token_cap_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("MTG_LLM_TOKEN_CAP_AUDIT_DOC_FRESHNESS", "20000")
    monkeypatch.setenv("MTG_LLM_TOKEN_CAP", "30000")
    assert llm_budgets.select_token_cap(
        "audit_doc_freshness", override=1234
    ) == 1234


def test_select_token_cap_task_env_beats_global(monkeypatch):
    monkeypatch.setenv("MTG_LLM_TOKEN_CAP_AUDIT_DOC_FRESHNESS", "6000")
    monkeypatch.setenv("MTG_LLM_TOKEN_CAP", "16000")
    assert llm_budgets.select_token_cap("audit_doc_freshness") == 6000


def test_select_token_cap_global_env_beats_default(monkeypatch):
    monkeypatch.delenv("MTG_LLM_TOKEN_CAP_AUDIT_DOC_FRESHNESS", raising=False)
    monkeypatch.setenv("MTG_LLM_TOKEN_CAP", "16000")
    assert llm_budgets.select_token_cap("audit_doc_freshness") == 16000


def test_select_token_cap_default_fallback(monkeypatch):
    monkeypatch.delenv("MTG_LLM_TOKEN_CAP_AUDIT_DOC_FRESHNESS", raising=False)
    monkeypatch.delenv("MTG_LLM_TOKEN_CAP", raising=False)
    assert (
        llm_budgets.select_token_cap("audit_doc_freshness")
        == llm_budgets.DEFAULT_TOKEN_CAPS["audit_doc_freshness"]
    )


# ─── month_to_date_spend ────────────────────────────────────────────────


def test_month_to_date_spend_returns_zero_on_empty_log(metrics_file):
    """No records → $0 spend, no crash."""
    assert llm_budgets.month_to_date_spend_usd("synth_gameplan") == 0.0


def test_month_to_date_spend_filters_by_30day_cutoff(metrics_file):
    """A 35-day-old record is excluded; a 5-day-old record is included."""
    now = datetime.now(timezone.utc)
    _write_record(
        metrics_file,
        task="synth_gameplan",
        cost_usd=0.42,
        ts=now - timedelta(days=35),
    )
    _write_record(
        metrics_file,
        task="synth_gameplan",
        cost_usd=0.10,
        ts=now - timedelta(days=5),
    )
    spent = llm_budgets.month_to_date_spend_usd("synth_gameplan")
    assert spent == pytest.approx(0.10)


def test_month_to_date_spend_filters_by_task(metrics_file):
    """Only the requested task's spend is summed."""
    now = datetime.now(timezone.utc)
    _write_record(
        metrics_file,
        task="synth_gameplan",
        cost_usd=0.30,
        ts=now - timedelta(days=1),
    )
    _write_record(
        metrics_file,
        task="audit_doc_freshness",
        cost_usd=0.05,
        ts=now - timedelta(days=1),
    )
    assert llm_budgets.month_to_date_spend_usd("synth_gameplan") == pytest.approx(0.30)
    assert llm_budgets.month_to_date_spend_usd("audit_doc_freshness") == pytest.approx(0.05)


def test_month_to_date_spend_skips_bad_cost_values(metrics_file, monkeypatch):
    """A garbage cost value should be skipped, not crash the gate."""
    now = datetime.now(timezone.utc)
    bad_record = {
        "ts": now.isoformat(),
        "task": "synth_gameplan",
        "model": "test",
        "prompt_version": 1,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": "not-a-number",
        "cache_hit": False,
        "input_hash": "x",
        "output_type": "X",
        "duration_ms": 0,
        "success": True,
        "error": None,
    }
    with metrics_file.open("a") as f:
        f.write(json.dumps(bad_record) + "\n")
    _write_record(
        metrics_file,
        task="synth_gameplan",
        cost_usd=0.07,
        ts=now,
    )
    assert llm_budgets.month_to_date_spend_usd("synth_gameplan") == pytest.approx(0.07)


# ─── estimate_call_cost_usd ─────────────────────────────────────────────


def test_estimate_call_cost_usd_uses_metrics_pricing():
    """Estimate must agree with `llm_metrics.estimate_cost_usd` for the
    derived (input, output_floor or input//8) pair."""
    model = "anthropic:claude-sonnet-4-6"
    input_tokens = 4000
    expected_output = max(
        llm_budgets.OUTPUT_TOKEN_FLOOR,
        input_tokens // llm_budgets.OUTPUT_TOKEN_RATIO_DENOM,
    )
    expected = llm_metrics.estimate_cost_usd(model, input_tokens, expected_output)
    actual = llm_budgets.estimate_call_cost_usd("synth_gameplan", model, input_tokens)
    assert actual == pytest.approx(expected)


def test_estimate_call_cost_usd_floors_output_tokens():
    """For a tiny input (<800 tokens) the output estimate hits the
    floor rather than collapsing to ~0 cost."""
    model = "anthropic:claude-sonnet-4-6"
    actual = llm_budgets.estimate_call_cost_usd("synth_gameplan", model, 10)
    floor_cost = llm_metrics.estimate_cost_usd(
        model, 10, llm_budgets.OUTPUT_TOKEN_FLOOR
    )
    assert actual == pytest.approx(floor_cost)


def test_estimate_call_cost_usd_unknown_model_zero():
    """Unknown model id → $0 (mirrors `llm_metrics.estimate_cost_usd`)."""
    actual = llm_budgets.estimate_call_cost_usd("synth_gameplan", "not-real", 999)
    assert actual == 0.0


# ─── check_budget ───────────────────────────────────────────────────────


def test_check_budget_passes_when_under_cap(metrics_file, monkeypatch):
    """An empty log + tiny estimate → no exception."""
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "10.00")
    # No records written -> MTD = 0; small estimate stays well under cap.
    llm_budgets.check_budget("synth_gameplan", "anthropic:claude-sonnet-4-6", 100)


def test_check_budget_raises_when_over_cap(metrics_file, monkeypatch):
    """When MTD spend already exceeds the cap, the next call is gated."""
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "0.10")
    now = datetime.now(timezone.utc)
    _write_record(
        metrics_file,
        task="synth_gameplan",
        cost_usd=0.20,
        ts=now - timedelta(days=1),
    )
    with pytest.raises(llm_budgets.BudgetExceededError) as excinfo:
        llm_budgets.check_budget(
            "synth_gameplan",
            "anthropic:claude-sonnet-4-6",
            1000,
        )
    err = excinfo.value
    assert err.task == "synth_gameplan"
    assert err.cap_usd == pytest.approx(0.10)
    assert err.mtd_usd == pytest.approx(0.20)
    assert err.est_cost_usd >= 0


def test_check_budget_override_beats_env(metrics_file, monkeypatch):
    """Explicit budget override applies even when env var is set."""
    monkeypatch.setenv("MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN", "100.00")
    now = datetime.now(timezone.utc)
    _write_record(
        metrics_file,
        task="synth_gameplan",
        cost_usd=5.0,
        ts=now,
    )
    with pytest.raises(llm_budgets.BudgetExceededError):
        llm_budgets.check_budget(
            "synth_gameplan",
            "anthropic:claude-sonnet-4-6",
            500,
            budget_override=1.0,
        )


def test_budget_exceeded_error_message_includes_env_var_name():
    """The error must tell the operator which env var raises the cap."""
    err = llm_budgets.BudgetExceededError(
        task="synth_gameplan",
        mtd_usd=2.5,
        cap_usd=2.0,
        est_cost_usd=0.1,
    )
    msg = str(err)
    assert "MTG_LLM_BUDGET_USD_SYNTH_GAMEPLAN" in msg
    assert "synth_gameplan" in msg
    assert "$2.0" in msg or "$2.00" in msg


# ─── estimate_input_tokens ──────────────────────────────────────────────


def test_estimate_input_tokens_string_input():
    """A string of N chars yields ~N/CHARS_PER_TOKEN_ESTIMATE tokens."""
    s = "x" * 4000
    n = llm_budgets.estimate_input_tokens(s)
    assert n == 4000 // llm_budgets.CHARS_PER_TOKEN_ESTIMATE


def test_estimate_input_tokens_dict_input():
    """Non-string inputs are reduced via repr() and still produce a
    nonzero estimate."""
    d = {"deck": "Burn", "size": 60}
    n = llm_budgets.estimate_input_tokens(d)
    assert n >= 1


def test_estimate_input_tokens_minimum_one():
    """Even empty input estimates to >=1 token (avoid div-by-zero gate)."""
    assert llm_budgets.estimate_input_tokens("") >= 1
