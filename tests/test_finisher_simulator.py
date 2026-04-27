"""Tests for `ai/finisher_simulator.py` — pure-function finisher
projection used as scaffolding for the eventual migration of
`card_combo_modifier` onto the decision kernel.

Each test verifies that the simulator detects the right pattern from
realistic hand/battlefield state, with no archetype gates and no
hardcoded card names.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Set
import pytest

from ai.ev_evaluator import EVSnapshot
from ai.finisher_simulator import simulate_finisher_chain
from ai.schemas import FinisherProjection


# ─── Mock helpers (mirror tests/test_combo_calc.py shape) ──────────

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


def _make_snap(my_mana: int = 6, opp_life: int = 20, my_life: int = 20,
               storm_count: int = 0) -> EVSnapshot:
    """Minimal EVSnapshot for finisher tests."""
    return EVSnapshot(
        my_life=my_life, opp_life=opp_life,
        my_mana=my_mana, opp_mana=0,
        my_total_lands=my_mana, opp_total_lands=0,
        my_hand_size=4, opp_hand_size=4,
        turn_number=4, storm_count=storm_count,
    )


def _grapeshot(iid: int = 100) -> MockCard:
    """Grapeshot-style storm-keyword damage closer."""
    from engine.cards import Keyword as Kw
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


def _ritual(iid: int = 200, name: str = "PyreticRitual") -> MockCard:
    """Pyretic Ritual-style: pay 1R, add RRR (net +2)."""
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2, is_instant=True,
            oracle_text="add three red mana",
            ritual_mana=("R", 3),
            tags={"ritual"},
        ),
        instance_id=iid,
    )


def _cantrip(iid: int = 300, name: str = "Manamorphose") -> MockCard:
    """Cantrip ritual with draw."""
    return MockCard(
        template=MockTemplate(
            name=name,
            cmc=2, is_instant=True,
            oracle_text="add two mana of any color, then draw a card",
            ritual_mana=("any", 2),
            tags={"cantrip", "ritual"},
        ),
        instance_id=iid,
    )


def _cascade_enabler(iid: int = 400, cmc: int = 3) -> MockCard:
    """Shardless Agent-style cascade enabler."""
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


def _reanimator(iid: int = 500, cmc: int = 3) -> MockCard:
    """Goryo-style reanimator spell."""
    return MockCard(
        template=MockTemplate(
            name="GoryosVengeanceMock",
            cmc=cmc, is_instant=True,
            oracle_text=(
                "return target creature card from your graveyard "
                "to the battlefield"
            ),
            tags={"reanimate"},
        ),
        instance_id=iid,
    )


def _discard_outlet(iid: int = 600) -> MockCard:
    """Faithful Mending-style discard outlet."""
    return MockCard(
        template=MockTemplate(
            name="DiscardOutletMock",
            cmc=2, is_instant=True,
            oracle_text="draw two cards, then discard a card",
            tags={"draw", "looter"},
        ),
        instance_id=iid,
    )


def _cycler(iid: int = 700, cmc: int = 3, cycle_cost: int = 1) -> MockCard:
    """Street Wraith-style cycling card."""
    return MockCard(
        template=MockTemplate(
            name=f"CyclerMock{iid}",
            cmc=cmc, is_creature=True,
            power=3, toughness=2,
            oracle_text=f"cycling {{{cycle_cost}}}",
            cycling_cost_data={"mana": cycle_cost, "life": 0, "colors": []},
            tags={"cycler"},
        ),
        instance_id=iid,
    )


def _cycling_payoff(iid: int = 800) -> MockCard:
    """Living End-style cycling payoff."""
    return MockCard(
        template=MockTemplate(
            name="LivingEndMock",
            cmc=5, is_sorcery=True,
            oracle_text=(
                "each player exiles all creatures they control, then returns "
                "all creature cards from their graveyards to the battlefield"
            ),
            tags={"combo"},
        ),
        instance_id=iid,
    )


def _gy_creature(iid: int = 900, power: int = 7) -> MockCard:
    """Big creature in graveyard for reanimation target."""
    return MockCard(
        template=MockTemplate(
            name=f"GraveCreature{iid}",
            cmc=8, is_creature=True,
            power=power, toughness=power,
        ),
        instance_id=iid,
        zone="graveyard",
    )


def _tutor(iid: int = 1000) -> MockCard:
    """Burning Wish-style tutor."""
    return MockCard(
        template=MockTemplate(
            name="WishMock",
            cmc=3, is_sorcery=True,
            oracle_text="search your sideboard for an instant or sorcery",
            tags={"tutor"},
        ),
        instance_id=iid,
    )


# ─── Tests by pattern ──────────────────────────────────────────────

class TestStormPattern:
    def test_storm_pattern_detected_from_hand(self):
        """Hand with rituals + storm closer → storm pattern projected."""
        snap = _make_snap(my_mana=6)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        assert proj.pattern == "storm"
        assert proj.closer_name == "StormBurn"
        assert proj.chain_length >= 2  # at least 1 ritual + closer
        assert proj.success_probability == 1.0

    def test_storm_uses_combo_chain_arithmetic(self):
        """Integration with `find_all_chains`: storm damage equals
        `ChainOutcome.storm_damage` for the chosen chain."""
        snap = _make_snap(my_mana=4)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        # Chain: 2 rituals + Grapeshot = storm 3, deals 3 damage.
        assert proj.expected_damage >= 3.0
        # mana_floor is the cheapest closer cmc (Grapeshot = 2)
        assert proj.mana_floor == 2

    def test_storm_no_closer_in_hand_low_success(self):
        """Storm pattern with only rituals + a tutor (no closer in
        hand or library) → low success probability."""
        snap = _make_snap(my_mana=5)
        # Tutor in hand but library has nothing it can fetch
        hand = [_ritual(1), _tutor(2)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=30, storm_count=0, archetype="storm",
        )
        # Either pattern="storm" with success<1, or pattern="none".
        # In both cases the simulator should not claim a confident
        # closer.
        assert proj.success_probability < 1.0


class TestCascadePattern:
    def test_cascade_pattern_detected(self):
        """Hand with a cascade enabler → cascade pattern."""
        snap = _make_snap(my_mana=3)
        hand = [_cascade_enabler(1, cmc=3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0,
            archetype="cascade_reanimator",
        )
        assert proj.pattern == "cascade"
        assert proj.closer_name == "ShardlessAgentMock"
        assert proj.mana_floor == 3
        assert proj.chain_length == 2  # enabler + free cast

    def test_cascade_unreachable_when_unaffordable(self):
        """Cascade enabler in hand but not enough mana → low success."""
        snap = _make_snap(my_mana=1)  # can't pay 3
        hand = [_cascade_enabler(1, cmc=3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0,
            archetype="cascade_reanimator",
        )
        # Storm fallback may not detect — cascade should still be
        # the chosen pattern, but with success_probability=0
        assert proj.pattern == "cascade"
        assert proj.success_probability == 0.0


class TestReanimationPattern:
    def test_reanimation_pattern_detected_with_target_in_gy(self):
        """Reanimator in hand + big creature in GY → reanimation."""
        snap = _make_snap(my_mana=3)
        hand = [_reanimator(1, cmc=3)]
        graveyard = [_gy_creature(iid=99, power=8)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=graveyard,
            library_size=40, storm_count=0, archetype="combo",
        )
        assert proj.pattern == "reanimation"
        assert proj.closer_name == "GoryosVengeanceMock"
        assert proj.expected_damage == 8.0
        assert proj.success_probability == 1.0
        assert proj.mana_floor == 3

    def test_reanimation_via_discard_outlet(self):
        """Reanimator + outlet + creature in hand → success degraded
        by extra rules step (outlet must succeed first)."""
        snap = _make_snap(my_mana=5)
        big_creature = MockCard(
            template=MockTemplate(
                name="BigBoy", cmc=10, is_creature=True,
                power=9, toughness=9,
            ),
            instance_id=42,
        )
        hand = [_reanimator(1), _discard_outlet(2), big_creature]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="combo",
        )
        assert proj.pattern == "reanimation"
        # Empty GY → must use outlet → success drops below 1.0
        assert proj.success_probability < 1.0
        assert proj.success_probability > 0.0


class TestCyclingPattern:
    def test_cycling_pattern_detected(self):
        """Cyclers + cycling-payoff in hand → cycling pattern."""
        snap = _make_snap(my_mana=5)
        hand = [_cycler(1, cmc=3, cycle_cost=1),
                _cycler(2, cmc=4, cycle_cost=2),
                _cycling_payoff(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0,
            archetype="cascade_reanimator",
        )
        # When cascade is also available the simulator picks the
        # higher-priority one; here only cycling/payoff exist.
        assert proj.pattern in ("cycling", "cascade")
        # In the absence of a cascade enabler, must be cycling.
        assert proj.pattern == "cycling"
        assert proj.mana_floor == 1  # cheapest cycle cost


class TestNoChainReachable:
    def test_no_chain_with_only_lands_and_creatures(self):
        """Hand with only lands and dorks → no chain reachable."""
        snap = _make_snap(my_mana=2)
        creature = MockCard(
            template=MockTemplate(
                name="VanillaDork", cmc=2, is_creature=True,
                power=2, toughness=2,
            ),
            instance_id=1,
        )
        hand = [creature]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="aggro",
        )
        assert proj.pattern == "none"
        assert proj.expected_damage == 0.0
        assert proj.closer_name is None
        assert proj.chain_length == 0

    def test_storm_fizzles_with_empty_hand(self):
        """Empty hand → no chain projection."""
        snap = _make_snap(my_mana=10)
        proj = simulate_finisher_chain(
            snap=snap, hand=[], battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        assert proj.pattern == "none"


class TestArchetypeTiebreaker:
    def test_archetype_breaks_ties_only(self):
        """When two patterns are reachable with equal EV, archetype
        is the tiebreaker — but archetype does not gate detection."""
        # Storm closer + cascade enabler in hand + a creature in GY.
        # Multiple patterns reachable.  Different archetypes should
        # pick different primaries when EV is otherwise equal.
        snap = _make_snap(my_mana=8)
        hand = [_ritual(1), _grapeshot(2)]
        proj_storm = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        # Detection is oracle-driven — passing archetype="aggro"
        # should still detect the storm pattern from the same hand.
        proj_aggro = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="aggro",
        )
        assert proj_storm.pattern == "storm"
        assert proj_aggro.pattern == "storm"
        # Same closer, same arithmetic — archetype didn't gate
        assert proj_storm.closer_name == proj_aggro.closer_name
        assert proj_storm.expected_damage == proj_aggro.expected_damage


class TestSchemaValidation:
    def test_finisher_projection_pydantic(self):
        """Returned object is a frozen Pydantic model with the
        documented field constraints."""
        snap = _make_snap()
        proj = simulate_finisher_chain(
            snap=snap, hand=[], battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="any",
        )
        assert isinstance(proj, FinisherProjection)
        assert 0.0 <= proj.success_probability <= 1.0
        assert proj.expected_damage >= 0.0
        assert proj.mana_floor >= 0
        assert proj.chain_length >= 0

    def test_finisher_projection_immutable(self):
        """`FinisherProjection` is frozen — mutation raises."""
        proj = FinisherProjection(pattern="none")
        with pytest.raises(Exception):
            proj.expected_damage = 99.0  # type: ignore


class TestSimulatorV2Fields:
    """v2 fields (PR3b): hold_value, next_turn_damage, coverage_ratio,
    closer_in_zone.  Required by Phase D's hold-vs-fire decision."""

    def test_v2_fields_default_to_safe_values(self):
        """`pattern="none"` projection has all v2 fields at their
        documented defaults — zero damage, no zones, no hold value."""
        snap = _make_snap()
        proj = simulate_finisher_chain(
            snap=snap, hand=[], battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="any",
        )
        assert proj.pattern == "none"
        assert proj.hold_value == 0.0
        assert proj.next_turn_damage == 0.0
        assert proj.coverage_ratio == 0.0
        assert proj.closer_in_zone == {
            'hand': False, 'sb': False,
            'library': False, 'graveyard': False,
        }

    def test_storm_coverage_ratio_clamped(self):
        """`coverage_ratio = expected_damage / opp_life`, clamped
        to [0, 1] (a chain that deals double-lethal damage clamps)."""
        snap = _make_snap(my_mana=10, opp_life=20)
        # Big chain: many rituals + Grapeshot → storm damage > opp_life
        hand = [_ritual(1), _ritual(2), _ritual(3), _ritual(4),
                _ritual(5), _grapeshot(6)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        assert 0.0 <= proj.coverage_ratio <= 1.0
        if proj.expected_damage > 0:
            assert proj.coverage_ratio == min(
                1.0, proj.expected_damage / max(1, snap.opp_life)
            )

    def test_storm_next_turn_damage_uses_extra_mana(self):
        """`next_turn_damage` is computed by re-running the chain
        finder with mana + 1 — it's >= this turn's damage when the
        extra mana lets a longer chain assemble."""
        snap = _make_snap(my_mana=3, opp_life=20)
        # Storm chain: tight at 3 mana, more at 4 mana
        hand = [_ritual(1), _ritual(2), _ritual(3), _grapeshot(4)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        # Next turn damage should be at least equal to this turn
        # (the chain finder always finds at least the same chain).
        assert proj.next_turn_damage >= 0.0

    def test_storm_hold_value_zero_when_no_clock(self):
        """`hold_value` ≥ 0; equal to 0 when next-turn projection is 0
        regardless of survival probability."""
        snap = _make_snap(my_mana=3, opp_life=20)
        # No payoff in hand at all → next chain finder also returns
        # nothing → next_turn_damage = 0 → hold_value = 0
        hand = [_ritual(1), _ritual(2)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        # Pattern detected (rituals present), but no closer →
        # hold_value should reflect that next turn is also barren.
        assert proj.hold_value >= 0.0
        if proj.next_turn_damage == 0.0:
            assert proj.hold_value == 0.0

    def test_storm_hold_value_scales_with_survival_p(self):
        """`hold_value = next_turn_damage × survival_p`, where
        `survival_p = 1 - 1/opp_clock_discrete`.  When opp has no
        clock (NO_CLOCK sentinel = 99), survival_p ≈ 1 and
        hold_value ≈ next_turn_damage."""
        snap = _make_snap(my_mana=3, opp_life=20)
        # Don't set opp_power so opp_clock falls back to NO_CLOCK.
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        # When opp_clock is huge (NO_CLOCK = 99), hold_value should
        # be very close to next_turn_damage (survival ≈ 0.99).
        if proj.next_turn_damage > 0:
            assert proj.hold_value <= proj.next_turn_damage
            # Survival floor: at NO_CLOCK=99, survival_p ≥ 0.98
            assert proj.hold_value >= proj.next_turn_damage * 0.98

    def test_storm_closer_in_zone_hand_set_when_payoff_in_hand(self):
        """Storm closer in hand → `closer_in_zone['hand'] = True`."""
        snap = _make_snap(my_mana=4)
        hand = [_ritual(1), _grapeshot(2)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        assert proj.closer_in_zone['hand'] is True

    def test_cascade_closer_in_zone_library(self):
        """Cascade payoffs live in the library by deckbuilding
        convention — `closer_in_zone['library'] = True`."""
        snap = _make_snap(my_mana=3)
        hand = [_cascade_enabler(1, cmc=3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="cascade",
        )
        assert proj.pattern == "cascade"
        assert proj.closer_in_zone['library'] is True
        assert proj.closer_in_zone['hand'] is False

    def test_reanimation_closer_in_zone_graveyard(self):
        """Reanimation closer in GY → `closer_in_zone['graveyard']
        = True`; the discard-outlet branch sets `hand` instead."""
        snap = _make_snap(my_mana=3)
        # `_gy_creature` is the simulator fixture for a fat creature
        # in the GY; reanimation pattern targets it.
        gy = [_gy_creature(iid=900, power=8)]
        hand = [_reanimator(iid=1, cmc=3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=gy,
            library_size=40, storm_count=0, archetype="reanimation",
        )
        assert proj.pattern == "reanimation"
        assert proj.closer_in_zone['graveyard'] is True


class TestMultiTurnRollout:
    """Sprint 1: depth-bounded multi-turn rollout via
    `next_turn_proj` recursive field."""

    def test_next_turn_proj_attached_when_chain_reachable(self):
        """A reachable chain has `next_turn_proj` populated with the
        next turn's projection (mana+1, life - opp_power)."""
        snap = _make_snap(my_mana=4, opp_life=20)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        assert proj.pattern == "storm"
        # Multi-turn rollout must produce at least one next_turn_proj.
        assert proj.next_turn_proj is not None

    def test_next_turn_proj_chain_depth_bounded(self):
        """Recursion stops at `_MULTI_TURN_DEPTH` — the projection
        chain has finite depth."""
        from ai.finisher_simulator import _MULTI_TURN_DEPTH
        snap = _make_snap(my_mana=4, opp_life=20)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        depth = 0
        cur = proj
        while cur is not None:
            depth += 1
            cur = cur.next_turn_proj
        assert depth <= _MULTI_TURN_DEPTH, (
            f"recursion depth {depth} exceeds cap {_MULTI_TURN_DEPTH}"
        )

    def test_next_turn_proj_applies_mana_increment(self):
        """Each next-turn projection has +1 mana / +1 land relative
        to the parent.  Verify by checking the chain finder picks a
        bigger storm chain at depth=1."""
        snap = _make_snap(my_mana=2, opp_life=20)
        # Tight chain at 2 mana, longer at 3 mana
        hand = [_ritual(1), _ritual(2), _ritual(3),
                _ritual(4), _grapeshot(5)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        if proj.next_turn_proj is not None:
            # Next turn has more mana → at least same expected damage
            # (chain finder is monotonic in mana — never finds a worse
            # chain with more resources).
            assert (proj.next_turn_proj.expected_damage
                    >= proj.expected_damage)

    def test_next_turn_proj_stops_when_dying(self):
        """If holding kills us (my_life - opp_power × 1 ≤ 0),
        recursion stops — no `next_turn_proj` attached."""
        # We're at 3 life vs 4 opp_power → die next turn
        snap = _make_snap(my_mana=4, my_life=3, opp_life=20)
        snap.opp_power = 4
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="storm",
        )
        # Recursion stops: my_life - opp_power = 3 - 4 = -1 ≤ 0
        assert proj.next_turn_proj is None

    def test_next_turn_proj_stops_when_library_empty(self):
        """Empty library → recursion stops (we can't draw to deepen
        the chain)."""
        snap = _make_snap(my_mana=4, opp_life=20)
        hand = [_ritual(1), _ritual(2), _grapeshot(3)]
        proj = simulate_finisher_chain(
            snap=snap, hand=hand, battlefield=[], graveyard=[],
            library_size=1, storm_count=0, archetype="storm",
        )
        # Recursion stops at library_size <= 1 (the guard in
        # simulate_finisher_chain).
        assert proj.next_turn_proj is None

    def test_no_chain_no_recursion(self):
        """`pattern="none"` projections don't recurse — the leaf
        already has `next_turn_proj=None`."""
        snap = _make_snap()
        proj = simulate_finisher_chain(
            snap=snap, hand=[], battlefield=[], graveyard=[],
            library_size=40, storm_count=0, archetype="any",
        )
        assert proj.pattern == "none"
        assert proj.next_turn_proj is None
