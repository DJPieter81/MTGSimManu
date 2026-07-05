"""Pricing-table correctness — `ai.llm_metrics.MODEL_PRICING_USD_PER_MTOKEN`.

The budget gate (`ai.llm_budgets.check_budget`) multiplies token counts
by this table.  A stale entry makes the gate porous: the 2026-07 audit
found Haiku 4.5 priced at its Haiku-3.5 rates (0.25/1.25 vs the
published 1.00/5.00), so the gate under-estimated Haiku spend ~4x and
let ~4x the intended budget through before raising
``BudgetExceededError``.

Rates asserted here are published Anthropic pricing (2026-07 refresh):

    Haiku 4.5     in $1.00 / out  $5.00 per MTok
    Sonnet 4.6    in $3.00 / out $15.00
    Sonnet 5      in $3.00 / out $15.00 (list; intro promo excluded)
    Opus 4.7      in $5.00 / out $25.00
    Opus 4.8      in $5.00 / out $25.00

When Anthropic publishes new rates, update the table AND these tests in
the same commit — the test is the provenance record.
"""
from __future__ import annotations

import pytest

from ai.llm_budgets import check_budget, BudgetExceededError
from ai.llm_metrics import MODEL_PRICING_USD_PER_MTOKEN, estimate_cost_usd


# ─── Corrected rates (the 2026-07 bug fix) ─────────────────────────────

def test_haiku_4_5_priced_at_published_rates_not_haiku_3_5_rates():
    """Haiku 4.5 is $1.00 in / $5.00 out per MTok.

    The stale 0.25/1.25 values were Haiku 3.5 pricing; keeping them
    made the budget gate under-count Haiku spend by 4x.
    """
    for model in (
        "anthropic:claude-haiku-4-5",
        "anthropic:claude-haiku-4-5-20251001",
    ):
        pricing = MODEL_PRICING_USD_PER_MTOKEN[model]
        assert pricing["in"] == 1.00, model
        assert pricing["out"] == 5.00, model


def test_opus_4_7_priced_at_published_rates():
    """Opus 4.7 is $5.00 in / $25.00 out per MTok (was wrongly 15/75)."""
    for model in (
        "anthropic:claude-opus-4-7",
        "anthropic:claude-opus-4-7-1m",
    ):
        pricing = MODEL_PRICING_USD_PER_MTOKEN[model]
        assert pricing["in"] == 5.00, model
        assert pricing["out"] == 25.00, model


def test_sonnet_4_6_rates_unchanged():
    """Sonnet 4.6 was already correct — pin it so a refresh can't drift it."""
    pricing = MODEL_PRICING_USD_PER_MTOKEN["anthropic:claude-sonnet-4-6"]
    assert pricing["in"] == 3.00
    assert pricing["out"] == 15.00


# ─── Current-generation entries (so overrides don't fall through) ─────

def test_current_generation_models_present_in_pricing_table():
    """`estimate_cost_usd` treats unknown models as FREE ($0), which
    silently disables the budget gate for any operator override onto a
    current-generation model.  The table must therefore carry entries
    for the models `select_model` can plausibly resolve to."""
    assert "anthropic:claude-sonnet-5" in MODEL_PRICING_USD_PER_MTOKEN
    assert "anthropic:claude-opus-4-8" in MODEL_PRICING_USD_PER_MTOKEN


def test_sonnet_5_priced_at_list_rates():
    """Sonnet 5 list price is $3.00/$15.00.  An introductory 2.00/10.00
    runs through 2026-08-31 — the table uses LIST price so the budget
    gate stays conservative when the promo lapses."""
    pricing = MODEL_PRICING_USD_PER_MTOKEN["anthropic:claude-sonnet-5"]
    assert pricing["in"] == 3.00
    assert pricing["out"] == 15.00


def test_opus_4_8_priced_at_published_rates():
    pricing = MODEL_PRICING_USD_PER_MTOKEN["anthropic:claude-opus-4-8"]
    assert pricing["in"] == 5.00
    assert pricing["out"] == 25.00


# ─── The gate itself sees the corrected rates ──────────────────────────

def test_estimate_cost_usd_haiku_million_tokens():
    """1M in + 1M out on Haiku 4.5 costs $6.00 under published rates
    (the stale table said $1.50 — the 4x porous-gate bug in dollars)."""
    cost = estimate_cost_usd("anthropic:claude-haiku-4-5", 1_000_000, 1_000_000)
    assert cost == pytest.approx(6.00)


def test_budget_gate_blocks_haiku_call_that_stale_pricing_let_through(
    tmp_path, monkeypatch
):
    """Gate-level regression: a planned Haiku call whose true cost
    exceeds the cap must raise, even where the stale 4x-low estimate
    would have passed it.

    2M estimated input tokens on Haiku 4.5:
      stale table:   2M × $0.25/M + 350K out (est.) × $1.25/M ≈ $0.94  < $1 cap → passes
      correct table: 2M × $1.00/M + 250K out (est.) × $5.00/M ≈ $3.25  > $1 cap → blocks
    """
    from ai import llm_metrics
    # Empty spend history — the whole cap is available to this one call.
    monkeypatch.setattr(llm_metrics, "METRICS_FILE", tmp_path / "calls.jsonl")
    with pytest.raises(BudgetExceededError):
        check_budget(
            "handler_audit",  # $1.00 default cap
            "anthropic:claude-haiku-4-5",
            2_000_000,
        )
