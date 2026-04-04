"""
MTG Priority System
Implements Comprehensive Rules 117 (Timing and Priority).

CR 117.1: Unless a spell or ability is allowing a player to take an action,
which player can take actions at any given time is determined by a system of
priority.

CR 117.3a: The active player receives priority at the beginning of most
steps and phases, after the turn-based action has been performed.

CR 117.3b: The active player receives priority after a spell or ability
on the stack resolves.

CR 117.3c: If a player has priority when they cast a spell, activate an
ability, or take a special action, that player receives priority afterward.

CR 117.3d: If a player has priority and chooses not to take any actions,
that player passes priority. If any mana is in that player's mana pool,
they announce what mana is there. Then the next player in turn order
receives priority.

CR 117.4: If all players pass in succession (that is, if all players pass
without taking any actions in between passing), the spell or ability on top
of the stack resolves or, if the stack is empty, the phase or step ends.
"""
from __future__ import annotations
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .game_state import GameState
    from .turn_manager import TurnStep


class PrioritySystem:
    """Manages priority passing between players.

    In a 2-player game, the priority loop is:
    1. Active player gets priority
    2. Active player either acts (gets priority again) or passes
    3. Non-active player gets priority
    4. Non-active player either acts (active player gets priority again) or passes
    5. Both passed in succession → resolve top of stack or end step

    For simulation purposes, we simplify this to:
    - Active player takes all sorcery-speed actions during main phases
    - After each spell cast, opponent gets a response window
    - At each priority window, both players can cast instants
    """

    def __init__(self):
        self.priority_holder: Optional[int] = None
        self._consecutive_passes: int = 0

    def give_priority(self, game: "GameState", player_idx: int):
        """Give priority to a specific player."""
        self.priority_holder = player_idx
        self._consecutive_passes = 0

    def pass_priority(self, game: "GameState") -> bool:
        """Current priority holder passes.

        Returns True if both players have passed in succession
        (meaning the stack should resolve or the step should end).
        """
        self._consecutive_passes += 1

        if self._consecutive_passes >= 2:
            # Both players passed — resolve or end step
            self.priority_holder = None
            return True

        # Pass to the other player
        self.priority_holder = 1 - self.priority_holder
        return False

    def take_action(self, game: "GameState"):
        """Player took an action — they keep priority (CR 117.3c).

        After a player casts a spell or activates an ability,
        they receive priority again. The consecutive pass counter resets.
        """
        self._consecutive_passes = 0

    def reset(self):
        """Reset priority state for a new step."""
        self.priority_holder = None
        self._consecutive_passes = 0

    def both_passed(self) -> bool:
        """Check if both players have passed in succession."""
        return self._consecutive_passes >= 2

    def resolve_priority_round(
        self,
        game: "GameState",
        active_player: int,
        active_action_fn=None,
        opponent_action_fn=None,
    ) -> bool:
        """Run a complete priority round.

        This is the simplified simulation version:
        1. Active player decides action (or passes)
        2. If active player acted, opponent gets response window
        3. Repeat until both pass

        Args:
            game: Current game state.
            active_player: Index of the active player.
            active_action_fn: Callable(game) -> bool. Returns True if action taken.
            opponent_action_fn: Callable(game) -> bool. Returns True if action taken.

        Returns:
            True if any actions were taken during this round.
        """
        any_actions = False
        opponent = 1 - active_player

        self.give_priority(game, active_player)

        # Active player acts
        if active_action_fn and active_action_fn(game):
            any_actions = True
            self.take_action(game)

            # Opponent response
            if opponent_action_fn and not game.game_over:
                if opponent_action_fn(game):
                    any_actions = True
                    self.take_action(game)
        else:
            self.pass_priority(game)

        return any_actions
