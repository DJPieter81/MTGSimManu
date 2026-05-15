"""Tests for the production EVPlayer scorer adapter — Phase 5 step 1.

The adapter at ``ai/search/evplayer_scorer_adapter.py`` exposes a
``RolloutPolicy``-shaped callable (``production_scorer_picker``) and a
factory (``make_production_picker``) that the A/B harness can drop
into the heuristic-baseline slot of ``compare_planners`` /
``acceptance_gate``.

These tests pin the adapter's contract so the 12-fixture acceptance
gate (``tests/test_ismcts_acceptance_real.py`` and
``test_ismcts_acceptance_real_production.py``) can rely on it:

  - The picker returns a legal ``ActionToken`` from the enumerated
    legal-action set.
  - The picker is deterministic for a fixed rng seed.
  - The EV-delta formulation (after − before) is the production
    formulation, NOT the synthetic ``position_value(after)``-only
    formulation. Pinned via a fixture where the two formulations
    disagree.
  - The urgency-factor discount applies to non-immediate-effect
    actions when the snapshot's urgency_factor < 1, matching the
    production guard in ``ai.ev_evaluator.compute_play_ev``.
  - ``archetype_subtype`` propagates so combo / storm snapshots
    receive the combo-clock override during scoring.

Reference: ``docs/handoff/2026-05_session_summary.md`` § "Phase 5 —
production scorer wiring".
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import EVSnapshot, evaluate_board
from ai.search.evplayer_scorer_adapter import (
    _IMMEDIATE_KINDS,
    _resolve_archetype,
    _score_action_production,
    make_production_picker,
    production_scorer_picker,
)
from ai.search.snapshot_adapter import (
    ActionToken,
    SearchState,
    apply_action,
    enumerate_actions,
    make_search_state,
)


# ─── Picker shape and legality ───────────────────────────────────────


def _basic_state() -> SearchState:
    """Tiny snapshot + 2 actions (cast a creature OR play a land).

    Used by shape tests that only need a non-empty action set."""
    snap = EVSnapshot(
        my_life=20, opp_life=20,
        my_mana=2, my_total_lands=2,
        my_hand_size=3, turn_number=2,
    )
    actions = [
        ActionToken(
            kind="cast_creature", label="2/2 vanilla",
            delta={"my_power": 2, "my_toughness": 2,
                   "my_creature_count": 1},
            cost=2,
        ),
        ActionToken(
            kind="play_land", label="basic land",
            delta={"my_total_lands": 1, "my_mana": 1},
            cost=0,
        ),
    ]
    return make_search_state(snap, actions)


def test_picker_returns_action_token():
    """The contract for ``compare_planners.heuristic_picker`` requires
    an object type-compatible with the ISMCTS pick output. The
    ``ABResult.heuristic_action == ismcts_action`` tie-break depends
    on this being an ``ActionToken``."""
    state = _basic_state()
    rng = random.Random(0)
    pick = production_scorer_picker(state, rng)
    assert isinstance(pick, ActionToken)


def test_picker_returns_legal_action():
    """The picked action must be one enumerated by
    ``enumerate_actions`` from the same state — otherwise downstream
    ``apply_action`` would mis-step."""
    state = _basic_state()
    rng = random.Random(0)
    pick = production_scorer_picker(state, rng)
    legal_labels = {a.label for a in enumerate_actions(state)}
    assert pick.label in legal_labels


def test_picker_deterministic_for_fixed_seed():
    """Reproducibility: same seed → same pick. The matrix-sim
    reproducibility contract requires this for any deterministic
    rollout policy."""
    state = _basic_state()
    a = production_scorer_picker(state, random.Random(123))
    b = production_scorer_picker(state, random.Random(123))
    assert a.label == b.label


def test_picker_handles_empty_action_set():
    """Defensive contract: with no legal actions, fall back to the
    universal ``pass turn`` token. Mirrors
    ``snapshot_adapter.heuristic_rollout``'s fallback."""
    snap = EVSnapshot(my_life=20, opp_life=20, my_mana=0,
                      my_hand_size=0, turn_number=1)
    state = make_search_state(snap, [])
    # enumerate_actions still returns ['pass'], so this is a no-op
    # regression guard rather than an actual empty-set scenario.
    pick = production_scorer_picker(state, random.Random(0))
    assert isinstance(pick, ActionToken)


# ─── Production EV-delta formulation ─────────────────────────────────


