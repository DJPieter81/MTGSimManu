"""Failing-test-first: --probe must reuse the matrix seed geometry.

Structural finding #5 (docs/proposals/2026-07-09_structural_findings.md):
`--matrix`, `--field`, and `--matchup` use different seed grids, so the
same build reads different WRs on identical code depending on which
command measured it.  `run_meta.run_probe` exists to re-run a single
matrix cell (or a full matrix row) on the EXACT per-pair seed sequence
the matrix path uses, making probe numbers directly comparable to
matrix cells by construction.

The rule these tests name: the probe path and the matrix path must
consume the identical seed sequence — same origin, same step, same
count — sourced from one shared function, not two copies of the
arithmetic.

No sims: `_run_pair` is stubbed to a seed recorder, so these tests are
pure unit tests over the seed plumbing.
"""
from __future__ import annotations

import pytest

import run_meta


class _FakeResult:
    def __init__(self, winner_deck):
        self.winner_deck = winner_deck


def _seed_recorder(record):
    """Stub for run_meta._run_pair that records the seed per pair."""
    def _stub(runner, d1, d2, seed, bo1=False, verbose=False):
        record.setdefault((d1, d2), []).append(seed)
        return _FakeResult(d1)
    return _stub


@pytest.fixture()
def two_decks(monkeypatch):
    names = run_meta.get_all_deck_names()[:2]
    monkeypatch.setattr(run_meta, "get_all_deck_names", lambda: names)
    monkeypatch.setattr(run_meta, "_get_runner", lambda: object())
    monkeypatch.setattr(run_meta, "_worker_runner", None)
    return names


def test_seed_grid_is_the_single_source_of_seed_arithmetic():
    """One shared function defines the per-pair seed sequence."""
    grid = run_meta.seed_grid(4, run_meta.MATRIX_SEED_START)
    assert grid == [40000, 40500, 41000, 41500]
    assert run_meta.seed_grid(0, run_meta.MATRIX_SEED_START) == []


def test_probe_seeds_equal_matrix_seeds_for_same_pair(monkeypatch, two_decks):
    """probe(A, B) must consume the identical seed sequence the serial
    matrix path consumes for cell (A, B)."""
    names = two_decks
    n = 4

    matrix_record = {}
    monkeypatch.setattr(run_meta, "_run_pair", _seed_recorder(matrix_record))
    run_meta.run_meta_matrix(n_games=n, parallel=False)

    probe_record = {}
    monkeypatch.setattr(run_meta, "_run_pair", _seed_recorder(probe_record))
    run_meta.run_probe(names[0], names[1], n_games=n, parallel=False)

    pair = (names[0], names[1])
    assert probe_record[pair] == matrix_record[pair]
    assert probe_record[pair] == run_meta.seed_grid(
        n, run_meta.MATRIX_SEED_START)


def test_worker_matchup_uses_shared_seed_grid(monkeypatch):
    """The parallel matrix worker consumes seed_grid(), not its own
    copy of the start+step arithmetic."""
    record = {}
    monkeypatch.setattr(run_meta, "_worker_runner", object())
    monkeypatch.setattr(run_meta, "_run_pair", _seed_recorder(record))
    run_meta._worker_matchup(("A", "B", 3, run_meta.MATRIX_SEED_START, False))
    assert record[("A", "B")] == run_meta.seed_grid(
        3, run_meta.MATRIX_SEED_START)


def test_probe_without_opponent_probes_all_opponents_on_matrix_grid(
        monkeypatch, two_decks):
    """DECK alone = matrix row: every opponent, matrix-seeded."""
    names = two_decks
    record = {}
    monkeypatch.setattr(run_meta, "_run_pair", _seed_recorder(record))
    result = run_meta.run_probe(names[0], n_games=2, parallel=False)

    opponents = [n for n in names if n != names[0]]
    assert set(result["matchups"]) == set(opponents)
    for opp in opponents:
        assert record[(names[0], opp)] == run_meta.seed_grid(
            2, run_meta.MATRIX_SEED_START)


def test_probe_result_declares_its_seed_geometry(monkeypatch, two_decks):
    """Probe results self-describe their grid so downstream consumers
    (save_results stamps, trend reports) never guess the geometry."""
    names = two_decks
    monkeypatch.setattr(run_meta, "_run_pair", _seed_recorder({}))
    result = run_meta.run_probe(names[0], names[1], n_games=3,
                                parallel=False)
    geo = result["seed_geometry"]
    assert geo["seed_start"] == run_meta.MATRIX_SEED_START
    assert geo["step"] == run_meta.SEED_STEP
    assert geo["n_games"] == 3
