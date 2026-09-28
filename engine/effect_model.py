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
from typing import Any, FrozenSet, Optional, Tuple


# ── Duration (CR 611.2) ──────────────────────────────────────────────

class DurationKind(Enum):
    THIS_TURN = "this_turn"                       # "this turn" / "until end of turn"
    UNTIL_YOUR_NEXT_TURN = "until_your_next_turn"
    WHILE_SOURCE_ON_BATTLEFIELD = "while_source"  # static abilities (CR 611.3a)
    UNTIL_LEAVES = "until_leaves"                 # "for as long as <obj> …"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class Duration:
    kind: DurationKind
    # UNTIL_YOUR_NEXT_TURN: whose next turn ends it.
    player: Optional[int] = None
    # UNTIL_LEAVES: (instance_id, battlefield_entry_seq) of the tracked object.
    obj: Optional[Tuple[int, int]] = None

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
        if k is DurationKind.UNTIL_LEAVES:
            return getattr(event, "obj", None) == self.obj
        return False   # PERMANENT, WHILE_SOURCE (retracted by derivation)


THIS_TURN = Duration(DurationKind.THIS_TURN)
PERMANENT = Duration(DurationKind.PERMANENT)
WHILE_SOURCE = Duration(DurationKind.WHILE_SOURCE_ON_BATTLEFIELD)


def until_your_next_turn(player: int) -> Duration:
    return Duration(DurationKind.UNTIL_YOUR_NEXT_TURN, player=player)


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

    def covers_player(self, idx: int) -> bool:
        k = self.kind
        return (k is SelectorKind.ALL_PLAYERS
                or (k is SelectorKind.PLAYER and self.player == idx)
                or (k is SelectorKind.OPPONENTS and self.player != idx))


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


_RULE_KINDS = frozenset({ModKind.PROHIBIT, ModKind.PERMIT, ModKind.LIMIT,
                         ModKind.COST_DELTA, ModKind.PREVENT_DAMAGE, ModKind.OBSERVE})


@dataclass(frozen=True)
class Modification:
    kind: ModKind
    # PROHIBIT/PERMIT/LIMIT: the action; others: None.
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
