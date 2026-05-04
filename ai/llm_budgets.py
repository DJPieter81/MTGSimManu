"""Per-task token + USD budget caps for LLM calls.

Phase I-7 of the cost-aware LLM strategy.  Builds on top of the
metrics layer (PR #269) and the cache layer (PR #266) to enforce
two budget signals on every ``Agent.run_sync`` invocation:

  * **Per-call input-token cap** — passed to pydantic-ai's
    ``UsageLimits(input_tokens_limit=N)`` so a single runaway prompt
    cannot drain budget.  Defaults to 8000 input tokens per call;
    each task can override per env var.

  * **Per-task USD budget cap** — enforced *before* the API call by
    summing ``cost_usd`` over the last 30 days from
    ``cache/llm/calls.jsonl``.  If month-to-date spend on the task
    plus the estimated cost of the planned call would exceed the
    cap, ``check_budget`` raises :class:`BudgetExceededError`.  The
    metrics layer never sees the call (no API request happens), so
    a budget block is also free.

Resolution order for both knobs (mirrors ``ai.llm_models``):

  1. Explicit ``override`` argument to :func:`select_budget_usd` /
     :func:`select_token_cap`.
  2. ``MTG_LLM_BUDGET_USD_<TASK_UPPER>`` /
     ``MTG_LLM_TOKEN_CAP_<TASK_UPPER>`` env var — per-task override.
  3. ``MTG_LLM_BUDGET_USD`` / ``MTG_LLM_TOKEN_CAP`` env var — global
     override.
  4. ``DEFAULT_BUDGETS_USD[task]`` / ``DEFAULT_TOKEN_CAPS[task]`` —
     built-in default.

Defaults are conservative — design-time tools should be ~$1/month
each.  Generative tasks (``synth_gameplan``, ``diagnose_replay``)
get a $5/month cap because they can be re-run across the deck list
and benefit from larger token windows.

Why not a hard wallet check on Anthropic?  Their API has no
month-to-date introspection endpoint, and we want the gate to fire
*before* the network round-trip.  Treating ``cache/llm/calls.jsonl``
as the source of truth gives us deterministic, offline enforcement
that mirrors the production cost report (``tools/llm_cost_report.py``).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from ai.llm_metrics import estimate_cost_usd, iter_calls
from ai.llm_models import LLMTask


# ─── Defaults ──────────────────────────────────────────────────────────
#
# Per-task USD caps per 30-day rolling window.  Picked from the Phase I
# design discussion: design-time tools (audit / handler / failing-test)
# are cheap and bounded, so $1/month is plenty.  Generative tasks
# (synth_gameplan, diagnose_replay) get $5/month — sized for monthly
# deck-list re-runs (16 decks × ~$0.06/call × a couple of cycles).
DEFAULT_BUDGETS_USD: dict[LLMTask, float] = {
    "synth_gameplan":      5.00,
    "diagnose_replay":     5.00,
    "audit_doc_freshness": 1.00,
    "handler_audit":       1.00,
    "failing_test_spec":   1.00,
}

# Per-task input-token caps per call.  8000 is sufficient for the
# generative tasks that ship today; the lighter extraction tasks get
# a tighter 4000-token ceiling so a runaway few-shot block can't
# silently pad the prompt.
DEFAULT_TOKEN_CAPS: dict[LLMTask, int] = {
    "synth_gameplan":      8000,
    "diagnose_replay":     8000,
    "audit_doc_freshness": 4000,
    "handler_audit":       4000,
    "failing_test_spec":   4000,
}

# Fallback budget when a caller passes an unknown task literal.
# Conservative to fail loudly rather than over-spend.
FALLBACK_BUDGET_USD = 1.00
FALLBACK_TOKEN_CAP = 8000

# Rolling window (days) over which spend is summed against the cap.
# Matches the default window in tools/llm_cost_report.py so the
# report and the gate agree on what "month-to-date" means.
BUDGET_WINDOW_DAYS = 30

# Output tokens are typically a fraction of input tokens for the
# structured-output tasks (the schemas are small).  ~1/8 is a safe
# upper bound for cost estimation; over-estimating just makes the
# budget gate slightly more conservative, which is the right error.
OUTPUT_TOKEN_RATIO_DENOM = 8

# Floor on estimated output tokens — even a tiny prompt produces a
# nonzero structured response.  Without a floor, very small inputs
# would estimate ~0 cost and slip past the gate by a wider margin
# than is honest.
OUTPUT_TOKEN_FLOOR = 100


GLOBAL_BUDGET_ENV = "MTG_LLM_BUDGET_USD"
"""Global env var: applies to every task unless a task-specific var is set."""

TASK_BUDGET_ENV_FMT = "MTG_LLM_BUDGET_USD_{task_upper}"
"""Per-task env var template: ``MTG_LLM_BUDGET_USD_<TASK_UPPER>``."""

GLOBAL_TOKEN_CAP_ENV = "MTG_LLM_TOKEN_CAP"
"""Global env var: per-call input-token ceiling (any task)."""

TASK_TOKEN_CAP_ENV_FMT = "MTG_LLM_TOKEN_CAP_{task_upper}"
"""Per-task env var template: ``MTG_LLM_TOKEN_CAP_<TASK_UPPER>``."""


class BudgetExceededError(Exception):
    """Raised when a planned call would push month-to-date spend over the
    per-task cap.  Carries the numeric breakdown so callers (and the
    error message) can show the exact deficit and the env var to raise.
    """

    def __init__(
        self,
        task: str,
        mtd_usd: float,
        cap_usd: float,
        est_cost_usd: float,
    ) -> None:
        self.task = task
        self.mtd_usd = mtd_usd
        self.cap_usd = cap_usd
        self.est_cost_usd = est_cost_usd
        env_name = TASK_BUDGET_ENV_FMT.format(task_upper=task.upper())
        super().__init__(
            f"LLM budget cap reached for {task}: "
            f"${mtd_usd:.4f} spent in last {BUDGET_WINDOW_DAYS}d, "
            f"cap ${cap_usd:.2f}, this call would add ~${est_cost_usd:.4f}. "
            f"Raise via env: {env_name}=N"
        )


def _read_float_env(name: str) -> Optional[float]:
    """Parse a float env var.  Returns ``None`` if unset or unparseable
    (a bad value should not silently bypass the budget gate)."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_int_env(name: str) -> Optional[int]:
    """Parse an int env var.  Returns ``None`` if unset or unparseable."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def select_budget_usd(
    task: LLMTask,
    *,
    override: Optional[float] = None,
) -> float:
    """Resolve the USD budget cap for ``task``.

    Resolution order:
        1. ``override`` argument.
        2. ``MTG_LLM_BUDGET_USD_<TASK_UPPER>`` env var.
        3. ``MTG_LLM_BUDGET_USD`` env var.
        4. ``DEFAULT_BUDGETS_USD[task]``.
        5. :data:`FALLBACK_BUDGET_USD` if the task isn't known.
    """
    if override is not None:
        return override
    task_specific = _read_float_env(
        TASK_BUDGET_ENV_FMT.format(task_upper=task.upper())
    )
    if task_specific is not None:
        return task_specific
    glob = _read_float_env(GLOBAL_BUDGET_ENV)
    if glob is not None:
        return glob
    return DEFAULT_BUDGETS_USD.get(task, FALLBACK_BUDGET_USD)


def select_token_cap(
    task: LLMTask,
    *,
    override: Optional[int] = None,
) -> int:
    """Resolve the per-call input-token cap for ``task``.

    Resolution order mirrors :func:`select_budget_usd`.
    """
    if override is not None:
        return override
    task_specific = _read_int_env(
        TASK_TOKEN_CAP_ENV_FMT.format(task_upper=task.upper())
    )
    if task_specific is not None:
        return task_specific
    glob = _read_int_env(GLOBAL_TOKEN_CAP_ENV)
    if glob is not None:
        return glob
    return DEFAULT_TOKEN_CAPS.get(task, FALLBACK_TOKEN_CAP)


def month_to_date_spend_usd(task: LLMTask) -> float:
    """Sum of ``cost_usd`` for ``task`` from ``cache/llm/calls.jsonl``
    over the last :data:`BUDGET_WINDOW_DAYS` days.  Returns 0.0 on a
    missing log file (first run) or empty window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=BUDGET_WINDOW_DAYS)
    total = 0.0
    for record in iter_calls(since=cutoff, task=task):
        try:
            total += float(record.get("cost_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            # Bad cost value: skip rather than crash the gate.
            continue
    return total


def estimate_call_cost_usd(
    task: LLMTask,
    model: str,
    input_tokens_est: int,
) -> float:
    """Rough cost estimate for a planned call.  Used by
    :func:`check_budget` to gate before the API hit.

    Output token count is estimated as ``max(OUTPUT_TOKEN_FLOOR,
    input_tokens_est // OUTPUT_TOKEN_RATIO_DENOM)`` — a typical
    structured-output ratio.  Over-estimation is preferred (it just
    tightens the budget gate slightly).
    """
    output_tokens_est = max(
        OUTPUT_TOKEN_FLOOR,
        input_tokens_est // OUTPUT_TOKEN_RATIO_DENOM,
    )
    return estimate_cost_usd(model, input_tokens_est, output_tokens_est)


def check_budget(
    task: LLMTask,
    model: str,
    input_tokens_est: int,
    *,
    budget_override: Optional[float] = None,
) -> None:
    """Raise :class:`BudgetExceededError` if the planned call would push
    month-to-date spend on ``task`` over the cap.

    Called by :class:`ai.llm_agents.MeteredAgent` immediately before
    every ``run_sync`` so the API request never happens when budget
    is exhausted.
    """
    cap = select_budget_usd(task, override=budget_override)
    mtd = month_to_date_spend_usd(task)
    est = estimate_call_cost_usd(task, model, input_tokens_est)
    if mtd + est > cap:
        raise BudgetExceededError(task, mtd, cap, est)


# Approximate tokens-per-character ratio used when no real tokenizer
# is available.  The agreed industry rule of thumb is ~4 chars per
# token for English text; 1 token = 4 chars is the conservative side
# (rounds *up* on token count, so the budget gate is slightly more
# protective than reality).
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_input_tokens(user_prompt: object) -> int:
    """Rough character-based input-token estimate for budget gating.

    Uses ``len(repr(prompt)) // CHARS_PER_TOKEN_ESTIMATE`` so any
    JSON-serialisable shape works without depending on a tokenizer
    package.  The estimate only feeds :func:`estimate_call_cost_usd`
    and the gate; the metrics layer still records the exact token
    counts returned by the API on a successful call.
    """
    if isinstance(user_prompt, str):
        material = user_prompt
    else:
        # Fall back to repr() — handles dicts, BaseModel instances, and
        # any other shape uniformly.  A precise count isn't needed; an
        # order-of-magnitude estimate is enough for the gate.
        material = repr(user_prompt)
    return max(1, len(material) // CHARS_PER_TOKEN_ESTIMATE)


__all__ = [
    "DEFAULT_BUDGETS_USD",
    "DEFAULT_TOKEN_CAPS",
    "FALLBACK_BUDGET_USD",
    "FALLBACK_TOKEN_CAP",
    "BUDGET_WINDOW_DAYS",
    "GLOBAL_BUDGET_ENV",
    "TASK_BUDGET_ENV_FMT",
    "GLOBAL_TOKEN_CAP_ENV",
    "TASK_TOKEN_CAP_ENV_FMT",
    "BudgetExceededError",
    "select_budget_usd",
    "select_token_cap",
    "month_to_date_spend_usd",
    "estimate_call_cost_usd",
    "check_budget",
    "estimate_input_tokens",
]
