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
its polarity (A21). `replace()` is `dataclasses.replace` for the schema's
frozen slotted classes, slot by slot; `clear_caches()` empties the bounded
memos of the invariant checks (section 12), and the grammar's own
`clear_caches` calls it.
"""
from __future__ import annotations

import dataclasses
import operator
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
    DRAW = "draw"            # "<player> draws ..." (CR 121.1); TriggerHead.draw types it
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
class DrawEvent:
    """The draw a draw-triggered head names (CR 121.1, 603.2): who draws,
    relative to the ability's controller ('you', 'opponent' or 'player');
    which card of that player's turn it must be (`nth`, "your second card
    each turn"; None for any card); and whether the first card the drawer
    draws in each of their draw steps is excepted ("except the first one
    they draw in each of their draw steps")."""
    drawer: str
    nth: Optional[int] = None
    except_first_in_draw_step: bool = False


@dataclass(**_FROZEN)
class CombatDamageEvent:
    """The combat damage a combat-damage-triggered head names (CR 510.2,
    603.2): who deals it -- 'self' ("~"), 'equipped' or 'enchanted' (the
    creature this permanent is attached to) -- and to what: 'player' ("a
    player"), 'opponent', 'player_or_planeswalker' or 'player_or_battle'.
    "That player" in the body is the player dealt the damage."""
    dealer: str
    recipient: str


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
    # The draw a DRAW head names, from a closed table of printed shapes
    # (`structure._draw_event`); None on a DRAW head with a rider the table
    # does not read, which no carrier resolves as a plain draw trigger.
    draw: Optional[DrawEvent] = None
    # The combat damage a COMBAT_DAMAGE_TO_PLAYER head names, from a closed
    # table (`structure._combat_damage_event`); None for a dealer or rider
    # the table does not read.
    combat_damage: Optional[CombatDamageEvent] = None


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


# How the invariant walks treat a value, by its exact type (`_walk_kind`):
# a leaf (never entered), an immutable sequence (tuple / frozenset and
# their subclasses), a list (mutable, entered by the Ref walk), a frozen
# dataclass with a getter of its field values, or anything else (mutable;
# a non-frozen dataclass keeps its getter for the Ref walk), plus whether
# it is a spec or host (the Ref walk stops there) and whether it is a Ref.
# The table holds the answer `isinstance` would give for the type, so a
# node costs one dict lookup instead of a chain of isinstance checks.
_LEAF, _SEQ, _LIST, _FROZEN_DC, _OTHER = range(5)
_WALK_KINDS: dict = {}


def _walk_kind(cls: type) -> tuple:
    kind = _WALK_KINDS.get(cls)
    if kind is not None:
        return kind
    params = getattr(cls, "__dataclass_params__", None)
    nesting = issubclass(cls, (EffectSpec, AbilityEffects))
    is_ref = issubclass(cls, Ref)
    if cls in _LEAF_TYPES or issubclass(cls, _IMMUTABLE_LEAVES):
        kind = (_LEAF, None, (), nesting, is_ref)
    elif issubclass(cls, (tuple, frozenset)):
        kind = (_SEQ, None, (), nesting, is_ref)
    elif issubclass(cls, list):
        kind = (_LIST, None, (), nesting, is_ref)
    elif params is not None and not issubclass(cls, type):
        names = _field_names(cls)
        getter = (operator.attrgetter(*names) if len(names) > 1 else
                  (lambda o, _n=names[0]: (getattr(o, _n),)) if names else
                  (lambda o: ()))
        kind = (_FROZEN_DC if params.frozen else _OTHER, getter, names,
                nesting, is_ref)
    else:
        kind = (_OTHER, None, (), nesting, is_ref)
    _WALK_KINDS[cls] = kind
    return kind


# The bounded memo of the immutability walk (section 12): the EffectSpecs
# and hosts it found clean. Grammar values nest -- a spec's walk covers its
# branch specs and the specs of the sub-ability hosts it creates, which are
# then validated on their own -- so a clean spec or host is walked once.
# Every entry holds the object it is keyed on, so its id cannot be reused
# while it is remembered, and only a value with no mutable object reachable
# is remembered: such a value cannot change, so the remembered verdict is
# the one a fresh walk would give. A memo that reaches its bound is emptied
# (the next values refill it); `clear_caches` empties both memos.
#
# `_VERDICTS` keeps, per distinct clean, hashable EffectSpec (keyed on its
# value: equal specs share a verdict, since every host-free check reads
# only the spec's value), the host-free part of its validation
# (`_verdict`): each distinct frozen spec is validated once; the checks
# that read its owning host (target slots, ref order) run on every call.
VALIDATION_MEMO_SIZE = 1 << 14
_CLEAN: dict = {}
_VERDICTS: dict = {}


def _remember(memo: dict, key: Any, value: Any) -> None:
    if len(memo) >= VALIDATION_MEMO_SIZE:
        memo.clear()
    memo[key] = value


def clear_caches() -> None:
    """Empty the invariant checks' memos (`_CLEAN`, `_VERDICTS`);
    `engine.effect_grammar.clear_caches` calls it."""
    _CLEAN.clear()
    _VERDICTS.clear()


def _field_values(obj: Any, k: tuple):
    """The field values of a dataclass `obj` in field order (one
    `attrgetter` call); a field that is not set raises where a walk of the
    fields one by one would reach it."""
    try:
        return k[1](obj)
    except AttributeError:
        return (getattr(obj, name) for name in k[2])


def _mutable_path(obj: Any, seen: set) -> Optional[list]:
    """The path components to the first mutable object reachable from
    `obj` ([] when `obj` itself is mutable), or None. Paths are built only
    on a hit, so a clean walk allocates no strings; a spec or host found
    clean is remembered (`_CLEAN`) and never walked again."""
    cls = type(obj)
    return _mutable_walk(obj, _WALK_KINDS.get(cls) or _walk_kind(cls), seen,
                         set(), [False])


def _mutable_walk(obj: Any, k: tuple, seen: set, stack: set,
                  cyclic: list) -> Optional[list]:
    """`_mutable_path` for `obj` of walk kind `k`. `stack` holds the values
    being walked; meeting one again (a cycle, which only a frozen value
    re-pointed through object.__setattr__ can form) sets `cyclic[0]`, and
    from then on the walk remembers nothing, since a value walked inside
    the cycle was not walked whole."""
    kind = k[0]
    if kind is _LEAF:
        return None
    oid = id(obj)
    if oid in seen:
        if oid in stack:
            cyclic[0] = True
        return None
    if kind is _SEQ:
        seen.add(oid)
        stack.add(oid)
        for i, x in enumerate(obj):
            kx = _WALK_KINDS.get(type(x)) or _walk_kind(type(x))
            if kx[0] is _LEAF:
                continue
            hit = _mutable_walk(x, kx, seen, stack, cyclic)
            if hit is not None:
                return [f"[{i}]"] + hit
        stack.discard(oid)
        return None
    if kind is not _FROZEN_DC:
        seen.add(oid)
        return []
    remember = k[3]
    if remember and _CLEAN.get(oid) is obj:
        return None
    seen.add(oid)
    stack.add(oid)
    for name, v in zip(k[2], _field_values(obj, k)):
        kv = _WALK_KINDS.get(type(v)) or _walk_kind(type(v))
        if kv[0] is _LEAF:
            continue
        hit = _mutable_walk(v, kv, seen, stack, cyclic)
        if hit is not None:
            return [f".{name}"] + hit
    stack.discard(oid)
    if remember and not cyclic[0]:
        _remember(_CLEAN, oid, obj)
    return None


_REPLACERS: dict = {}


def _replacer(cls: type):
    """(field index, slot setters) for a frozen slotted dataclass with a
    generated `__init__`, whose fields are all init fields (no InitVar or
    ClassVar pseudo-field) and which has no `__post_init__`, or None."""
    got = _REPLACERS.get(cls, False)
    if got is not False:
        return got
    got = None
    params = getattr(cls, "__dataclass_params__", None)
    if (params is not None and params.frozen and params.init
            and "__slots__" in cls.__dict__
            and not hasattr(cls, "__post_init__")
            # no InitVar / ClassVar pseudo-field, every field an init field
            and len(cls.__dataclass_fields__) == len(dataclasses.fields(cls))
            and all(f.init for f in dataclasses.fields(cls))):
        names = _field_names(cls)
        got = ({n: i for i, n in enumerate(names)},
               tuple(cls.__dict__[n].__set__ for n in names))
    _REPLACERS[cls] = got
    return got


def replace(obj: Any, **changes: Any) -> Any:
    """`dataclasses.replace` for the schema's frozen, slotted classes: the
    same new object, built by setting its slots directly instead of through
    the generated keyword `__init__` (no schema class has a
    `__post_init__` or a non-init field; any other class, or an unknown
    field name, goes through `dataclasses.replace`). The grammar copies a
    spec or host with a few fields changed on every clause it places and
    every spec it freezes."""
    cls = type(obj)
    r = _replacer(cls)
    if r is None or not changes.keys() <= r[0].keys():
        return dataclasses.replace(obj, **changes)
    index, setters = r
    values = list(_walk_kind(cls)[1](obj))
    for name, value in changes.items():
        values[index[name]] = value
    new = object.__new__(cls)
    for setter, value in zip(setters, values):
        setter(new, value)
    return new


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
    cls = type(obj)
    _refs_walk(obj, _WALK_KINDS.get(cls) or _walk_kind(cls), seen, out)


def _refs_walk(obj: Any, k: tuple, seen: set, out: list) -> None:
    kind, getter, _names, nesting, is_ref = k
    if kind is _LEAF or id(obj) in seen:
        return
    seen.add(id(obj))
    if nesting:
        return
    if is_ref:
        out.append(obj)
    if kind is _SEQ or kind is _LIST:
        values = obj
    elif getter is None:
        return
    else:
        values = _field_values(obj, k)
    for v in values:
        kv = _WALK_KINDS.get(type(v)) or _walk_kind(type(v))
        if kv[0] is not _LEAF:
            _refs_walk(v, kv, seen, out)


_SPEC_OWN_FIELDS = tuple(f for f in EffectSpec.__dataclass_fields__
                         if f not in ("then", "otherwise", "alternatives"))


def _host_seqs(host: AbilityEffects) -> FrozenSet[int]:
    return frozenset(s.seq for s in iter_specs(host.specs))


def _early(spec: EffectSpec) -> Optional[str]:
    """Invariants 1 and the host-free half of 2, in their check order."""
    verb = spec.verb
    if not isinstance(verb, Verb):
        return "invalid:verb"
    principals = [n for n in ("target", "subject", "ref")
                  if getattr(spec, n) is not None]
    if len(principals) > 1:
        return "principal:" + "+".join(principals)
    if principals and verb in ACTOR_ONLY_VERBS:
        return "principal:actor_only"
    if (spec.target is None) != (spec.target_slot is None):
        return "target_slot:unpaired"
    return None


def _late(spec: EffectSpec, hashed: bool = False) -> Optional[str]:
    """Invariants 4-8, in their check order; none reads the host. `hashed`:
    the caller already hashed `spec`, so the hashability half of 8 holds."""
    verb = spec.verb
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
    if not hashed:
        try:
            hash(spec)
        except TypeError:
            return "immutable:unhashable"
    return None


def _own_refs(spec: EffectSpec) -> Tuple[Tuple[Ref, ...], Optional[Exception]]:
    """The Refs of a spec's own fields (`_SPEC_OWN_FIELDS`, in order), each
    field walked on its own as `_refs` walks it, and the exception a
    malformed field raised (the Refs before it are kept, so the check
    meets them in the order the field walk would)."""
    out: list = []
    try:
        for name in _SPEC_OWN_FIELDS:
            value = getattr(spec, name)
            if type(value) in _LEAF_TYPES:
                continue
            _collect_refs(value, set(), out)
    except Exception as exc:          # re-raised where the walk would raise
        return tuple(out), exc
    return tuple(out), None


def _verdict(spec: EffectSpec) -> tuple:
    """The host-free part of a spec's validation: (invariants 1-2 host-free,
    its own Refs, the exception their walk raised, invariants 4-8 or the
    exception they raised). Remembered per distinct frozen spec (`_VERDICTS`,
    when the spec is clean and hashable), keyed on the spec's value -- its
    hash, confirmed by equality, so the spec is hashed once per call -- and
    a third of the pool's specs equal an earlier one (the same printed
    clause at the same position on many cards), so each distinct value is
    walked once. Every host-free check reads only the spec's value except
    the Ref indices (an index equal to an int need not be one), so an equal
    spec that is not the remembered object, and whose value holds Refs, has
    its own Refs collected again."""
    try:
        h = hash(spec)
    except Exception:                 # invariant 8 reports it, in its turn
        h = None
    if h is not None:
        hit = _VERDICTS.get(h)
        if hit is not None:
            if hit[0] is spec:
                return hit[1]
            try:
                same = bool(hit[0] == spec)
            except Exception:         # an equality that raises is no hit
                same = False
            if same:
                v = hit[1]
                if not v[1]:
                    return v
                refs, refs_exc = _own_refs(spec)
                return (v[0], refs, refs_exc, v[3])
    early = _early(spec)
    refs, refs_exc = _own_refs(spec)
    try:
        late = _late(spec, hashed=h is not None)
    except Exception as exc:          # re-raised where invariants 4-8 run
        late = exc
    v = (early, refs, refs_exc, late)
    if h is not None and late is None and refs_exc is None:
        _remember(_VERDICTS, h, (spec, v))
    return v


def _check(spec: EffectSpec, host: Optional[AbilityEffects],
           creators: Tuple[AbilityEffects, ...]) -> Optional[str]:
    early, refs, refs_exc, late = _verdict(spec)
    if early is not None:
        return early
    # 2. target_slot: the requirement is the owning host's target in slot.
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
    seqs: dict = {}

    def host_seqs(h: AbilityEffects) -> FrozenSet[int]:
        got = seqs.get(id(h))
        if got is None:
            got = seqs[id(h)] = _host_seqs(h)
        return got

    for r in refs:
        if r.kind is RefKind.RESULT:
            if not (isinstance(r.index, int) and 0 <= r.index < spec.seq):
                return "ref_order:result"
            if host is None:
                return "ref_order:no_host"
            if not any(r.index in host_seqs(h) for h in (host,) + creators):
                return "ref_order:cross_host"
        elif r.kind is RefKind.TARGET:
            if not isinstance(r.index, int):
                return "ref_order:target"
            if host is None:
                return "ref_order:no_host"
            if not 0 <= r.index < len(host.targets):
                return "ref_order:cross_host"
    if refs_exc is not None:
        raise refs_exc
    for s in spec.replaces:
        if not (isinstance(s, int) and 0 <= s < spec.seq):
            return "ref_order:replaces"
        if host is None:
            return "ref_order:no_host"
        if s not in host_seqs(host):
            return "ref_order:cross_host"
    if isinstance(late, Exception):
        raise late
    return late


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
