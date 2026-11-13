"""The rules-audit report ranks a JSONL of findings. Two ranking rules:

  * a VIOLATION is a real per-game event — its `count` is every finding.
  * a CENSUS row is deduped, because `engine.rules_audit.census` dedupes only
    per PROCESS, so N parallel matrix workers each re-emit the same
    (rule, key) once. The report counts distinct (key, seed, deck pair) so a
    mechanic seen in every worker is not ranked N× above a real violation.

Census is ranked by how many deck pairs surfaced it (breadth), not by the
inflated raw count.
"""
from __future__ import annotations

from tools.rules_audit_report import summarize, format_summary


def _census(rule, key, seed, d1, d2):
    return {"kind": "census", "rule": rule, "key": key,
            "seed": seed, "deck1": d1, "deck2": d2, "detail": ""}


def _violation(rule, seed, d1, d2, detail=""):
    return {"kind": "violation", "rule": rule,
            "seed": seed, "deck1": d1, "deck2": d2, "detail": detail}


def test_census_rows_are_deduped_across_parallel_workers():
    # Two workers each emit the SAME (rule, key) for the SAME game — a pure
    # per-process re-emission. The report must count it once.
    rows = [
        _census("keyword/unmodelled", "devoid", 50000, "A", "B"),
        _census("keyword/unmodelled", "devoid", 50000, "A", "B"),  # dup worker
    ]
    s = summarize(rows)["keyword/unmodelled"]
    assert s["count"] == 1, "identical census re-emissions collapse to one"


def test_census_count_grows_with_distinct_deck_pairs():
    rows = [
        _census("keyword/unmodelled", "devoid", 50000, "A", "B"),
        _census("keyword/unmodelled", "devoid", 50500, "A", "C"),  # new pair
    ]
    s = summarize(rows)["keyword/unmodelled"]
    assert s["count"] == 2
    assert s["pairs"] == 2


def test_a_violation_keeps_its_raw_per_game_count():
    rows = [
        _violation("510.2/creature_dealt", 50000, "A", "B"),
        _violation("510.2/creature_dealt", 50000, "A", "B"),  # a second game event
    ]
    s = summarize(rows)["510.2/creature_dealt"]
    assert s["count"] == 2, "each violation is a real event, never deduped"


def test_census_is_ranked_by_breadth_not_inflated_count():
    # 'wide' surfaces on two pairs (one row each); 'narrow' re-emits three
    # times on ONE pair. Breadth ranking puts 'wide' first.
    rows = [
        _census("keyword/unmodelled", "wide", 50000, "A", "B"),
        _census("keyword/unmodelled", "wide", 50500, "A", "C"),
        _census("keyword/unmodelled", "narrow", 50000, "A", "B"),
        _census("keyword/unmodelled", "narrow", 50000, "A", "B"),
        _census("keyword/unmodelled", "narrow", 50000, "A", "B"),
    ]
    # (both keys share a rule_id here; split them so ranking is visible)
    rows = [dict(r, rule=f"kw/{r['key']}") for r in rows]
    out = format_summary(summarize(rows))
    wide_pos = out.index("kw/wide")
    narrow_pos = out.index("kw/narrow")
    assert wide_pos < narrow_pos, "census ranks by deck-pair breadth"
