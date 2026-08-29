#!/usr/bin/env python3
"""Regenerate ``tests/fixtures/wr_baseline_anchor.json``.

Use after a PR that deliberately changes a deterministic match
outcome. Commit the regenerated baseline as part of the same PR
with a 1-line explanation.

Usage:

    python tools/refresh_wr_baseline.py

The 16-matchup roster is hardcoded in this script — the same one
``tests/test_wr_baseline_anchor.py`` asserts against. If a deck is
added to ``decks/modern_meta.py``, extend ``MATCHUPS`` below to
include it in at least one pairing, then re-run this tool.

Runtime: ~3 seconds (mostly DB load + first-match warmup).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Silence engine logging — we only want the deterministic outcome.
logging.disable(logging.CRITICAL)

# Suppress sideboard-manager stderr spam.
_DEVNULL = open(os.devnull, "w")
_original_stderr = sys.stderr


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "wr_baseline_anchor.json"

# Allow `python tools/refresh_wr_baseline.py` from any cwd.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# 17 canonical pairings: every registered deck appears at least twice.
MATCHUPS: list[tuple[str, str, int]] = [
    ("Boros Energy",          "Affinity",                50000),
    ("Ruby Storm",            "Dimir Midrange",          50000),
    ("Domain Zoo",            "Eldrazi Tron",            50000),
    ("Amulet Titan",          "Living End",              50000),
    ("Jeskai Blink",          "4c Omnath",               50000),
    ("Goryo's Vengeance",     "Izzet Prowess",           50000),
    ("Pinnacle Affinity",     "4/5c Control",            50000),
    ("Azorius Control",       "Azorius Control (WST)",   50000),
    ("Azorius Control (WST v2)", "Boros Energy",         50000),
    ("Boros Energy",          "Ruby Storm",              50500),
    ("Affinity",              "Domain Zoo",              50500),
    ("Eldrazi Tron",          "Amulet Titan",            50500),
    ("Living End",            "Jeskai Blink",            50500),
    ("4c Omnath",             "Goryo's Vengeance",       50500),
    ("Izzet Prowess",         "Dimir Midrange",          50500),
    ("4/5c Control",          "Pinnacle Affinity",       50500),
    ("Azorius Control (WST)", "Azorius Control (WST v2)", 50500),
    # July 2026 meta refresh — new registrations, each appears twice.
    ("Instant Reanimator",    "Dimir Midrange",          51000),
    ("Boros Ponza",           "Boros Energy",            51000),
    ("Instant Reanimator",    "Boros Ponza",             51500),
    # Aug 2026 meta-gap fill — new registrations, each appears twice.
    ("Eldrazi Ramp",          "Broodscale Bloodchief",   52000),
    ("Creatures Toolbox",     "Grixis Reanimator",       52000),
    ("Azorius Blink",         "Eldrazi Ramp",            52500),
    ("Broodscale Bloodchief", "Creatures Toolbox",       52500),
    ("Grixis Reanimator",     "Azorius Blink",           53000),
    # Hollow One (Aug 2026, unblocked by refresh_card_db.yml).
    ("Hollow One",            "Eldrazi Ramp",            53500),
    ("Broodscale Bloodchief", "Hollow One",              53500),
]


def main() -> None:
    sys.stderr = _DEVNULL
    try:
        from run_meta import _run_pair
        from engine.card_database import CardDatabase
        from engine.game_runner import GameRunner

        # Neutralise the wall-clock deadline for the SAME reason the anchor
        # test does (tests/test_wr_baseline_anchor.py::_ANCHOR_TIMEOUT_SECONDS,
        # and its `_replay` docstring): the engine's GAME_TIMEOUT_SECONDS
        # breaks out of the turn loop and abandons the game, so a recorded
        # outcome becomes a function of MACHINE SPEED as well as of the seed.
        #
        # The test was hardened for this; the refresher was not, and that gap
        # nearly poisoned the fixture: run on a loaded container (a 4x-slower
        # host with field sweeps in flight), an unprotected refresh rewrote 14
        # of 29 entries as `draw` at turns 1-8, while the self-neutralising
        # anchor test on the identical code reported exactly ONE real drift.
        # A refresher that writes machine-dependent values corrupts the very
        # fixture meant to detect drift, so the two must neutralise
        # identically. The constant is imported rather than duplicated so the
        # tool and the test cannot silently diverge.
        import ai.constants as _ai_constants
        from tests.test_wr_baseline_anchor import _ANCHOR_TIMEOUT_SECONDS
        _ai_constants.GAME_TIMEOUT_SECONDS = _ANCHOR_TIMEOUT_SECONDS

        db = CardDatabase()
        runner = GameRunner(db)

        entries: list[dict] = []
        for d1, d2, seed in MATCHUPS:
            r = _run_pair(runner, d1, d2, seed=seed, bo1=True)
            entries.append({
                "deck1": d1, "deck2": d2, "seed": seed,
                "winner": r.winner_deck,
                "turns": r.turns,
            })
    finally:
        sys.stderr = _original_stderr

    payload = {
        "comment": (
            "Deterministic Bo1 match outcomes at canonical seeds. "
            "Each entry is (deck1, deck2, seed) -> (winner, turns). "
            "test_wr_baseline_anchor.py asserts these match on every "
            "run. Any code change that shifts a deterministic outcome "
            "for any matchup trips the test, prompting a deliberate "
            "snapshot refresh."
        ),
        "matchups": entries,
    }
    FIXTURE_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {len(entries)} entries to {FIXTURE_PATH}")
    for e in entries:
        print(
            f"  {e['deck1']} vs {e['deck2']} s{e['seed']}: "
            f"winner={e['winner']} T{e['turns']}"
        )


if __name__ == "__main__":
    main()
