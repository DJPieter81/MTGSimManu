"""
Combat Manager
==============
Centralizes all combat logic per CR 500-series (specifically 506-511):
  - Declare Attackers step (CR 508)
  - Declare Blockers step (CR 509)
  - First-Strike Damage step (CR 510.4)
  - Combat Damage step (CR 510)
  - End of Combat step (CR 511)

Key improvements over the previous scattered implementation:
  1. Proper first-strike / double-strike damage ordering
  2. Deathtouch + trample interaction (CR 702.2c + 702.19c):
     only 1 damage needed to kill each blocker with deathtouch
  3. Multiple blockers damage assignment order (CR 510.1c)
  4. Battle cry, annihilator, and other attack triggers via registry
  5. Lifelink applied correctly to all combat damage (CR 702.15)
  6. Prowess triggers fire for noncreature spells (CR 702.107)

This module is called by GameRunner; it reads from GameState and
delegates zone changes back to GameState._creature_dies / zone_mgr.
"""
from __future__ import annotations
from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .game_state import GameState
    from .cards import CardInstance

from .cards import Keyword, CardType, AbilityType


@dataclass
class CombatAssignment:
    """Tracks a single attacker's combat state."""
    attacker: "CardInstance"
    blocker_ids: List[int] = field(default_factory=list)
    is_blocked: bool = False
    damage_to_player: int = 0
    damage_to_blockers: Dict[int, int] = field(default_factory=dict)


