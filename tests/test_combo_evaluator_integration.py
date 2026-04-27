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


class TestChainProgressCredit:
    """Step 4 from docs/PHASE_D_FOURTH_ATTEMPT.md fourth-gap fix:
    when the simulator detects a reachable storm pattern but
    `expected_damage = 0` (build-up turn), the evaluator must
    return a positive chain-progress credit, not 0.

    Without this credit, AI sees chain-fuel cards as worth 0 EV
    during build-up → defaults to default scoring → doesn't
    differentiate Storm strategy from random midrange play.

    Formula: `combo_value × relevance / opp_life` — each chain-
    relevant spell represents 1/N of eventual lethal.  Rules-
    derived from Grapeshot's storm-count damage arithmetic
    (lethal at storm = opp_life).
    """

    def test_pif_only_hand_chain_fuel_gets_positive_credit(self):
        """Storm hand with PiF + cantrip but no closer.  Pre-step-4:
        chain_credit = 0.  Post-step-4: chain_credit > 0 (the
        principled per-spell progress credit)."""
        from ai.combo_evaluator import card_combo_evaluation, _BASELINE_CACHE
        _BASELINE_CACHE.clear()

        from tests.test_finisher_simulator import _pif, _cantrip
        from types import SimpleNamespace

        snap = _make_snap(my_mana=4, opp_life=20)
        pif = _pif(1)
        cantrip = _cantrip(2)
        hand = [pif, cantrip]
        me = MockMe(hand=hand, library=list(range(40)))
        opp = SimpleNamespace(battlefield=[])
        game = SimpleNamespace(players=[me, opp])

        # Score the cantrip — it's chain-fuel, relevance should be 1.0
        score = card_combo_evaluation(
            cantrip, snap, me, game, 0, archetype="storm")

        # Build-up credit: combo_value / opp_life ≈ 80/20 = 4.0
        # Plus orthogonal terms (flip/tax) which are 0 in this setup.
        # Pin: must be POSITIVE.
        assert score > 0.0, (
            f"build-up chain-fuel must score positively, got {score}"
        )
        # And must be MUCH less than full lethal credit
        # (which would be ~combo_value ≈ 80)
        assert score < 80.0, (
            f"build-up credit shouldn't equal lethal credit, got {score}"
        )

    def test_no_pattern_truly_empty_hand_returns_zero(self):
        """When pattern=none (no chain enabler at all — neither
        ritual, closer, tutor, nor PiF in hand), the build-up
        credit doesn't fire.  Pin: relevance + non-fuel cards
        score 0 cleanly."""
        from ai.combo_evaluator import card_combo_evaluation, _BASELINE_CACHE
        _BASELINE_CACHE.clear()

        from tests.test_finisher_simulator import MockCard, MockTemplate
        from types import SimpleNamespace

        # A creature — not chain fuel.  Hand has ONLY this creature,
        # so pattern=none (no ritual, closer, tutor, or PiF).
        creature = MockCard(
            template=MockTemplate(name="Bear", cmc=2, is_creature=True,
                                   power=2, toughness=2),
            instance_id=1,
        )
        snap = _make_snap(my_mana=4, opp_life=20)
        hand = [creature]
        me = MockMe(hand=hand, library=list(range(40)))
        opp = SimpleNamespace(battlefield=[])
        game = SimpleNamespace(players=[me, opp])

        score = card_combo_evaluation(
            creature, snap, me, game, 0, archetype="storm")

        # Non-fuel card + pattern=none → all-branches return 0
        # (no chain bonus).
        assert score == 0.0

    def test_lethal_chain_uses_full_chain_credit_not_progress(self):
        """When chain reaches lethal damage, the regular
        chain_credit fires (NOT the build-up progress credit).
        Pin that the two formulas don't double-count."""
        from ai.combo_evaluator import card_combo_evaluation, _BASELINE_CACHE
        _BASELINE_CACHE.clear()

        from tests.test_finisher_simulator import _ritual, _grapeshot
        from types import SimpleNamespace

        # Hand that produces a lethal Grapeshot chain at low opp_life
        snap = _make_snap(my_mana=6, opp_life=4)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        me = MockMe(hand=hand, library=list(range(40)))
        opp = SimpleNamespace(battlefield=[])
        game = SimpleNamespace(players=[me, opp])

        score = card_combo_evaluation(
            hand[0], snap, me, game, 0, archetype="storm")

        # With opp_life=4 and storm damage ≈ 4, fire_value > 0,
        # full chain_credit fires.  Score should be MUCH higher
        # than the build-up progress (which would be combo_value/4).
        # combo_value/opp_life ≈ 80/4 = 20.  Lethal chain_credit
        # ≈ damage * combo_value / opp_life ≈ 80.
        assert score > 20.0, (
            f"lethal-chain score should exceed build-up credit, got {score}"
        )
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
