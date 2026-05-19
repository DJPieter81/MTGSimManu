"""position_value uses combo_clock when archetype_subtype is set AND not dying.

Phase 2 (PR #408, commit 3736d5a) removed the
``if archetype in ("combo", "storm"): my_clock = min(my_clock, combo_clock(snap))``
override from ``ai.clock.position_value``. The rationale was that the
prior override masked lethal-NOW states for combo decks (see
docs/diagnostics/2026-05-16_cascade_combo_override_at_lethal.md).

The removal was too aggressive: Storm and Living End went from 56% /
53% pre-Phase-2 to ~10-15% / ~37% post. Without the combo-clock
signal in position_value, combo decks with no creatures see
``my_clock == NO_CLOCK == 99`` and ``position_value`` treats them as
losing every position eval — even when the combo is one turn from
firing.

# Rule the test names

When a deck declares ``archetype_subtype`` (a gameplan-driven field
already used by ``combo_clock`` to pick the right resource-assembly
target — current entries: "storm" for Ruby Storm,
"cascade_reanimator" for Living End), ``position_value`` should use
``min(combat_clock, combo_clock(snap))`` as ``my_clock`` **unless**
the snapshot is in a lethal state (``snap.am_dead_next is True``).

The lethal-NOW guard answers the cascade-combo-override diagnostic:
when the controller has NO survival path, combo_clock's
resource-availability heuristic must not mask that fact. Combining
the gate (``archetype_subtype != None``) with the guard
(``not am_dead_next``) restores combo decks' positional awareness
without re-introducing the lethal-NOW masking bug.

# Generic by gameplan field

No archetype branch is reintroduced in code. The override gates on a
snap attribute that the gameplan loader populates from the deck's
JSON. Any future combo deck opts in by adding ``archetype_subtype``
to its gameplan (Goryo's, Amulet Titan, future printings).
"""
from __future__ import annotations

import pytest

from ai.clock import position_value, combat_clock, combo_clock, NO_CLOCK
from ai.ev_evaluator import EVSnapshot


def _snap(**overrides) -> EVSnapshot:
    """Build a minimal EVSnapshot with sensible defaults; overrides
    layer on top. Defaults are chosen to put a Storm-pattern deck in
    a typical T3 state (no board, 3 mana, 7 cards in hand, no
    storm count yet)."""
    defaults = dict(
        my_life=20, opp_life=20,
        my_power=0, opp_power=2,
        my_toughness=0, opp_toughness=2,
        my_evasion_power=0, opp_evasion_power=2,
        my_creature_count=0, opp_creature_count=1,
        my_hand_size=5, opp_hand_size=4,
        my_mana=3, opp_mana=2,
        my_gy_creatures=0, opp_gy_creatures=0,
        storm_count=0,
        my_artifact_count=0, opp_artifact_count=0,
        archetype_subtype=None,
    )
    defaults.update(overrides)
    return EVSnapshot(**defaults)


class TestPositionValueRestoresComboClock:
    """Phase-2-removed combo-clock override re-added under
    archetype_subtype gate + am_dead_next guard."""

    def test_storm_subtype_not_dying_uses_combo_clock(self):
        """Ruby Storm with 5 storm_count + comfortable life:
        ``combo_clock`` returns 1.0 (close to lethal). Without the
        combo-clock override, ``my_clock == NO_CLOCK`` and the
        position is scored as losing. With the override,
        ``my_clock`` clamps to combo_clock(=1.0), and position is
        scored as winning vs the opp's slower clock.

        The position_value WITH the combo-clock override should be
        strictly greater than the position_value WITHOUT."""
        snap = _snap(
            my_power=0, opp_power=2,
            my_life=20, opp_life=20,
            storm_count=5,  # close-to-lethal via combo
            my_hand_size=5, my_mana=3,
            archetype_subtype="storm",
        )
        assert not snap.am_dead_next, "Snap should be in safe life range"
        cc = combo_clock(snap)
        assert cc < NO_CLOCK, (
            f"Sanity: combo_clock returns {cc} at storm_count=5; should be "
            f"< NO_CLOCK={NO_CLOCK} for the override to matter."
        )

        # Capture position WITH combo signal (storm subtype set):
        pv_with = position_value(snap)

        # Compare against a snap WITHOUT the subtype declared
        # (the Phase-2 default — no override):
        snap_no_subtype = _snap(
            my_power=0, opp_power=2,
            my_life=20, opp_life=20,
            storm_count=5,
            my_hand_size=5, my_mana=3,
            archetype_subtype=None,
        )
        pv_without = position_value(snap_no_subtype)

        assert pv_with > pv_without, (
            f"position_value with archetype_subtype='storm' "
            f"({pv_with:.2f}) should beat the same snap without subtype "
            f"({pv_without:.2f}). When the combo is close to lethal "
            f"(storm_count=5 → combo_clock=1.0) and the controller is "
            f"NOT in immediate danger (life=20, opp_power=2), the "
            f"combo-clock signal should give the deck positional "
            f"awareness of its proximity to a win — Phase 2 dropped "
            f"this and Storm WR collapsed from 56% to ~10%."
        )

    def test_lethal_now_snapshot_does_not_use_combo_clock(self):
        """Lethal-NOW guard: when ``am_dead_next is True``, the
        combo-clock override must NOT fire even with
        archetype_subtype set. This is the bug the cascade-combo
        override diagnostic identified: combo_clock returns 1.0
        from resource math, masking that the controller has no
        survival path.

        Concretely: my_life=1, opp_power=24 (am_dead_next is True),
        archetype_subtype='cascade_reanimator', with storm_count=0
        and 6 resource points in hand+gy+mana (enough to fire
        combo_clock=1.0). Position must still reflect lethal
        danger (large negative), not the combo's resource math."""
        snap = _snap(
            my_life=1, opp_life=20,
            my_power=0, opp_power=24,
            my_hand_size=5, my_mana=3,
            my_gy_creatures=3,
            storm_count=0,
            archetype_subtype="cascade_reanimator",
        )
        assert snap.am_dead_next, "Snap should be lethal-NOW"

        pv = position_value(snap)
        assert pv < -10.0, (
            f"position_value at am_dead_next=True with high opp clock "
            f"= {pv:.2f}; must reflect lethal danger (strongly negative). "
            f"The combo-clock override must NOT fire here even though "
            f"archetype_subtype is set — the lethal-NOW guard is the "
            f"specific bug the 2026-05-16 cascade-override diagnostic "
            f"identified."
        )
