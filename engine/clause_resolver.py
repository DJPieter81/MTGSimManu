"""The single owner of "resolve an effect clause" (CR 608.2).

Every carrier of effect text resolves through one ordered registry of
clause handlers:

* a spell's resolution text — ``oracle_resolver.resolve_spell_from_oracle``;
* a single routed clause — a modal mode or a kicked rider (``oracle_override``);
* a planeswalker loyalty line — resolved on its own clause template (S2).

Each ``ClauseHandler`` is a ``gate`` and an ``apply``. The gate is a pure
function of the clause's static facts — its template, oracle text, routed
override and removal data. It never reads the game, so "can this clause run
at all?" has one static answer: ``clause_is_executable``. The apply is the
handler's body. It returns ``None`` to fall through to the next handler, or
a bool to end resolution with that result. Handlers that only contribute
("this clause did something") set ``ctx.handled``.

The registry is a behaviour-identical transcription of the branch sequence
that lived inline in ``resolve_spell_from_oracle`` (same order, same early
returns, same fall-throughs). Helpers are reached through the
``oracle_resolver`` module object so they stay patchable there.

This module is part of the resolution-fallback layer (it reads oracle text at
resolve time for shapes that have no typed field yet), exactly as
``oracle_resolver.py`` did; typed-field migrations keep moving shapes out.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from engine.cards import CardInstance, CardTemplate
    from engine.game_state import GameState


@dataclass
class ClauseContext:
    """Everything one clause resolution reads. Static fields (template,
    oracle, override, removal data, abilities) are what gates may use;
    ``game`` / ``targets`` / ``x_value`` are for applies only."""
    card: "CardInstance"
    controller: int
    game: Optional["GameState"] = None
    targets: Optional[list] = None
    x_value: int = 0
    oracle_override: Optional[str] = None
    removal_data: Optional[dict] = None
    oracle: str = ""
    abilities: List[str] = field(default_factory=list)
    handled: bool = False

    @property
    def template(self) -> "CardTemplate":
        return self.card.template

    @property
    def opponent(self) -> int:
        return 1 - self.controller


@dataclass(frozen=True)
class ClauseHandler:
    name: str
    gate: Callable[[ClauseContext], bool]
    apply: Callable[[ClauseContext], Optional[bool]]


def _or():
    from engine import oracle_resolver
    return oracle_resolver


def _num(tok: str) -> int:
    try:
        return int(tok)
    except ValueError:
        return _or()._WORD_TO_NUM.get(tok, 0)


# ─────────────────────────────────────────────────────────────────────
# Handlers that run before the oracle text is read
# ─────────────────────────────────────────────────────────────────────

def _g_x_tutor(ctx):
    return ctx.oracle_override is None and bool(
        getattr(ctx.template, 'x_creature_tutor_data', None))


def _a_x_tutor(ctx):
    return _or()._resolve_x_creature_tutor(ctx.game, ctx.card, ctx.controller,
                                           ctx.x_value)


def _g_team_pump(ctx):
    return (ctx.template.team_pump_data or {}).get('trigger') == 'spell'


def _a_team_pump(ctx):
    return _or()._resolve_team_pump(ctx.game, ctx.card, ctx.controller)


PRE_ORACLE_HANDLERS: List[ClauseHandler] = [
    # X-bound creature tutor (parse_x_creature_tutor); whole card only.
    ClauseHandler("x_creature_tutor", _g_x_tutor, _a_x_tutor),
    # "Creatures you control get +N/+N [and gain <kw>] until end of turn".
    ClauseHandler("team_pump", _g_team_pump, _a_team_pump),
]


# ─────────────────────────────────────────────────────────────────────
# Handlers over the oracle text
# ─────────────────────────────────────────────────────────────────────

def _combat_prevention_shape(ctx):
    from engine.oracle_parser import parse_combat_prevention
    # CR 702.33: an unkicked resolution never applies the "if this spell was
    # kicked" rider, so the whole-card parse excludes that clause (the
    # kicked path resolves it separately through oracle_override).
    text = ctx.oracle
    kc = getattr(ctx.template, 'kicked_clause', None)
    if ctx.oracle_override is None and kc:
        text = text.replace(kc.lower(), '')
    return parse_combat_prevention(text)


def _g_combat_prevention(ctx):
    return _combat_prevention_shape(ctx) is not None


def _a_combat_prevention(ctx):
    # CR 509.4 / 615 — one turn-scoped flag per shape; not an early return
    # (a compound card keeps resolving its other clauses).
    # Each shape is a THIS_TURN Effect (CR 611.2a): it ends as the game
    # turn ends, whichever player's turn it was cast in.
    from .effect_model import (THIS_TURN, prevent_combat_damage,
                               prohibit_attack, prohibit_be_attacked)
    game, cp = ctx.game, _combat_prevention_shape(ctx)
    reg, src = game.continuous_effects, ctx.card.instance_id
    if cp["no_attack"] == "all":
        for idx in range(len(game.players)):
            reg.register_effect(prohibit_attack(idx, THIS_TURN, ctx.controller, src))
    elif cp["no_attack"] == "you":
        reg.register_effect(prohibit_be_attacked(ctx.controller, THIS_TURN,
                                                 ctx.controller, src))
    if cp["prevent_combat_damage"]:
        reg.register_effect(prevent_combat_damage(THIS_TURN, ctx.controller, src))
    game.log.append(
        f"T{game.display_turn} P{ctx.controller+1}: {ctx.card.name} — combat prevention")
    ctx.handled = True
    return None


def _g_wheel(ctx):
    return ctx.oracle_override is None and bool(
        getattr(ctx.template, 'hand_refill', None))


def _a_wheel(ctx):
    # Each player shuffles hand [+ graveyard] into library (or discards the
    # hand through the discard funnel), then draws N through draw_cards, so
    # static draw limits apply; "if it's your turn, end the turn" (CR 723).
    game, card, controller = ctx.game, ctx.card, ctx.controller
    wheel = ctx.template.hand_refill
    for pidx, p in enumerate(game.players):
        if wheel['mode'] == 'discard':
            if p.hand:
                game._force_discard(pidx, len(p.hand),
                                    self_discard=(pidx == controller))
        else:
            back = list(p.hand)
            if wheel['graveyard']:
                back += list(p.graveyard)
            for c in back:
                game.zone_mgr.move_card(game, c, c.zone, 'library',
                                        cause=f"{card.name}: shuffled in")
            game.rng.shuffle(p.library)
    for pidx in range(len(game.players)):
        game.draw_cards(pidx, wheel['count'])
    game.log.append(f"T{game.display_turn} P{controller+1}: {card.name} — "
                    f"each player's hand refilled to {wheel['count']}")
    if wheel['ends_turn'] and game.active_player == controller:
        game.end_the_turn(controller)
    ctx.handled = True
    return None


def _g_cast_prohibition(ctx):
    return ctx.oracle_override is None and bool(
        getattr(ctx.template, 'cast_prohibition', None))


def _a_cast_prohibition(ctx):
    # CR 101.2 — "<who> can't cast [<type>] spells this turn".
    game, card, controller = ctx.game, ctx.card, ctx.controller
    cpro = ctx.template.cast_prohibition
    players = (list(game.players) if cpro['who'] == 'all'
               else [game.players[ctx.opponent]])
    kind = '' if cpro['filter'] == 'all' else f"{cpro['filter']} "
    from engine.effect_model import THIS_TURN, prohibit_cast
    for p in players:
        game.continuous_effects.register_effect(prohibit_cast(
            p.player_idx, cpro['filter'], THIS_TURN, controller=controller,
            source_id=card.instance_id))
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {card.name} silences "
            f"P{game.players.index(p)+1} ({kind}spells) this turn")
    ctx.handled = True
    return None


def _g_until_next_turn(ctx):
    return ctx.oracle_override is None and bool(
        getattr(ctx.template, 'next_turn_effect', None))


def _a_until_next_turn(ctx):
    # CR 611.2b — the wrapped effect lasts until the controller's next turn
    # (continuous effects: cleanup_until_next_turn at that untap; player
    # effects: reset_turn_tracking at that untap).
    from engine.cards import Keyword as KW
    from engine.continuous_effects import create_pump_spell_effect
    game, card, controller = ctx.game, ctx.card, ctx.controller
    eff = ctx.template.next_turn_effect
    player = game.players[controller]
    kind = eff['kind']
    if kind == 'cost_reduction':
        from engine.effect_model import cost_delta_effect, until_your_next_turn
        game.continuous_effects.register_effect(cost_delta_effect(
            controller, dict(eff['rule']), until_your_next_turn(controller),
            source_id=card.instance_id))
        from engine.oracle_parser import describe_cost_reduction
        desc = describe_cost_reduction(eff['rule'])
    elif kind == 'flash_permission':
        from engine.effect_model import permit_cast_as_flash, until_your_next_turn
        game.continuous_effects.register_effect(permit_cast_as_flash(
            controller, eff['types'], until_your_next_turn(controller),
            source_id=card.instance_id))
        desc = f"{'/'.join(eff['types'])} spells as though they had flash"
    elif eff['scope'] == 'target':
        hostile = eff['power'] < 0 or eff['toughness'] < 0   # a grant (0/0 + kw) is friendly
        tgt = _or().pump_target(game, controller, ctx.targets, hostile=hostile,
                                source=card)
        if tgt is None:
            return None
        kws = {getattr(KW, k.upper().replace(' ', '_'))
               for k in (eff.get('keywords') or ([eff['keyword']] if eff.get('keyword') else []))
               if hasattr(KW, k.upper().replace(' ', '_'))} or None
        for ce in create_pump_spell_effect(card.instance_id, card.name, tgt.instance_id,
                                           eff['power'], eff['toughness'], kws,
                                           duration="until_next_turn",
                                           controller=controller,
                                           target_seq=tgt.battlefield_entry_seq):
            game.continuous_effects.register(ce)
        desc = f"{tgt.name} {eff['power']:+d}/{eff['toughness']:+d}"
    else:  # scope 'yours'
        kws = {getattr(KW, k.upper().replace(' ', '_')) for k in eff.get('keywords', ())
               if hasattr(KW, k.upper().replace(' ', '_'))} or None
        for c in list(player.creatures):
            for ce in create_pump_spell_effect(card.instance_id, card.name, c.instance_id,
                                               eff['power'], eff['toughness'], kws,
                                               duration="until_next_turn",
                                               controller=controller,
                                               target_seq=c.battlefield_entry_seq):
                game.continuous_effects.register(ce)
        desc = f"creatures you control {eff['power']:+d}/{eff['toughness']:+d}"
    game.continuous_effects.recalculate(game)
    game.log.append(f"T{game.display_turn} P{controller+1}: {card.name} — "
                    f"until your next turn: {desc}")
    ctx.handled = True
    return True


def _g_mass_mode(ctx):
    return ctx.oracle_override is not None


def _a_mass_mode(ctx):
    # A routed single mode clause of a modal wipe ("deals N damage to each
    # creature …", "destroy all <type> …").
    if _or()._resolve_mass_mode_clause(ctx.game, ctx.card, ctx.controller,
                                       ctx.oracle):
        return True
    return None


def _g_pump(ctx):
    return ctx.oracle_override is None and ctx.template.has_targeted_pump


def _a_pump(ctx):
    # "Target creature gets +N/+M until end of turn [and gains <kw>]".
    from engine.cards import Keyword as KW
    game, card, controller = ctx.game, ctx.card, ctx.controller
    pp = getattr(card.template, 'pump_spell_power', 0)
    pt = getattr(card.template, 'pump_spell_toughness', 0)
    tgt = _or().pump_target(game, controller, ctx.targets)
    if tgt is None:
        return None
    tgt.temp_power_mod += pp
    tgt.temp_toughness_mod += pt
    kws = (getattr(card.template, 'pump_spell_keywords', ()) or
           ((getattr(card.template, 'pump_spell_keyword', '') or None),))
    kws = tuple(k for k in kws if k)
    for kw in kws:
        kwe = getattr(KW, kw.upper().replace(' ', '_'), None)
        if kwe is not None:
            tgt.temp_keywords.add(kwe)
    # Rules audit (CR 613.1f): the target now has every granted keyword
    # (read from the permanent's resulting keyword set).
    from engine.rules_audit import enabled as audit_on, check as audit_check
    if audit_on():
        missing = [k for k in kws
                   if getattr(KW, k.upper().replace(' ', '_'), None) not in tgt.keywords]
        audit_check("613.1f/keyword_granted", not missing,
                    f"{card.name}: {tgt.name} lacks granted {missing}", game=game)
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"{card.name} gives {tgt.name} +{pp}/+{pt}"
        f"{' and ' + ', '.join(kws) if kws else ''}")
    return True


def _g_mass_reanimate(ctx):
    return any('all creature cards' in a and 'graveyard' in a
               and 'battlefield' in a for a in ctx.abilities)


def _a_mass_reanimate(ctx):
    # Living End shape — the same effect the cascade path runs.
    ctx.game._resolve_living_end(ctx.controller)
    return True


def _g_energy_damage(ctx):
    return bool(getattr(ctx.template, 'has_energy_damage_target', False))


def _a_energy_damage(ctx):
    # "Target creature or planeswalker. You get {E}^k, then you may pay any
    # amount of {E}. ~ deals that much (additional) damage."
    game, card, controller, oracle = ctx.game, ctx.card, ctx.controller, ctx.oracle
    base_match = re.search(
        r'deals?\s+(\d+)\s+damage\s+to\s+target\s+creature\s+or\s+planeswalker',
        oracle)
    base_damage = int(base_match.group(1)) if base_match else 0
    gain_match = re.search(r'you get\s+((?:\{e\}\s*)+)', oracle)
    self_gen_energy = gain_match.group(1).count('{e}') if gain_match else 0
    chosen = None
    for tid in (ctx.targets or []):
        if tid == -1:
            continue  # face-marker — illegal target for this spell
        cand = game.get_card_by_id(tid)
        if (cand is not None and cand.zone == "battlefield"
                and (cand.template.is_creature
                     or 'planeswalker' in [t.value for t in cand.template.card_types])):
            chosen = cand
            break
    if chosen is None:
        opp = game.players[1 - controller]
        opp_pw = [c for c in opp.battlefield
                  if 'planeswalker' in [t.value for t in c.template.card_types]]
        candidates = list(opp.creatures) + opp_pw
        if not candidates:
            return True  # fizzle: no legal target
        chosen = max(candidates,
                     key=lambda c: (c.power or 0) + (c.toughness or 0)
                     + getattr(c, 'loyalty_counters', 0))
    player = game.players[controller]
    player.add_energy(self_gen_energy)
    if chosen.template.is_creature:
        remaining = (chosen.toughness or 0) - getattr(chosen, 'damage_marked', 0)
    else:
        remaining = chosen.loyalty_counters  # CR 119.3
    need_to_kill = max(0, remaining - base_damage)
    spend = min(need_to_kill, player.energy_counters) if need_to_kill > 0 else 0
    if spend > 0:
        player.spend_energy(spend)
    total = base_damage + spend
    if total > 0:
        if chosen.template.is_creature:
            chosen.damage_marked = getattr(chosen, 'damage_marked', 0) + total
            if chosen.is_dead:
                game._creature_dies(chosen)
        else:
            chosen.loyalty_counters = max(0, chosen.loyalty_counters - total)
            game.check_state_based_actions()
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"{card.name} deals {total} to {chosen.name} "
        f"(base {base_damage} + {spend} energy)")
    return True


def _g_land_destruction(ctx):
    return bool(getattr(ctx.template, 'destroys_target_land', False))


def _a_land_destruction(ctx):
    return _or()._resolve_destroy_target_land(ctx.game, ctx.card, ctx.controller,
                                              ctx.targets)


def _g_direct_damage(ctx):
    return ctx.oracle_override is None and bool(
        getattr(ctx.template, 'direct_damage_data', None))


def _a_direct_damage(ctx):
    # "Deals N damage to any target" through the shared damage owner.
    # A strict, executable spell host resolves through the effect
    # dispatcher (design doc 2026-09-29, section 11); any other takes the
    # legacy apply below, whose CR 608.2 upgrade audit is restated
    # independently.
    from engine import effect_carrier
    from engine import effect_conditions as ec
    effects = ctx.template.effects
    if effect_carrier.dispatch(ctx.game, ctx.card, ctx.controller,
                               effects.spell(0), ctx.targets, family="damage",
                               x_value=ctx.x_value,
                               face_hosts=effects.front()) is not None:
        return True
    orr = _or()
    game, card, controller = ctx.game, ctx.card, ctx.controller
    dd = card.template.direct_damage_data
    amt = ec.effective_direct_damage(game, controller, card.template)
    from engine.rules_audit import check as audit_check
    up = dd.get('upgrade_amount')
    if up and ec.direct_damage_condition_met(game, controller,
                                             dd.get('upgrade_condition')):
        audit_check("608.2/damage_upgrade", amt == up,
                    f"{card.name}: {dd.get('upgrade_condition')} met but "
                    f"dealt {amt}, not {up}", game=game)
    orr.resolve_damage_to_chosen_target(game, card, controller, amt, ctx.targets)
    return True


def _g_dispatched(ctx):
    # A spell whose whole parsed SPELL host a landed effect family runs
    # (strict, executable, its targets placeable) and that no handler above
    # claims: the effect dispatcher is its carrier (design doc 2026-09-29,
    # section 11). Last in the registry, so it never takes a host from a
    # legacy handler; reads typed effects only.
    if ctx.oracle_override is not None:
        return False
    from engine import effect_carrier
    return effect_carrier.spell_family(ctx.template) is not None


def _a_dispatched(ctx):
    # Under `effect_resolver.legacy_only()` the dispatcher declines and the
    # spell falls through to the legacy per-ability path, as before this
    # handler existed (the per-host harness's legacy side).
    from engine import effect_carrier
    if ctx.handled:          # a handler above already applied part of it
        return None
    family = effect_carrier.spell_family(ctx.template)
    effects = ctx.template.effects
    if family is not None and effect_carrier.dispatch(
            ctx.game, ctx.card, ctx.controller, effects.spell(0), ctx.targets,
            family=family, x_value=ctx.x_value,
            face_hosts=effects.front()) is not None:
        return True
    return None


def _g_board_sweep(ctx):
    return ctx.oracle_override is None and bool(
        getattr(ctx.template, 'board_sweep_data', None))


def _a_board_sweep(ctx):
    from engine.card_effects import _resolve_board_sweep
    bs = ctx.template.board_sweep_data
    _resolve_board_sweep(ctx.game, ctx.card, ctx.controller, ctx.targets,
                         item=None, action=bs['action'],
                         types=frozenset(bs['types']))
    return True


def _removal_shape(ctx):
    if ctx.removal_data is not None:
        return ctx.removal_data
    return (getattr(ctx.template, 'targeted_removal_data', None)
            if ctx.oracle_override is None else None)


def _g_targeted_removal(ctx):
    return bool(_removal_shape(ctx))


def _a_targeted_removal(ctx):
    # "Destroy/exile target <permanent> [MV <= N|X]"; a modal caller passes
    # the mode's own typed classification.
    from engine.card_effects import _resolve_nonland_permanent_removal
    rm = _removal_shape(ctx)
    mv = rm.get('mv')
    if mv is None:
        mv_fn = None
    elif mv == 'x':
        mv_fn = lambda g, c, ctl, it, _x=ctx.x_value: _x
    else:
        mv_fn = lambda g, c, ctl, it, _n=mv: _n
    exile = rm['action'] == 'exile'
    _resolve_nonland_permanent_removal(
        ctx.game, ctx.card, ctx.controller, ctx.targets, None,
        zone_dest='exile' if exile else 'graveyard',
        types=frozenset(rm['types']), mv_max_fn=mv_fn,
        log_verb='exiles' if exile else 'destroys', count=rm.get('count', 1))
    return True


def _g_library_dig(ctx):
    return bool(getattr(ctx.template, 'library_dig_data', None))


def _a_library_dig(ctx):
    return _or()._resolve_library_dig(ctx.game, ctx.card, ctx.controller)


def _hand_attack_shape(ctx):
    return (getattr(ctx.template, 'hand_attack_data', None) or {}
            if ctx.oracle_override is None else {})


def _g_hand_attack(ctx):
    return (_hand_attack_shape(ctx).get('chooser') == 'caster'
            or _or().any_ability_with(ctx.oracle, 'reveals', 'hand', 'discard'))


def _a_hand_attack(ctx):
    # "Target opponent reveals their hand. You choose a nonland card and that
    # player discards it." — through the one discard funnel.
    orr = _or()
    game, card, controller, oracle = ctx.game, ctx.card, ctx.controller, ctx.oracle
    hand_attack = _hand_attack_shape(ctx)
    from engine.target_solver import targeted_player
    if hand_attack.get('target') == 'opponent':
        victim_idx = ctx.opponent
    else:
        victim_idx = targeted_player(game, controller, ctx.targets)
    victim = game.players[victim_idx]
    if victim.hand:
        choose_clause = hand_attack.get('choose_clause') or next(
            (c for c in orr.split_clauses(oracle) if 'choose' in c and 'card' in c), '')
        legal = orr._targeted_discard_candidates(victim.hand, choose_clause)
        if legal:
            before = list(victim.hand)
            game._force_discard(victim_idx, 1, self_discard=(victim_idx == controller),
                                candidates=legal)
            gone = [c for c in before if c not in victim.hand]
            for c in gone:
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} discards {c.name}"
                    + (" (own hand)" if victim_idx == controller else ""))
            ctx.handled = True
    loss_clause = next(
        (c for c in orr.split_clauses(oracle) if 'you lose' in c and 'life' in c), None)
    if loss_clause is not None:
        m = re.search(r'lose\s+(\d+)\s+life', loss_clause)
        if m:
            game.players[controller].life -= int(m.group(1))
            ctx.handled = True
    return None


def resolve_bounce(game, controller, source, requirement, targets=None) -> list:
    """Return chosen permanents to their OWNERS' hands (CR 608.2b / 400.3).
    The one owner for every bounce carrier — spells, channel and loyalty
    lines. Legal targets come from the target solver; a chosen legal target
    is honoured; with none chosen, the opponent's most threatening legal
    permanents are taken (a self-scoped bounce takes the controller's
    costliest). Returns the bounced cards."""
    from engine.target_solver import enumerate_legal_targets
    candidates = enumerate_legal_targets(game, controller, requirement, exclude=source)
    chosen = [c for tid in (targets or []) for c in candidates
              if isinstance(tid, int) and c.instance_id == tid]
    if not chosen:
        def _ctrl(c):
            return c.controller if c.controller is not None else c.owner
        if requirement.owner_scope == "you":
            pool = sorted(candidates, key=lambda c: c.template.cmc or 0, reverse=True)
        else:
            from engine.card_effects import _nonland_permanent_threat
            opp_bf = game.players[1 - controller].battlefield
            pool = sorted((c for c in candidates if _ctrl(c) != controller),
                          key=lambda c: _nonland_permanent_threat(c, opp_bf), reverse=True)
        chosen = pool
    chosen = chosen[:max(1, requirement.count_max or 1)]
    from .rules_audit import enabled as _audit_on, check as _audit_check
    for c in chosen:
        game._bounce_permanent(c)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{getattr(source, 'name', 'effect')} returns {c.name} "
                        f"to its owner's hand")
        if _audit_on():
            owner = c.owner if c.owner is not None else c.controller
            _audit_check("400.3/bounced_to_owners_hand",
                         c.zone == "hand" and c in game.players[owner].hand
                         and all(c not in p.hand for i, p in enumerate(game.players)
                                 if i != owner),
                         f"{c.name} bounced", game=game)
    return chosen


def _bounce_shape(ctx):
    # A modal mode resolves its own text; otherwise the typed requirement.
    if ctx.oracle_override is not None:
        from .oracle_parser import parse_bounce_target
        return parse_bounce_target(ctx.oracle_override)
    return getattr(ctx.template, 'bounce_target', None)


def _g_bounce(ctx):
    return _bounce_shape(ctx) is not None


def _a_bounce(ctx):
    # "Return [up to N] target <types> to its owner's hand".
    if resolve_bounce(ctx.game, ctx.controller, ctx.card, _bounce_shape(ctx), ctx.targets):
        ctx.handled = True
    return None


def _reanimate_ability(ctx):
    return next(
        (a for a in ctx.abilities
         if re.search(r'return target\s+(\w+\s+)?creature card', a)
         and 'graveyard' in a and 'battlefield' in a
         and not re.search(r'return target legendary creature', a)),
        None)


def _g_reanimate(ctx):
    return _reanimate_ability(ctx) is not None


def _a_reanimate(ctx):
    # "Return target [nonlegendary] creature card from your graveyard to the
    # battlefield" — targets through the unified target solver.
    game, card, controller = ctx.game, ctx.card, ctx.controller
    from engine.target_solver import enumerate_legal_targets, parse as parse_targets
    requirements = parse_targets(card.template.oracle_text or "")
    gy_reqs = [r for r in requirements if r.zone == "graveyard"]
    creatures: list = []
    for req in gy_reqs:
        creatures.extend(enumerate_legal_targets(game, controller, req, exclude=card))
    creatures = [c for c in creatures if c.template.is_creature]
    if creatures:
        best = max(creatures, key=lambda c: (c.template.power or 0)
                   + (c.template.toughness or 0))
        game.reanimate(controller, best)
        game.log.append(
            f"T{game.display_turn} P{controller+1}: {card.name} reanimates {best.name}")
        ctx.handled = True
    return None



# ─────────────────────────────────────────────────────────────────────
# Scaled counts: "… for each <X>" (CR 608.2 — counted as the effect
# resolves). A scaler shape is recognised statically (gates may use it);
# its count is read from the game at apply time.
# ─────────────────────────────────────────────────────────────────────

_FOR_EACH_RE = re.compile(r'\s+for each ([^.,;]+)')
_YOU_CONTROL_RE = re.compile(r'^(?:other )?([a-z]+) you control$')


def _scaler_shape(phrase: str):
    """('you_control', word) | ('opponents_lost_life',) | None."""
    if phrase == 'opponent who lost life this turn':
        return ('opponents_lost_life',)
    m = _YOU_CONTROL_RE.match(phrase)
    if m:
        return ('you_control', m.group(1), phrase.startswith('other '))
    return None


def _card_flow_effects(ctx):
    """Scry / surveil / draw / loot in oracle-text order (CR 601.2 / 608.2)."""
    oracle, tpl = ctx.oracle, ctx.template
    scry_n, scry_pos = 0, len(oracle)
    if getattr(tpl, 'has_scry', False):
        m = re.search(r'\bscry\s+(\d+)', oracle)
        if m:
            scry_n, scry_pos = _num(m.group(1)), m.start()
    surv_n, surv_pos = 0, len(oracle)
    if getattr(tpl, 'has_surveil', False):
        m = re.search(r'\bsurveil\s+(\d+)', oracle)
        if m:
            surv_n, surv_pos = _num(m.group(1)), m.start()
    # Reminder text is masked with spaces (positions stay comparable) so a
    # keyword's reminder ("({2}, Discard this card: Draw a card.)") is not
    # read as this spell's own draw.
    draw_n, draw_pos, draw_scaler = 0, len(oracle), None
    no_reminder = re.sub(r'\([^()]*\)', lambda m: ' ' * len(m.group(0)), oracle)
    m_draw = re.search(r'draw\s+(\w+)\s+cards?', no_reminder)
    if m_draw:
        draw_n, draw_pos = _num(m_draw.group(1)), m_draw.start()
        # "draw N card(s) for each <X>" draws N × count(X) (CR 608.2); a
        # scaler that cannot be counted draws nothing, never a flat N.
        m_each = _FOR_EACH_RE.match(no_reminder, m_draw.end())
        if m_each:
            draw_scaler = _scaler_shape(m_each.group(1).strip())
            if draw_scaler is None:
                draw_n = 0
                if ctx.game is not None:
                    # Census: a scaler this engine cannot count yet (ranked
                    # across audited runs; no-op with the audit off).
                    from engine.rules_audit import census
                    census("608.2/uncountable_scaler", m_each.group(1).strip()[:60],
                           game=ctx.game)
    elif getattr(tpl, 'has_look_hand_selection', False):
        draw_n, draw_pos = 1, 0
    effects: list = []
    if scry_n > 0:
        effects.append((scry_pos, 'scry', scry_n))
    if surv_n > 0:
        effects.append((surv_pos, 'surveil', surv_n))
    if draw_n > 0:
        effects.append((draw_pos, 'draw', (draw_n, draw_scaler)))
    from engine.oracle_parser import parse_loot_effect
    loot = tpl.loot_data if ctx.oracle_override is None else parse_loot_effect(oracle)
    if loot:
        effects = [e for e in effects if e[1] != 'draw']
        effects.append((draw_pos, 'loot', loot))
    effects.sort(key=lambda x: x[0])
    return effects


def _g_card_flow(ctx):
    return bool(_card_flow_effects(ctx))


def _a_card_flow(ctx):
    game, card, controller = ctx.game, ctx.card, ctx.controller
    for _, kind, count in _card_flow_effects(ctx):
        if kind == 'scry':
            game.scry(controller, count)
        elif kind == 'surveil':
            game.surveil(controller, count)
        elif kind == 'loot':
            _or()._resolve_loot(game, card, controller, count)
        elif kind == 'draw':
            per, scaler = count
            from engine.effect_conditions import scaler_count
            count = (per * scaler_count(game, controller, scaler, source=card)
                     if scaler else per)
            if count <= 0:
                continue
            drawn = game.draw_cards(controller, count)
            if drawn:
                names = ", ".join(c.name for c in drawn)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{card.name} → draw {count} ({names})")
        ctx.handled = True
    return None


_TOKEN_RE = re.compile(
    r'\bcreate\s+(a|an|one|two|three|four|five|\d+)\s+(\d+)/(\d+)\b[^.]*?\btokens?\b')


def _token_clause(ctx):
    for clause in _or().split_abilities(ctx.oracle):
        m = _TOKEN_RE.search(clause)
        if m is None:
            continue
        tok = m.group(1)
        count = 1 if tok in ('a', 'an', 'one') else _num(tok)
        if count <= 0:
            continue
        return clause, m, count
    return None


def _g_token(ctx):
    return ((ctx.template.is_instant or ctx.template.is_sorcery)
            and _token_clause(ctx) is not None)


def _a_token(ctx):
    # CR 111: a spell whose own effect creates a creature token. Only when
    # no earlier handler resolved the clause.
    if ctx.handled:
        return None
    clause, m, count = _token_clause(ctx)
    created = ctx.game.create_token(
        ctx.controller, "creature", count=count,
        power=int(m.group(2)), toughness=int(m.group(3)), source_oracle=clause)
    if created:
        ctx.handled = True
    return None


def _object_restriction_shape(ctx):
    # A modal mode resolves its own text; otherwise the typed field.
    if ctx.oracle_override is not None:
        from .oracle_parser import parse_object_restriction
        return parse_object_restriction(ctx.oracle_override)
    return getattr(ctx.template, 'object_restriction', None)


def _g_object_restriction(ctx):
    return _object_restriction_shape(ctx) is not None


def _a_object_restriction(ctx):
    # CR 508.1c / 509.1b / 611.2c: PROHIBIT effects on the chosen objects,
    # for the printed duration. Not an early return — a compound card keeps
    # resolving its other clauses.
    from engine.target_solver import can_be_targeted
    from .effect_model import THIS_TURN, prohibit_object, until_your_next_turn
    game, card, controller = ctx.game, ctx.card, ctx.controller
    shape = _object_restriction_shape(ctx)
    chosen = []
    for tid in (ctx.targets or []):
        c = game.get_card_by_id(tid) if isinstance(tid, int) and tid > 0 else None
        if (c is not None and c.zone == 'battlefield' and c.effective_is_creature
                and can_be_targeted(c, card, controller) and c not in chosen):
            chosen.append(c)
    if not ctx.targets:
        theirs = [c for c in game.players[1 - controller].creatures
                  if can_be_targeted(c, card, controller)]
        chosen = sorted(theirs, key=lambda c: c.power or 0, reverse=True)
    chosen = chosen[:shape['count']]
    if not chosen:
        return None
    duration = (THIS_TURN if shape['duration'] == 'this_turn'
                else until_your_next_turn(controller))
    for c in chosen:
        for action in shape['actions']:
            game.continuous_effects.register_effect(
                prohibit_object(c, action, duration, controller, card.instance_id))
    game.log.append(
        f"T{game.display_turn} P{controller+1}: {card.name} — "
        f"{', '.join(c.name for c in chosen)} can't {' or '.join(shape['actions'])} "
        f"({shape['duration'].replace('_', ' ')})")
    ctx.handled = True
    return None


def _g_attack_observer(ctx):
    obs = getattr(ctx.template, 'attack_observer', None)
    return (ctx.oracle_override is None and obs is not None
            and obs['duration'] == 'until_next_turn')


def _a_attack_observer(ctx):
    # CR 611.2b / 603.2: "until your next turn, whenever a creature attacks
    # you …" — a stored OBSERVE effect for the controller.
    from .effect_model import observe_attacks, until_your_next_turn
    game, card, controller = ctx.game, ctx.card, ctx.controller
    game.continuous_effects.register_effect(observe_attacks(
        controller, ctx.template.attack_observer, until_your_next_turn(controller),
        source_id=card.instance_id))
    game.log.append(f"T{game.display_turn} P{controller+1}: {card.name} — "
                    f"until your next turn, attackers at you are observed")
    ctx.handled = True
    return None


def _g_group_restriction(ctx):
    return ctx.oracle_override is None and getattr(ctx.template, 'group_restriction', None)


def _a_group_restriction(ctx):
    # CR 508.1c / 509.1b: a class restriction, re-evaluated for its duration.
    from .effect_model import THIS_TURN, prohibit_group, until_your_next_turn
    game, card, controller = ctx.game, ctx.card, ctx.controller
    shape = ctx.template.group_restriction
    duration = (THIS_TURN if shape['duration'] == 'this_turn'
                else until_your_next_turn(controller))
    for action in shape['actions']:
        game.continuous_effects.register_effect(
            prohibit_group(controller, shape, action, duration, card.instance_id))
    game.log.append(f"T{game.display_turn} P{controller+1}: {card.name} — creatures "
                    f"({shape['controller']}) can't {' or '.join(shape['actions'])}")
    ctx.handled = True
    return None


HANDLERS: List[ClauseHandler] = [
    ClauseHandler("combat_prevention", _g_combat_prevention, _a_combat_prevention),
    ClauseHandler("hand_refill_wheel", _g_wheel, _a_wheel),
    ClauseHandler("cast_prohibition", _g_cast_prohibition, _a_cast_prohibition),
    ClauseHandler("object_restriction", _g_object_restriction, _a_object_restriction),
    ClauseHandler("attack_observer", _g_attack_observer, _a_attack_observer),
    ClauseHandler("group_restriction", _g_group_restriction, _a_group_restriction),
    ClauseHandler("until_next_turn", _g_until_next_turn, _a_until_next_turn),
    ClauseHandler("mass_mode_clause", _g_mass_mode, _a_mass_mode),
    ClauseHandler("targeted_pump", _g_pump, _a_pump),
    ClauseHandler("mass_reanimate", _g_mass_reanimate, _a_mass_reanimate),
    ClauseHandler("energy_damage", _g_energy_damage, _a_energy_damage),
    ClauseHandler("land_destruction", _g_land_destruction, _a_land_destruction),
    ClauseHandler("direct_damage", _g_direct_damage, _a_direct_damage),
    ClauseHandler("board_sweep", _g_board_sweep, _a_board_sweep),
    ClauseHandler("targeted_removal", _g_targeted_removal, _a_targeted_removal),
    ClauseHandler("library_dig", _g_library_dig, _a_library_dig),
    ClauseHandler("hand_attack", _g_hand_attack, _a_hand_attack),
    ClauseHandler("bounce", _g_bounce, _a_bounce),
    ClauseHandler("reanimate_target", _g_reanimate, _a_reanimate),
    ClauseHandler("card_flow", _g_card_flow, _a_card_flow),
    ClauseHandler("create_token", _g_token, _a_token),
    ClauseHandler("dispatched", _g_dispatched, _a_dispatched),
]


def _static_context(card, controller, oracle_override, removal_data, game=None,
                    targets=None, x_value=0, mode=None) -> ClauseContext:
    # A typed mode (`CardTemplate.modes[i]`) is its clause with its shapes.
    if mode is not None:
        oracle_override = mode.get("text", "")
        removal_data = mode.get("removal")
    ctx = ClauseContext(card=card, controller=controller, game=game,
                        targets=targets, x_value=x_value,
                        oracle_override=oracle_override, removal_data=removal_data)
    ctx.oracle = (oracle_override if oracle_override is not None
                  else (card.template.oracle_text or '')).lower()
    ctx.abilities = _or().split_abilities(ctx.oracle) if ctx.oracle else []
    return ctx


def resolve_clause(game, card, controller, targets=None, *, x_value=0,
                   oracle_override=None, removal_data=None, mode=None) -> bool:
    """Resolve one clause through the registry. Returns True when an effect
    was applied (the contract `resolve_spell_from_oracle` always had)."""
    ctx = _static_context(card, controller, oracle_override, removal_data,
                          game=game, targets=targets, x_value=x_value,
                          mode=mode)
    for h in PRE_ORACLE_HANDLERS:
        if h.gate(ctx):
            return h.apply(ctx)
    if not ctx.oracle:
        return False
    for h in HANDLERS:
        if h.gate(ctx):
            result = h.apply(ctx)
            if result is not None:
                return result
    return ctx.handled


def clause_is_executable(card, controller=0, *, oracle_override=None,
                         removal_data=None, mode=None) -> bool:
    """The one static answer to "can this clause run at all?" — some
    handler's gate accepts it. Reads no game state."""
    ctx = _static_context(card, controller, oracle_override, removal_data,
                          mode=mode)
    if any(h.gate(ctx) for h in PRE_ORACLE_HANDLERS):
        return True
    return bool(ctx.oracle) and any(h.gate(ctx) for h in HANDLERS)
