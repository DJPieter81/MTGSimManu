"""Generic Combo Resource Engine — derived scoring for all combo archetypes.

Wraps combo_chain.py arithmetic with position_value-derived scoring.
All values computed from game state — no arbitrary constants.

Zone-based dispatch: resource_zone from gameplan JSON determines which
assessment function runs. New combo archetypes add a zone assessor and
a gameplan JSON — no code changes needed.

Design principle: the combo engine doesn't know deck names. It reads
card_roles, resource_zone, and resource_target from gameplan JSON,
then uses tags/keywords/oracle to classify cards.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, List, Set, Dict

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot
    from ai.gameplan import GoalEngine
    from ai.bhi import BayesianHandTracker
    from ai.combo_chain import ChainOutcome


@dataclass
class ComboAssessment:
    """Unified combo evaluation — works for storm, graveyard, mana zones."""
    resource_zone: str = ""
    is_ready: bool = False
    payoff_value: float = 0.0         # expected_effect / opp_life (1.0 = lethal)
    resource_current: float = 0.0
    resource_target: float = 0.0
    has_payoff: bool = False
    has_enabler: bool = False
    combo_value: float = 1.0          # 100.0 - position_value(snap) (derived)
    risk_discount: float = 1.0        # 1.0 - P(counter) from BHI
    best_chain: Optional["ChainOutcome"] = None  # storm zone only
    payoff_names: Set[str] = field(default_factory=set)
    _role_cache: Dict[str, str] = field(default_factory=dict)
    reason: str = ""


def _null_assessment() -> ComboAssessment:
    return ComboAssessment(reason="no combo zone")


# ═══════════════════════════════════════════════════════════════
# Top-level entry — zone dispatch from gameplan data
# ═══════════════════════════════════════════════════════════════

def assess_combo(game: "GameState", player_idx: int,
                 goal_engine: "GoalEngine", snap: "EVSnapshot",
                 bhi: "BayesianHandTracker" = None) -> ComboAssessment:
    """Assess combo readiness. Dispatches on resource_zone from gameplan."""
    if not goal_engine or not goal_engine.gameplan:
        return _null_assessment()
    if goal_engine.on_fallback_plan:
        return _null_assessment()

    zone, target, min_cmc = _find_resource_zone(goal_engine)
    assessor = _ZONE_ASSESSORS.get(zone)
    if not assessor:
        return _null_assessment()

    me = game.players[player_idx]
    opp = game.players[1 - player_idx]
    return assessor(game, player_idx, goal_engine, snap, zone, target, min_cmc,
                    me, opp, bhi)


def _find_resource_zone(goal_engine):
    """Find resource_zone, target, min_cmc from any goal in the gameplan."""
    for goal in goal_engine.gameplan.goals:
        if goal.resource_target > 0:
            return goal.resource_zone, goal.resource_target, goal.resource_min_cmc
    return "graveyard", 0, 0


def _collect_payoff_names(goal_engine):
    """Gather payoff card names from all goals' card_roles."""
    names = set()
    for goal in goal_engine.gameplan.goals:
        names |= goal.card_roles.get('payoffs', set())
        names |= goal.card_roles.get('finishers', set())
    return names


def _build_role_cache(goal_engine):
    """Map card names to their combo roles from gameplan card_roles."""
    cache = {}
    for goal in goal_engine.gameplan.goals:
        for role, card_names in goal.card_roles.items():
            for name in card_names:
                if name not in cache:
                    cache[name] = role
    return cache


def _compute_combo_value(snap, archetype="combo"):
    """Position swing from winning: 100 - current position."""
    from ai.clock import position_value
    current_pos = position_value(snap, archetype)
    return max(1.0, 100.0 - current_pos)


def _compute_risk_discount(bhi, opp):
    """Discount factor from BHI counter probability."""
    if not bhi or not bhi._initialized:
        return 1.0
    p_counter = bhi.get_counter_probability()
    opp_mana = len(getattr(opp, 'untapped_lands', [])) + getattr(
        opp, 'mana_pool', _NullPool()).total()
    if opp_mana == 0:
        p_free = getattr(bhi.beliefs, 'p_free_counter', 0.0) if bhi.beliefs else 0.0
        return 1.0 - p_free
    return 1.0 - p_counter


class _NullPool:
    def total(self):
        return 0


# ═══════════════════════════════════════════════════════════════
# Zone assessors — one per resource_zone type
# ═══════════════════════════════════════════════════════════════

