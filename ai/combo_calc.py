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

from ai.scoring_constants import (
    COMBO_IDEAL_POSITION_CEIL,
    COMBO_DIVERGENCE_RES_THRESHOLD,
    COMBO_EARLY_GAME_LAND_THRESHOLD,
    COMBO_PATIENCE_PENALTY_SCALE,
    COMBO_NON_READY_POTENTIAL_FALLBACK,
    COMBO_RITUAL_MISSED_FINISHER_SCALE,
    COMBO_CASCADE_RISK_SCALE,
    COMBO_FLIP_TRANSFORM_VALUE_FRACTION,
    COMBO_SEARCH_TAX_CARD_SCALE,
    COMBO_HALF_LETHAL_FRACTION,
    COMBO_MIN_CHAIN_DEPTH,
    COMBO_CASCADE_DRAW_FLOOR,
)

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
    r_res: float = 0.0               # chain-reaction residue (mana surplus)


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
    """Map card names to their combo roles from gameplan card_roles.

    Priority order: rituals > payoffs > engines > enablers > fillers.
    This ensures cards appearing in multiple roles get the most
    specific classification (e.g. Desperate Ritual in both 'enablers'
    and 'rituals' gets 'rituals').
    """
    _ROLE_PRIORITY = {
        'rituals': 0, 'payoffs': 1, 'finishers': 1, 'engines': 2,
        'enablers': 3, 'protection': 4, 'interaction': 5, 'fillers': 6,
        'fuel': 0,
    }
    cache = {}
    for goal in goal_engine.gameplan.goals:
        for role, card_names in goal.card_roles.items():
            for name in card_names:
                existing_priority = _ROLE_PRIORITY.get(cache.get(name, ''), 99)
                new_priority = _ROLE_PRIORITY.get(role, 50)
                if new_priority < existing_priority:
                    cache[name] = role
    return cache


def _compute_combo_value(snap, archetype="combo"):
    """Position swing from winning: COMBO_IDEAL_POSITION_CEIL - current position."""
    from ai.clock import position_value
    current_pos = position_value(snap, archetype)
    return max(1.0, COMBO_IDEAL_POSITION_CEIL - current_pos)


def _compute_risk_discount(bhi, opp):
    """Discount factor from BHI disruption probabilities.

    Combines the existing counter-spell prior with a discard prior
    sourced from the opp's published gameplan (see
    `BayesianHandTracker._compute_discard_prior`). The discard term
    is multiplied by `_DISCARD_HIT_RATE` - the documented
    expectation that a discard spell, when cast, removes one of our
    critical pieces ~50% of the time (the opp picks rationally from
    our revealed hand). Effective risk = max(P(counter), P(discard)
    * _DISCARD_HIT_RATE), so whichever disruption vector is more
    likely dominates.

    Returns a value in [0, 1] - higher means safer.
    """
    if not bhi or not bhi._initialized:
        return 1.0
    p_counter = bhi.get_counter_probability()
    p_discard = (bhi.get_discard_probability()
                 if hasattr(bhi, 'get_discard_probability') else 0.0)
    effective_discard = p_discard * _DISCARD_HIT_RATE
    opp_mana = len(getattr(opp, 'untapped_lands', [])) + getattr(
        opp, 'mana_pool', _NullPool()).total()
    if opp_mana == 0:
        p_free = getattr(bhi.beliefs, 'p_free_counter', 0.0) if bhi.beliefs else 0.0
        # Discard is sorcery-speed, but the opp casts it on their
        # own turn before we go off - it still threatens our chain
        # even when they're tapped out for instant-speed answers.
        return 1.0 - max(p_free, effective_discard)
    return 1.0 - max(p_counter, effective_discard)


# Documented hit-rate prior. Given the opp casts a discard spell
# (Thoughtseize / Inquisition / etc.), how often does it actually
# strip one of our critical_pieces? Empirically ~50% - the opp sees
# our hand and picks rationally, but is constrained by what's
# revealed. This is the "given they have it, given they cast it"
# conditional probability that the chain actually loses a key piece.
_DISCARD_HIT_RATE = 0.5


class _NullPool:
    def total(self):
        return 0


