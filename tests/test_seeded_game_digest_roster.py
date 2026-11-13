"""The seeded-game digest roster covers pre- and post-sideboard games.

`tools/seeded_game_digest.py` is the per-commit proof that a pure refactor
changes no seeded game (design doc 2026-09-29, F8/A42). Its proof is only as
wide as its roster, so the roster's shape is pinned here: 16 Bo1 games and 4
Bo3 matches (Bo3 exercises sideboarded games), canonical seeds from 50000 in
steps of 500, every deck registered, and a baseline for every roster entry.
No game is run by this test.

The tool forces the offline decision scorer (CLAUDE.md sequencing rule 0)
when it runs the roster, before its first engine or ai import -- never as a
side effect of importing it, which would flip the flag for a whole pytest
session and make other tests depend on collection order.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

from tools.seeded_game_digest import BASELINE_PATH, load_pairs

REPO = Path(__file__).resolve().parent.parent
_FLAG = "MTG_LLM_DECISION_SCORER_OFFLINE"


def _flag_after(code: str) -> str:
    """The flag's value after `code` runs in a fresh interpreter that starts
    without it (never mutating this process's environment)."""
    env = {k: v for k, v in os.environ.items() if k != _FLAG}
    return subprocess.run(
        [sys.executable, "-c", f"import os; {code}; print(os.environ.get({_FLAG!r}))"],
        capture_output=True, text=True, env=env, cwd=REPO,
        check=True).stdout.strip()


def test_importing_the_digest_tool_leaves_the_environment_alone():
    assert _flag_after("import tools.seeded_game_digest") == "None"


def test_running_the_roster_forces_the_offline_scorer_before_any_engine_import():
    import tools.seeded_game_digest as tool
    assert _flag_after("import tools.seeded_game_digest as t; "
                       "t.force_offline_scorer()") == "1"
    # compute_digests calls it first, before any import in its body.
    fn = ast.parse(inspect.getsource(tool.compute_digests)).body[0]
    first = fn.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        first = fn.body[1]          # skip the docstring
    assert (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
            and getattr(first.value.func, "id", None) == "force_offline_scorer")
    imports = [n for n in ast.walk(fn) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports and all(n.lineno > first.lineno for n in imports)


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
