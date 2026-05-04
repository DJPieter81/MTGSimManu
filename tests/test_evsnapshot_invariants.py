"""Construction-time invariant tests for EVSnapshot (Phase J-1).

Phase J-1 of the pydantic-first AI engine refactor migrated EVSnapshot
from a plain ``@dataclass`` to a strict pydantic v2 ``BaseModel``.
These tests verify the structural-prevention contract:

  1. Typo'd field names raise ``ValidationError`` at construction.
     The PR-L1 bug class (state-drift between snapshot and backing
     game state) becomes impossible-by-construction once the kwargs
     surface is locked down.
  2. Counts that are structurally non-negative (lands, artifact /
     enchantment counts) raise when constructed with negative values.
  3. ``turn_number`` cannot be < 1 (Magic rules: turn numbering
     starts at 1).
  4. ``replace`` validates; ``fast_replace`` skips validation.
  5. JSON round-tripping is loss-free for the field surface that
     ``model_dump`` covers.

These tests do NOT exercise the WR pipeline or scoring math — those
are covered by the existing matchup smoke tests in ``test_ev_system``,
``test_clock``, and the per-deck test suites.  This file's contract is
narrowly the snapshot's construction-time invariants.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.ev_evaluator import EVSnapshot, snapshot_from_game


# ─── Construction-success cases ──────────────────────────────────────


def test_construction_with_valid_state_succeeds():
    """The default constructor with no overrides is the simplest valid
    state — no field violates its invariant.  Used everywhere as a
    baseline (``_DEFAULT_SNAP``, fixture defaults)."""
    snap = EVSnapshot()
    assert snap.my_life > 0
    assert snap.opp_life > 0
    assert snap.turn_number >= 1


def test_construction_with_typical_mid_game_state_succeeds():
    """Mirrors the ``snapshot_from_game`` output shape: positive life,
    a few creatures on each side, mid-curve mana.  No invariant is
    triggered; construction goes through validators cleanly."""
    snap = EVSnapshot(
        my_life=14, opp_life=11,
        my_power=5, opp_power=4,
        my_creature_count=2, opp_creature_count=2,
        my_total_lands=4, opp_total_lands=4,
        turn_number=4,
    )
    assert snap.my_power == 5
    assert snap.turn_number == 4


# ─── extra="forbid" — typo'd kwargs rejected ─────────────────────────


def test_extra_field_raises_validation_error():
    """Structural-prevention proof: a typo'd kwarg raises at
    construction instead of silently building a default-valued
    snapshot that scoring code reads incorrectly downstream.

    This is the PR-L1 bug class made impossible: the bug there was a
    boundary error between game state and snapshot count fields, and
    a hypothetical typo'd kwarg version (``my_artifct_count=`` for
    ``my_artifact_count=``) would have failed silently in the
    dataclass world and been invisible to review."""
    with pytest.raises(ValidationError) as exc_info:
        EVSnapshot(my_lfie=20)  # typo of my_life
    assert "my_lfie" in str(exc_info.value) or "extra" in str(exc_info.value).lower()


def test_extra_field_raises_even_with_valid_kwargs_alongside():
    """Same protection when the typo is mixed with valid kwargs —
    pydantic catches the unknown key and rejects the whole call."""
    with pytest.raises(ValidationError):
        EVSnapshot(my_life=20, my_artifct_count=3)  # typo


# ─── Negative-count invariant ────────────────────────────────────────


def test_negative_artifact_count_raises():
    """``my_artifact_count`` reads a board-state count.  A negative
    value indicates state drift between the snapshot and the backing
    battlefield list (PR-L1 was a positive-direction drift but the
    same boundary)."""
    with pytest.raises(ValidationError):
        EVSnapshot(my_artifact_count=-1)


def test_negative_enchantment_count_raises():
    """Symmetric to the artifact-count check."""
    with pytest.raises(ValidationError):
        EVSnapshot(opp_enchantment_count=-3)


def test_negative_total_lands_raises():
    """Total lands is a board-state count: lands cannot be < 0."""
    with pytest.raises(ValidationError):
        EVSnapshot(my_total_lands=-2)


def test_turn_number_zero_raises():
    """Magic rules: turn numbering starts at 1."""
    with pytest.raises(ValidationError):
        EVSnapshot(turn_number=0)


def test_turn_number_negative_raises():
    """Same floor enforced for negative values."""
    with pytest.raises(ValidationError):
        EVSnapshot(turn_number=-3)


def test_negative_life_is_admissible():
    """Magic rules: a player can be at < 0 life on the stack before
    state-based actions resolve.  This is intentionally NOT in the
    non-negative-count invariant set."""
    snap = EVSnapshot(my_life=-2)
    assert snap.my_life == -2


# ─── Mutation / replacement contract ─────────────────────────────────


def test_replace_returns_new_instance_with_overrides():
    """``replace`` is a validated copy.  The original is unchanged;
    the copy has the overrides applied."""
    snap = EVSnapshot(my_life=20)
    snap2 = snap.replace(my_life=10)
    assert snap.my_life == 20
    assert snap2.my_life == 10
    assert snap is not snap2


def test_replace_validates_overrides():
    """``replace`` runs the post-validator.  Negative artifact count
    in the override is rejected the same way it would be at
    construction."""
    snap = EVSnapshot(my_artifact_count=2)
    with pytest.raises(ValidationError):
        snap.replace(my_artifact_count=-1)


def test_replace_rejects_typo_in_override():
    """``replace`` enforces ``extra="forbid"`` on the override surface
    — typo'd field names raise the same as at construction."""
    snap = EVSnapshot(my_life=20)
    with pytest.raises(ValidationError):
        snap.replace(my_lfie=10)


