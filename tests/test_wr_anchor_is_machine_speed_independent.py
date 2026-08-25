"""The WR baseline anchor must not depend on how fast the machine is.

The anchor is this project's primary behavioural guarantee: 27 seeded matchups
whose winner and turn count are asserted to be unchanged. That guarantee is only
worth anything if the outcome is a function of the SEED, not of the hardware.

It was not. `engine/game_runner` arms a wall-clock deadline
(`GAME_TIMEOUT_SECONDS`, 8.0s) and, when it fires, breaks out of the turn loop,
sets `game_over`, and abandons stack resolution. A seeded game that finishes in
~4s on a fast machine can exceed 8s on a loaded 2-core CI runner and terminate
in a different state — flipping the recorded winner.

Observed: `Amulet Titan vs Living End @ 50000` passed locally (29/29) and
simultaneously failed in CI with `- Living End / + Amulet Titan`, on the same
commit.

Both failure directions matter. A false FAILURE wastes a session chasing a
regression that does not exist; a false PASS is worse, because a slow machine
can mask a real behavioural change behind an early termination.

Rule under test: replaying a baseline entry yields the same result regardless of
the wall-clock budget, and the anchor's own replay path does not arm a deadline
short enough to truncate its games.
"""
from __future__ import annotations

import json
import pathlib

import pytest

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "wr_baseline_anchor.json")


def _entry():
    """A baseline entry that runs long enough to be at risk of truncation."""
    entries = json.loads(FIXTURE.read_text())["matchups"]
    longest = max(entries, key=lambda e: e.get("turns", 0))
    return longest


def test_anchor_replay_disables_the_wall_clock_deadline():
    """The replay helper must neutralise the wall-clock bound.

    This is the structural guard: whatever the deadline's production value, the
    anchor's own replay must not be subject to it.
    """
    from tests import test_wr_baseline_anchor as anchor_mod

    src = pathlib.Path(anchor_mod.__file__).read_text()
    assert "_game_deadline" in src or "GAME_TIMEOUT" in src, (
        "the anchor's replay path must explicitly neutralise the wall-clock "
        "deadline; otherwise a slow machine changes seeded outcomes and the "
        "anchor stops being a function of the seed")


def test_same_seed_gives_same_result_under_a_generous_budget():
    """Replaying twice must agree — and agree with the committed fixture."""
    from tests.test_wr_baseline_anchor import _replay
    from engine.game_runner import GameRunner
    from tests._card_db_cache import shared_card_database

    entry = _entry()
    runner = GameRunner(shared_card_database())
    first = _replay(runner, entry["deck1"], entry["deck2"], entry["seed"])
    second = _replay(runner, entry["deck1"], entry["deck2"], entry["seed"])

    assert first == second, (
        f"the same seed produced two different results in one process: "
        f"{first} vs {second}")
    assert first["winner"] == entry["winner"], (
        f"replay disagrees with the committed baseline for "
        f"{entry['deck1']} vs {entry['deck2']} @ {entry['seed']}: "
        f"got {first}, fixture says "
        f"{{'winner': {entry['winner']!r}, 'turns': {entry['turns']}}}")
