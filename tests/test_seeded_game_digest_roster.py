"""The seeded-game digest roster covers pre- and post-sideboard games.

`tools/seeded_game_digest.py` is the per-commit proof that a pure refactor
changes no seeded game (design doc 2026-09-29, F8/A42). Its proof is only as
wide as its roster, so the roster's shape is pinned here: 16 Bo1 games and 4
Bo3 matches (Bo3 exercises sideboarded games), canonical seeds from 50000 in
steps of 500, every deck registered, and a baseline for every roster entry.
No game is run by this test.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.seeded_game_digest import BASELINE_PATH, load_pairs


def test_digest_roster_has_sixteen_bo1_games_and_four_bo3_matches():
    pairs = load_pairs()
    assert len(pairs["bo1"]) == 16
    assert len(pairs["bo3"]) == 4


def test_digest_roster_seeds_start_at_the_canonical_seed_in_steps_of_500():
    pairs = load_pairs()
    seeds = [s for _, _, s in pairs["bo1"] + pairs["bo3"]]
    assert seeds == [50000 + 500 * i for i in range(len(seeds))]


def test_digest_roster_names_only_registered_decks():
    from decks.modern_meta import MODERN_DECKS
    pairs = load_pairs()
    for d1, d2, _ in pairs["bo1"] + pairs["bo3"]:
        assert d1 in MODERN_DECKS and d2 in MODERN_DECKS
        assert d1 != d2


def test_digest_baseline_records_every_roster_game_including_post_sideboard():
    base = json.loads(Path(BASELINE_PATH).read_text())
    pairs = load_pairs()
    keys = set(base["games"])
    for d1, d2, s in pairs["bo1"]:
        assert f"bo1|{d1}|{d2}|{s}" in keys
    post_sideboard = 0
    for d1, d2, s in pairs["bo3"]:
        assert f"bo3|{d1}|{d2}|{s}|g1" in keys
        assert f"bo3|{d1}|{d2}|{s}|g2" in keys
        post_sideboard += sum(
            1 for g in (2, 3) if f"bo3|{d1}|{d2}|{s}|g{g}" in keys)
    assert post_sideboard >= 4
    assert base["n_games"] == len(keys) >= 24