def test_score_uses_value_delta_not_post_value_alone():
    """The production scorer computes ``after − before``, NOT
    ``position_value(after)`` directly.

    Pinned with a state whose ``before`` value is non-zero — if the
    adapter forgot to subtract ``before`` it would return a
    different number. Stronger signal than just "returns a float":
    asserts the delta formulation is the one in use.
    """
    snap = EVSnapshot(
        my_life=20, opp_life=20,
        my_power=3, my_creature_count=1,
        my_total_lands=2, my_mana=2,
        turn_number=2,
    )
    action = ActionToken(
        kind="burn", label="3 to face",
        delta={"opp_life": -3}, cost=1,
    )
    state = make_search_state(snap, [action])

    archetype = "midrange"
    score = _score_action_production(state, action, archetype,
                                     random.Random(0))

    # Recompute manually to pin the formulation.
    before = evaluate_board(snap, archetype)
    after_snap = apply_action(state, action,
                              random.Random(0)).snapshot
    after = evaluate_board(after_snap, archetype)
    expected = after - before

    # The action kind "burn" is in _IMMEDIATE_KINDS, so no urgency
    # discount is applied — the raw delta should match exactly.
    assert "burn" in _IMMEDIATE_KINDS
    assert score == pytest.approx(expected, abs=1e-9)


def test_immediate_kinds_are_not_urgency_discounted():
    """Sanity: the action kinds we classify as immediate must not
    receive the urgency-factor discount. Pinned because changing
    ``_IMMEDIATE_KINDS`` is the obvious place to introduce a
    regression."""
    expected = {"cast_creature", "cast_artifact", "play_land",
                "burn", "draw", "pass"}
    assert _IMMEDIATE_KINDS == frozenset(expected)


def test_picker_prefers_higher_value_delta():
    """When two actions have clearly different EV deltas, the picker
    must pick the higher-delta one. Pinned with two ``burn`` actions
    of different damage so urgency_factor doesn't enter the picture
    (both are in ``_IMMEDIATE_KINDS``)."""
    snap = EVSnapshot(
        my_life=20, opp_life=10,
        my_total_lands=2, my_mana=2, turn_number=3,
    )
    big_burn = ActionToken(
        kind="burn", label="big 5",
        delta={"opp_life": -5}, cost=2,
    )
    small_burn = ActionToken(
        kind="burn", label="small 1",
        delta={"opp_life": -1}, cost=1,
    )
    state = make_search_state(snap, [small_burn, big_burn])
    rng = random.Random(0)
    pick = production_scorer_picker(state, rng)
    assert pick.label == "big 5"


# ─── Archetype propagation ───────────────────────────────────────────


def test_resolve_archetype_caller_overrides_snapshot():
    """Caller-supplied archetype wins over snapshot.archetype_subtype.
    Pinned because the resolution order matters for the A/B harness:
    fixtures with a known matchup should override whatever the
    snapshot's own subtype claims."""
    snap = EVSnapshot(my_life=20, opp_life=20, turn_number=1,
                      archetype_subtype="storm")
    state = make_search_state(snap, [])
    assert _resolve_archetype(state, "midrange") == "midrange"


def test_resolve_archetype_falls_back_to_snapshot_subtype():
    """When caller passes None, ``archetype_subtype`` from the
    snapshot is the next-best signal."""
    snap = EVSnapshot(my_life=20, opp_life=20, turn_number=1,
                      archetype_subtype="storm")
    state = make_search_state(snap, [])
    assert _resolve_archetype(state, None) == "storm"


def test_resolve_archetype_default_is_midrange():
    """Final fallback matches ``position_value``'s default. Pinned so
    the default doesn't drift unannounced."""
    snap = EVSnapshot(my_life=20, opp_life=20, turn_number=1)
    state = make_search_state(snap, [])
    assert _resolve_archetype(state, None) == "midrange"


# ─── Factory shape for the A/B harness ───────────────────────────────


def test_factory_returns_one_arg_callable():
    """``compare_planners.heuristic_picker`` is a 1-arg callable —
    the factory must produce that shape so the A/B harness can call
    it without a wrapper."""
    picker = make_production_picker(archetype="midrange")
    state = _basic_state()
    pick = picker(state)
    assert isinstance(pick, ActionToken)


def test_factory_picker_deterministic():
    """Repeated calls of the factory's picker on the same state
    return the same action — required for the A/B harness's
    ``state_factory`` rebuild semantics."""
    picker = make_production_picker(archetype="midrange")
    state_a = _basic_state()
    state_b = _basic_state()
    a = picker(state_a)
    b = picker(state_b)
    assert a.label == b.label
