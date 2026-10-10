"""The continuous-effect model (CR 611–613): every effect — from a static
ability or a resolved spell/ability — is one record

    Effect(selector, modification, duration, origin)

* **Selector** — what it affects: a player set, a fixed object (CR 611.2c;
  identity is `instance_id` + `battlefield_entry_seq`, CR 400.7), or a
  dynamic filter (statics, CR 611.3a).
* **Modification** — what it changes. Two closed families:
  characteristic modifications (applied in layers, CR 613) and rule
  modifications (queried by the gate that owns the action: prohibit /
  permit / limit / cost delta / prevent damage / observe).
* **Duration** — how long (CR 611.2): a predicate over the event clock
  (engine/turn_clock.py) and object identity.
* **Origin** — static (derived from a permanent every recalculation, lives
  while its source does) or resolved (stored until its duration expires).

Rule gates never read effect state directly; they ask engine/rules_query.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, FrozenSet, Optional, Tuple


# ── Duration (CR 611.2) ──────────────────────────────────────────────

class DurationKind(Enum):
    THIS_TURN = "this_turn"                       # "this turn" / "until end of turn"
    UNTIL_YOUR_NEXT_TURN = "until_your_next_turn"
    # "until the end of your next turn" (CR 611.2): through the cleanup of
    # `player`'s first turn after the one it was created in.
    UNTIL_END_OF_YOUR_NEXT_TURN = "until_end_of_your_next_turn"
    WHILE_SOURCE_ON_BATTLEFIELD = "while_source"  # static abilities (CR 611.3a)
    UNTIL_LEAVES = "until_leaves"                 # "for as long as <obj> …"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class Duration:
    kind: DurationKind
    # UNTIL_YOUR_NEXT_TURN / UNTIL_END_OF_YOUR_NEXT_TURN: whose next turn
    # ends it.
    player: Optional[int] = None
    # UNTIL_LEAVES: (instance_id, battlefield_entry_seq) of the tracked object.
    obj: Optional[Tuple[int, int]] = None
    # UNTIL_END_OF_YOUR_NEXT_TURN: the game turn it was created in (bound
    # when the effect is created; a parsed duration carries None).
    turn: Optional[int] = None

    def expired_by(self, event) -> bool:
        """Does this clock event end the effect? `event` is a
        turn_clock.ClockEvent (or an object event with `.obj`)."""
        from .turn_clock import Clock
        k = self.kind
        if k is DurationKind.THIS_TURN:
            # The game turn ends: cleanup, or the next turn beginning
            # (whichever the stream reaches first).
            return event.kind in (Clock.CLEANUP, Clock.TURN_BEGINS)
        if k is DurationKind.UNTIL_YOUR_NEXT_TURN:
            return event.kind is Clock.TURN_BEGINS and event.player == self.player
        if k is DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN:
            # The cleanup of `player`'s first turn after the creation turn
            # (created on their own turn: the turn after next; on another
            # player's turn: their next turn).
            at = getattr(event, "turn", None)
            return (event.kind is Clock.CLEANUP and event.player == self.player
                    and at is not None and self.turn is not None
                    and at > self.turn)
        if k is DurationKind.UNTIL_LEAVES:
            return getattr(event, "obj", None) == self.obj
        return False   # PERMANENT, WHILE_SOURCE (retracted by derivation)


THIS_TURN = Duration(DurationKind.THIS_TURN)
PERMANENT = Duration(DurationKind.PERMANENT)
WHILE_SOURCE = Duration(DurationKind.WHILE_SOURCE_ON_BATTLEFIELD)


def until_your_next_turn(player: int) -> Duration:
    return Duration(DurationKind.UNTIL_YOUR_NEXT_TURN, player=player)


def until_end_of_your_next_turn(player: int, turn: int) -> Duration:
    """"Until the end of your next turn", created in game turn `turn`."""
    return Duration(DurationKind.UNTIL_END_OF_YOUR_NEXT_TURN, player=player,
                    turn=turn)


# The duration kinds `Duration.expired_by` (or source retraction) ends. A
# printed duration outside this set is unmodelled, never a new kind here.
CLOCKED_DURATIONS: FrozenSet[DurationKind] = frozenset(DurationKind)


# ── Selector ─────────────────────────────────────────────────────────

class SelectorKind(Enum):
    PLAYER = "player"          # one player
    OPPONENTS = "opponents"    # the opponents of `player`
    ALL_PLAYERS = "all_players"
    OBJECT = "object"          # one object, fixed at creation (CR 611.2c)
    FILTER = "filter"          # a dynamic set (statics, CR 611.3a)


@dataclass(frozen=True)
class Selector:
    kind: SelectorKind
    player: Optional[int] = None
    obj: Optional[Tuple[int, int]] = None
    # FILTER: a typed, controller-relative description, e.g.
    # {'types': ('creature',), 'controller': 'you'}.
    filter: Optional[Tuple[Tuple[str, Any], ...]] = None

    def covers_object(self, card) -> bool:
        """An OBJECT selector covers the object it chose, not a later object
        of the same card (CR 400.7); a FILTER selector covers whatever
        matches its filter now (evaluated at each query, CR 611.3a-style)."""
        if self.kind is SelectorKind.OBJECT:
            return (self.obj is not None
                    and self.obj == (card.instance_id, card.battlefield_entry_seq))
        if self.kind is SelectorKind.FILTER:
            f = dict(self.filter or ())
            if f.get('controller') == 'opponents' and card.controller == self.player:
                return False
            kw = f.get('without_keyword')
            if kw and any(k.value == kw for k in card.keywords):
                return False
            return True
        return False

    def covers_player(self, idx: int) -> bool:
        k = self.kind
        return (k is SelectorKind.ALL_PLAYERS
                or (k is SelectorKind.PLAYER and self.player == idx)
                or (k is SelectorKind.OPPONENTS and self.player != idx))


# Value-typed FILTER support: exactly the (key, value) entries
# `Selector.covers_object` evaluates. ANY_KEYWORD stands for any value
# covers_object can match a keyword against -- a `cards.Keyword` value
# ('first_strike', never the printed 'first strike'). Any other entry
# ('controller': 'you', a Ref controller, type keys, a printed or unknown
# keyword, …) is ignored or never matched by covers_object, so a spec that
# needs it is not executable.
ANY_KEYWORD = "<any cards.Keyword value>"
SUPPORTED_FILTER_VALUES: FrozenSet[Tuple[str, Any]] = frozenset({
    ("controller", "opponents"),
    ("without_keyword", ANY_KEYWORD),
})


@lru_cache(maxsize=1)
def _keyword_values() -> FrozenSet[str]:
    """The values covers_object compares keywords against (`k.value`)."""
    from .cards import Keyword     # lazy: the effect model imports no card module
    return frozenset(k.value for k in Keyword)


def is_supported_filter_entry(key: str, value: Any) -> bool:
    """Does `Selector.covers_object` evaluate this FILTER entry?"""
    if key == "without_keyword":
        return (("without_keyword", ANY_KEYWORD) in SUPPORTED_FILTER_VALUES
                and isinstance(value, str) and value in _keyword_values())
    try:
        return (key, value) in SUPPORTED_FILTER_VALUES
    except TypeError:          # an unhashable value is no supported entry
        return False


# ── Modification ─────────────────────────────────────────────────────

class ModFamily(Enum):
    CHARACTERISTIC = "characteristic"   # layers, CR 613
    RULE = "rule"                       # queried by the owning gate


class ModKind(Enum):
    # characteristic (CR 613)
    SET_TYPES = "set_types"
    ADD_TYPES = "add_types"
    SET_COLORS = "set_colors"
    ADD_KEYWORDS = "add_keywords"
    REMOVE_KEYWORDS = "remove_keywords"
    SET_BASE_PT = "set_base_pt"
    MODIFY_PT = "modify_pt"
    # rule
    PROHIBIT = "prohibit"               # action ∈ cast/attack/block/draw/be_attacked/activate
    PERMIT = "permit"                   # e.g. cast as though it had flash
    LIMIT = "limit"                     # e.g. draw at most N per turn
    COST_DELTA = "cost_delta"
    PREVENT_DAMAGE = "prevent_damage"
    OBSERVE = "observe"                 # "whenever <event>, <effect>"
    # Payload vocabulary for the clause grammar (design doc 2026-09-29).
    # Typed at load, applied by nothing yet: absent from APPLIED_MODKINDS, so
    # a dispatcher refuses them until an owner applies each one.
    GRANT_ABILITY = "grant_ability"                 # CR 613.1f (layer 6)
    REMOVE_ALL_ABILITIES = "remove_all_abilities"   # CR 613.1f (layer 6)
    SWITCH_PT = "switch_pt"                         # CR 613.4d (layer 7d)
    SET_CONTROLLER = "set_controller"               # CR 613.1b (layer 2)
    REQUIRE = "require"                             # "must attack/block" (rule)


_RULE_KINDS = frozenset({ModKind.PROHIBIT, ModKind.PERMIT, ModKind.LIMIT,
                         ModKind.COST_DELTA, ModKind.PREVENT_DAMAGE, ModKind.OBSERVE,
                         ModKind.REQUIRE})

# The kinds some owner applies today, listed member by member and never
# derived from ModKind: a kind added later is unapplied (refused by a
# dispatcher) until its owner lands and it is listed here -- fail closed.
# The payload vocabulary above is absent for that reason.
APPLIED_MODKINDS: FrozenSet[ModKind] = frozenset({
    # the layer system (continuous_effects, CR 613)
    ModKind.SET_TYPES, ModKind.ADD_TYPES, ModKind.SET_COLORS,
    ModKind.ADD_KEYWORDS, ModKind.REMOVE_KEYWORDS, ModKind.SET_BASE_PT,
    ModKind.MODIFY_PT,
    # the rule gate that owns the action (rules_query)
    ModKind.PROHIBIT, ModKind.PERMIT, ModKind.LIMIT, ModKind.COST_DELTA,
    ModKind.PREVENT_DAMAGE, ModKind.OBSERVE,
})


@dataclass(frozen=True)
class Modification:
    kind: ModKind
    # PROHIBIT/PERMIT/LIMIT/PREVENT_DAMAGE: the action; others: None.
    action: Optional[str] = None
    # A typed, closed payload (spell filter, amount, keywords, P/T …).
    data: Tuple[Tuple[str, Any], ...] = ()

    @property
    def family(self) -> ModFamily:
        return ModFamily.RULE if self.kind in _RULE_KINDS else ModFamily.CHARACTERISTIC

    def get(self, key: str, default=None):
        return dict(self.data).get(key, default)


# ── Origin and the record ────────────────────────────────────────────

class OriginKind(Enum):
    STATIC = "static"       # derived every recalculation from a permanent
    RESOLVED = "resolved"   # created by a resolving spell/ability; stored


@dataclass(frozen=True)
class Effect:
    selector: Selector
    modification: Modification
    duration: Duration
    origin: OriginKind
    source_id: int = 0
    controller: Optional[int] = None
    timestamp: int = 0
    # A printed condition the rule applies under, evaluated at each query
    # (CR 611.3a: "during your turn" / "as long as it's your turn"), an
    # `effect_spec.Condition`; None applies always. Only the kinds
    # `effect_conditions.rule_condition_supported` accepts are ever set.
    condition: Optional[Any] = None


# ── Constructors for the rule families (one shape each) ───────────────

def prohibit_cast(player: int, spell_filter: str, duration: Duration,
                  controller: Optional[int] = None, source_id: int = 0) -> Effect:
    """"<player> can't cast [<filter>] spells" (CR 101.2)."""
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.PROHIBIT, action="cast",
                               data=(("filter", spell_filter),)),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=controller)


def permit_cast_as_flash(player: int, types, duration: Duration,
                         source_id: int = 0) -> Effect:
    """"You may cast <types> spells as though they had flash" (CR 702.8d)."""
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.PERMIT, action="cast_as_flash",
                               data=(("types", tuple(types)),)),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=player)


def permit_play(player: int, objects, action: str, duration: Duration,
                source_id: int = 0) -> Effect:
    """"You may play / cast those cards" (CR 305.1, 601.2a): `player` may
    play (lands and spells) or cast (spells only) the objects -- instance
    ids, in exile -- for `duration`."""
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.PERMIT, action=action,
                               data=(("objects", tuple(sorted(objects))),
                                     ("zone", "exile"))),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=player)


def cost_delta_effect(player: int, rule: dict, duration: Duration,
                      source_id: int = 0, origin: "OriginKind" = OriginKind.RESOLVED) -> Effect:
    """"<spells> you cast cost {N} less" — the parse_cost_reduction rule shape
    (target / amount / color) applied to `player`'s spells (CR 601.2f)."""
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.COST_DELTA,
                               data=tuple(sorted(rule.items()))),
                  duration, origin, source_id=source_id, controller=player)


