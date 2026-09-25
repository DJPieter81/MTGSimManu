"""Assembly state — ONE owner of the engine / sink / lethal-line facts the
decision layer reads (docs/design/2026-09-16_payoff_sequencing_design.md §2).

The traced Creatures Toolbox losses were not a credit's magnitude but the
absence of any place that answered three questions together: *is the mana
engine live*, *which mana sink can be reached this turn and how*, and *does
that sink, at the mana left after paying for it, kill through the board*.
Each reader (tutor delivery, cast/activation EV, the attack declarer) used
to answer a slice of that on its own; this module answers it once per
main-phase iteration and the readers consume the same object.

Pure query layer: every fact is derived from engine rules queries
(`ActivationManager`), typed `CardTemplate` / `ActivatedAbility` fields
and the clock primitives already owned by `ai/clock.py`. No weights, no
card or deck names, no bare numeric literal (the module is pinned at zero
in `tools/magic_numbers_baseline.json`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from ai.ev_evaluator import EVSnapshot


# Sink shapes: exactly the MANA-scaling payoffs (§2.2). A token maker whose
# scaling variable is storm or discard is owned by the Storm chain, not here.
SHAPE_X_DAMAGE = "x_damage"
SHAPE_TEAM_PUMP = "team_pump"
SHAPE_TEAM_COUNTERS = "team_counters"

# How a sink can be deployed this turn (§2.3).
VIA_CAST = "cast"
VIA_ACTIVATE = "activate"
VIA_TUTOR_CAST = "tutor_cast"
VIA_TUTOR_ACTIVATE = "tutor_activate"

# The first step of a line, as the readers match it.
STEP_CAST = "cast"          # (STEP_CAST, card_instance_id)
STEP_ACTIVATE = "activate"  # (STEP_ACTIVATE, perm_instance_id, ability_index)
STEP_ATTACK = "attack"      # (STEP_ATTACK,) — already lethal on board


@dataclass(frozen=True)
class SinkAccess:
    """One way to bring one sink to bear this turn, with what it projects."""
    shape: str
    via: str
    source: Any               # the sink (cast/activate) or the tutor (tutor_*)
    ability_index: Optional[int]
    delivered: Any            # the sink card for tutor_*; None otherwise
    x: int                    # X paid (tutor X, or the sink's own X)
    mana_after: int           # mana left once the access is paid
    damage: int               # projected damage through blocks this turn
    p_resolves: float
    lethal: bool
    steps_needed: int = 1     # main-phase actions before the attack (0 = attack now)

    @property
    def first_step(self) -> Tuple:
        if self.steps_needed <= 0:
            # The board already reaches lethal: the line's next step is
            # combat, so no further activation or cast is credited.
            return (STEP_ATTACK,)
        if self.via in (VIA_CAST, VIA_TUTOR_CAST):
            return (STEP_CAST, self.source.instance_id)
        if self.via in (VIA_ACTIVATE, VIA_TUTOR_ACTIVATE):
            return (STEP_ACTIVATE, self.source.instance_id, self.ability_index)
        return (STEP_ATTACK,)


@dataclass(frozen=True)
class LethalLine:
    access: SinkAccess
    first_step: Tuple
    swing: float              # p_resolves × win_swing(snap)


@dataclass(frozen=True)
class AssemblyState:
    engine_live: bool         # an unbounded loop exists (sickness ignored)
    engine_spins_now: bool    # ... and can be spun this turn
    mana: int                 # snap.my_mana (already folds the loop allowance)
    completers: Tuple[int, ...]   # instance ids that would complete a loop
    sinks: Tuple[SinkAccess, ...]
    best_line: Optional[LethalLine]


# ── §2.2 sink identity ────────────────────────────────────────────────

def is_mana_sink(obj) -> Optional[str]:
    """The sink shape of a `CardTemplate` or an `ActivatedAbility`, or None.

    Typed fields only: `team_pump_data` (the Overrun class), `x_cost_data`
    with `deals_targeted_damage` (the X-burn / Ballista class), and the
    `PUT_COUNTER_TEAM` activated-ability kind with a mana cost (the
    team-counter class). A template carrying such an ability is a
    `team_counters` sink itself (cast it, then activate)."""
    from engine.cards import ActivatedAbility, ActivationEffectKind as K
    if isinstance(obj, ActivatedAbility):
        if (obj.effect_kind is K.PUT_COUNTER_TEAM
                and obj.cost.mana.cmc > 0):
            return SHAPE_TEAM_COUNTERS
        return None
    t = obj
    if t is None:
        return None
    if getattr(t, 'team_pump_data', None):
        return SHAPE_TEAM_PUMP
    if (getattr(t, 'deals_targeted_damage', False)
            and getattr(t, 'x_cost_data', None)):
        return SHAPE_X_DAMAGE
    if any(is_mana_sink(a) is not None
           for a in (getattr(t, 'activated_abilities', None) or ())):
        return SHAPE_TEAM_COUNTERS
    return None


# ── §2.4 combat fold ──────────────────────────────────────────────────

def attack_reach(swings: Sequence[Tuple[int, bool]],
                 blocker_toughness: Sequence[int] = (),
                 through_blocks: bool = False) -> int:
    """Damage a set of attackers deals the defending player.

    `swings` are `(power, has_trample)` pairs — the projected power of each
    attacker. `through_blocks=False` is the unblocked sum the attack
    declarer's lethal test uses. `through_blocks=True` lets the defender
    assign each untapped blocker to the largest remaining attacker: a
    non-trampler blocked deals nothing (CR 509.1a / 510.1a); a trampler
    deals its excess over the blocker's toughness (CR 702.19b)."""
    powers = sorted(((max(0, int(p)), bool(tr)) for p, tr in swings),
                    key=lambda pt: -pt[0])
    if not through_blocks:
        return sum(p for p, _ in powers)
    blockers = sorted((max(0, int(t)) for t in blocker_toughness),
                      reverse=True)
    total = 0
    for i, (p, tr) in enumerate(powers):
        if i < len(blockers):
            total += max(0, p - blockers[i]) if tr else 0
        else:
            total += p
    return total


