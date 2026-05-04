"""LLM cost report — aggregates ``cache/llm/calls.jsonl`` records.

Phase I-5 companion to ``ai/llm_metrics.py``.  Reads the append-only
JSONL log, groups by task and by model, and prints a per-task table
with totals, cache-hit rates, token counts, and USD spend.

CLI::

    # Last 30 days, per-task summary (default)
    python -m tools.llm_cost_report

    # Last N days
    python -m tools.llm_cost_report --days 7

    # Per-task drilldown (only show records for one task)
    python -m tools.llm_cost_report --task synth_gameplan

    # Output as JSON for further processing / dashboards
    python -m tools.llm_cost_report --json

If any task's spend in the window exceeds the budget threshold
(default $5), the report prints a recommendation to extend the
rule-based path before the LLM tier — this is the project's
escalation rule from the cost-aware design doc.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ai.llm_metrics import iter_calls


# Budget threshold (USD per window) above which a task is flagged
# as a "hot" path and recommended for rule-based extension.  Named
# constant so tests can pin the threshold without assuming the
# default; see CLAUDE.md ABSTRACTION CONTRACT — "no magic numbers".
HOT_TASK_USD_THRESHOLD = 5.0
"""Per-task spend (USD) above which a hot-task warning is emitted."""


def _empty_task_bucket() -> dict:
    return {
        "calls": 0,
        "cache_hits": 0,
        "api_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "duration_ms_total": 0,
    }


def aggregate(records: list[dict]) -> dict:
    """Aggregate a list of call records into per-task and per-model
    totals plus an overall summary.

    Returns a dict with keys::

        {
            "by_task":  {task: {calls, cache_hits, api_calls,
                                tokens_in, tokens_out, cost_usd,
                                duration_ms_total}},
            "by_model": {model: {calls, cost_usd}},
            "total":    {calls, cache_hits, api_calls,
                         cost_usd, tokens_in, tokens_out},
        }

    The shape is stable and is the same one emitted by ``--json``.
    """
    by_task: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    total = {
        "calls": 0,
        "cache_hits": 0,
        "api_calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
    }
    for r in records:
        task = r.get("task", "unknown")
        model = r.get("model", "unknown")
        cache_hit = bool(r.get("cache_hit", False))
        tokens_in = int(r.get("tokens_in", 0) or 0)
        tokens_out = int(r.get("tokens_out", 0) or 0)
        cost = float(r.get("cost_usd", 0.0) or 0.0)
        duration = int(r.get("duration_ms", 0) or 0)

        bucket = by_task.setdefault(task, _empty_task_bucket())
        bucket["calls"] += 1
        if cache_hit:
            bucket["cache_hits"] += 1
        else:
            bucket["api_calls"] += 1
        bucket["tokens_in"] += tokens_in
        bucket["tokens_out"] += tokens_out
        bucket["cost_usd"] += cost
        bucket["duration_ms_total"] += duration

        m_bucket = by_model.setdefault(model, {"calls": 0, "cost_usd": 0.0})
        m_bucket["calls"] += 1
        m_bucket["cost_usd"] += cost

        total["calls"] += 1
        if cache_hit:
            total["cache_hits"] += 1
        else:
            total["api_calls"] += 1
        total["tokens_in"] += tokens_in
        total["tokens_out"] += tokens_out
        total["cost_usd"] += cost

    return {"by_task": by_task, "by_model": by_model, "total": total}


def _fmt_pct(numer: int, denom: int) -> str:
    if denom == 0:
        return " 0%"
    return f"{round(100 * numer / denom)}%"


def _fmt_avg_ms(total_ms: int, calls: int) -> str:
    if calls == 0:
        return "    0"
    return f"{total_ms // calls:>5}"


def format_report(
    summary: dict,
    *,
    window_start: datetime,
    window_end: datetime,
    days: int,
    task_filter: Optional[str] = None,
    threshold: float = HOT_TASK_USD_THRESHOLD,
) -> str:
    """Render the per-task table.  See module docstring for shape."""
    lines: list[str] = []
    header_label = (
        f"LLM cost report — last {days} days "
        f"({window_start.date()} → {window_end.date()})"
    )
    if task_filter:
        header_label += f"  [task={task_filter}]"
    lines.append(header_label)
    lines.append("")
    lines.append(
        f"{'Task':<22}| {'Calls':>5} | {'Cache hits':>10} | {'API calls':>9} | "
        f"{'Tokens (in/out)':>17} | {'Cost (USD)':>10} | Avg ms/call"
    )
    lines.append("-" * 22 + "+" + "-" * 7 + "+" + "-" * 12 + "+"
                 + "-" * 11 + "+" + "-" * 19 + "+"
                 + "-" * 12 + "+" + "-" * 12)

    by_task = summary["by_task"]
    for task in sorted(by_task.keys()):
        b = by_task[task]
        toks = f"{b['tokens_in']:>7,} / {b['tokens_out']:>6,}"
        ch_str = f"{b['cache_hits']:>3} ({_fmt_pct(b['cache_hits'], b['calls']):>3})"
        lines.append(
            f"{task:<22}| {b['calls']:>5} | {ch_str:>10} | "
            f"{b['api_calls']:>9} | {toks:>17} | "
            f"${b['cost_usd']:>8.4f} | "
            f"{_fmt_avg_ms(b['duration_ms_total'], b['calls'])}"
        )

    lines.append("")
    lines.append(f"{'TOTAL':<22}  Calls={summary['total']['calls']}  "
                 f"Cost=${summary['total']['cost_usd']:.4f}")
    lines.append("")

    # Per-model breakdown
    if summary["by_model"]:
        lines.append("Per-model:")
        for model in sorted(summary["by_model"].keys()):
            mb = summary["by_model"][model]
            lines.append(f"  {model}: {mb['calls']} calls, ${mb['cost_usd']:.4f}")
        lines.append("")

    # Hot-task escalation rule.
    hot = [t for t, b in by_task.items() if b["cost_usd"] > threshold]
    if hot:
        lines.append(
            f"Hot tasks (cost > ${threshold:.2f}/window threshold): {', '.join(sorted(hot))}"
        )
        for t in sorted(hot):
            lines.append(
                f"  Recommendation: task `{t}` exceeds ${threshold:.2f} budget "
                "threshold. Consider extending the rule-based path before "
                "the LLM tier."
            )
    else:
        lines.append(f"Hot tasks (cost > ${threshold:.2f}/window threshold): NONE")

    # Monthly projection from the actual window.
    if days > 0 and summary["total"]["cost_usd"] > 0:
        per_day = summary["total"]["cost_usd"] / days
        lines.append(
            f"Recommendation: at current rate, monthly run = ${per_day * 30:.2f}"
        )

    return "\n".join(lines)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="llm_cost_report",
        description="Aggregate cache/llm/calls.jsonl into a per-task cost report.",
    )
    p.add_argument(
        "--days", type=int, default=30,
        help="Window size in days (default: 30).",
    )
    p.add_argument(
        "--task", type=str, default=None,
        help="Restrict to one task (e.g. synth_gameplan).",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Emit aggregation as JSON for downstream tools.",
    )
    p.add_argument(
        "--threshold", type=float, default=HOT_TASK_USD_THRESHOLD,
        help=f"Hot-task USD threshold (default: {HOT_TASK_USD_THRESHOLD}).",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(days=args.days)

    records = list(iter_calls(since=window_start, task=args.task))
    summary = aggregate(records)

    if args.as_json:
        payload = {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "days": args.days,
            "task_filter": args.task,
            "threshold_usd": args.threshold,
            "summary": summary,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(format_report(
        summary,
        window_start=window_start,
        window_end=window_end,
        days=args.days,
        task_filter=args.task,
        threshold=args.threshold,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
