"""
Spell resolution — extracted from GameState (Phase 4A).

Contains the core spell casting, stack resolution, and spell effect
execution logic. These are mixed into GameState via SpellResolutionMixin.

The mixin pattern is used because these methods are tightly coupled to
GameState's internal state (players, stack, log, etc.). Composition
would require passing too many references.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance


class SpellResolutionMixin:
    """Mixin providing spell casting and resolution methods for GameState.

    Methods extracted here:
    - cast_spell() — pay costs, put on stack, resolve
    - resolve_stack() — process the stack until empty
    - _handle_storm() — create storm copies
    - _handle_cascade() — cascade into cheaper spells
    - _execute_spell_effects() — dispatch to EFFECT_REGISTRY + generic fallback

    These methods are defined in game_state.py and will be migrated here
    incrementally. This file serves as the target module.
    """
    pass  # Methods will be migrated from game_state.py incrementally
