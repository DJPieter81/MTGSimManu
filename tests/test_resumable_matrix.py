"""A matrix run survives a process restart: every finished cell is
checkpointed as it completes, a relaunch runs only the missing cells, and
the results file is written once, only when every cell is present, in the
same shape `run_meta.py --matrix --parallel --save` writes.

Rules pinned:
* a cell already in the checkpoint is never run again;
* the assembled results equal a direct `run_matrix_parallel_cells` run on
  the same pairs (same win rates, draws and aborts per cell);
* a partial checkpoint never writes a results file.
Deck names are fixture carriers only.
"""
from __future__ import annotations

import json

from tools import resumable_matrix as rm
from tools.parallel_matrix import run_matrix_parallel_cells

DECKS = ["Alpha", "Bravo", "Charlie"]
CALLS = []


def _fake_matchup(d1, d2, n_games=2):
    CALLS.append((d1, d2))
    wins = {"Alpha": 3, "Bravo": 2, "Charlie": 1}
    pct1 = 100.0 if wins[d1] > wins[d2] else 0.0
    return {"pct1": pct1, "pct2": 100.0 - pct1, "draws": 0, "aborted": 0}


def test_a_checkpointed_cell_is_never_run_again(tmp_path):
    ck = tmp_path / "ck.jsonl"
    ck.write_text(json.dumps({"d1": "Alpha", "d2": "Bravo", "wr": 100.0,
                              "wr_reverse": 0.0, "draws": 0, "aborted": 0,
                              "audit": []}) + "\n")
    CALLS.clear()
    cells = rm.run_cells(DECKS, n_games=2, workers=1, checkpoint=ck,
                         run_matchup_fn=_fake_matchup)
    assert ("Alpha", "Bravo") not in CALLS
    assert len(CALLS) == 5 and len(cells) == 6
    assert len(ck.read_text().splitlines()) == 6


def test_assembled_results_equal_a_direct_parallel_run(tmp_path):
    ck = tmp_path / "ck.jsonl"
    out = tmp_path / "metagame_results.json"
    rm.run(DECKS, n_games=2, workers=1, checkpoint=ck, results_path=out,
           run_matchup_fn=_fake_matchup, merge=False)
    direct = run_matrix_parallel_cells(DECKS, n_games=2, workers=1,
                                       run_matchup_fn=_fake_matchup)
    saved = json.loads(out.read_text())
    assert saved["type"] == "matrix" and saved["names"] == DECKS
    assert saved["n_games"] == 2
    for (d1, d2), cell in direct.items():
        assert saved["matrix"][f"{d1}|{d2}"] == cell.wr
        assert saved["cell_draws"][f"{d1}|{d2}"] == cell.draws
        assert saved["cell_aborted"][f"{d1}|{d2}"] == cell.aborted


def test_a_partial_checkpoint_never_writes_results(tmp_path):
    ck = tmp_path / "ck.jsonl"
    out = tmp_path / "metagame_results.json"

    def _dies_after_two(d1, d2, n_games=2):
        if len(CALLS) >= 2:
            raise KeyboardInterrupt
        return _fake_matchup(d1, d2, n_games)

    CALLS.clear()
    try:
        rm.run(DECKS, n_games=2, workers=1, checkpoint=ck, results_path=out,
               run_matchup_fn=_dies_after_two, merge=False)
    except KeyboardInterrupt:
        pass
    assert not out.exists()
    assert len(ck.read_text().splitlines()) == 2


def test_a_row_run_plays_only_that_decks_ordered_pairs(tmp_path):
    ck = tmp_path / "row.jsonl"
    CALLS.clear()
    cells = rm.run_cells(DECKS, n_games=2, workers=1, checkpoint=ck,
                         run_matchup_fn=_fake_matchup, row="Bravo")
    assert sorted(CALLS) == [("Bravo", "Alpha"), ("Bravo", "Charlie")]
    assert set(cells) == {("Bravo", "Alpha"), ("Bravo", "Charlie")}


def test_merged_row_checkpoints_assemble_like_one_full_run(tmp_path):
    shards = []
    for d in DECKS:
        ck = tmp_path / f"{d}.jsonl"
        rm.run_cells(DECKS, n_games=2, workers=1, checkpoint=ck,
                     run_matchup_fn=_fake_matchup, row=d)
        shards.append(ck)
    merged = tmp_path / "merged.jsonl"
    rm.merge_checkpoints(shards, merged)
    out = tmp_path / "metagame_results.json"
    CALLS.clear()
    rm.run(DECKS, n_games=2, workers=1, checkpoint=merged, results_path=out,
           run_matchup_fn=_fake_matchup, merge=False)
    assert CALLS == [], "every cell came from the shards"
    direct = run_matrix_parallel_cells(DECKS, n_games=2, workers=1,
                                       run_matchup_fn=_fake_matchup)
    saved = json.loads(out.read_text())
    for (d1, d2), cell in direct.items():
        assert saved["matrix"][f"{d1}|{d2}"] == cell.wr
