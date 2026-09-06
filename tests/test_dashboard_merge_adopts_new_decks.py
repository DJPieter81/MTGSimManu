"""build_dashboard.merge() must adopt decks present in the results file but
absent from the existing metagame_data.jsx, not silently drop them.

Regression, 2026-08-31: a full 25-deck matrix was merged into a JSX that
still had the pre-Aug-2026 19-deck set. `merge()` read `decks = D['decks']`
and iterated ONLY that list — six real matchup results (including Creatures
Toolbox, the worst outlier in that run) were computed, saved to
`metagame_results.json`, and then silently discarded on merge. The dashboard
under-represented the field by six decks with no error or warning.

Card-narrative detail (`matchup_cards`, per-deck MVP/finisher summaries)
cannot be backfilled from a matrix run — that data comes from verbose game
logs, a separate pipeline. A newly-adopted deck must still get a coherent
`overall` entry (real win rate from the matrix) and a minimal `deck_cards`
placeholder; the frontend already renders "Card-level data pending verbose
run for this matchup" when narrative detail is absent (`showMatchup`'s
`else` branch) and `DC[idx] || {}` in `showDeckProfile`, so a placeholder
deck must not crash either path — it must just look sparse, not missing.

Deck names are fixture carriers; the rule is "no deck present in a merged
results file is ever silently dropped."
"""
from __future__ import annotations

import json

import pytest

import build_dashboard as bd


def _base_D(decks):
    """A minimal but shape-correct D dict, the way metagame_data.jsx holds one."""
    n = len(decks)
    return {
        "decks": list(decks),
        "wins": [[0] * n for _ in range(n)],
        "matches_per_pair": 20,
        "overall": [
            {"deck": d, "idx": i, "win_rate": 50.0, "weighted_wr": 50.0,
             "total_wins": 0, "total_matches": 0}
            for i, d in enumerate(decks)
        ],
        "matchup_cards": {},
        "deck_cards": [{"deck": d, "idx": i} for i, d in enumerate(decks)],
        "meta_shares": {d: 1.0 for d in decks},
    }


def _write_jsx(path, D):
    path.write_text(
        "const D = " + json.dumps(D) + ";\n"
        "const N = D.matches_per_pair;\n"
    )


def _write_results(path, names, matrix_pct):
    """`matrix_pct` maps "d1|d2" -> win percentage for d1 over d2."""
    payload = {
        "type": "matrix",
        "n_games": 20,
        "names": list(names),
        "matrix": matrix_pct,
    }
    path.write_text(json.dumps(payload))


def test_a_deck_present_in_results_but_absent_from_the_jsx_is_adopted(tmp_path):
    old_decks = ["Deck A", "Deck B"]
    new_decks = ["Deck A", "Deck B", "Deck C"]
    jsx = tmp_path / "metagame_data.jsx"
    results = tmp_path / "metagame_results.json"
    out = tmp_path / "out.html"

    _write_jsx(jsx, _base_D(old_decks))
    matrix = {
        "Deck A|Deck B": 60, "Deck B|Deck A": 40,
        "Deck A|Deck C": 70, "Deck C|Deck A": 30,
        "Deck B|Deck C": 55, "Deck C|Deck B": 45,
    }
    _write_results(results, new_decks, matrix)

    bd.merge(results_path=str(results), jsx_path=str(jsx), out_html=str(out))

    D = bd.load_D(str(jsx))
    assert "Deck C" in D["decks"], (
        "a deck present in the results file must not be silently dropped "
        "by merge() just because it predates the existing jsx deck list")
    assert len(D["decks"]) == 3


def test_the_adopted_decks_wins_matrix_row_and_column_are_real_not_zero(tmp_path):
    old_decks = ["Deck A", "Deck B"]
    new_decks = ["Deck A", "Deck B", "Deck C"]
    jsx = tmp_path / "metagame_data.jsx"
    results = tmp_path / "metagame_results.json"
    out = tmp_path / "out.html"

    _write_jsx(jsx, _base_D(old_decks))
    matrix = {
        "Deck A|Deck B": 60, "Deck B|Deck A": 40,
        "Deck A|Deck C": 70, "Deck C|Deck A": 30,
        "Deck B|Deck C": 55, "Deck C|Deck B": 45,
    }
    _write_results(results, new_decks, matrix)

    bd.merge(results_path=str(results), jsx_path=str(jsx), out_html=str(out))

    D = bd.load_D(str(jsx))
    idx = {d: i for i, d in enumerate(D["decks"])}
    n = D["matches_per_pair"]
    # Deck C's wins against Deck A: 30% of n_games.
    assert D["wins"][idx["Deck C"]][idx["Deck A"]] == round(0.30 * n)
    assert D["wins"][idx["Deck A"]][idx["Deck C"]] == round(0.70 * n)


