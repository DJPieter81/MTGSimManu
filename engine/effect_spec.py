"""The typed effect model: what a card's oracle text DOES, as frozen data.

Design doc: docs/design/2026-09-29_clause_and_trigger_grammar.md, section 2.

One grammar (`engine/effect_grammar/`, later) parses every face's oracle text
once, at load, into a `CardEffects`: per face, a tuple of `AbilityEffects`
hosts (a spell, a mode, an activated / loyalty / triggered / static ability,
a keyword line, a cost), each holding its `EffectSpec`s in printed order. One
dispatcher (`engine/effect_resolver.py`, later) executes specs through the
owners that already exist. This module is the schema only:

* every class is ``@dataclass(frozen=True, slots=True)``: hashable,
  picklable, shareable across the parse memo; no field holds a mutable
  object (A31 -- costs are `CostSnapshot`s, never `ActivationCost`s);
* it parses nothing and has no game access. Its only engine imports are
  `TargetRequirement` (target_solver), `Selector` / `Duration` /
  `Modification` (effect_model) and `DelayedTriggerTiming`
  (delayed_triggers); `CostSnapshot.thaw` imports `ActivationCost` lazily.

`canonical()` is the hash-seed-independent text form (frozensets sorted,
enums by name) that determinism tests compare (F10). `validate_spec()`
checks the eight schema invariants (SCHEMA_INVARIANTS), returns the violated
rule by name and never raises; `enforce_invariants()` lowers a violating
spec to UNMODELLED(INVALID). `RESIDUE_CODES` gives every target-residue code
its polarity (A21).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping as _AbcMapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import (TYPE_CHECKING, Any, FrozenSet, Iterator, Mapping, Optional,
                    Tuple, Union)

from .delayed_triggers import DelayedTriggerTiming
from .effect_model import (Duration, DurationKind, Modification, Selector,
                           SelectorKind)
from .target_solver import TargetRequirement

if TYPE_CHECKING:
    from .cards import ActivationCost

GRAMMAR_VERSION = 1

_FROZEN = dict(frozen=True, slots=True)


# ── Vocabulary ──────────────────────────────────────────────────────────

class Verb(Enum):
    # zone (CR 701.8, 701.13, 701.21, 400.7, 701.24)
    DESTROY = "destroy"
    EXILE = "exile"
    SACRIFICE = "sacrifice"
    MOVE = "move"              # return/put/shuffle <object> to|onto|into <dest>
    SHUFFLE = "shuffle"        # a player shuffles their library
    DAMAGE = "damage"
    FIGHT = "fight"
    LOSE_LIFE = "lose_life"
    GAIN_LIFE = "gain_life"
    SET_LIFE = "set_life"
    EXCHANGE_LIFE = "exchange_life"
    DRAW = "draw"
    DISCARD = "discard"
    MILL = "mill"
    SCRY = "scry"
    SURVEIL = "surveil"
    LOOK = "look"
    REVEAL = "reveal"
    REVEAL_UNTIL = "reveal_until"
    SEARCH = "search"
    CHOOSE = "choose"
    CAST_FREE = "cast_free"
    CREATE_TOKEN = "create_token"
    PUT_COUNTERS = "put_counters"
    REMOVE_COUNTERS = "remove_counters"
    MOVE_COUNTERS = "move_counters"
    DOUBLE_COUNTERS = "double_counters"
    PLAYER_COUNTERS = "player_counters"
    KEYWORD_ACTION = "keyword_action"    # CR 701 action; payload KeywordAction
    CONTINUOUS = "continuous"            # CR 611-613; payload Modification
    TAP = "tap"
    UNTAP = "untap"
    TRANSFORM = "transform"
    ATTACH = "attach"
    COUNTER = "counter"
    ADD_MANA = "add_mana"
    PAY = "pay"
    COPY = "copy"
    CHANGE_TARGETS = "change_targets"
    CREATE_EMBLEM = "create_emblem"
    EXTRA_TURN = "extra_turn"
    END_TURN = "end_turn"
    SKIP = "skip"
    CREATE_TRIGGER = "create_trigger"    # delayed (CR 603.7) / reflexive (CR 603.12)
    UNMODELLED = "unmodelled"


class HostKind(Enum):
    SPELL = "spell"
    MODE = "mode"
    ACTIVATED = "activated"
    MANA_ABILITY = "mana_ability"
    LOYALTY = "loyalty"
    TRIGGERED = "triggered"
    CHAPTER = "chapter"
    STATIC = "static"
    KEYWORD = "keyword"
    ADDITIONAL_COST = "additional_cost"
    ALTERNATIVE_COST = "alternative_cost"   # CR 118.9 (A2)
    REPLACEMENT = "replacement"
    GRANTED = "granted"
    UNKNOWN = "unknown"


class RefKind(Enum):
    SELF = "self"
    TARGET = "target"
    RESULT = "result"
    EVENT_OBJECT = "event_object"
    EVENT_PLAYER = "event_player"
    LINKED = "linked"
    ATTACHED = "attached"
    CONTROLLER_OF = "controller_of"
    OWNER_OF = "owner_of"
    CHOSEN = "chosen"
    DEFENDING_PLAYER = "defending_player"
    MEMBER = "member"    # the member of this spec's own quantified subject (A23)


class RefPart(Enum):
    ALL = "all"
    ONE = "one"
    REST = "rest"
    OTHER = "other"
    EACH = "each"


class AmountKind(Enum):
    LITERAL = "literal"
    X = "x"
    X_DEFINED = "x_defined"
    EQUAL_TO = "equal_to"
    FOR_EACH = "for_each"
    THAT_MUCH = "that_much"
    ALL = "all"
    ANY_NUMBER = "any_number"
    UP_TO = "up_to"
    HALF = "half"
    MULTIPLY = "multiply"
    PLUS = "plus"
    DIVIDED = "divided"
    WHOLE_ZONE = "whole_zone"


class QuantityKind(Enum):
    COUNT = "count"
    CARDS_IN = "cards_in"
    POWER = "power"
    TOUGHNESS = "toughness"
    MANA_VALUE = "mana_value"
    COUNTERS_ON = "counters_on"
    LIFE_TOTAL = "life_total"
    DEVOTION = "devotion"
    BASIC_LAND_TYPES = "basic_land_types"
    COLORS_SPENT = "colors_spent"
    CARD_TYPES_IN_GRAVEYARD = "card_types_in_graveyard"
    GREATEST = "greatest"
    TOTAL = "total"
    TIMES_KICKED = "times_kicked"
    RESULT_SIZE = "result_size"
    HISTORY = "history"


class ConditionKind(Enum):
    STATE = "state"
    OBJECT = "object"
    CAST_FACT = "cast_fact"
    TURN = "turn"
    HISTORY = "history"
    RESOLUTION_ORDINAL = "resolution_ordinal"
    UNLESS = "unless"
    ALL_OF = "all_of"
    ANY_OF = "any_of"
    NOT = "not"


class SubAbilityKind(Enum):
    DELAYED = "delayed"        # CR 603.7
    REFLEXIVE = "reflexive"    # CR 603.12


class Stage(Enum):
    """Where the grammar gave up on a clause (UNMODELLED payload)."""
    STRUCTURE = "structure"
    SPLIT = "split"
    NO_LEMMA = "no_lemma"
    CLAUSE = "clause"
    TARGET = "target"
    TARGET_COUNT = "target_count"
    FILTER = "filter"
    AMOUNT = "amount"
    QUANTITY = "quantity"
    CONDITION = "condition"
    DURATION = "duration"
    DELAY = "delay"
    REFERENCE = "reference"
    ITERATION = "iteration"
    TRIGGER_EMBEDDED = "trigger_embedded"
    RECOGNIZED_UNSUPPORTED = "recognized_unsupported"
    REPLACEMENT = "replacement"
    INVALID = "invalid"


class Chooser(Enum):
    CONTROLLER = "controller"
    PARTICIPANT = "participant"
    OPPONENT = "opponent"
    RANDOM = "random"


class EventHint(Enum):
    SELF_ENTERS = "self_enters"
    SELF_DIES = "self_dies"
    SELF_LEAVES = "self_leaves"
    SELF_ATTACKS = "self_attacks"
    SELF_CAST = "self_cast"
    OTHER_ENTERS = "other_enters"
    OTHER_DIES = "other_dies"
    ATTACKS_OTHER = "attacks_other"
    SPELL_CAST = "spell_cast"
    BEGINNING_OF = "beginning_of"
    COMBAT_DAMAGE_TO_PLAYER = "combat_damage_to_player"
    LANDFALL = "landfall"
    COUNTERS_PUT = "counters_put"
    CYCLE = "cycle"
    TAPPED_FOR_MANA = "tapped_for_mana"
    REFLEXIVE = "reflexive"
    DELAYED = "delayed"
    OTHER = "other"


# Closed flag vocabularies (documentation of the allowed values).
SPEC_FLAGS: FrozenSet[str] = frozenset({
    "reflexive", "may_scope", "if_you_do", "reveal", "no_regeneration",
    "at_random", "each", "gapped", "dest_override"})
HOST_FLAGS: FrozenSet[str] = frozenset({
    "uncounterable", "sorcery_speed", "mana_ability"})


# ── Participants ───────────────────────────────────────────────────────

@dataclass(**_FROZEN)
class Ref:
    """A reference to an object or player the ability already knows."""
    kind: RefKind
    index: Optional[int] = None      # TARGET: owning host's targets[k]; RESULT: producing spec.seq; LINKED: host index
    part: RefPart = RefPart.ALL
    n: Optional["Amount"] = None
    of: Optional["Ref"] = None       # operand of CONTROLLER_OF / OWNER_OF
    lki: bool = False                # last-known information, CR 608.2h (A27)
    per_actor: bool = False          # RESULT bound per actor of a multi-player spec
    noun: str = ""


Participant = Union[TargetRequirement, Selector, Ref]


@dataclass(**_FROZEN)
class CardFilter:
    """An untargeted / hidden-zone object description; never a target."""
    zone: str = "battlefield"
    types: FrozenSet[str] = frozenset()
    all_types: FrozenSet[str] = frozenset()
    not_types: FrozenSet[str] = frozenset()
    supertypes: FrozenSet[str] = frozenset()
    not_supertypes: FrozenSet[str] = frozenset()
    subtypes: FrozenSet[str] = frozenset()
    not_subtypes: FrozenSet[str] = frozenset()
    colors: FrozenSet[str] = frozenset()
    not_colors: FrozenSet[str] = frozenset()
    # Closed CR-defined derived classes: historic (CR 700.6), colored,
    # multicolored, monocolored (A19).
    classes: FrozenSet[str] = frozenset()
    colorless: Optional[bool] = None
    controller: Union[str, Ref] = "any"
    owner: Union[str, Ref] = "any"
    other: bool = False
    token: Optional[bool] = None
    stat_bounds: Tuple[Tuple[str, str, "Amount"], ...] = ()
    with_keywords: FrozenSet[str] = frozenset()
    without_keywords: FrozenSet[str] = frozenset()
    state: FrozenSet[str] = frozenset()
    counters: Tuple[Tuple[str, bool], ...] = ()
    named: Union[None, str, Ref] = None
    position: Optional[str] = None
    different_names: bool = False
    raw: str = ""

    def as_tuple(self) -> Tuple[Tuple[str, Any], ...]:
        """The set (non-default) entries as `Selector.filter` pairs, sets
        sorted. One excluded keyword is the ('without_keyword', kw) entry
        `Selector.covers_object` evaluates; several are one
        ('without_keywords', (...)) entry, which it does not -- so a
        dispatcher refuses rather than silently checking only one."""
        out = []
        for key, default in _CARD_FILTER_DEFAULTS:
            v = getattr(self, key)
            if v == default:
                continue
            if key in _KEYWORD_EXCLUSION_FIELD:
                kws = tuple(sorted(v))
                out.append(("without_keyword", kws[0]) if len(kws) == 1
                           else ("without_keywords", kws))
                continue
            if isinstance(v, frozenset):
                v = tuple(sorted(v))
            out.append((key, v))
        return tuple(out)

    def as_selector(self) -> Selector:
        """A FILTER selector; its player is bound by the dispatcher."""
        return Selector(SelectorKind.FILTER, player=None, filter=self.as_tuple())


# CardFilter fields in declaration order with their defaults; `raw` (the
# printed phrase) is description, not a filter entry.
_CARD_FILTER_DEFAULTS: Tuple[Tuple[str, Any], ...] = tuple(
    (f.name, f.default) for f in dataclasses.fields(CardFilter)
    if f.name not in {"raw"})
_KEYWORD_EXCLUSION_FIELD = frozenset({"without_keywords"})


# ── Amounts, quantities, costs, conditions ─────────────────────────────

@dataclass(**_FROZEN)
class Amount:
    kind: AmountKind
    # LITERAL: signed value | X: sign (+1/-1, loyalty costs) | FOR_EACH: per
    # unit | MULTIPLY: factor | PLUS: addend | UP_TO: bound.
    n: int = 0
    quantity: Optional["Quantity"] = None
    ref: Optional[Ref] = None
    inner: Optional["Amount"] = None
    rounding: Optional[str] = None
    evenly: bool = False


@dataclass(**_FROZEN)
class Quantity:
    kind: QuantityKind
    filter: Optional[CardFilter] = None
    player: Union[str, Ref] = "you"
    ref: Optional[Ref] = None        # COUNTERS_ON(Ref(SELF, lki=True)) after a sacrifice-as-cost (A27)
    stat: Optional[str] = None
    counter_kind: Optional[str] = None
    event: Optional[str] = None      # HISTORY closed vocabulary
    raw: str = ""


@dataclass(**_FROZEN)
class CostSnapshot:
    """A frozen image of an `ActivationCost` (A31). `items` holds every
    field sorted by name, nested dataclasses (ManaCost) flattened the same
    way, lists/dicts as tuples. `thaw()` returns a fresh mutable object per
    call, never a shared one."""
    items: Tuple[Tuple[str, Any], ...] = ()

    def thaw(self) -> "ActivationCost":
        from .cards import ActivationCost
        return _thaw_dataclass(ActivationCost, self.items)


def _freeze_value(v: Any) -> Any:
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return _freeze_dataclass(v)
    if isinstance(v, dict):
        return tuple(sorted((k, _freeze_value(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(_freeze_value(x) for x in v)
    if isinstance(v, (set, frozenset)):
        return tuple(sorted(_freeze_value(x) for x in v))
    return v


def _freeze_dataclass(obj: Any) -> Tuple[Tuple[str, Any], ...]:
    return tuple(sorted((f.name, _freeze_value(getattr(obj, f.name)))
                        for f in dataclasses.fields(obj)))


def _field_prototype(f: dataclasses.Field) -> Any:
    if f.default_factory is not dataclasses.MISSING:   # type: ignore[misc]
        return f.default_factory()                      # type: ignore[misc]
    return None if f.default is dataclasses.MISSING else f.default


def _thaw_dataclass(cls: type, items: Tuple[Tuple[str, Any], ...]) -> Any:
    """Rebuild a mutable `cls` from `_freeze_dataclass` output, restoring
    each container field to its declared kind (read off the field's
    default)."""
    values = dict(items)
    init, late = {}, {}
    for f in dataclasses.fields(cls):
        if f.name not in values:
            continue
        v, proto = values[f.name], _field_prototype(f)
        if dataclasses.is_dataclass(proto):
            v = _thaw_dataclass(type(proto), v)
        elif isinstance(proto, dict):
            v = {k: x for k, x in v}
        elif isinstance(proto, list):
            v = list(v)
        elif isinstance(proto, set):
            v = set(v)
        (init if f.init else late)[f.name] = v
    obj = cls(**init)
    for k, v in late.items():
        setattr(obj, k, v)
    return obj


def freeze_cost(cost: Optional["ActivationCost"]) -> Optional[CostSnapshot]:
    """The frozen, hashable image of a parsed activation cost (None stays None)."""
    if cost is None:
        return None
    return CostSnapshot(items=_freeze_dataclass(cost))


@dataclass(**_FROZEN)
class Condition:
    kind: ConditionKind
    pred: str = ""
    ref: Optional[Ref] = None        # OBJECT subject (Ref(TARGET k) / Ref(MEMBER)) / UNLESS payer
    payer: Optional[Selector] = None
    filter: Optional[CardFilter] = None
    op: str = ">="
    n: Optional[Amount] = None
    cost: Optional[CostSnapshot] = None
    children: Tuple["Condition", ...] = ()
    label: str = ""                  # ability word (CR 207.2c): informational only
    raw: str = ""


@dataclass(**_FROZEN)
class Destination:
    zone: str
    position: Optional[str] = None   # top | bottom | shuffle | nth
    nth: Optional[int] = None
    order: Optional[str] = None
    tapped: bool = False
    attacking: bool = False
    transformed: bool = False
    face_down: bool = False
    controller: str = "default"      # 'you' | 'owner' | 'default'
    entry_counters: Tuple[Tuple[str, Amount], ...] = ()
    attached_to: Optional[Ref] = None
    instead_of: Optional[str] = None  # the zone this destination replaces (A15)


# ── Payloads ───────────────────────────────────────────────────────────

@dataclass(**_FROZEN)
class CounterSpec:
    kinds: Tuple[str, ...] = ()
    choice: bool = False


@dataclass(**_FROZEN)
class ManaSpec:
    symbols: Tuple[str, ...] = ()
    choice: Tuple[str, ...] = ()
    any_color: bool = False
    one_color: bool = False
    combination: bool = False
    chosen_color: bool = False
    mirror: Optional[Ref] = None
    restriction: str = ""


@dataclass(**_FROZEN)
class KeywordSpec:
    name: str
    n: Optional[int] = None
    cost: Optional[str] = None
    cost_snapshot: Optional[CostSnapshot] = None
    param: Optional[str] = None
    cost_rule: str = ""              # 'mana_cost' for "flashback cost is equal to its mana cost" (A8)


@dataclass(**_FROZEN)
class KeywordAction:
    name: str
    amount: Optional[Amount] = None
    subtype: Optional[str] = None
    expansion: Tuple["EffectSpec", ...] = ()


@dataclass(**_FROZEN)
class Granted:
    hosts: Tuple["AbilityEffects", ...] = ()


@dataclass(**_FROZEN)
class TokenSpec:
    power: Optional[Amount] = None
    toughness: Optional[Amount] = None
    colors: FrozenSet[str] = frozenset()
    types: Tuple[str, ...] = ()
    subtypes: Tuple[str, ...] = ()
    supertypes: Tuple[str, ...] = ()
    keywords: Tuple[Tuple[str, Optional[str]], ...] = ()
    name: Optional[str] = None
    predefined: Optional[str] = None
    granted: Tuple["AbilityEffects", ...] = ()
    copy_of: Optional[Participant] = None
    copy_except: Tuple["EffectSpec", ...] = ()


@dataclass(**_FROZEN)
class SubAbility:
    """A delayed (CR 603.7) or reflexive (CR 603.12) triggered ability
    created by a resolving spec (A30). The nested host owns its specs, its
    targets (chosen when it triggers, never with the parent) and its own
    TriggerHead with any intervening-if (CR 603.4)."""
    kind: SubAbilityKind
    timing: Optional[DelayedTriggerTiming]    # DELAYED only
    host: "AbilityEffects"


@dataclass(**_FROZEN)
class Unmodelled:
    stage: Stage
    lemma: str = ""
    detail: str = ""


Payload = Union[Modification, TokenSpec, CounterSpec, ManaSpec, KeywordAction,
                Granted, CostSnapshot, SubAbility, Unmodelled]


# ── The spec, the host, the card ───────────────────────────────────────

@dataclass(**_FROZEN)
class EffectSpec:
    verb: Verb
    target: Optional[TargetRequirement] = None   # ONLY from target_solver; is owning host.targets[target_slot]
    target_slot: Optional[int] = None
    subject: Optional[Selector] = None
    ref: Optional[Ref] = None
    other: Optional[Participant] = None
    actor: Optional[Participant] = None
    filter: Optional[CardFilter] = None
    amount: Optional[Amount] = None
    chooser: Chooser = Chooser.CONTROLLER
    dest: Optional[Destination] = None
    payload: Optional[Payload] = None
    duration: Optional[Duration] = None          # effect_model.Duration, kind only
    condition: Optional[Condition] = None
    optional: bool = False
    then: Tuple["EffectSpec", ...] = ()          # iff THIS spec was performed / accepted
    otherwise: Tuple["EffectSpec", ...] = ()     # iff not performed, declined, or condition false
    replaces: Tuple[int, ...] = ()               # 'instead': seqs of earlier siblings replaced
    alternatives: Tuple["EffectSpec", ...] = ()  # 'your choice of X or Y' (A19)
    group: Optional[int] = None                  # simultaneity group
    flags: FrozenSet[str] = frozenset()          # SPEC_FLAGS
    residue: Tuple[str, ...] = ()                # RESIDUE_CODES (A21)
    seq: int = 0
    span: Tuple[int, int] = (0, 0)               # into the owning AbilityEffects.text
    raw: str = ""

    @property
    def mod_kind(self):
        return self.payload.kind if isinstance(self.payload, Modification) else None


@dataclass(**_FROZEN)
class TriggerHead:
    event_hints: Tuple[EventHint, ...] = ()      # disjunctive heads keep every hint (A11)
    raw: str = ""
    step: str = ""
    intervening_if: Optional[Condition] = None   # CR 603.4; also on reflexive heads
    once_each_turn: bool = False
    frequency_raw: str = ""
    # What the head's event names, typed by the participant leaf at L1
    # (`participant.head_names`): a player ("whenever an opponent casts")
    # and / or an object besides the source ("whenever a creature dies").
    # The linker's host antecedent reads these, never the raw head.
    names_player: bool = False
    names_object: bool = False


@dataclass(**_FROZEN)
class AbilityEffects:
    kind: HostKind
    face: int
    index: int
    paragraphs: Tuple[int, ...] = ()
    text: str = ""
    specs: Tuple[EffectSpec, ...] = ()
    targets: Tuple[TargetRequirement, ...] = ()    # this host's own targets only
    target_alts: Tuple[Tuple[int, int], ...] = ()
    trigger: Optional[TriggerHead] = None
    cost: Optional[CostSnapshot] = None            # parse_activation_cost(PRINTED head) (A7)
    cost_modifiers: Tuple[Modification, ...] = ()  # COST_DELTA from "This ability costs …" (A8)
    cost_condition: Optional[Condition] = None     # ALTERNATIVE_COST condition (A2)
    activation_index: Optional[int] = None
    loyalty_cost: Optional[Amount] = None          # LITERAL signed, or X with sign (A12)
    loyalty_slot: str = ""
    chapters: Tuple[int, ...] = ()
    modes: Tuple["AbilityEffects", ...] = ()
    choose: Tuple[int, int] = (0, 0)
    mode_index: int = -1
    mode_cost: str = ""                            # spree '+ {1}', tiered '{2}' (A4)
    label: str = ""
    keywords: Tuple[KeywordSpec, ...] = ()
    from_zone: str = "battlefield"
    flags: FrozenSet[str] = frozenset()            # HOST_FLAGS
    restrictions: Tuple[str, ...] = ()


# Payload fields that hold specs of the SAME host (walked by iter_specs):
# a keyword action's rules-English expansion and a token copy's exceptions.
# A payload's hosts (SubAbility.host, Granted.hosts, TokenSpec.granted) are
# other hosts, reached by the host walk instead.
PAYLOAD_SPEC_FIELDS: Mapping[type, str] = MappingProxyType({
    KeywordAction: "expansion",
    TokenSpec: "copy_except",
})


def iter_specs(specs: Tuple[EffectSpec, ...]) -> Iterator[EffectSpec]:
    """Pre-order over specs and every spec nested in them: their branches
    (then, otherwise, alternatives) and the specs their payloads carry
    (PAYLOAD_SPEC_FIELDS). Sub-ability and granted hosts are not entered."""
    for s in specs:
        yield s
        yield from iter_specs(s.then)
        yield from iter_specs(s.otherwise)
        yield from iter_specs(s.alternatives)
        field_name = PAYLOAD_SPEC_FIELDS.get(type(s.payload))
        if field_name is not None:
            yield from iter_specs(getattr(s.payload, field_name))


def _walk_hosts(faces, include_granted: bool, include_sub: bool
                ) -> Iterator[Tuple[AbilityEffects, Tuple[AbilityEffects, ...]]]:
    """(host, creators) in pre-order: each host, then the sub-ability (and,
    on request, granted) hosts its specs create, then its modes.

    `creators` are the hosts besides its own whose specs a host's RESULT
    refs may name (invariant 3): a sub-ability host's creating host and, in
    turn, that host's creators (the A34 snapshot chain). A mode shares its
    modal host's creators; a granted ability is an ability of its own and
    has none."""
    def _visit(h: AbilityEffects, creators):
        yield h, creators
        for s in iter_specs(h.specs):
            p = s.payload
            if include_sub and isinstance(p, SubAbility):
                yield from _visit(p.host, (h,) + creators)
            elif include_granted and isinstance(p, Granted):
                for g in p.hosts:
                    yield from _visit(g, ())
            elif include_granted and isinstance(p, TokenSpec):
                for g in p.granted:
                    yield from _visit(g, ())
        for m in h.modes:
            yield from _visit(m, creators)
    for face in faces:
        for h in face:
            yield from _visit(h, ())


@dataclass(**_FROZEN)
class CardEffects:
    faces: Tuple[Tuple[AbilityEffects, ...], ...] = ()
    verbs: FrozenSet[Verb] = frozenset()

    @classmethod
    def of(cls, faces) -> "CardEffects":
        """Build from faces, computing `verbs` over every spec (sub-ability
        hosts included, granted abilities excluded)."""
        faces = tuple(tuple(f) for f in faces)
        probe = cls(faces=faces)
        verbs = frozenset(s.verb for h in probe.walk()
                          for s in iter_specs(h.specs))
        return cls(faces=faces, verbs=verbs)

    def _face(self, face: int) -> Tuple[AbilityEffects, ...]:
        return self.faces[face] if 0 <= face < len(self.faces) else ()

    def front(self) -> Tuple[AbilityEffects, ...]:
        return self._face(0)

    def spell(self, face: int = 0) -> Optional[AbilityEffects]:
        return next((h for h in self._face(face) if h.kind is HostKind.SPELL), None)

    def modes(self, face: int = 0) -> Tuple[AbilityEffects, ...]:
        spell = self.spell(face)
        if spell is not None and spell.modes:
            return spell.modes
        return next((h.modes for h in self._face(face) if h.modes), ())

    def loyalty(self, slot: str, face: int = 0) -> Optional[AbilityEffects]:
        return next((h for h in self._face(face)
                     if h.kind is HostKind.LOYALTY and h.loyalty_slot == slot), None)

    def activated(self, index: int, face: int = 0) -> Optional[AbilityEffects]:
        return next((h for h in self._face(face)
                     if h.kind in (HostKind.ACTIVATED, HostKind.MANA_ABILITY)
                     and h.activation_index == index), None)

    def with_face(self, i: int, hosts) -> "CardEffects":
        faces = list(self.faces)
        while len(faces) <= i:
            faces.append(())
        faces[i] = tuple(hosts)
        return CardEffects.of(faces)

    def walk(self, include_granted: bool = False,
             include_sub: bool = True) -> Iterator[AbilityEffects]:
        """Pre-order over every host: each host, then the sub-ability (and,
        on request, granted) hosts its specs create, then its modes."""
        for h, _ in _walk_hosts(self.faces, include_granted, include_sub):
            yield h

    def unmodelled(self) -> Tuple[Tuple[AbilityEffects, EffectSpec], ...]:
        return tuple((h, s) for h in self.walk() for s in iter_specs(h.specs)
                     if s.verb is Verb.UNMODELLED)


EMPTY_EFFECTS = CardEffects()


# ── Residue polarity (A21) ─────────────────────────────────────────────

WIDENING = "WIDENING"      # the requirement admits objects the text excludes
NARROWING = "NARROWING"    # the requirement excludes objects the text admits
UNPARSED = "UNPARSED"      # slot tokens that were not consumed; never tolerable

class _ResidueCodes(_AbcMapping):
    """The residue vocabulary as a read-only Mapping code -> polarity.

    Entries are exact codes and parameterised families declared as
    '<prefix>:*'. A family resolves every '<prefix>:<param>' with a
    non-empty param, so `code in RESIDUE_CODES`, `RESIDUE_CODES[code]` and
    `.get(code)` answer for real codes ('target.keyword:flying') exactly as
    for exact ones. Iteration yields the declared entries."""
    __slots__ = ("_table",)

    def __init__(self, table: Mapping[str, str]):
        self._table = MappingProxyType(dict(table))

    def __getitem__(self, code: Any) -> str:
        if isinstance(code, str):
            polarity = self._table.get(code)
            if polarity is None:
                prefix, sep, param = code.partition(":")
                if sep and param:
                    polarity = self._table.get(prefix + ":*")
            if polarity is not None:
                return polarity
        raise KeyError(code)

    def __iter__(self) -> Iterator[str]:
        return iter(self._table)

    def __len__(self) -> int:
        return len(self._table)

    def __repr__(self) -> str:
        return f"RESIDUE_CODES({dict(self._table)!r})"


RESIDUE_CODES: Mapping[str, str] = _ResidueCodes({
    "target.scope:opponent": WIDENING,
    "target.scope:not_you": WIDENING,
    "target.exclude_source": WIDENING,
    "target.keyword:*": WIDENING,
    "target.state:*": WIDENING,
    "target.color:*": WIDENING,
    "target.colored": WIDENING,
    "target.nontoken": WIDENING,
    "target.historic": WIDENING,
    "target.stat:*": WIDENING,
    "target.single_graveyard": WIDENING,
    "target.dependent_controller": WIDENING,
    "target.total_mv": WIDENING,
    "target.conjunctive_types": WIDENING,
    "target.union:*": NARROWING,
    "target.zone_union": UNPARSED,
    "target.unparsed": UNPARSED,
})


def residue_polarity(code: Any) -> Optional[str]:
    """The polarity of a residue code, or None when it is not a code."""
    return RESIDUE_CODES.get(code)


# ── Canonical form (F10) ───────────────────────────────────────────────

def canonical(obj: Any) -> str:
    """A deterministic text form: dataclasses by field, enums by name,
    sets sorted -- independent of PYTHONHASHSEED."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return repr(obj)
    if isinstance(obj, Enum):
        return f"{type(obj).__name__}.{obj.name}"
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        body = ",".join(f"{f.name}={canonical(getattr(obj, f.name))}"
                        for f in dataclasses.fields(obj))
        return f"{type(obj).__name__}({body})"
    if isinstance(obj, (set, frozenset)):
        return "{" + ",".join(sorted(canonical(x) for x in obj)) + "}"
    if isinstance(obj, tuple):
        return "(" + ",".join(canonical(x) for x in obj) + ")"
    if isinstance(obj, list):
        return "[" + ",".join(canonical(x) for x in obj) + "]"
    if isinstance(obj, _AbcMapping):
        return "{" + ",".join(sorted(f"{canonical(k)}:{canonical(v)}"
                                     for k, v in obj.items())) + "}"
    return f"<{type(obj).__name__}>"


