"""
Cycling — extracted from engine/game_state.py (Commit 6).

Owns the cycling mechanic:
- can_cycle: legality check for the cycling activated ability
  (oracle text parsing + mana/life availability).
- activate_cycling: pay cycling cost, exile the card, draw 1 (or
  execute the cycling-replacement effect for Lorien Revealed-style
  "search for an Island" variants).
- _cycling_tutor_search: library search for typecycling /
  landcycling variants.

Static methods; take game: GameState as first argument.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Dict, Optional

from .cards import CardInstance, CardType, Keyword

if TYPE_CHECKING:
    from .game_state import GameState


class CyclingManager:
    """Cycling activated ability + typecycling tutors. Stateless."""

    @staticmethod
    def can_cycle(game: "GameState", player_idx: int, card: "CardInstance") -> bool:
        """Check if a player can cycle a card from hand."""
        if card.zone != "hand":
            return False
        # Use oracle-derived cycling data from template
        cost = card.template.cycling_cost_data
        if cost is None:
            return False
        player = game.players[player_idx]
        # Life cost check
        if cost["life"] > 0 and player.life <= cost["life"]:
            return False
        # Mana cost check
        if cost["mana"] > 0:
            untapped = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()
            if untapped < cost["mana"]:
                return False
            # Color check for colored cycling costs.  Routes through
            # `_effective_produces_mana` so Leyline / dynamic mana
            # abilities (E1: Mox Opal metalcraft, CR 702.98) count as
            # valid sources for cycling colours.
            if cost["colors"]:
                has_color = False
                for land in player.untapped_lands:
                    if cost["colors"] & set(game._effective_produces_mana(player_idx, land)):
                        has_color = True
                        break
                if not has_color:
                    for color in cost["colors"]:
                        if player.mana_pool.get(color) > 0:
                            has_color = True
                            break
                if not has_color:
                    return False
        return True


    @staticmethod
    def activate_cycling(game: "GameState", player_idx: int, card: "CardInstance") -> bool:
        """Activate cycling: pay cost, discard card, draw a card.
        
        Cycling is a special action (not casting a spell). The card goes
        to the graveyard and the player draws a card. This does NOT count
        as casting a spell (no storm count, no prowess triggers).
        """
        if not game.can_cycle(player_idx, card):
            return False
        cost = card.template.cycling_cost_data or {"mana": 0, "life": 0, "colors": set()}
        player = game.players[player_idx]
        # Pay life cost
        if cost["life"] > 0:
            player.life -= cost["life"]
        # Pay mana cost
        if cost["mana"] > 0:
            if cost["colors"]:
                # Tap a land that produces the required color.  Routes
                # through `_effective_produces_mana` for Leyline /
                # dynamic mana abilities (E1: Mox Opal metalcraft,
                # CR 702.98).
                for color in cost["colors"]:
                    for land in player.untapped_lands:
                        if color in game._effective_produces_mana(player_idx, land):
                            land.tapped = True
                            break
                    break
                # Pay remaining generic mana
                remaining = cost["mana"] - 1  # 1 colored already paid
                for land in player.untapped_lands:
                    if remaining <= 0:
                        break
                    land.tapped = True
                    remaining -= 1
            else:
                # All generic mana
                remaining = cost["mana"]
                for land in player.untapped_lands:
                    if remaining <= 0:
                        break
                    land.tapped = True
                    remaining -= 1
        # Move card from hand to graveyard
        if card in player.hand:
            player.hand.remove(card)
        card.zone = "graveyard"
        player.graveyard.append(card)
        # Landcycling / typecycling tutors; plain cycling draws.
        variant = card.template.cycling_variant_data
        cost_desc = f"pay {cost['life']} life" if cost["life"] > 0 else f"pay {cost['mana']} mana"
        if variant is not None:
            found = game._cycling_tutor_search(player_idx, variant)
            # CR 701.18d — shuffle after the search, whether or not a
            # matching card was found.
            game.rng.shuffle(player.library)
            player.library_searches_this_game += 1
            game._trigger_library_search(player_idx)
            if found is not None:
                game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                               f"Cycle {card.name} ({cost_desc}, "
                               f"tutor: {found.name})")
            else:
                game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                               f"Cycle {card.name} ({cost_desc}, "
                               f"tutor: none found)")
            return True
        # Plain cycling — draw a card; include the drawn card's name in
        # the log so that any card "appearing from nowhere" on a later
        # turn can be traced back to the cycle that produced it
        # (conservation-invariant).
        drawn = game.draw_cards(player_idx, 1)
        drawn_name = drawn[0].name if drawn else "—"
        game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                       f"Cycle {card.name} ({cost_desc}, draw: {drawn_name})")
        return True


    @staticmethod
    def _cycling_tutor_search(game: "GameState", player_idx: int,
                              variant: Dict) -> Optional["CardInstance"]:
        """Search ``player_idx``'s library for a card that satisfies the
        landcycling / typecycling predicate.  Moves the card to hand and
        returns it, or returns None if no legal target exists.  Caller
        is responsible for shuffling the library and firing search
        triggers.

        ``variant`` is a dict produced by
        :func:`engine.oracle_parser.parse_cycling_variant` with keys
        ``require_types``, ``require_supertypes``, ``require_subtypes``.
        All three sets are ANDed; empty set = no constraint.
        """
        req_types = variant.get('require_types') or set()
        req_supers = variant.get('require_supertypes') or set()
        req_subs = variant.get('require_subtypes') or set()
        player = game.players[player_idx]
        for lib_card in player.library:
            tmpl = lib_card.template
            card_types = {ct.value for ct in tmpl.card_types}
            supertypes = {st.value for st in tmpl.supertypes}
            subtypes = set(tmpl.subtypes)
            if req_types and not req_types.issubset(card_types):
                continue
            if req_supers and not req_supers.issubset(supertypes):
                continue
            if req_subs and not req_subs.issubset(subtypes):
                continue
            # Match — tutor it to hand.
            player.library.remove(lib_card)
            lib_card.zone = "hand"
            player.hand.append(lib_card)
            return lib_card
        return None

    ALL_COLORS = ["W", "U", "B", "R", "G"]