def test_fast_replace_returns_new_instance_with_overrides():
    """``fast_replace`` is the hot-path equivalent — bypasses
    validation, used by the scoring loop."""
    snap = EVSnapshot(my_life=20)
    snap2 = snap.fast_replace(my_life=10)
    assert snap.my_life == 20
    assert snap2.my_life == 10
    assert snap is not snap2


def test_fast_replace_bypasses_validation():
    """``fast_replace`` is the hot-path escape hatch.  Caller is
    responsible for upholding invariants — the function itself does
    no validation, so a wonky update is silently accepted.  Tests
    using ``fast_replace`` to prepare odd snapshots rely on this."""
    snap = EVSnapshot(my_artifact_count=2)
    # This would raise via ``replace``; ``fast_replace`` lets it
    # through.  The behaviour is intentional — see docstring on
    # ``EVSnapshot.fast_replace``.
    snap2 = snap.fast_replace(my_artifact_count=-1)
    assert snap2.my_artifact_count == -1


# ─── JSON round-trip ─────────────────────────────────────────────────


def test_round_trip_via_model_dump_json():
    """Pydantic's standard JSON round-trip should be loss-free over
    the full field surface, modulo the per-color-mana dict which is
    a regular ``Dict[str, int]``."""
    snap = EVSnapshot(
        my_life=14, opp_life=12,
        my_power=4, opp_power=3,
        my_total_lands=4,
        my_mana_by_color={"R": 2, "W": 1},
        turn_number=4,
        archetype_subtype="storm",
    )
    j = snap.model_dump_json()
    snap2 = EVSnapshot.model_validate_json(j)
    assert snap2.my_life == 14
    assert snap2.opp_life == 12
    assert snap2.my_mana_by_color == {"R": 2, "W": 1}
    assert snap2.archetype_subtype == "storm"


# ─── snapshot_from_game integration ──────────────────────────────────


def test_snapshot_from_game_constructs_valid_snapshot():
    """``snapshot_from_game`` is the single legitimate boundary
    between live game state and EVSnapshot.  This test exercises the
    full path on a bare GameState and verifies the resulting snapshot
    satisfies every construction invariant.  If the boundary drifts
    (PR-L1's class of bug), one of the count-floor invariants will
    fire and this test will go red."""
    import random
    from engine.game_state import GameState

    game = GameState(rng=random.Random(0))

    # Snapshot from initial state — every count field should be
    # non-negative and turn_number ≥ 1.  No invariant fires.
    snap = snapshot_from_game(game, 0)
    assert snap.my_life > 0
    assert snap.opp_life > 0
    assert snap.turn_number >= 1
    assert snap.my_total_lands >= 0
    assert snap.my_artifact_count >= 0


# ─── PR-L1 bug class explicitly ──────────────────────────────────────


def test_pr_l1_class_caught_structurally():
    """Explicit demonstration that PR-L1's bug class is now blocked
    by the snapshot's construction-time invariants.

    PR-L1's bug: ``snapshot_from_game`` populated
    ``my_artifact_count`` from a battlefield filter that included
    artifact-typed lands (Darksteel Citadel, Vault of Whispers).
    Downstream scoring read this count as a board-strength proxy and
    inflated affinity decks' EV.

    With the Phase J-1 invariants:

    1. ``extra="forbid"`` blocks the typo'd-field variant of the bug
       (a refactor that renamed the field but missed one site would
       fail at construction, not silently default to 0).
    2. The non-negative-count invariant on ``my_artifact_count``
       blocks a sign-flipped version of the bug (e.g. a buggy
       constructor producing a negative count due to mis-paired
       increment / decrement).

    The CONTENT-level bug (count=5 when reality is 3) cannot be
    enforced inside the model because the model holds the count, not
    the backing list — that's why ``snapshot_from_game`` is the
    contract-bearing site, and PR-L1's existing
    ``test_artifact_lands_excluded_from_artifact_count`` is the
    boundary's correctness test.  This test documents the contract
    boundary; the boundary test asserts the contract."""
    # 1. Typo'd field rejected.
    with pytest.raises(ValidationError):
        EVSnapshot(my_artifct_count=3)
    # 2. Negative count rejected.
    with pytest.raises(ValidationError):
        EVSnapshot(my_artifact_count=-1)
    # 3. ``replace`` enforces both on the override surface — so a
    #    speculative projection that drifts negative is caught at the
    #    boundary, not silently propagated.
    snap = EVSnapshot(my_artifact_count=2)
    with pytest.raises(ValidationError):
        snap.replace(my_artifact_count=-1)
    with pytest.raises(ValidationError):
        snap.replace(my_artifct_count=3)


# ─── Frozen-equivalence via fast_replace + replace ───────────────────


def test_replace_does_not_mutate_source():
    """Even though the model itself is mutable (validate_assignment=
    False), ``replace`` is a pure-function copy: the source snapshot
    is unchanged.  Callers can rely on this for speculative scoring
    that tries multiple alternatives from a single baseline."""
    snap = EVSnapshot(my_life=20, opp_life=18)
    snap2 = snap.replace(my_life=15)
    assert snap.my_life == 20
    assert snap.opp_life == 18
    assert snap2.my_life == 15
    assert snap2.opp_life == 18
