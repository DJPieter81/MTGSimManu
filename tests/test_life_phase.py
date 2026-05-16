"""Rule-phrased tests for the life-phase classifier (W0-B).

The `life_phase(snap)` function classifies game state into one of four
phases — `DEVELOP`, `GRIND`, `PANIC`, `LETHAL` — by composing primitives
already exported from `ai/clock.py` and properties of `EVSnapshot`.

Every test names the *rule* it encodes, never a specific card or deck:
the function is a pure composition primitive, callers in Wave 1 will
consume it to gear-shift behaviour (panic mode, defender chump, etc.).

No magic numbers in this module: every boundary comparison in the
function under test derives from existing clock/snapshot primitives,
so the tests assert on those derived relationships rather than on
literal life totals.
"""
import pytest
from ai.clock import (
    LifePhase,
    life_phase,
    life_as_resource,
    is_early_game,
)
from ai.ev_evaluator import EVSnapshot


# ─────────────────────────────────────────────────────────────
# Rule: at full life with no opposing threats, we are in the
# DEVELOP phase — neither side has a committed clock so neither
# is racing.
# ─────────────────────────────────────────────────────────────

def test_full_life_no_threats_returns_develop():
    snap = EVSnapshot(my_life=20, opp_life=20,
                      my_power=0, opp_power=0)
    # Sanity: this snap should be classified as "early" by the
    # existing predicate, which is the canonical DEVELOP region.
    assert is_early_game(snap), "fixture invariant — both clocks are NO_CLOCK"
    assert life_phase(snap) is LifePhase.DEVELOP


# ─────────────────────────────────────────────────────────────
# Rule: when the opponent's one-turn damage equals or exceeds my
# life total, I lose at the next combat step — that is LETHAL.
# Expressed via the existing `am_dead_next` predicate which is
# exactly `opp_power >= my_life > 0` — no fresh literal here.
# ─────────────────────────────────────────────────────────────

def test_lethal_next_turn_returns_lethal():
    snap = EVSnapshot(my_life=3, opp_life=20,
                      my_power=2, opp_power=5)
    # Fixture invariant: opp_power (5) ≥ my_life (3) so I die next combat.
    assert snap.am_dead_next, "fixture invariant — opp has lethal on board"
    assert life_phase(snap) is LifePhase.LETHAL


# ─────────────────────────────────────────────────────────────
# Rule: PANIC is the region where I am NOT dead next combat, but
# my life buffer (life-as-resource) is strictly shorter than the
# opponent's — I am losing the race in absolute clock terms.
# The boundary is derived: `life_as_resource(my_life, opp_power)
# < life_as_resource(opp_life, my_power)`, no magic threshold.
# ─────────────────────────────────────────────────────────────

def test_panic_below_one_turn_buffer():
    # Tight life, sustained pressure, my clock is much slower than opp's.
    snap = EVSnapshot(my_life=4, opp_life=20,
                      my_power=2, opp_power=3)
    # Fixture invariants the rule depends on:
    assert not snap.am_dead_next, "fixture invariant — opp_power<my_life so not LETHAL"
    my_buffer = life_as_resource(snap.my_life, snap.opp_power)
    opp_buffer = life_as_resource(snap.opp_life, snap.my_power)
    assert my_buffer < opp_buffer, "fixture invariant — I'm behind on the race"
    assert not is_early_game(snap), "fixture invariant — past development phase"
    assert life_phase(snap) is LifePhase.PANIC


# ─────────────────────────────────────────────────────────────
# Rule: GRIND is the mid-zone — both sides have committed clocks
# (we are past `is_early_game`) but neither side is in PANIC or
# LETHAL. Buffers are comparable (mine is not strictly less).
# This is the "long game, neither racing" attractor.
# ─────────────────────────────────────────────────────────────

def test_grind_between_develop_and_panic():
    snap = EVSnapshot(my_life=8, opp_life=8,
                      my_power=2, opp_power=2)
    # Fixture invariants:
    assert not snap.am_dead_next, "fixture invariant — not LETHAL"
    my_buffer = life_as_resource(snap.my_life, snap.opp_power)
    opp_buffer = life_as_resource(snap.opp_life, snap.my_power)
    assert my_buffer == opp_buffer, "fixture invariant — balanced race"
    assert not is_early_game(snap), "fixture invariant — past development phase"
    assert life_phase(snap) is LifePhase.GRIND


# ─────────────────────────────────────────────────────────────
# Rule: the four phases are mutually exclusive — every snap maps
# to exactly one. The classifier never returns None and never
# returns a value outside the enum.
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "snap",
    [
        EVSnapshot(my_life=20, opp_life=20, my_power=0, opp_power=0),
        EVSnapshot(my_life=3, opp_life=20, my_power=2, opp_power=5),
        EVSnapshot(my_life=4, opp_life=20, my_power=2, opp_power=3),
        EVSnapshot(my_life=8, opp_life=8, my_power=2, opp_power=2),
        EVSnapshot(my_life=15, opp_life=15, my_power=5, opp_power=3),
    ],
)
def test_life_phase_is_total_function(snap):
    """Composition primitive must classify every legal snapshot."""
    result = life_phase(snap)
    assert isinstance(result, LifePhase)
    assert result in {LifePhase.DEVELOP, LifePhase.GRIND,
                      LifePhase.PANIC, LifePhase.LETHAL}


# ─────────────────────────────────────────────────────────────
# Rule: LETHAL strictly dominates PANIC. If `am_dead_next` is
# true, the classifier never returns PANIC even if the buffer
# comparison alone would suggest otherwise — the most-urgent
# phase always wins. This is the contract Wave-1 callers rely on
# to gear-shift in the correct order.
# ─────────────────────────────────────────────────────────────

def test_lethal_dominates_panic_in_ordering():
    snap = EVSnapshot(my_life=2, opp_life=20,
                      my_power=1, opp_power=8)
    assert snap.am_dead_next, "fixture invariant — am_dead_next is true"
    assert life_phase(snap) is LifePhase.LETHAL
    # NOT PANIC, even though buffer comparison would also hold.
    assert life_phase(snap) is not LifePhase.PANIC