def _blocker_toughness(game: "GameState", opp_idx: int) -> List[int]:
    return [int(c.toughness or 0)
            for c in game.get_valid_blockers(opp_idx)]


def _land_capacity(game: "GameState", player_idx: int) -> int:
    from engine.mana_payment import ManaPayment
    player = game.players[player_idx]
    return (sum(len(ManaPayment.land_mana_units(game, player_idx, land))
                for land in player.untapped_lands)
            + player.mana_pool.total())


def _attackers_after_payment(game: "GameState", player_idx: int,
                             attackers: List["CardInstance"],
                             cost_total: int) -> List["CardInstance"]:
    """The attackers left once `cost_total` mana is paid: mana the lands
    and pool do not cover comes from creatures, and a creature tapped for
    mana cannot attack. A live loop covers any shortfall by itself (its
    members are the ones tapped); otherwise the lowest-power mana creatures
    are tapped first — the payment path's own order."""
    from engine.activation import ActivationManager
    from ai.activation_ev import tap_mana_units
    shortfall = cost_total - _land_capacity(game, player_idx)
    if shortfall <= 0:
        return list(attackers)
    engines = ActivationManager.unbounded_mana_engines(game, player_idx)
    if engines:
        engine_ids = {e.instance_id for e in engines}
        return [c for c in attackers if c.instance_id not in engine_ids]
    tappers = sorted((c for c in attackers if tap_mana_units(c) > 0),
                     key=lambda c: (int(c.power or 0), c.instance_id))
    out = list(attackers)
    for c in tappers:
        if shortfall <= 0:
            break
        out.remove(c)
        shortfall -= tap_mana_units(c)
    return out


def _has_trample(perm, granted=()) -> bool:
    from engine.cards import Keyword
    return Keyword.TRAMPLE in perm.keywords or Keyword.TRAMPLE in granted


def _team_pump_amount(data: dict, player, entering) -> int:
    scaling = data.get('scaling') or ''
    extra = 1 if (entering is not None and entering.template.is_creature
                  and entering not in player.battlefield) else 0
    if scaling == 'creature_count':
        return len(player.creatures) + extra
    if scaling == 'greatest_power':
        powers = [int(c.power or 0) for c in player.creatures]
        if entering is not None and entering.template.is_creature:
            powers.append(int(entering.template.power or 0))
        return max(powers, default=0)
    return int(data.get('power') or 0)


