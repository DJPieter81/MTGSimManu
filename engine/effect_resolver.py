"""The effect dispatcher: sequences a host's typed EffectSpecs at resolution.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 11.

E0 ships the skeleton only. Its tables are empty and NOTHING calls it
(tests/test_effect_resolver_sequencing.py pins both), so no game behaviour
changes. A family switches later (E1-E7) by registering its executors and
calling `resolve_ability` from its carrier, falling back to the legacy apply
whenever `can_execute` refuses the host (A37, A38).

What this module owns is SEQUENCING (CR 608.2c): printed order, conditions,
"if you do" / "otherwise" branches, optional effects, "instead"
replacements, delayed (CR 603.7) and reflexive (CR 603.12) sub-abilities,
and the APNAP order of a player set's choices (CR 101.4). What it never does:

* read oracle text -- it sees only the parse-once `CardTemplate.effects`
  data (no regex, no grammar, no target parse at resolution);
* write game state -- every action is ONE executor call per spec into the
  owner that already exists (A33); the only game call it makes itself is
  registering a delayed trigger with the owner queue;
* pick targets -- an unbound slot reaches the owner unbound and the owner's
  own picker decides (A36);
* answer a choice -- "you may" goes through `callbacks.choose_optional_effect`
  (A35); the engine never scores.

`can_execute` fails closed: a spec is executable only when every part of it
has an owner that evaluates it (an executor for its verb, an evaluator for
each condition leaf, support for each filter entry, a tolerated residue
code, an applied modification kind, a bindable actor), recursively through
its sub-ability hosts.
"""
from __future__ import annotations

import dataclasses
from collections import namedtuple
from dataclasses import dataclass, field
from functools import partial
from typing import (Any, Callable, Dict, FrozenSet, Iterator, List, Mapping,
                    Optional, Sequence, Tuple, Union)

from .delayed_triggers import DelayedTrigger
from .effect_model import (APPLIED_MODKINDS, CLOCKED_DURATIONS, Selector,
                           SelectorKind, is_supported_filter_entry)
from .effect_spec import (UNPARSED, AbilityEffects, CardFilter, Condition,
                          ConditionKind, EffectSpec, Ref, RefKind, SubAbility,
                          SubAbilityKind, Verb, _refs, iter_specs,
                          residue_polarity)


# ── Bindings ───────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Handle:
    """An object, not a card (CR 400.7): the instance in one zone, entered
    once. A Handle whose object has since changed zone no longer binds, so
    an executor acting on it does nothing (CR 603.7c, A34).

    Known gap (open for E1): `entry_seq` is `battlefield_entry_seq`, the
    only zone-entry ordinal the engine keeps, and it counts battlefield
    entries only. A battlefield re-entry is told apart; a card that leaves
    a non-battlefield zone and returns to it (graveyard -> exile ->
    graveyard) gets an equal Handle, so CR 400.7 is NOT enforced outside
    the battlefield until CardInstance keeps a generic zone-entry ordinal."""
    instance_id: int
    zone: str
    entry_seq: int          # battlefield_entry_seq: battlefield entries only (see above)
    lki: Optional[object] = None   # characteristics snapshot taken when it left (CR 608.2h)


def handle_of(card: Any) -> Handle:
    """The Handle of a card object as it is now."""
    return Handle(card.instance_id, card.zone, card.battlefield_entry_seq)


# An object whose identity cannot be established (the legacy id names no
# card in the game). It never binds, so the owner sees the slot as illegal.
_UNKNOWN_ZONE = ""
_UNKNOWN_ENTRY = -1


