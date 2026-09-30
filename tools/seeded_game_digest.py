#!/usr/bin/env python3
"""Seeded-game digest: a per-commit proof that a refactor changes no game.

A pure refactor (no resolution behaviour change) must leave every seeded
game log byte-identical to its parent commit. This tool runs a fixed roster
of seeded games — 16 Bo1 games and 4 Bo3 matches with sideboards passed, so
post-sideboard games and sideboard cards are exercised — and records the
sha256 of each verbose game log, per game and combined.

Usage::

    python tools/seeded_game_digest.py --record   # write the baseline
    python tools/seeded_game_digest.py --check    # compare against it

``--check`` exits non-zero and names every game whose digest differs.

Determinism guards (CLAUDE.md "Sequencing rules"):

* ``MTG_LLM_DECISION_SCORER_OFFLINE=1`` is set before any engine or ai
  import and asserted, so no live LLM call enters the decision loop.
* The CPU safety budget is neutralised through the same constant the WR
  anchor test and ``tools/refresh_wr_baseline.py`` use
  (``tests.test_wr_baseline_anchor._ANCHOR_TIMEOUT_SECONDS``), so a
  recorded digest is a function of the seed alone, not of machine speed.

This is a per-commit proof tool, not a standing CI gate. The roster lives in
``tools/seeded_game_digest_pairs.json``; the baseline in
``tools/seeded_game_digest_baseline.json``. Design reference:
``docs/design/2026-09-29_clause_and_trigger_grammar.md`` (F8, A42).
"""
from __future__ import annotations

import os

# Must precede every engine / ai import (see module docstring).
os.environ["MTG_LLM_DECISION_SCORER_OFFLINE"] = "1"
assert os.environ.get("MTG_LLM_DECISION_SCORER_OFFLINE") == "1"

import argparse
import hashlib
import json
import logging
import random
import sys
from pathlib import Path
from typing import Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PAIRS_PATH = REPO_ROOT / "tools" / "seeded_game_digest_pairs.json"
BASELINE_PATH = REPO_ROOT / "tools" / "seeded_game_digest_baseline.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_pairs(path: Path = PAIRS_PATH) -> Dict[str, List[Tuple[str, str, int]]]:
    """The roster: ``{"bo1": [(d1, d2, seed)], "bo3": [(d1, d2, seed)]}``."""
    data = json.loads(path.read_text())
    return {
        "bo1": [tuple(p) for p in data["bo1"]],
        "bo3": [tuple(p) for p in data["bo3"]],
    }


def _log_digest(lines: List[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _entry(result) -> dict:
    return {
        "sha256": _log_digest(result.game_log),
        "lines": len(result.game_log),
        "winner": result.winner_deck,
        "turns": result.turns,
    }


def compute_digests(pairs=None) -> dict:
    """Run the roster and return the digest document (JSON-serialisable)."""
    pairs = pairs if pairs is not None else load_pairs()
    logging.disable(logging.CRITICAL)

    import ai.constants as _ai_constants
    from tests.test_wr_baseline_anchor import _ANCHOR_TIMEOUT_SECONDS
    _ai_constants.GAME_TIMEOUT_SECONDS = _ANCHOR_TIMEOUT_SECONDS

    from decks.modern_meta import MODERN_DECKS
    from engine.card_database import CardDatabase
    from engine.game_runner import GameRunner

    runner = GameRunner(CardDatabase())
    games: Dict[str, dict] = {}

    for d1, d2, seed in pairs["bo1"]:
        a, b = MODERN_DECKS[d1], MODERN_DECKS[d2]
        random.seed(seed)
        runner.rng.seed(seed)
        r = runner.run_game(
            d1, a["mainboard"], d2, b["mainboard"],
            deck1_sideboard=a.get("sideboard", {}),
            deck2_sideboard=b.get("sideboard", {}),
            verbose=True,
        )
        games[f"bo1|{d1}|{d2}|{seed}"] = _entry(r)

    for d1, d2, seed in pairs["bo3"]:
        random.seed(seed)
        runner.rng.seed(seed)
        m = runner.run_match(d1, MODERN_DECKS[d1], d2, MODERN_DECKS[d2],
                             verbose=True)
        for g in m.games:
            games[f"bo3|{d1}|{d2}|{seed}|g{g.game_number}"] = _entry(g)

    combined = hashlib.sha256(
        "\n".join(f"{k}={games[k]['sha256']}" for k in sorted(games)).encode()
    ).hexdigest()
    return {
        "comment": (
            "sha256 of each seeded verbose game log (tools/seeded_game_digest.py). "
            "A pure refactor must reproduce every digest byte for byte."
        ),
        "n_games": len(games),
        "combined": combined,
        "games": games,
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _loadavg() -> str:
    try:
        return Path("/proc/loadavg").read_text().strip()
    except OSError:
        return "unavailable"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true",
                      help="write the baseline from the current tree")
    mode.add_argument("--check", action="store_true",
                      help="compare the current tree against the baseline")
    args = ap.parse_args(argv)

    # Games are CPU-budgeted and the budget is neutralised, so load does not
    # change the digest; the reading is printed for the record only.
    print(f"loadavg: {_loadavg()}")
    doc = compute_digests()
    text = render(doc)

    if args.record:
        BASELINE_PATH.write_text(text)
        print(f"recorded {doc['n_games']} games, combined {doc['combined']}")
        return 0

    baseline_text = BASELINE_PATH.read_text()
    if baseline_text == text:
        print(f"OK: {doc['n_games']} games byte-identical, "
              f"combined {doc['combined']}")
        return 0
    base = json.loads(baseline_text)
    bg, ng = base.get("games", {}), doc["games"]
    for key in sorted(set(bg) | set(ng)):
        if bg.get(key) != ng.get(key):
            print(f"DIFF {key}: baseline={bg.get(key)} now={ng.get(key)}")
    print(f"FAIL: combined baseline={base.get('combined')} now={doc['combined']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
