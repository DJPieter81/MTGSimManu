"""Performance budget tests for EVSnapshot (Phase J-1).

The matrix runs ~1.28M scoring ops per N=20 16x16 sweep.  Each
scoring op constructs and clones snapshots.  These tests pin a
budget so the pydantic migration's overhead stays out of the
critical path.

Budget rationale: 100 ``fast_replace`` clones must complete in
< 10 ms.  At 100 µs / clone, the per-matrix cost is bounded by
100 µs × 1.28M ≈ 128 s — that's 2× the current baseline matrix
runtime, so a single clone-per-scoring-op cost of 100 µs is the
hard ceiling.  The test budget is 10× tighter (10 µs / clone in
aggregate) to leave headroom.

These tests are deterministic and run in the standard test suite
— they do NOT depend on a card database or game state.  Wall-clock
flake risk is low because the test runs 100 ops in a tight loop on
a known-good baseline snapshot.
"""
from __future__ import annotations
import time

from ai.ev_evaluator import EVSnapshot


def _baseline_snap() -> EVSnapshot:
    """A non-trivial snapshot: every field assigned, the dict
    populated.  More representative of real scoring-loop input than
    a default-constructed snap."""
    return EVSnapshot(
        my_life=14, opp_life=11,
        my_power=5, opp_power=4,
        my_toughness=8, opp_toughness=6,
        my_creature_count=2, opp_creature_count=2,
        my_hand_size=4, opp_hand_size=3,
        my_mana=4, opp_mana=4,
        my_mana_by_color={"R": 1, "W": 2, "C": 1},
        my_total_lands=4, opp_total_lands=4,
        turn_number=4,
        storm_count=0,
        my_artifact_count=1, opp_artifact_count=2,
    )


def test_evsnapshot_construction_under_budget():
    """100 ``from-scratch`` constructions in < 50 ms.

    Construction goes through every validator (extra-forbid,
    count-floor, turn-floor).  This is the worst case — a real
    scoring loop calls ``snapshot_from_game`` once per decision and
    then clones via ``fast_replace`` for each option, so the cost
    here only hits at decision boundaries.
    """
    snap = _baseline_snap()
    fields = snap.model_dump()
    start = time.perf_counter()
    for _ in range(100):
        EVSnapshot(**fields)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.050, (
        f"100 EVSnapshot constructions took {elapsed*1000:.1f}ms "
        f"(budget 50ms).  This indicates the post-validator chain "
        f"has grown beyond the matrix runtime budget — investigate "
        f"per-validator cost before merging."
    )


def test_evsnapshot_fast_replace_under_budget():
    """100 ``fast_replace`` clones in < 10 ms.

    ``fast_replace`` is the hot-path clone — every option in the
    speculative scoring loop calls it.  This budget caps the
    per-clone cost at 100 µs, well under the 1 ms threshold where
    the matrix runtime would balloon by > 10%.
    """
    snap = _baseline_snap()
    start = time.perf_counter()
    for _ in range(100):
        snap.fast_replace(my_life=snap.my_life - 1)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.010, (
        f"100 EVSnapshot.fast_replace calls took {elapsed*1000:.1f}ms "
        f"(budget 10ms, ~100µs per clone).  At matrix scale this "
        f"would dominate runtime — investigate the model_construct "
        f"path before merging."
    )


def test_evsnapshot_replace_under_budget():
    """100 ``replace`` calls in < 100 ms.

    ``replace`` is the validating-clone alternative.  More expensive
    because it re-runs the post-validator on the copied snapshot,
    but still fast enough for callers outside the matrix hot loop.
    A breach of this budget indicates the validator pipeline has
    become disproportionately expensive.
    """
    snap = _baseline_snap()
    start = time.perf_counter()
    for _ in range(100):
        snap.replace(my_life=snap.my_life - 1)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.100, (
        f"100 EVSnapshot.replace calls took {elapsed*1000:.1f}ms "
        f"(budget 100ms).  Post-validator overhead grew unexpectedly."
    )
