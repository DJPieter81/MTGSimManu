"""Calibration check — pairwise matrix vs ground-truth bands.

Synthetic-matrix unit tests + a repo-state smoke (report-only mode
must run clean against the committed matrix regardless of how many
pairs are out of band — the misses are the work queue, not a build
failure).
"""
from __future__ import annotations

from tools.check_calibration import check, load_matrix, pair_win_pct


def _matrix():
    return {
        "decks": ["A", "B", "C"],
        "matches_per_pair": 20,
        # wins[i][j] = wins of deck i vs deck j
        "wins": [[0, 10, 18],
                 [10, 0, 4],
                 [2, 16, 0]],
    }


def _bands(pairs):
    return {"pairs": pairs}


def test_pair_win_pct_formula():
    d = _matrix()
    assert pair_win_pct(d, "A", "B") == 50
    assert pair_win_pct(d, "A", "C") == 90
    assert pair_win_pct(d, "C", "A") == 10


def test_pair_absent_returns_none():
    assert pair_win_pct(_matrix(), "A", "Nope") is None


def test_check_partitions_in_out_absent():
    bands = _bands([
        {"deck1": "A", "deck2": "B", "lo": 40, "hi": 60},   # 50 → in
        {"deck1": "A", "deck2": "C", "lo": 40, "hi": 60},   # 90 → out
        {"deck1": "A", "deck2": "Z", "lo": 40, "hi": 60},   # absent
    ])
    in_band, misses, absent = check(_matrix(), bands)
    assert [r["deck2"] for r in in_band] == ["B"]
    assert [r["deck2"] for r in misses] == ["C"]
    assert misses[0]["pct"] == 90
    assert [r["deck2"] for r in absent] == ["Z"]


def test_band_edges_are_inclusive():
    bands = _bands([
        {"deck1": "A", "deck2": "B", "lo": 50, "hi": 50},
    ])
    in_band, misses, _ = check(_matrix(), bands)
    assert in_band and not misses


def test_repo_matrix_loads_and_checks():
    """Repo smoke: the committed JSX parses and every configured pair
    resolves to in-band, out-of-band, or absent — no exceptions."""
    import json
    from pathlib import Path
    d = load_matrix()
    bands = json.loads(
        (Path(__file__).resolve().parent.parent / "tools" /
         "calibration_matchups.json").read_text())
    in_band, misses, absent = check(d, bands)
    assert len(in_band) + len(misses) + len(absent) == len(bands["pairs"])