def draw_limit_effect(controller: int, who: str, cap: int, duration: Duration,
                      source_id: int = 0,
                      origin: "OriginKind" = OriginKind.RESOLVED) -> Effect:
    """"<each opponent | each player> can't draw more than N cards each
    turn" — the parse_draw_limit shape (CR 101.2). `who` is 'opponents'
    (the controller's opponents) or 'all'."""
    selector = (Selector(SelectorKind.OPPONENTS, player=controller)
                if who == 'opponents' else Selector(SelectorKind.ALL_PLAYERS))
    return Effect(selector,
                  Modification(ModKind.LIMIT, action="draw",
                               data=(("max", cap),)),
                  duration, origin, source_id=source_id, controller=controller)


def prohibit_attack(player: int, duration: Duration,
                    controller: Optional[int] = None, source_id: int = 0) -> Effect:
    """"<player>'s creatures can't attack" (CR 508.1c / 509.4)."""
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.PROHIBIT, action="attack"),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=controller)


def prohibit_be_attacked(player: int, duration: Duration,
                         controller: Optional[int] = None, source_id: int = 0) -> Effect:
    """"Creatures can't attack <player>" (CR 508.1c)."""
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.PROHIBIT, action="be_attacked"),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=controller)


def prevent_combat_damage(duration: Duration, controller: Optional[int] = None,
                          source_id: int = 0) -> Effect:
    """"Prevent all combat damage that would be dealt" (CR 615)."""
    return Effect(Selector(SelectorKind.ALL_PLAYERS),
                  Modification(ModKind.PREVENT_DAMAGE, action="combat"),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=controller)


