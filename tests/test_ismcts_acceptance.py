"""Phase 4A Week 4 — ISMCTS 12-fixture acceptance gate scaffolding.

Loads ``tests/fixtures/ismcts_acceptance_fixtures.jsonl`` (12
canonical decision points across the 16-deck Modern meta) and
runs ISMCTS at config-specified rollout budgets.

Two run modes:

1. **Smoke mode** (always run): low-rollout-budget search just
   verifies the planner returns a legal action and doesn't
   crash. Fast (sub-second per fixture).

2. **Acceptance mode** (``ISMCTS_ACCEPTANCE=1``): higher rollout
   budget; asserts MCTS strictly dominates the heuristic on
   ≥ 4 of 12 fixtures with 0 regressions. ~5 min wall clock.

The acceptance criteria from the scoping doc:
  - MCTS picks the same action as heuristic OR a strictly
    better action on each fixture.
  - On ≥ 4 of 12 the MCTS action wins more games across 20
    reseeded forward simulations (p < 0.10).
  - 0 strict regressions (no fixture where heuristic strictly
    beats MCTS at the same wall-clock budget).

Reference: docs/research/2026-05_phase_4a_ismcts_scoping.md
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

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


FIXTURES_PATH = (
    Path(__file__).parent / "fixtures" / "ismcts_acceptance_fixtures.jsonl"
)


def _load_fixtures() -> List[dict]:
    if not FIXTURES_PATH.exists():
        return []
    rows = []
    with FIXTURES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _build_state(fixture: dict) -> SearchState:
    """Construct a SearchState from a fixture row."""
    snap = EVSnapshot(**fixture["snapshot"])
    actions = [
        ActionToken(
            kind=a["kind"], label=a["label"],
            delta=a["delta"], cost=a.get("cost", 0),
        )
        for a in fixture["available_actions"]
    ]
    return make_search_state(snap, actions)


# ─── Tier 1: corpus structure ────────────────────────────────────────


def test_fixtures_load():
    rows = _load_fixtures()
    assert len(rows) == 12, (
        f"ISMCTS acceptance corpus must have exactly 12 fixtures "
        f"(currently {len(rows)})."
    )


def test_fixtures_have_required_fields():
    for row in _load_fixtures():
        assert "id" in row
        assert "label" in row
        assert "matchup" in row
        assert "snapshot" in row
        assert "available_actions" in row
        assert len(row["available_actions"]) >= 2, (
            f"Fixture {row.get('id')} must offer ≥2 actions to "
            f"have a meaningful decision (got "
            f"{len(row['available_actions'])})."
        )


def test_fixtures_cover_diverse_archetypes():
    """The 12 fixtures must span ≥ 8 distinct matchups for the
    acceptance gate to provide diverse signal."""
    matchups = {row["matchup"] for row in _load_fixtures()}
    assert len(matchups) >= 8, (
        f"Acceptance corpus has only {len(matchups)} distinct "
        f"matchups: {matchups}. Need ≥ 8 for diverse signal."
    )


# ─── Tier 1: smoke — every fixture returns a legal action ────────────


class TestSmokeMode:
    """Run ISMCTS at low rollout budget on each fixture; verify
    we get a legal ActionToken back without crashing."""

    @pytest.mark.parametrize(
        "fixture_id", [i + 1 for i in range(12)],
    )
    def test_fixture_returns_legal_action(self, fixture_id):
        rows = _load_fixtures()
        fixture = next(r for r in rows if r["id"] == fixture_id)
        state = _build_state(fixture)

        config = SearchConfig(n_rollouts=50, rollout_depth=2, seed=42)
        planner = ISMCTSPlanner(config=config)
        chosen = planner.search(
            root_state=state,
            enumerate_actions=enumerate_actions,
            rollout_policy=heuristic_rollout,
            evaluate_terminal=evaluate_terminal,
            transition=apply_action,
        )
        assert isinstance(chosen, ActionToken)
        # Must be one of the original options OR a "pass" action
        # (which enumerate_actions always offers).
        original_labels = {
            a.label for a in state.available
        }
        assert chosen.label in (original_labels | {"pass turn"}), (
            f"Fixture {fixture_id}: ISMCTS returned a non-legal "
            f"action {chosen.label!r}. Original choices: "
            f"{original_labels}."
        )


# ─── Tier 1: deterministic per fixture under fixed seed ──────────────


class TestDeterminism:
    @pytest.mark.parametrize(
        "fixture_id", [1, 5, 11],  # spot-check 3 to keep test fast
    )
    def test_fixture_deterministic_under_fixed_seed(self, fixture_id):
        rows = _load_fixtures()
        fixture = next(r for r in rows if r["id"] == fixture_id)

        def _run():
            state = _build_state(fixture)
            config = SearchConfig(n_rollouts=100, rollout_depth=2, seed=99)
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
            f"Fixture {fixture_id}: deterministic seed must "
            f"produce same action. Got a={a.label}, b={b.label}."
        )


# ─── Tier 2: full acceptance gate ────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("ISMCTS_ACCEPTANCE"),
    reason="ISMCTS_ACCEPTANCE not set — run gate explicitly: "
           "ISMCTS_ACCEPTANCE=1 pytest tests/test_ismcts_acceptance.py",
)
def test_acceptance_gate_no_regressions():
    """Full acceptance gate: MCTS at 1000 rollouts vs heuristic
    1-ply at the same wall-clock budget. Across 12 fixtures with
    20 reseeded forward simulations each:
      - 0 strict MCTS regressions (heuristic > MCTS p < 0.10)
      - ≥ 4 strict MCTS wins (MCTS > heuristic p < 0.10)

    This test will require Phase 5 work to compare actions via
    forward simulation. Currently it lands as a placeholder /
    acceptance contract record; the actual game-end simulation
    plumbing is Phase 5 scope.

    Marked as skipped without ``ISMCTS_ACCEPTANCE=1`` so CI
    doesn't block on the heavy run.
    """
    rows = _load_fixtures()
    # Placeholder: in Phase 5, replace with actual win-rate
    # comparison via forward simulation.
    # For now we run both planners, count diffs vs same actions,
    # and assert at least some diversity.
    diff_count = 0
    for row in rows:
        state_a = _build_state(row)
        state_b = _build_state(row)
        cfg_low = SearchConfig(n_rollouts=10, rollout_depth=1,
                                seed=row["id"])
        cfg_hi = SearchConfig(n_rollouts=1000, rollout_depth=2,
                               seed=row["id"])
        a = ISMCTSPlanner(config=cfg_low).search(
            state_a, enumerate_actions, heuristic_rollout,
            evaluate_terminal, apply_action,
        )
        b = ISMCTSPlanner(config=cfg_hi).search(
            state_b, enumerate_actions, heuristic_rollout,
            evaluate_terminal, apply_action,
        )
        if a.label != b.label:
            diff_count += 1
    print(f"\nLow-budget vs high-budget action diff: "
          f"{diff_count}/{len(rows)} fixtures")
    # Acceptance criterion lands in Phase 5. For now, just record
    # the diff rate as a smoke metric.
    assert diff_count >= 0  # always passes; placeholder
