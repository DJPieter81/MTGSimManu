"""Tests for `ai/combo_evaluator.py` — pure-additive verification that
the simulator's tutor-as-finisher-access fallback flows correctly
through the evaluator.

The evaluator is NOT wired live (per docs/PHASE_D_FOURTH_ATTEMPT.md
loop-break protocol — five Phase D wire-up attempts have collapsed
Storm).  These tests pin the bottom of the integration: when called
for a tutor-only Storm hand, `_project_baseline` must see the
tutor-access projection (expected_damage > 0).

This closes the gap that was the root cause of the four prior
Phase D failures.  When Sprint 5 retries the live wire-up, the
combo_evaluator's projection will already have correct damage values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

import pytest

from ai.ev_evaluator import EVSnapshot
from engine.cards import Keyword as Kw


@dataclass
class MockTemplate:
    name: str = "Test"
    cmc: int = 1
    is_instant: bool = False
    is_sorcery: bool = False
    is_creature: bool = False
    is_land: bool = False
    oracle_text: str = ""
    tags: Set[str] = field(default_factory=set)
    keywords: Set = field(default_factory=set)
    color_identity: Set = field(default_factory=set)
    power: Optional[int] = None
    toughness: Optional[int] = None
    ritual_mana: Optional[tuple] = None
    cycling_cost_data: Optional[dict] = None
    is_cascade: bool = False
    is_arcane: bool = False
    splice_cost: Optional[int] = None


@dataclass
class MockCard:
    template: MockTemplate = field(default_factory=MockTemplate)
    instance_id: int = 0
    zone: str = "hand"

    @property
    def name(self):
        return self.template.name


def _grapeshot(iid: int = 100) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name="StormBurn",
            cmc=2, is_sorcery=True,
            keywords={Kw.STORM},
            oracle_text="storm — deal 1 damage to any target",
            tags={"finisher"},
        ),
        instance_id=iid,
    )


def _ritual(iid: int = 200) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=f"Ritual{iid}",
            cmc=2, is_instant=True,
            oracle_text="add three red mana",
            ritual_mana=("R", 3),
            tags={"ritual"},
        ),
        instance_id=iid,
    )


def _tutor(iid: int = 1000) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=f"Wish{iid}",
            cmc=3, is_sorcery=True,
            oracle_text="search your sideboard for an instant or sorcery",
            tags={"tutor"},
        ),
        instance_id=iid,
    )


def _make_snap(my_mana: int = 6, opp_life: int = 20) -> EVSnapshot:
    return EVSnapshot(
        my_life=20, opp_life=opp_life,
        my_mana=my_mana, opp_mana=0,
        my_total_lands=my_mana, opp_total_lands=0,
        my_hand_size=4, opp_hand_size=4,
        turn_number=4, storm_count=0,
    )


@dataclass
class MockMe:
    """Minimal player stub for `card_combo_evaluation`'s `me` arg."""
    hand: List = field(default_factory=list)
    battlefield: List = field(default_factory=list)
    graveyard: List = field(default_factory=list)
    library: List = field(default_factory=list)
    sideboard: List = field(default_factory=list)
    spells_cast_this_turn: int = 0


class TestComboEvaluatorTutorAccessIntegration:
    """Pin that combo_evaluator's `_project_baseline` flows through
    the simulator's tutor-access fallback when sideboard contains
    a closer.  This is the integration the live wire-up depends on."""

    def test_tutor_hand_with_sb_closer_projects_damage(self):
        """The critical case: Wish in hand, Grapeshot in SB.
        Without the tutor-access flow, baseline_proj.expected_damage
        is 0.  With the flow, it's > 0."""
        from ai.combo_evaluator import _project_baseline
        snap = _make_snap(my_mana=6)
        hand = [_ritual(1), _ritual(2), _tutor(3)]
        sb = [_grapeshot(99)]

        proj, _ = _project_baseline(
            snap, hand, [], [], 40, 0, "storm",
            sideboard=sb, library=[],
        )
        assert proj.pattern == "storm"
        assert proj.expected_damage > 0.0
        assert proj.closer_in_zone['sb'] is True

    def test_no_sb_no_tutor_flow(self):
        """Without sideboard arg, baseline projection collapses
        to 0 for tutor-only hands — same as pre-Sprint-1 behaviour."""
        from ai.combo_evaluator import _project_baseline
        snap = _make_snap(my_mana=6)
        hand = [_ritual(1), _ritual(2), _tutor(3)]

        proj, _ = _project_baseline(
            snap, hand, [], [], 40, 0, "storm",
        )
        assert proj.expected_damage == 0.0

    def test_card_combo_evaluation_uses_me_sideboard(self):
        """End-to-end: `card_combo_evaluation` reads `me.sideboard`
        and passes it through to `_project_baseline`.  Verifies the
        cache-key path also sees the SB."""
        from ai.combo_evaluator import (
            card_combo_evaluation, _BASELINE_CACHE)
        from types import SimpleNamespace

        # Clear cache so this test runs fresh
        _BASELINE_CACHE.clear()

        snap = _make_snap(my_mana=6)
        hand = [_ritual(1), _ritual(2), _tutor(3)]
        sb = [_grapeshot(99)]
        me = MockMe(hand=hand, sideboard=sb)
        # `card` is the card being evaluated (any chain fuel)
        card = hand[0]
        # Minimal `game` stub — only `players[1-player_idx]` is
        # touched by the search-tax check; pass an empty opponent.
        opp = SimpleNamespace(battlefield=[])
        game = SimpleNamespace(players=[me, opp])

        # Just running the function shouldn't raise.  The internal
        # baseline cache should now contain a proj with positive
        # expected_damage.
        _ = card_combo_evaluation(
            card, snap, me, game, 0, archetype="storm")

        # Verify cache populated with SB-aware projection
        cached = list(_BASELINE_CACHE.values())
        assert len(cached) == 1
        proj, _ids = cached[0]
        assert proj.expected_damage > 0.0