# ── Invariants ─────────────────────────────────────────────────────────

SCHEMA_INVARIANTS: Tuple[str, ...] = (
    "principal",     # 1. at most one principal; actor-only verbs have none
    "target_slot",   # 2. target set iff target_slot set, and is host.targets[slot]
    "ref_order",     # 3. refs point backwards and never cross hosts (see _check)
    "unmodelled",    # 4. UNMODELLED iff an Unmodelled payload, with raw text
    "payload",       # 5. CONTINUOUS: Modification; CREATE_TRIGGER: SubAbility, no duration
    "duration",      # 6. duration only on CONTINUOUS, EXILE UNTIL_LEAVES, PLAYER_COUNTERS
    "residue",       # 7. every residue code is in RESIDUE_CODES
    "immutable",     # 8. hashable, no mutable object reachable
)

# Verbs whose only participant is the acting player(s) -- the lexicon's
# "player" role. They have no principal (invariant 1): a targeted player acts
# through `actor=Ref(TARGET, k)`, its requirement only in the owning host's
# `targets` (CR 115.1), so "target player draws two cards" has exactly one
# encoding.
ACTOR_ONLY_VERBS: FrozenSet[Verb] = frozenset({
    Verb.SHUFFLE, Verb.LOSE_LIFE, Verb.GAIN_LIFE, Verb.SET_LIFE,
    Verb.EXCHANGE_LIFE, Verb.DRAW, Verb.MILL, Verb.SCRY, Verb.SURVEIL,
    Verb.SEARCH, Verb.PLAYER_COUNTERS, Verb.ADD_MANA, Verb.CREATE_EMBLEM,
    Verb.EXTRA_TURN, Verb.END_TURN, Verb.SKIP})

