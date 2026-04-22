"""
Callback protocol for engine -> AI decisions.

The engine layer must never import from the AI layer directly.
Instead, GameState calls methods on a GameCallbacks instance,
which the GameRunner wires to the appropriate AI implementations.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.card_database import CardInstance


class GameCallbacks(Protocol):
    """Protocol that the engine calls for strategic decisions."""

    def should_pay_life_for_untapped(
        self, game: GameState, player_idx: int, land: CardInstance
    ) -> bool:
        """Should this land pay life to enter untapped?"""
        ...

    def choose_fetch_target(
        self, game: GameState, player_idx: int, fetch_card: CardInstance,
        library: List[CardInstance], fetch_colors: list
    ) -> Optional[CardInstance]:
        """Which land should a fetch land search for?"""
        ...

    def should_evoke(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> bool:
        """Should this creature be evoked instead of hardcast?"""
        ...

    def should_dash(
        self, game: GameState, player_idx: int, card: CardInstance,
        can_normal: bool, can_dash: bool
    ) -> bool:
        """Should this creature be dashed instead of hardcast?"""
        ...

    def choose_discard(
        self, game: GameState, player_idx: int,
        hand: List[CardInstance], self_discard: bool
    ) -> CardInstance:
        """Pick the best card to discard.

        self_discard=True: player chose to discard (Faithful Mending).
        self_discard=False: opponent forced discard (Thoughtseize).
        """
        ...


class DefaultCallbacks:
    """Safe defaults: always tapped, first legal target, no evoke, no dash."""

    def should_pay_life_for_untapped(
        self, game: GameState, player_idx: int, land: CardInstance
    ) -> bool:
        return False

    def choose_fetch_target(
        self, game: GameState, player_idx: int, fetch_card: CardInstance,
        library: List[CardInstance], fetch_colors: list
    ) -> Optional[CardInstance]:
        fetchable = [c for c in library if c.template.is_land]
        return fetchable[0] if fetchable else None

    def should_evoke(
        self, game: GameState, player_idx: int, card: CardInstance
    ) -> bool:
        return False

    def should_dash(
        self, game: GameState, player_idx: int, card: CardInstance,
        can_normal: bool, can_dash: bool
    ) -> bool:
        return can_dash and not can_normal

    def choose_discard(
        self, game: GameState, player_idx: int,
        hand: List[CardInstance], self_discard: bool
    ) -> CardInstance:
        """Default: discard highest-CMC card (least-mana-efficient to
        re-cast). This matches the legacy non-AI forced-discard
        behaviour at the pre-refactor GameState._force_discard (sort
        by CMC desc, take head). AI callback implementations should
        override this with a proper discard-scoring strategy."""
        return max(hand, key=lambda c: c.template.cmc or 0)