# ═══════════════════════════════════════════════════════════════
# Zone assessors — one per resource_zone type
# ═══════════════════════════════════════════════════════════════

def _assess_storm_zone(game, player_idx, goal_engine, snap, zone, target,
                       min_cmc, me, opp, bhi):
    """Storm zone: wraps find_all_chains() + R_res calculation.

    R_res = (mana + ritual_mana) - (spell_costs - η × count)
    When R_res >= 3 and have draw spells, projected storm includes
    expected draws × fuel density in library.
    """
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

    # ── Hypothetical reducer deploy: what if we cast a reducer first? ──
    # If a cost reducer is in hand and affordable, try chains with it deployed.
    # The better chain (with or without reducer) is used. No card names —
    # detected from cost_reducer tag on non-instant non-sorcery cards.
    reducer_in_hand = [c for c in me.hand
                       if 'cost_reducer' in getattr(c.template, 'tags', set())
                       and not c.template.is_instant and not c.template.is_sorcery]
    if reducer_in_hand:
        cheapest_cmc = min(c.template.cmc or 99 for c in reducer_in_hand)
        if mana >= cheapest_cmc:
            # Remove the cheapest reducer from hand, subtract its cost, add 1 medallion
            cheapest = min(reducer_in_hand, key=lambda c: c.template.cmc or 99)
            hand_after = [c for c in me.hand if c.instance_id != cheapest.instance_id]
            mana_after = mana - cheapest_cmc
            chains_with = find_all_chains(hand_after, mana_after, medallions + 1,
                                          payoff_names, storm + 1)
            best_with = max(chains_with, key=lambda c: c.storm_damage, default=None)
            if best_with and best_with.storm_damage > best_damage:
                best = best_with
                best_damage = best_with.storm_damage

    missing = what_is_missing(me.hand, mana, medallions, payoff_names)

    # ── R_res: chain-reaction residue ──
    # Compute available resources vs costs for the entire hand
    r_res = _compute_r_res(me.hand, mana, medallions)

    # ── Projected damage including expected draws ──
    # If chain draws cards (cantrips), estimate additional storm from drawn fuel
    projected_damage = best_damage
    if best and best.cards_drawn > 0 and len(me.library) > 0:
        # Fuel density: what fraction of library is castable fuel?
        # Uses `predicates.is_chain_fuel` for the tag check (ritual /
        # cantrip / draw / card_advantage).
        from ai.predicates import is_chain_fuel
        fuel_in_library = sum(1 for c in me.library
                              if not c.template.is_land
                              and is_chain_fuel(c))
        fuel_density = fuel_in_library / max(1, len(me.library))
        # Each draw finds fuel with probability fuel_density
        # Each fuel spell adds ~1 storm (and net-positive rituals add mana for more)
        expected_extra = best.cards_drawn * fuel_density
        # If R_res >= COMBO_DIVERGENCE_RES_THRESHOLD (mana surplus),
        # drawn fuel can be cast.
        if r_res >= COMBO_DIVERGENCE_RES_THRESHOLD:
            projected_damage += int(expected_extra * 2)  # fuel chains into more fuel
        elif r_res >= 0:
            projected_damage += int(expected_extra)

    payoff_value = projected_damage / opp_life

    combo_value = _compute_combo_value(snap, "storm")
    risk_discount = _compute_risk_discount(bhi, opp)

    is_ready = (projected_damage >= opp_life
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
        reason=f"storm: best_dmg={best_damage} proj={projected_damage} "
               f"r_res={r_res:.1f} opp_life={opp_life}",
        r_res=r_res,
    )


def _compute_r_res(hand, mana, medallions):
    """Chain-Reaction Residue: available mana minus costs after reduction.

    R_res = (M_pool + Σ ritual_mana) - Σ (spell_cost - η)
    Positive means mana surplus (chain can sustain).
    """
    available = mana
    total_cost = 0
    for c in hand:
        if c.template.is_land:
            continue
        tags = getattr(c.template, 'tags', set())
        cmc = c.template.cmc or 0

        # Ritual mana production (from template, set by oracle parser)
        ritual_data = getattr(c.template, 'ritual_mana', None)
        if ritual_data:
            available += ritual_data[1]  # (color, amount) → add amount

        # Effective cost after reducer discount
        from engine.cards import Color
        reduction = 0
        if (c.template.is_instant or c.template.is_sorcery):
            if hasattr(c.template, 'color_identity') and Color.RED in c.template.color_identity:
                reduction = medallions
        effective_cost = max(0, cmc - reduction)
        total_cost += effective_cost

    return available - total_cost


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