_IMMUTABLE_LEAVES = (type(None), bool, int, float, str, bytes, Enum)


# Value types a walk never enters (exact types: a subclass may hold state).
_LEAF_TYPES = frozenset({type(None), bool, int, float, str, bytes})
_DATACLASS_FIELDS: dict = {}


def _field_names(cls: type) -> Tuple[str, ...]:
    names = _DATACLASS_FIELDS.get(cls)
    if names is None:
        names = tuple(f.name for f in dataclasses.fields(cls))
        _DATACLASS_FIELDS[cls] = names
    return names


def _mutable_path(obj: Any, seen: set) -> Optional[list]:
    """The path components to the first mutable object reachable from
    `obj` ([] when `obj` itself is mutable), or None. Paths are built only
    on a hit, so a clean walk allocates no strings."""
    if type(obj) in _LEAF_TYPES or isinstance(obj, _IMMUTABLE_LEAVES):
        return None
    if id(obj) in seen:
        return None
    seen.add(id(obj))
    if isinstance(obj, (tuple, frozenset)):
        for i, x in enumerate(obj):
            if type(x) in _LEAF_TYPES:
                continue
            hit = _mutable_path(x, seen)
            if hit is not None:
                return [f"[{i}]"] + hit
        return None
    cls = type(obj)
    params = getattr(cls, "__dataclass_params__", None)
    if params is not None and not isinstance(obj, type):
        if not params.frozen:
            return []
        for name in _field_names(cls):
            v = getattr(obj, name)
            if type(v) in _LEAF_TYPES:
                continue
            hit = _mutable_path(v, seen)
            if hit is not None:
                return [f".{name}"] + hit
        return None
    return []


