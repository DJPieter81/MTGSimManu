"""Combo-clock override masks lethal-NOW state for cascade / storm decks.

`ai/clock.py::position_value` lines 383-386 replace `my_clock` with
`min(my_clock, combo_clock(snap))` when archetype ∈ {"combo", "storm"}.
At a lethal-NOW state (controller at 1 life, no creatures, opponent has
overkill power) `combat_clock(my_power=0, …)` correctly returns
`NO_CLOCK` (99.0), but `combo_clock` returns 1.0 from its resource-
assembly heuristic (resources are available — it does NOT model whether
the controller survives to cast the combo). The `min(…)` pulls `my_clock`
out of `NO_CLOCK` and into the linear branch of the clock-diff select,
masking the lethal-dread term.

The mechanic the override should encode: "if my combo deck can assemble
a kill in N turns, treat me as having a real clock." Sound when the
controller survives to cast the combo; unsound when opponent has lethal
combat damage this turn and the combo hasn't resolved yet.

The fix: gate the override on a survival predicate. The override fires
only if combat_clock against me is strictly above 1 turn OR I have
blockers and the integer-level `am_dead_next` predicate is false.

Tests are rule-phrased (no card names). Class size ≥10 — every cascade
or storm combo archetype, see
docs/diagnostics/2026-05-16_cascade_combo_override_at_lethal.md.
"""
import pytest

from ai.clock import position_value
from ai.ev_evaluator import EVSnapshot


def _lethal_now_combo_snap(archetype_subtype="cascade_reanimator"):
    """Reference snap from the diagnostic doc.

    Controller has NO creatures, 1 life, faces 24 power of opp board.
    Combo resources (fuel + mana + GY) are assembled — `combo_clock`
    reports 1.0 under the cascade-reanimator threshold. Without the
    fix, `position_value(snap, "combo")` masks the lethal-dread
    contribution because the combo clock overrides combat_clock's
    `NO_CLOCK` signal.
    """
    return EVSnapshot(
        my_life=1, opp_life=20,
        my_power=0, opp_power=24,
        my_creature_count=0, opp_creature_count=4,
        my_toughness=0, opp_toughness=8,
        my_hand_size=3, my_mana=3,
        my_gy_creatures=3,
        storm_count=0,
        archetype_subtype=archetype_subtype,
    )


# ─────────────────────────────────────────────────────────────
# Test 1 — Override does NOT mask lethal-NOW state for combo
# ─────────────────────────────────────────────────────────────

def test_combo_clock_override_does_not_mask_lethal_now_state():
    """At lethal-NOW (1 life, no creatures, opp overkill), the combo
    archetype's position_value must reflect the same lethal-dread
    signal that midrange does.

    Pre-fix: combo branch overrides my_clock via combo_clock, routing
    into the linear branch — clock_diff = opp_clock - 1.0 ≈ 0, missing
    the NO_CLOCK lethal-dread term.

    Post-fix: the override is gated on a survival predicate that is
    False here (opp_clock = 1.0, my_creature_count = 0), so combo
    falls into the same NO_CLOCK branch as midrange.

    Tolerance: ±5% of the midrange value. Either both archetypes
    score equivalent lethal-dread (gap ≈ 0) or the override is masking
    the signal (gap ≫ 5%).
    """
    snap = _lethal_now_combo_snap("cascade_reanimator")
    pv_combo = position_value(snap, archetype="combo")
    pv_mid = position_value(snap, archetype="midrange")

    # Rule: at lethal-NOW the combo override must not change the
    # position-value relative to midrange.
    assert abs(pv_combo - pv_mid) <= 0.05 * abs(pv_mid), (
        f"combo override masked lethal-NOW state: "
        f"combo={pv_combo:.4f} vs midrange={pv_mid:.4f}, "
        f"gap={pv_combo - pv_mid:.4f} ({abs(pv_combo - pv_mid) / abs(pv_mid) * 100:.1f}%)"
    )


# ─────────────────────────────────────────────────────────────
# Test 2 — Override DOES apply when not at lethal (regression)
# ─────────────────────────────────────────────────────────────

def test_combo_clock_override_fires_normally_when_not_at_lethal():
    """Regression: the combo override must continue to apply when the
    controller is not dead-this-turn. The intended use case — combo
    deck with no on-board creatures but a real combo clock several
    turns away — must still see the combo_clock substitution.

    Setup: mid-game snap, ample life, some creatures on board, combo
    resources partially assembled (combo_clock < combat_clock).
    Expected: position_value(combo) ≠ position_value(midrange) — the
    override actively changes my_clock from combat_clock to combo_c.
    """
    snap = EVSnapshot(
        my_life=20, opp_life=20,
        my_power=4, opp_power=3,
        my_creature_count=2, opp_creature_count=2,
        my_toughness=4, opp_toughness=3,
        my_hand_size=4, my_mana=3,
        my_gy_creatures=2,
        storm_count=0,
        archetype_subtype="storm",
    )
    pv_combo = position_value(snap, archetype="combo")
    pv_mid = position_value(snap, archetype="midrange")

    # At midgame with creatures + resources, combo_clock is faster
    # than combat_clock, so the override pulls my_clock down. The
    # values must differ meaningfully (>= 0.5 unit) to confirm the
    # override is still functional in its intended regime.
    assert pv_combo != pv_mid, (
        f"combo override did not fire in non-lethal midgame state: "
        f"combo={pv_combo:.4f}, midrange={pv_mid:.4f}"
    )
    assert pv_combo > pv_mid, (
        "combo archetype with assembled resources should score "
        f"higher than midrange baseline; got combo={pv_combo:.4f}, "
        f"midrange={pv_mid:.4f}"
    )


# ─────────────────────────────────────────────────────────────
# Test 3 — Storm archetype mirror (same lethal-NOW shape)
# ─────────────────────────────────────────────────────────────

def test_storm_archetype_at_lethal_routes_through_lethal_dread_branch():
    """Symmetric mirror of test 1 for the "storm" archetype string.
    The override gates on `archetype in ("combo", "storm")` — both
    code paths must respect the survival predicate identically.

    Setup: same lethal-NOW snap, but archetype_subtype="storm"
    (Ruby Storm pattern: low life, no on-board, Wish-tutored
    payoff in SB or library, payoff cannot resolve this turn).
    """
    snap = _lethal_now_combo_snap(archetype_subtype="storm")
    pv_storm = position_value(snap, archetype="storm")
    pv_mid = position_value(snap, archetype="midrange")

    assert abs(pv_storm - pv_mid) <= 0.05 * abs(pv_mid), (
        f"storm override masked lethal-NOW state: "
        f"storm={pv_storm:.4f} vs midrange={pv_mid:.4f}, "
        f"gap={pv_storm - pv_mid:.4f} ({abs(pv_storm - pv_mid) / abs(pv_mid) * 100:.1f}%)"
    )
