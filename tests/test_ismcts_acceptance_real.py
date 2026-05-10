"""Phase 5 — Real ISMCTS acceptance gate using the A/B harness.

Replaces the placeholder ``test_acceptance_gate_no_regressions``
in ``tests/test_ismcts_acceptance.py`` with a real call to
``ai.search.ab_compare.acceptance_gate`` against the 12-fixture
corpus.

Acceptance criteria (per docs/research/2026-05_phase_4a_ismcts_scoping.md):
  - 0 strict heuristic wins (no regressions vs the heuristic
    baseline at equal wall-clock budget)
  - ≥ 4 of 12 strict ISMCTS wins (MCTS strictly dominates the
    heuristic with p_value_proxy < 0.10)

The "heuristic baseline" picker for these synthetic fixtures is
the greedy-1-ply scorer from ``ai/search/snapshot_adapter.py``
(``heuristic_rollout`` applied to the root state). This isn't
the production EVPlayer scorer — it's a placeholder for a
realistic 1-ply baseline. A follow-up PR can wire the real
production scorer.

Skipped without ``ISMCTS_ACCEPTANCE=1`` so CI doesn't block on
the heavier run (~60s total at 12 fixtures × 50 forward sims).

Reference:
- docs/research/2026-05_phase_4a_ismcts_scoping.md
- tests/fixtures/ismcts_acceptance_fixtures.jsonl (12 decisions)
"""
from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from ai.ev_evaluator import EVSnapshot
from ai.search.ab_compare import acceptance_gate
from ai.search.ismcts import SearchConfig
from ai.search.snapshot_adapter import (
    ActionToken,
    apply_action,
    enumerate_actions,
    evaluate_terminal,
    heuristic_rollout,
    make_search_state,
)


FIXTURES_PATH = (
    Path(__file__).parent / "fixtures" / "ismcts_acceptance_fixtures.jsonl"
)


def _load_fixtures():
    rows = []
    with FIXTURES_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _state_factory_for(fixture):
    """Returns a callable that produces a fresh SearchState from
    the fixture each time it's invoked."""
    def _factory():
        snap = EVSnapshot(**fixture["snapshot"])
        actions = [
            ActionToken(
                kind=a["kind"], label=a["label"],
                delta=a["delta"], cost=a.get("cost", 0),
            )
            for a in fixture["available_actions"]
        ]
        return make_search_state(snap, actions)
    return _factory


def _heuristic_picker_factory(fixture):
    """Returns a 1-ply greedy heuristic picker. Mirrors
    ``ai.search.snapshot_adapter.heuristic_rollout`` applied to
    the root state — picks the action that maximizes immediate
    position_value delta."""
    def _picker(state):
        rng = random.Random(fixture.get("id", 0))
        return heuristic_rollout(state, rng)
    return _picker


@pytest.mark.skipif(
    not os.environ.get("ISMCTS_ACCEPTANCE"),
    reason="Set ISMCTS_ACCEPTANCE=1 to run the heavy gate "
           "(~60s for 12 fixtures × 50 forward sims).",
)
def test_ismcts_meets_acceptance_gate():
    """Phase 4A acceptance: ≥ 4 strict ISMCTS wins, 0 strict
    heuristic wins, on the 12-fixture corpus.

    This is the real gate from docs/research/2026-05_phase_4a_
    ismcts_scoping.md, replacing the placeholder in
    test_ismcts_acceptance.py::test_acceptance_gate_no_regressions.
    """
    fixtures = _load_fixtures()
    assert len(fixtures) == 12

    config = SearchConfig(n_rollouts=500, rollout_depth=2, seed=42)
    report = acceptance_gate(
        fixtures=fixtures,
        ismcts_config=config,
        heuristic_picker_factory=_heuristic_picker_factory,
        enumerate_actions=enumerate_actions,
        rollout_policy=heuristic_rollout,
        evaluate_terminal=evaluate_terminal,
        transition=apply_action,
        state_factory_for=_state_factory_for,
        n_rollouts=20,
        sim_depth=2,
    )

    # Print the per-fixture breakdown for diagnostic visibility.
    print(f"\nAcceptance gate report:")
    print(f"  ISMCTS strict wins   : {report['ismcts_strict_wins']}")
    print(f"  Heuristic strict wins: {report['heuristic_strict_wins']}")
    print(f"  Ties                 : {report['ties']}")
    print(f"  Passed               : {report['passed']}")
    for i, (fix, result) in enumerate(zip(fixtures, report['results'])):
        marker = "✓" if result.ismcts_strict_win else (
            "✗" if result.heuristic_strict_win else "="
        )
        print(f"    {marker} {fix['id']:2d} {fix['label']}: "
              f"H={result.heuristic_action.label!r}, "
              f"M={result.ismcts_action.label!r}, "
              f"Δ={result.mean_diff:+.2f}, p={result.p_value_proxy:.3f}")

    # The gate's two acceptance criteria.
    assert report['heuristic_strict_wins'] == 0, (
        f"Found {report['heuristic_strict_wins']} strict heuristic "
        f"win(s) — ISMCTS is regressing on these fixtures."
    )
    assert report['ismcts_strict_wins'] >= 4, (
        f"Only {report['ismcts_strict_wins']} of 12 strict ISMCTS "
        f"wins — below the 4-win acceptance threshold."
    )


# ─── Tier 1: pickers wire correctly ──────────────────────────────────


def test_state_factory_returns_fresh_state():
    """Each call to the factory must return a fresh SearchState
    — the A/B harness depends on this for clean re-use."""
    fixtures = _load_fixtures()
    factory = _state_factory_for(fixtures[0])
    a = factory()
    b = factory()
    assert a is not b
    assert a.snapshot.my_life == b.snapshot.my_life


def test_heuristic_picker_returns_action_token():
    """The heuristic picker must return an ActionToken instance —
    type-compatible with ISMCTS's pick output for a fair A/B
    comparison."""
    fixtures = _load_fixtures()
    fixture = fixtures[0]
    picker = _heuristic_picker_factory(fixture)
    state = _state_factory_for(fixture)()
    pick = picker(state)
    assert isinstance(pick, ActionToken)
