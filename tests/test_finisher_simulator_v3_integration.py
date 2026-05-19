"""Failing-first tests for `simulate_finisher_chain_v3` orchestrator
(sim v3 PR2).

Pre-fix the function at ``ai/finisher_simulator_v3.py:879`` raises
``NotImplementedError``. The orchestrator is glue: build library
composition → multi-turn rollout → argmax by score → assemble
``FinisherProjectionV3``.

Decisions encoded:

  - **No-finisher-in-hand**: fall through to library-only draws
    (do NOT early-return at offset 0). The offset>0 projections
    are the entire point of v3 — they fix the v2 gap that
    PHASE_D_DEFERRED.md called out.
  - **Early return**: ONLY when hand is empty OR library composition
    is empty (both: no actions possible).
  - **Parity contract**: at offset 0 with closer-in-hand, v3's
    ``expected_damage`` matches v2 within ±1.0 floating-point.
    No parity contract for offset>0 (v2 cannot represent the case).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai.finisher_simulator_v3 import (
    FinisherProjectionV3,
    LibraryComposition,
    simulate_finisher_chain_v3,
)
from ai.scoring_constants import CHAIN_MULTI_TURN_DEPTH
from engine.card_database import CardDatabase
from engine.cards import CardInstance


def _instance(card_db: CardDatabase, name: str, instance_id: int = 0,
              zone: str = "hand") -> CardInstance:
    template = card_db.cards.get(name)
    if template is None:
        pytest.skip(f"Card not in DB: {name}")
    return CardInstance(
        template=template,
        owner=0,
        controller=0,
        instance_id=instance_id,
        zone=zone,
    )


def _bhi() -> MagicMock:
    """Mocked BHI returning low pressure (good for v3 path tests)."""
    bhi = MagicMock()
    bhi.get_counter_probability.return_value = 0.05
    bhi.get_removal_probability.return_value = 0.1
    return bhi


def _snap(my_mana=4, my_life=20, opp_life=20, opp_power=2,
          turn_number=4, my_total_lands=4):
    """Construct a real EVSnapshot so .replace() works inside the
    rollout. Uses the model's actual field names (verified via
    EVSnapshot.model_fields)."""
    from ai.ev_evaluator import EVSnapshot

    return EVSnapshot(
        my_life=my_life,
        opp_life=opp_life,
        my_power=0,
        opp_power=opp_power,
        my_toughness=0,
        opp_toughness=0,
        my_creature_count=0,
        opp_creature_count=0,
        my_hand_size=4,
        opp_hand_size=4,
        my_mana=my_mana,
        opp_mana=4,
        my_mana_by_color={},
        my_total_lands=my_total_lands,
        opp_total_lands=4,
        turn_number=turn_number,
        storm_count=0,
        my_gy_creatures=0,
        opp_gy_creatures=0,
        my_energy=0,
    )


# ──────────────────────────────────────────────────────────────────
# Positive cases — v3 produces a real projection
# ──────────────────────────────────────────────────────────────────

def test_orchestrator_with_storm_closer_in_hand_returns_pattern_storm(card_db):
    """Hand has Grapeshot + rituals. v3 must produce a real
    storm-pattern projection (not raise, not return pattern=none)."""
    db = card_db
    if "Grapeshot" not in db.cards or "Pyretic Ritual" not in db.cards:
        pytest.skip("Required cards not in DB")
    hand = [
        _instance(db, "Grapeshot", 1),
        _instance(db, "Pyretic Ritual", 2),
        _instance(db, "Pyretic Ritual", 3),
        _instance(db, "Pyretic Ritual", 4),
    ]
    library = []
    sideboard = []
    proj = simulate_finisher_chain_v3(
        snap=_snap(my_mana=6),
        hand=hand,
        battlefield=[],
        graveyard=[],
        library=library,
        sideboard=sideboard,
        storm_count=0,
        archetype="combo",
        bhi_state=_bhi(),
    )
    assert isinstance(proj, FinisherProjectionV3)
    # Pattern is one of {storm, cascade, reanimation, cycling}; must
    # NOT be "none" with closer in hand.
    assert proj.pattern != "none", (
        f"With Grapeshot + rituals in hand, v3 must detect a "
        f"chain pattern; got pattern='{proj.pattern}'"
    )


def test_orchestrator_with_empty_hand_returns_pattern_none(card_db):
    """Empty hand → no actions possible → return pattern=none
    (early-return guard per design decision)."""
    proj = simulate_finisher_chain_v3(
        snap=_snap(),
        hand=[],
        battlefield=[],
        graveyard=[],
        library=[],
        sideboard=[],
        storm_count=0,
        archetype="combo",
        bhi_state=_bhi(),
    )
    assert proj.pattern == "none"
    assert proj.expected_damage == 0.0


def test_orchestrator_with_empty_library_returns_pattern_none(card_db):
    """Empty library → no draw path → return pattern=none."""
    db = card_db
    if "Lightning Bolt" not in db.cards:
        pytest.skip("Lightning Bolt not in DB")
    hand = [_instance(db, "Lightning Bolt", 1)]
    proj = simulate_finisher_chain_v3(
        snap=_snap(),
        hand=hand,
        battlefield=[],
        graveyard=[],
        library=[],
        sideboard=[],
        storm_count=0,
        archetype="aggro",
        bhi_state=_bhi(),
    )
    assert proj.pattern == "none"


def test_orchestrator_assembles_full_FinisherProjectionV3_schema(card_db):
    """Schema-completeness: every v3 field present and well-typed."""
    db = card_db
    if "Grapeshot" not in db.cards or "Pyretic Ritual" not in db.cards:
        pytest.skip("Required cards not in DB")
    hand = [
        _instance(db, "Grapeshot", 1),
        _instance(db, "Pyretic Ritual", 2),
    ]
    proj = simulate_finisher_chain_v3(
        snap=_snap(my_mana=4),
        hand=hand,
        battlefield=[],
        graveyard=[],
        library=[],
        sideboard=[],
        storm_count=0,
        archetype="combo",
        bhi_state=_bhi(),
    )
    assert isinstance(proj.library_composition, LibraryComposition)
    # turn_projections must have at least one entry per offset up to
    # CHAIN_MULTI_TURN_DEPTH (3) + 1 = 4, unless early-stopped by
    # death-by-pressure.
    assert len(proj.turn_projections) >= 1
    assert len(proj.turn_projections) <= CHAIN_MULTI_TURN_DEPTH + 1
    # best_turn_offset ∈ [0, max_offset]
    max_off = max(p.offset for p in proj.turn_projections)
    assert 0 <= proj.best_turn_offset <= max_off
    # p_closer_by_turn parallel to turn_projections
    assert len(proj.p_closer_by_turn) == len(proj.turn_projections)


# ──────────────────────────────────────────────────────────────────
# Best-offset selection — argmax-by-score behaviour
# ──────────────────────────────────────────────────────────────────

def test_orchestrator_best_offset_is_argmax_of_turn_projections(card_db):
    """best_turn_offset must match the offset of the highest-score
    projection in turn_projections."""
    db = card_db
    if "Grapeshot" not in db.cards or "Pyretic Ritual" not in db.cards:
        pytest.skip("Required cards not in DB")
    hand = [
        _instance(db, "Grapeshot", 1),
        _instance(db, "Pyretic Ritual", 2),
    ]
    proj = simulate_finisher_chain_v3(
        snap=_snap(my_mana=3),
        hand=hand,
        battlefield=[],
        graveyard=[],
        library=[],
        sideboard=[],
        storm_count=0,
        archetype="combo",
        bhi_state=_bhi(),
    )
    if not proj.turn_projections:
        pytest.skip("No projections to argmax over")
    best_score = max(p.score for p in proj.turn_projections)
    best_offset_from_projections = next(
        p.offset for p in proj.turn_projections if p.score == best_score
    )
    assert proj.best_turn_offset == best_offset_from_projections


# ──────────────────────────────────────────────────────────────────
# Parity contract — offset 0 closer-in-hand matches v2
# ──────────────────────────────────────────────────────────────────

def test_offset_zero_with_closer_in_hand_matches_v2_within_tolerance(card_db):
    """Parity contract: at offset 0 with closer in hand, v3's
    expected_damage must be within ±1.0 floating-point of v2.

    Both call ai.combo_chain.find_all_chains internally, so the
    arithmetic is shared; this test pins the contract that the
    orchestrator does not introduce additional damage drift in the
    closer-in-hand case.
    """
    db = card_db
    if "Grapeshot" not in db.cards or "Pyretic Ritual" not in db.cards:
        pytest.skip("Required cards not in DB")
    hand = [
        _instance(db, "Grapeshot", 1),
        _instance(db, "Pyretic Ritual", 2),
        _instance(db, "Pyretic Ritual", 3),
        _instance(db, "Pyretic Ritual", 4),
    ]
    snap = _snap(my_mana=6)

    from ai.finisher_simulator import simulate_finisher_chain
    v2 = simulate_finisher_chain(
        snap=snap, hand=hand, battlefield=[], graveyard=[],
        library_size=50, storm_count=0, archetype="combo",
        sideboard=[], library=None,
    )
    v3 = simulate_finisher_chain_v3(
        snap=snap, hand=hand, battlefield=[], graveyard=[],
        library=[], sideboard=[], storm_count=0, archetype="combo",
        bhi_state=_bhi(),
    )
    if v2.pattern == "none" or v3.best_turn_offset != 0:
        pytest.skip(
            f"Parity contract only meaningful when v2 detects pattern "
            f"AND v3 picks offset 0; got v2.pattern={v2.pattern}, "
            f"v3.best_turn_offset={v3.best_turn_offset}"
        )
    # ±1.0 floating-point tolerance on expected_damage at offset 0.
    assert abs(v3.expected_damage - v2.expected_damage) <= 1.0, (
        f"Parity contract violated: v2.expected_damage="
        f"{v2.expected_damage}, v3.expected_damage="
        f"{v3.expected_damage}; tolerance=1.0"
    )
