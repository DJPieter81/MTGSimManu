"""
Land manager — extracted from engine/game_state.py.

Owns land-entry flow:
- play_land: hand → battlefield with tapped/untapped/shock/fast/fetch
  dispatch, cost-payment callbacks, and landfall triggers.
- crack_fetchland: sacrifice fetch, pay 1 life (if applicable), search
  library for a land, enter it, shuffle, trigger landfall + opponent
  library-search triggers.
- trigger_library_search: opponent "whenever an opponent searches"
  watchers (Wan Shi Tong pattern).
- trigger_landfall: generic multi-trigger landfall (Omnath-pattern
  "first/second/third time a land enters").
- apply_untap_on_enter_triggers: Amulet-of-Vigor-pattern — any
  permanent with the untap trigger oracle un-taps the entering one.
- apply_lands_enter_untapped: Spelunking-pattern static for lands.

All methods are static; they take `game: GameState` as the first
argument, matching the CombatManager / ManaPayment pattern.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


class LandManager:
    """Land-entry flow. Stateless; call methods with `game` as 1st arg."""

    @staticmethod
    def play_land(game: "GameState", player_idx: int,
                  card: "CardInstance") -> None:
        """Play a land from hand to battlefield."""
        from .card_database import FETCH_LAND_COLORS
        player = game.players[player_idx]
        max_lands = 1 + player.extra_land_drops
        if player.lands_played_this_turn >= max_lands:
            return
        if card not in player.hand:
            return

        player.hand.remove(card)
        player.lands_played_this_turn += 1
        card.controller = player_idx

        # ── Always-tapped lands (from oracle text: "enters tapped") ──
        if (card.template.enters_tapped
                and card.template.untap_life_cost == 0
                and card.template.untap_max_other_lands < 0):
            card.enter_battlefield()
            card.tapped = True
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: "
                f"Play {card.name} (enters tapped)")
        # ── Lands with discoverable optional ETB costs (shock-pay,
        # painlands, future "pay X for ETB-untapped" mechanics) ──
        elif card.template.untap_life_cost > 0:
            from engine.optional_costs import offer_optional_costs
            # Default: enters tapped.  The router asks the AI to decide
            # any optional costs; an accepted "pay N life, ETB
            # untapped" cost flips `tapped` back to False as part of
            # apply_to_game.  No mechanic-named callback in this path.
            card.enter_battlefield()
            card.tapped = True
            offer_optional_costs(game, player_idx, card, trigger="etb")
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}"
                f" ({'untapped, life: ' + str(player.life) if not card.tapped else 'tapped'})")
        # ── Conditional untap: untapped if ≤ N other lands (fast lands) ──
        elif card.template.untap_max_other_lands >= 0:
            other_lands = len([c for c in player.battlefield
                               if c.template.is_land])
            card.enter_battlefield()
            if other_lands <= card.template.untap_max_other_lands:
                card.tapped = False
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Play {card.name} (untapped, {other_lands} other lands)")
            else:
                card.tapped = True
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Play {card.name} (tapped, {other_lands} other lands)")
        # ── Fetchland: play then immediately crack ──
        elif card.name in FETCH_LAND_COLORS:
            card.enter_battlefield()
            player.battlefield.append(card)
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}")
            # Trigger landfall for the fetch itself
            LandManager.trigger_landfall(game, player_idx)
            # Immediately crack the fetchland
            LandManager.crack_fetchland(game, player_idx, card)
            return  # Don't append again or trigger landfall again below
        else:
            card.enter_battlefield()
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}")

        # Add to battlefield (non-fetch path)
        if card.name not in FETCH_LAND_COLORS:
            player.battlefield.append(card)

        # ── Generic "untap enters tapped" (Amulet of Vigor pattern) ──
        LandManager.apply_untap_on_enter_triggers(game, card, player_idx)
        # ── "Lands you control enter untapped" static (Spelunking pattern) ──
        LandManager.apply_lands_enter_untapped(game, card, player_idx)

        # ── Landfall triggers ──
        LandManager.trigger_landfall(game, player_idx)

    @staticmethod
    def crack_fetchland(game: "GameState", player_idx: int,
                        fetch_card: "CardInstance") -> None:
        """Sacrifice a fetchland, pay 1 life, search library for a land."""
        from .card_database import FETCH_LAND_COLORS
        player = game.players[player_idx]
        fetch_name = fetch_card.name
        fetch_colors = FETCH_LAND_COLORS.get(fetch_name, [])

        # Pay 1 life (Prismatic Vista, Fabled Passage, Evolving Wilds,
        # Terramorphic Expanse don't cost life; Zendikar/Onslaught fetches do)
        no_life_fetches = {"Prismatic Vista", "Fabled Passage",
                           "Evolving Wilds", "Terramorphic Expanse"}
        if fetch_name not in no_life_fetches:
            # Safety: if paying 1 life would kill us, don't crack the fetch
            if player.life <= 1:
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"{fetch_name} not cracked (life too low: {player.life})")
                return
            player.life -= 1

        # Sacrifice the fetchland (triggers revolt)
        if fetch_card in player.battlefield:
            player.battlefield.remove(fetch_card)
        fetch_card.zone = "graveyard"
        player.graveyard.append(fetch_card)
        # Track that a permanent left the battlefield (for revolt)
        player.creatures_died_this_turn = max(
            player.creatures_died_this_turn, 1)

        # ── Hand-aware fetch target selection via callbacks ──
        best_land = game.callbacks.choose_fetch_target(
            game, player_idx, fetch_card, player.library, fetch_colors
        )

        if best_land:
            player.library.remove(best_land)
            best_land.controller = player_idx

            # Lands with discoverable optional ETB costs.  Router-driven;
            # no mechanic-named callback.  See `engine/optional_costs.py`.
            if best_land.template.untap_life_cost > 0:
                from engine.optional_costs import offer_optional_costs
                best_land.enter_battlefield()
                best_land.tapped = True
                offer_optional_costs(game, player_idx, best_land, trigger="etb")
                state = ("untapped" if not best_land.tapped else "tapped")
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Crack {fetch_name} (pay 1 life) -> {best_land.name} "
                    f"({state}, life: {player.life})")
            else:
                # Fabled Passage: tapped if < 4 lands; Zendikar fetches:
                # always untapped.
                best_land.enter_battlefield()
                if fetch_name in no_life_fetches and len(player.lands) < 4:
                    best_land.tapped = True
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Crack {fetch_name} -> {best_land.name} "
                    f"({'tapped' if best_land.tapped else 'untapped'})")

            player.battlefield.append(best_land)
            # Amulet of Vigor and similar untap triggers
            LandManager.apply_untap_on_enter_triggers(
                game, best_land, player_idx)
            # Spelunking / "lands you control enter untapped" static must
            # apply on the fetchland-crack path too — matches the play_land
            # path.
            LandManager.apply_lands_enter_untapped(
                game, best_land, player_idx)
            # Bounce land ETB (return a land to hand)
            if best_land.template.is_land:
                from .oracle_resolver import resolve_etb_from_oracle
                resolve_etb_from_oracle(game, best_land, player_idx)
            # Shuffle library
            game.rng.shuffle(player.library)
            # Track library search and trigger opponent's search triggers
            player.library_searches_this_game += 1
            LandManager.trigger_library_search(game, player_idx)
            # Trigger landfall for the fetched land
            LandManager.trigger_landfall(game, player_idx)
        else:
            # No valid land found (shuffle anyway)
            game.rng.shuffle(player.library)
            player.library_searches_this_game += 1
            LandManager.trigger_library_search(game, player_idx)
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: "
                f"Crack {fetch_name} (no valid land found)")

    @staticmethod
    def trigger_library_search(game: "GameState", searcher_idx: int) -> None:
        """Trigger effects for opponents when a player searches their library.

        Handles cards like Wan Shi Tong that grow when opponents search.
        """
        opp_idx = 1 - searcher_idx
        opp = game.players[opp_idx]
        for c in opp.battlefield:
            oracle = (c.template.oracle_text or '').lower()
            if ('whenever an opponent searches' in oracle
                    and 'library' in oracle):
                # +1/+1 counter
                c.plus_counters += 1
                # Draw a card if oracle says so
                if 'draw a card' in oracle:
                    game.draw_cards(opp_idx, 1)
                game.log.append(
                    f"T{game.display_turn} P{opp_idx+1}: "
                    f"{c.name} triggers (opponent searched) — "
                    f"+1/+1 counter ({c.power}/{c.toughness}), draw a card")

    @staticmethod
    def trigger_landfall(game: "GameState", player_idx: int) -> None:
        """Process landfall triggers for the given player."""
        player = game.players[player_idx]
        opponent_idx = 1 - player_idx

        # Track landfall count this turn (initialize if needed)
        if not hasattr(player, '_landfall_count_this_turn'):
            player._landfall_count_this_turn = 0
        player._landfall_count_this_turn += 1
        landfall_num = player._landfall_count_this_turn

        # Generic multi-landfall triggers from oracle text.
        # Handles: "first time...gain life", "second time...add mana",
        # "third time...damage"
        for perm in player.battlefield:
            oracle = (perm.template.oracle_text or '').lower()
            if ('landfall' not in oracle
                    and 'land enters' not in oracle
                    and 'whenever a land' not in oracle):
                continue
            if ('first time' in oracle
                    or 'second time' in oracle
                    or 'third time' in oracle):
                # Multi-trigger landfall (Omnath pattern)
                if landfall_num == 1 and 'first time' in oracle:
                    m = re.search(r'gain\s+(\d+)\s+life', oracle)
                    if m:
                        game.gain_life(player_idx, int(m.group(1)),
                                        f"{perm.name} landfall")
                        game.log.append(
                            f"T{game.display_turn} P{player_idx+1}: "
                            f"{perm.name} 1st landfall: +{m.group(1)} life")
                elif landfall_num == 2 and 'second time' in oracle:
                    # Add mana — parse colors from oracle
                    for color in ['R', 'G', 'W', 'U', 'B']:
                        if '{' + color.lower() + '}' in oracle:
                            player.mana_pool.add(color, 1)
                    game.log.append(
                        f"T{game.display_turn} P{player_idx+1}: "
                        f"{perm.name} 2nd landfall: add mana")
                elif landfall_num == 3 and 'third time' in oracle:
                    m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
                    if m:
                        dmg = int(m.group(1))
                        game.players[opponent_idx].life -= dmg
                        player.damage_dealt_this_turn += dmg
                        game.log.append(
                            f"T{game.display_turn} P{player_idx+1}: "
                            f"{perm.name} 3rd landfall: {dmg} damage")

    @staticmethod
    def apply_untap_on_enter_triggers(game: "GameState",
                                       permanent: "CardInstance",
                                       controller: int) -> None:
        """Generic 'whenever a permanent you control enters tapped, untap
        it' trigger.

        Detects any artifact/enchantment on the battlefield with that
        oracle pattern (e.g. Amulet of Vigor) without hardcoding card
        names.
        """
        if not getattr(permanent, 'tapped', False):
            return
        player = game.players[controller]
        untaps = 0
        for watcher in player.battlefield:
            if watcher.instance_id == permanent.instance_id:
                continue
            w_oracle = (watcher.template.oracle_text or '').lower()
            if ('whenever' in w_oracle and 'enters tapped' in w_oracle
                    and 'untap it' in w_oracle):
                untaps += 1
        if untaps > 0:
            # Each copy of the untap-trigger permanent independently
            # untaps. Idempotent today (tapped = False after any one),
            # but semantically correct: N copies fire N triggers.
            for _ in range(untaps):
                permanent.tapped = False
            # Find watcher names for logging
            watcher_names = [
                w.name for w in player.battlefield
                if w.instance_id != permanent.instance_id
                and 'whenever' in (w.template.oracle_text or '').lower()
                and 'enters tapped' in (w.template.oracle_text or '').lower()
                and 'untap it' in (w.template.oracle_text or '').lower()
            ]
            copies_note = f" (x{untaps})" if untaps > 1 else ""
            game.log.append(
                f"T{game.display_turn} P{controller+1}: "
                f"{', '.join(watcher_names)} untaps {permanent.name}{copies_note}"
            )

    @staticmethod
    def apply_lands_enter_untapped(game: "GameState",
                                    land: "CardInstance",
                                    controller: int) -> None:
        """Generic 'lands you control enter the battlefield untapped'
        static ability.

        Fires when a land enters; checks for Spelunking and similar
        permanents. Does nothing if land is already untapped.
        """
        if (not getattr(land, 'tapped', False)
                or not land.template.is_land):
            return
        player = game.players[controller]
        for watcher in player.battlefield:
            if watcher.instance_id == land.instance_id:
                continue
            w_oracle = (watcher.template.oracle_text or '').lower()
            if ('lands you control enter' in w_oracle
                    and 'untapped' in w_oracle):
                land.tapped = False
                game.log.append(
                    f"T{game.display_turn} P{controller+1}: "
                    f"{watcher.name} — {land.name} enters untapped")
                break