def _assess_storm_zone(game, player_idx, goal_engine, snap, zone, target,
                       min_cmc, me, opp, bhi):
    """Storm zone: wraps find_all_chains() from combo_chain.py."""
    from ai.combo_chain import find_all_chains, what_is_missing

    payoff_names = _collect_payoff_names(goal_engine)
    medallions = sum(1 for c in me.battlefield
                     if 'cost_reducer' in getattr(c.template, 'tags', set()))
    mana = snap.my_mana
    storm = me.spells_cast_this_turn

    chains = find_all_chains(me.hand, mana, medallions, payoff_names, storm)
    best = max(chains, key=lambda c: c.storm_damage, default=None)
    opp_life = max(1, snap.opp_life)
    best_damage = best.storm_damage if best else 0
    payoff_value = best_damage / opp_life

    missing = what_is_missing(me.hand, mana, medallions, payoff_names)

    combo_value = _compute_combo_value(snap, "storm")
    risk_discount = _compute_risk_discount(bhi, opp)

    is_ready = (payoff_value >= 1.0
                or (missing['has_payoff'] and best_damage > 0
                    and storm + best_damage >= opp_life))

    return ComboAssessment(
        resource_zone=zone,
        is_ready=is_ready,
        payoff_value=payoff_value,
        resource_current=storm,
        resource_target=target,
        has_payoff=missing['has_payoff'],
        has_enabler=missing['has_fuel'] or missing['reducer_deployed'],
        combo_value=combo_value,
        risk_discount=risk_discount,
        best_chain=best,
        payoff_names=payoff_names,
        _role_cache=_build_role_cache(goal_engine),
        reason=f"storm: best_dmg={best_damage}, opp_life={opp_life}",
    )


def _assess_graveyard_zone(game, player_idx, goal_engine, snap, zone, target,
                           min_cmc, me, opp, bhi):
    """Graveyard zone: creature power in GY. min_cmc distinguishes patterns."""
    opp_life = max(1, snap.opp_life)
    payoff_names = _collect_payoff_names(goal_engine)

    gy_creatures = [c for c in me.graveyard
                    if c.template.is_creature
                    and (c.template.cmc or 0) >= min_cmc]

    if min_cmc >= 5:
        # Single big target pattern (reanimate)
        best_power = max((c.power or 0 for c in gy_creatures), default=0)
        payoff_value = best_power / opp_life
    else:
        # Mass reanimation pattern (cascade → Living End)
        total_power = sum(c.power or 0 for c in gy_creatures)
        payoff_value = total_power / opp_life

    resource_current = len(gy_creatures)
    has_payoff = any(c.name in payoff_names for c in me.hand)

    # Check enablers (discard/entomb effects) from current goal
    enabler_names = set()
    for goal in goal_engine.gameplan.goals:
        enabler_names |= goal.card_roles.get('enablers', set())
    has_enabler = any(c.name in enabler_names for c in me.hand)

    combo_value = _compute_combo_value(snap, "combo")
    risk_discount = _compute_risk_discount(bhi, opp)

    is_ready = (resource_current >= target and has_payoff
                and snap.my_mana >= min(
                    (c.template.cmc or 99 for c in me.hand if c.name in payoff_names),
                    default=99))

    return ComboAssessment(
        resource_zone=zone,
        is_ready=is_ready,
        payoff_value=payoff_value,
        resource_current=resource_current,
        resource_target=target,
        has_payoff=has_payoff,
        has_enabler=has_enabler,
        combo_value=combo_value,
        risk_discount=risk_discount,
        best_chain=None,
        payoff_names=payoff_names,
        _role_cache=_build_role_cache(goal_engine),
        reason=f"gy: creatures={resource_current}/{target}, pv={payoff_value:.2f}",
    )


