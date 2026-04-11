"""Tests for the derived combo resource engine (ai/combo_calc.py).

Tests cover:
- Zone assessors (storm, graveyard, mana)
- Card role classification
- Per-card combo modifier derivation
- ComboAssessment properties
"""
import pytest
from dataclasses import dataclass, field
from typing import Optional, Set, List
from ai.ev_evaluator import EVSnapshot
from ai.combo_calc import (
    ComboAssessment, assess_combo, card_combo_modifier, card_combo_role,
    _compute_combo_value, _compute_risk_discount, _null_assessment,
    _find_resource_zone, _collect_payoff_names, _build_role_cache,
)


# ─── Helpers ──────────────────────────────────────────────────

def _make_snap(opp_life=20, my_mana=3, **kwargs):
    """Build a minimal EVSnapshot for testing."""
    defaults = dict(
        my_life=20, opp_life=opp_life, my_power=0, opp_power=0,
        my_toughness=0, opp_toughness=0, my_creature_count=0,
        opp_creature_count=0, my_hand_size=5, opp_hand_size=5,
        my_mana=my_mana, opp_mana=2, my_total_lands=3, opp_total_lands=3,
        turn_number=3, storm_count=0, my_gy_creatures=0, my_energy=0,
        my_evasion_power=0, my_lifelink_power=0, opp_evasion_power=0,
        cards_drawn_this_turn=0,
    )
    defaults.update(kwargs)
    return EVSnapshot(**defaults)


@dataclass
class MockTemplate:
    name: str = "Test Card"
    cmc: int = 1
    is_instant: bool = False
    is_sorcery: bool = True
    is_land: bool = False
    is_creature: bool = False
    oracle_text: str = ""
    tags: Set[str] = field(default_factory=set)
    keywords: Set[str] = field(default_factory=set)
    color_identity: Set = field(default_factory=set)
    has_flash: bool = False
    ritual_mana: Optional[tuple] = None
    domain_reduction: int = 0
    card_types: Set = field(default_factory=set)
    power: Optional[int] = None
    toughness: Optional[int] = None
    x_cost_data: Optional[dict] = None


@dataclass
class MockCard:
    name: str = "Test Card"
    instance_id: int = 0
    template: MockTemplate = field(default_factory=MockTemplate)
    zone: str = "hand"
    power: Optional[int] = None
    toughness: Optional[int] = None
    other_counters: dict = field(default_factory=dict)


@dataclass
class MockGoal:
    goal_type: str = "EXECUTE_PAYOFF"
    description: str = "test"
    card_roles: dict = field(default_factory=dict)
    resource_target: int = 0
    resource_zone: str = "graveyard"
    resource_min_cmc: int = 0
    card_priorities: dict = field(default_factory=dict)
    transition_check: str = None
    min_turns: int = 0
    prefer_cycling: bool = False
    hold_mana: bool = False
    dig_roles: set = None
    hold_roles: set = None


@dataclass
class MockGameplan:
    goals: list = field(default_factory=list)
    reactive_only: set = field(default_factory=set)


@dataclass
class MockGoalEngine:
    gameplan: MockGameplan = field(default_factory=MockGameplan)
    current_goal_idx: int = 0
    on_fallback_plan: bool = False

    @property
    def current_goal(self):
        if self.current_goal_idx < len(self.gameplan.goals):
            return self.gameplan.goals[self.current_goal_idx]
        return self.gameplan.goals[-1]


# ─── ComboAssessment basics ──────────────────────────────────

class TestComboAssessmentBasics:
    def test_null_assessment(self):
        a = _null_assessment()
        assert not a.is_ready
        assert a.payoff_value == 0.0
        assert a.combo_value == 1.0

    def test_combo_value_derived_from_position(self):
        """combo_value should be higher when losing (low position)."""
        snap_losing = _make_snap(my_life=5, opp_power=8)
        snap_winning = _make_snap(my_power=10, opp_life=5)
        cv_losing = _compute_combo_value(snap_losing)
        cv_winning = _compute_combo_value(snap_winning)
        assert cv_losing > cv_winning

    def test_combo_value_minimum_floor(self):
        """combo_value should never go below 1.0."""
        snap = _make_snap(my_power=20, opp_life=1)
        cv = _compute_combo_value(snap)
        assert cv >= 1.0


