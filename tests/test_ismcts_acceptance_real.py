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

Two heuristic-baseline variants are wired:

  1. ``test_ismcts_meets_acceptance_gate`` — uses the synthetic
     1-ply scorer (``snapshot_adapter.heuristic_rollout``) which
     scores by ``position_value(after-state)`` directly. Kept for
     historical comparability with the Phase-4A scoping target.

  2. ``test_ismcts_meets_acceptance_gate_production_baseline`` —
     uses the production-style scorer adapter
     (``ai/search/evplayer_scorer_adapter.py``) which mirrors the
     value-delta formulation (``after − before``) used by
     ``ai.ev_evaluator.compute_play_ev``. This is the
     apples-to-apples baseline the acceptance gate is meant to
     compare ISMCTS against (Phase 5 step 1).

Both gate tests are skipped without ``ISMCTS_ACCEPTANCE=1`` so CI
doesn't block on the heavier run (~60s total at 12 fixtures × 50
forward sims).

Reference:
- docs/research/2026-05_phase_4a_ismcts_scoping.md
- docs/handoff/2026-05_session_summary.md § "Phase 5 — production
  scorer wiring"
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
from ai.search.cardinstance_proxy import (
    make_full_production_picker,
)
from ai.search.evplayer_scorer_adapter import (
    make_production_picker,
    production_scorer_picker,
)
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


# ─── Phase 5 — production scorer baseline ────────────────────────────


def _production_picker_factory(fixture):
    """Returns the FULL-production-scorer picker for the fixture.

    Routes the snapshot through ``ai.ev_evaluator.compute_play_ev``
    end-to-end via the CardInstance / GameState proxy in
    ``ai/search/cardinstance_proxy.py`` (Phase 5 step 2). This
    exercises BHI counter / removal probability, oracle-text-driven
    deferral, goal-engine state, AND the combo-chain assessment —
    not just the value-delta backbone the thin adapter
    (``evplayer_scorer_adapter.py``) covers.

    For the thin-adapter baseline kept for historical comparability
    see ``_thin_production_picker_factory`` below.
    """
    archetype = fixture.get("archetype")  # may be None on most fixtures
    return make_full_production_picker(archetype=archetype)


def _thin_production_picker_factory(fixture):
    """Historical baseline — the PR #367 thin adapter. Retained so the
    production-baseline gate can be re-run against the thin scorer for
    bisection if the full proxy result diverges sharply."""
    archetype = fixture.get("archetype")
    return make_production_picker(archetype=archetype)


def test_production_picker_returns_action_token_on_every_fixture():
    """Smoke test for the production scorer adapter against all 12
    corpus fixtures. Type-compatibility with ISMCTS's output is the
    hard requirement; this test fails red if the adapter ever stops
    returning ``ActionToken``."""
    fixtures = _load_fixtures()
    for fixture in fixtures:
        picker = _production_picker_factory(fixture)
        state = _state_factory_for(fixture)()
        pick = picker(state)
        assert isinstance(pick, ActionToken), (
            f"Fixture {fixture['id']} ({fixture['label']}): "
            f"production picker returned {type(pick).__name__}, "
            f"expected ActionToken."
        )


def test_production_picker_picks_legal_action_on_every_fixture():
    """The production picker must pick from the enumerated legal
    set on every fixture — otherwise ``apply_action`` downstream in
    the A/B harness would mis-step.

    Note: ``enumerate_actions`` always appends a 'pass turn' token,
    so legal_labels includes that synthetic option."""
    fixtures = _load_fixtures()
    for fixture in fixtures:
        picker = _production_picker_factory(fixture)
        state = _state_factory_for(fixture)()
        legal_labels = {a.label for a in enumerate_actions(state)}
        pick = picker(state)
        assert pick.label in legal_labels, (
            f"Fixture {fixture['id']} ({fixture['label']}): "
            f"production picker chose {pick.label!r}, not in legal "
            f"set {legal_labels}."
        )


@pytest.mark.skipif(
    not os.environ.get("ISMCTS_ACCEPTANCE"),
    reason="Set ISMCTS_ACCEPTANCE=1 to run the heavy gate "
           "(~60s for 12 fixtures × 50 forward sims).",
)
def test_ismcts_meets_acceptance_gate_production_baseline():
    """Phase 5 step 2 — the apples-to-apples acceptance gate.

    Routes the snapshot through the FULL production ``compute_play_ev``
    via the CardInstance / GameState proxy in
    ``ai/search/cardinstance_proxy.py``. This exercises BHI counter /
    removal probability, oracle-text-driven deferral, goal-engine
    state, and the combo-chain assessment — the four production
    signals the thin adapter (``evplayer_scorer_adapter.py``, PR
    #367) could not reach because the snapshot-only fixtures lacked
    ``CardInstance`` + ``GameState``.

    Closes the blocker note recorded in
    ``docs/handoff/2026-05_session_summary.md`` § "Phase 5 —
    production scorer wiring".

    Acceptance criteria are unchanged from the synthetic gate:
      - 0 strict heuristic wins
      - ≥ 4 of 12 strict ISMCTS wins
    """
    fixtures = _load_fixtures()
    assert len(fixtures) == 12

    config = SearchConfig(n_rollouts=500, rollout_depth=2, seed=42)
    report = acceptance_gate(
        fixtures=fixtures,
        ismcts_config=config,
        heuristic_picker_factory=_production_picker_factory,
        enumerate_actions=enumerate_actions,
        rollout_policy=heuristic_rollout,
        evaluate_terminal=evaluate_terminal,
        transition=apply_action,
        state_factory_for=_state_factory_for,
        n_rollouts=20,
        sim_depth=2,
    )

    print(f"\nProduction-baseline acceptance gate report:")
    print(f"  ISMCTS strict wins   : {report['ismcts_strict_wins']}")
    print(f"  Heuristic strict wins: {report['heuristic_strict_wins']}")
    print(f"  Ties                 : {report['ties']}")
    print(f"  Passed               : {report['passed']}")
    for fix, result in zip(fixtures, report['results']):
        marker = "✓" if result.ismcts_strict_win else (
            "✗" if result.heuristic_strict_win else "="
        )
        print(f"    {marker} {fix['id']:2d} {fix['label']}: "
              f"H={result.heuristic_action.label!r}, "
              f"M={result.ismcts_action.label!r}, "
              f"Δ={result.mean_diff:+.2f}, p={result.p_value_proxy:.3f}")

    assert report['heuristic_strict_wins'] == 0, (
        f"Found {report['heuristic_strict_wins']} strict heuristic "
        f"win(s) under the production baseline — ISMCTS is "
        f"regressing on these fixtures. The production baseline is "
        f"a stronger heuristic than the synthetic 1-ply scorer; if "
        f"the apples-to-apples gate flips heuristic-favourable, "
        f"that's signal worth investigating."
    )
    assert report['ismcts_strict_wins'] >= 4, (
        f"Only {report['ismcts_strict_wins']} of 12 strict ISMCTS "
        f"wins under the production baseline — below the 4-win "
        f"acceptance threshold."
    )