# ═══════════════════════════════════════════════════════════════
# Storm finisher-access predicates
# ═══════════════════════════════════════════════════════════════
# Ported from the deleted `_combo_modifier` in `ai/ev_player.py`
# (Phase 2c.3 hard refactor).  These predicates determine whether a
# Storm chain has a viable finisher path: either a direct
# STORM-keyword spell in hand, a tutor whose target deck (SB ∪
# library) actually contains a finisher, or a flashback-combo card
# (Past in Flames pattern) backed by graveyard fuel + mana to cast it
# + a finisher to close after the replay.
#
# Without these predicates, Storm AI fires speculative ritual chains
# that fizzle when no finisher is reachable — burning mana at phase
# end (CR 500.4).  Observed in the storm patience suite of tests
# (test_storm_pif_requires_gy_and_mana, test_storm_wish_validates_sideboard,
# test_storm_ritual_held_without_finisher).

# Force-pass sentinel for when a ritual would empty mana at phase
# end with no finisher reachable (CR 500.4).  Returned from
# `card_combo_modifier` and ADDED to `ev` by the caller, so the
# value must be strongly negative enough to swamp any positive
# contributions already accumulated in `ev`.
#
# Magnitude justification: position_value's natural scale is
# bounded by NO_CLOCK (99.0 in clock.py — the "no clock" sentinel
# used as a turn-count cap throughout the EV pipeline).  Setting
# the hard-hold to `-NO_CLOCK × ratio_of_safety` (here, ratio=10)
# ensures `ev + STORM_HARD_HOLD` is strongly negative for any
# realistic `ev` accumulation.  Auto-derived at import time so a
# future change to NO_CLOCK keeps the sentinel correctly scaled.
def _derive_storm_hard_hold() -> float:
    from ai.clock import NO_CLOCK
    return -NO_CLOCK * 10.0


STORM_HARD_HOLD = _derive_storm_hard_hold()


def _has_storm_finisher(card, me) -> bool:
    """Direct STORM-keyword finisher in hand, OR tutor with valid target.

    A tutor (`Wish`, `Burning Wish`) is only a finisher path when the
    sideboard ∪ library actually contains a STORM-keyword card or a
    token-spawning finisher (Empty-the-Warrens pattern: oracle text
    contains "create … tokens" + "for each").  After Wish has been
    cast and the lone SB Grapeshot is gone, a second Wish in hand
    must NOT register as "has finisher".  Zero hardcoded card names.
    """
    from engine.cards import Keyword as Kw
    has_direct = any(
        Kw.STORM in getattr(c.template, 'keywords', set())
        for c in me.hand if c.instance_id != card.instance_id
    )
    if has_direct:
        return True
    has_tutor = any(
        'tutor' in getattr(c.template, 'tags', set())
        for c in me.hand if c.instance_id != card.instance_id
    )
    if not has_tutor:
        return False
    sb = getattr(me, 'sideboard', None) or []
    for c in list(sb) + list(me.library):
        if Kw.STORM in getattr(c.template, 'keywords', set()):
            return True
        oracle = (c.template.oracle_text or '').lower()
        if ('create' in oracle and 'tokens' in oracle
                and 'for each' in oracle):
            return True
    return False