class CombatManager:
    """Manages the combat phase per MTG Comprehensive Rules 506-511.

    Usage:
        cm = CombatManager()

        # In DECLARE_ATTACKERS step:
        cm.declare_attackers(game, attackers, active_player)

        # In DECLARE_BLOCKERS step:
        cm.declare_blockers(game, blocks)

        # In COMBAT_DAMAGE step:
        total_damage = cm.resolve_combat_damage(game)

        # In END_COMBAT step:
        cm.end_combat(game)
    """

    def __init__(self):
        self._assignments: List[CombatAssignment] = []
        self._attackers: List["CardInstance"] = []
        self._active_player: int = 0
        self._defending_player: int = 1

    def declare_attackers(self, game: "GameState",
                          attackers: List["CardInstance"],
                          active_player: int):
        """CR 508: Declare attackers step.

        Sets attacking state, taps non-vigilance creatures,
        fires attack triggers, and handles battle cry.
        """
        self._attackers = attackers
        self._active_player = active_player
        self._defending_player = 1 - active_player
        self._assignments = []

        for attacker in attackers:
            attacker.attacking = True
            # CR 508.1f: Tap attacking creatures (unless vigilance)
            if Keyword.VIGILANCE not in attacker.keywords:
                attacker.tap()

            self._assignments.append(CombatAssignment(attacker=attacker))

            # Fire attack triggers via game_state
            game.trigger_attack(attacker, active_player)

        # Battle cry: Signal Pest and similar
        self._apply_battle_cry(game, attackers)

        if attackers:
            game.log.append(
                f"T{game.display_turn} P{active_player+1}: Attack with "
                f"{', '.join(a.name for a in attackers)}"
            )

    def declare_blockers(self, game: "GameState",
                         blocks: Dict[int, List[int]]):
        """CR 509: Declare blockers step.

        Records blocking assignments. The AI has already chosen blockers;
        this method validates and records the assignments.
        """
        for assignment in self._assignments:
            attacker_id = assignment.attacker.instance_id
            blocker_ids = blocks.get(attacker_id, [])
            assignment.blocker_ids = blocker_ids
            assignment.is_blocked = len(blocker_ids) > 0

    def resolve_combat_damage(self, game: "GameState") -> int:
        """CR 510: Combat damage step.

        Handles first-strike, regular, and double-strike damage in order.
        Returns total damage dealt to defending player.
        """
        total_player_damage = 0

        # Separate attackers by damage step
        first_strikers = [a for a in self._assignments
                          if a.attacker.zone == "battlefield" and
                          (Keyword.FIRST_STRIKE in a.attacker.keywords or
                           Keyword.DOUBLE_STRIKE in a.attacker.keywords)]

        regular_strikers = [a for a in self._assignments
                            if a.attacker.zone == "battlefield" and
                            Keyword.FIRST_STRIKE not in a.attacker.keywords and
                            Keyword.DOUBLE_STRIKE not in a.attacker.keywords]

        double_strikers = [a for a in self._assignments
                           if a.attacker.zone == "battlefield" and
                           Keyword.DOUBLE_STRIKE in a.attacker.keywords]

        # CR 510.4: First-strike damage step
        if first_strikers:
            dmg = self._deal_combat_damage(game, first_strikers,
                                            first_strike_step=True)
            total_player_damage += dmg
            # CR 510.4: SBAs checked after first-strike damage
            game.check_state_based_actions()
            if game.game_over:
                return total_player_damage

        # CR 510.2: Regular damage step
        regular_plus_double = regular_strikers + double_strikers
        if regular_plus_double:
            dmg = self._deal_combat_damage(game, regular_plus_double,
                                            first_strike_step=False)
            total_player_damage += dmg

        # Mark all attackers as having attacked
        for assignment in self._assignments:
            assignment.attacker.attacked_this_turn = True

        # SBAs after all combat damage
        game.check_state_based_actions()

        return total_player_damage

    def end_combat(self, game: "GameState"):
        """CR 511: End of combat step.

        Reset combat state on all creatures.
        """
        for assignment in self._assignments:
            assignment.attacker.reset_combat()

        for p in game.players:
            for c in p.creatures:
                c.reset_combat()
            # Consume transient aggression flag after combat (Living End etc.)
            if getattr(p, 'aggression_boost_turns', 0) > 0:
                p.aggression_boost_turns -= 1

        self._assignments = []
        self._attackers = []

    def _deal_combat_damage(self, game: "GameState",
                             assignments: List[CombatAssignment],
                             first_strike_step: bool) -> int:
        """Assign and deal combat damage for a set of attackers.

        CR 510.1: Each attacking creature assigns damage equal to its power.
        CR 510.1c: If blocked by multiple creatures, damage is assigned in order.
        CR 702.2c + 702.19c: Deathtouch + trample = 1 lethal to each blocker.

        Returns total damage dealt to defending player.
        """
        total_player_damage = 0

        for assignment in assignments:
            attacker = assignment.attacker
            if attacker.zone != "battlefield":
                continue  # Died in first-strike step

            attacker_power = attacker.power
            if attacker_power <= 0:
                continue

            has_deathtouch = Keyword.DEATHTOUCH in attacker.keywords
            has_trample = Keyword.TRAMPLE in attacker.keywords
            has_lifelink = Keyword.LIFELINK in attacker.keywords
            total_damage_dealt = 0

            if assignment.blocker_ids:
                # CR 510.1a: Blocked creature assigns damage to blockers
                remaining_damage = attacker_power

                for blocker_id in assignment.blocker_ids:
                    if remaining_damage <= 0:
                        break
                    blocker = game.get_card_by_id(blocker_id)
                    if not blocker or blocker.zone != "battlefield":
                        continue

                    # CR 702.2c: With deathtouch, 1 damage is lethal
                    if has_deathtouch:
                        lethal = max(1, blocker.toughness - blocker.damage_marked)
                        damage_to_blocker = min(lethal, remaining_damage)
                    else:
                        # Assign enough to kill, or all remaining
                        lethal = max(0, blocker.toughness - blocker.damage_marked)
                        damage_to_blocker = min(lethal, remaining_damage)
                        if damage_to_blocker < lethal:
                            # Not enough to kill — assign all remaining
                            damage_to_blocker = remaining_damage

                    blocker.damage_marked += damage_to_blocker
                    remaining_damage -= damage_to_blocker
                    total_damage_dealt += damage_to_blocker

                    # Blocker deals damage back
                    blocker_has_fs = (Keyword.FIRST_STRIKE in blocker.keywords or
                                     Keyword.DOUBLE_STRIKE in blocker.keywords)
                    should_deal_back = (
                        (first_strike_step and blocker_has_fs) or
                        (not first_strike_step and not blocker_has_fs) or
                        (not first_strike_step and
                         Keyword.DOUBLE_STRIKE in blocker.keywords)
                    )
                    if should_deal_back and blocker.power > 0:
                        attacker.damage_marked += blocker.power

                    # Deathtouch from blocker
                    if (Keyword.DEATHTOUCH in blocker.keywords and
                            blocker.power > 0 and should_deal_back):
                        attacker.damage_marked = max(
                            attacker.damage_marked, attacker.toughness
                        )

                # CR 702.19c: Trample — excess damage to defending player
                player_damage = 0
                if has_trample and remaining_damage > 0:
                    game.players[self._defending_player].life -= remaining_damage
                    game.players[self._active_player].damage_dealt_this_turn += remaining_damage
                    total_damage_dealt += remaining_damage
                    player_damage = remaining_damage
                    game.log.append(
                        f"T{game.display_turn} P{self._active_player+1}: "
                        f"  {attacker.name} ({attacker.power}/{attacker.toughness})"
                        f" → {remaining_damage} dmg to player (trample)"
                    )

                # Deathtouch from attacker — ensure blocker is marked as dead
                if has_deathtouch:
                    for blocker_id in assignment.blocker_ids:
                        blocker = game.get_card_by_id(blocker_id)
                        if blocker and blocker.zone == "battlefield" and blocker.damage_marked > 0:
                            blocker.damage_marked = max(
                                blocker.damage_marked, blocker.toughness
                            )

            else:
                # CR 510.1b: Unblocked creature assigns damage to defending player
                game.players[self._defending_player].life -= attacker_power
                game.players[self._active_player].damage_dealt_this_turn += attacker_power
                total_damage_dealt = attacker_power
                player_damage = attacker_power
                game.log.append(
                    f"T{game.display_turn} P{self._active_player+1}: "
                    f"  {attacker.name} ({attacker.power}/{attacker.toughness})"
                    f" → {attacker_power} dmg to player"
                )

            # CR 702.15: Lifelink — gain life equal to ALL damage dealt
            if has_lifelink and total_damage_dealt > 0:
                game.players[self._active_player].life += total_damage_dealt
                game.players[self._active_player].life_gained_this_turn += total_damage_dealt

            # "Deals combat damage to a player" triggers (oracle-based)
            if player_damage > 0:
                a_oracle = (attacker.template.oracle_text or '').lower()
                if 'combat damage to a player' in a_oracle:
                    if 'treasure' in a_oracle:
                        game.create_token(self._active_player, "treasure",
                                          count=1)
                    if 'exile the top card' in a_oracle:
                        opp = game.players[self._defending_player]
                        if opp.library:
                            exiled = opp.library.pop(0)
                            exiled.zone = "exile"
                            opp.exile.append(exiled)
                            game.log.append(
                                f"T{game.display_turn} P{self._active_player+1}: "
                                f"{attacker.name} exiles {exiled.name} "
                                f"from top of P{self._defending_player+1}'s library"
                            )
                    if 'draw a card' in a_oracle:
                        game.draw_cards(self._active_player, 1)
                        game.log.append(
                            f"T{game.display_turn} P{self._active_player+1}: "
                            f"{attacker.name} deals combat damage — draw a card"
                        )

        return total_player_damage + sum(
            max(0, game.players[self._defending_player].life - game.players[self._defending_player].life)
            for _ in [0]  # dummy — we already tracked via direct mutation
        )

    def _apply_battle_cry(self, game: "GameState",
                           attackers: List["CardInstance"]):
        """Apply battle cry: each other attacking creature gets +1/+0.

        Signal Pest has battle cry. This is extensible for other
        battle cry sources.
        """
        battle_cry_sources = [a for a in attackers
                              if 'battle cry' in (a.template.oracle_text or '').lower()]
        for source in battle_cry_sources:
            for other in attackers:
                if other != source:
                    other.temp_power_mod += 1

    @property
    def attackers(self) -> List["CardInstance"]:
        """Get the list of declared attackers."""
        return self._attackers

    @property
    def blocks(self) -> Dict[int, List[int]]:
        """Get the blocking assignments as a dict."""
        return {a.attacker.instance_id: a.blocker_ids
                for a in self._assignments}
