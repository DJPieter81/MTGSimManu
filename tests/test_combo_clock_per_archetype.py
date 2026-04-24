"""LE-G2 — per-archetype resource threshold for combo_clock.

Living End's combo clock had inherited the 8-resource assembly model from
Ruby Storm / Amulet Titan.  Living End's real win condition is cheaper:
3 mana + ~3 graveyard creatures + a cascade spell = 6 resource points.
The 8-resource ceiling produced ~5-turn kill estimates when the actual
kill arrives in ~3 turns.

This test pins down the fix: when the EVSnapshot's `archetype_subtype`
identifies the deck as a cascade-reanimator combo, `combo_clock`
resolves the assembly target from a subtype-keyed table rather than
using a single literal.

Detection is archetype-/tag-driven (subtype string loaded from the
gameplan JSON), NOT hardcoded card or deck names.
"""
import pytest

from ai.clock import combo_clock, NO_CLOCK
from ai.ev_evaluator import EVSnapshot


def _pre_chain_snapshot(archetype_subtype=None, storm_count=0):
    """A "pre-chain" board: some mana, some fuel, some graveyard
    creatures.  Sits between Storm's 8-resource line and Living End's
    6-resource line so the archetype-routed threshold changes the
    answer."""
    return EVSnapshot(
        storm_count=storm_count,
        my_hand_size=3,      # has a cascade spell + a cycler or two
        my_mana=3,           # enough to cast cascade
        my_gy_creatures=3,   # decent fuel for Living End's resolve
        archetype_subtype=archetype_subtype,
    )


# ─────────────────────────────────────────────────────────────
# Test 1 — Storm regression.  Storm (default model) unchanged.
# ─────────────────────────────────────────────────────────────

def test_storm_default_threshold_unchanged():
    """Storm decks see no behavioural change between 'None' (legacy
    default) and 'storm' (explicit)."""
    snap_none = _pre_chain_snapshot(archetype_subtype=None)
    snap_storm = _pre_chain_snapshot(archetype_subtype="storm")
    assert combo_clock(snap_none) == combo_clock(snap_storm)


def test_storm_thin_resources_match_pre_fix():
    """Under the 8-resource model, the pre-fix math still holds.

    resources = min(1,5) + min(1,5) + min(0,2) + 0 = 2; deficit = 6;
    combo_clock = 1 + 6 = 7.0.
    """
    snap = EVSnapshot(
        storm_count=0, my_hand_size=1, my_mana=1, my_gy_creatures=0,
        archetype_subtype="storm",
    )
    assert combo_clock(snap) == pytest.approx(7.0)


# ─────────────────────────────────────────────────────────────
# Test 2 — Living End (cascade-reanimator) subtype is faster.
# ─────────────────────────────────────────────────────────────

def test_cascade_reanimator_threshold_is_lower():
    """With identical resources, a cascade-reanimator should report a
    STRICTLY FASTER (lower) combo clock than a Storm deck."""
    snap_storm = _pre_chain_snapshot(archetype_subtype="storm")
    snap_cascade = _pre_chain_snapshot(archetype_subtype="cascade_reanimator")
    assert combo_clock(snap_cascade) <= combo_clock(snap_storm)

    # And the difference actually bites in the pre-chain regime where
    # the Storm model still reports > 1.0 turn.
    snap_storm_thin = EVSnapshot(
        storm_count=0, my_hand_size=2, my_mana=2, my_gy_creatures=2,
        archetype_subtype="storm",
    )
    snap_cascade_thin = EVSnapshot(
        storm_count=0, my_hand_size=2, my_mana=2, my_gy_creatures=2,
        archetype_subtype="cascade_reanimator",
    )
    assert combo_clock(snap_cascade_thin) < combo_clock(snap_storm_thin)


def test_cascade_reanimator_ready_at_minimum_assembly():
    """Living End assembles at 3 mana + 3 GY + cascade card in hand.

    Under the cascade-reanimator 6-resource model this reads as ready
    (clock == 1.0).  Under the default 8-resource Storm model the same
    board must NOT read as ready.
    """
    snap = EVSnapshot(
        storm_count=0,
        my_hand_size=1,      # one cascade spell in hand
        my_mana=3,           # cascade cost
        my_gy_creatures=3,   # reanimate pool
        archetype_subtype="cascade_reanimator",
    )
    assert combo_clock(snap) == pytest.approx(1.0)

    snap_storm = EVSnapshot(
        storm_count=0,
        my_hand_size=1,
        my_mana=3,
        my_gy_creatures=3,
        archetype_subtype="storm",
    )
    assert combo_clock(snap_storm) > 1.0


# ─────────────────────────────────────────────────────────────
# Test 3 — Amulet Titan (archetype=combo, no subtype) regression.
# ─────────────────────────────────────────────────────────────

def test_amulet_titan_default_model_unchanged():
    """Amulet Titan is archetype=combo with no subtype declared.  It
    must continue to see the default 8-resource model."""
    snap = EVSnapshot(
        storm_count=0,
        my_hand_size=4,      # Grazer, Amulet, Titan, misc
        my_mana=2,           # early turns
        my_gy_creatures=0,
        archetype_subtype=None,   # Amulet does not set a subtype
    )
    # resources = 4 + 2 + 0 + 0 = 6; deficit = 2; combo_clock = 3.0.
    assert combo_clock(snap) == pytest.approx(3.0)


def test_mid_chain_storm_count_wins_regardless_of_subtype():
    """storm_count >= 5 short-circuits to 1.0 for every subtype.
    Subtype routing must not break the mid-chain guard."""
    for sub in (None, "storm", "cascade_reanimator"):
        snap = EVSnapshot(storm_count=6, my_hand_size=0, my_mana=0,
                          archetype_subtype=sub)
        assert combo_clock(snap) == 1.0


def test_no_resources_stays_slow_for_all_subtypes():
    """An empty hand, no mana, no graveyard → slow for every subtype."""
    for sub in (None, "storm", "cascade_reanimator"):
        snap = EVSnapshot(storm_count=0, my_hand_size=0, my_mana=0,
                          my_gy_creatures=0, archetype_subtype=sub)
        assert combo_clock(snap) > 1.0
