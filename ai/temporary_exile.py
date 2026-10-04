"""How much of a permanent an "exile until the next end step" removes.

The exiled permanent returns as a new object before its controller's next
turn (CR 400.7). What the exile takes away is only what changes across that
return:

* a planeswalker returns at its printed loyalty (CR 306.5b), so the exile
  removes its loyalty above printed. Combat damage already headed at it is
  lost as well, because a planeswalker removed from combat is dealt none
  (CR 506.4); that damage is subtracted;
* any other permanent keeps the whole-permanent value the removal pickers
  already use (`ai.permanent_threat` via the engine's threat score).

The result is a share in [0, 1] that scales that threat score, so every
candidate is compared in one currency.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState

WHOLE = 1.0     # a non-walker: the exile removes it entirely for the turn
NOTHING = 0.0   # the exile removes nothing that matters


def _incoming_combat_damage(walker: "CardInstance", game: "GameState",
                            controller: int) -> int:
    """Power of `controller`'s creatures attacking `walker` (CR 508.1b)."""
    return sum(max(c.power, 0) for c in game.players[controller].battlefield
               if getattr(c, "attacking", False)
               and getattr(c, "attacked_planeswalker", None) is walker)


def temporary_exile_share(target: "CardInstance", game: "GameState",
                          controller: int) -> float:
    """Share of `target`'s value that `controller` removes by exiling it
    until the next end step (see the module docstring)."""
    from engine.cards import CardType
    if CardType.PLANESWALKER not in target.template.card_types:
        return WHOLE
    current = target.loyalty_counters
    if current <= 0:
        return NOTHING
    printed = target.template.loyalty or 0
    removed = current - printed - _incoming_combat_damage(target, game, controller)
    return max(removed, 0) / current