def _team_counter_reach(game, player_idx, attackers, blockers, source,
                        ability, k: int, cost_paid: int) -> int:
    """Damage through blocks after `k` activations of a team-counter
    ability, the payment for all `k` tapped out of the team first."""
    per = int(ability.cost.mana.cmc)
    team = _attackers_after_payment(game, player_idx, attackers,
                                    cost_paid + k * per)
    spec = ability.put_counter_data or {}
    if spec.get('other'):
        team = [c for c in team if c is not source]
    return attack_reach([(int(c.power or 0) + k, _has_trample(c))
                         for c in team], blockers, True)


def team_counter_activations_needed(game: "GameState", player_idx: int,
                                    snap: "EVSnapshot", source, ability,
                                    mana_left: int, cost_paid: int = 0
                                    ) -> Optional[int]:
    """The fewest activations of a team-counter ability that reach lethal
    through blocks within `mana_left`, or None when none does. Zero means
    the board is lethal as it stands — the next step is combat."""
    per = int(ability.cost.mana.cmc)
    k_max = mana_left // per if per > 0 else 0
    attackers = game.get_valid_attackers(player_idx)
    blockers = _blocker_toughness(game, 1 - player_idx)
    opp_life = int(snap.opp_life)
    for k in range(0, k_max + 1):
        if _team_counter_reach(game, player_idx, attackers, blockers,
                               source, ability, k, cost_paid) >= opp_life:
            return k
    return None


