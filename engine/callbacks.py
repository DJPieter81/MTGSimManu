"""
Callback protocol for engine -> AI decisions.

The engine layer must never import from the AI layer directly.
Instead, GameState calls methods on a GameCallbacks instance,
which the GameRunner wires to the appropriate AI implementations.

Decision channels are uniform per *kind*, never per mechanic.
`decide_optional_cost` handles every "pay X to gain Y" decision
(shock lands, painlands, fetchlands, Phyrexian mana, Sylvan
Library, hybrid mana, channel, kicker-with-life, ...) by routing
oracle-derived `OptionalCost` descriptors through a single AI
seam.  Engine call sites use `engine.optional_costs.offer_optional_costs`
to discover and present these costs — no mechanic-named callbacks.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.card_database import CardInstance
    from ai.schemas import OptionalCost


class GameCallbacks(Protocol):
    """Protocol that the engine calls for strategic decisions."""

    def decide_optional_cost(
        self, game: GameState, player_idx: int, opt: "OptionalCost"
    ) -> bool:
        """Should this optional cost be paid?

        Uniform entry point for every "pay X to gain Y" decision the
        engine may legally offer.  The AI projects the post-payment
        snapshot via `opt.apply_to_snap` and compares against
        skipping; True means pay.
        """
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

    def decide_optional_cost(
        self, game: GameState, player_idx: int, opt
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
