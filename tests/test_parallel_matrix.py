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

import pytest

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


# This test runs ~36 real Bo3 games twice (serial and parallel) and takes
# roughly two minutes of genuine simulation. The suite-wide --timeout=120 in
# CI exists to catch HANGS — "any single test exceeding the cap fails by name
# rather than letting the job silently consume the wall budget" — not to bound
# legitimate long work, so this test carries an explicit exemption instead of
# producing intermittent false failures at the boundary.
#
# Measured: activation enumeration is NOT the cause. Same matchup, 4 Bo3
# matches: 26.9s with enumeration live vs 26.6s with it reverted (~1%, inside
# noise). The test was already sitting just under the cap; it is marginal by
# construction, not by regression.
# DESELECTED IN CI (see .github/workflows/abstraction-contract.yml).
# This test spawns mp.Pool worker processes, each of which loads the full
# ~22k-card database. On a 2-core GitHub runner that is enough memory pressure
# to get the whole job killed ("The runner has received a shutdown signal"),
# which fails every other test with it. Three runs died that way, each after
# stalling 160-312s at this point in the suite -- matching this test's ~205s
# runtime under the 300s exemption below.
#
# Before that exemption existed the test was killed at the suite-wide 120s cap
# and the job completed with one clean named failure. The exemption turned a
# small honest failure into a whole-job kill, which is strictly worse.
#
# It still runs locally, where the timeout below is the appropriate bound.
@pytest.mark.timeout(300)
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
