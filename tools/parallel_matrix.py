"""Parallel N=50 matrix runner.

Cuts matrix from ~30 min serial to ~10 min on 4 cores by running
each matchup pair in a worker process.

Constraint: CardDatabase reloads per worker (~400MB each). Verify
memory budget before bumping `workers` past 4.
"""
from __future__ import annotations
from functools import partial
from multiprocessing import Pool
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple


class PairCell(NamedTuple):
    """One ordered pair's tally as `run_meta.run_matchup` reports it:
    d1's match win percent, d2's own win percent (NOT the complement —
    draws and aborts credit nobody), and the games credited to nobody."""
    wr: float
    wr_reverse: float
    draws: int
    aborted: int


def _run_pair(args: Tuple[str, str], n_games: int,
              run_matchup_fn: Optional[Callable]) -> Tuple[str, str, PairCell]:
    """Worker: run one Bo3 matchup, return the pair's `PairCell`."""
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
        cell = PairCell(result.get('pct1', 0.0), result.get('pct2', 0.0),
                        int(result.get('draws', 0) or 0),
                        int(result.get('aborted', 0) or 0))
    else:
        cell = PairCell(float(result), 0.0, 0, 0)
    return d1, d2, cell


def run_matrix_parallel_cells(decks: List[str], n_games: int = 50,
                              workers: int = 4,
                              run_matchup_fn: Optional[Callable] = None,
                              ) -> Dict[Tuple[str, str], PairCell]:
    """Run the full N×N matrix in parallel. Returns {(d1, d2): PairCell}.

    Each off-diagonal pair (d1 != d2) is dispatched to a worker, so both
    orderings of a pair are independent runs. The cell carries d1's win
    percent, d2's win percent and the draw/abort counts, so the caller
    can stamp every game credited to nobody into the saved results (a
    run with aborts is not calibration-grade and must say so).

    Workers > 1 use multiprocessing; workers == 1 runs serially in
    the calling process (useful for tests and debugging).
    `run_matchup_fn` substitutes the matchup runner (tests; must be
    picklable when workers > 1).
    """
    pairs = [(d1, d2) for d1 in decks for d2 in decks if d1 != d2]
    fn = partial(_run_pair, n_games=n_games, run_matchup_fn=run_matchup_fn)
    if workers <= 1:
        results = [fn(p) for p in pairs]
    else:
        with Pool(workers) as pool:
            results = pool.map(fn, pairs)
    return {(d1, d2): cell for d1, d2, cell in results}


def run_matrix_parallel(decks: List[str], n_games: int = 50,
                        workers: int = 4) -> Dict[Tuple[str, str], float]:
    """Run full N×N matrix in parallel. Returns {(d1, d2): wr} dict.

    The returned WR is d1's match win percent (0..100), matching
    `run_meta.run_matchup`'s `pct1` field. Thin view over
    `run_matrix_parallel_cells`, which also carries draws and aborts.
    """
    cells = run_matrix_parallel_cells(decks, n_games=n_games, workers=workers)
    return {pair: cell.wr for pair, cell in cells.items()}
