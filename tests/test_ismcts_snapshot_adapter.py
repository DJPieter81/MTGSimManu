"""Phase 4A Week 2 — ISMCTS on EVSnapshot integration smoke tests.

Verifies that the Week-1 ISMCTS skeleton runs against the Week-2
snapshot adapter and produces sensible action choices on small
synthetic boards.

These are L1 fixtures per the Phase 1+2 verification protocol:
pre-built game state, exact mechanical assertion, sub-second
wall clock. No matrix runs.

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import EVSnapshot
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


# ─── Adapter unit tests ──────────────────────────────────────────────


class TestEnumerateActions:
    def test_pass_always_available(self):
        snap = EVSnapshot(my_life=20, opp_life=20)
        state = make_search_state(snap, available_actions=[])
        actions = enumerate_actions(state)
        assert any(a.kind == "pass" for a in actions)

    def test_filters_unaffordable_actions(self):
        snap = EVSnapshot(my_life=20, opp_life=20, my_mana=1)
        expensive = ActionToken(
            kind="cast_creature", label="Big creature",
            delta={"my_power": 5, "my_toughness": 5,
                   "my_creature_count": 1}, cost=4,
        )
        cheap = ActionToken(
            kind="cast_creature", label="Memnite",
            delta={"my_power": 1, "my_toughness": 1,
                   "my_creature_count": 1}, cost=0,
        )
        state = make_search_state(snap, [expensive, cheap])
        actions = enumerate_actions(state)
        labels = {a.label for a in actions}
        assert "Memnite" in labels
        assert "Big creature" not in labels  # 1 mana < 4
        assert "pass turn" in labels

    def test_one_land_per_turn(self):
        snap = EVSnapshot(my_life=20, opp_life=20)
        land1 = ActionToken(
            kind="play_land", label="Mountain 1",
            delta={"my_mana": 1, "my_total_lands": 1}, cost=0,
        )
        land2 = ActionToken(
            kind="play_land", label="Mountain 2",
            delta={"my_mana": 1, "my_total_lands": 1}, cost=0,
        )
        state = make_search_state(snap, [land1, land2])
        # Apply land1 first.
        rng = random.Random(0)
        state2 = apply_action(state, land1, rng)
        # Now enumerate — only one of the lands should remain (it
        # was popped from `available`), but even if both were
        # there, the per-turn cap should filter it out.
        actions = enumerate_actions(state2)
        assert not any(a.kind == "play_land" for a in actions), (
            "Second land must be filtered out by per-turn cap"
        )


class TestApplyAction:
    def test_pass_advances_turn_and_deals_combat_damage(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20, my_power=5,
            my_total_lands=2, my_mana=2, turn_number=3,
        )
        state = make_search_state(snap)
        next_state = apply_action(
            state, ActionToken(kind="pass", label="pass"),
            random.Random(0),
        )
        assert next_state.snapshot.opp_life == 15  # 20 - 5
        assert next_state.snapshot.my_mana == 2    # untapped lands
        assert next_state.snapshot.turn_number == 4
        assert next_state.plays_this_turn == []

    def test_cast_applies_delta_and_deducts_cost(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20, my_mana=4,
            my_creature_count=0, my_power=0,
        )
        state = make_search_state(snap)
        bear = ActionToken(
            kind="cast_creature", label="Grizzly Bears",
            delta={"my_power": 2, "my_toughness": 2,
                   "my_creature_count": 1}, cost=2,
        )
        next_state = apply_action(state, bear, random.Random(0))
        assert next_state.snapshot.my_mana == 2  # 4 - 2
        assert next_state.snapshot.my_power == 2
        assert next_state.snapshot.my_creature_count == 1
        assert bear in next_state.plays_this_turn


class TestEvaluateTerminal:
    def test_opp_dead_returns_one(self):
        snap = EVSnapshot(my_life=10, opp_life=0)
        state = make_search_state(snap)
        assert evaluate_terminal(state) == 1.0

    def test_me_dead_returns_zero(self):
        snap = EVSnapshot(my_life=0, opp_life=10)
        state = make_search_state(snap)
        assert evaluate_terminal(state) == 0.0

    def test_returns_in_unit_interval(self):
        snap = EVSnapshot(my_life=20, opp_life=20)
        state = make_search_state(snap)
        v = evaluate_terminal(state)
        assert 0.0 <= v <= 1.0


# ─── End-to-end ISMCTS on a synthetic Affinity-like board ────────────


class TestISMCTSEndToEnd:
    """Synthetic 'Affinity-style' decision: the player has 1 mana
    and a choice between a 1/1 Memnite (free), a Mox Opal (free),
    or passing the turn. With 0 power on board, casting Memnite
    is the highest-EV opener (passing yields 0 damage, Mox does
    nothing observable on this synthetic state).

    ISMCTS at 200 rollouts must pick a non-pass action."""

    def test_search_does_not_just_pass(self):
        snap = EVSnapshot(
            my_life=20, opp_life=20,
            my_mana=1, my_total_lands=1, my_power=0,
            my_creature_count=0, turn_number=2,
        )
        memnite = ActionToken(
            kind="cast_creature", label="Memnite",
            delta={"my_power": 1, "my_toughness": 1,
                   "my_creature_count": 1,
                   "my_artifact_count": 1}, cost=0,
        )
        mox = ActionToken(
            kind="cast_artifact", label="Mox Opal",
            delta={"my_artifact_count": 1, "my_mana": 1}, cost=0,
        )
        state = make_search_state(snap, [memnite, mox])

        config = SearchConfig(n_rollouts=200, rollout_depth=3, seed=42)
        planner = ISMCTSPlanner(config=config)
        chosen = planner.search(
            root_state=state,
            enumerate_actions=enumerate_actions,
            rollout_policy=heuristic_rollout,
            evaluate_terminal=evaluate_terminal,
            transition=apply_action,
        )
        assert chosen.kind != "pass", (
            f"ISMCTS shouldn't pass when free creatures are "
            f"available. Picked: {chosen.label}."
        )

    def test_search_prefers_lethal_burn_over_creature(self):
        """Synthetic: opponent at 1 life. A 1-damage burn for cost
        0 wins immediately. A 1/1 creature for cost 0 doesn't
        (still needs a turn to attack). ISMCTS must pick burn.

        rollout_depth=0 forces the search to compare immediate
        terminal evaluations rather than 1-turn projections, where
        both paths converge to a win within the rollout window.
        """
        snap = EVSnapshot(
            my_life=20, opp_life=1,
            my_mana=1, my_total_lands=1, my_power=0,
            turn_number=4,
        )
        burn = ActionToken(
            kind="burn", label="Lightning Bolt",
            delta={"opp_life": -1}, cost=0,
        )
        creature = ActionToken(
            kind="cast_creature", label="Memnite",
            delta={"my_power": 1, "my_toughness": 1,
                   "my_creature_count": 1}, cost=0,
        )
        state = make_search_state(snap, [burn, creature])

        config = SearchConfig(n_rollouts=200, rollout_depth=0, seed=7)
        planner = ISMCTSPlanner(config=config)
        chosen = planner.search(
            root_state=state,
            enumerate_actions=enumerate_actions,
            rollout_policy=heuristic_rollout,
            evaluate_terminal=evaluate_terminal,
            transition=apply_action,
        )
        assert chosen.label == "Lightning Bolt", (
            f"ISMCTS must take lethal burn at opp_life=1. "
            f"Picked: {chosen.label}."
        )


def test_search_is_deterministic_with_snapshot_adapter():
    """Same SearchConfig.seed must produce same answer when wired
    through the snapshot adapter. Required for matrix
    reproducibility when ISMCTS is opt-in."""
    snap = EVSnapshot(
        my_life=20, opp_life=10,
        my_mana=2, my_total_lands=2, my_power=2,
        turn_number=5,
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

    def _run():
        state = make_search_state(snap, [bolt, creature])
        config = SearchConfig(n_rollouts=300, rollout_depth=2, seed=99)
        planner = ISMCTSPlanner(config=config)
        return planner.search(
            root_state=state,
            enumerate_actions=enumerate_actions,
            rollout_policy=heuristic_rollout,
            evaluate_terminal=evaluate_terminal,
            transition=apply_action,
        )

    a = _run()
    b = _run()
    assert a.label == b.label, (
        f"Deterministic search expected matching outputs. Got "
        f"a={a.label}, b={b.label}."
    )
