"""Damage routing primitive (W0-D).

Single source of truth for how N damage from any source is applied to
any target — creature, planeswalker, or player.  Backs every burn
spell, ETB ping, attack trigger, and combat damage step uniformly so
the AI's target-enumeration layer (M10 in `ai/ev_player.py`) can
treat the three target types symmetrically.

Routing rules (mechanic-driven, no per-source conditionals):

- creature target → damage stacks on ``CardInstance.damage_marked``;
  state-based actions (handled by ``GameState.check_state_based_actions``
  via the dynamic ``is_dead`` check) move it to the graveyard when
  damage ≥ toughness.
- planeswalker target → damage is removed from ``loyalty_counters``
  one-for-one; SBA pulls it off the battlefield when loyalty hits 0.
- player target → damage is removed from ``life``; the source's
  controller's ``damage_dealt_this_turn`` is bumped when supplied.

The dispatch is on the target's type (``CardType.PLANESWALKER`` vs
``CardInstance.template.is_creature`` vs ``PlayerState``), not on the
caller's identity.  No card names appear.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState
    from engine.player_state import PlayerState


def deal_damage(
    game: "GameState",
    target: Union["CardInstance", "PlayerState"],
    amount: int,
    source_controller: int | None = None,
) -> None:
    """Apply ``amount`` damage to ``target``.

    ``target`` may be a ``CardInstance`` (creature or planeswalker) or
    a ``PlayerState``.  Dispatch is on the target's type; the caller
    does not branch.

    ``source_controller`` is the player index whose
    ``damage_dealt_this_turn`` counter should be bumped on player-face
    damage (used by aggro lethal tracking).  ``None`` skips the bump
    — appropriate for sources that already book-keep damage elsewhere
    (combat damage, abilities that bypass the source).
    """
    if amount <= 0:
        return

    # Import locally to avoid circular dependencies at module load.
    from engine.cards import CardInstance, CardType
    from engine.player_state import PlayerState

    if isinstance(target, PlayerState):
        target.life -= amount
        if source_controller is not None:
            game.players[source_controller].damage_dealt_this_turn += amount
        return

    if isinstance(target, CardInstance):
        if target.zone != "battlefield":
            # Target left the battlefield between targeting and
            # resolution — damage is lost (CR 608.2b: spell does
            # nothing if its target is illegal).
            return
        types = target.template.card_types
        if CardType.PLANESWALKER in types:
            new_loyalty = max(0, target.loyalty_counters - amount)
            target.loyalty_counters = new_loyalty
            # SBA will pull a 0-loyalty planeswalker on the next check.
            game.check_state_based_actions()
            return
        if target.template.is_creature:
            target.damage_marked = getattr(target, "damage_marked", 0) + amount
            if target.is_dead:
                game._creature_dies(target)
            return

    # Unknown target type: silently no-op rather than crash.  Logged
    # via SBA / replay layer if needed.
    return
