"""v2 ↔ v3 finisher simulator behavioural parity (guardrail G2).

The default flip from v2 to v3 nearly shipped 3 integration test
regressions because v3's empty-library guard was too aggressive
(returned ``pattern="none"`` for tutor+SB and flashback-recursion
hands that v2 handled). These tests pin the contract so any future
v3 change that loses detection v2 has trips CI.

## Parity contract (one-way)

For each canonical fixture:

1. **No detection-loss**: if v2 reports a real pattern (not
   ``"none"``), v3 must also report a real pattern. v3 picking a
   different pattern is allowed (e.g. cascade vs storm tiebreak).
2. **Offset-0 damage match**: when v3's ``best_turn_offset == 0``
   AND v2 detects a real pattern, ``expected_damage`` matches
   within ±1.0 floating-point. This is the design doc's parity
   contract (`docs/design/2026-05-10_simulator_v3.md` §6.5).

## What is NOT contracted

- v3 may detect chains at offset>0 that v2 cannot represent — the
  entire point of v3. v2 reporting ``"none"`` with v3 reporting a
  pattern is allowed and is the v3 enhancement direction.
- Pattern identity at offset 0 — v3 inherits v2's pattern from
  the best-offset sub-call, so identity is usually preserved, but
  the test only asserts non-none parity.

## Wall budget

~2 seconds. Lives in default pytest suite.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai.finisher_simulator import simulate_finisher_chain
from ai.finisher_simulator_v3 import simulate_finisher_chain_v3

# Reuse the existing mock card library — no duplication.
from tests.test_finisher_simulator import (
    MockCard, MockTemplate, _make_snap,
    _grapeshot, _ritual, _cantrip, _pif, _tutor,
    _cascade_enabler, _reanimator, _discard_outlet,
    _cycler, _cycling_payoff, _gy_creature,
)


def _bhi():
    """Minimal BHI mock — both methods return 0 (no counter / removal
    pressure), so survival_p ≈ 1.0 and the v3 rollout's offset
    arithmetic stays clean."""
    bhi = MagicMock()
    bhi.get_counter_probability.return_value = 0.0
    bhi.get_removal_probability.return_value = 0.0
    return bhi


# ──────────────────────────────────────────────────────────────────
# Fixtures: (label, snap, hand, sideboard, library, archetype)
# Each represents a real game state that exercises a specific v2
# detection path. v3 must not lose detection for any of them.
# ──────────────────────────────────────────────────────────────────

def _fixtures():
    """Return list of (label, kwargs) for parity tests.

    Each kwargs dict is the call-time arguments to BOTH v2 and v3
    (with the v2/v3-specific divergences mapped: v2 takes
    library_size, v3 takes library list).
    """
    return [
        # Storm: ritual + closer in hand. The canonical case the
        # design doc parity contract pins at ±1.0 expected_damage.
        ("storm-ritual-plus-grapeshot",
         dict(
             hand=[_grapeshot(1), _ritual(2), _ritual(3), _ritual(4)],
             sideboard=[],
             library=[_ritual(100 + i) for i in range(40)],
             archetype="combo",
             snap_kwargs=dict(my_mana=6),
         )),

        # Storm: tutor in hand, closer in SB (Wish → Grapeshot).
        # v3 had a regression here — its empty-library guard fired
        # when library was empty even though tutor+SB chain works.
        ("tutor-in-hand-closer-in-sb",
         dict(
             hand=[_tutor(1), _ritual(2)],
             sideboard=[_grapeshot(100)],
             library=[],
             archetype="combo",
             snap_kwargs=dict(my_mana=5),
         )),

        # Storm: PiF only in hand (flashback recursion). v3 had a
        # regression — its empty-library guard returned `none` and
        # combo_evaluator scored cantrips as STORM_HARD_HOLD = -50.
        ("pif-only-extender",
         dict(
             hand=[_pif(1), _cantrip(2)],
             sideboard=[],
             library=[_ritual(100 + i) for i in range(40)],
             archetype="combo",
             snap_kwargs=dict(my_mana=4),
         )),

        # Cycling: cycler + cycling-payoff in hand.
        ("cycling-payoff-in-hand",
         dict(
             hand=[_cycler(1), _cycling_payoff(2)],
             sideboard=[],
             library=[_cycler(100 + i) for i in range(40)],
             archetype="cycling",
             snap_kwargs=dict(my_mana=5),
         )),

        # Cascade: enabler in hand.
        ("cascade-enabler-in-hand",
         dict(
             hand=[_cascade_enabler(1, cmc=3)],
             sideboard=[],
             library=[_cantrip(100 + i) for i in range(40)],
             archetype="cascade",
             snap_kwargs=dict(my_mana=4),
         )),

        # Reanimation: discard outlet + reanimator + big GY creature.
        ("reanimation-with-gy-target",
         dict(
             hand=[_reanimator(1), _discard_outlet(2)],
             sideboard=[],
             library=[_cantrip(100 + i) for i in range(40)],
             archetype="reanimation",
             snap_kwargs=dict(my_mana=4),
             graveyard=[_gy_creature(50, power=8)],
         )),

        # Empty hand — both must return pattern=none.
        ("empty-hand",
         dict(
             hand=[],
             sideboard=[],
             library=[_ritual(100 + i) for i in range(40)],
             archetype="combo",
             snap_kwargs=dict(my_mana=6),
             expect_pattern_none=True,
         )),

        # Non-fuel hand (only lands/creatures) — both must return none.
        ("non-fuel-hand",
         dict(
             hand=[
                 MockCard(template=MockTemplate(
                     name="LightningBoltMock", cmc=1,
                     is_instant=True,
                     oracle_text="deal 3 damage to any target",
                 ), instance_id=1),
             ],
             sideboard=[],
             library=[],
             archetype="aggro",
             snap_kwargs=dict(my_mana=3),
             expect_pattern_none=True,
         )),
    ]


@pytest.mark.parametrize(
    "label,kwargs",
    [(f[0], f[1]) for f in _fixtures()],
    ids=[f[0] for f in _fixtures()],
)
def test_v3_does_not_lose_pattern_detection_v2_has(label, kwargs):
    """One-way parity: if v2 detects a real pattern, v3 must too."""
    snap = _make_snap(**kwargs["snap_kwargs"])
    hand = kwargs["hand"]
    sideboard = kwargs["sideboard"]
    library = kwargs["library"]
    archetype = kwargs["archetype"]
    graveyard = kwargs.get("graveyard", [])
    expect_none = kwargs.get("expect_pattern_none", False)

    v2 = simulate_finisher_chain(
        snap=snap, hand=hand, battlefield=[], graveyard=graveyard,
        library_size=len(library) or 40, storm_count=0,
        archetype=archetype, sideboard=sideboard, library=library,
    )
    v3 = simulate_finisher_chain_v3(
        snap=snap, hand=hand, battlefield=[], graveyard=graveyard,
        library=library, sideboard=sideboard, storm_count=0,
        archetype=archetype, bhi_state=_bhi(),
    )

    if expect_none:
        assert v2.pattern == "none", (
            f"[{label}] fixture says v2 should return none, "
            f"got {v2.pattern!r}"
        )
        assert v3.pattern == "none", (
            f"[{label}] fixture says v3 should return none, "
            f"got {v3.pattern!r}"
        )
        return

    # Positive parity: v2 must detect a pattern (sanity check on
    # the fixture itself — if this fails, the fixture is wrong, not v3).
    assert v2.pattern != "none", (
        f"[{label}] v2 returned 'none' — fixture does not exercise a "
        f"detection path. Fix the fixture before re-running parity."
    )

    # The core guardrail: v3 must NOT return 'none' when v2 doesn't.
    assert v3.pattern != "none", (
        f"[{label}] v3 lost detection that v2 has: "
        f"v2.pattern={v2.pattern!r}, v3.pattern='none'. "
        f"Likely cause: v3 orchestrator guard too aggressive "
        f"(empty library, missing predicate). Loosen the guard."
    )


@pytest.mark.parametrize(
    "label,kwargs",
    [(f[0], f[1]) for f in _fixtures() if not f[1].get("expect_pattern_none")],
    ids=[f[0] for f in _fixtures() if not f[1].get("expect_pattern_none")],
)
def test_v3_offset_zero_expected_damage_within_parity_delta(label, kwargs):
    """When v3 picks offset 0 (act-now), expected_damage must match
    v2 within ±1.0 floating-point per design §6.5 parity contract."""
    snap = _make_snap(**kwargs["snap_kwargs"])
    hand = kwargs["hand"]
    sideboard = kwargs["sideboard"]
    library = kwargs["library"]
    archetype = kwargs["archetype"]
    graveyard = kwargs.get("graveyard", [])

    v2 = simulate_finisher_chain(
        snap=snap, hand=hand, battlefield=[], graveyard=graveyard,
        library_size=len(library) or 40, storm_count=0,
        archetype=archetype, sideboard=sideboard, library=library,
    )
    v3 = simulate_finisher_chain_v3(
        snap=snap, hand=hand, battlefield=[], graveyard=graveyard,
        library=library, sideboard=sideboard, storm_count=0,
        archetype=archetype, bhi_state=_bhi(),
    )

    # Skip when v3 picks a future offset — the parity contract only
    # binds offset 0 (the act-now case).
    if v3.best_turn_offset != 0:
        pytest.skip(
            f"[{label}] v3 picked offset {v3.best_turn_offset} — "
            f"offset-0 parity contract not applicable"
        )

    # Skip when v2 has no chain — nothing to compare against.
    if v2.pattern == "none":
        pytest.skip(
            f"[{label}] v2 detected no chain — no parity baseline"
        )

    delta = abs(v3.expected_damage - v2.expected_damage)
    assert delta <= 1.0, (
        f"[{label}] v3 expected_damage drift {delta:.2f} exceeds "
        f"±1.0 parity contract. v2={v2.expected_damage:.2f}, "
        f"v3={v3.expected_damage:.2f}. The contract pins v3's "
        f"offset-0 arithmetic to v2 — drift here means the v3 "
        f"orchestrator's best-offset sub-call introduced a math "
        f"difference (likely in library_size derivation)."
    )
