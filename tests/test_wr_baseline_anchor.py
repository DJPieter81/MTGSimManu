"""Deterministic match-outcome regression anchor (drift detection).

This test pins the *deterministic* (winner, turns) outcome of 16
canonical-seed matchups. The full Bo3 matrix is too slow to run on
every PR (~30-40 min); this 16-Bo1 anchor runs in ~3 seconds and
trips on any code change that alters the deterministic seed → match
outcome mapping for the listed pairings.

When this test trips:

  1. Investigate the trip. Is the new winner correct (a real
     behavioral fix)? Or is it an unintended regression?
  2. If the new outcome is correct, regenerate the snapshot:
     ``python tools/refresh_wr_baseline.py`` (or rerun the same
     loop used to seed ``tests/fixtures/wr_baseline_anchor.json``).
  3. Commit the new baseline as part of the same PR with a 1-line
     comment in the PR body explaining what changed and why.

What this test is NOT:

  - It is not a *statistical* WR test. Each entry is a single
    deterministic game outcome at one fixed seed. Statistical WR
    bands (50–65% target for Affinity, etc.) belong in a matrix run.
  - It is not a correctness oracle. A change that "improves" a deck
    will trip the test; that's the intended signal, not noise.

The 16 matchups touch every one of the 16 registered Modern decks
at least twice across the snapshot.

Cross-reference: docs/research (Phase 4) for the larger drift-
detection story. This anchor is the cheapest possible regression
gate; matrix runs remain the ground-truth measurement.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "wr_baseline_anchor.json"


@pytest.fixture(scope="module")
def baseline() -> list[dict]:
    """Load the committed baseline."""
    with FIXTURE_PATH.open() as f:
        data = json.load(f)
    return data["matchups"]


@pytest.fixture(scope="module")
def runner():
    """Single GameRunner shared across all baseline matchups (warmup
    cost amortizes across 16 matches → ~0.1s/match)."""
    # Silence engine logging for the duration of the test — we only
    # care about the deterministic match outcome, not the play log.
    logging.disable(logging.CRITICAL)
    from engine.card_database import CardDatabase
    from engine.game_runner import GameRunner

    db = CardDatabase()
    return GameRunner(db)


def _replay(runner, deck1: str, deck2: str, seed: int) -> dict:
    """Re-run a Bo1 match at the given seed and return the
    deterministic outcome shape used in the fixture."""
    from run_meta import _run_pair

    r = _run_pair(runner, deck1, deck2, seed=seed, bo1=True)
    return {"winner": r.winner_deck, "turns": r.turns}


def test_baseline_fixture_present_and_nonempty(baseline):
    """Sanity: the fixture exists and has at least 8 entries."""
    assert len(baseline) >= 8, (
        f"WR baseline anchor has only {len(baseline)} entries; "
        f"expected ≥ 8 for meaningful coverage. Refresh the snapshot."
    )


def test_baseline_covers_all_decks(baseline):
    """Sanity: every registered Modern deck appears at least once
    in the baseline. If a new deck lands without being added here,
    that's an oversight — the snapshot should be refreshed."""
    from decks.modern_meta import MODERN_DECKS

    decks_in_baseline: set[str] = set()
    for entry in baseline:
        decks_in_baseline.add(entry["deck1"])
        decks_in_baseline.add(entry["deck2"])

    registered = set(MODERN_DECKS.keys())
    missing = registered - decks_in_baseline
    assert not missing, (
        f"WR baseline does not cover these registered decks: "
        f"{sorted(missing)}. Add them to the fixture and refresh."
    )


@pytest.mark.parametrize(
    "idx",
    range(17),
    ids=lambda i: f"baseline[{i}]",
)
def test_match_outcome_matches_baseline(idx, baseline, runner):
    """Each baseline entry: re-run the seeded match and assert the
    winner + turn count are unchanged. A failure means a code change
    has altered a deterministic outcome — the regression-anchor
    signal."""
    expected = baseline[idx]
    actual = _replay(runner, expected["deck1"], expected["deck2"],
                     expected["seed"])
    assert actual["winner"] == expected["winner"], (
        f"WR anchor drift at {expected['deck1']} vs "
        f"{expected['deck2']} (seed={expected['seed']}): "
        f"baseline winner={expected['winner']}, actual={actual['winner']}. "
        f"If this is an intentional improvement, refresh the snapshot."
    )
    assert actual["turns"] == expected["turns"], (
        f"WR anchor drift at {expected['deck1']} vs "
        f"{expected['deck2']} (seed={expected['seed']}): "
        f"winner unchanged but turn count shifted "
        f"({expected['turns']} → {actual['turns']}). Same drift signal "
        f"— refresh the snapshot if intentional."
    )
