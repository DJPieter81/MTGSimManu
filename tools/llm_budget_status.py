"""LLM budget status — report MTD spend vs per-task USD cap.

Phase I-7 companion to ``ai/llm_budgets.py``.  Reads the same
``cache/llm/calls.jsonl`` log that the cost report uses, sums spend
per task over the last 30 days, and renders a table comparing it
against the per-task cap (resolved via the same env-var override
chain as :func:`ai.llm_budgets.select_budget_usd`).

CLI::

    # All tasks (default) — MTD spend vs cap, OK / over-budget summary.
    python -m tools.llm_budget_status

    # Single task drilldown
    python -m tools.llm_budget_status --task synth_gameplan

Exit code is 0 when every task is under its cap and 1 when at least
one task is over — useful to wire into a CI healthcheck.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional, get_args

from ai.llm_budgets import (
    BUDGET_WINDOW_DAYS,
    DEFAULT_BUDGETS_USD,
    month_to_date_spend_usd,
    select_budget_usd,
)
from ai.llm_models import LLMTask


def _pct_used(mtd: float, cap: float) -> str:
    if cap <= 0:
        return "  -"
    return f"{round(100 * mtd / cap)}%"


def render_status(task_filter: Optional[str] = None) -> tuple[str, bool]:
    """Build the status table.  Returns ``(text, any_over_budget)``."""
    tasks = list(get_args(LLMTask))
    if task_filter:
        if task_filter not in tasks:
            return (
                f"Unknown task: {task_filter!r}.  Known: {sorted(tasks)}",
                False,
            )
        tasks = [task_filter]

    lines: list[str] = []
    lines.append(
        f"LLM budget status — last {BUDGET_WINDOW_DAYS} days "
        f"(MTD spend vs per-task cap)"
    )
    lines.append("")
    lines.append(
        f"{'Task':<22}| {'MTD spend':>10} | {'Cap':>9} | "
        f"{'Remaining':>10} | {'% used':>7}"
    )
    lines.append("-" * 22 + "+" + "-" * 12 + "+" + "-" * 11
                 + "+" + "-" * 12 + "+" + "-" * 9)

    any_over = False
    for task in sorted(tasks):
        mtd = month_to_date_spend_usd(task)
        cap = select_budget_usd(task)
        remaining = max(0.0, cap - mtd)
        over = mtd >= cap
        any_over = any_over or over
        lines.append(
            f"{task:<22}| ${mtd:>8.4f} | ${cap:>7.2f} | "
            f"${remaining:>8.4f} | {_pct_used(mtd, cap):>7}"
        )

    lines.append("")
    if any_over:
        lines.append("STATUS: at least one task is OVER budget.  "
                     "Raise the cap with the env var listed in the error "
                     "or wait for the 30-day window to roll forward.")
    else:
        lines.append("STATUS: OK (no caps reached)")
    return "\n".join(lines), any_over


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="llm_budget_status",
        description="Per-task LLM USD budget status from cache/llm/calls.jsonl.",
    )
    p.add_argument(
        "--task", type=str, default=None,
        help=f"Restrict to one task.  Known: {sorted(DEFAULT_BUDGETS_USD)}.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    text, any_over = render_status(task_filter=args.task)
    print(text)
    return 1 if any_over else 0


if __name__ == "__main__":
    sys.exit(main())
