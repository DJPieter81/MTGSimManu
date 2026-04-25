"""Parallel N=50 matrix runner.

Cuts matrix from ~30 min serial to ~10 min on 4 cores by running
each matchup pair in a worker process.

Constraint: CardDatabase reloads per worker (~400MB each). Verify
memory budget before bumping `workers` past 4.
"""
from __future__ import annotations
from functools import partial
from multiprocessing import Pool
from typing import Callable, Dict, List, Optional, Tuple


def _run_pair(args: Tuple[str, str], n_games: int,
              run_matchup_fn: Optional[Callable]) -> Tuple[str, str, float]:
    """Worker: run one Bo3 matchup, return WR for d1 (0..100 percent)."""
    d1, d2 = args
    # Import inside worker for clean process boundary (each worker
    # loads its own CardDatabase via run_matchup -> _get_runner).
    if run_matchup_fn is None:
        from run_meta import run_matchup as _rm
    else:
        _rm = run_matchup_fn
    result = _rm(d1, d2, n_games=n_games)
    # run_matchup returns a dict; pct1 is d1's win percent (0..100).
    if isinstance(result, dict):
        wr = result.get('pct1', 0.0)
    else:
        wr = float(result)
    return d1, d2, wr


def run_matrix_parallel(decks: List[str], n_games: int = 50,
                        workers: int = 4) -> Dict[Tuple[str, str], float]:
    """Run full N×N matrix in parallel. Returns {(d1, d2): wr} dict.

    Each off-diagonal pair (d1 != d2) is dispatched to a worker. The
    returned WR is d1's match win percent (0..100), matching
    `run_meta.run_matchup`'s `pct1` field.

    Workers > 1 use multiprocessing; workers == 1 runs serially in
    the calling process (useful for tests and debugging).
    """
    pairs = [(d1, d2) for d1 in decks for d2 in decks if d1 != d2]
    fn = partial(_run_pair, n_games=n_games, run_matchup_fn=None)
    if workers <= 1:
        results = [fn(p) for p in pairs]
    else:
        with Pool(workers) as pool:
            results = pool.map(fn, pairs)
    return {(d1, d2): wr for d1, d2, wr in results}
