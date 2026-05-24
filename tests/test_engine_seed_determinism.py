"""Engine seed determinism.

Bug: ``run_meta._run_game`` / ``_run_match`` call ``random.seed(seed)``
which seeds Python's global ``random`` module, but ``GameRunner.rng``
is a separate ``random.Random()`` instance created with system entropy
at runner construction time. The seed never reaches the engine, so
two back-to-back identical calls produce different game outcomes.

This is a silent correctness bug for any test or matrix gate that
relies on seed reproducibility (Phase 2.5 discovered it; Phase 2b
matrix-gate noise tolerances depend on the fix).

CR: there is no rules-text justification for non-determinism; this
is purely a plumbing bug.
"""
from __future__ import annotations

import pytest

from decks.modern_meta import MODERN_DECKS
from run_meta import _get_runner, _run_game, _run_match


@pytest.fixture(scope="module")
def runner():
    return _get_runner()


class TestEngineSeedDeterminism:
    """Two identical (seed, deck1, deck2) calls must produce identical
    outcomes. Pre-fix this fails because runner.rng draws from system
    entropy regardless of the global random.seed() call."""

    def test_run_game_deterministic_under_same_seed(self, runner):
        """Bo1 path: same seed → same winner_deck.

        Domain Zoo vs Affinity at seed 50000 is empirically a
        shuffle-sensitive matchup — pre-fix the winner flips between
        consecutive calls because runner.rng state has advanced. Less
        balanced matchups (Boros vs Affinity, Storm vs Dimir) hide
        the bug because their outcome doesn't depend on shuffle order.
        """
        d1, d2 = "Domain Zoo", "Affinity"
        seed = 50000

        r1 = _run_game(runner, d1, d2, seed)
        r2 = _run_game(runner, d1, d2, seed)

        assert r1.winner_deck == r2.winner_deck, (
            f"Non-deterministic under seed {seed}: "
            f"r1={r1.winner_deck} vs r2={r2.winner_deck}. "
            f"runner.rng is not being seeded.")

    def test_run_match_deterministic_under_same_seed(self, runner):
        """Bo3 path on a shuffle-sensitive matchup: same seed →
        same match.winner_deck and same match_score tuple."""
        d1, d2 = "Domain Zoo", "Affinity"
        seed = 50000

        m1 = _run_match(runner, d1, d2, seed)
        m2 = _run_match(runner, d1, d2, seed)

        assert m1.winner_deck == m2.winner_deck, (
            f"Bo3 winner non-deterministic: "
            f"m1={m1.winner_deck} vs m2={m2.winner_deck}")
        assert m1.match_score == m2.match_score, (
            f"Bo3 score non-deterministic: "
            f"m1={m1.match_score} vs m2={m2.match_score}")

    def test_different_seeds_produce_distinct_outcomes(self):
        """Sanity: the seed actually controls outcome (this would fail
        if the fix accidentally hard-coded a constant). At least one of
        four different seeds must produce a distinct single-game result.

        Measured at the SINGLE-GAME level (turn count + winner), not the
        Bo3 match score. A Bo3 match score is a coarse 3-bucket signal
        and for a lopsided matchup collapses to one value at every nearby
        seed (Ruby Storm vs Dimir is currently 0-2 at seeds 50000-53000),
        which made the old match-level assertion both stale and fragile.
        `_run_game` fully reseeds `random` and `runner.rng` per call, so
        a fresh runner per seed makes this independent of any prior test's
        global state (the old shared-runner Bo3 form derived its "variance"
        from runner-state accumulation and broke when a sibling test
        perturbed process globals).

        Empirical (2026-05-20): Ruby Storm vs Dimir at seeds
        (50000, 51000, 52000, 53000) → games of (T6, T8, T7, T6):
        three distinct game lengths, so the seed demonstrably matters.
        """
        d1, d2 = "Ruby Storm", "Dimir Midrange"
        outcomes = []
        for s in (50000, 51000, 52000, 53000):
            r = _run_game(_get_runner(), d1, d2, s)
            outcomes.append((r.winner_deck, r.turns))

        assert len(set(outcomes)) > 1, (
            f"All four seeds produced the same single-game outcome "
            f"({outcomes[0]}); seed is being ignored entirely.")