def find_mutable(obj: Any, _path: str = "", _seen=None) -> Optional[str]:
    """The path of the first mutable object reachable from `obj`, or None."""
    hit = _mutable_path(obj, set() if _seen is None else _seen)
    if hit is None:
        return None
    path = _path + "".join(hit)
    if path:
        return path
    return type(obj).__name__


def _refs(obj: Any, _seen=None) -> Iterator[Ref]:
    """Every Ref reachable from a spec's own fields, not entering nested
    EffectSpecs (they are checked on their own) or sub-ability hosts."""
    out: list = []
    _collect_refs(obj, set() if _seen is None else _seen, out)
    return iter(out)


def _collect_refs(obj: Any, seen: set, out: list) -> None:
    if type(obj) in _LEAF_TYPES or isinstance(obj, _IMMUTABLE_LEAVES) \
            or id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, (EffectSpec, AbilityEffects)):
        return
    if isinstance(obj, Ref):
        out.append(obj)
    if isinstance(obj, (tuple, frozenset, list)):
        for x in obj:
            if type(x) not in _LEAF_TYPES:
                _collect_refs(x, seen, out)
    elif dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        for name in _field_names(type(obj)):
            v = getattr(obj, name)
            if type(v) not in _LEAF_TYPES:
                _collect_refs(v, seen, out)


