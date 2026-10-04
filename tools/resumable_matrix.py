"""A full matrix run that survives a process restart.

Runs exactly what `run_meta.py --matrix --parallel --save` runs (each
ordered pair through `tools.parallel_matrix._run_pair`, i.e. one
`run_matchup` on the matchup seed grid), but appends every finished cell
to a JSONL checkpoint as it completes. A relaunch with the same checkpoint
runs only the cells still missing. When every cell is present it writes
`metagame_results.json` through `run_meta.save_results` (the same shape a
normal run writes), the rules-audit findings if any, and rebuilds the site
(`build_dashboard.merge`).

Usage (relaunch the same command after an interruption):
    MTG_LLM_DECISION_SCORER_OFFLINE=1 python -m tools.resumable_matrix \\
        -n 60 --workers 3 --checkpoint /path/ck.jsonl [--rules-audit]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from functools import partial
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.parallel_matrix import PairCell, _run_pair  # noqa: E402


def load_checkpoint(path: Path) -> Dict[Tuple[str, str], PairCell]:
    cells: Dict[Tuple[str, str], PairCell] = {}
    if not Path(path).exists():
        return cells
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue   # a line cut by the interruption; that cell re-runs
        cells[(r["d1"], r["d2"])] = PairCell(r["wr"], r["wr_reverse"], r["draws"],
                                             r["aborted"], tuple(r.get("audit") or ()))
    return cells


def _append(path: Path, d1: str, d2: str, cell: PairCell) -> None:
    row = {"d1": d1, "d2": d2, "wr": cell.wr, "wr_reverse": cell.wr_reverse,
           "draws": cell.draws, "aborted": cell.aborted, "audit": list(cell.audit)}
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def merge_checkpoints(sources: List[Path], dest: Path) -> int:
    """Concatenate checkpoints (shards of one run) into `dest`; a pair that
    appears in several keeps its first record. Returns the cell count."""
    seen: Dict[Tuple[str, str], PairCell] = {}
    for src in sources:
        for pair, cell in load_checkpoint(Path(src)).items():
            seen.setdefault(pair, cell)
    Path(dest).write_text("")
    for (d1, d2), cell in seen.items():
        _append(Path(dest), d1, d2, cell)
    return len(seen)


def run_cells(decks: List[str], *, n_games: int, workers: int, checkpoint: Path,
              run_matchup_fn: Optional[Callable] = None,
              row: Optional[str] = None) -> Dict[Tuple[str, str], PairCell]:
    """Every ordered pair's cell (only `row`'s pairs when given): from the
    checkpoint when present, else run and appended to it the moment it
    finishes."""
    checkpoint = Path(checkpoint)
    cells = load_checkpoint(checkpoint)
    rows = [row] if row else decks
    todo = [(a, b) for a in rows for b in decks if a != b and (a, b) not in cells]
    print(f"resumable matrix: {len(cells)} cell(s) checkpointed, {len(todo)} to run",
          file=sys.stderr)
    fn = partial(_run_pair, n_games=n_games, run_matchup_fn=run_matchup_fn)
    if workers <= 1:
        results = (fn(p) for p in todo)
        for d1, d2, cell in results:
            _append(checkpoint, d1, d2, cell)
            cells[(d1, d2)] = cell
    else:
        with Pool(workers) as pool:
            for k, (d1, d2, cell) in enumerate(pool.imap_unordered(fn, todo), 1):
                _append(checkpoint, d1, d2, cell)
                cells[(d1, d2)] = cell
                print(f"  [{len(cells)}] {d1} vs {d2}: "
                      f"{cell.wr:.0f}%", file=sys.stderr, flush=True)
    return cells


def run(decks: List[str], *, n_games: int, workers: int, checkpoint: Path,
        results_path: Path, run_matchup_fn: Optional[Callable] = None,
        merge: bool = True, rules_audit: bool = False) -> Optional[Path]:
    import run_meta
    cells = run_cells(decks, n_games=n_games, workers=workers,
                      checkpoint=checkpoint, run_matchup_fn=run_matchup_fn)
    expected = len(decks) * (len(decks) - 1)
    if len(cells) < expected:
        print(f"resumable matrix: {len(cells)}/{expected} cells; results not written",
              file=sys.stderr)
        return None
    result = run_meta._assemble_parallel_matrix(decks, cells, n_games=n_games, bo1=False)
    run_meta.save_results(result, str(results_path))
    if rules_audit:
        run_meta._AUDIT_SINK.clear()
        for cell in cells.values():
            run_meta._sink_audit(list(cell.audit))
        run_meta._write_audit_report(str(Path(results_path).parent / "audits"))
    if merge:
        import build_dashboard
        build_dashboard.merge(results_path=str(Path(results_path).name),
                              root=Path(results_path).parent)
    return Path(results_path)


def neutralise_game_deadline() -> None:
    """Raise GAME_TIMEOUT_SECONDS to the anchor test's constant before any
    worker forks: the deadline abandons a game on a slow or loaded machine,
    which makes a recorded outcome depend on machine speed as well as on the
    seed. Imported, not duplicated, so the tools cannot diverge (the same
    constant tools/refresh_wr_baseline.py and tools/seeded_game_digest.py use)."""
    import ai.constants as _ai_constants
    from tests.test_wr_baseline_anchor import _ANCHOR_TIMEOUT_SECONDS
    _ai_constants.GAME_TIMEOUT_SECONDS = _ANCHOR_TIMEOUT_SECONDS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", "--games", type=int, default=60)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--rules-audit", action="store_true")
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--row", help="run only this deck's ordered pairs (one shard); "
                                  "never writes results")
    ap.add_argument("--seed-checkpoint", nargs="*", default=[],
                    help="checkpoints whose cells are merged in before running")
    ap.add_argument("--neutralise-deadline", action="store_true",
                    help="raise the per-game deadline so every outcome depends "
                         "on the seed alone, not on machine speed")
    a = ap.parse_args(argv)
    if a.rules_audit:
        os.environ["MTG_RULES_AUDIT"] = "1"
    if a.neutralise_deadline:
        neutralise_game_deadline()
    from decks.modern_meta import get_all_deck_names
    decks = get_all_deck_names()
    ck = Path(a.checkpoint)
    seeds = [Path(p) for p in a.seed_checkpoint if Path(p).exists()]
    if seeds:
        merge_checkpoints(([ck] if ck.exists() else []) + seeds, ck)
    if a.row:
        cells = run_cells(decks, n_games=a.games, workers=a.workers, checkpoint=ck,
                          row=a.row)
        return 0 if len([p for p in cells if p[0] == a.row]) == len(decks) - 1 else 1
    out = run(decks, n_games=a.games, workers=a.workers,
              checkpoint=ck, results_path=ROOT / "metagame_results.json",
              merge=not a.no_merge, rules_audit=a.rules_audit)
    return 0 if out else 1


if __name__ == "__main__":
    raise SystemExit(main())
