"""Failing-test-first: calibration trend report between two snapshots.

Structural finding #6 (docs/proposals/2026-07-09_structural_findings.md):
the headline in/out count has been flat for three waves while band
composition visibly improved, and below-band decks donate inflated
rows to every deck above them.  The cure is trend reporting: per-deck
field-WR deltas between two saved results files, band-composition
transitions (which anchors moved IN/OUT, not just how many are OUT),
and a field-average view that excludes out-of-band opponents so a
deck's number is not propped up by farming broken rows.

Pure reporting over two synthetic results dicts — no sims, no writes,
and band VALUES are supplied by the test, never read from
tools/calibration_bands.json.
"""
from __future__ import annotations

import json

from tools.check_calibration import compute_trend, main


def _bands(matchups=None, fields=None):
    return {"schema": "1.0",
            "matchup_bands": matchups or [],
            "field_bands": fields or []}


def _results(matrix, names):
    return {"type": "matrix", "matrix": matrix, "names": names}


NAMES = ["A", "B", "C"]
PREV = _results({"A|B": 90, "A|C": 50, "B|C": 50}, NAMES)
CURR = _results({"A|B": 70, "A|C": 60, "B|C": 50}, NAMES)
BANDS = _bands(
    matchups=[{"deck_a": "A", "deck_b": "B", "expected_wr_a": [65, 75],
               "provenance": "test prior"}],
    fields=[{"deck": "B", "expected_wr": [45, 55],
             "provenance": "test prior"}],
)


def _delta_by_deck(trend):
    return {r["deck"]: r for r in trend["deck_deltas"]}


def test_per_deck_field_wr_deltas_between_snapshots():
    trend = compute_trend(PREV, CURR, BANDS)
    rows = _delta_by_deck(trend)
    # prev: A=(90+50)/2=70, B=(10+50)/2=30, C=(50+50)/2=50
    # curr: A=(70+60)/2=65, B=(30+50)/2=40, C=(50+40)/2=45
    assert rows["A"]["prev_wr"] == 70 and rows["A"]["curr_wr"] == 65
    assert rows["A"]["delta"] == -5
    assert rows["B"]["delta"] == 10
    assert rows["C"]["delta"] == -5


def test_band_composition_transitions_are_reported_per_anchor():
    trend = compute_trend(PREV, CURR, BANDS)
    # A vs B: prev 90 OUT(above) -> curr 70 IN. B field: OUT both.
    assert trend["transitions"] == [
        {"kind": "MATCHUP", "anchor": "A vs B", "prev": "OUT", "curr": "IN"},
    ]
    assert trend["composition"]["prev"] == {"IN": 0, "OUT": 2, "SKIP": 0}
    assert trend["composition"]["curr"] == {"IN": 1, "OUT": 1, "SKIP": 0}


def test_exclude_out_of_band_opponents_removes_broken_rows_from_averages():
    trend = compute_trend(PREV, CURR, BANDS,
                          exclude_out_of_band_opponents=True)
    # B is out of its field band in both snapshots -> excluded as an
    # opponent from everyone's field average (same pool both sides,
    # so deltas stay apples-to-apples).
    assert trend["excluded_opponents"] == ["B"]
    rows = _delta_by_deck(trend)
    # A vs {C} only: prev 50 -> curr 60
    assert rows["A"]["prev_wr"] == 50 and rows["A"]["curr_wr"] == 60
    assert rows["A"]["delta"] == 10
    # Excluded decks still get their own trend row — exclusion drops
    # them as OPPONENTS, not as rows. B vs {A, C}: (10+50)/2 -> (30+50)/2.
    assert rows["B"]["prev_wr"] == 30 and rows["B"]["curr_wr"] == 40


def test_trend_only_compares_decks_present_in_both_snapshots():
    prev = _results({"A|B": 60, "A|D": 40, "B|D": 50}, ["A", "B", "D"])
    trend = compute_trend(prev, CURR, _bands())
    rows = _delta_by_deck(trend)
    assert set(rows) == {"A", "B"}
    assert trend["decks_only_in_prev"] == ["D"]
    assert trend["decks_only_in_curr"] == ["C"]
    # A's field average is taken over the SHARED pool ({B}) on both
    # sides so the delta is not an artifact of pool churn.
    assert rows["A"]["prev_wr"] == 60 and rows["A"]["curr_wr"] == 70


def test_cli_trend_mode_prints_report_and_exits_zero(tmp_path, capsys):
    prev_p = tmp_path / "prev.json"
    curr_p = tmp_path / "curr.json"
    bands_p = tmp_path / "bands.json"
    prev_p.write_text(json.dumps(PREV))
    curr_p.write_text(json.dumps(CURR))
    bands_p.write_text(json.dumps(BANDS))

    rc = main(["--trend", str(prev_p), str(curr_p), "--bands", str(bands_p)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "A vs B" in out and "OUT -> IN" in out

    rc = main(["--trend", str(prev_p), str(curr_p), "--bands", str(bands_p),
               "--exclude-out-of-band-opponents"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "B" in out


def test_cli_trend_missing_file_is_a_usage_error(tmp_path):
    curr_p = tmp_path / "curr.json"
    curr_p.write_text(json.dumps(CURR))
    rc = main(["--trend", str(tmp_path / "nope.json"), str(curr_p)])
    assert rc == 2
    rc = main(["--trend", str(curr_p)])  # missing CURR argument
    assert rc == 2
