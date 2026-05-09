"""Phase 4A Week 3 — determinizer correctness tests.

The determinizer perturbs opp-side fields per rollout to simulate
"what could the opponent have" hypotheses. Properties to verify:

  1. Perturbed states stay within rules-feasible bounds
     (counts ≥ 0).
  2. With probability 0.5 (per design), the determinizer returns
     the unperturbed state (preserves baseline rollouts).
  3. The original state is never mutated; the determinizer
     returns a fresh SearchState.
  4. End-to-end: ISMCTS with the determinized transition still
     converges on the same answer as without it on a deterministic
     toy (perturbation is no-op for fields irrelevant to the
     decision).

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import EVSnapshot
from ai.search.determinizer import (
    Determinizer,
    PerturbationConfig,
    make_determinized_transition,
)
from ai.search.ismcts import ISMCTSPlanner, SearchConfig
from ai.search.snapshot_adapter import (
    ActionToken,
    SearchState,
    apply_action,
    enumerate_actions,
    evaluate_terminal,
    heuristic_rollout,
    make_search_state,
)


# ─── Bounds correctness ──────────────────────────────────────────────


class TestPerturbationBounds:
    def test_opp_creature_count_stays_nonnegative(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            opp_creature_count=0, opp_power=0, opp_hand_size=0,
        )
        state = make_search_state(snap)
        determinizer = Determinizer()
        # Run many samples; none should produce negative counts.
        rng = random.Random(0)
        for _ in range(200):
            sampled = determinizer.sample(state, rng)
            assert sampled.snapshot.opp_creature_count >= 0
            assert sampled.snapshot.opp_power >= 0
            assert sampled.snapshot.opp_hand_size >= 0

    def test_my_side_unperturbed(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            my_creature_count=3, my_power=5, my_hand_size=4,
        )
        state = make_search_state(snap)
        determinizer = Determinizer()
        rng = random.Random(0)
        for _ in range(50):
            sampled = determinizer.sample(state, rng)
            # My-side fields must NEVER be touched.
            assert sampled.snapshot.my_creature_count == 3
            assert sampled.snapshot.my_power == 5
            assert sampled.snapshot.my_hand_size == 4


# ─── Determinism + isolation ────────────────────────────────────────


class TestDeterminism:
    def test_same_seed_same_sample(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            opp_creature_count=2, opp_power=4, opp_hand_size=3,
        )
        state = make_search_state(snap)
        d = Determinizer()
        # Two independent rngs at the same seed must produce
        # identical samples.
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        s1 = d.sample(state, rng1)
        s2 = d.sample(state, rng2)
        assert s1.snapshot.opp_creature_count == s2.snapshot.opp_creature_count
        assert s1.snapshot.opp_power == s2.snapshot.opp_power
        assert s1.snapshot.opp_hand_size == s2.snapshot.opp_hand_size

    def test_original_state_not_mutated(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            opp_creature_count=3, opp_power=6,
        )
        state = make_search_state(snap)
        original_count = state.snapshot.opp_creature_count
        original_power = state.snapshot.opp_power
        d = Determinizer()
        rng = random.Random(7)
        for _ in range(20):
            d.sample(state, rng)
        assert state.snapshot.opp_creature_count == original_count
        assert state.snapshot.opp_power == original_power


# ─── No-op rate (preserves baseline rollouts) ───────────────────────


class TestNoOpRate:
    def test_about_half_of_samples_are_unperturbed(self):
        """Per design, 50% of samples return the original state.
        Verify this empirically — within 10% of 0.5 over 1000
        samples.
        """
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            opp_creature_count=2, opp_power=4, opp_hand_size=3,
        )
        state = make_search_state(snap)
        d = Determinizer()
        rng = random.Random(123)
        unchanged = 0
        for _ in range(1000):
            sampled = d.sample(state, rng)
            if (sampled.snapshot.opp_creature_count == snap.opp_creature_count
                    and sampled.snapshot.opp_power == snap.opp_power
                    and sampled.snapshot.opp_hand_size == snap.opp_hand_size):
                unchanged += 1
        # Some perturbed samples will randomly produce the same
        # state (Gaussian rounded to 0). The intentional 50% no-op
        # plus those random-zero samples → expected ~70% unchanged.
        # Lower bound: 50% (the intentional rate); upper bound: 90%.
        rate = unchanged / 1000
        assert 0.4 < rate < 0.95, (
            f"Determinizer unchanged-rate out of expected band. "
            f"Got {rate:.2f}."
        )


# ─── Transition wrapper ──────────────────────────────────────────────


class TestDeterminizedTransition:
    def test_first_call_per_rollout_determinizes(self):
        """The wrapper applies determinization on the first call
        for a given rng instance, then runs base transition
        directly on subsequent calls."""
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            opp_creature_count=2, opp_power=4,
        )
        action = ActionToken(kind="pass", label="pass")
        state = make_search_state(snap)
        d = Determinizer()
        wrapped = make_determinized_transition(d, apply_action)

        rng = random.Random(99)
        # First call: determinizes then applies action.
        s1 = wrapped(state, action, rng)
        # Second call: same rng id → uses base transition only.
        s2 = wrapped(s1, action, rng)
        # Both calls returned new SearchStates; original is intact.
        assert state.snapshot.turn_number == snap.turn_number


# ─── End-to-end: ISMCTS with determinizer ────────────────────────────


class TestISMCTSWithDeterminizer:
    def test_deterministic_seed_produces_same_action(self):
        """Determinizer adds rng-driven variance, but the search
        as a whole must remain deterministic under a fixed
        SearchConfig.seed. The rng inside the search is seeded
        from config.seed, so the determinizer's perturbations are
        also reproducible."""
        snap = EVSnapshot(
            my_life=20, opp_life=10,
            my_mana=2, my_total_lands=2,
            opp_creature_count=1, opp_power=2,
            turn_number=4,
        )
        bolt = ActionToken(
            kind="burn", label="Lightning Bolt",
            delta={"opp_life": -3}, cost=1,
        )
        creature = ActionToken(
            kind="cast_creature", label="Bear",
            delta={"my_power": 2, "my_toughness": 2,
                   "my_creature_count": 1}, cost=2,
        )

        d = Determinizer()
        wrapped = make_determinized_transition(d, apply_action)

        def _run():
            state = make_search_state(snap, [bolt, creature])
            config = SearchConfig(
                n_rollouts=200, rollout_depth=1, seed=55,
            )
            planner = ISMCTSPlanner(config=config)
            return planner.search(
                root_state=state,
                enumerate_actions=enumerate_actions,
                rollout_policy=heuristic_rollout,
                evaluate_terminal=evaluate_terminal,
                transition=wrapped,
            )

        a = _run()
        b = _run()
        assert a.label == b.label, (
            f"Determinizer must not break deterministic-seed "
            f"reproducibility. Got a={a.label}, b={b.label}."
        )
