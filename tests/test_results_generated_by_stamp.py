"""Failing-test-first: saved results must stamp their generating command.

Structural finding #5 (docs/proposals/2026-07-09_structural_findings.md):
"numbers always stamped with their generating command" — every
before/after comparison this wave required re-baselining per command
because a saved WR number carried no record of which command (and
which seed grid) produced it.

The rule these tests name: every results JSON written by
run_meta.save_results carries a ``generated_by`` object with the
generating ``command``, the ``seed_geometry`` of the run (when the
producing function declares it), and a ``timestamp``.

No sims: results dicts are synthetic; only the writer is exercised.
"""
from __future__ import annotations

import json

import run_meta


def _save(tmp_path, result):
    path = tmp_path / "results.json"
    run_meta.save_results(result, path=str(path))
    with open(path) as f:
        return json.load(f)


def test_saved_results_carry_generated_by_stamp(tmp_path):
    data = _save(tmp_path, {
        "deck1": "A", "deck2": "B", "pct1": 55, "pct2": 45, "games": 20,
    })
    gb = data["generated_by"]
    assert set(gb) >= {"command", "seed_geometry", "timestamp"}
    assert isinstance(gb["command"], str) and gb["command"]
    assert gb["timestamp"] == data["timestamp"]


def test_stamp_propagates_declared_seed_geometry(tmp_path):
    geo = {"grid": "matrix", "seed_start": run_meta.MATRIX_SEED_START,
           "step": run_meta.SEED_STEP, "n_games": 3}
    data = _save(tmp_path, {
        "deck": "A", "matchups": {"B": 60}, "average": 60.0,
        "seed_geometry": geo,
    })
    assert data["generated_by"]["seed_geometry"] == geo


def test_stamp_names_unknown_geometry_instead_of_guessing(tmp_path):
    """A result whose producer declared no grid must not be stamped
    with an invented one — unknown is stamped as None."""
    data = _save(tmp_path, {"deck": "A", "matchups": {"B": 60},
                            "average": 60.0})
    assert data["generated_by"]["seed_geometry"] is None


def test_producing_functions_declare_seed_geometry(monkeypatch):
    """The writers' inputs — matrix/matchup/field/probe results —
    self-describe their grid so the stamp is truthful end to end."""
    names = run_meta.get_all_deck_names()[:2]
    monkeypatch.setattr(run_meta, "get_all_deck_names", lambda: names)
    monkeypatch.setattr(run_meta, "_get_runner", lambda: object())
    monkeypatch.setattr(run_meta, "_worker_runner", None)

    class _Fake:
        def __init__(self, w):
            self.winner_deck = w
            self.turns = 5
            self.games = []
            self.match_score = (2, 0)

    monkeypatch.setattr(
        run_meta, "_run_pair",
        lambda runner, d1, d2, seed, bo1=False, verbose=False: _Fake(d1))

    matrix = run_meta.run_meta_matrix(n_games=2, parallel=False)
    assert matrix["seed_geometry"]["seed_start"] == run_meta.MATRIX_SEED_START

    matchup = run_meta.run_matchup(names[0], names[1], n_games=2)
    assert (matchup["seed_geometry"]["seed_start"]
            == run_meta.MATCHUP_SEED_START)

    field = run_meta.run_field(names[0], n_games=2, parallel=False)
    assert field["seed_geometry"]["seed_start"] == run_meta.MATCHUP_SEED_START

    probe = run_meta.run_probe(names[0], names[1], n_games=2, parallel=False)
    assert probe["seed_geometry"]["seed_start"] == run_meta.MATRIX_SEED_START

    for result in (matrix, matchup, field, probe):
        geo = result["seed_geometry"]
        assert geo["step"] == run_meta.SEED_STEP
        assert geo["n_games"] == 2
