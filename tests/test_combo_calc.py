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

    # ─── STORM finisher branch (Phase 2c.2-prep) ─────────────────

    def test_storm_finisher_lethal_now_fires_at_combo_value(self):
        """STORM keyword + storm+1 >= opp_life: return combo_value (commit to lethal)."""
        from engine.cards import Keyword as Kw
        a = ComboAssessment(
            resource_zone="storm", is_ready=True,
            payoff_value=0.9, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, _role_cache={"Grapeshot": "payoffs"},
        )
        card = MockCard(name="Grapeshot", template=MockTemplate(
            name="Grapeshot", keywords={Kw.STORM}))
        snap = _make_snap(opp_life=4)
        me = type('', (), {'spells_cast_this_turn': 4, 'hand': [], 'library': [None]*30,
                           'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        assert mod == 80.0  # storm+1 (5) >= opp_life (4): fire at full combo_value

    def test_storm_finisher_held_with_fuel_in_hand(self):
        """STORM finisher with fuel remaining: negative modifier (hold for more storm)."""
        from engine.cards import Keyword as Kw
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, _role_cache={"Grapeshot": "payoffs"},
        )
        card = MockCard(name="Grapeshot", instance_id=1, template=MockTemplate(
            name="Grapeshot", keywords={Kw.STORM}))
        # Three non-storm non-land cards in hand → 3 fuel
        fuel1 = MockCard(name="Pyretic Ritual", instance_id=2, template=MockTemplate(
            name="Pyretic Ritual", tags={'ritual'}))
        fuel2 = MockCard(name="Manamorphose", instance_id=3, template=MockTemplate(
            name="Manamorphose", tags={'cantrip'}))
        fuel3 = MockCard(name="Wish", instance_id=4, template=MockTemplate(
            name="Wish", tags={'tutor'}))
        snap = _make_snap(opp_life=20)
        me = type('', (), {'spells_cast_this_turn': 2, 'hand': [card, fuel1, fuel2, fuel3],
                           'library': [None]*30, 'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        # 3 fuel × 1/20 × 80.0 = -12.0
        assert mod == pytest.approx(-12.0)

    def test_storm_finisher_no_fuel_fires_at_partial(self):
        """STORM finisher with no fuel and not lethal: fire at (storm+1)/opp_life × combo_value."""
        from engine.cards import Keyword as Kw
        a = ComboAssessment(
            resource_zone="storm", is_ready=True,
            payoff_value=0.5, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, _role_cache={"Grapeshot": "payoffs"},
        )
        card = MockCard(name="Grapeshot", instance_id=1, template=MockTemplate(
            name="Grapeshot", keywords={Kw.STORM}))
        # Hand contains only the Grapeshot itself + a land (lands excluded from fuel)
        land = MockCard(name="Mountain", instance_id=2, template=MockTemplate(
            name="Mountain", is_sorcery=False, is_land=True))
        snap = _make_snap(opp_life=20)
        me = type('', (), {'spells_cast_this_turn': 6, 'hand': [card, land],
                           'library': [None]*30, 'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        # storm+1 (7) / opp_life (20) × combo_value (80.0) = 28.0
        assert mod == pytest.approx(28.0)

    # ─── COST REDUCER branch ─────────────────────────────────────

    def test_cost_reducer_returns_chain_improvement(self, monkeypatch):
        """Cost reducer with storm zone: return (dmg_with - dmg_without) / opp_life × combo_value."""
        # Mock find_all_chains to return controlled storm_damage values
        class _FakeChain:
            def __init__(self, dmg): self.storm_damage = dmg
        calls = {'count': 0}
        def fake_find(hand, mana, medallions, payoff_names, storm):
            calls['count'] += 1
            # First call (with): medallions+1 → 14 dmg
            # Second call (without): medallions → 8 dmg
            return [_FakeChain(14)] if calls['count'] == 1 else [_FakeChain(8)]
        monkeypatch.setattr('ai.combo_calc.find_all_chains', fake_find,
                             raising=False)
        # Need to monkeypatch the import inside card_combo_modifier
        import ai.combo_chain
        monkeypatch.setattr(ai.combo_chain, 'find_all_chains', fake_find)

        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, payoff_names={"Grapeshot"},
            _role_cache={"Ruby Medallion": "engines"},
        )
        card = MockCard(name="Ruby Medallion", instance_id=1, template=MockTemplate(
            name="Ruby Medallion", cmc=2, tags={'cost_reducer'}))
        snap = _make_snap(opp_life=20, my_mana=4)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [card],
                           'library': [None]*30, 'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        # (14 - 8) / 20 × 80.0 = 24.0
        assert mod == pytest.approx(24.0)

    def test_cost_reducer_floors_to_total_chain_when_no_improvement(self, monkeypatch):
        """When chains_with == chains_without (no improvement), use dmg_with as floor."""
        class _FakeChain:
            def __init__(self, dmg): self.storm_damage = dmg
        # Both calls return same damage → improvement = 0; floor kicks in
        def fake_find(*args, **kwargs):
            return [_FakeChain(10)]
        import ai.combo_chain
        monkeypatch.setattr(ai.combo_chain, 'find_all_chains', fake_find)

        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, payoff_names={"Grapeshot"},
            _role_cache={"Ruby Medallion": "engines"},
        )
        card = MockCard(name="Ruby Medallion", instance_id=1, template=MockTemplate(
            name="Ruby Medallion", cmc=2, tags={'cost_reducer'}))
        snap = _make_snap(opp_life=20, my_mana=4)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [card],
                           'library': [None]*30, 'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(card, a, snap, me, game, 0)
        # Floor: 10 / 20 × 80.0 = 40.0
        assert mod == pytest.approx(40.0)

    # ─── Ritual reducer-first heuristic ──────────────────────────

    def test_ritual_penalised_when_castable_reducer_in_hand(self, monkeypatch):
        """At storm=0 with a castable reducer in hand: penalise rituals to defer to reducer."""
        # Stub find_all_chains so the COST_REDUCER branch (which Ritual is NOT
        # routed through) is harmless if accidentally invoked.
        import ai.combo_chain
        monkeypatch.setattr(ai.combo_chain, 'find_all_chains',
                             lambda *a, **kw: [])
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True,
            _role_cache={"Pyretic Ritual": "rituals",
                         "Ruby Medallion": "engines"},
        )
        ritual = MockCard(name="Pyretic Ritual", instance_id=1, template=MockTemplate(
            name="Pyretic Ritual", cmc=2, tags={'ritual'}))
        # Reducer in hand, castable (cmc 2, my_mana 2)
        reducer = MockCard(name="Ruby Medallion", instance_id=2, template=MockTemplate(
            name="Ruby Medallion", cmc=2, is_sorcery=False,
            tags={'cost_reducer'}))
        # Two fuel spells (instant/sorcery, non-reducer, non-land)
        fuel1 = MockCard(name="Manamorphose", instance_id=3, template=MockTemplate(
            name="Manamorphose", cmc=2, tags={'cantrip'}))
        fuel2 = MockCard(name="Desperate Ritual", instance_id=4, template=MockTemplate(
            name="Desperate Ritual", cmc=2, tags={'ritual'}))
        snap = _make_snap(opp_life=20, my_mana=2)
        me = type('', (), {'spells_cast_this_turn': 0,
                           'hand': [ritual, reducer, fuel1, fuel2],
                           'library': [None]*30, 'graveyard': [], 'battlefield': []})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(ritual, a, snap, me, game, 0)
        # fuel_count = 2 (Manamorphose, Desperate Ritual; Ruby Medallion is reducer)
        # amplification_loss = 2 / 20 × 80.0 = 8.0
        assert mod == pytest.approx(-8.0)

    def test_ritual_no_reducer_first_penalty_when_reducer_already_deployed(self, monkeypatch):
        """When a reducer is already on the battlefield, ritual is not penalised for it."""
        import ai.combo_chain
        monkeypatch.setattr(ai.combo_chain, 'find_all_chains',
                             lambda *a, **kw: [])
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, r_res=5,  # above divergence threshold
            _role_cache={"Pyretic Ritual": "rituals"},
        )
        ritual = MockCard(name="Pyretic Ritual", instance_id=1, template=MockTemplate(
            name="Pyretic Ritual", cmc=2, tags={'ritual'}))
        # Reducer ON battlefield, not in hand
        deployed_reducer = MockCard(name="Ruby Medallion", instance_id=2,
                                     template=MockTemplate(
            name="Ruby Medallion", tags={'cost_reducer'}))
        snap = _make_snap(opp_life=20, my_mana=2)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [ritual],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': [deployed_reducer]})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(ritual, a, snap, me, game, 0)
        assert mod == 0.0  # has_payoff + reducer deployed + r_res>=3 → fall-through

    # ─── Golden-turn / divergence-point patience ─────────────────

    def test_ritual_patience_penalty_low_r_res_few_lands(self, monkeypatch):
        """At storm=0, has_payoff, low r_res, few lands → patience penalty."""
        import ai.combo_chain
        monkeypatch.setattr(ai.combo_chain, 'find_all_chains',
                             lambda *a, **kw: [])
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, r_res=1,  # well below divergence (3)
            _role_cache={"Pyretic Ritual": "rituals"},
        )
        ritual = MockCard(name="Pyretic Ritual", instance_id=1, template=MockTemplate(
            name="Pyretic Ritual", cmc=2, tags={'ritual'}))
        # Reducer already deployed → reducer-first branch skipped
        deployed_reducer = MockCard(name="Ruby Medallion", instance_id=2,
                                     template=MockTemplate(
            name="Ruby Medallion", tags={'cost_reducer'}))
        snap = _make_snap(opp_life=20, my_mana=2, my_total_lands=2)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [ritual],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': [deployed_reducer]})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(ritual, a, snap, me, game, 0)
        # divergence_gap = (3-1)/3 = 2/3
        # early_factor = (4-2)/4 = 0.5
        # patience_penalty = 2/3 × 0.5 × 80.0 × 0.2 ≈ 5.333
        assert mod == pytest.approx(-5.333, abs=0.01)

    def test_ritual_no_patience_penalty_when_r_res_at_divergence(self, monkeypatch):
        """At storm=0 with r_res >= 3 (divergence point): no patience penalty."""
        import ai.combo_chain
        monkeypatch.setattr(ai.combo_chain, 'find_all_chains',
                             lambda *a, **kw: [])
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True, r_res=3,  # exactly at divergence threshold
            _role_cache={"Pyretic Ritual": "rituals"},
        )
        ritual = MockCard(name="Pyretic Ritual", instance_id=1, template=MockTemplate(
            name="Pyretic Ritual", cmc=2, tags={'ritual'}))
        deployed_reducer = MockCard(name="Ruby Medallion", instance_id=2,
                                     template=MockTemplate(
            name="Ruby Medallion", tags={'cost_reducer'}))
        snap = _make_snap(opp_life=20, my_mana=2, my_total_lands=2)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [ritual],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': [deployed_reducer]})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(ritual, a, snap, me, game, 0)
        # r_res=3 → divergence_gap=0 → no patience penalty
        assert mod == 0.0

    # ─── Flip-transform stack batching ───────────────────────────

    def test_flip_transform_bonus_for_instant_with_flip_creature(self):
        """Cheap instant/sorcery gets bonus when an untransformed flip-coin creature exists."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=60.0, risk_discount=0.7,
            has_payoff=True,
            _role_cache={"Manamorphose": "fillers"},
        )
        # Flip-coin creature on battlefield (e.g., Storm-Kiln Artist style)
        flipper_template = MockTemplate(
            name="Coin Flipper", is_creature=True, is_sorcery=False,
            oracle_text=("Whenever you cast an instant or sorcery spell, "
                          "flip a coin. If you win the flip, transform Coin Flipper."))
        flipper = MockCard(name="Coin Flipper", instance_id=10,
                            template=flipper_template, zone="battlefield")
        # Mark untransformed via setattr (the function reads is_transformed)
        flipper.is_transformed = False
        spell = MockCard(name="Manamorphose", instance_id=1, template=MockTemplate(
            name="Manamorphose", is_sorcery=False, is_instant=True,
            tags={'cantrip'}))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [spell],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': [flipper]})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(spell, a, snap, me, game, 0)
        # marginal_p = 0.5^(0+1) = 0.5
        # transform_value = 60.0 × 0.3 = 18.0
        # bonus = 0.5 × 18.0 × 1 = 9.0
        assert mod == pytest.approx(9.0)

    def test_flip_transform_no_bonus_when_creature_already_transformed(self):
        """Already-transformed flip creatures grant no further bonus."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=60.0, risk_discount=0.7,
            has_payoff=True,
            _role_cache={"Manamorphose": "fillers"},
        )
        flipper_template = MockTemplate(
            name="Coin Flipper", is_creature=True, is_sorcery=False,
            oracle_text=("Whenever you cast an instant or sorcery spell, "
                          "flip a coin. If you win the flip, transform Coin Flipper."))
        flipper = MockCard(name="Coin Flipper", instance_id=10,
                            template=flipper_template, zone="battlefield")
        flipper.is_transformed = True  # already flipped
        spell = MockCard(name="Manamorphose", instance_id=1, template=MockTemplate(
            name="Manamorphose", is_sorcery=False, is_instant=True,
            tags={'cantrip'}))
        snap = _make_snap()
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [spell],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': [flipper]})()
        game = type('', (), {'players': [me, me], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(spell, a, snap, me, game, 0)
        assert mod == 0.0

    # ─── Search-tax penalty ──────────────────────────────────────

    def test_tutor_penalised_with_search_tax_permanent(self):
        """Tutor cards penalised when opp has 'whenever a player searches' permanent."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True,
            _role_cache={"Wish": "tutors"},
        )
        wish = MockCard(name="Wish", instance_id=1, template=MockTemplate(
            name="Wish", tags={'tutor'}))
        # Opp has Aven Mindcensor-style permanent
        mindcensor = MockCard(name="Aven Mindcensor", instance_id=99,
                               template=MockTemplate(
            name="Aven Mindcensor", is_creature=True, is_sorcery=False,
            oracle_text=("Flash. Flying. If an opponent would search a "
                          "library, that player searches the top four "
                          "cards of that library instead.")))
        snap = _make_snap(opp_life=20)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [wish],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': []})()
        opp = type('', (), {'spells_cast_this_turn': 0, 'hand': [],
                            'library': [None]*30, 'graveyard': [],
                            'battlefield': [mindcensor]})()
        game = type('', (), {'players': [me, opp], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(wish, a, snap, me, game, 0)
        # search_tax_count=1
        # card_value = 80.0 / 20 × 3.0 = 12.0
        # non_lethal_factor = 1 - 0.3 = 0.7
        # penalty = -1 × 12.0 × 0.7 = -8.4
        assert mod == pytest.approx(-8.4)

    def test_tutor_search_tax_attenuated_by_payoff_value(self):
        """When payoff_value approaches 1 (near-lethal), search-tax penalty fades to 0."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=True,
            payoff_value=1.0, combo_value=80.0, risk_discount=0.7,
            has_payoff=True,
            _role_cache={"Wish": "tutors"},
        )
        wish = MockCard(name="Wish", instance_id=1, template=MockTemplate(
            name="Wish", tags={'tutor'}))
        mindcensor = MockCard(name="Aven Mindcensor", instance_id=99,
                               template=MockTemplate(
            name="Aven Mindcensor", is_creature=True, is_sorcery=False,
            oracle_text=("If an opponent would search a library, that player "
                          "searches the top four cards instead.")))
        snap = _make_snap(opp_life=20)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [wish],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': []})()
        opp = type('', (), {'spells_cast_this_turn': 0, 'hand': [],
                            'library': [None]*30, 'graveyard': [],
                            'battlefield': [mindcensor]})()
        game = type('', (), {'players': [me, opp], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(wish, a, snap, me, game, 0)
        # non_lethal_factor = max(0, 1 - 1.0) = 0
        assert mod == 0.0

    def test_tutor_no_penalty_without_search_tax_permanent(self):
        """Without opp search-tax permanents, tutors get no penalty (0 modifier)."""
        a = ComboAssessment(
            resource_zone="storm", is_ready=False,
            payoff_value=0.3, combo_value=80.0, risk_discount=0.7,
            has_payoff=True,
            _role_cache={"Wish": "tutors"},
        )
        wish = MockCard(name="Wish", instance_id=1, template=MockTemplate(
            name="Wish", tags={'tutor'}))
        snap = _make_snap(opp_life=20)
        me = type('', (), {'spells_cast_this_turn': 0, 'hand': [wish],
                           'library': [None]*30, 'graveyard': [],
                           'battlefield': []})()
        opp = type('', (), {'spells_cast_this_turn': 0, 'hand': [],
                            'library': [None]*30, 'graveyard': [],
                            'battlefield': []})()
        game = type('', (), {'players': [me, opp], 'can_cast': lambda *a: True})()
        mod = card_combo_modifier(wish, a, snap, me, game, 0)
        assert mod == 0.0


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
