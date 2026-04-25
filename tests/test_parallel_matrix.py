"""Phase 2.5 — parallel matrix runner (Option C).

`tools/parallel_matrix.run_matrix_parallel` is a multiprocessing
wrapper around `run_meta.run_matchup`. Tests assert the dispatch
contract:

  1. Parallel and serial both return the full off-diagonal pair set
     with valid integer-percent WRs (no missing pairs, no NaN, no
     out-of-range values).
  2. `workers=1` cleanly disables the Pool (no subprocess overhead)
     and produces the same shape.
  3. Structural pair enumeration is correct (N*(N-1) entries, no
     deck-vs-self, empty/single-deck inputs short-circuit).

Why we don't assert exact value equality across runs:
`engine.GameRunner.__init__` defaults `self.rng = random.Random()`
— a *fresh, system-entropy-seeded* RNG per runner. Each
`run_matchup` call builds a fresh runner, so the same matchup
produces different WRs across calls *even within the same
process*. This is pre-existing engine behaviour, independent of
Phase 2.5 (verify with two back-to-back calls to
`run_meta.run_matchup` from the REPL).

A future hardening pass could thread an explicit seed into
`GameRunner` for reproducibility; until then, the matrix runner
guarantees the dispatch contract but inherits the engine's
non-determinism.
"""
from __future__ import annotations

from tools.parallel_matrix import run_matrix_parallel


# Three small competitive decks. Use real deck names so the workers
# can resolve them via the standard `run_matchup` path.
SMALL_DECKS = ['Boros Energy', 'Domain Zoo', 'Affinity']
N_GAMES = 2  # 2 Bo3 matches per pair = 6 pairs * ~6 games = ~36 games


def _expected_pairs(decks):
    return {(d1, d2) for d1 in decks for d2 in decks if d1 != d2}


def _assert_valid_matrix(matrix, decks):
    """Shape + value contract: every off-diagonal pair present,
    every WR an integer percent in [0, 100]."""
    assert set(matrix.keys()) == _expected_pairs(decks), (
        f"Missing pairs: {_expected_pairs(decks) - set(matrix.keys())}; "
        f"extra: {set(matrix.keys()) - _expected_pairs(decks)}"
    )
    for pair, wr in matrix.items():
        assert isinstance(wr, (int, float)), f"{pair}: wr={wr!r} not numeric"
        assert 0 <= wr <= 100, f"{pair}: wr={wr} outside [0, 100]"


def test_parallel_matches_serial_small_N():
    """Parallel and serial dispatchers both honour the contract:
    same set of pairs, all WRs are valid percents.

    See module docstring for why we don't assert exact value
    equality across the in-process / subprocess boundary.
    """
    serial = run_matrix_parallel(SMALL_DECKS, n_games=N_GAMES, workers=1)
    parallel = run_matrix_parallel(SMALL_DECKS, n_games=N_GAMES, workers=2)

    _assert_valid_matrix(serial, SMALL_DECKS)
    _assert_valid_matrix(parallel, SMALL_DECKS)

    # Pair sets must agree exactly — any missing pair would be a
    # dispatch bug (lost work).
    assert set(serial.keys()) == set(parallel.keys())


def test_no_workers_falls_back_to_serial():
    """`workers=1` must produce a valid matrix without spinning up
    a multiprocessing Pool — that is the contract of the no-pool
    path. Value equality is *not* asserted: the engine's RNG is
    re-seeded from system entropy on each `GameRunner.__init__`
    (see module docstring), so the same matchup produces different
    WRs across calls. We assert structural validity only.
    """
    matrix = run_matrix_parallel(SMALL_DECKS, n_games=N_GAMES, workers=1)
    _assert_valid_matrix(matrix, SMALL_DECKS)


def test_pair_enumeration_excludes_diagonal():
    """The matrix returned must have exactly N*(N-1) entries — every
    off-diagonal ordered pair is present, mirror is independent, no
    deck-vs-self entry. This is a structural test that doesn't run
    any games (empty / single-deck input shortcuts the dispatch).
    """
    # Sanity: empty decks yields empty matrix without spinning workers.
    assert run_matrix_parallel([], n_games=1, workers=1) == {}
    # Single deck has no off-diagonal pairs.
    assert run_matrix_parallel(['Boros Energy'], n_games=1, workers=1) == {}