def _assess_mana_zone(game, player_idx, goal_engine, snap, zone, target,
                      min_cmc, me, opp, bhi):
    """Mana zone: can we afford the payoff? Payoff value from creature power."""
    opp_life = max(1, snap.opp_life)
    payoff_names = _collect_payoff_names(goal_engine)

    # Check payoff cards in hand
    payoff_cards = [c for c in me.hand if c.name in payoff_names]
    best_power = max((c.power or 0 for c in payoff_cards
                      if c.template.is_creature), default=0)
    payoff_value = best_power / opp_life

    # Mana available (untapped lands + pool)
    mana_available = snap.my_mana
    has_payoff = len(payoff_cards) > 0

    # Check engines from gameplan
    engine_names = set()
    for goal in goal_engine.gameplan.goals:
        engine_names |= goal.card_roles.get('engines', set())
    has_enabler = any(c.name in engine_names for c in me.battlefield)

    combo_value = _compute_combo_value(snap, "combo")
    risk_discount = _compute_risk_discount(bhi, opp)

    is_ready = (mana_available >= target and has_payoff)

    return ComboAssessment(
        resource_zone=zone,
        is_ready=is_ready,
        payoff_value=payoff_value,
        resource_current=mana_available,
        resource_target=target,
        has_payoff=has_payoff,
        has_enabler=has_enabler,
        combo_value=combo_value,
        risk_discount=risk_discount,
        best_chain=None,
        payoff_names=payoff_names,
        _role_cache=_build_role_cache(goal_engine),
        reason=f"mana: {mana_available}/{target}, payoff_power={best_power}",
    )


# Zone dispatch table
_ZONE_ASSESSORS = {
    "storm": _assess_storm_zone,
    "graveyard": _assess_graveyard_zone,
    "mana": _assess_mana_zone,
}


# ═══════════════════════════════════════════════════════════════
# Per-card modifier — role-based, no deck names
# ═══════════════════════════════════════════════════════════════

def card_combo_role(card, assessment):
    """Classify card's combo role from gameplan roles, fallback to tags."""
    # 1. Gameplan roles
    if assessment._role_cache and card.name in assessment._role_cache:
        role = assessment._role_cache[card.name]
        # Normalize gameplan role names to combo categories
        if role in ('payoffs', 'finishers'):
            return 'payoff'
        if role in ('rituals',):
            return 'fuel'
        if role in ('engines',):
            return 'engine'
        if role in ('enablers',):
            return 'enabler'
        if role in ('fillers', 'fuel'):
            return 'dig'
        if role in ('protection', 'interaction'):
            return 'other'
        return role

    # 2. Fallback to tags/keywords
    tags = getattr(card.template, 'tags', set())
    from engine.cards import Keyword
    kws = getattr(card.template, 'keywords', set())
    if Keyword.STORM in kws or Keyword.CASCADE in kws:
        return 'payoff'
    if 'tutor' in tags:
        return 'payoff'
    if 'ritual' in tags:
        return 'fuel'
    if 'cantrip' in tags or 'draw' in tags:
        return 'dig'
    if 'cost_reducer' in tags:
        return 'engine'
    if 'flashback' in tags and 'combo' in tags:
        return 'enabler'
    return 'other'


