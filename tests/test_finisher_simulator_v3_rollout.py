"""Tests for the multi-turn rollout in ``ai/finisher_simulator_v3.py``.

Each test names the MECHANIC being verified, not a card. The rollout
is exercised across all four chain patterns (storm / cascade /
reanimation / cycling) per the design contract in
``docs/design/2026-05-10_simulator_v3.md`` §5 and §6.3.

The rollout returns a tuple of ``TurnOffsetProjection`` nodes —
offset 0, 1, ..., up to ``CHAIN_MULTI_TURN_DEPTH``. The caller picks
the offset that maximises ``damage × survival``. These tests
verify the SHAPE and the OPTIMISATION RULE; they do not pin the
exact damage numbers (those are validated by the v2 chain finder's
own tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

import pytest

from ai.bhi import BayesianHandTracker
from ai.ev_evaluator import EVSnapshot
from ai.finisher_simulator_v3 import (
    LibraryComposition,
    TurnOffsetProjection,
    _project_multi_turn,
    _survival_to_offset,
)
from ai.scoring_constants import (
    CHAIN_MULTI_TURN_DEPTH,
    CHAIN_REMOVAL_PRESSURE_FLOOR,
)


# ─── Mock helpers (shape mirrors tests/test_finisher_simulator.py) ──


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

    @property
    def power(self):
        return self.template.power


def _make_snap(
    *,
    my_mana: int = 4,
    opp_life: int = 20,
    my_life: int = 20,
    storm_count: int = 0,
    opp_power: int = 0,
    turn_number: int = 4,
) -> EVSnapshot:
    return EVSnapshot(
        my_life=my_life,
        opp_life=opp_life,
        my_mana=my_mana,
        opp_mana=0,
        my_total_lands=my_mana,
        opp_total_lands=0,
        my_hand_size=4,
        opp_hand_size=4,
        turn_number=turn_number,
        storm_count=storm_count,
        opp_power=opp_power,
    )


def _make_bhi(
    *, p_counter: float = 0.0, p_removal: float = 0.0
) -> BayesianHandTracker:
    """Construct a BHI tracker with explicit posterior beliefs.

    Skips ``initialize_from_game`` (which requires a live ``GameState``);
    instead we plant the beliefs directly so tests can isolate the
    rollout arithmetic from the prior-computation pipeline.
    """
    bhi = BayesianHandTracker(player_idx=0)
    bhi.beliefs.p_counter = p_counter
    bhi.beliefs.p_removal = p_removal
    bhi._initialized = True
    return bhi


# Card mocks — generic by tag/oracle (no card-name semantics).


def _grapeshot(iid: int = 1) -> MockCard:
    from engine.cards import Keyword as Kw

    return MockCard(
        template=MockTemplate(
            name="StormBurn",
            cmc=2,
            is_sorcery=True,
            keywords={Kw.STORM},
            oracle_text="storm — deal 1 damage to any target",
            tags={"finisher"},
        ),
        instance_id=iid,
    )


def _ritual(iid: int = 2, name: str = "PyreticRitual") -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2,
            is_instant=True,
            oracle_text="add three red mana",
            ritual_mana=("R", 3),
            tags={"ritual"},
        ),
        instance_id=iid,
    )


def _cascade_enabler(iid: int = 3, cmc: int = 3) -> MockCard:
    from engine.cards import Keyword as Kw

    return MockCard(
        template=MockTemplate(
            name="ShardlessAgentMock",
            cmc=cmc,
            is_creature=True,
            keywords={Kw.CASCADE},
            is_cascade=True,
            oracle_text="cascade",
            tags={"cascade"},
        ),
        instance_id=iid,
    )


def _reanimator(iid: int = 4, cmc: int = 3) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name="GoryosVengeanceMock",
            cmc=cmc,
            is_instant=True,
            oracle_text=(
                "return target creature card from your graveyard "
                "to the battlefield"
            ),
            tags={"reanimate"},
        ),
        instance_id=iid,
    )


def _gy_creature(iid: int = 5, power: int = 8) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=f"BigCreatureGY{iid}",
            cmc=8,
            is_creature=True,
            power=power,
            toughness=power,
        ),
        instance_id=iid,
        zone="graveyard",
    )


def _cycler(iid: int = 6, cycle_cost: int = 1) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name=f"CyclerMock{iid}",
            cmc=3,
            is_creature=True,
            power=3,
            toughness=2,
            oracle_text=f"cycling {{{cycle_cost}}}",
            cycling_cost_data={"mana": cycle_cost, "life": 0, "colors": []},
            tags={"cycler"},
        ),
        instance_id=iid,
    )


def _cycling_payoff(iid: int = 7) -> MockCard:
    return MockCard(
        template=MockTemplate(
            name="LivingEndMock",
            cmc=5,
            is_sorcery=True,
            oracle_text=(
                "each player exiles all creatures they control, then "
                "returns all creature cards from their graveyards "
                "to the battlefield"
            ),
            tags={"combo"},
        ),
        instance_id=iid,
    )


def _empty_library_composition() -> LibraryComposition:
    """A library composition with no closers in any category."""
    return LibraryComposition(
        total=40,
        by_tag={},
        closer_count=0,
        closer_categories=(),
    )


# ─── Multi-turn rollout tests — covers all four chain patterns ─────


class TestMultiTurnRolloutShape:
    """Smoke checks on the rollout return shape."""

    def test_multi_turn_rollout_returns_tuple_of_turnoffset_projections(self):
        """Rule: rollout returns a non-empty tuple of
        TurnOffsetProjection nodes, one per offset 0..depth, with
        ``offset`` strictly increasing from 0."""
        snap = _make_snap(my_mana=6)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="storm",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        assert isinstance(projections, tuple)
        assert len(projections) >= 1
        for i, p in enumerate(projections):
            assert isinstance(p, TurnOffsetProjection)
            assert p.offset == i


class TestMultiTurnRolloutStormPattern:
    """§6.3 storm pattern — every test names a mechanic."""

    def test_multi_turn_rollout_returns_max_damage_x_survival_offset(self):
        """Rule: when offset 1 projects strictly higher
        ``damage × survival`` than offset 0, the rollout's argmax
        prefers offset 1.

        Storm-pattern fixture: at offset 0 we have too little mana
        to cast the cheapest ritual (mana=1 < ritual.cmc=2), so the
        chain is unreachable — T+0 damage is 0.  At offset 1 we
        gain +1 land (mana=2), the rituals chain into the closer
        and damage becomes positive.  This is the canonical
        "build chain THIS turn, fire NEXT turn" case from design §1.2.
        """
        # T+0: 1 mana → can't even cast the cheapest ritual (cmc=2).
        # T+1: 2 mana → rituals + closer fire, damage > 0.
        snap = _make_snap(my_mana=1, opp_power=0)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="storm",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # Score at offset 1 must exceed score at offset 0 — the
        # extra land enables a longer chain and survival stays high
        # under the no-clock opponent.
        assert projections[1].expected_damage > projections[0].expected_damage
        # argmax over the rollout
        best = max(projections, key=lambda p: p.score)
        assert best.offset >= 1

    def test_multi_turn_rollout_survival_discounts_late_turns(self):
        """Rule: when opponent's clock is 1 turn (lethal next opp
        turn), the rollout's survival_p at offset >= 1 collapses to
        0, so offset 0 wins even at lower damage.

        Sets opp_power so high that ``opp_clock_discrete`` = 1.
        """
        # my_life=5, opp_power=5 → opp_clock_discrete = 1.
        snap = _make_snap(my_mana=3, my_life=5, opp_power=5)
        hand = [_ritual(1), _grapeshot(2)]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="storm",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # Offset 0 always has full survival (we're still alive now);
        # offset >= 1 has zero survival because opp_clock=1 → dead.
        assert projections[0].survival_p > 0.0
        for p in projections[1:]:
            assert p.survival_p == 0.0

        best = max(projections, key=lambda p: p.score)
        assert best.offset == 0

    def test_multi_turn_rollout_storm_pattern_credits_ritual_fuel(self):
        """Rule: the rollout's offset-1 chain incorporates the
        ritual already cast at offset 0 — i.e. the projected damage
        at offset 1 reflects chain enablement, not just +1 land.

        At T+0 we have rituals + a closer. At T+1 we have +1 mana
        for a longer chain.  Per the design doc, the storm pattern
        is the canonical "build chain THIS turn, find closer NEXT
        turn" case; the rollout must produce non-zero damage on at
        least one offset where rituals enable extra storm count.
        """
        snap = _make_snap(my_mana=4, opp_power=0)
        hand = [
            _ritual(1),
            _ritual(2),
            _ritual(3),
            _grapeshot(4),
        ]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="storm",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # At least one offset projects positive damage (the chain
        # is reachable — rituals + storm-closer present).
        assert any(p.expected_damage > 0.0 for p in projections)
        # The argmax offset must produce a strictly-positive score
        # — ritual fuel translates to a real chain projection.
        best = max(projections, key=lambda p: p.score)
        assert best.score > 0.0


class TestMultiTurnRolloutCascadePattern:
    """§6.3 cascade pattern."""

    def test_multi_turn_rollout_cascade_pattern_inherits_v2_expected_damage_zero_for_now(
        self,
    ):
        """Rule: cascade pattern's ``expected_damage`` stays 0
        (per design doc §8.5 open question — board-swing payoff is
        out of scope for v3, will compose with ``clock.py`` later).
        Rollout MUST NOT crash on cascade and SHOULD pick the
        offset maximising survival when damage is degenerate.
        """
        snap = _make_snap(my_mana=3, opp_power=2)
        hand = [_cascade_enabler(1, cmc=3)]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="cascade_reanimator",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # Damage is zero (§8.5 open question): the chain is reachable
        # but storm-damage arithmetic doesn't model cascade.
        for p in projections:
            assert p.expected_damage == 0.0
        # No crash; non-empty rollout.
        assert len(projections) >= 1
        # Argmax doesn't error; survival_p is monotone non-increasing
        # in offset, so offset 0 wins when damage is tied at zero.
        best = max(projections, key=lambda p: (p.score, -p.offset))
        assert best.offset == 0


class TestMultiTurnRolloutReanimationPattern:
    """§6.3 reanimation pattern."""

    def test_multi_turn_rollout_reanimation_pattern_floors_at_gameplan_target(
        self,
    ):
        """Rule: when a reanimator is in hand and a big creature
        is in the graveyard, the rollout's expected_damage at the
        firing offset is at LEAST the creature's power. Mechanic:
        the chain's damage floor is the reanimation target's power,
        regardless of which turn we fire on (the target doesn't
        change between turns).
        """
        snap = _make_snap(my_mana=3, opp_power=0)
        hand = [_reanimator(1, cmc=3)]
        graveyard = [_gy_creature(2, power=8)]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=graveyard,
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="reanimation",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # Offset 0 has the reanimator in hand and target in GY —
        # expected_damage >= target's power (8).
        assert projections[0].expected_damage >= 8.0
        best = max(projections, key=lambda p: p.score)
        # The chain's target power floors the picked-offset damage.
        assert best.expected_damage >= 8.0


class TestMultiTurnRolloutCyclingPattern:
    """§6.3 cycling pattern."""

    def test_multi_turn_rollout_cycling_pattern_credits_cycler_as_fuel(self):
        """Rule: a cycling card + a payoff in hand activates the
        cycling chain on offset 0 (payoff is reachable from hand).
        The rollout returns the cycling pattern's chain shape on at
        least one offset without crashing.
        """
        snap = _make_snap(my_mana=5, opp_power=0)
        # Cycler + Living-End-style payoff both in hand.
        hand = [_cycler(1, cycle_cost=1), _cycling_payoff(2)]
        bhi = _make_bhi()

        projections = _project_multi_turn(
            snap=snap,
            hand=hand,
            battlefield=[],
            graveyard=[],
            sideboard=[],
            library_composition=_empty_library_composition(),
            storm_count=0,
            archetype="cycling",
            bhi_state=bhi,
            max_depth=CHAIN_MULTI_TURN_DEPTH,
        )

        # Non-empty rollout, no crash.
        assert len(projections) >= 1
        # Survival monotone non-increasing in offset (rules check —
        # the cycling test exercises the survival path).
        for i in range(len(projections) - 1):
            assert projections[i + 1].survival_p <= projections[i].survival_p


# ─── Survival composition tests (§6.4) ─────────────────────────────


class TestSurvivalComposition:
    def test_survival_monotone_in_offset(self):
        """Rule: ``_survival_to_offset(snap, i+1) <=
        _survival_to_offset(snap, i)`` for every i."""
        snap = _make_snap(my_life=10, opp_power=3)
        bhi = _make_bhi()
        last = 1.1
        for i in range(CHAIN_MULTI_TURN_DEPTH + 1):
            s = _survival_to_offset(snap, i, bhi)
            assert 0.0 <= s <= 1.0
            assert s <= last
            last = s

    def test_survival_nonzero_when_no_clock(self):
        """Rule: when opp has no clock (opp_power = 0,
        opp_clock_discrete = NO_CLOCK), survival is 1.0 at every
        offset."""
        snap = _make_snap(my_life=20, opp_power=0)
        bhi = _make_bhi()
        for i in range(CHAIN_MULTI_TURN_DEPTH + 1):
            s = _survival_to_offset(snap, i, bhi)
            assert s == pytest.approx(1.0)

    def test_survival_floored_above_zero_under_max_removal_density(self):
        """Rule: even with bhi.get_removal_probability() = 1.0 and
        a positive opp clock, survival at offset 0 is at LEAST
        ``1 - CHAIN_REMOVAL_PRESSURE_FLOOR`` (= 0.5), never zero.

        Encodes the "fully removal-leaden opponent halves but does
        not zero" rule from design §5.2.
        """
        snap = _make_snap(my_life=10, opp_power=3)
        bhi = _make_bhi(p_removal=1.0)
        s0 = _survival_to_offset(snap, 0, bhi)
        # At offset 0 base_survival = 1.0; removal dampens to
        # 1.0 - CHAIN_REMOVAL_PRESSURE_FLOOR.
        assert s0 == pytest.approx(1.0 - CHAIN_REMOVAL_PRESSURE_FLOOR)