def prohibit_object(card, action: str, duration: Duration,
                    controller: Optional[int] = None, source_id: int = 0) -> Effect:
    """"<chosen creature> can't <attack|block>" (CR 508.1c / 509.1b) — on
    the object as it is now (CR 611.2c)."""
    return Effect(Selector(SelectorKind.OBJECT,
                           obj=(card.instance_id, card.battlefield_entry_seq)),
                  Modification(ModKind.PROHIBIT, action=action),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=controller)


def observe_attacks(player: int, observer: dict, duration: Duration,
                    controller: Optional[int] = None, source_id: int = 0,
                    origin: "OriginKind" = OriginKind.RESOLVED) -> Effect:
    """"Whenever a creature attacks <player> [or a planeswalker they
    control], <effect>" (CR 603.2) — the parse_attack_observer shape."""
    eff = observer['effect']
    return Effect(Selector(SelectorKind.PLAYER, player=player),
                  Modification(ModKind.OBSERVE, action="attacked",
                               data=(("scope", observer['scope']),
                                     ("effect", tuple(sorted(eff.items()))))),
                  duration, origin, source_id=source_id,
                  controller=player if controller is None else controller)


def prohibit_group(controller: int, shape: dict, action: str, duration: Duration,
                   source_id: int = 0) -> Effect:
    """"Creatures [<controller filter>] [without <kw>] can't <action>" — a
    FILTER selector relative to the effect's controller (CR 508.1c/509.1b)."""
    return Effect(Selector(SelectorKind.FILTER, player=controller,
                           filter=(("controller", shape['controller']),
                                   ("without_keyword", shape['without_keyword']))),
                  Modification(ModKind.PROHIBIT, action=action),
                  duration, OriginKind.RESOLVED, source_id=source_id,
                  controller=controller)