def card_combo_modifier(card, assessment, snap, me, game, player_idx):
    """Per-card combo modifier derived from ComboAssessment.

    All values computed from assessment.combo_value (position swing)
    and assessment.payoff_value (expected effect / opp_life).
    No arbitrary constants.
    """
    from engine.cards import Keyword as Kw
    from ai.clock import mana_clock_impact

    a = assessment
    if not a or not a.resource_zone:
        return 0.0

    tags = getattr(card.template, 'tags', set())
    opp_life = max(1, snap.opp_life)
    storm = me.spells_cast_this_turn
    role = card_combo_role(card, a)

    # ── PAYOFF: fire when ready, hold when not ──
    if role == 'payoff':
        if Kw.STORM in getattr(card.template, 'keywords', set()):
            # Storm payoffs: hold if castable fuel remains (maximize storm)
            fuel = sum(1 for c in me.hand
                       if c.instance_id != card.instance_id
                       and not c.template.is_land
                       and game.can_cast(player_idx, c)
                       and Kw.STORM not in getattr(c.template, 'keywords', set()))
            if storm + 1 >= opp_life:
                return a.combo_value  # lethal
            if fuel > 0:
                # Each fuel adds 1 damage = 1/opp_life of a kill
                return -fuel / opp_life * a.combo_value
            # No fuel left — fire now
            return (storm + 1) / opp_life * a.combo_value

        if a.is_ready:
            # Non-storm payoff (cascade, reanimate): fire now
            return a.payoff_value * a.combo_value * a.risk_discount
        else:
            # Not ready: penalty = wasted potential
            # (what we'd get at target - what we get now) × combo_value
            potential = a.resource_target / opp_life if a.resource_target > 0 else 0.5
            current = a.payoff_value
            wasted = max(0.01, potential - current)
            return -wasted * a.combo_value

    # ── FUEL (rituals): only when chain is viable ──
    if role == 'fuel':
        if storm == 0:
            # Storm=0: deterministic gate
            if a.resource_zone == "storm" and a.best_chain:
                # Viable chain exists — card's marginal contribution
                mv = _card_marginal_value(card, me, snap, a)
                # Ensure positive when chain exists (card enables the chain)
                return max(mv, a.combo_value / opp_life) * a.risk_discount
            if not a.has_payoff:
                return -a.combo_value / opp_life
            if snap.am_dead_next:
                return a.combo_value / opp_life
            # Has payoff but no viable chain — ritual may unlock one
            # Small positive: lets rituals compete with other plays
            return a.combo_value / (opp_life * opp_life) * a.risk_discount
        # Mid-chain: block if no payoff access
        if not a.has_payoff and not a.has_enabler:
            if not snap.am_dead_next:
                return -a.combo_value / opp_life
        return a.payoff_value * a.combo_value / opp_life

    # ── ENGINE (cost reducer, Amulet-type): deploy for chain improvement ──
    if role == 'engine':
        if a.resource_zone == "storm" and a.best_chain:
            return _card_marginal_value(card, me, snap, a)
        return mana_clock_impact(snap) * a.combo_value

    # ── ENABLER (GY access, discard outlets): progress toward threshold ──
    if role == 'enabler':
        if a.resource_zone == "graveyard":
            gap = max(0, a.resource_target - a.resource_current)
            return gap / max(1, a.resource_target) * a.combo_value / opp_life
        return a.payoff_value * a.combo_value / opp_life

    # ── DIG (cantrips): value from advancing toward combo ──
    if role == 'dig':
        opp_creatures = getattr(game.players[1 - player_idx], 'creatures', [])
        has_punisher = any(
            'draw' in (c.template.oracle_text or '').lower()
            and 'opponent' in (c.template.oracle_text or '').lower()
            and 'damage' in (c.template.oracle_text or '').lower()
            for c in opp_creatures)
        # Dig value scales with how far from ready we are
        # Missing pieces → each draw is more valuable
        resource_gap = max(0, a.resource_target - a.resource_current)
        if not a.has_payoff:
            resource_gap += 1  # missing payoff = extra urgency
        # Value = gap_fraction * combo_value / opp_life
        # This gives meaningful positive values (not just p_find)
        gap_fraction = resource_gap / max(1, a.resource_target) if a.resource_target > 0 else 0.5
        dig_value = gap_fraction * a.combo_value / opp_life
        return -dig_value if has_punisher else dig_value

    # ── FLASHBACK COMBO (PiF-style): GY fuel value ──
    if 'flashback' in tags and 'combo' in tags and card.template.is_sorcery:
        if card.zone == "graveyard":
            return -a.combo_value / opp_life
        gy_fuel = sum(1 for c in me.graveyard
                      if (c.template.is_instant or c.template.is_sorcery)
                      and any(ft in getattr(c.template, 'tags', set())
                              for ft in ('ritual', 'cantrip')))
        if storm == 0:
            return -gy_fuel / opp_life * a.combo_value / opp_life
        if gy_fuel < 2:
            return -a.combo_value / (opp_life * opp_life)
        return gy_fuel / opp_life * a.combo_value

    # ── TUTOR: value of finding missing piece ──
    if 'tutor' in tags:
        if storm == 0:
            return -a.combo_value / (opp_life * opp_life)
        return (storm + 1) / opp_life * a.combo_value * a.risk_discount

    return 0.0


def _card_marginal_value(card, me, snap, assessment):
    """How much does this card improve the best storm chain?"""
    from ai.combo_chain import find_all_chains

    a = assessment
    opp_life = max(1, snap.opp_life)
    hand_without = [c for c in me.hand if c.instance_id != card.instance_id]
    medallions = sum(1 for c in me.battlefield
                     if 'cost_reducer' in getattr(c.template, 'tags', set()))

    chains_without = find_all_chains(hand_without, snap.my_mana, medallions,
                                     a.payoff_names, me.spells_cast_this_turn)
    best_without = max(chains_without, key=lambda c: c.storm_damage, default=None)

    dmg_with = a.best_chain.storm_damage if a.best_chain else 0
    dmg_without = best_without.storm_damage if best_without else 0

    return (dmg_with - dmg_without) / opp_life * a.combo_value