Chosen = Tuple[Tuple[Union[Handle, int], ...], ...]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What a sub-ability created by a resolving spec keeps of its parent
    (A34): Handles and ints, never live objects -- frozen at creation, so a
    delayed trigger acts on what the parent did, not on what came later."""
    source: Handle
    controller: int
    family: Optional[str]
    results: Tuple[Tuple[int, Tuple[Tuple[int, tuple], ...]], ...]
    performed: Tuple[Tuple[int, bool], ...]
    x_value: int
    event: Optional[object]
    cast_facts: FrozenSet[str]

    def results_map(self) -> Dict[int, Dict[int, tuple]]:
        return {seq: dict(per) for seq, per in self.results}


@dataclass
class Resolution:
    """The runtime binding of one resolving host; never reads oracle text."""
    game: Any
    source: Handle
    controller: int
    ability: AbilityEffects
    chosen: Chosen                     # per slot of THIS host (never sub-ability slots)
    division: Dict[int, Tuple[int, ...]] = field(default_factory=dict)   # CR 601.2d
    x_value: int = 0
    event: Optional[object] = None
    cast_facts: FrozenSet[str] = frozenset()
    modes: Tuple[int, ...] = ()
    family: Optional[str] = None
    results: Dict[int, Dict[int, tuple]] = field(default_factory=dict)   # seq -> actor -> Handles/ints; {} if not performed
    performed: Dict[int, bool] = field(default_factory=dict)
    instead_holds: Dict[int, bool] = field(default_factory=dict)         # replacing seq -> evaluated once
    pending_reflexive: List[Tuple[SubAbility, Snapshot]] = field(default_factory=list)

    def snapshot(self) -> Snapshot:
        return Snapshot(
            source=self.source, controller=self.controller, family=self.family,
            results=tuple(sorted(
                (seq, tuple(sorted(((a, tuple(v)) for a, v in per.items()),
                                   key=lambda kv: kv[0])))
                for seq, per in self.results.items())),
            performed=tuple(sorted(self.performed.items())),
            x_value=self.x_value, event=self.event, cast_facts=self.cast_facts)


Outcome = namedtuple("Outcome", "performed result")   # result: actor -> tuple
# actors in APNAP order; ONE simultaneous action through the owner (A33).
Executor = Callable[[Resolution, EffectSpec, Tuple[int, ...]], Outcome]
ConditionEvaluator = Callable[[Resolution, Condition], bool]

# The verbs a family's executors own. An executor that evaluates a
# Ref(MEMBER) condition per member of its subject (A23) says so with an
# `evaluates_members = True` attribute.
EXECUTORS: Dict[Verb, Executor] = {}
# Per verb: FILTER (key, value) entries its executor hands to an owner that
# evaluates them, beyond effect_model.SUPPORTED_FILTER_VALUES (F5, A22).
EXECUTOR_FILTER_KEYS: Dict[Verb, FrozenSet[Tuple[str, Any]]] = {}
# family -> residue codes its legacy apply already resolves the same way
# (F4, M4). UNPARSED codes are never tolerable, listed or not.
LEGACY_RESIDUE_TOLERATED: Dict[str, FrozenSet[str]] = {}
# Leaf condition kinds an evaluator owns (E1 moves the quantity and
# condition evaluators into engine/effect_conditions.py). ALL_OF, ANY_OF
# and NOT are combinators `holds` evaluates itself.
CONDITION_EVALUATORS: Dict[ConditionKind, ConditionEvaluator] = {}

_COMBINATORS = frozenset({ConditionKind.ALL_OF, ConditionKind.ANY_OF,
                          ConditionKind.NOT})
_PLAYER_SETS = frozenset({SelectorKind.PLAYER, SelectorKind.OPPONENTS,
                          SelectorKind.ALL_PLAYERS})
_BRANCH_FIELDS = ("then", "otherwise", "alternatives")
_OWN_FIELDS = tuple(f.name for f in dataclasses.fields(EffectSpec)
                    if f.name not in _BRANCH_FIELDS)


# ── can_execute (section 11; fails closed) ─────────────────────────────

def can_execute(ability: AbilityEffects, family: Optional[str] = None) -> bool:
    """Can every spec of `ability` -- its branches, its modes and the
    sub-ability hosts it creates, but not the hosts it grants (they resolve
    when THEY are activated or trigger) -- be executed by an owner? Never
    raises: a malformed value is simply not executable."""
    try:
        return _host_executable(ability, family, {})
    except Exception:            # a malformed host must not escape (A37)
        return False


def _host_executable(host: AbilityEffects, family: Optional[str],
                     outer: Mapping[int, EffectSpec]) -> bool:
    if not isinstance(host, AbilityEffects):
        return False
    by_seq = dict(outer)
    by_seq.update((s.seq, s) for s in iter_specs(host.specs))
    # CR 603.4: the dispatcher rechecks every host's intervening-if on
    # resolution (_intervening_if_holds), top-level or sub-ability alike.
    if host.trigger is not None \
            and host.trigger.intervening_if is not None \
            and not _condition_executable(host.trigger.intervening_if):
        return False
    if not all(_replacements_well_formed(lst) for lst in _sibling_lists(host.specs)):
        return False
    if not all(_spec_executable(s, family, by_seq) for s in iter_specs(host.specs)):
        return False
    return all(_host_executable(m, family, by_seq) for m in host.modes)


def _sibling_lists(specs: Tuple[EffectSpec, ...]) -> Iterator[Tuple[EffectSpec, ...]]:
    """Each sequence the dispatcher runs in order: the host's specs and
    every then / otherwise / alternatives branch below them."""
    yield specs
    for s in specs:
        for name in _BRANCH_FIELDS:
            branch = getattr(s, name)
            if branch:
                yield from _sibling_lists(branch)


def _replacements_well_formed(specs: Tuple[EffectSpec, ...]) -> bool:
    """A replacing spec runs at its first victim's position, so every
    victim must be a sibling in the same sequence (else it never runs).
    A victim with several replacers ("X and Y instead") may not have an
    optional one: "you may X and Y instead" is one choice, not one per
    clause."""
    seqs = {s.seq for s in specs}
    if not all(set(s.replaces) <= seqs - {s.seq} for s in specs if s.replaces):
        return False
    replacers: Dict[int, List[EffectSpec]] = {}
    for s in specs:
        for v in s.replaces:
            replacers.setdefault(v, []).append(s)
    return not any(len(rs) > 1 and any(r.optional for r in rs)
                   for rs in replacers.values())


def _spec_executable(s: EffectSpec, family: Optional[str],
                     by_seq: Mapping[int, EffectSpec]) -> bool:
    verb = s.verb
    if verb is Verb.UNMODELLED:
        return False
    executor = None
    if verb is Verb.CREATE_TRIGGER:
        sub = s.payload
        if not isinstance(sub, SubAbility):
            return False
        if sub.kind is SubAbilityKind.DELAYED and sub.timing is None:
            return False
        if not _host_executable(sub.host, family, by_seq):
            return False
    else:
        executor = EXECUTORS.get(verb)
        if executor is None:
            return False
    # Defence in depth: every spec a RESULT ref can name (this host's, an
    # outer host's) is itself walked and refused if UNMODELLED, so this
    # never changes the verdict today; it keeps the rule local to the ref.
    if _refs_unmodelled(s, by_seq):
        return False
    tolerated = LEGACY_RESIDUE_TOLERATED.get(family, frozenset()) \
        if family is not None else frozenset()
    for code in s.residue:
        polarity = residue_polarity(code)
        if polarity is None or polarity == UNPARSED or code not in tolerated:
            return False
    if s.duration is not None \
            and getattr(s.duration, "kind", None) not in CLOCKED_DURATIONS:
        return False
    if s.mod_kind is not None and s.mod_kind not in APPLIED_MODKINDS:
        return False
    extra = EXECUTOR_FILTER_KEYS.get(verb, frozenset())
    for key, value in _filter_entries(s):
        if not (is_supported_filter_entry(key, value)
                or _contains(extra, (key, value))):
            return False
    if s.condition is not None:
        if not _condition_executable(s.condition):
            return False
        if _member_condition(s.condition) \
                and not getattr(executor, "evaluates_members", False):
            return False
    return _actor_bindable(s.actor)


def _contains(entries: FrozenSet[Tuple[str, Any]], entry: Tuple[str, Any]) -> bool:
    try:
        return entry in entries
    except TypeError:            # an unhashable value is in no table
        return False


def _filter_entries(s: EffectSpec) -> Iterator[Tuple[str, Any]]:
    """Every FILTER entry the spec's participants ask an owner to evaluate:
    FILTER selectors (subject, other, actor) and its untargeted CardFilter."""
    for p in (s.subject, s.other, s.actor):
        if isinstance(p, Selector) and p.kind is SelectorKind.FILTER:
            yield from (p.filter or ())
    if isinstance(s.filter, CardFilter):
        yield from s.filter.as_tuple()


def _refs_unmodelled(s: EffectSpec, by_seq: Mapping[int, EffectSpec]) -> bool:
    """Does `s` name the RESULT of an UNMODELLED spec (of its host or of a
    host that created it)?"""
    for name in _OWN_FIELDS:
        for r in _refs(getattr(s, name)):
            if r.kind is RefKind.RESULT:
                target = by_seq.get(r.index)
                if target is not None and target.verb is Verb.UNMODELLED:
                    return True
    return False


def _condition_executable(cond: Condition) -> bool:
    if not isinstance(cond, Condition):
        return False
    if cond.kind is ConditionKind.NOT:
        return len(cond.children) == 1 and _condition_executable(cond.children[0])
    if cond.kind in _COMBINATORS:
        return bool(cond.children) and all(_condition_executable(c)
                                           for c in cond.children)
    return cond.kind in CONDITION_EVALUATORS


def _member_condition(cond: Condition) -> bool:
    """Does the condition read Ref(MEMBER) -- each member of the spec's own
    quantified subject (A23)? Only the executor can evaluate that."""
    return any(r.kind is RefKind.MEMBER for r in _refs(cond))


def _actor_bindable(actor: Any) -> bool:
    """The acting-player shapes the dispatcher binds: the controller (no
    actor), a player set relative to the controller, or a chosen player
    target slot."""
    if actor is None:
        return True
    if isinstance(actor, Selector):
        return actor.kind in _PLAYER_SETS and actor.filter is None
    if isinstance(actor, Ref):
        return actor.kind is RefKind.TARGET and isinstance(actor.index, int)
    return False


# ── Conditions ─────────────────────────────────────────────────────────

def holds(ctx: Resolution, cond: Optional[Condition]) -> bool:
    """Evaluate a condition now. Leaves go to their evaluator; can_execute
    has already refused any leaf without one."""
    if cond is None:
        return True
    kind = cond.kind
    if kind is ConditionKind.ALL_OF:
        return all(holds(ctx, c) for c in cond.children)
    if kind is ConditionKind.ANY_OF:
        return any(holds(ctx, c) for c in cond.children)
    if kind is ConditionKind.NOT:
        return not holds(ctx, cond.children[0])
    return bool(CONDITION_EVALUATORS[kind](ctx, cond))


# ── resolve_ability ────────────────────────────────────────────────────

def resolve_ability(game: Any, source: Handle, controller: int,
                    ability: AbilityEffects, chosen: Chosen, *,
                    family: Optional[str] = None, x_value: int = 0,
                    event: Optional[object] = None,
                    cast_facts: FrozenSet[str] = frozenset(),
                    modes: Tuple[int, ...] = (),
                    division: Optional[Dict[int, Tuple[int, ...]]] = None) -> bool:
    """Resolve `ability` (with its chosen `modes`) in printed order.

    Returns False without raising -- and without touching the game -- when
    the host is not executable or a chosen mode does not exist: the carrier
    then falls back to its legacy apply (A37). Otherwise True iff any spec
    was performed."""
    if not can_execute(ability, family):
        return False
    if not all(isinstance(i, int) and 0 <= i < len(ability.modes) for i in modes):
        return False
    ctx = Resolution(game=game, source=source, controller=controller,
                     ability=ability, chosen=tuple(chosen),
                     division=dict(division or {}), x_value=x_value,
                     event=event, cast_facts=frozenset(cast_facts),
                     modes=tuple(modes), family=family)
    if not _intervening_if_holds(ctx, ability):
        return False
    specs = ability.specs + tuple(s for i in modes for s in ability.modes[i].specs)
    _sequence(ctx, specs)
    _drain_reflexive(ctx)
    return any(ctx.performed.values())


def _intervening_if_holds(ctx: Resolution, host: AbilityEffects) -> bool:
    """CR 603.4: a triggered ability's intervening-if is checked again as
    it resolves (the trigger-time check is its carrier's); false, the
    ability does nothing."""
    head = host.trigger
    return head is None or holds(ctx, head.intervening_if)


def _sequence(ctx: Resolution, specs: Tuple[EffectSpec, ...]) -> None:
    """CR 608.2c: in printed order. A replacing ("instead") spec runs only
    at its first victim's position; whether it applies is decided there,
    lazily and once: its condition (it may read earlier results; an UNLESS
    cost is offered once) and, for "you may ... instead", the controller's
    choice -- declined, the victims happen. A group of victims is replaced
    by one run of it (A33), and every replacer of a victim that applies
    runs there, in printed order ("X and Y instead")."""
    by_victim: Dict[int, List[EffectSpec]] = {}   # structural only
    for r in specs:
        for v in r.replaces:
            by_victim.setdefault(v, []).append(r)
    for s in specs:
        if s.replaces:
            continue
        applying = [r for r in by_victim.get(s.seq, ()) if _instead_applies(ctx, r)]
        if applying:
            _not_performed(ctx, s)              # the victim did not happen
            for r in applying:
                if r.seq not in ctx.performed:
                    _run(ctx, r, decided=True)
            continue
        _run(ctx, s)


def _instead_applies(ctx: Resolution, r: EffectSpec) -> bool:
    if r.seq not in ctx.instead_holds:
        ctx.instead_holds[r.seq] = holds(ctx, r.condition) and (
            not r.optional
            or bool(ctx.game.callbacks.choose_optional_effect(ctx, r)))
    return ctx.instead_holds[r.seq]


def _branch(ctx: Resolution, specs: Tuple[EffectSpec, ...]) -> None:
    if specs:
        _sequence(ctx, specs)


def _not_performed(ctx: Resolution, s: EffectSpec) -> None:
    # A35: RESULT / THAT_MUCH of a declined or unperformed spec is empty / 0.
    ctx.performed[s.seq] = False
    ctx.results[s.seq] = {}


def _run(ctx: Resolution, s: EffectSpec, *, decided: bool = False) -> None:
    """`decided`: a replacement whose condition and optional choice were
    already answered at its first victim (_instead_applies)."""
    # A per-member condition (A23) is the executor's to evaluate.
    if not decided and s.condition is not None \
            and not _member_condition(s.condition) and not holds(ctx, s.condition):
        _not_performed(ctx, s)
        return _branch(ctx, s.otherwise)
    if not decided and s.optional \
            and not ctx.game.callbacks.choose_optional_effect(ctx, s):
        _not_performed(ctx, s)
        return _branch(ctx, s.otherwise)
    if s.verb is Verb.CREATE_TRIGGER:
        _create_trigger(ctx, s)
        ctx.performed[s.seq] = True
        ctx.results[s.seq] = {}
        return _branch(ctx, s.then)
    actors = _actors_apnap(ctx, s)              # choices in APNAP order (CR 101.4) ...
    out = EXECUTORS[s.verb](ctx, s, actors)     # ... then ONE simultaneous action (A33)
    ctx.results[s.seq] = {a: tuple(v) for a, v in dict(out.result or {}).items()}
    ctx.performed[s.seq] = bool(out.performed)
    _branch(ctx, s.then if out.performed else s.otherwise)


def _apnap_order(game: Any) -> Tuple[int, ...]:
    n = len(game.players)
    return tuple((game.active_player + i) % n for i in range(n))


def _actors_apnap(ctx: Resolution, s: EffectSpec) -> Tuple[int, ...]:
    """The acting players, active player first then in turn order."""
    actor = s.actor
    if actor is None:
        return (ctx.controller,)
    order = _apnap_order(ctx.game)
    if isinstance(actor, Selector):
        bound = dataclasses.replace(actor, player=ctx.controller)
        return tuple(p for p in order if bound.covers_player(p))
    slot = ctx.chosen[actor.index] if actor.index < len(ctx.chosen) else ()
    players = {v for v in slot if isinstance(v, int) and not isinstance(v, bool)}
    return tuple(p for p in order if p in players)


# ── Sub-abilities (A30) ────────────────────────────────────────────────

def _create_trigger(ctx: Resolution, s: EffectSpec) -> None:
    sub, snap = s.payload, ctx.snapshot()       # Handles, never objects (A34)
    if sub.kind is SubAbilityKind.DELAYED:      # CR 603.7; 603.7c: a moved object is not acted on
        # The queue calls effect(game) when the trigger is due.
        ctx.game.register_delayed_trigger(DelayedTrigger(
            timing=sub.timing, controller=ctx.controller,
            effect=partial(resolve_sub_ability, sub=sub, snap=snap),
            description=s.raw, created_turn=ctx.game.turn_number))
    else:                                       # CR 603.12: after the parent
        ctx.pending_reflexive.append((sub, snap))


def _drain_reflexive(ctx: Resolution) -> None:
    # Inline after the parent until T2 makes them stack items.
    while ctx.pending_reflexive:
        sub, snap = ctx.pending_reflexive.pop(0)
        resolve_sub_ability(ctx.game, sub, snap)


def resolve_sub_ability(game: Any, sub: SubAbility, snap: Snapshot) -> bool:
    """Resolve a delayed or reflexive ability its parent created. Its
    targets are its own, chosen now -- the dispatcher binds none, so each
    slot reaches the owner unbound -- and its intervening-if (CR 603.4) is
    checked again now. True iff any of its specs was performed."""
    host = sub.host
    if not can_execute(host, snap.family):
        return False
    ctx = Resolution(game=game, source=snap.source, controller=snap.controller,
                     ability=host, chosen=tuple(() for _ in host.targets),
                     x_value=snap.x_value, event=snap.event,
                     cast_facts=snap.cast_facts, family=snap.family)
    own = {s.seq for s in iter_specs(host.specs)}
    ctx.results.update((k, v) for k, v in snap.results_map().items() if k not in own)
    ctx.performed.update((k, v) for k, v in snap.performed if k not in own)
    if not _intervening_if_holds(ctx, host):
        return False
    _sequence(ctx, host.specs)
    _drain_reflexive(ctx)
    return any(ctx.performed.get(k, False) for k in own)


# ── The legacy target adapter (A36) ────────────────────────────────────

def chosen_from_legacy(ability: AbilityEffects, item_targets: Sequence[int],
                       positions: Sequence[int], *,
                       slot_spans: Sequence[Tuple[int, int]], game: Any,
                       face: int) -> Chosen:
    """Map the legacy flat `item.targets` onto this host's per-slot tuples.

    `positions[i]` is the printed position, in `ability.text`, of the
    requirement `item_targets[i]` was chosen for (the legacy list is in
    whole-oracle category order; the caller locates each entry with the
    parse-once `parse_located`). `slot_spans[k]` is the printed
    `[start, end)` of slot k's target phrase in `ability.text` (the caller's
    `parse_spans`; this function reads no text). Each position goes to the
    slot whose span holds it (A36 step 2) -- never to its rank among the
    positions present, so an unchosen earlier slot shifts nothing.

    An entry outside the host (another host's target, or -1 = unlocated)
    is not this host's. An in-host position no slot's span holds, or one
    two spans hold, is a disagreement between the legacy parse and the
    host: ValueError. The -1 face sentinel becomes the `face` player; an
    id is the Handle of that object now. A slot nothing maps to stays
    empty: the owner's picker decides (A36)."""
    if len(item_targets) != len(positions):
        raise ValueError("one position per legacy target")
    if len(slot_spans) != len(ability.targets):
        raise ValueError("one printed span per host slot")
    slots: List[List[Union[Handle, int]]] = [[] for _ in ability.targets]
    for tid, p in zip(item_targets, positions):
        if not 0 <= p < len(ability.text):
            continue
        ks = [k for k, (a, b) in enumerate(slot_spans) if a <= p < b]
        if len(ks) != 1:
            raise ValueError(f"position {p} is held by {len(ks)} host slots")
        slots[ks[0]].append(face if tid == -1 else _legacy_handle(game, tid))
    return tuple(tuple(s) for s in slots)


def _legacy_handle(game: Any, tid: int) -> Handle:
    card = game.get_card_by_id(tid)
    if card is None:
        return Handle(tid, _UNKNOWN_ZONE, _UNKNOWN_ENTRY)
    return handle_of(card)
