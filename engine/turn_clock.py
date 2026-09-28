"""The event clock — the single owner of *when* temporal state changes.

The turn manager emits a `ClockEvent` at each step boundary; every reset,
expiry and "at the beginning of …" firing is a subscriber here, never an
ad-hoc call inside a step handler. Durations (engine/effect_model.py) are
predicates over this same stream.

Subscribers run in registration order. The registrations below are the
resets that previously ran inline, in the order they ran — so moving them
onto the clock changed no behaviour (proved by byte-identical seeded game
logs when the clock was introduced).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from engine.game_state import GameState


class Clock(Enum):
    TURN_BEGINS = "turn_begins"   # the untap step of `player`'s turn begins
    UPKEEP = "upkeep"
    END_STEP = "end_step"
    CLEANUP = "cleanup"


@dataclass(frozen=True)
class ClockEvent:
    kind: Clock
    player: Optional[int] = None


_SUBSCRIBERS: Dict[Clock, List[Tuple[str, Callable]]] = {k: [] for k in Clock}


def subscribe(kind: Clock, name: str):
    """Register `fn(game, event)` to run on `kind`, after those already
    registered for it."""
    def deco(fn):
        _SUBSCRIBERS[kind].append((name, fn))
        return fn
    return deco


def subscribers(kind: Clock) -> List[str]:
    return [name for name, _ in _SUBSCRIBERS[kind]]


def emit(game: "GameState", event: ClockEvent) -> None:
    for _name, fn in _SUBSCRIBERS[event.kind]:
        fn(game, event)


# ─────────────────────────────────────────────────────────────────────
# Subscribers — the temporal owners, in the order they ran inline.
# ─────────────────────────────────────────────────────────────────────

@subscribe(Clock.TURN_BEGINS, "rule_effects_expiry")
def _expire_rule_effects_at_turn_begin(game, ev):
    # CR 611.2: resolved rule effects end when their duration says so —
    # "this turn" ends with the game turn, "until your next turn" as that
    # player's turn begins. The one expiry path (effect_model.Duration).
    game.continuous_effects.expire_rule_effects(ev)


@subscribe(Clock.TURN_BEGINS, "this_turn_effects_audit")
def _audit_this_turn_expired(game, ev):
    from .rules_audit import enabled as _audit_on, check as _audit_check
    if not _audit_on():
        return
    from .effect_model import DurationKind
    _audit_check("611.2a/this_turn_effect_expired",
                 not any(e.duration.kind is DurationKind.THIS_TURN
                         for e in game.continuous_effects._rule_effects),
                 "a 'this turn' effect outlived its turn", game=game)


@subscribe(Clock.TURN_BEGINS, "player_turn_state_reset")
def _reset_turn_tracking(game, ev):
    game.players[ev.player].reset_turn_tracking()


@subscribe(Clock.TURN_BEGINS, "until_next_turn_expiry")
def _expire_until_next_turn(game, ev):
    # CR 611.2b: "until your next turn" effects end as this turn begins.
    game.continuous_effects.cleanup_until_next_turn(ev.player)


@subscribe(Clock.TURN_BEGINS, "until_next_turn_audit")
def _audit_until_next_turn(game, ev):
    from .rules_audit import enabled as _audit_on, check as _audit_check
    if not _audit_on():
        return
    player = game.players[ev.player]
    _audit_check(
        "611.2b/until_next_turn_expired",
        not any(e.duration == "until_next_turn" and e.controller == ev.player
                for e in game.continuous_effects._effects)
        and not player.temp_cost_rules and not player.flash_permission_types,
        f"P{ev.player+1}'s next turn began with an until-your-next-turn effect",
        game=game)


@subscribe(Clock.TURN_BEGINS, "cross_turn_event_counters_reset")
def _reset_cross_turn_counters(game, ev):
    # "This turn" is one game-turn clock shared by both players: the
    # non-active player's per-turn EVENT tallies reset at this boundary too.
    game.players[1 - ev.player].reset_cross_turn_event_counters()


@subscribe(Clock.UPKEEP, "delayed_triggers_upkeep")
def _fire_upkeep_delayed(game, ev):
    from .delayed_triggers import DelayedTriggerStep
    game.fire_delayed_triggers(DelayedTriggerStep.UPKEEP)


@subscribe(Clock.END_STEP, "delayed_triggers_end_step")
def _fire_end_step_delayed(game, ev):
    from .delayed_triggers import DelayedTriggerStep
    game.fire_delayed_triggers(DelayedTriggerStep.END_STEP)


@subscribe(Clock.CLEANUP, "rule_effects_expiry")
def _expire_rule_effects_at_cleanup(game, ev):
    game.continuous_effects.expire_rule_effects(ev)


@subscribe(Clock.CLEANUP, "end_of_turn_effects_expiry")
def _expire_end_of_turn(game, ev):
    game.continuous_effects.cleanup_end_of_turn()
