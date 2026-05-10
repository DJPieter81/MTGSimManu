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

    def test_different_seeds_produce_distinct_outcomes(self, runner):
        """Sanity: the seed actually controls outcome (this would fail
        if the fix accidentally hard-coded a constant). At least one
        of three different seeds should differ from the first.

        Uses Ruby Storm vs Dimir Midrange — a variance-friendly Bo3
        matchup whose outcome at the match-score level depends on
        shuffle order (combo-vs-disruption is shuffle-sensitive: a
        Storm hand with a fast-mana cluster wins; a hand bricked on
        rituals loses to early counterspells/discard).

        Domain Zoo vs Affinity (the previous fixture) is too lopsided
        for n=1 Bo3 — Affinity wins 2-0 at all four sample seeds, so
        the assertion fails despite the engine being properly seeded.
        The bug we want to catch (constant outcome regardless of seed)
        is unobservable in a pre-decided matchup, so we pick a pair
        where Bo3 outcomes naturally vary across nearby seeds.

        Empirical (2026-05-10): seeds (50000, 51000, 52000, 53000) →
        (0,2), (2,1), (2,1), (1,2) — three distinct match scores.
        """
        d1, d2 = "Ruby Storm", "Dimir Midrange"
        baseline = _run_match(runner, d1, d2, 50000).match_score

        differing = []
        for s in (51000, 52000, 53000):
            r = _run_match(runner, d1, d2, s).match_score
            if r != baseline:
                differing.append((s, r))

        assert differing, (
            f"All four seeds produced same score ({baseline}); "
            f"seed is being ignored entirely.")