_SPEC_OWN_FIELDS = tuple(f for f in EffectSpec.__dataclass_fields__
                         if f not in ("then", "otherwise", "alternatives"))


def _host_seqs(host: AbilityEffects) -> FrozenSet[int]:
    return frozenset(s.seq for s in iter_specs(host.specs))


def _check(spec: EffectSpec, host: Optional[AbilityEffects],
           creators: Tuple[AbilityEffects, ...]) -> Optional[str]:
    verb = spec.verb
    if not isinstance(verb, Verb):
        return "invalid:verb"
    # 1. principal
    principals = [n for n in ("target", "subject", "ref")
                  if getattr(spec, n) is not None]
    if len(principals) > 1:
        return "principal:" + "+".join(principals)
    if principals and verb in ACTOR_ONLY_VERBS:
        return "principal:actor_only"
    # 2. target_slot
    if (spec.target is None) != (spec.target_slot is None):
        return "target_slot:unpaired"
    if spec.target is not None:
        if not isinstance(host, AbilityEffects):
            return "target_slot:no_host"
        k = spec.target_slot
        if not isinstance(k, int) or not 0 <= k < len(host.targets):
            return "target_slot:out_of_range"
        if host.targets[k] is not spec.target:
            return "target_slot:not_host_target"
    # 3. ref_order: a RESULT index and a replaces seq name an EARLIER spec
    # of this host; a RESULT index may also name a spec of a host that
    # created this sub-ability host (the A34 snapshot). A TARGET index names
    # one of this host's own targets. LINKED (CR 607) and the other kinds
    # are not host-relative. Without a host nothing can be resolved.
    for name in _SPEC_OWN_FIELDS:
        value = getattr(spec, name)
        if type(value) in _LEAF_TYPES:
            continue
        for r in _refs(value):
            if r.kind is RefKind.RESULT:
                if not (isinstance(r.index, int) and 0 <= r.index < spec.seq):
                    return "ref_order:result"
                if host is None:
                    return "ref_order:no_host"
                if not any(r.index in _host_seqs(h) for h in (host,) + creators):
                    return "ref_order:cross_host"
            elif r.kind is RefKind.TARGET:
                if not isinstance(r.index, int):
                    return "ref_order:target"
                if host is None:
                    return "ref_order:no_host"
                if not 0 <= r.index < len(host.targets):
                    return "ref_order:cross_host"
    for s in spec.replaces:
        if not (isinstance(s, int) and 0 <= s < spec.seq):
            return "ref_order:replaces"
        if host is None:
            return "ref_order:no_host"
        if s not in _host_seqs(host):
            return "ref_order:cross_host"
    # 4. unmodelled
    is_um = isinstance(spec.payload, Unmodelled)
    if (verb is Verb.UNMODELLED) != is_um:
        return "unmodelled:payload"
    if verb is Verb.UNMODELLED and not spec.raw:
        return "unmodelled:raw"
    # 5. payload
    if verb is Verb.CONTINUOUS and not isinstance(spec.payload, Modification):
        return "payload:continuous"
    if (verb is Verb.CREATE_TRIGGER) != isinstance(spec.payload, SubAbility):
        return "payload:create_trigger"
    if verb is Verb.CREATE_TRIGGER and spec.duration is not None:
        return "payload:create_trigger_duration"
    # 6. duration
    d = spec.duration
    if d is not None and not (
            verb in (Verb.CONTINUOUS, Verb.PLAYER_COUNTERS)
            or (verb is Verb.EXILE and isinstance(d, Duration)
                and d.kind is DurationKind.UNTIL_LEAVES)):
        return "duration"
    # 7. residue
    for code in spec.residue:
        if code not in RESIDUE_CODES:
            return f"residue:{code}"
    # 8. immutable
    where = find_mutable(spec)
    if where is not None:
        return f"immutable:{where}"
    try:
        hash(spec)
    except TypeError:
        return "immutable:unhashable"
    return None


