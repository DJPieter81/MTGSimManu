"""
MTG Stack System
Implements the stack for spell and ability resolution with proper priority passing.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING, Callable
from enum import Enum

if TYPE_CHECKING:
    from .cards import CardInstance, Ability
    from .game_state import GameState


class StackItemType(Enum):
    SPELL = "spell"
    ACTIVATED_ABILITY = "activated_ability"
    TRIGGERED_ABILITY = "triggered_ability"


@dataclass
class StackItem:
    """An item on the stack (spell or ability)."""
    item_type: StackItemType
    source: CardInstance  # the card that created this stack item
    controller: int  # player index
    targets: List[int] = field(default_factory=list)  # instance_ids of targets
    effect: Optional[Callable] = None  # for abilities
    description: str = ""
    # For spells, the CardInstance itself is the source
    # For abilities, the source is the permanent that has the ability
    ability: Optional[Ability] = None
    # Modes chosen (for modal spells)
    modes_chosen: List[int] = field(default_factory=list)
    # X value for X spells
    x_value: int = 0
    # Whether this was evoked
    evoked: bool = False

    @property
    def name(self) -> str:
        if self.item_type == StackItemType.SPELL:
            return self.source.name
        return f"{self.source.name} ability"


class Stack:
    """The game stack - LIFO structure for spell/ability resolution."""

    def __init__(self):
        self.items: List[StackItem] = []
        self._priority_player: int = 0  # who has priority
        self._passed_priority: List[bool] = [False, False]

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0

    @property
    def top(self) -> Optional[StackItem]:
        return self.items[-1] if self.items else None

    def push(self, item: StackItem):
        """Add an item to the top of the stack."""
        self.items.append(item)
        # When something is put on the stack, priority resets
        self._passed_priority = [False, False]
        # Active player gets priority (or the controller of the item)
        self._priority_player = item.controller

    def pop(self) -> Optional[StackItem]:
        """Remove and return the top item from the stack."""
        if self.items:
            return self.items.pop()
        return None

    def peek(self, index: int = -1) -> Optional[StackItem]:
        """Look at a stack item without removing it."""
        try:
            return self.items[index]
        except IndexError:
            return None

    def size(self) -> int:
        return len(self.items)

    def pass_priority(self, player_idx: int):
        """A player passes priority."""
        self._passed_priority[player_idx] = True

    def both_passed(self) -> bool:
        """Check if both players have passed priority in succession."""
        return all(self._passed_priority)

    def reset_priority(self):
        """Reset priority passing (called when something is added to stack)."""
        self._passed_priority = [False, False]

    @property
    def priority_player(self) -> int:
        return self._priority_player

    @priority_player.setter
    def priority_player(self, value: int):
        self._priority_player = value

    def switch_priority(self):
        """Switch priority to the other player."""
        self._priority_player = 1 - self._priority_player

    def resolve_top(self, game_state: "GameState") -> Optional[StackItem]:
        """
        Resolve the top item on the stack.
        Both players must have passed priority for this to happen.
        Returns the resolved item.
        """
        if self.is_empty:
            return None

        item = self.pop()

        if item.item_type == StackItemType.SPELL:
            self._resolve_spell(game_state, item)
        elif item.item_type in (StackItemType.ACTIVATED_ABILITY,
                                 StackItemType.TRIGGERED_ABILITY):
            self._resolve_ability(game_state, item)

        # Reset priority after resolution
        self.reset_priority()
        return item

    def _resolve_spell(self, game_state: "GameState", item: StackItem):
        """Resolve a spell from the stack."""
        card = item.source
        template = card.template

        # Check if targets are still valid
        if item.targets:
            valid_targets = []
            for tid in item.targets:
                target = game_state.get_card_by_id(tid)
                if target and target.zone == "battlefield":
                    valid_targets.append(tid)
            if not valid_targets and item.targets:
                # Spell fizzles - all targets invalid
                card.zone = "graveyard"
                game_state.players[card.owner].graveyard.append(card)
                return

        # Execute spell effect
        if card.template.abilities:
            for ability in card.template.abilities:
                if ability.effect:
                    ability.effect(game_state, card, item.controller, item.targets)

        # Determine where the card goes after resolution
        from .cards import CardType
        if CardType.INSTANT in template.card_types or CardType.SORCERY in template.card_types:
            card.zone = "graveyard"
            game_state.players[card.owner].graveyard.append(card)
        elif CardType.CREATURE in template.card_types or \
             CardType.ENCHANTMENT in template.card_types or \
             CardType.ARTIFACT in template.card_types or \
             CardType.PLANESWALKER in template.card_types:
            # Permanent - enters the battlefield
            card.controller = item.controller
            # Apply X-cost counters before entering
            if item.x_value > 0:
                from .game_state import X_COST_SPELLS
                x_info = X_COST_SPELLS.get(card.name, {})
                effect = x_info.get("effect", "")
                if effect == "plus1_counters":
                    card.plus_counters += item.x_value
                    game_state.log.append(
                        f"T{game_state.turn_number} P{item.controller+1}: "
                        f"{card.name} enters with {item.x_value} +1/+1 counters "
                        f"({card.power}/{card.toughness})")
                elif effect == "charge_counters":
                    card.other_counters["charge"] = item.x_value
                    game_state.log.append(
                        f"T{game_state.turn_number} P{item.controller+1}: "
                        f"{card.name} enters with {item.x_value} charge counters")
            card.enter_battlefield()
            game_state.players[item.controller].battlefield.append(card)
            # Trigger ETB abilities
            game_state.trigger_etb(card, item.controller)

    def _resolve_ability(self, game_state: "GameState", item: StackItem):
        """Resolve an activated or triggered ability."""
        if item.effect:
            item.effect(game_state, item.source, item.controller, item.targets)
        elif item.ability and item.ability.effect:
            item.ability.effect(game_state, item.source, item.controller, item.targets)

    def __len__(self):
        return len(self.items)

    def __str__(self):
        if not self.items:
            return "Stack: [empty]"
        lines = ["Stack (top to bottom):"]
        for i, item in enumerate(reversed(self.items)):
            lines.append(f"  {i+1}. {item.name} ({item.item_type.value}) - controller: P{item.controller+1}")
        return "\n".join(lines)
