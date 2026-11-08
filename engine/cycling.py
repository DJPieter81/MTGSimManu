"""
Cycling — extracted from engine/game_state.py (Commit 6).

Owns the cycling mechanic:
- can_cycle: legality check for the cycling activated ability
  (oracle text parsing + mana/life availability).
- activate_cycling: pay the cycling cost (discarding the card) and put
  the ability on the stack.
- resolve_cycling: the ability resolves -- draw 1 (or execute the
  cycling-replacement effect for Lorien Revealed-style "search for an
  Island" variants).
- _cycling_tutor_search: library search for typecycling /
  landcycling variants.

Static methods; take game: GameState as first argument.
"""
from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Dict, Optional

from .cards import CardInstance, CardType, Keyword
from .stack import StackItem, StackItemType

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
            untapped = player.untapped_mana_capacity() + player.mana_pool.total() + player._tron_mana_bonus()
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
        """Activate cycling (CR 702.29a): pay the cost -- discarding the
        card is part of it -- and put the ability on the stack; it draws a
        card (or, for typecycling, searches) when it resolves
        (`resolve_cycling`). Cycling is an activated ability, not a cast
        (no storm count, no prowess triggers).

        A trigger the cost payment set off -- a cycled madness card's
        (CR 702.35b) -- goes on the stack above the ability, so it
        resolves first (CR 602.2e, 603.3).
        """
        if not game.can_cycle(player_idx, card):
            return False
        cost = card.template.cycling_cost_data or {"mana": 0, "life": 0, "colors": set()}
        player = game.players[player_idx]
        stack_depth = len(game.stack.items)
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
        # Discard the card as the cost (CR 702.29a: "Discard this card").
        # Routed through the discard funnel so discard replacements
        # (madness, CR 702.35) apply to cost-discards too.
        game.discard_card(player_idx, card, cause="cycling")
        # Fire "whenever you cycle (another card)" triggers from
        # battlefield permanents controlled by the cycling player.
        # CR 702.28c family: Drannith Stinger, Drannith Healer,
        # Flourishing Fox, Curator of Mysteries, and any future card
        # with has_cycling_watch_trigger=True on its template.
        CyclingManager._fire_cycling_watch_triggers(game, player_idx)
        cost_desc = (f"pay {cost['life']} life" if cost["life"] > 0
                     else f"pay {cost['mana']} mana")
        cycling = StackItem(
            item_type=StackItemType.ACTIVATED_ABILITY, source=card,
            controller=player_idx, ability=None, description="cycling",
            effect=functools.partial(CyclingManager.resolve_cycling,
                                     cost_desc=cost_desc))
        game.stack.push(cycling)
        game.stack.below_waiting_triggers(cycling, stack_depth)
        return True

    @staticmethod
    def resolve_cycling(game: "GameState", source: "CardInstance",
                        controller: int, targets=None, *,
                        cost_desc: str) -> None:
        """The cycling ability resolves (CR 702.29a): landcycling and
        typecycling search the library; plain cycling draws a card."""
        card, player_idx = source, controller
        player = game.players[player_idx]
        variant = card.template.cycling_variant_data
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
            return
        # Plain cycling — draw a card; include the drawn card's name in
        # the log so that any card "appearing from nowhere" on a later
        # turn can be traced back to the cycle that produced it
        # (conservation-invariant).
        drawn = game.draw_cards(player_idx, 1)
        drawn_name = drawn[0].name if drawn else "—"
        game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                       f"Cycle {card.name} ({cost_desc}, draw: {drawn_name})")


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
            game.zone_mgr.move_card(game, lib_card, "library", "hand",
                                    cause="cycling tutor")
            return lib_card
        return None

    @staticmethod
    def _fire_cycling_watch_triggers(game: "GameState", controller: int) -> None:
        """Resolve 'whenever you cycle (another card)' effects from
        battlefield permanents controlled by ``controller``.

        Scans the controller's battlefield for any permanent whose
        template has ``has_cycling_watch_trigger=True`` and resolves
        the supported effect subtypes:

        - damage subtype: ``cycling_watch_trigger_damage > 0``
          → deal that much damage to each opponent (CR 702.28c pattern:
          "this creature deals N damage to each opponent").
        - life-gain subtype: ``cycling_watch_trigger_life_gain > 0``
          → controller gains that many life points (pattern:
          "you gain N life").

        Additional subtypes (scry, +1/+1 counters, tokens) are not
        yet implemented; they are silent no-ops until a test and
        implementation are added for each mechanic.
        """
        from .damage import deal_damage  # local import avoids circular dep

        for watcher in game.players[controller].battlefield[:]:
            tmpl = watcher.template
            if not tmpl.has_cycling_watch_trigger:
                continue
            # Damage subtype — deal to each opponent.
            if tmpl.cycling_watch_trigger_damage > 0:
                dmg = tmpl.cycling_watch_trigger_damage
                opp_idx = 1 - controller
                deal_damage(watcher, game.players[opp_idx], dmg)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{watcher.name} cycle-watch trigger — "
                    f"deals {dmg} to P{opp_idx+1}"
                )
            # Life-gain subtype — controller gains life.
            if tmpl.cycling_watch_trigger_life_gain > 0:
                gain = tmpl.cycling_watch_trigger_life_gain
                game.gain_life(controller, gain, watcher.name)
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{watcher.name} cycle-watch trigger — "
                    f"P{controller+1} gains {gain} life"
                )

    ALL_COLORS = ["W", "U", "B", "R", "G"]