def validate_spec(spec: Any, host: Any = None, parents: Any = ()) -> Optional[str]:
    """The first violated schema invariant of `spec` inside its innermost
    owning host, as '<rule>[:<detail>]' (rule in SCHEMA_INVARIANTS), or None.
    `parents` are the hosts that created a sub-ability `host`, innermost
    first: its RESULT refs may name their specs (invariant 3). Nested branch
    specs are checked on their own. Never raises."""
    try:
        if not isinstance(spec, EffectSpec):
            return "invalid:not_a_spec"
        creators = (tuple(p for p in parents if isinstance(p, AbilityEffects))
                    if isinstance(parents, (tuple, list)) else ())
        return _check(spec, host if isinstance(host, AbilityEffects) else None,
                      creators)
    except Exception as exc:          # a malformed value must not escape
        return f"invalid:{type(exc).__name__}"


def _well_formed_span(span: Any) -> Tuple[int, int]:
    if (isinstance(span, (tuple, list)) and len(span) == 2
            and all(isinstance(x, int) for x in span)):
        return (span[0], span[1])
    return (0, 0)


def enforce_invariants(spec: EffectSpec, host: Any = None,
                       parents: Any = ()) -> EffectSpec:
    """`spec` if it satisfies the invariants, else UNMODELLED(INVALID) with
    the violated rule as detail. seq, span and raw are carried over when
    well-formed and replaced when not (0, (0, 0), '<lemma>'), so the lowered
    spec itself satisfies every invariant -- a violation in one of those
    fields never survives the lowering."""
    rule = validate_spec(spec, host, parents)
    if rule is None:
        return spec
    verb = getattr(spec, "verb", None)
    lemma = verb.value if isinstance(verb, Verb) else ""
    raw = getattr(spec, "raw", None)
    seq = getattr(spec, "seq", 0)
    return EffectSpec(verb=Verb.UNMODELLED,
                      payload=Unmodelled(Stage.INVALID, lemma=lemma, detail=rule),
                      seq=seq if isinstance(seq, int) else 0,
                      span=_well_formed_span(getattr(spec, "span", None)),
                      raw=raw if isinstance(raw, str) and raw else f"<{lemma or 'spec'}>")


def validate_card_effects(effects: Any) -> Optional[str]:
    """Invariant 8 on the whole value, then every spec of every host
    (sub-ability and granted hosts included, each with the hosts that
    created it in view). None when all hold."""
    try:
        if not isinstance(effects, CardEffects):
            return "immutable:not_card_effects"
        where = find_mutable(effects)
        if where is not None:
            return f"immutable:{where}"
        hash(effects)
        for h, creators in _walk_hosts(effects.faces, include_granted=True,
                                       include_sub=True):
            for s in iter_specs(h.specs):
                rule = validate_spec(s, h, creators)
                if rule is not None:
                    return f"{rule} @face{h.face}/host{h.index}/seq{s.seq}"
        return None
    except Exception as exc:
        return f"invalid:{type(exc).__name__}"
