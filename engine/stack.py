"""
MTG Stack System — the stack as a pure data container.

`StackItem` / `StackItemType` describe what sits on the stack; `Stack`
is a plain LIFO. Resolution lives in ResolutionManager
(engine/spell_resolution.py), priority in engine/priority_system.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING, Callable
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
    # Spliced card templates (splice onto Arcane)
    spliced: List = field(default_factory=list)
    # Colors of mana actually spent to cast this spell, per the Converge
    # rule ("number of colors of mana spent to cast this spell"). Populated
    # by cast_spell() from lands_to_tap + drained mana-pool colors.
    # Empty set for free casts or when payment tracking isn't available.
    colors_spent: Set[str] = field(default_factory=set)
    # CR 608.2b support: zone each card-target occupied when it was
    # chosen at cast time (instance_id → zone name). Populated by
    # CastManager.cast_spell; consumed by ResolutionManager's
    # target-legality re-check on resolution — a target that has left
    # its cast-time zone is illegal, and a spell whose targets are ALL
    # illegal fizzles. Player-target markers (negative ids) have no
    # zone and are never snapshotted.
    target_zones: Dict[int, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        if self.item_type == StackItemType.SPELL:
            return self.source.name
        return f"{self.source.name} ability"


class Stack:
    """Pure LIFO container for spells and abilities on the stack.

    This class holds state only. Resolution does NOT live here:
    `ResolutionManager.resolve_stack` (engine/spell_resolution.py) owns
    popping and resolving items, including the CR 608.2b target-legality
    re-check (a spell whose targets are ALL illegal at resolution
    fizzles). Priority passing lives in engine/priority_system.py.

    The legacy resolver/priority machinery that used to sit on this
    class (resolve_top / _resolve_spell / _resolve_ability, peek, size,
    pass_priority, both_passed, reset_priority, priority_player,
    switch_priority, __len__, __str__) was a dead parallel
    implementation with zero callers; it was deleted after the one rule
    trapped inside it (CR 608.2b) was ported to the live resolver. See
    docs/proposals/resolver_sba_unification.md.
    """

    def __init__(self):
        self.items: List[StackItem] = []

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0

    @property
    def top(self) -> Optional[StackItem]:
        return self.items[-1] if self.items else None

    def push(self, item: StackItem):
        """Add an item to the top of the stack."""
        self.items.append(item)

    def pop(self) -> Optional[StackItem]:
        """Remove and return the top item from the stack."""
        if self.items:
            return self.items.pop()
        return None
