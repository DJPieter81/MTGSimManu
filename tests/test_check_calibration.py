"""Calibration-check rules — small synthetic-results unit tests.

Mirrors tests/test_replay_lint.py granularity for the sibling tool
(tools/check_calibration.py, institutionalization item 1 from the
calibration-probe diagnostics).  Each behaviour gets a minimal
synthetic results dict; the smoke test runs against the committed
metagame_results.json without asserting in/out counts — the committed
matrix predates the fix wave and IS out of band on known pairs, which
is the tool working as intended.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.check_calibration import (
    DEFAULT_BANDS_PATH,
    check_results,
    load_bands,
    main,
    matchup_wr,
)

_ROOT = Path(__file__).resolve().parent.parent
_RESULTS = _ROOT / "metagame_results.json"


def _bands(matchups=None, fields=None, default=None):
    b = {"schema": "1.0",
         "matchup_bands": matchups or [],
         "field_bands": fields or []}
    if default is not None:
        b["default_field_band"] = default
    return b


def _mband(a, b, lo, hi, prov="test prior"):
    return {"deck_a": a, "deck_b": b, "expected_wr_a": [lo, hi],
            "provenance": prov}


def _results(matrix, names=None):
    return {"type": "matrix", "matrix": matrix,
            "names": names or sorted({d for k in matrix
                                      for d in k.split("|")})}


def _by(findings, kind):
    return [f for f in findings if f.kind == kind]


# ─── in band / out of band, with direction ──────────────────────────

def test_in_band_pair_reported_in():
    res = _results({"A|B": 50, "B|A": 50})
    f = _by(check_results(res, _bands([_mband("A", "B", 45, 55)])),
            "MATCHUP")
    assert [x.status for x in f] == ["IN"]
    assert f[0].sim_wr == 50.0


def test_out_of_band_below_reports_direction():
    res = _results({"A|B": 10, "B|A": 90})
    f = _by(check_results(res, _bands([_mband("A", "B", 45, 55)])),
            "MATCHUP")
    assert [x.status for x in f] == ["OUT"]
    assert f[0].direction == "below"
    assert f[0].sim_wr == 10.0


def test_out_of_band_above_reports_direction():
    res = _results({"A|B": 90, "B|A": 10})
    f = _by(check_results(res, _bands([_mband("A", "B", 45, 55)])),
            "MATCHUP")
    assert [x.status for x in f] == ["OUT"]
    assert f[0].direction == "above"


def test_band_edges_are_inclusive():
    res = _results({"A|B": 45, "B|A": 55})
    f = _by(check_results(res, _bands([_mband("A", "B", 45, 55)])),
            "MATCHUP")
    assert [x.status for x in f] == ["IN"]


# ─── orientation: band for deck_a vs (a,b)/(b,a) matrix keys ─────────
# The classic bug: a band stored for deck_a silently read against the
# (b,a) cell.  An asymmetric matchup makes any flip visible.

def test_orientation_direct_key_only():
    assert matchup_wr({"A|B": 80}, "A", "B") == 80.0


def test_orientation_reverse_key_only_complements():
    # Only the (b,a) cell exists: deck_a's WR must be the complement.
    assert matchup_wr({"B|A": 80}, "A", "B") == 20.0


def test_orientation_band_checked_against_deck_a_not_deck_b():
    # A beats B 80-20.  Band says A should be 15-25 → sim is OUT above.
    # A flipped implementation would read 20 and report IN.
    res = _results({"A|B": 80, "B|A": 20})
    f = _by(check_results(res, _bands([_mband("A", "B", 15, 25)])),
            "MATCHUP")
    assert [x.status for x in f] == ["OUT"]
    assert f[0].direction == "above"
    assert f[0].sim_wr == 80.0
    # Same band expressed from B's side is IN — both orderings agree.
    f2 = _by(check_results(res, _bands([_mband("B", "A", 15, 25)])),
             "MATCHUP")
    assert [x.status for x in f2] == ["IN"]
    assert f2[0].sim_wr == 20.0


def test_orientation_both_keys_averaged_on_asymmetry():
    # Symmetry-violating save: 70 vs (100-50)=50 → deck_a WR 60.
    res = _results({"A|B": 70, "B|A": 50})
    f = _by(check_results(res, _bands([_mband("A", "B", 55, 65)])),
            "MATCHUP")
    assert [x.status for x in f] == ["IN"]
    assert f[0].sim_wr == 60.0


# ─── missing decks / missing cells skip, never crash ─────────────────

def test_missing_deck_in_results_skipped_with_notice():
    res = _results({"A|B": 50, "B|A": 50})
    f = _by(check_results(res, _bands([_mband("A", "Ghost", 40, 60)])),
            "MATCHUP")
    assert [x.status for x in f] == ["SKIP"]
    assert "Ghost" in f[0].detail


def test_missing_matrix_cell_skipped():
    # Both decks registered in names, but the pair was never simmed.
    res = _results({"A|B": 50, "B|A": 50}, names=["A", "B", "C"])
    f = _by(check_results(res, _bands([_mband("A", "C", 40, 60)])),
            "MATCHUP")
    assert [x.status for x in f] == ["SKIP"]


# ─── field bands: row average + default band ─────────────────────────

def test_field_band_uses_row_average():
    res = _results({"A|B": 80, "B|A": 20, "A|C": 40, "C|A": 60,
                    "B|C": 50, "C|B": 50})
    bands = _bands(fields=[{"deck": "A", "expected_wr": [55, 65],
                            "provenance": "test"}])
    f = _by(check_results(res, bands), "FIELD")
    a = [x for x in f if x.anchor.startswith("A")]
    assert a[0].status == "IN" and a[0].sim_wr == 60.0  # (80+40)/2


def test_field_default_band_applies_to_unlisted_decks():
    res = _results({"A|B": 90, "B|A": 10})
    bands = _bands(default={"expected_wr": [30, 70],
                            "provenance": "default"})
    f = {x.anchor.split(" ")[0]: x for x in _by(check_results(res, bands),
                                                "FIELD")}
    assert f["A"].status == "OUT" and f["A"].direction == "above"
    assert f["B"].status == "OUT" and f["B"].direction == "below"


# ─── exit codes: non-fatal by default, --strict fails on OUT ─────────

def _write(tmp_path, results, bands):
    rp = tmp_path / "results.json"
    bp = tmp_path / "bands.json"
    rp.write_text(json.dumps(results))
    bp.write_text(json.dumps(bands))
    return str(rp), str(bp)


def test_exit_zero_by_default_even_when_out_of_band(tmp_path, capsys):
    rp, bp = _write(tmp_path, _results({"A|B": 90, "B|A": 10}),
                    _bands([_mband("A", "B", 45, 55)]))
    assert main([rp, "--bands", bp]) == 0
    assert "OUT" in capsys.readouterr().out


def test_strict_exits_one_on_out_of_band(tmp_path):
    rp, bp = _write(tmp_path, _results({"A|B": 90, "B|A": 10}),
                    _bands([_mband("A", "B", 45, 55)]))
    assert main([rp, "--bands", bp, "--strict"]) == 1


def test_strict_exits_zero_when_all_in_band(tmp_path):
    rp, bp = _write(tmp_path, _results({"A|B": 50, "B|A": 50}),
                    _bands([_mband("A", "B", 45, 55)]))
    assert main([rp, "--bands", bp, "--strict"]) == 0


def test_missing_results_file_is_a_usage_error(tmp_path):
    _, bp = _write(tmp_path, {}, _bands())
    assert main([str(tmp_path / "nope.json"), "--bands", bp]) == 2


# ─── committed bands file is well-formed ─────────────────────────────

def test_committed_bands_file_loads_and_is_schema_1():
    bands = load_bands(DEFAULT_BANDS_PATH)
    assert str(bands["schema"]).split(".")[0] == "1"
    assert len(bands["matchup_bands"]) >= 10
    for b in bands["matchup_bands"]:
        lo, hi = b["expected_wr_a"]
        assert 0 <= lo < hi <= 100
        assert b["provenance"]
    for b in bands["field_bands"]:
        lo, hi = b["expected_wr"]
        assert 0 <= lo < hi <= 100
        assert b["provenance"]


# ─── real-file smoke: committed matrix vs committed bands ────────────

@pytest.mark.skipif(not _RESULTS.exists(),
                    reason="committed metagame_results.json not present")
def test_smoke_committed_results_completes_with_summary(capsys):
    # Do NOT assert in/out counts: the committed matrix predates the
    # current fix wave and is out of band on several pairs — that is
    # the tool's value proposition, not a failure.
    assert main([str(_RESULTS)]) == 0
    out = capsys.readouterr().out
    assert "calibration summary:" in out
    assert "in band" in out and "out of band" in out


# ─── run_meta hook: non-fatal, degrades on missing bands ─────────────

def test_run_meta_hook_runs_nonfatally(capsys):
    import run_meta
    run_meta._run_calibration_check(str(_RESULTS))
    assert "calibration summary:" in capsys.readouterr().out


def test_run_meta_hook_missing_bands_degrades_to_notice(monkeypatch,
                                                        capsys):
    import run_meta
    import tools.check_calibration as cc
    monkeypatch.setattr(cc, "DEFAULT_BANDS_PATH", "/nonexistent/bands.json")
    run_meta._run_calibration_check(str(_RESULTS))
    err = capsys.readouterr().err
    assert "check_calibration: skipped" in err
