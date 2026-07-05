"""
MTG State-Based Actions Manager
Implements Comprehensive Rules 704.

State-based actions are checked as a batch whenever a player would
receive priority. If any SBAs are performed, the entire batch is
checked again. This repeats until no SBAs are performed (CR 704.3).

Key SBAs implemented:
  704.5a  - Player at 0 or less life loses
  704.5b  - Player who drew from empty library loses
  704.5c  - Player with 10+ poison counters loses
  704.5f  - Token not on battlefield ceases to exist
  704.5g  - Creature with toughness 0 or less goes to graveyard
  704.5h  - Creature with lethal damage marked is destroyed
  704.5i  - Creature dealt damage by deathtouch source is destroyed
  704.5j  - Legend rule (two legendaries with same name under one player)
  704.5p  - Planeswalker with 0 loyalty goes to graveyard
"""
from __future__ import annotations
from typing import List, Optional, TYPE_CHECKING

from .constants import POISON_COUNTER_LETHAL, SBA_MAX_ITERATIONS

if TYPE_CHECKING:
    from .cards import CardInstance, CardType, Keyword, Supertype
    from .game_state import GameState
    from .zone_manager import ZoneManager


class SBAManager:
    """Manages state-based actions per Comprehensive Rules 704."""

    def __init__(self, zone_manager: "ZoneManager"):
        self.zone_manager = zone_manager

    def check_and_perform_loop(self, game: "GameState") -> bool:
        """Run the full SBA loop: check and perform until stable (CR 704.3).

        Returns True if any SBAs were performed during the entire loop.
        """
        any_performed = False
        iteration = 0
        max_iterations = SBA_MAX_ITERATIONS  # Safety valve (CR 704.3)

        while iteration < max_iterations:
            performed = self._check_and_perform_once(game)
            if performed:
                any_performed = True
                iteration += 1
            else:
                break

        return any_performed

    def _check_and_perform_once(self, game: "GameState") -> bool:
        """Check all SBAs once. Return True if any were performed."""
        from .cards import CardType, Keyword, Supertype

        performed = False

        # 704.5a: Player at 0 or less life loses the game
        for p in game.players:
            if p.life <= 0 and not game.game_over:
                game.game_over = True
                game.winner = 1 - p.player_idx
                game.log.append(
                    f"T{game.display_turn}: P{p.player_idx+1} loses "
                    f"(life={p.life}, SBA 704.5a)"
                )
                performed = True

        if game.game_over:
            return performed

        # 704.5b: Player who attempted to draw from empty library loses
        for p in game.players:
            if getattr(p, '_drew_from_empty', False) and not game.game_over:
                game.game_over = True
                game.winner = 1 - p.player_idx
                game.log.append(
                    f"T{game.display_turn}: P{p.player_idx+1} loses "
                    f"(drew from empty library, SBA 704.5b)"
                )
                p._drew_from_empty = False
                performed = True

        if game.game_over:
            return performed

        # 704.5c: Player with lethal poison loses — single
        # implementation lives in perform_poison_check (also called by
        # the live SBA path).
        if SBAManager.perform_poison_check(game):
            performed = True

        if game.game_over:
            return performed

        # 704.5f: Tokens not on the battlefield cease to exist
        for p in game.players:
            for zone_name in ["hand", "graveyard", "exile", "library"]:
                zone_list = getattr(p, zone_name)
                tokens = [c for c in zone_list if getattr(c, 'is_token', False)]
                for t in tokens:
                    zone_list.remove(t)
                    performed = True

        # 704.5g: Creature with toughness 0 or less is put into graveyard
        for p in game.players:
            for c in list(p.battlefield):
                if c.template.is_creature and c.toughness <= 0:
                    self.zone_manager.move_card(
                        game, c, "battlefield", "graveyard",
                        cause="SBA 704.5g: zero toughness"
                    )
                    p.creatures_died_this_turn += 1
                    performed = True

        # 704.5h: Creature with lethal damage is destroyed
        #         (damage >= toughness, and not indestructible)
        for p in game.players:
            for c in list(p.battlefield):
                if (c.template.is_creature
                        and c.damage_marked >= c.toughness
                        and c.toughness > 0):
                    if Keyword.INDESTRUCTIBLE not in c.keywords:
                        self.zone_manager.move_card(
                            game, c, "battlefield", "graveyard",
                            cause="SBA 704.5h: lethal damage"
                        )
                        p.creatures_died_this_turn += 1
                        performed = True

        # 704.5i: Creature dealt damage by a deathtouch source is
        # destroyed — single implementation lives in
        # perform_deathtouch_check (also called by the live SBA path).
        if SBAManager.perform_deathtouch_check(game):
            performed = True

        # 704.5j: Legend rule — if a player controls two or more legendary
        #         permanents with the same name, they choose one to keep
        #         and put the rest into the graveyard.
        for p in game.players:
            legendaries_by_name = {}
            for c in list(p.battlefield):
                if Supertype.LEGENDARY in c.template.supertypes:
                    name = c.template.name
                    if name not in legendaries_by_name:
                        legendaries_by_name[name] = []
                    legendaries_by_name[name].append(c)

            for name, cards in legendaries_by_name.items():
                if len(cards) > 1:
                    # Keep the newest (highest instance_id), sacrifice the rest
                    cards.sort(key=lambda c: c.instance_id)
                    for old in cards[:-1]:
                        if old.zone == "battlefield":
                            self.zone_manager.move_card(
                                game, old, "battlefield", "graveyard",
                                cause=f"SBA 704.5j: legend rule ({name})"
                            )
                            performed = True

        # 704.5p: Planeswalker with 0 or less loyalty is put into graveyard
        for p in game.players:
            for c in list(p.battlefield):
                if (CardType.PLANESWALKER in c.template.card_types
                        and c.zone == "battlefield"):
                    if c.loyalty_counters <= 0:
                        self.zone_manager.move_card(
                            game, c, "battlefield", "graveyard",
                            cause="SBA 704.5p: zero loyalty"
                        )
                        performed = True

        return performed

    # ── Shared single-implementation SBAs ────────────────────────────
    # Statics called from BOTH this manager's _check_and_perform_once
    # and the live SBA path in game_state.check_state_based_actions.
    # Pattern established by perform_token_cleanup (PR #443); see
    # docs/proposals/resolver_sba_unification.md §6 for the end-state
    # (every 704.5x rule becomes one of these).

    @staticmethod
    def perform_deathtouch_check(game: "GameState") -> bool:
        """SBA 704.5i — a creature that's been dealt damage by a source
        with deathtouch since the last damage cleanup is destroyed,
        however small the damage.

        The marker `_deathtouch_damage` is written by
        engine/damage.py:deal_damage (deathtouch is a property of the
        SOURCE; the marker travels with the damage). Destruction routes
        through game._creature_dies so Undying/Persist replacement is
        preserved — never through a raw zone move. Indestructible
        creatures are exempt (704.5i is a destroy effect); their marker
        is still cleared so the check reaches a fixpoint.

        Returns True if any creature was destroyed.
        """
        from .cards import Keyword

        performed = False
        for p in game.players:
            for c in list(p.battlefield):
                if (c.template.is_creature
                        and getattr(c, '_deathtouch_damage', 0) > 0
                        and c.damage_marked > 0
                        and c.zone == "battlefield"):
                    if Keyword.INDESTRUCTIBLE not in c.keywords:
                        game.log.append(
                            f"T{game.display_turn}: {c.name} destroyed "
                            f"(deathtouch, SBA 704.5i)")
                        game._creature_dies(c)
                        performed = True
                    c._deathtouch_damage = 0
        return performed

    @staticmethod
    def perform_poison_check(game: "GameState") -> bool:
        """SBA 704.5c — a player with lethal poison counters loses.

        Threshold is the POISON_COUNTER_LETHAL rules constant
        (engine/constants.py). Returns True if a player lost.
        """
        performed = False
        for p in game.players:
            if (p.poison_counters >= POISON_COUNTER_LETHAL
                    and not game.game_over):
                game.game_over = True
                game.winner = 1 - p.player_idx
                game.log.append(
                    f"T{game.display_turn}: P{p.player_idx+1} loses "
                    f"(poison={p.poison_counters}, SBA 704.5c)"
                )
                performed = True
        return performed