# ─── Resource zone finding ────────────────────────────────────

class TestResourceZoneFinding:
    def test_finds_storm_zone(self):
        goal = MockGoal(resource_zone="storm", resource_target=5)
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
        zone, target, min_cmc = _find_resource_zone(ge)
        assert zone == "storm"
        assert target == 5

    def test_finds_graveyard_zone(self):
        goal = MockGoal(resource_zone="graveyard", resource_target=2, resource_min_cmc=0)
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
        zone, target, min_cmc = _find_resource_zone(ge)
        assert zone == "graveyard"
        assert min_cmc == 0

    def test_finds_mana_zone(self):
        goal = MockGoal(resource_zone="mana", resource_target=6)
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
        zone, target, min_cmc = _find_resource_zone(ge)
        assert zone == "mana"

    def test_default_when_no_target(self):
        goal = MockGoal(resource_target=0)
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
        zone, target, _ = _find_resource_zone(ge)
        assert zone == "graveyard"
        assert target == 0


# ─── Payoff name collection ──────────────────────────────────

class TestPayoffNames:
    def test_collects_from_payoffs_role(self):
        goal = MockGoal(card_roles={"payoffs": {"Grapeshot", "Empty the Warrens"}})
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
        names = _collect_payoff_names(ge)
        assert "Grapeshot" in names
        assert "Empty the Warrens" in names

    def test_collects_from_finishers_role(self):
        goal = MockGoal(card_roles={"finishers": {"Grapeshot"}})
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal]))
        names = _collect_payoff_names(ge)
        assert "Grapeshot" in names


# ─── Card role classification ─────────────────────────────────

class TestCardComboRole:
    def test_role_from_gameplan(self):
        a = ComboAssessment(
            _role_cache={"Desperate Ritual": "rituals", "Grapeshot": "payoffs"}
        )
        ritual = MockCard(name="Desperate Ritual", template=MockTemplate(name="Desperate Ritual"))
        assert card_combo_role(ritual, a) == 'fuel'

        payoff = MockCard(name="Grapeshot", template=MockTemplate(name="Grapeshot"))
        assert card_combo_role(payoff, a) == 'payoff'

    def test_role_fallback_storm_keyword(self):
        from engine.cards import Keyword
        a = ComboAssessment()
        card = MockCard(name="Something", template=MockTemplate(
            name="Something", keywords={Keyword.STORM}))
        assert card_combo_role(card, a) == 'payoff'

    def test_role_fallback_ritual_tag(self):
        a = ComboAssessment()
        card = MockCard(name="Pyretic Ritual", template=MockTemplate(
            name="Pyretic Ritual", tags={'ritual'}))
        assert card_combo_role(card, a) == 'fuel'

    def test_role_fallback_cantrip_tag(self):
        a = ComboAssessment()
        card = MockCard(name="Opt", template=MockTemplate(
            name="Opt", tags={'cantrip'}))
        assert card_combo_role(card, a) == 'dig'

    def test_role_fallback_cascade_keyword(self):
        from engine.cards import Keyword
        a = ComboAssessment()
        card = MockCard(name="Cascade Spell", template=MockTemplate(
            name="Cascade Spell", keywords={Keyword.CASCADE}))
        assert card_combo_role(card, a) == 'payoff'


# ─── card_combo_modifier ─────────────────────────────────────