def _tutor_has_payoff_access(card, me) -> bool:
    """True when `card` is a tutor whose target deck (SB ∪ library)
    contains a STORM-keyword card or a token-spawning finisher.

    Differs from `_has_storm_finisher`: that predicate asks "does my
    hand have a finisher path, EXCLUDING `card`?" and is used by the
    ritual gate.  This predicate asks "does THIS tutor card have a
    real target?" and is used to score the tutor itself as a
    finisher-access cast.

    Generic by construction — no card names hardcoded.  The
    finisher detection mirrors `_has_storm_finisher`'s SB scan:
    STORM keyword OR `create … tokens` + `for each` (the printed
    Empty-the-Warrens template).
    """
    from engine.cards import Keyword as Kw
    if 'tutor' not in getattr(card.template, 'tags', set()):
        return False
    sb = getattr(me, 'sideboard', None) or []
    for c in list(sb) + list(me.library):
        # Defensive: in test scaffolds (and real scenarios such as a
        # transformed face-down library), an entry may be missing its
        # template — skip those rather than crash.
        tmpl = getattr(c, 'template', None)
        if tmpl is None:
            continue
        if Kw.STORM in getattr(tmpl, 'keywords', set()):
            return True
        oracle = (tmpl.oracle_text or '').lower()
        if ('create' in oracle and 'tokens' in oracle
                and 'for each' in oracle):
            return True
    return False


def _has_viable_pif(card, me, snap, after_cast_card_cmc=0,
                    after_cast_ritual_net=0) -> bool:
    """Past-in-Flames-pattern viable as a finisher path THIS turn.

    PiF only functions when (a) the card is in hand, (b) the graveyard
    contains ≥1 ritual to flashback into, (c) we have enough mana to
    cast PiF after this turn's chain, and (d) a real finisher is
    accessible to close the chain (storm keyword in hand or tutor with
    valid target).  Without all four, the AI burns rituals "because
    PiF is in hand" on a chain that fizzles.
    """
    has_pif_card = any(
        'flashback' in getattr(c.template, 'tags', set())
        and 'combo' in getattr(c.template, 'tags', set())
        for c in me.hand if c.instance_id != card.instance_id
    )
    if not has_pif_card:
        return False
    gy_fuel = sum(
        1 for c in me.graveyard
        if (c.template.is_instant or c.template.is_sorcery)
        and 'ritual' in getattr(c.template, 'tags', set())
    )
    if gy_fuel < 1:
        return False
    pif_card = next(
        (c for c in me.hand
         if c.instance_id != card.instance_id
         and 'flashback' in getattr(c.template, 'tags', set())
         and 'combo' in getattr(c.template, 'tags', set())),
        None,
    )
    if pif_card is None:
        return False
    reducers = sum(
        1 for c in me.battlefield
        if 'cost_reducer' in getattr(c.template, 'tags', set())
    )
    pif_cost = max(0, (pif_card.template.cmc or 0) - reducers)
    mana_after_ritual = (snap.my_mana - after_cast_card_cmc
                         + after_cast_ritual_net)
    if mana_after_ritual < pif_cost:
        return False
    return _has_storm_finisher(card, me)


def _has_draw_in_hand(card, me) -> bool:
    """Any cantrip / card-advantage / draw spell in hand (excluding `card`).

    Uses `predicates.is_draw_engine` for the tag check so the
    "what counts as a draw engine" definition lives in one place.
    """
    from ai.predicates import is_draw_engine
    return any(
        is_draw_engine(c)
        for c in me.hand
        if c.instance_id != card.instance_id
        and not c.template.is_land
    )


