"""ISMCTS budget-shape sweep for the Phase-5 acceptance gate.

Background
----------
PR #367 (2026-05-09) landed the Phase 5 production-scorer adapter so
the 12-fixture acceptance gate in
``tests/test_ismcts_acceptance_real.py`` is now apples-to-apples
against the production heuristic baseline.  At the documented default
budget (n_rollouts=500, rollout_depth=2, n_determinizations=50) the
gate produces *2 strict ISMCTS wins / 1 strict heuristic regression /
7 ties* against both the synthetic and the production baseline — short
of the ≥4 wins / 0 regressions threshold from the scoping doc.

This tool sweeps ``(n_rollouts, rollout_depth)`` to identify whether a
realistic budget shape clears the gate, or whether the snapshot-only
fixtures bottleneck the strict-win count regardless of search depth
(in which case the deliverable is the diagnostic doc, not a config
change).

Usage
-----
::

    python tools/ismcts_budget_sweep.py \
        --out data/ismcts_budget_sweep_2026_05_10.csv

By default sweeps rollouts ∈ {100, 500, 1000, 2000, 5000} and depths
∈ {1, 2, 3, 4}.  Determinizations are pinned at 50 (the scoping-doc
default).  Each (rollouts, depth) cell has a 60-second wall-clock cap;
exceeded cells write ``timeout`` rows and skip.

The whole sweep is hard-capped at ``--time-budget`` seconds (default
900 = 15 min) so it can run as a one-shot in CI / acceptance protocols
without runaway.  Cells beyond the time budget are written as
``budget_exhausted`` and skipped.

Output columns
--------------
``rollouts, depth, determinizations, ismcts_wins, ties,
heuristic_wins, regressions, wall_clock_per_fixture, status``

where ``status`` ∈ ``{ok, timeout, budget_exhausted}`` and
``regressions`` == ``heuristic_wins`` (kept as a separate column for
acceptance-gate readability).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


from ai.ev_evaluator import EVSnapshot
from ai.search.ab_compare import acceptance_gate
from ai.search.evplayer_scorer_adapter import make_production_picker
from ai.search.ismcts import SearchConfig
from ai.search.snapshot_adapter import (
    ActionToken,
    apply_action,
    enumerate_actions,
    evaluate_terminal,
    heuristic_rollout,
    make_search_state,
)


FIXTURES_PATH = REPO_ROOT / "tests" / "fixtures" / "ismcts_acceptance_fixtures.jsonl"


def _load_fixtures() -> List[dict]:
    rows: List[dict] = []
    with FIXTURES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _state_factory_for(fixture):
    def _factory():
        snap = EVSnapshot(**fixture["snapshot"])
        actions = [
            ActionToken(
                kind=a["kind"], label=a["label"],
                delta=a["delta"], cost=a.get("cost", 0),
            )
            for a in fixture["available_actions"]
        ]
        return make_search_state(snap, actions)
    return _factory


def _synthetic_picker_factory(fixture):
    """Synthetic 1-ply scorer baseline (snapshot_adapter.heuristic_rollout)."""
    def _picker(state):
        rng = random.Random(fixture.get("id", 0))
        return heuristic_rollout(state, rng)
    return _picker


def _production_picker_factory(fixture):
    """Production-scorer baseline (evplayer_scorer_adapter)."""
    archetype = fixture.get("archetype")
    return make_production_picker(archetype=archetype)


BASELINES = {
    "synthetic": _synthetic_picker_factory,
    "production": _production_picker_factory,
}


def run_cell(
    fixtures: Sequence[dict],
    n_rollouts: int,
    rollout_depth: int,
    n_determinizations: int,
    picker_factory,
    cell_timeout_s: float,
) -> Tuple[Optional[dict], float, str]:
    """Run the acceptance gate once at a given budget.

    Returns ``(report, wall_clock_seconds, status)`` where ``status``
    is ``"ok"`` or ``"timeout"``. On timeout, ``report`` is ``None``.

    Wall-clock is wrapped around the ``acceptance_gate`` call. The
    inner harness has no timeout primitive — Python doesn't ship one
    that is safe to interrupt CPU-bound code without threads — so the
    timeout is enforced *after* the call: if the cell takes longer
    than ``cell_timeout_s``, we still record the result but mark the
    cell as ``timeout`` to signal it's not feasible for CI.

    For configs we know in advance might be expensive, ``main`` checks
    the elapsed time per fixture during the first cell run and skips
    proportionally larger cells.
    """
    config = SearchConfig(
        n_rollouts=n_rollouts,
        rollout_depth=rollout_depth,
        n_determinizations=n_determinizations,
        seed=42,
    )
    t0 = time.monotonic()
    report = acceptance_gate(
        fixtures=list(fixtures),
        ismcts_config=config,
        heuristic_picker_factory=picker_factory,
        enumerate_actions=enumerate_actions,
        rollout_policy=heuristic_rollout,
        evaluate_terminal=evaluate_terminal,
        transition=apply_action,
        state_factory_for=_state_factory_for,
        n_rollouts=20,    # forward-sim count (variance reduction)
        sim_depth=2,      # forward-sim depth (matches existing gate)
    )
    elapsed = time.monotonic() - t0
    status = "ok"
    if elapsed > cell_timeout_s:
        status = "timeout"
    return report, elapsed, status


def _write_row(writer, row: dict) -> None:
    writer.writerow(row)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "data" / "ismcts_budget_sweep_2026_05_10.csv",
        help="output CSV path",
    )
    parser.add_argument(
        "--rollouts",
        type=int,
        nargs="+",
        default=[100, 500, 1000, 2000, 5000],
        help="n_rollouts values to sweep",
    )
    parser.add_argument(
        "--depths",
        type=int,
        nargs="+",
        default=[1, 2, 3, 4],
        help="rollout_depth values to sweep",
    )
    parser.add_argument(
        "--determinizations",
        type=int,
        nargs="+",
        default=[50],
        help="n_determinizations values to sweep (default 50)",
    )
    parser.add_argument(
        "--baseline",
        choices=("synthetic", "production", "both"),
        default="both",
        help="which heuristic baseline to compare against",
    )
    parser.add_argument(
        "--cell-timeout",
        type=float,
        default=60.0,
        help="per-cell wall-clock cap in seconds (default 60)",
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        default=900.0,
        help="total sweep wall-clock cap in seconds (default 900 = 15 min)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    fixtures = _load_fixtures()
    assert len(fixtures) == 12, f"expected 12 fixtures, got {len(fixtures)}"

    baselines: List[str]
    if args.baseline == "both":
        baselines = ["synthetic", "production"]
    else:
        baselines = [args.baseline]

    args.out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "baseline",
        "rollouts",
        "depth",
        "determinizations",
        "ismcts_wins",
        "ties",
        "heuristic_wins",
        "regressions",
        "passed",
        "wall_clock_total",
        "wall_clock_per_fixture",
        "status",
    ]

    # Sweep is rollout-major: for each baseline, sweep all
    # (rollouts × depth × determinizations) combos.  Per-cell wall
    # clock is logged; total budget guard skips remaining cells
    # once exceeded.
    sweep_start = time.monotonic()

    with args.out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        best_per_baseline: dict = {}

        for baseline_name in baselines:
            picker_factory = BASELINES[baseline_name]
            cells: List[Tuple[int, int, int]] = []
            for d in args.determinizations:
                for r in args.rollouts:
                    for depth in args.depths:
                        cells.append((r, depth, d))

            for r, depth, dets in cells:
                elapsed_total = time.monotonic() - sweep_start
                if elapsed_total > args.time_budget:
                    row = {
                        "baseline": baseline_name,
                        "rollouts": r,
                        "depth": depth,
                        "determinizations": dets,
                        "ismcts_wins": "",
                        "ties": "",
                        "heuristic_wins": "",
                        "regressions": "",
                        "passed": "",
                        "wall_clock_total": "",
                        "wall_clock_per_fixture": "",
                        "status": "budget_exhausted",
                    }
                    _write_row(writer, row)
                    continue

                try:
                    report, wall_clock, status = run_cell(
                        fixtures=fixtures,
                        n_rollouts=r,
                        rollout_depth=depth,
                        n_determinizations=dets,
                        picker_factory=picker_factory,
                        cell_timeout_s=args.cell_timeout,
                    )
                except Exception as exc:
                    row = {
                        "baseline": baseline_name,
                        "rollouts": r,
                        "depth": depth,
                        "determinizations": dets,
                        "ismcts_wins": "",
                        "ties": "",
                        "heuristic_wins": "",
                        "regressions": "",
                        "passed": "",
                        "wall_clock_total": "",
                        "wall_clock_per_fixture": "",
                        "status": f"error:{type(exc).__name__}",
                    }
                    _write_row(writer, row)
                    continue

                wins = report["ismcts_strict_wins"]
                losses = report["heuristic_strict_wins"]
                ties = report["ties"]
                passed = report["passed"]
                row = {
                    "baseline": baseline_name,
                    "rollouts": r,
                    "depth": depth,
                    "determinizations": dets,
                    "ismcts_wins": wins,
                    "ties": ties,
                    "heuristic_wins": losses,
                    "regressions": losses,
                    "passed": str(bool(passed)).lower(),
                    "wall_clock_total": f"{wall_clock:.3f}",
                    "wall_clock_per_fixture": (
                        f"{wall_clock / max(1, len(fixtures)):.3f}"
                    ),
                    "status": status,
                }
                _write_row(writer, row)
                fh.flush()

                # Track best-passing cell per baseline (lowest cost).
                if passed:
                    prev = best_per_baseline.get(baseline_name)
                    if prev is None or wall_clock < prev["wall_clock_total"]:
                        best_per_baseline[baseline_name] = {
                            "rollouts": r,
                            "depth": depth,
                            "determinizations": dets,
                            "ismcts_wins": wins,
                            "heuristic_wins": losses,
                            "ties": ties,
                            "wall_clock_total": wall_clock,
                        }

                print(
                    f"[{baseline_name:10s}] "
                    f"rollouts={r:>5} depth={depth} dets={dets:>3} "
                    f"-> wins={wins} ties={ties} regr={losses} "
                    f"passed={passed} ({wall_clock:.2f}s, {status})",
                    flush=True,
                )

    # Summary
    print()
    print("=" * 60)
    print("Sweep summary")
    print("=" * 60)
    if not best_per_baseline:
        print("No (rollouts, depth, dets) combo cleared the gate.")
        print("Recommended next step: write the budget-ceiling diagnostic doc.")
    else:
        for name, best in best_per_baseline.items():
            print(
                f"  {name}: rollouts={best['rollouts']} "
                f"depth={best['depth']} dets={best['determinizations']} "
                f"wins={best['ismcts_wins']} regr={best['heuristic_wins']} "
                f"ties={best['ties']} "
                f"({best['wall_clock_total']:.2f}s)"
            )
    print()
    print(f"CSV written to: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
