"""Discard funnel — the single owner of the discard event (CR 701.8).

Every "discard a card" in the engine — opponent-forced discard
(Thoughtseize class), self-discard as an effect (Faithful Mending
class), discard as a cost (cycling, a spell's or an ability's additional
cost), and the cleanup-step discard to maximum hand size — routes
through `DiscardManager.discard_card`. Having one funnel is what lets a
*replacement* on the discard event be implemented once instead of at
every site.

The replacement implemented here is Madness (CR 702.35):

  702.35a  "If you would discard this card, discard it, but exile it
           instead of putting it into your graveyard."
  702.35b  "When this card is exiled this way, its owner may cast it
           by paying [cost] rather than paying its mana cost. If that
           player doesn't, they put this card into their graveyard."

The discard exiles the card (hand → exile, through the zone funnel) and
the "when ... exiled this way" ability triggers: a TRIGGERED_ABILITY
goes on the stack, so it resolves the next time players receive
priority (CR 603.3) — after the ability or spell that made the player
discard has finished resolving (CR 608.2), above a spell or ability
whose cost was the discard (`Stack.below_waiting_triggers`, CR
601.2i, 602.2e), and in the cleanup step with another cleanup step following
(CR 514.3a). The card is cast only while that trigger resolves
(`resolve_madness_trigger`, CR 608.2g) through the ordinary `cast_spell`
pipeline with a madness alternative-cost branch — `_madness_pending` on
the instance opens the exile window `can_cast`/`cast_spell` honour, for
the duration of the offer only. The "may" is delegated to
`callbacks.decide_offered_cast`, the uniform seam for engine-offered
casts; the engine never scores.

Abstraction contract: the mechanic is keyed on the typed
`CardTemplate.madness_cost` field (parsed once at load by
`oracle_parser.parse_madness_cost`), never on card names or oracle
text. Class size: 47 Modern-legal cards.
"""
from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState

# The stack item's description that names a madness trigger; the cast
# audit (`cast_manager._audit_madness_cast`) reads it back.
MADNESS_TRIGGER = "madness"


class DiscardManager:
    """Static discard funnel. Mirrors the other `*Manager` statics that
    `GameState` delegates to (TurnManager, CastManager, …)."""

    @staticmethod
    def discard_card(game: "GameState", player_idx: int,
                     card: "CardInstance", cause: str = "discard") -> str:
        """Discard `card` from `player_idx`'s hand, applying discard
        replacements. Returns the zone the card ended in: "graveyard",
        or "exile" for a madness card, whose trigger is then on the stack
        (CR 702.35a-b) — its cast or its move to the graveyard happens
        when that trigger resolves, never inside the discard.

        Callers are responsible for CHOOSING the card (via
        `callbacks.choose_discard`); this funnel only performs the event.
        """
        if card.template.madness_cost is None:
            game.zone_mgr.move_card(
                game, card, "hand", "graveyard", cause=cause)
            return "graveyard"

        # CR 702.35a — the discard happens, but into exile.
        game.zone_mgr.move_card(
            game, card, "hand", "exile",
            cause=f"{cause}; madness — discarded into exile")

        # CR 702.35b, 603.3 — the "exiled this way" trigger, controlled by
        # the card's owner, naming the exiled object (CR 400.7) by the
        # zone and entry ordinal it has now.
        from .stack import StackItem, StackItemType
        game.stack.push(StackItem(
            item_type=StackItemType.TRIGGERED_ABILITY, source=card,
            controller=card.owner, ability=None,
            description=MADNESS_TRIGGER,
            effect=functools.partial(DiscardManager.resolve_madness_trigger,
                                     exiled=(card.zone,
                                             card.battlefield_entry_seq))))
        return "exile"

    @staticmethod
    def resolve_madness_trigger(game: "GameState", source: "CardInstance",
                                controller: int, targets=None, *,
                                exiled: tuple) -> None:
        """The madness trigger resolves (CR 702.35b, 608.2g): its
        controller may cast the card for its madness cost; if they don't
        (or can't), the card goes into its owner's graveyard. A card that
        has left exile since it was exiled this way is a new object the
        trigger no longer names, and nothing happens (CR 400.7).

        Known gap, shared with `effect_resolver.Handle`: the engine keeps
        only a battlefield-entry ordinal, so a card that left exile and was
        exiled again by another route before the trigger resolves is not
        told apart."""
        card = source
        if ((card.zone, card.battlefield_entry_seq) != exiled
                or all(card is not c for c in game.players[card.owner].exile)):
            return

        # The pending flag opens the exile cast window for
        # `can_cast`/`cast_spell`; it is closed again whatever happens so
        # the card is never castable from exile afterwards.
        card._madness_pending = True
        try:
            cast = bool(
                game.can_cast(controller, card)
                and game.callbacks.decide_offered_cast(game, controller, card)
                and game.cast_spell(controller, card)
            )
        finally:
            card._madness_pending = False

        if cast:
            game.log.append(
                f"T{game.display_turn} P{controller + 1}: Madness — cast "
                f"{card.name} from exile (pays {card.template.madness_cost})")
            return

        game.zone_mgr.move_card(
            game, card, "exile", "graveyard",
            cause="madness — not cast, put into graveyard")