class TestCardComboModifier:
    def test_payoff_cascade_no_modifier_when_ready(self):
        """Cascade payoff gets 0 modifier when ready (projection handles value)."""
        from engine.cards import Keyword
        a = ComboAssessment(
            resource_zone="graveyard", is_ready=True,
            payoff_value=0.5, combo_value=80.0, risk_discount=0.8,
            has_payoff=True, _role_cache={"Cascade Spell": "payoffs"},
        )
        card = MockCard(name="Cascade Spell", template=MockTemplate(
            name="Cascade Spell", keywords={Keyword.CASCADE}))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [], 'library': [None]*30,
                           'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        assert mod == 0.0  # projection handles cascade payoff value

    def test_non_storm_payoff_held_when_not_ready(self):
        """Non-cascade, non-storm payoff gets negative modifier when not ready."""
        a = ComboAssessment(
            resource_zone="graveyard", is_ready=False,
            payoff_value=0.1, combo_value=80.0, risk_discount=0.8,
            resource_target=2,
            has_payoff=True, _role_cache={"Reanimate Spell": "payoffs"},
        )
        card = MockCard(name="Reanimate Spell", template=MockTemplate(
            name="Reanimate Spell"))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [], 'library': [None]*30,
                           'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        assert mod < 0

    def test_fuel_blocked_without_payoff(self):
        """Fuel should be blocked when no payoff exists."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.0, combo_value=80.0, risk_discount=1.0,
            has_payoff=False, has_enabler=False,
            _role_cache={"Desperate Ritual": "rituals"},
        )
        card = MockCard(name="Desperate Ritual", template=MockTemplate(
            name="Desperate Ritual", tags={'ritual'}))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [], 'library': [None]*30,
                           'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        assert mod < 0

    def test_cantrip_no_modifier(self):
        """Cantrips get 0 modifier — projection handles draw value."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.0, combo_value=80.0,
            _role_cache={"Opt": "fillers"},
        )
        card = MockCard(name="Opt", template=MockTemplate(
            name="Opt", tags={'cantrip'}))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [], 'library': [None]*30,
                           'graveyard': [], 'battlefield': []})()
        opp_player = type('', (), {'creatures': []})()
        game = type('', (), {'players': [me, opp_player]})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        assert mod == 0.0  # let projection handle cantrip value

    def test_null_assessment_returns_zero(self):
        """Null assessment should always return 0."""
        a = _null_assessment()
        card = MockCard(name="Any", template=MockTemplate(name="Any"))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [], 'library': [],
                           'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me]})()
        assert card_combo_modifier(card, a, snap, me, game, 0) == 0.0


# ─── Risk discount ────────────────────────────────────────────

class TestRiskDiscount:
    def test_no_bhi_full_discount(self):
        """Without BHI, risk discount should be 1.0."""
        assert _compute_risk_discount(None, None) == 1.0

    def test_counter_probability_reduces_discount(self):
        """Higher P(counter) should lower risk_discount."""
        class MockPool:
            def total(self):
                return 0
        class MockBHI:
            _initialized = True
            beliefs = type('', (), {'p_free_counter': 0.0})()
            def get_counter_probability(self):
                return 0.4
        class MockOpp:
            untapped_lands = [1, 2]
            mana_pool = MockPool()

        rd = _compute_risk_discount(MockBHI(), MockOpp())
        assert rd == pytest.approx(0.6)

    def test_tapped_out_uses_free_counter(self):
        """When tapped out, only free counter probability matters."""
        class MockPool:
            def total(self):
                return 0
        class MockBHI:
            _initialized = True
            beliefs = type('', (), {'p_free_counter': 0.2})()
            def get_counter_probability(self):
                return 0.5  # high regular counter
        class MockOpp:
            untapped_lands = []
            mana_pool = MockPool()

        rd = _compute_risk_discount(MockBHI(), MockOpp())
        assert rd == pytest.approx(0.8)  # only 0.2 free counter matters


# ─── Role cache building ─────────────────────────────────────

class TestRoleCache:
    def test_builds_from_goals(self):
        goal1 = MockGoal(card_roles={
            "payoffs": {"Grapeshot"}, "rituals": {"Pyretic Ritual"}
        })
        goal2 = MockGoal(card_roles={
            "enablers": {"Past in Flames"}
        })
        ge = MockGoalEngine(gameplan=MockGameplan(goals=[goal1, goal2]))
        cache = _build_role_cache(ge)
        assert cache["Grapeshot"] == "payoffs"
        assert cache["Pyretic Ritual"] == "rituals"
        assert cache["Past in Flames"] == "enablers"
