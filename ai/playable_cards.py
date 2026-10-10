"""The cards a player's turn plan can play (CR 305.1, 601.2a).

A turn plan -- a storm chain, a combo's readiness, a payoff check, the
turn planner's orderings, a lethal line -- is made of the cards the player
may play from where they are: the hand, and the exiled cards a resolved
permission names ("exile the top two cards of your library ... you may
play those cards"; the engine's one read path is
`rules_query.permitted_cards`). Reading the hand alone makes an impulse
draw's cards invisible to the plan: they are castable, but no plan counts
them, and they expire unplayed.

`playable_cards` is that list. `plan_view` is the player as such a plan
sees it: every attribute is the player's own except `hand`, which is the
playable list; it is read-only, so a plan cannot write through it. Reads
that are about the hand itself -- discard, hand size, what an opponent can
take -- keep the player.
"""
from __future__ import annotations

from typing import Any, List


def playable_cards(game: Any, player_idx: int) -> List[Any]:
    """The hand, then the cards a permission lets the player play from
    exile (no timing or cost check: each play makes its own)."""
    player = game.players[player_idx]
    hand = list(player.hand)
    if not getattr(player, "exile", None):
        return hand          # nothing exiled, nothing a permission names
    from engine import rules_query
    return hand + rules_query.permitted_cards(game, player_idx)


def expires_this_turn(game: Any, player_idx: int, card: Any) -> bool:
    """A card the player may play only until this turn's cleanup (the
    last turn of an impulse draw's permission): it is gone at cleanup
    whether or not it is played."""
    if getattr(card, "zone", None) != "exile":
        return False
    from engine import rules_query
    return rules_query.permission_ends_this_turn(game, player_idx, card)


def held_cards(game: Any, player_idx: int) -> List[Any]:
    """The cards the player holds as resources past this turn: the
    playable cards except those whose permission ends this turn. A
    position's card count reads these; casting a card the player does not
    hold spends nothing it would keep."""
    return [c for c in playable_cards(game, player_idx)
            if not expires_this_turn(game, player_idx, c)]


class _PlanView:
    """A player whose `hand` is the playable list; read-only."""

    __slots__ = ("_player", "hand")

    def __init__(self, player: Any, hand: List[Any]) -> None:
        object.__setattr__(self, "_player", player)
        object.__setattr__(self, "hand", hand)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._player, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"a turn plan's view of the player is "
                             f"read-only (tried to set {name!r})")


def plan_view(game: Any, player_idx: int) -> Any:
    """The player as a turn plan sees it: `hand` is `playable_cards`. The
    player itself when no permission names a card (the common case)."""
    hand = playable_cards(game, player_idx)
    player = game.players[player_idx]
    if len(hand) == len(player.hand):
        return player
    return _PlanView(player, hand)
