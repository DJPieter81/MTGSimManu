"""
Planeswalker manager — extracted from engine/game_state.py (Commit 5c).

Owns planeswalker loyalty-ability activation:
- activate: parse loyalty abilities from oracle text (via
  player_state._parse_planeswalker_abilities), apply the loyalty
  cost to the planeswalker card, resolve the ability's effect
  (tokens, draw, damage, etc.).

Static method; takes game: GameState as first arg.
"""
from __future__ import annotations

import re
import random
from typing import TYPE_CHECKING, List

from .cards import CardInstance, CardType, Keyword, Supertype, Ability, AbilityType
from .player_state import _parse_planeswalker_abilities
from .card_effects import EFFECT_REGISTRY

if TYPE_CHECKING:
    from .game_state import GameState


class PlaneswalkerManager:
    """Planeswalker loyalty-ability activation. Stateless."""

    @staticmethod
    def activate_planeswalker(game: "GameState", controller: int, pw_card: CardInstance,
                               ability_type: str = "plus"):
        """Activate a planeswalker loyalty ability."""
        pw_name = pw_card.template.name
        # Use back face oracle for transformed cards
        oracle = pw_card.template.oracle_text
        loyalty = pw_card.template.loyalty
        if getattr(pw_card, 'is_transformed', False) and pw_card.template.back_face_oracle:
            oracle = pw_card.template.back_face_oracle
            loyalty = pw_card.template.back_face_loyalty
        pw_data = _parse_planeswalker_abilities(oracle, loyalty)

        ability_info = pw_data.get(ability_type)
        if not ability_info:
            return

        loyalty_change, effect_desc = ability_info
        new_loyalty = pw_card.loyalty_counters + loyalty_change

        # Can't activate minus if not enough loyalty
        if new_loyalty < 0:
            return

        pw_card.loyalty_counters = new_loyalty
        opponent = 1 - controller

        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{pw_name} [{loyalty_change:+d}] -> {effect_desc}")

        # Execute effect based on description keywords
        # Each handler is matched by keywords in the effect description string.
        # Order matters: more specific checks first.

        if "return land from graveyard" in effect_desc:
            # Wrenn and Six +1: return a land from graveyard to hand
            player = game.players[controller]
            lands_in_gy = [c for c in player.graveyard if c.template.is_land]
            if lands_in_gy:
                land = lands_in_gy[0]
                player.graveyard.remove(land)
                land.zone = "hand"
                player.hand.append(land)
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                               f"Wrenn and Six returns {land.name} from GY to hand")

        elif "exile all colored" in effect_desc:
            # Ugin -X: exile all colored permanents (simplified as -7)
            for p in game.players:
                to_exile = [c for c in p.battlefield
                            if c.template.color_identity and c.template.name != pw_name]
                for c in to_exile:
                    game._exile_permanent(c)

        elif "exile opponent library" in effect_desc:
            # Jace -12 ult
            opp = game.players[opponent]
            while opp.library:
                card = opp.library.pop(0)
                card.zone = "exile"
                opp.exile.append(card)
            game.game_over = True
            game.winner = controller

        elif "bounce" in effect_desc and "draw" in effect_desc:
            # Teferi -3: bounce target nonland permanent AND draw a card
            opp = game.players[opponent]
            if opp.battlefield:
                nonlands = [c for c in opp.battlefield if not c.template.is_land]
                if nonlands:
                    target = max(nonlands, key=lambda c: c.template.cmc)
                    game._bounce_permanent(target)
            game.draw_cards(controller, 1)

        elif "bounce" in effect_desc:
            # Jace -1: bounce target creature
            opp = game.players[opponent]
            if opp.creatures:
                target = max(opp.creatures, key=lambda c: c.template.cmc)
                game._bounce_permanent(target)

        elif "brainstorm" in effect_desc:
            # Jace 0: draw 3, put 2 back on top
            game.draw_cards(controller, 3)
            player = game.players[controller]
            if len(player.hand) >= 2:
                # Put back 2 worst cards (lowest CMC non-land, or lands if hand is all lands)
                hand_sorted = sorted(player.hand, key=lambda c: c.template.cmc)
                for _ in range(2):
                    if hand_sorted:
                        card = hand_sorted.pop(0)
                        player.hand.remove(card)
                        card.zone = "library"
                        player.library.insert(0, card)

        elif "cast sorceries as flash" in effect_desc:
            # Teferi +1: cast sorceries as flash until next turn
            # Simplified: minor advantage, no direct board impact
            # (The static ability restricting opponents is more impactful)
            pass

        elif "look at top card" in effect_desc:
            # Jace +2: look at top of opponent's library, may put on bottom
            opp = game.players[opponent]
            if opp.library:
                # Simplified: always put on bottom (deny opponent their draw)
                card = opp.library.pop(0)
                opp.library.append(card)

        elif "instants and sorceries cost" in effect_desc:
            # Ral +1: instants/sorceries cost 1 less until next turn
            player = game.players[controller]
            player.temp_cost_reduction += 1
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                           f"{pw_name} +1 — instants and sorceries cost {{1}} less")

        elif "damage" in effect_desc:
            import re
            dmg_match = re.search(r'(\d+)\s+damage', effect_desc)
            if dmg_match:
                dmg = int(dmg_match.group(1))
            elif "equal to instants" in effect_desc:
                # Ral -2: damage = instants/sorceries cast this turn
                dmg = game._global_storm_count
            else:
                dmg = 1  # fallback

            # Smart targeting: kill a creature if the damage is lethal,
            # otherwise go face
            opp = game.players[opponent]
            if opp.creatures:
                # Find creatures we can actually kill with this damage
                killable = [
                    c for c in opp.creatures
                    if (c.toughness or 0) - c.damage_marked <= dmg
                ]
                if killable:
                    # Kill the most valuable creature we can
                    target = max(killable, key=lambda c: (
                        c.template.cmc,  # prefer higher CMC
                        c.power or 0,    # then higher power
                    ))
                    target.damage_marked += dmg
                    game.log.append(f"T{game.display_turn} P{controller+1}: "
                                   f"{pw_name} deals {dmg} to {target.name}")
                    if target.is_dead:
                        game._creature_dies(target)
                else:
                    # Can't kill anything, go face
                    opp.life -= dmg
                    game.players[controller].damage_dealt_this_turn += dmg
            else:
                opp.life -= dmg
                game.players[controller].damage_dealt_this_turn += dmg

        elif "gain" in effect_desc and "draw" in effect_desc:
            # Ugin -10 ult: gain 7 life, draw 7, put 7 permanents
            import re
            life_match = re.search(r'gain\s+(\d+)\s+life', effect_desc)
            draw_match = re.search(r'draw\s+(\d+)', effect_desc)
            if life_match:
                game.gain_life(controller, int(life_match.group(1)), pw_name)
            if draw_match:
                game.draw_cards(controller, int(draw_match.group(1)))
            # Simplified: skip the "put permanents onto battlefield" part

        elif "exile the top" in effect_desc and "cast" in effect_desc:
            # Ral -8 ultimate: exile top N, cast instants/sorceries for free
            import re
            n_match = re.search(r'top\s+(\d+)', effect_desc)
            n_cards = int(n_match.group(1)) if n_match else 8
            player = game.players[controller]
            exiled = []
            for _ in range(min(n_cards, len(player.library))):
                card = player.library.pop(0)
                card.zone = "exile"
                player.exile.append(card)
                exiled.append(card)
            game.log.append(f"T{game.display_turn} P{controller+1}: "
                           f"{pw_name} ultimate — exiles top {len(exiled)} cards")
            # Cast all instants and sorceries for free
            for card in list(exiled):
                if card.template.is_instant or card.template.is_sorcery:
                    if card in player.exile:
                        player.exile.remove(card)
                    card.zone = "hand"
                    player.hand.append(card)
                    game.log.append(f"T{game.display_turn} P{controller+1}: "
                                   f"  Free-cast {card.name} from exile")
                    game.cast_spell(controller, card, free_cast=True)

        elif "draw a card" in effect_desc.lower() and "untap" in effect_desc.lower():
            # Teferi, Hero of Dominaria +1: draw a card, untap 2 lands
            game.draw_cards(controller, 1)
            player = game.players[controller]
            untapped = 0
            for land in player.lands:
                if land.tapped and untapped < 2:
                    land.tapped = False
                    untapped += 1
            if untapped:
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                               f"  untap {untapped} lands")

        elif "put target" in effect_desc.lower() and "library" in effect_desc.lower():
            # Teferi Hero -3: tuck nonland permanent into library
            opp = game.players[opponent]
            targets = [c for c in opp.battlefield if not c.template.is_land]
            if targets:
                target = max(targets, key=lambda c: (c.template.cmc or 0, c.power or 0))
                # Tuck: remove from battlefield (not death — no dies triggers)
                if target in opp.battlefield:
                    opp.battlefield.remove(target)
                # Put into library 3rd from top
                target.zone = "library"
                if len(opp.library) >= 2:
                    opp.library.insert(2, target)
                else:
                    opp.library.append(target)
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                               f"  tucks {target.name} into library")

        elif "emblem" in effect_desc.lower() and "exile" in effect_desc.lower():
            # Teferi Hero -8 / generic emblem: exile an opponent's permanent
            opp = game.players[opponent]
            targets = [c for c in opp.battlefield]
            if targets:
                target = max(targets, key=lambda c: (c.template.cmc or 0, c.power or 0))
                if target in opp.battlefield:
                    opp.battlefield.remove(target)
                target.zone = "exile"
                opp.exile.append(target)
                game.log.append(f"T{game.display_turn} P{controller+1}: "
                               f"  emblem exiles {target.name}")

        # Planeswalker dies at 0 loyalty (SBA will catch this)

    # ─── ENTERS-TAPPED UNTAP TRIGGER ─────────────────────────────

