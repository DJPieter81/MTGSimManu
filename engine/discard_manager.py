"""Discard funnel — the single owner of the discard event (CR 701.8).

Every "discard a card" in the engine — opponent-forced discard
(Thoughtseize class), self-discard as an effect (Faithful Mending
class), discard as a cost (cycling), and the cleanup-step discard to
maximum hand size — routes through `DiscardManager.discard_card`.
Having one funnel is what lets a *replacement* on the discard event be
implemented once instead of at every site.

The replacement implemented here is Madness (CR 702.35):

  702.35a  "If you would discard this card, discard it, but exile it
           instead of putting it into your graveyard."
  702.35b  "When this card is exiled this way, its owner may cast it
           by paying [cost] rather than paying its mana cost. If that
           player doesn't, they put this card into their graveyard."

The card physically travels hand → exile → (stack via cast | graveyard).
Every hop goes through `zone_mgr.move_card` (the zone funnel). The cast
itself is the ordinary `cast_spell` pipeline with a madness alternative
-cost branch — `_madness_pending` on the instance marks the exile
window during which `can_cast`/`cast_spell` honour the madness route.
The "may" is delegated to `callbacks.decide_offered_cast`, the uniform
seam for engine-offered casts; the engine never scores.

Abstraction contract: the mechanic is keyed on the typed
`CardTemplate.madness_cost` field (parsed once at load by
`oracle_parser.parse_madness_cost`), never on card names or oracle
text. Class size: 47 Modern-legal cards.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


class DiscardManager:
    """Static discard funnel. Mirrors the other `*Manager` statics that
    `GameState` delegates to (TurnManager, CastManager, …)."""

    @staticmethod
    def discard_card(game: "GameState", player_idx: int,
                     card: "CardInstance", cause: str = "discard") -> str:
        """Discard `card` from `player_idx`'s hand, applying discard
        replacements. Returns the zone the card ended in: "graveyard",
        or "stack" when a madness cast was made.

        Callers are responsible for CHOOSING the card (via
        `callbacks.choose_discard`); this funnel only performs the event.
        """
        template = card.template

        if template.madness_cost is None:
            game.zone_mgr.move_card(
                game, card, "hand", "graveyard", cause=cause)
            return "graveyard"

        # CR 702.35a — the discard happens, but into exile.
        game.zone_mgr.move_card(
            game, card, "hand", "exile",
            cause=f"{cause}; madness — discarded into exile")

        # CR 702.35b — reflexive trigger: cast for the madness cost, or
        # put into the graveyard. The pending flag opens the exile cast
        # window for `can_cast`/`cast_spell`; it is closed again whatever
        # happens so the card is never castable from exile afterwards.
        card._madness_pending = True
        try:
            cast = bool(
                game.can_cast(player_idx, card)
                and game.callbacks.decide_offered_cast(game, player_idx, card)
                and game.cast_spell(player_idx, card)
            )
        finally:
            card._madness_pending = False

        if cast:
            game.log.append(
                f"T{game.display_turn} P{player_idx + 1}: Madness — cast "
                f"{card.name} from exile (pays {template.madness_cost})")
            return "stack"

        game.zone_mgr.move_card(
            game, card, "exile", "graveyard",
            cause="madness — not cast, put into graveyard")
        return "graveyard"