def test_the_adopted_deck_gets_a_real_overall_win_rate_entry(tmp_path):
    old_decks = ["Deck A", "Deck B"]
    new_decks = ["Deck A", "Deck B", "Deck C"]
    jsx = tmp_path / "metagame_data.jsx"
    results = tmp_path / "metagame_results.json"
    out = tmp_path / "out.html"

    _write_jsx(jsx, _base_D(old_decks))
    matrix = {
        "Deck A|Deck B": 60, "Deck B|Deck A": 40,
        "Deck A|Deck C": 70, "Deck C|Deck A": 30,
        "Deck B|Deck C": 55, "Deck C|Deck B": 45,
    }
    _write_results(results, new_decks, matrix)

    bd.merge(results_path=str(results), jsx_path=str(jsx), out_html=str(out))

    D = bd.load_D(str(jsx))
    idx = {d: i for i, d in enumerate(D["decks"])}
    overall_by_idx = {e["idx"]: e for e in D["overall"]}
    entry = overall_by_idx[idx["Deck C"]]
    assert entry["deck"] == "Deck C"
    # C beat A 30% and B 45%: average 37.5%, not the placeholder 50.0 default.
    assert entry["win_rate"] == pytest.approx(37.5, abs=0.6)


def test_the_adopted_deck_has_no_matchup_narrative_but_does_not_crash_the_shape(tmp_path):
    """Card-level detail cannot be backfilled from a matrix run. The adopted
    deck must still produce a valid deck_cards placeholder — the frontend's
    `DC[idx] || {}` and the `showMatchup` "pending" branch already handle a
    deck with no narrative, so merge() must not invent fake narrative data,
    only a real overall/wins entry."""
    old_decks = ["Deck A", "Deck B"]
    new_decks = ["Deck A", "Deck B", "Deck C"]
    jsx = tmp_path / "metagame_data.jsx"
    results = tmp_path / "metagame_results.json"
    out = tmp_path / "out.html"

    _write_jsx(jsx, _base_D(old_decks))
    matrix = {
        "Deck A|Deck B": 60, "Deck B|Deck A": 40,
        "Deck A|Deck C": 70, "Deck C|Deck A": 30,
        "Deck B|Deck C": 55, "Deck C|Deck B": 45,
    }
    _write_results(results, new_decks, matrix)

    bd.merge(results_path=str(results), jsx_path=str(jsx), out_html=str(out))

    D = bd.load_D(str(jsx))
    idx = {d: i for i, d in enumerate(D["decks"])}
    dc_by_idx = {e["idx"]: e for e in D["deck_cards"]}
    entry = dc_by_idx[idx["Deck C"]]
    assert entry["deck"] == "Deck C"
    assert "mvp_casts" not in entry or not entry["mvp_casts"], (
        "an adopted deck must not be given fabricated MVP-card narrative")


def test_a_genuinely_partial_subset_run_is_still_refused(tmp_path, capsys):
    """The existing safety guard — refuse a run that covers FEWER decks
    than the current jsx — must survive the fix. Adopting new decks and
    refusing incomplete coverage of the OLD ones are different rules."""
    old_decks = ["Deck A", "Deck B", "Deck C"]
    jsx = tmp_path / "metagame_data.jsx"
    results = tmp_path / "metagame_results.json"
    out = tmp_path / "out.html"

    _write_jsx(jsx, _base_D(old_decks))
    # Results cover only 2 of the 3 existing decks: a genuine --decks N subset.
    _write_results(results, ["Deck A", "Deck B"],
                   {"Deck A|Deck B": 60, "Deck B|Deck A": 40})

    bd.merge(results_path=str(results), jsx_path=str(jsx), out_html=str(out))

    D = bd.load_D(str(jsx))
    assert D["decks"] == old_decks, (
        "a subset run must not overwrite the existing deck set or matrix")
    assert not out.exists(), "a refused merge must not rebuild the HTML"
