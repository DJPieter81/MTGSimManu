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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


class LandManager:
    """Land-entry flow. Stateless; call methods with `game` as 1st arg."""

    @staticmethod
    def play_land(game: "GameState", player_idx: int,
                  card: "CardInstance") -> None:
        """Play a land from hand to battlefield.

        Hand→battlefield is dispatched through
        `engine.zone_transfer.transfer(..., kind=TransferKind.ETB)` so
        ETB triggers (including the surveil-land cycle's "When this
        land enters, surveil 1") fire through the same fan-out a
        creature ETB uses — see R3 in
        docs/history/audits/2026-05-16_rules_audit.md. The
        permanent-type-specific tapped-state logic (shock-pay,
        fast-lands, fetchlands) runs around the transfer; the trigger
        dispatch is uniform.
        """
        from .zone_transfer import TransferKind, transfer
        player = game.players[player_idx]
        max_lands = 1 + player.extra_land_drops
        if player.lands_played_this_turn >= max_lands:
            return
        if card not in player.hand:
            return

        player.hand.remove(card)
        player.lands_played_this_turn += 1
        card.controller = player_idx

        # ── Fetchland: play then immediately crack ──
        # Fetchlands sacrifice themselves on resolution; no ETB
        # trigger pipeline runs on the fetchland itself (the fetched
        # land's ETB runs in `crack_fetchland`). Kept on the legacy
        # manual-append path so the sacrifice-and-replace mechanic
        # stays atomic.
        if card.template.fetchland is not None:
            card.enter_battlefield()
            player.battlefield.append(card)
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}")
            # Trigger landfall for the fetch itself
            LandManager.trigger_landfall(game, player_idx)
            # Immediately crack the fetchland
            LandManager.crack_fetchland(game, player_idx, card)
            return

        # ── Non-fetch lands: route through zone_transfer.transfer for
        #    uniform ETB-trigger dispatch.  `transfer` calls
        #    `_append_to_zone("battlefield")` which invokes
        #    `card.enter_battlefield()` (sets `tapped = True` if the
        #    template's `enters_tapped` flag is set), appends to the
        #    battlefield, then fires the registered ETB fan-out
        #    (`EFFECT_REGISTRY.execute(EffectTiming.ETB)` + generic
        #    `resolve_etb_from_oracle`).
        transfer(game, card,
                 src_zone="hand", dst_zone="battlefield",
                 kind=TransferKind.ETB, controller=player_idx)

        # ── Post-entry tapped-state finalisation ──
        # `enter_battlefield()` sets `tapped = True` only for lands
        # whose template flag is enters_tapped=True; the conditional
        # cases below override for shock-pay (optional cost flips
        # back to untapped) and fast-lands (untapped if few other
        # lands).  Logging is done here so each path's message
        # matches the legacy format and the replay parser is
        # unchanged.
        if (card.template.enters_tapped
                and card.template.untap_life_cost == 0
                and card.template.untap_max_other_lands < 0):
            # Always-tapped land — `enter_battlefield` already set
            # tapped=True. No-op here, just log.
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: "
                f"Play {card.name} (enters tapped)")
        elif card.template.untap_life_cost > 0:
            # Shock-pay / painland: offer the optional ETB-untapped
            # cost. The router-driven cost may flip `card.tapped`
            # back to False as part of apply_to_game.
            from engine.optional_costs import offer_optional_costs
            offer_optional_costs(game, player_idx, card, trigger="etb")
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}"
                f" ({'untapped, life: ' + str(player.life) if not card.tapped else 'tapped'})")
        elif card.template.untap_max_other_lands >= 0:
            # Fast-land: untapped if few enough other lands present.
            # `enter_battlefield` set tapped=True (template flag);
            # override to False when condition holds.  Exclude the
            # land just entered from the count.
            other_lands = len([c for c in player.battlefield
                               if c.template.is_land
                               and c.instance_id != card.instance_id])
            if other_lands <= card.template.untap_max_other_lands:
                card.tapped = False
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Play {card.name} (untapped, {other_lands} other lands)")
            else:
                # Already tapped from enter_battlefield(); just log.
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Play {card.name} (tapped, {other_lands} other lands)")
        else:
            # Regular land (untapped, no special cost).
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}")

        # ── Generic "untap enters tapped" (Amulet of Vigor pattern) ──
        LandManager.apply_land_etb_static(game, card, player_idx)
        # ── "Lands you control enter untapped" static (Spelunking pattern) ──
        LandManager.apply_lands_enter_untapped(game, card, player_idx)

        # ── Landfall triggers ──
        LandManager.trigger_landfall(game, player_idx)

    @staticmethod
    def crack_fetchland(game: "GameState", player_idx: int,
                        fetch_card: "CardInstance") -> None:
        """Sacrifice a fetchland, pay its printed cost, search for a land.

        Every number here comes off the card: `CardTemplate.fetchland` is
        the parsed `FetchLandProfile` (colours, life payment, how many
        lands, whether the fetched land enters tapped, and any conditional
        untap rider).  Nothing about a fetchland is keyed by card name.
        """
        player = game.players[player_idx]
        fetch_name = fetch_card.name
        profile = fetch_card.template.fetchland
        if profile is None:
            return
        fetch_colors = list(profile.colors)

        # Life is part of the printed activation cost ("Pay 1 life") — the
        # Onslaught/Zendikar cycles print it, the Panorama/Landscape/
        # Evolving Wilds families do not.
        if profile.life_cost:
            # NOTE (layering): this "don't kill yourself" guard is a
            # STRATEGY decision living in the engine, which the
            # abstraction contract reserves for `ai/`.  Left in place
            # deliberately — moving it changes which plays the AI is
            # offered, a behavioural change this migration is not
            # measuring.  Flagged for a follow-up that lifts it into the
            # play-scoring layer (`ai/ev_player.py` already declines to
            # PLAY a life-costing fetch at 1 life).
            if player.life <= profile.life_cost:
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"{fetch_name} not cracked (life too low: {player.life})")
                return
            player.life -= profile.life_cost

        # Sacrifice the fetchland (triggers revolt). The battlefield -> graveyard
        # move funnels through ZoneManager.move_card, which advances the
        # controller's permanents_left_battlefield_this_turn (CR 702.139 revolt)
        # exactly once — so no per-fetch counter bump is needed here, and a land
        # crack is correctly NOT miscounted as a creature death.
        game.zone_mgr.move_card(game, fetch_card, "battlefield", "graveyard",
                                cause="fetchland sacrifice")

        # ── Hand-aware fetch target selection via callbacks ──
        # `profile.count` is the printed number of land cards one
        # activation finds ("up to two basic land cards"); it is 1 for
        # every fetch outside the Blighted Woodland shape, so this loop
        # runs exactly once for them.
        for _ in range(profile.count):
            best_land = game.callbacks.choose_fetch_target(
                game, player_idx, fetch_card, player.library, fetch_colors
            )
            if not best_land:
                break
            player.library.remove(best_land)
            best_land.controller = player_idx

            paid = (f" (pay {profile.life_cost} life)"
                    if profile.life_cost else "")
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
                    f"Crack {fetch_name}{paid} -> {best_land.name} "
                    f"({state}, life: {player.life})")
            else:
                # The fetch's OWN text decides how the found land arrives:
                # "put it onto the battlefield" vs "… onto the battlefield
                # tapped", plus any conditional untap rider ("Then if you
                # control four or more lands, untap that land").  The +1
                # counts the land now entering, which the printed
                # threshold includes.
                best_land.enter_battlefield()
                if profile.target_enters_tapped:
                    best_land.tapped = True
                    if (profile.untap_target_min_lands
                            and len(player.lands) + 1
                            >= profile.untap_target_min_lands):
                        best_land.tapped = False
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Crack {fetch_name} -> {best_land.name} "
                    f"({'tapped' if best_land.tapped else 'untapped'})")

            player.battlefield.append(best_land)
            # Amulet of Vigor and similar untap triggers
            LandManager.apply_land_etb_static(
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
            # Loop ran to completion — every printed target was found.
            return

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
            if c.template.has_library_search_opponent_trigger:
                # +1/+1 counter
                c.add_plus_counters(1, game)
                # Draw a card if the trigger says so (pre-computed at load time)
                if c.template.library_search_trigger_draws_card:
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

        # Generic multi-landfall triggers — amounts pre-computed at load time.
        # Handles: "first time…gain life", "second time…add mana",
        # "third time…damage" (Omnath, Locus of Creation pattern).
        for perm in player.battlefield:
            t = perm.template
            if not t.has_landfall:
                continue
            if (t.landfall_first_life_gain
                    or t.landfall_second_mana_colors
                    or t.landfall_third_damage):
                # Multi-trigger landfall (Omnath pattern)
                if landfall_num == 1 and t.landfall_first_life_gain:
                    gain = t.landfall_first_life_gain
                    game.gain_life(player_idx, gain, f"{perm.name} landfall")
                    game.log.append(
                        f"T{game.display_turn} P{player_idx+1}: "
                        f"{perm.name} 1st landfall: +{gain} life")
                elif landfall_num == 2 and t.landfall_second_mana_colors:
                    # Add mana — colors pre-computed at load time
                    for color in t.landfall_second_mana_colors:
                        player.mana_pool.add(color, 1)
                    game.log.append(
                        f"T{game.display_turn} P{player_idx+1}: "
                        f"{perm.name} 2nd landfall: add mana")
                elif landfall_num == 3 and t.landfall_third_damage:
                    dmg = t.landfall_third_damage
                    game.players[opponent_idx].life -= dmg
                    player.damage_dealt_this_turn += dmg
                    game.log.append(
                        f"T{game.display_turn} P{player_idx+1}: "
                        f"{perm.name} 3rd landfall: {dmg} damage")

    @staticmethod
    def apply_land_etb_static(game: "GameState",
                              land: "CardInstance",
                              controller: int) -> None:
        """Uniform land-entry hook: structural clauses every land-entry
        path must fire, regardless of HOW the land arrived (land drop,
        fetch crack, mass land search).

        Currently: untap-on-enter watchers (Amulet pattern) followed by
        the mandatory karoo ETB clause 'return a land you control to
        its owner's hand' (E1b), parsed at DB load into
        `template.etb_return_land`.  Ordering matters — the untap
        trigger and the return trigger enter the queue together and
        the controller orders them; resolving untap first matches the
        line every karoo deck takes (untap, then bounce).
        """
        LandManager.apply_untap_on_enter_triggers(game, land, controller)
        if not getattr(land.template, "etb_return_land", False):
            return
        player = game.players[controller]
        if land not in player.battlefield:
            return  # already gone (replaced/removed by another trigger)
        others = [c for c in player.battlefield
                  if c.template.is_land
                  and c.instance_id != land.instance_id]
        if others:
            # Engine-neutral deterministic choice: bounce the land
            # whose loss costs least NOW — prefer an already-tapped
            # land, then the fewest mana units, then the fewest color
            # options (a basic before a dual).  All keys derive from
            # game state; the AI can pass a preference later via the
            # same optional-cost router the shock-pay path uses.
            def _cost_key(c):
                return (0 if c.tapped else 1,
                        c.template.mana_count,
                        len(c.template.produces_mana))
            target = min(others, key=_cost_key)
        else:
            # Mandatory trigger, one legal object: itself.
            target = land
        game.zone_mgr.move_card(game, target, "battlefield", "hand",
                                cause=f"{land.name} ETB returns {target.name} to hand")

    @staticmethod
    def player_has_untap_on_enter_watcher(game: "GameState",
                                           controller: int) -> bool:
        """Return True if the controller has any permanent that makes
        entering-tapped permanents untap (Amulet of Vigor oracle pattern)
        or that makes lands enter untapped (Spelunking oracle pattern).

        Used by mass-land-search effects to decide whether bounce lands
        are worth prioritising — they only generate extra mana when an
        untap trigger or static ability is in play.
        """
        player = game.players[controller]
        for perm in player.battlefield:
            w_oracle = (perm.template.oracle_text or '').lower()
            if ('whenever' in w_oracle
                    and 'enters tapped' in w_oracle
                    and 'untap it' in w_oracle):
                return True
            if 'lands you control enter' in w_oracle and 'untapped' in w_oracle:
                return True
        return False

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
