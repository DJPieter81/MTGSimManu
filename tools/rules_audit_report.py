#!/usr/bin/env python3
"""Rank a rules-audit JSONL (engine/rules_audit, written by
`run_meta.py --rules-audit`) by rule: how many findings, in how many games and
deck pairs, with the most frequent detail lines — the backlog the next
rules units draw from.

    python tools/rules_audit_report.py audits/rules_audit_<stamp>.jsonl
    python tools/rules_audit_report.py audits/*.jsonl --top 5

Violations and census rows are ranked separately: a violation is a rule
the engine broke; a census row is a mechanic it has no model for.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List


def load(paths: Iterable[str]) -> List[dict]:
    rows: List[dict] = []
    for p in paths:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def summarize(rows: List[dict]) -> Dict[str, dict]:
    """{rule_id: {kind, count, games, pairs, top_details}} — `games` counts
    distinct (seed, deck1, deck2) keys, `pairs` distinct deck pairs.

    A violation's `count` is every finding (each is a real per-game event). A
    census row's `count` is DEDUPED to distinct (key, seed, deck pair): the
    engine's `census` dedupes only per process, so N parallel workers each
    re-emit the same (rule, key) once and the raw count inflates ~N×."""
    out: Dict[str, dict] = {}
    by_rule: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_rule[r.get("rule", "?")].append(r)
    for rule, items in by_rule.items():
        kind = items[0].get("kind", "violation")
        games = {(r.get("seed"), r.get("deck1"), r.get("deck2")) for r in items}
        pairs = {tuple(sorted((r.get("deck1") or "", r.get("deck2") or ""))) for r in items}
        details = Counter((r.get("detail") or r.get("key") or "") for r in items)
        if kind == "census":
            count = len({(r.get("key"), r.get("seed"),
                          r.get("deck1"), r.get("deck2")) for r in items})
        else:
            count = len(items)
        out[rule] = {
            "kind": kind,
            "count": count,
            "games": len(games),
            "pairs": len(pairs),
            "top_details": details.most_common(3),
        }
    return out


def format_summary(summary: Dict[str, dict], top: int = 20) -> str:
    if not summary:
        return "Rules audit: no findings."
    lines = []
    for kind, title in (("violation", "VIOLATIONS (a rule the engine broke)"),
                        ("census", "CENSUS (mechanics with no model)")):
        rows = [(rule, s) for rule, s in summary.items() if s["kind"] == kind]
        if not rows:
            continue
        if kind == "census":
            # Rank by breadth (distinct deck pairs, then games), not the
            # per-process-inflated raw count.
            rows.sort(key=lambda rs: (-rs[1]["pairs"], -rs[1]["games"], rs[0]))
        else:
            rows.sort(key=lambda rs: (-rs[1]["count"], rs[0]))
        lines.append(f"== {title} ==")
        lines.append(f"{'rule':34s} {'count':>6s} {'games':>6s} {'pairs':>6s}  top detail")
        for rule, s in rows[:top]:
            top_detail = s["top_details"][0][0] if s["top_details"] else ""
            lines.append(f"{rule:34s} {s['count']:6d} {s['games']:6d} {s['pairs']:6d}  "
                         f"{top_detail[:70]}")
        if len(rows) > top:
            lines.append(f"  … {len(rows) - top} more")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args(argv)
    rows = load(args.paths)
    print(f"{len(rows)} finding(s) in {len(args.paths)} file(s)")
    print(format_summary(summarize(rows), top=args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
