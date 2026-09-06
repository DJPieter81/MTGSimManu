"""A game nobody won is credited to nobody — and an aborted game is not a
game result at all.

Every aggregator in `run_meta` used to tally `wins[r.winner_deck] += 1` and
report `pct = wins[d1] / n`, while the matrix wrote the reverse cell as
`100 - pct`. Two consequences:

  * A drawn game (CR 104.4 turn cap, or simultaneous loss) lowered the
    first-named deck's cell and RAISED the second-named deck's — every draw
    was a free win for whichever deck sorted later. Control mirrors reach the
    turn cap in 2 of 3 / 3 of 3 games, so this was not a corner case.
  * A game cut off by the safety budget (`win_condition == "aborted"`) was
    indistinguishable from a draw and got the same treatment, so a loaded
    box silently rewrote win rates instead of reporting that the run was not
    calibration-grade.

Rules these tests name: the reverse cell is the opponent's own wins; draws
and aborts are counted in every aggregate and stamped into the saved
results; a results file with aborts is announced as not calibration-grade.

No sims: `_run_pair` is stubbed. Deck names are labels only.
"""
from __future__ import annotations

import json

import pytest

import run_meta


class _Game:
    """Shape of a Bo1 GameResult as the aggregators read it."""
    def __init__(self, winner_deck, win_condition):
        self.winner_deck = winner_deck
        self.win_condition = win_condition
        self.turns = 7
        self.winner = None if winner_deck == "draw" else 0


def _ten_games(d1, d2):
    """4 d1 wins, 3 d2 wins, 2 rules draws, 1 abort — in that order."""
    return ([_Game(d1, "damage")] * 4 + [_Game(d2, "damage")] * 3
            + [_Game("draw", "timeout"), _Game("draw", "draw")]
            + [_Game("draw", "aborted")])


def _stub_pair(script):
    it = iter(script)

    def _stub(runner, d1, d2, seed, bo1=False, verbose=False):
        return next(it)
    return _stub


@pytest.fixture()
def two_decks(monkeypatch):
    names = run_meta.get_all_deck_names()[:2]
    monkeypatch.setattr(run_meta, "get_all_deck_names", lambda: names)
    monkeypatch.setattr(run_meta, "_get_runner", lambda: object())
    monkeypatch.setattr(run_meta, "_worker_runner", object())
    return names


def test_reverse_matrix_cell_is_the_opponents_wins_not_the_complement(
        monkeypatch, two_decks):
    a, b = two_decks
    monkeypatch.setattr(run_meta, "_run_pair", _stub_pair(_ten_games(a, b)))
    w = run_meta._worker_matchup((a, b, 10, run_meta.MATRIX_SEED_START, True))

    assert (w.d1, w.d2) == (a, b)
    assert w.pct == 40
    assert w.pct_reverse == 30, "the reverse cell is B's wins, not 100 - 40"
    assert w.draws == 2
    assert w.aborted == 1
    assert w.errors == []
    # Positional contract kept for older unpackers: errors stays at index 3.
    assert w[3] == w.errors

    monkeypatch.setattr(run_meta, "_run_pair", _stub_pair(_ten_games(a, b)))
    m = run_meta.run_meta_matrix(n_games=10, parallel=False)
    assert m["matrix"][(a, b)] == 40
    assert m["matrix"][(b, a)] == 30
    assert m["draws"] == 2
    assert m["aborted"] == 1
    assert m["symmetry_issues"] == [], (
        "40 + 30 + 20 (draws) + 10 (aborted) = 100 is symmetric")


def test_aborted_games_surface_in_every_aggregate_and_the_saved_results(
        monkeypatch, two_decks, tmp_path, capsys):
    a, b = two_decks

    monkeypatch.setattr(run_meta, "_run_pair", _stub_pair(_ten_games(a, b)))
    mu = run_meta.run_matchup(a, b, n_games=10, bo3=False)
    assert (mu["pct1"], mu["pct2"]) == (40, 30)
    assert mu["draws"] == 2 and mu["aborted"] == 1

    monkeypatch.setattr(run_meta, "_run_pair", _stub_pair(_ten_games(a, b)))
    fld = run_meta.run_field(a, n_games=10, parallel=False, bo3=False)
    assert fld["matchups"][b] == 40
    assert fld["draws"] == 2 and fld["aborted"] == 1

    monkeypatch.setattr(run_meta, "_run_pair", _stub_pair(_ten_games(a, b)))
    m = run_meta.run_meta_matrix(n_games=10, parallel=False)
    out = tmp_path / "results.json"
    run_meta.save_results(m, path=str(out))
    saved = json.loads(out.read_text())
    assert saved["aborted"] == 1 and saved["draws"] == 2
    assert saved["cell_draws"][f"{a}|{b}"] == 2

    captured = capsys.readouterr()
    assert "not calibration-grade" in (captured.err + captured.out).lower(), (
        "an aborted game must be announced loudly at the end of the run")


def test_check_calibration_announces_a_run_with_aborts(tmp_path, capsys):
    from tools import check_calibration as cc

    base = json.loads(open(run_meta.RESULTS_FILE).read())
    base["aborted"] = 3
    p = tmp_path / "r.json"
    p.write_text(json.dumps(base))
    cc.run_check(str(p))
    text = capsys.readouterr().out
    assert "NOT CALIBRATION-GRADE (aborted=3)" in text

    base["aborted"] = 0
    p.write_text(json.dumps(base))
    cc.run_check(str(p))
    assert "NOT CALIBRATION-GRADE" not in capsys.readouterr().out