def sink_damage(game: "GameState", player_idx: int, snap: "EVSnapshot",
                shape: str, obj, mana_left: int, cost_paid: int,
                entering: Optional["CardInstance"] = None,
                ability=None) -> int:
    """Damage the opponent takes this turn if the sink is deployed with
    `mana_left` to spend on it after `cost_paid` was spent bringing it —
    the only place a sink is priced in damage (§2.4).

    `obj` is the sink's template (cast / delivered shapes) or the
    battlefield permanent (`activate` shapes); `entering` is the body the
    access puts onto the battlefield, which attacks only with haste."""
    from engine.cards import ActivationEffectKind as K, Keyword
    player = game.players[player_idx]
    opp_idx = 1 - player_idx
    blockers = _blocker_toughness(game, opp_idx)
    attackers = game.get_valid_attackers(player_idx)

    if shape == SHAPE_TEAM_COUNTERS:
        if ability is None:
            return attack_reach([(int(c.power or 0), _has_trample(c))
                                 for c in attackers], blockers, True)
        per = int(ability.cost.mana.cmc)
        k = mana_left // per if per > 0 else 0
        return _team_counter_reach(game, player_idx, attackers, blockers,
                                   obj, ability, k, cost_paid)

    if shape == SHAPE_TEAM_PUMP:
        template = getattr(obj, 'template', obj)
        data = template.team_pump_data or {}
        team = _attackers_after_payment(game, player_idx, attackers,
                                        cost_paid)
        if (entering is not None and entering.template.is_creature
                and Keyword.HASTE in entering.template.keywords
                and entering not in team):
            team.append(entering)
        pump = _team_pump_amount(data, player, entering)
        granted = {Keyword(k) for k in data.get('keywords', ())}
        others_only = bool(data.get('others_only'))
        swings = []
        for c in team:
            if others_only and c is entering:
                swings.append((int(c.power or 0), _has_trample(c)))
            else:
                swings.append((int(c.power or 0) + pump,
                               _has_trample(c, granted)))
        return attack_reach(swings, blockers, True)

    if shape == SHAPE_X_DAMAGE:
        template = getattr(obj, 'template', obj)
        perm = obj if getattr(obj, 'zone', None) == "battlefield" else None
        ping = next((a for a in (template.activated_abilities or ())
                     if a.effect_kind is K.DAMAGE_ANY_TARGET
                     and a.cost.remove_counter_kind is not None), None)
        if perm is not None:
            # On-board: each ping spends one counter for `amount` damage.
            if ping is None:
                return 0
            counters = perm.counter_count(ping.cost.remove_counter_kind)
            need = math.ceil(int(snap.opp_life) / max(1, ping.amount))
            k = min(counters, need)
            team = [c for c in attackers if c is not perm]
            return (k * ping.amount
                    + attack_reach([(int(c.power or 0), _has_trample(c))
                                    for c in team], blockers, True))
        # Cast from hand at the affordable X: X counters, each a ping —
        # or, for an instant/sorcery, X damage on resolution.
        x_info = template.x_cost_data or {}
        mult = max(1, int(x_info.get('multiplier', 1) or 1))
        x = max(0, (mana_left - (template.cmc or 0)) // mult)
        per_x = ping.amount if ping is not None else 1
        team = _attackers_after_payment(
            game, player_idx, attackers,
            cost_paid + (template.cmc or 0) + x * mult)
        return (x * per_x
                + attack_reach([(int(c.power or 0), _has_trample(c))
                                for c in team], blockers, True))
    return 0


# ── §2.5 resolution probability ───────────────────────────────────────

def _p_resolves_cast(game: "GameState", player_idx: int, card, bhi) -> float:
    """1 − P(interaction) from the single BHI query the cast scorer uses;
    an activation is not a spell and resolves against spell counters."""
    if bhi is None or not getattr(bhi, '_initialized', False):
        return 1.0
    p = bhi.get_interaction_probability(
        game, can_counter=True,
        is_creature_target=bool(card.template.is_creature))
    return max(0.0, min(1.0, 1.0 - float(p)))


# ── §2.1 assemble ─────────────────────────────────────────────────────

def _deliverable_sink(template) -> Optional[str]:
    """A sink a tutor can put onto the battlefield as a live access: a
    mass pump fires on entry, a team-counter permanent can be activated;
    an X-damage permanent enters with no counters (CR 704.5f, a 0/0)
    unless the tutor's spec puts them on, so it is not an access."""
    shape = is_mana_sink(template)
    if shape == SHAPE_X_DAMAGE:
        return None
    return shape


def assemble(game: "GameState", player_idx: int, snap: "EVSnapshot",
             bhi=None) -> AssemblyState:
    """Compute the assembly state for one main-phase iteration."""
    from engine.activation import ActivationManager
    from engine.activated_effects import (activated_tutor_x_budget,
                                          eligible_tutor_targets,
                                          tutor_search_pool)
    from engine.cards import ActivationEffectKind as K
    from engine.cast_manager import CastManager
    from ai.clock import win_swing
    from ai.effective_cmc import effective_cmc

    player = game.players[player_idx]
    mana = int(snap.my_mana)
    opp_life = int(snap.opp_life)
    engine_live = bool(ActivationManager.unbounded_mana_engines(
        game, player_idx, ignore_summoning_sickness=True))
    spins = bool(ActivationManager.unbounded_mana_engines(game, player_idx))
    swing = win_swing(snap)
    accesses: List[SinkAccess] = []

    def _add(shape, via, source, ability_index, delivered, x, cost_paid,
             p_resolves, obj, entering=None, ability=None):
        mana_after = max(0, mana - cost_paid)
        dmg = sink_damage(game, player_idx, snap, shape, obj, mana_after,
                          cost_paid, entering=entering, ability=ability)
        lethal = dmg >= opp_life
        steps = 1
        if lethal and via == VIA_ACTIVATE and shape == SHAPE_TEAM_COUNTERS:
            # How many activations the line still needs; zero once the
            # counters already on the board reach lethal.
            steps = team_counter_activations_needed(
                game, player_idx, snap, obj, ability, mana_after, cost_paid)
            steps = 1 if steps is None else steps
        elif lethal and via == VIA_ACTIVATE and shape == SHAPE_X_DAMAGE:
            steps = 0 if attack_reach(
                [(int(c.power or 0), _has_trample(c))
                 for c in game.get_valid_attackers(player_idx)],
                _blocker_toughness(game, 1 - player_idx), True
            ) >= opp_life else 1
        accesses.append(SinkAccess(
            shape=shape, via=via, source=source,
            ability_index=ability_index, delivered=delivered, x=x,
            mana_after=mana_after, damage=dmg, p_resolves=p_resolves,
            lethal=lethal, steps_needed=steps))

    def _tutor_candidates(spec, budget, mult):
        out = []
        for c in eligible_tutor_targets(tutor_search_pool(player, spec),
                                        spec, x_value=None):
            shape = _deliverable_sink(c.template)
            if shape is None:
                continue
            x = (c.template.cmc or 0) if spec.get('mv_bound_is_x') else 0
            if x > budget:
                continue
            out.append((c, shape, x, x * mult))
        return out

    # Battlefield: activatable sinks and activated tutors.
    for perm in list(player.battlefield):
        for ab in (perm.template.activated_abilities or ()):
            if not ActivationManager.can_activate(game, player_idx, perm, ab):
                continue
            shape = is_mana_sink(ab)
            if shape is not None:
                _add(shape, VIA_ACTIVATE, perm, ab.index, None, 0, 0,
                     1.0, perm, ability=ab)
                continue
            if ab.effect_kind is K.TUTOR_CREATURE_TO_BATTLEFIELD:
                spec = ab.tutor_data or {}
                budget = activated_tutor_x_budget(game, player_idx, ab)
                mult = max(1, int(ab.cost.x_count or 1))
                for c, shape, x, x_cost in _tutor_candidates(spec, budget,
                                                             mult):
                    _add(shape, VIA_TUTOR_ACTIVATE, perm, ab.index, c, x,
                         ab.cost.mana.cmc + x_cost, 1.0, c.template,
                         entering=c)
        if is_mana_sink(perm.template) == SHAPE_X_DAMAGE:
            _add(SHAPE_X_DAMAGE, VIA_ACTIVATE, perm, None, None, 0, 0,
                 1.0, perm)

    # Hand: castable sinks and X creature tutors.
    for card in list(player.hand):
        t = card.template
        shape = is_mana_sink(t)
        if shape is not None and not t.is_land:
            cost = int(effective_cmc(card, snap, game=game,
                                     player_idx=player_idx))
            if cost <= mana:
                p = _p_resolves_cast(game, player_idx, card, bhi)
                ab = next((a for a in (t.activated_abilities or ())
                           if is_mana_sink(a) is not None), None)
                _add(shape, VIA_CAST, card, None, None, 0, cost, p, t,
                     entering=card, ability=ab)
        spec = getattr(t, 'x_creature_tutor_data', None)
        if spec:
            budget = CastManager.affordable_x(game, player_idx, t) or 0
            mult = max(1, int((t.x_cost_data or {}).get('multiplier', 1)
                              or 1))
            p = _p_resolves_cast(game, player_idx, card, bhi)
            for c, dshape, x, x_cost in _tutor_candidates(spec, budget, mult):
                _add(dshape, VIA_TUTOR_CAST, card, None, c, x,
                     (t.cmc or 0) + x_cost, p, c.template, entering=c)

    best: Optional[LethalLine] = None
    for a in accesses:
        if not a.lethal:
            continue
        value = a.p_resolves * swing
        if best is None or value > best.swing:
            best = LethalLine(access=a, first_step=a.first_step,
                              swing=value)

    completers: List[int] = []
    if not engine_live:
        for card in player.hand:
            if ActivationManager.would_complete_unbounded_engine(
                    game, player_idx, card.template):
                completers.append(card.instance_id)
        for card in player.hand:
            spec = getattr(card.template, 'x_creature_tutor_data', None)
            if not spec:
                continue
            budget = CastManager.affordable_x(game, player_idx,
                                              card.template) or 0
            for c in eligible_tutor_targets(
                    tutor_search_pool(player, spec), spec, x_value=budget):
                if (c.instance_id not in completers
                        and ActivationManager.would_complete_unbounded_engine(
                            game, player_idx, c.template)):
                    completers.append(c.instance_id)

    return AssemblyState(engine_live=engine_live, engine_spins_now=spins,
                         mana=mana, completers=tuple(completers),
                         sinks=tuple(accesses), best_line=best)


def delivered_line_is_lethal(game: "GameState", player_idx: int,
                             snap: "EVSnapshot", source, candidate,
                             state: Optional[AssemblyState] = None) -> bool:
    """Cast-time truth for the tutor delivery choice (§2.3 amendment A3):
    would delivering `candidate` through `source` — a hand X tutor or a
    battlefield activated tutor — leave a lethal line at the mana left
    after paying the tutor and the X the candidate needs? Computed
    explicitly from (source, X, mana_after, candidate), never from the
    state's `best_line` (which cannot see a tutor already on the stack)."""
    from engine.cards import ActivationEffectKind as K
    if source is None or candidate is None:
        return False
    shape = _deliverable_sink(candidate.template)
    if shape is None:
        return False
    cmc = candidate.template.cmc or 0
    t = getattr(source, 'template', None)
    spec = getattr(t, 'x_creature_tutor_data', None) if t is not None else None
    if spec:
        mult = max(1, int((t.x_cost_data or {}).get('multiplier', 1) or 1))
        x = cmc if spec.get('mv_bound_is_x') else 0
        cost_paid = (t.cmc or 0) + x * mult
    else:
        ab = next((a for a in (getattr(t, 'activated_abilities', None) or ())
                   if a.effect_kind is K.TUTOR_CREATURE_TO_BATTLEFIELD), None)
        if ab is None:
            return False
        tspec = ab.tutor_data or {}
        x = cmc if tspec.get('mv_bound_is_x') else 0
        cost_paid = ab.cost.mana.cmc + x * max(1, int(ab.cost.x_count or 1))
    mana_after = max(0, int(snap.my_mana) - cost_paid)
    ability = next((a for a in (candidate.template.activated_abilities or ())
                    if is_mana_sink(a) is not None), None)
    dmg = sink_damage(game, player_idx, snap, shape, candidate.template,
                      mana_after, cost_paid, entering=candidate,
                      ability=ability)
    return dmg >= int(snap.opp_life)


# ── §2.7 engine-completion credit ─────────────────────────────────────

def engine_completion_credit(game: "GameState", player_idx: int,
                             state: Optional[AssemblyState],
                             candidate_template, snap: "EVSnapshot",
                             spending=None) -> float:
    """Mana units a delivered `candidate_template` is worth for completing
    an unbounded mana engine — derived from `LOOP_SHORTCUT_MANA`'s own
    justification, never a new number:

    * 0 when the engine is already live (a second loop's marginal mana is
      zero — the "fetch a redundant self-untapper" shape);
    * `LOOP_SHORTCUT_MANA − cmc` when the piece completes a loop and a sink
      is reachable without the access being spent (in hand, on the
      battlefield, or in the library with another tutor still available);
    * draw-discounted by the exact hypergeometric chance of drawing a
      library sink within the surviving horizon when the only access is
      the tutor being spent (`spending`) — not a hard zero, which is the
      cliff that made the sink-reachability gate a no-op."""
    from engine.activation import ActivationManager
    from engine.cards import ActivationEffectKind as K
    from engine.constants import LOOP_SHORTCUT_MANA
    from ai.outcome_ev import p_draw_in_n_turns

    if state is None:
        state = assemble(game, player_idx, snap)
    if state.engine_live:
        return 0.0
    if not ActivationManager.would_complete_unbounded_engine(
            game, player_idx, candidate_template):
        return 0.0
    base = float(LOOP_SHORTCUT_MANA - (candidate_template.cmc or 0))
    player = game.players[player_idx]
    if any(is_mana_sink(c.template) is not None for c in player.hand):
        return base
    if any(is_mana_sink(c.template) is not None for c in player.battlefield):
        return base
    library_sinks = [c for c in player.library
                     if is_mana_sink(c.template) is not None]
    if not library_sinks:
        return 0.0
    # Another access survives this turn: a second X tutor in hand, or a
    # battlefield tutor that is not the one being spent (or is, but stays).
    for c in player.hand:
        if c is not spending and getattr(c.template,
                                         'x_creature_tutor_data', None):
            return base
    for perm in player.battlefield:
        for ab in (perm.template.activated_abilities or ()):
            if ab.effect_kind is not K.TUTOR_CREATURE_TO_BATTLEFIELD:
                continue
            if perm is spending and ab.cost.sacrifice_self:
                continue
            return base
    horizon = max(1, int(snap.opp_clock_discrete))
    return p_draw_in_n_turns(len(player.library), len(library_sinks),
                             horizon) * base


__all__ = [
    "AssemblyState", "SinkAccess", "LethalLine", "assemble",
    "is_mana_sink", "sink_damage", "attack_reach",
    "engine_completion_credit", "delivered_line_is_lethal",
    "team_counter_activations_needed",
    "SHAPE_X_DAMAGE", "SHAPE_TEAM_PUMP", "SHAPE_TEAM_COUNTERS",
    "VIA_CAST", "VIA_ACTIVATE", "VIA_TUTOR_CAST", "VIA_TUTOR_ACTIVATE",
    "STEP_CAST", "STEP_ACTIVATE", "STEP_ATTACK",
]
