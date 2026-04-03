"""
MTG Event System
Central event bus for triggers and replacement effects.
Implements Comprehensive Rules 603 (Triggers) and 614 (Replacement Effects).

All game-state mutations fire events through this system, allowing cards
to register triggered abilities and replacement effects generically
instead of requiring card-specific code in the engine.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING,
)
from collections import defaultdict
from enum import Enum

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


# ─── Event Types ────────────────────────────────────────────────────

class EventType(Enum):
    """All game events that can trigger abilities or be replaced."""

    # Zone transitions
    ZONE_CHANGE = "zone_change"
    ENTERS_BATTLEFIELD = "etb"
    LEAVES_BATTLEFIELD = "ltb"
    DIES = "dies"

    # Game actions
    DAMAGE_DEALT = "damage_dealt"
    LIFE_GAINED = "life_gained"
    LIFE_LOST = "life_lost"
    SPELL_CAST = "spell_cast"
    ABILITY_ACTIVATED = "ability_activated"
    DRAW_CARD = "draw_card"

    # Combat
    ATTACKER_DECLARED = "attacker_declared"
    BLOCKER_DECLARED = "blocker_declared"
    COMBAT_DAMAGE_DEALT = "combat_damage_dealt"

    # Turn structure
    BEGINNING_OF_UPKEEP = "upkeep"
    BEGINNING_OF_COMBAT = "begin_combat"
    END_OF_TURN = "end_step"

    # Counters and resources
    COUNTER_ADDED = "counter_added"
    COUNTER_REMOVED = "counter_removed"
    LAND_PLAYED = "land_played"
    ENERGY_PRODUCED = "energy_produced"
    ENERGY_SPENT = "energy_spent"

    # Special
    CREATURE_DESTROYED = "creature_destroyed"
    PERMANENT_SACRIFICED = "permanent_sacrificed"
    CARD_MILLED = "card_milled"
    TOKEN_CREATED = "token_created"


# ─── Game Event ─────────────────────────────────────────────────────

@dataclass
class GameEvent:
    """A single game event that may trigger abilities or be replaced.

    Attributes:
        event_type: The category of event.
        source: The card that caused the event (e.g., the creature that dealt damage).
        player: The player index associated with the event.
        target: The target of the event (card, player index, etc.).
        amount: Numeric value (damage amount, life gained, etc.).
        extra: Additional context (zone names, cause string, etc.).
        prevented: If True, the event was prevented by a replacement effect.
    """
    event_type: EventType
    source: Optional[CardInstance] = None
    player: Optional[int] = None
    target: Optional[Any] = None
    amount: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)
    prevented: bool = False

    def prevent(self):
        """Mark this event as prevented (replacement effect)."""
        self.prevented = True


# ─── Registrations ──────────────────────────────────────────────────

@dataclass
class TriggerRegistration:
    """A triggered ability registered with the event bus.

    Attributes:
        card: The permanent that has this triggered ability.
        controller: The player who controls the permanent.
        condition: Callable(event, source_card, game_state) -> bool.
                   Returns True if this trigger should fire for the given event.
        effect: Callable(game_state, source_card, controller, event) -> None.
                The effect to execute when the trigger resolves.
        description: Human-readable description for logging.
        goes_on_stack: Whether this trigger uses the stack (most do).
        zone_required: The zone the source card must be in for the trigger to work.
                       Default "battlefield" for most permanents.
    """
    card: CardInstance
    controller: int
    condition: Callable
    effect: Callable
    description: str = ""
    goes_on_stack: bool = True
    zone_required: str = "battlefield"

    def is_valid(self) -> bool:
        """Check if this trigger registration is still valid."""
        return self.card.zone == self.zone_required


@dataclass
class ReplacementRegistration:
    """A replacement effect registered with the event bus.

    Replacement effects modify or prevent events before they happen (CR 614).

    Attributes:
        card: The permanent that has this replacement effect.
        controller: The player who controls the permanent.
        condition: Callable(event, source_card, game_state) -> bool.
        apply_replacement: Callable(event, source_card, game_state) -> GameEvent.
                           Returns the modified event (or prevents it).
        description: Human-readable description.
        self_only: If True, only applies to events involving this card.
    """
    card: CardInstance
    controller: int
    condition: Callable
    apply_replacement: Callable
    description: str = ""
    self_only: bool = False

    def is_valid(self) -> bool:
        """Check if this replacement registration is still valid."""
        return self.card.zone == "battlefield"


# ─── Event Bus ──────────────────────────────────────────────────────

class EventBus:
    """Central event dispatcher for the MTG rules engine.

    Cards register their triggered abilities and replacement effects here.
    When a game action fires an event, the bus:
      1. Applies applicable replacement effects (CR 614).
      2. Collects triggered abilities that should trigger (CR 603).
      3. Returns both the (possibly modified) event and the list of triggers.

    The caller (typically the Rules Engine or Game Actions layer) is
    responsible for putting triggered abilities on the stack in APNAP order.
    """

    def __init__(self):
        self._triggers: Dict[EventType, List[TriggerRegistration]] = defaultdict(list)
        self._replacements: Dict[EventType, List[ReplacementRegistration]] = defaultdict(list)
        self._event_log: List[GameEvent] = []
        self._max_log_size: int = 500

    # ── Registration ────────────────────────────────────────────────

    def register_trigger(
        self,
        event_type: EventType,
        card: "CardInstance",
        controller: int,
        condition: Callable,
        effect: Callable,
        description: str = "",
        goes_on_stack: bool = True,
        zone_required: str = "battlefield",
    ):
        """Register a triggered ability for a card."""
        reg = TriggerRegistration(
            card=card,
            controller=controller,
            condition=condition,
            effect=effect,
            description=description,
            goes_on_stack=goes_on_stack,
            zone_required=zone_required,
        )
        self._triggers[event_type].append(reg)

    def register_replacement(
        self,
        event_type: EventType,
        card: "CardInstance",
        controller: int,
        condition: Callable,
        apply_replacement: Callable,
        description: str = "",
        self_only: bool = False,
    ):
        """Register a replacement effect for a card."""
        reg = ReplacementRegistration(
            card=card,
            controller=controller,
            condition=condition,
            apply_replacement=apply_replacement,
            description=description,
            self_only=self_only,
        )
        self._replacements[event_type].append(reg)

    def unregister_card(self, card: "CardInstance"):
        """Remove all registrations for a card (when it leaves the battlefield)."""
        for event_type in list(self._triggers.keys()):
            self._triggers[event_type] = [
                r for r in self._triggers[event_type] if r.card != card
            ]
        for event_type in list(self._replacements.keys()):
            self._replacements[event_type] = [
                r for r in self._replacements[event_type] if r.card != card
            ]

    # ── Event Firing ────────────────────────────────────────────────

    def fire_event(
        self, event: GameEvent, game_state: "GameState" = None
    ) -> Tuple[GameEvent, List[TriggerRegistration]]:
        """Fire an event through the bus.

        1. Apply replacement effects (CR 614).
        2. Collect matching triggered abilities (CR 603).
        3. Return the (possibly modified) event and triggered abilities.

        Args:
            event: The game event to fire.
            game_state: The current game state (for condition evaluation).

        Returns:
            Tuple of (modified_event, list_of_triggered_abilities).
        """
        # Step 1: Apply replacement effects
        event = self._apply_replacements(event, game_state)

        if event.prevented:
            return event, []

        # Step 2: Log the event
        self._log_event(event)

        # Step 3: Collect triggered abilities
        triggered = self._collect_triggers(event, game_state)

        return event, triggered

    def _apply_replacements(
        self, event: GameEvent, game_state: "GameState"
    ) -> GameEvent:
        """Apply all applicable replacement effects to an event (CR 614.1).

        If multiple replacement effects could apply, the affected player
        or controller chooses the order (CR 616.1). For simplicity in
        simulation, we apply them in registration order.
        """
        replacements = self._replacements.get(event.event_type, [])
        for reg in replacements:
            if not reg.is_valid():
                continue
            try:
                if reg.condition(event, reg.card, game_state):
                    event = reg.apply_replacement(event, reg.card, game_state)
                    if event.prevented:
                        break
            except Exception:
                # Replacement effect failed; skip it
                continue
        return event

    def _collect_triggers(
        self, event: GameEvent, game_state: "GameState"
    ) -> List[TriggerRegistration]:
        """Collect all triggered abilities that match this event (CR 603.2)."""
        triggers = self._triggers.get(event.event_type, [])
        matched = []
        for reg in triggers:
            if not reg.is_valid():
                continue
            try:
                if reg.condition(event, reg.card, game_state):
                    matched.append(reg)
            except Exception:
                # Trigger condition failed; skip it
                continue
        return matched

    def _log_event(self, event: GameEvent):
        """Keep a bounded log of recent events."""
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size // 2:]

    # ── Cleanup ─────────────────────────────────────────────────────

    def cleanup_invalid(self):
        """Remove registrations for cards no longer in the required zone."""
        for event_type in list(self._triggers.keys()):
            self._triggers[event_type] = [
                r for r in self._triggers[event_type] if r.is_valid()
            ]
        for event_type in list(self._replacements.keys()):
            self._replacements[event_type] = [
                r for r in self._replacements[event_type] if r.is_valid()
            ]

    def clear(self):
        """Clear all registrations and event log. Called at game start."""
        self._triggers.clear()
        self._replacements.clear()
        self._event_log.clear()

    # ── Query ───────────────────────────────────────────────────────

    def get_recent_events(
        self, event_type: Optional[EventType] = None, count: int = 10
    ) -> List[GameEvent]:
        """Get recent events, optionally filtered by type."""
        if event_type is None:
            return self._event_log[-count:]
        return [e for e in self._event_log if e.event_type == event_type][-count:]

    def count_events_this_turn(
        self, event_type: EventType, player: Optional[int] = None,
        turn_number: Optional[int] = None
    ) -> int:
        """Count events of a given type this turn."""
        count = 0
        for event in reversed(self._event_log):
            if event.extra.get("turn") != turn_number:
                break
            if event.event_type == event_type:
                if player is None or event.player == player:
                    count += 1
        return count

    @property
    def trigger_count(self) -> int:
        return sum(len(v) for v in self._triggers.values())

    @property
    def replacement_count(self) -> int:
        return sum(len(v) for v in self._replacements.values())

    def __repr__(self) -> str:
        return (f"EventBus(triggers={self.trigger_count}, "
                f"replacements={self.replacement_count}, "
                f"log_size={len(self._event_log)})")