def card_combo_modifier(card, assessment, snap, me, game, player_idx):
    """Minimal combo modifier — only handles what projection CAN'T model.

    The projection (compute_play_ev) already handles:
    - Cantrip value (draws cards → position improvement)
    - Engine value (Medallion → mana spent, permanent deployed)
    - Ritual value (mana production → position improvement)

    This modifier ONLY intervenes for two things:
    1. Storm finisher timing: hold until storm count maximized
    2. Ritual chain gate: don't waste rituals without payoff access

    Everything else returns 0.0 — let arithmetic flow naturally.
    """
    from engine.cards import Keyword as Kw

    a = assessment
    if not a or not a.resource_zone:
        return 0.0

    tags = getattr(card.template, 'tags', set())
    opp_life = max(1, snap.opp_life)
    storm = me.spells_cast_this_turn
    role = card_combo_role(card, a)

    # ═══ STORM FINISHER: hold until chain-extending fuel exhausted ═══
    if Kw.STORM in getattr(card.template, 'keywords', set()):
        if storm + 1 >= opp_life:
            return a.combo_value  # lethal — fire immediately

        # Only count CHAIN-EXTENDING fuel via `is_chain_fuel`: cards
        # tagged ritual / cantrip / draw / card_advantage.  Cards
        # without those tags (creatures like Ral, filler) add 1 to
        # storm if cast but don't enable more spells — holding the
        # finisher for them strands the chain when mana runs out.
        # Symmetric to the tutor branch below (F2.1 / F2.1b).
        from ai.predicates import is_chain_fuel
        total_fuel = sum(1 for c in me.hand
                         if c.instance_id != card.instance_id
                         and not c.template.is_land
                         and Kw.STORM not in getattr(c.template, 'keywords', set())
                         and is_chain_fuel(c))
        if total_fuel > 0:
            # Hold: each remaining fuel adds 1/opp_life of a kill × combo_value.
            # This is the opportunity cost of firing early instead of chaining more.
            return -total_fuel / opp_life * a.combo_value
        # Truly no chain-extending fuel left — fire now
        return (storm + 1) / opp_life * a.combo_value

    # ═══ TUTOR-AS-FINISHER-ACCESS ═══
    # A tutor card whose target deck (SB ∪ library) contains a real
    # payoff IS the finisher, one cast away.  Score it symmetrically
    # to the STORM-keyword branch above: hold while non-tutor fuel
    # remains in hand to grow the chain, fire when the tutor is the
    # closer.  After resolving, the fetched payoff will deal
    # (storm + 2) damage when chained — matches the STORM branch
    # arithmetic with one extra spell in the chain (the tutor itself).
    #
    # Without this branch, a tutor scores at the projection's vanilla
    # baseline (~−0.2 EV) and ties with cantrips, so the tiebreaker
    # picks the cantrip and the chain never reaches the finisher
    # (Storm vs Boros s50500 loss path).
    #
    # Generic by construction: detection is `'tutor' in tags` AND
    # `_tutor_has_payoff_access` (oracle/tag-driven, no card names).
    # Same mechanism credits Wish, Burning Wish, Living Wish,
    # Demonic Tutor, Summoner's Pact — any tutor with a real target.
    if 'tutor' in tags and _tutor_has_payoff_access(card, me):
        if storm + 1 >= opp_life:
            return a.combo_value  # tutor reach is lethal — fire
        # Only count CHAIN-EXTENDING fuel via `is_chain_fuel`: cards
        # tagged ritual / cantrip / draw / card_advantage.  Cards
        # without those tags (creatures like Ral, random filler)
        # add 1 to storm if cast but don't enable more spells —
        # holding the tutor "for them" strands the chain when mana
        # runs out.  Audit F2.1: storm vs Dimir T10 trace shows AI
        # passes at 1 life with 2 Wishes uncast because 2 Ral
        # creatures counted as `non_tutor_fuel`.  Filtering by
        # chain-extending tags gives non_tutor_fuel=0 → tutor fires.
        from ai.predicates import is_chain_fuel
        non_tutor_fuel = sum(
            1 for c in me.hand
            if c.instance_id != card.instance_id
            and not c.template.is_land
            and Kw.STORM not in getattr(c.template, 'keywords', set())
            and 'tutor' not in getattr(c.template, 'tags', set())
            and is_chain_fuel(c)
        )
        if non_tutor_fuel > 0:
            return -non_tutor_fuel / opp_life * a.combo_value
        # No more chain-extending fuel — fire the tutor now to close.
        # +2 spells: the tutor + the fetched payoff.
        return (storm + 2) / opp_life * a.combo_value

    # ═══ NON-STORM PAYOFF: hold until resources ready ═══
    if role == 'payoff' and Kw.CASCADE not in getattr(card.template, 'keywords', set()):
        if not a.is_ready:
            # Wasted potential = (target - current) / opp_life × combo_value
            potential = (a.resource_target / opp_life
                         if a.resource_target > 0
                         else COMBO_NON_READY_POTENTIAL_FALLBACK)
            wasted = max(0.01, potential - a.payoff_value)
            return -wasted * a.combo_value
        # Ready — let projection handle the positive value
        return 0.0

    # ═══ COST REDUCER: value from actual chain improvement ═══
    # Run find_all_chains with medallions vs medallions+1 — the storm damage
    # difference IS the reducer's value. No magic numbers.
    if role == 'engine' and a.resource_zone == "storm":
        from ai.combo_chain import find_all_chains
        medallions = sum(1 for c in me.battlefield
                         if 'cost_reducer' in getattr(c.template, 'tags', set()))
        hand_after = [c for c in me.hand if c.instance_id != card.instance_id]
        mana_after = max(0, snap.my_mana - (card.template.cmc or 0))

        # Chain WITH the extra reducer deployed
        chains_with = find_all_chains(hand_after, mana_after, medallions + 1,
                                      a.payoff_names, storm)
        best_with = max(chains_with, key=lambda c: c.storm_damage, default=None)
        # Chain WITHOUT (current state, same hand minus the reducer card)
        chains_without = find_all_chains(hand_after, mana_after, medallions,
                                         a.payoff_names, storm)
        best_without = max(chains_without, key=lambda c: c.storm_damage, default=None)

        dmg_with = best_with.storm_damage if best_with else 0
        dmg_without = best_without.storm_damage if best_without else 0
        # The reducer's value = (damage with it - damage without) / opp_life × combo_value
        improvement = (dmg_with - dmg_without) / opp_life * a.combo_value
        # Even if no chain improvement yet, reducer has future value from
        # spells we'll draw. Use storm count from best chain as floor.
        if improvement <= 0 and dmg_with > 0:
            improvement = dmg_with / opp_life * a.combo_value
        return improvement

    # ═══ RITUAL CHAIN GATE: storm=0 — block speculative chains ═══
    # Tightened predicate (Phase 2c.3 port): a tutor-tagged card alone
    # is not a finisher path; the SB ∪ library must contain a real
    # finisher.  PiF is only a finisher path when GY has fuel + we
    # have mana to cast it + a finisher exists to close the chain.
    #
    # `assessment.has_payoff` is the canonical "payoff-in-hand" signal
    # produced by the zone assessor (see `_assess_storm_zone` →
    # `what_is_missing`).  When the assessor has already confirmed a
    # payoff is in hand, the hand-scan predicates (`_has_storm_finisher`
    # / `_has_viable_pif`) are redundant: the chain has a real closer.
    # Skipping them lets the reducer-first / patience heuristics below
    # decide the timing.  When no payoff is confirmed, the hand-scan
    # predicates remain the last defence against the speculative-chain
    # mana-burn (CR 500.4).
    if role == 'fuel' and storm == 0 and 'ritual' in tags:
        rdata = getattr(card.template, 'ritual_mana', None)
        ritual_net = max(0, (rdata[1] - (card.template.cmc or 0))) if rdata else 0
        if not a.has_payoff:
            has_finisher = _has_storm_finisher(card, me)
            has_pif = _has_viable_pif(card, me, snap,
                                       after_cast_card_cmc=(card.template.cmc or 0),
                                       after_cast_ritual_net=ritual_net)
            if not has_finisher and not has_pif and not snap.am_dead_next:
                # No finisher path and not under lethal pressure — this
                # ritual's mana empties at phase end (CR 500.4).  Hard hold.
                return STORM_HARD_HOLD

        # ── Reducer-first heuristic: rituals are worth more AFTER a reducer ──
        # If no reducer deployed yet but one exists in hand and is castable,
        # penalize casting rituals now — deploying the reducer first makes
        # every subsequent ritual produce more net mana.
        if a.resource_zone == "storm":
            reducer_deployed = any(
                'cost_reducer' in getattr(c.template, 'tags', set())
                for c in me.battlefield)
            if not reducer_deployed:
                reducer_in_hand = [
                    c for c in me.hand
                    if c.instance_id != card.instance_id
                    and 'cost_reducer' in getattr(c.template, 'tags', set())
                    and not c.template.is_instant and not c.template.is_sorcery
                ]
                if reducer_in_hand:
                    # Check if any reducer is castable with current mana
                    castable_reducer = any(
                        (c.template.cmc or 0) <= snap.my_mana
                        for c in reducer_in_hand)
                    if castable_reducer:
                        # Penalty = the mana amplification we'd lose by not
                        # deploying the reducer first. Each fuel spell in hand
                        # saves 1 mana with a reducer, so the penalty scales
                        # with how many fuel spells remain.
                        fuel_count = sum(
                            1 for c in me.hand
                            if c.instance_id != card.instance_id
                            and not c.template.is_land
                            and 'cost_reducer' not in getattr(c.template, 'tags', set())
                            and (c.template.is_instant or c.template.is_sorcery))
                        # Each future spell saves 1 mana with reducer deployed
                        amplification_loss = fuel_count / opp_life * a.combo_value
                        return -amplification_loss

        # ── Golden turn / divergence point: patience when R_res is poor ──
        # The "divergence point" is when mana generated exceeds mana spent
        # by enough to sustain the chain. If R_res is low, we haven't reached
        # it yet — waiting for another land drop or reducer will multiply
        # our chain's output significantly.
        if (a.resource_zone == "storm"
                and a.r_res < COMBO_DIVERGENCE_RES_THRESHOLD):
            # Count lands (proxy for turn number / ramp)
            land_count = snap.my_total_lands
            # On early turns (few lands, no reducer), the chain can't sustain.
            # Penalty scales with how far below the divergence threshold we are.
            # At divergence-point r_res, no penalty.
            divergence_gap = ((COMBO_DIVERGENCE_RES_THRESHOLD - a.r_res)
                              / float(COMBO_DIVERGENCE_RES_THRESHOLD))  # 0..1+
            # Early game (fewer lands) means waiting is more valuable
            # because the next land drop adds proportionally more mana.
            early_factor = max(
                0.0,
                (COMBO_EARLY_GAME_LAND_THRESHOLD - land_count)
                / float(COMBO_EARLY_GAME_LAND_THRESHOLD),
            )  # peaks at 1 land
            patience_penalty = (divergence_gap * early_factor
                                * a.combo_value
                                * COMBO_PATIENCE_PENALTY_SCALE)
            if patience_penalty > 0:
                return -patience_penalty

        # Has access — let projection's mana-production arithmetic handle it
        return 0.0

    # ═══ MID-CHAIN RITUAL GATE (storm >= 1) ═══
    # Phase 2c.3 port: when we're already mid-chain but no finisher
    # path is reachable, the rituals' mana empties at phase end.
    # Hard-clamp when no draws remain (no way to dig); soft-penalize
    # with storm-coverage escalation + draw-miss cascade risk when
    # draws still exist.  Sentinel constants (HALF_LETHAL,
    # MIN_CHAIN_DEPTH, CASCADE_DRAW_FLOOR) are derived from CR damage
    # rules and STORM-profile fuel thresholds, not tuning weights.
    if role == 'fuel' and storm >= 1 and 'ritual' in tags:
        rdata = getattr(card.template, 'ritual_mana', None)
        ritual_net = max(0, (rdata[1] - (card.template.cmc or 0))) if rdata else 0
        has_finisher = _has_storm_finisher(card, me)
        has_pif = _has_viable_pif(card, me, snap,
                                   after_cast_card_cmc=(card.template.cmc or 0),
                                   after_cast_ritual_net=ritual_net)
        if not has_finisher and not has_pif:
            opp_clock = getattr(snap, 'opp_clock_discrete', 99)
            if not snap.am_dead_next and opp_clock > 2:
                if not _has_draw_in_hand(card, me):
                    # No finisher, no flashback, no draws → hard hold.
                    return STORM_HARD_HOLD
                # Draws remain — soft penalty with two refinements.
                # (A) Storm-coverage escalation: when storm/opp_life
                #     > COMBO_HALF_LETHAL_FRACTION we've already invested
                #     most of the resources in this chain; missing the
                #     closer is increasingly catastrophic.
                storm_coverage = storm / opp_life
                escalation = 1.0 + max(0.0,
                                       storm_coverage - COMBO_HALF_LETHAL_FRACTION)
                penalty = ((storm + 2) / opp_life
                           * COMBO_RITUAL_MISSED_FINISHER_SCALE
                           * escalation)
                # (B) Draw-miss cascade risk: at storm >= COMBO_MIN_CHAIN_DEPTH
                #     and only one draw left, the chain is one bad top-deck
                #     from collapse.  Penalty scales with library
                #     miss probability times coverage.
                if storm >= COMBO_MIN_CHAIN_DEPTH:
                    draw_count = sum(
                        1 for c in me.hand
                        if c.instance_id != card.instance_id
                        and not c.template.is_land
                        and any(dt in getattr(c.template, 'tags', set())
                                for dt in ('cantrip', 'card_advantage', 'draw'))
                    )
                    if draw_count <= COMBO_CASCADE_DRAW_FLOOR:
                        lethal_gap = max(0, opp_life - storm)
                        library_size = max(1, len(me.library))
                        miss_risk = min(1.0, lethal_gap / library_size)
                        penalty += (miss_risk * (storm / opp_life)
                                    * COMBO_CASCADE_RISK_SCALE)
                return -penalty

    # ═══ FLIP-TRANSFORM STACK BATCHING ═══
    # When a creature with a "flip a coin" on-cast trigger is on the
    # battlefield (untransformed), cheap instant/sorcery spells get a bonus.
    # Each additional spell cast = another flip chance. The probability of
    # at least one successful flip in N tries = 1 - (1/2)^N.
    # Bonus = marginal flip probability × transform value.
    if (card.template.is_instant or card.template.is_sorcery) and role != 'payoff':
        flip_creatures = [
            c for c in me.battlefield
            if c.template.is_creature
            and not getattr(c, 'is_transformed', False)
            and 'flip a coin' in (c.template.oracle_text or '').lower()
            and ('instant or sorcery' in (c.template.oracle_text or '').lower()
                 or 'instant and sorcery' in (c.template.oracle_text or '').lower())
        ]
        if flip_creatures:
            # Marginal probability of getting the transform THIS spell:
            # P(at least one flip in storm+1 tries) - P(at least one in storm tries)
            # = (1 - 0.5^(storm+1)) - (1 - 0.5^storm) = 0.5^storm - 0.5^(storm+1)
            # = 0.5^(storm+1)
            marginal_p = 0.5 ** (storm + 1)
            # Transform value: the creature becomes a planeswalker with
            # loyalty = base + spells_cast. Use combo_value as proxy for
            # how good transformation is (engines boost the combo turn).
            transform_value = (a.combo_value
                               * COMBO_FLIP_TRANSFORM_VALUE_FRACTION)
            return marginal_p * transform_value * len(flip_creatures)

    # ═══ SEARCH-TAX AWARENESS ═══
    # When opponent has permanents with "whenever an opponent searches" or
    # "whenever a player searches" triggers, tutoring gives them card
    # advantage. Penalize tutor/search spells proportionally to how many
    # search-punish permanents are on the opponent's board.
    if 'tutor' in tags:
        opp = game.players[1 - player_idx]
        search_tax_count = sum(
            1 for c in opp.battlefield
            if _has_search_tax(c.template.oracle_text or ''))
        if search_tax_count > 0:
            # Each search-tax permanent draws the opponent a card (or worse)
            # when we search. Penalty = cards given away × card_value.
            # If the combo is near-lethal, searching may still be worth it
            # — scale by (1 - payoff_value) so lethal combos override.
            card_value = (a.combo_value / opp_life
                          * COMBO_SEARCH_TAX_CARD_SCALE)
            non_lethal_factor = max(0.0, 1.0 - a.payoff_value)
            return -search_tax_count * card_value * non_lethal_factor

    # ═══ EVERYTHING ELSE: no modifier ═══
    # Cantrips, engines (Medallion), enablers, tutors, PiF —
    # projection already models their effects correctly.
    # Don't interfere with natural card ordering.
    return 0.0


def _has_search_tax(oracle_text: str) -> bool:
    """Check if oracle text punishes the opponent for searching their library.

    Detects patterns like:
    - "whenever an opponent searches" → draws cards / gains counters
    - "whenever a player searches" → similar punishment
    - "if a player would search" → replacement effects (Aven Mindcensor-style)
    """
    lower = oracle_text.lower()
    if not lower:
        return False
    return (('opponent' in lower or 'player' in lower)
            and 'search' in lower
            and ('whenever' in lower or 'if' in lower))


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
