"""
MTG Turn Manager
Encapsulates the turn structure per Comprehensive Rules 500-514.

Provides a clean interface for the game runner to advance through
turn phases, with proper hooks for priority windows at each step.

CR 500.1: A turn consists of these phases, in order:
  beginning phase (untap, upkeep, draw),
  first main phase,
  combat phase (beginning of combat, declare attackers,
    declare blockers, combat damage, end of combat),
  second main phase,
  ending phase (end step, cleanup step).
"""
from __future__ import annotations
from typing import Callable, List, Optional, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .game_state import GameState, Phase


class TurnStep(Enum):
    """Fine-grained turn steps for priority tracking."""
    UNTAP = "untap"
    UPKEEP = "upkeep"
    DRAW = "draw"
    MAIN1 = "main1"
    BEGIN_COMBAT = "begin_combat"
    DECLARE_ATTACKERS = "declare_attackers"
    AFTER_ATTACKERS_DECLARED = "after_attackers_declared"
    DECLARE_BLOCKERS = "declare_blockers"
    AFTER_BLOCKERS_DECLARED = "after_blockers_declared"
    FIRST_STRIKE_DAMAGE = "first_strike_damage"
    COMBAT_DAMAGE = "combat_damage"
    END_COMBAT = "end_combat"
    MAIN2 = "main2"
    END_STEP = "end_step"
    CLEANUP = "cleanup"


# Steps where the active player receives priority (CR 117.3a)
PRIORITY_STEPS = {
    TurnStep.UPKEEP,
    TurnStep.DRAW,           # after draw action
    TurnStep.MAIN1,
    TurnStep.BEGIN_COMBAT,
    TurnStep.AFTER_ATTACKERS_DECLARED,
    TurnStep.AFTER_BLOCKERS_DECLARED,
    TurnStep.FIRST_STRIKE_DAMAGE,
    TurnStep.COMBAT_DAMAGE,
    TurnStep.END_COMBAT,
    TurnStep.MAIN2,
    TurnStep.END_STEP,
}

# Steps where no player gets priority (CR 500.2)
NO_PRIORITY_STEPS = {
    TurnStep.UNTAP,
    TurnStep.CLEANUP,  # unless a trigger fires
}


class TurnManager:
    """Manages the turn structure and tracks phase progression.

    This module does NOT execute game actions itself — it provides
    the sequencing framework and tells the caller what step we're in
    and whether priority should be given.

    Usage:
        tm = TurnManager()
        for step in tm.iterate_turn(game):
            if step == TurnStep.UNTAP:
                game.untap_step(active)
            elif step == TurnStep.UPKEEP:
                # handle upkeep triggers, give priority
                ...
    """

    def __init__(self):
        self.current_step: Optional[TurnStep] = None
        self.turn_number: int = 0
        self.active_player: int = 0
        self.first_player: int = 0  # who went first (skip draw T1)
        self._skip_combat: bool = False

    def iterate_turn(self, game: "GameState"):
        """Generator that yields each step of a turn in order.

        The caller handles each step and can check game_over between steps.
        This replaces the monolithic while-loop in game_runner.py.
        """
        self.active_player = game.active_player
        self.turn_number = game.turn_number

        # Beginning Phase
        yield TurnStep.UNTAP
        yield TurnStep.UPKEEP
        yield TurnStep.DRAW

        # First Main Phase
        yield TurnStep.MAIN1

        # Combat Phase
        yield TurnStep.BEGIN_COMBAT
        yield TurnStep.DECLARE_ATTACKERS
        # AFTER_ATTACKERS_DECLARED only fires if there are attackers
        # (the caller decides whether to yield based on game state)
        yield TurnStep.AFTER_ATTACKERS_DECLARED
        yield TurnStep.DECLARE_BLOCKERS
        yield TurnStep.AFTER_BLOCKERS_DECLARED
        yield TurnStep.FIRST_STRIKE_DAMAGE
        yield TurnStep.COMBAT_DAMAGE
        yield TurnStep.END_COMBAT

        # Second Main Phase
        yield TurnStep.MAIN2

        # Ending Phase
        yield TurnStep.END_STEP
        yield TurnStep.CLEANUP

    @staticmethod
    def has_priority(step: TurnStep) -> bool:
        """Check if players receive priority during this step."""
        return step in PRIORITY_STEPS

    @staticmethod
    def is_main_phase(step: TurnStep) -> bool:
        """Check if this is a main phase (sorcery-speed actions allowed)."""
        return step in (TurnStep.MAIN1, TurnStep.MAIN2)

    @staticmethod
    def is_combat_step(step: TurnStep) -> bool:
        """Check if this is a combat step."""
        return step in (
            TurnStep.BEGIN_COMBAT,
            TurnStep.DECLARE_ATTACKERS,
            TurnStep.AFTER_ATTACKERS_DECLARED,
            TurnStep.DECLARE_BLOCKERS,
            TurnStep.AFTER_BLOCKERS_DECLARED,
            TurnStep.FIRST_STRIKE_DAMAGE,
            TurnStep.COMBAT_DAMAGE,
            TurnStep.END_COMBAT,
        )

    def should_skip_draw(self, game: "GameState") -> bool:
        """CR 103.7a: The player who goes first skips the draw step of their first turn."""
        return (game.turn_number == 1
                and game.active_player == self.first_player)
