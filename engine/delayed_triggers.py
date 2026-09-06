"""Delayed triggered abilities (CR 603.7) — the general queue.

A delayed trigger is an effect that says "at the beginning of <a later
step>, <do something>". It is created by a resolving spell or ability, it
waits, it fires ONCE at its stated step, and then it is gone. Three
properties make it a subsystem rather than a rider on whatever created it:

  * It outlives its source. CR 603.7d — once created, the delayed trigger
    is independent of the object that made it. Mishra's Bauble sacrifices
    itself to pay its own cost, and the draw still happens; a token copy
    that is removed before the end step still had its sacrifice rider
    created. Anything that reaches back into the source at fire time is
    wrong.
  * It fires exactly once. Not once per step, not once per matching
    permanent — one creation, one firing, then removal from the queue.
  * Its timing is a STEP, not a turn count. "the next turn's upkeep" is
    whoever's turn comes next; "your next upkeep" skips the opponent's.

Why a queue rather than a third special case
--------------------------------------------
The engine already carried two hand-rolled delayed riders — `GameState.
_end_of_turn_exiles` (temporary reanimation) and `_end_of_turn_sacrifices`
(Mobilize) — each with its own list, its own register function, and its
own hand-written firing block in `TurnManager.end_of_turn_cleanup`. Both
are end-step-only and both hard-code WHAT happens as well as WHEN. A third
list for "draw at the next upkeep" would have been the fourth copy of the
same shape.

Measured class (ModernAtomic, 22 506 cards, 2026-08-30):

    "at the beginning of the next end step, …"        307 cards
    "at the beginning of your next upkeep, …"          44 cards
    "at the beginning of your next end step, …"        13 cards
    "at the beginning of the next turn's upkeep, …"     5 cards
    ─────────────────────────────────────────────────────────
    any "at the beginning of the NEXT …" trigger      375 cards

375 cards create a delayed trigger; 48 of them create one from an
ACTIVATED ability. The queue is the mechanic those 375 share. What each
delayed effect DOES stays with the subsystem that owns that effect — the
queue only knows when to run a callable and that it must run once.

The queue does not put its triggers on the stack. That matches the two
riders it generalises, which resolve directly at the end step; the engine
models no priority window at these moments, so a stack item would add a
resolution hop with no observable difference.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Sequence, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .game_state import GameState


class DelayedTriggerStep(Enum):
    """The step at which the engine drains the queue.

    One member per place the turn loop calls `fire_for_step`. A timing
    belongs to exactly one step (`TIMING_STEP` below), so a step that is
    never reached can never fire a trigger early.
    """
    UPKEEP = "upkeep"
    END_STEP = "end_step"


class DelayedTriggerTiming(Enum):
    """When a delayed trigger fires, in the printed vocabulary.

    The `YOUR_*` members are the controller-restricted variants: they skip
    every intervening opponent turn, which is exactly the difference
    between "at the beginning of the next turn's upkeep" (Mishra's Bauble
    — whoever's turn is next) and "at the beginning of your next upkeep"
    (44 Modern cards — your own next turn).
    """
    NEXT_UPKEEP = "next_upkeep"
    YOUR_NEXT_UPKEEP = "your_next_upkeep"
    NEXT_END_STEP = "next_end_step"
    YOUR_NEXT_END_STEP = "your_next_end_step"


#: Which drain point each timing belongs to. Exhaustive by construction —
#: `fire_for_step` reads this map, so a timing added without a step here
#: would raise rather than silently never fire.
TIMING_STEP = {
    DelayedTriggerTiming.NEXT_UPKEEP: DelayedTriggerStep.UPKEEP,
    DelayedTriggerTiming.YOUR_NEXT_UPKEEP: DelayedTriggerStep.UPKEEP,
    DelayedTriggerTiming.NEXT_END_STEP: DelayedTriggerStep.END_STEP,
    DelayedTriggerTiming.YOUR_NEXT_END_STEP: DelayedTriggerStep.END_STEP,
}

#: Timings restricted to the controller's own turn.
CONTROLLER_TURN_ONLY = frozenset({
    DelayedTriggerTiming.YOUR_NEXT_UPKEEP,
    DelayedTriggerTiming.YOUR_NEXT_END_STEP,
})

#: Timings that cannot fire during the turn they were created in. An
#: upkeep step named "the NEXT turn's upkeep" is by definition a later
#: turn's; an end step named "the next end step" is this turn's own when
#: the trigger was created before it, which is why the end-step timings
#: are absent here and rely on the same-pass guard in `fire_for_step`.
STRICTLY_LATER_TURN = frozenset({
    DelayedTriggerTiming.NEXT_UPKEEP,
    DelayedTriggerTiming.YOUR_NEXT_UPKEEP,
})


@dataclass
class DelayedTrigger:
    """One pending delayed trigger.

    `effect` takes the GameState and nothing else: every other value the
    effect needs (controller, amount, the card it tracks) is closed over at
    CREATION time, which is what makes CR 603.7d's independence from the
    source structural rather than a rule someone has to remember.

    `created_turn` is `GameState.turn_number` — the internal half-turn
    counter that increments once per player turn — captured at
    registration. It is the whole of the "not this turn" test.
    """
    timing: DelayedTriggerTiming
    controller: int
    effect: Callable[["GameState"], None]
    description: str
    created_turn: int


class DelayedTriggerQueue:
    """Holds pending delayed triggers and drains them at the right step.

    Stateless with respect to WHAT the triggers do — it owns only the
    timing rule and the fire-once guarantee.
    """

    def __init__(self) -> None:
        self._pending: List[DelayedTrigger] = []

    def register(self, trigger: DelayedTrigger) -> None:
        self._pending.append(trigger)

    @property
    def pending(self) -> Sequence[DelayedTrigger]:
        """Read-only view — for tests and for AI projection, never for
        mutation (firing is `fire_for_step`'s job and it must stay the only
        path that removes an entry)."""
        return tuple(self._pending)

    def clear(self) -> None:
        self._pending.clear()

    def is_due(self, trigger: DelayedTrigger, game: "GameState",
               step: DelayedTriggerStep) -> bool:
        """Is this trigger's stated moment happening right now?"""
        if TIMING_STEP[trigger.timing] is not step:
            return False
        if (trigger.timing in CONTROLLER_TURN_ONLY
                and game.active_player != trigger.controller):
            return False
        if trigger.timing in STRICTLY_LATER_TURN:
            return game.turn_number > trigger.created_turn
        return game.turn_number >= trigger.created_turn

    def fire_for_step(self, game: "GameState",
                      step: DelayedTriggerStep) -> int:
        """Fire every due trigger once and drop it. Returns how many fired.

        The pending list is SNAPSHOT before the loop: a trigger created by
        an effect that fires in this same pass belongs to a later step, not
        to this one, and a live list would let it fire immediately (and, for
        a self-recreating rider, forever).
        """
        due = [t for t in list(self._pending) if self.is_due(t, game, step)]
        for trigger in due:
            # Removed BEFORE the effect runs, so an effect that raises, or
            # that re-enters the queue, can never fire this entry twice.
            try:
                self._pending.remove(trigger)
            except ValueError:  # pragma: no cover — defensive
                continue
            game.log.append(
                f"T{game.display_turn}: delayed trigger — "
                f"{trigger.description}")
            trigger.effect(game)
        return len(due)
