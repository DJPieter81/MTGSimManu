"""The effect dispatcher's executors and condition evaluators, by family.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, sections 11
and 14. `engine/effect_resolver.py` imports this module last, which registers
each family into its tables.

An executor binds one typed EffectSpec at resolution and performs it through
the owner that already exists -- damage through `engine.damage.deal_damage`
(for a chosen target, through `oracle_resolver.resolve_damage_to_chosen_target`,
the owner of "deal N damage to the chosen target"), life loss through
`engine.damage.lose_life`, life gain through `GameState.gain_life`. It never
writes game state itself, never reads oracle text, and adds no log line of
its own: logging is the owner's.

Every executor and evaluator declares the shapes it binds (`supports`), and
`can_execute` refuses any other shape -- an amount, recipient, source or
predicate nothing here evaluates -- so the carrier keeps its legacy apply
(fail closed, A37).

Family `damage` (E1): DAMAGE to a chosen "any target" slot, LOSE_LIFE and
GAIN_LIFE by acting players, and the STATE conditions printed upgrades read.
Until the family's behaviour-change commits (E1.b), a chosen target reaches
the owner as the legacy target list, so the owner's own illegal-target rule
applies unchanged (A36).
"""
from __future__ import annotations

from typing import Any, Tuple

from . import effect_conditions as conditions
from .effect_resolver import (CONDITION_EVALUATORS, EXECUTORS, Handle,
                              Outcome, Resolution, handle_of)
from .effect_spec import (Chooser, Condition, ConditionKind, EffectSpec, Ref,
                          RefKind, Verb)

FAMILY_DAMAGE = "damage"

# The verbs each family's executors own (the tables' contents, pinned by
# tests/test_effect_resolver_sequencing.py).
FAMILIES = {FAMILY_DAMAGE: frozenset({Verb.DAMAGE, Verb.LOSE_LIFE,
                                      Verb.GAIN_LIFE})}


# ── Binding helpers ───────────────────────────────────────────────────

def _source_object(ctx: Resolution) -> Any:
    """The object the ability's source is: the carrier's own when it passed
    one (a resolving spell is in no zone), else the Handle's object now."""
    if ctx.source_object is not None:
        return ctx.source_object
    return ctx.game.get_card_by_id(ctx.source.instance_id)


def _plain_participants(s: EffectSpec) -> bool:
    """No part of the spec this family does not bind: no optional choice
    (its callback is not wired), no alternatives, no untargeted filter, no
    simultaneity group, no destination, payload or duration, and the
    controller chooses."""
    return (not s.optional and not s.alternatives and s.filter is None
            and s.group is None and s.dest is None and s.payload is None
            and s.duration is None and s.chooser is Chooser.CONTROLLER)


def _amount(ctx: Resolution, s: EffectSpec) -> int:
    return conditions.amount_value(ctx.game, ctx.controller, s.amount,
                                   ctx.x_value)


# ── DAMAGE ────────────────────────────────────────────────────────────

def _damage_supported(s: EffectSpec) -> bool:
    """"~ deals <amount> damage to any target": the source deals it (CR
    120.1) to the one object or player chosen for an "any target" slot
    (CR 115.4)."""
    if s.verb is not Verb.DAMAGE or not _plain_participants(s) or s.flags:
        return False
    src = s.other
    if not (isinstance(src, Ref) and src.kind is RefKind.SELF and not src.lki):
        return False
    req = s.target
    if s.target_slot is None or s.subject is not None or s.ref is not None \
            or s.actor is not None or req is None \
            or set(req.types) != {"any"} or req.count_max != 1:
        return False
    return conditions.amount_supported(s.amount)


def execute_damage(ctx: Resolution, s: EffectSpec,
                   actors: Tuple[int, ...]) -> Outcome:
    from .oracle_resolver import resolve_damage_to_chosen_target
    amount = _amount(ctx, s)
    slot = ctx.chosen[s.target_slot] if s.target_slot < len(ctx.chosen) else ()
    # The owner's legacy target list: an object by id, the opponent's face
    # by its -1 sentinel (`chosen_from_legacy` is the inverse mapping).
    face = 1 - ctx.controller
    legacy = [v.instance_id if isinstance(v, Handle) else -1
              for v in slot if isinstance(v, Handle) or v == face]
    hit = resolve_damage_to_chosen_target(ctx.game, _source_object(ctx),
                                          ctx.controller, amount, legacy)
    if amount <= 0:                 # CR 120.8: no damage is dealt
        return Outcome(False, {})
    recipient = handle_of(hit) if hit is not None else face
    return Outcome(True, {ctx.controller: (recipient,)})


execute_damage.supports = _damage_supported


# ── LOSE_LIFE / GAIN_LIFE ─────────────────────────────────────────────

def _life_supported(s: EffectSpec) -> bool:
    """"<player(s)> lose / gain <amount> life", the acting players bound by
    the dispatcher (the controller, a player set, or a chosen player)."""
    if s.verb not in (Verb.LOSE_LIFE, Verb.GAIN_LIFE) \
            or not _plain_participants(s) or s.flags - {"each"}:
        return False
    if s.subject is not None or s.ref is not None or s.other is not None:
        return False
    if s.target_slot is not None and not (
            isinstance(s.actor, Ref) and s.actor.kind is RefKind.TARGET
            and s.actor.index == s.target_slot):
        return False
    return conditions.amount_supported(s.amount)


def execute_lose_life(ctx: Resolution, s: EffectSpec,
                      actors: Tuple[int, ...]) -> Outcome:
    from .damage import lose_life
    amount = _amount(ctx, s)
    if amount <= 0 or not actors:
        return Outcome(False, {})
    for p in actors:                # CR 119.3: loss of life, not damage
        lose_life(ctx.game, p, amount)
    return Outcome(True, {p: (amount,) for p in actors})


def execute_gain_life(ctx: Resolution, s: EffectSpec,
                      actors: Tuple[int, ...]) -> Outcome:
    amount = _amount(ctx, s)
    if amount <= 0 or not actors:
        return Outcome(False, {})
    source = _source_object(ctx)
    for p in actors:                # CR 119.3
        ctx.game.gain_life(p, amount, getattr(source, "name", ""))
    return Outcome(True, {p: (amount,) for p in actors})


execute_lose_life.supports = _life_supported
execute_gain_life.supports = _life_supported


# ── Conditions ────────────────────────────────────────────────────────

def evaluate_state(ctx: Resolution, cond: Condition) -> bool:
    return conditions.state_condition_holds(ctx.game, ctx.controller, cond)


evaluate_state.supports = conditions.state_condition_supported


# ── Registration ──────────────────────────────────────────────────────

EXECUTORS[Verb.DAMAGE] = execute_damage
EXECUTORS[Verb.LOSE_LIFE] = execute_lose_life
EXECUTORS[Verb.GAIN_LIFE] = execute_gain_life
CONDITION_EVALUATORS[ConditionKind.STATE] = evaluate_state
