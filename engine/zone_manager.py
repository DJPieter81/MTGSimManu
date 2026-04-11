"""
MTG Zone Manager
Centralized zone transition handling.

ALL card movements between zones MUST go through this manager.
This ensures:
  1. Replacement effects are checked (CR 614)
  2. Zone-change triggers fire (CR 603)
  3. State cleanup happens consistently (flags, counters, combat state)
  4. The game log is updated

Replaces the scattered pattern of:
    player.hand.remove(card)
    card.zone = "graveyard"
    player.graveyard.append(card)
"""
from __future__ import annotations
from typing import List, Optional, TYPE_CHECKING

from .event_system import EventBus, EventType, GameEvent

if TYPE_CHECKING:
    from .cards import CardInstance, Keyword
    from .game_state import GameState


class ZoneManager:
    """Handles all card movement between zones."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    # ── Public API ──────────────────────────────────────────────────

    def move_card(
        self,
        game: "GameState",
        card: "CardInstance",
        from_zone: str,
        to_zone: str,
        cause: str = "",
        controller_override: Optional[int] = None,
    ) -> bool:
        """Move a card from one zone to another.

        This is the ONLY sanctioned way to change a card's zone.

        Args:
            game: The current game state.
            card: The card instance to move.
            from_zone: The zone the card is currently in.
            to_zone: The destination zone.
            cause: Human-readable reason for the move (for logging).
            controller_override: If set, change the card's controller on ETB.

        Returns:
            True if the move was performed, False if prevented.
        """
        owner = card.owner

        # Validate: card should be in from_zone
        source_list = self._get_zone_list(game, owner, from_zone)
        if card not in source_list:
            # Card is not where we expect — try to find it
            actual_zone = self._find_card_zone(game, card)
            if actual_zone is None:
                return False
            from_zone = actual_zone
            source_list = self._get_zone_list(game, owner, from_zone)

        # Fire ZONE_CHANGE event for replacement effects
        event = GameEvent(
            event_type=EventType.ZONE_CHANGE,
            source=card,
            player=owner,
            extra={
                "from": from_zone,
                "to": to_zone,
                "cause": cause,
                "turn": game.display_turn,
            },
        )
        event, _ = self.event_bus.fire_event(event, game)

        if event.prevented:
            return False

        # Replacement effects may have changed the destination
        actual_to = event.extra.get("to", to_zone)

        # ── Remove from source zone ────────────────────────────────
        if card in source_list:
            source_list.remove(card)

        # ── Clean up state when leaving battlefield ─────────────────
        if from_zone == "battlefield":
            self._cleanup_leaving_battlefield(card)
            # Unregister triggers/replacements for this card
            self.event_bus.unregister_card(card)

        # ── Add to destination zone ─────────────────────────────────
        card.zone = actual_to
        dest_list = self._get_zone_list(game, owner, actual_to)
        dest_list.append(card)

        # ── Handle entering battlefield ─────────────────────────────
        if actual_to == "battlefield":
            if controller_override is not None:
                card.controller = controller_override
            card.enter_battlefield()
            card._game_state = game

        # ── Fire post-move events ───────────────────────────────────
        triggered = []

        if actual_to == "battlefield":
            etb_event = GameEvent(
                event_type=EventType.ENTERS_BATTLEFIELD,
                source=card,
                player=card.controller,
                extra={"turn": game.display_turn, "cause": cause},
            )
            _, etb_triggers = self.event_bus.fire_event(etb_event, game)
            triggered.extend(etb_triggers)

        if from_zone == "battlefield":
            ltb_event = GameEvent(
                event_type=EventType.LEAVES_BATTLEFIELD,
                source=card,
                player=card.controller,
                extra={"turn": game.display_turn, "cause": cause},
            )
            _, ltb_triggers = self.event_bus.fire_event(ltb_event, game)
            triggered.extend(ltb_triggers)

            # "Dies" = creature goes from battlefield to graveyard
            if actual_to == "graveyard" and card.template.is_creature:
                dies_event = GameEvent(
                    event_type=EventType.DIES,
                    source=card,
                    player=card.controller,
                    extra={"turn": game.display_turn, "cause": cause},
                )
                _, dies_triggers = self.event_bus.fire_event(dies_event, game)
                triggered.extend(dies_triggers)

        # Queue triggered abilities (the caller or rules engine puts them on stack)
        for trig in triggered:
            if trig.goes_on_stack:
                game.queue_trigger(trig)

        # Log the move
        if cause:
            game.log.append(
                f"T{game.display_turn}: {card.name} moved "
                f"{from_zone} -> {actual_to} ({cause})"
            )

        return True

    def move_card_to_graveyard(
        self, game: "GameState", card: "CardInstance", cause: str = ""
    ) -> bool:
        """Convenience: move a card from its current zone to graveyard."""
        return self.move_card(game, card, card.zone, "graveyard", cause=cause)

    def move_card_to_exile(
        self, game: "GameState", card: "CardInstance", cause: str = ""
    ) -> bool:
        """Convenience: move a card from its current zone to exile."""
        return self.move_card(game, card, card.zone, "exile", cause=cause)

    def move_card_to_hand(
        self, game: "GameState", card: "CardInstance", cause: str = ""
    ) -> bool:
        """Convenience: move a card from its current zone to hand."""
        return self.move_card(game, card, card.zone, "hand", cause=cause)

    def move_card_to_battlefield(
        self, game: "GameState", card: "CardInstance",
        from_zone: str = "stack", cause: str = "",
        controller: Optional[int] = None,
    ) -> bool:
        """Convenience: move a card to the battlefield."""
        return self.move_card(
            game, card, from_zone, "battlefield",
            cause=cause, controller_override=controller,
        )

    # ── Internal Helpers ────────────────────────────────────────────

    def _get_zone_list(
        self, game: "GameState", player_idx: int, zone_name: str
    ) -> List["CardInstance"]:
        """Get the list representing a player's zone."""
        player = game.players[player_idx]
        zone_map = {
            "library": player.library,
            "hand": player.hand,
            "battlefield": player.battlefield,
            "graveyard": player.graveyard,
            "exile": player.exile,
        }
        return zone_map.get(zone_name, [])

    def _find_card_zone(
        self, game: "GameState", card: "CardInstance"
    ) -> Optional[str]:
        """Find which zone a card is actually in."""
        player = game.players[card.owner]
        for zone_name in ["library", "hand", "battlefield", "graveyard", "exile"]:
            zone_list = getattr(player, zone_name)
            if card in zone_list:
                return zone_name
        return None

    def _cleanup_leaving_battlefield(self, card: "CardInstance"):
        """Reset all battlefield-specific state when a card leaves."""
        # Combat state
        card.attacking = False
        card.blocking = None
        card.blocked_by = []

        # Damage
        card.damage_marked = 0

        # Temporary effects (until end of turn effects end when leaving)
        card.temp_power_mod = 0
        card.temp_toughness_mod = 0
        card.temp_keywords.clear()

        # Summoning sickness
        card.summoning_sick = False
        card.entered_battlefield_this_turn = False
        card.attacked_this_turn = False

        # Tapped state
        card.tapped = False

        # Alternative cast flags
        card._dashed = False
        card._evoked = False
        card._escaped = False

        # Instance tags (equipment, etc.)
        card.instance_tags.clear()

        # Note: continuous effects from this source are cleaned up
        # by ContinuousEffectsManager._cleanup_stale_effects() on next recalculate()

        # Counters are removed when leaving battlefield
        card.plus_counters = 0
        card.minus_counters = 0
        card.loyalty_counters = 0
        card.other_counters.clear()

        # Clear game state reference
        card._game_state = None
