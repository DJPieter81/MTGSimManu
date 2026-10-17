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

from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


# Which of a land's own entry rules decided its tapped state
# (`LandManager.settle_own_entry_tapped`; CR 614.1d, 614.12).
ENTERS_TAPPED = "enters_tapped"           # "This land enters tapped."
PAYS_TO_UNTAP = "pays_to_untap"           # "... you may pay 2 life. If you don't, it enters tapped."
COUNTS_OTHER_LANDS = "counts_other_lands"  # "... enters tapped unless you control two or fewer other lands."


class LandManager:
    """Land-entry flow. Stateless; call methods with `game` as 1st arg."""

    @staticmethod
    def settle_own_entry_tapped(game: "GameState", card: "CardInstance",
                                player_idx: int) -> Tuple[str, int]:
        """A land's own entry rules decide whether it enters tapped,
        however it enters (CR 614.1d, 614.12: each is a replacement
        effect on its entering). `enter_battlefield` has already tapped a
        land whose template enters tapped. Then:
        - an optional untap payment ("you may pay 2 life") is offered
          through the optional-cost router;
        - a land-count condition untaps the land when few enough other
          lands are present.

        Returns which rule applied (`ENTERS_TAPPED`, `PAYS_TO_UNTAP`,
        `COUNTS_OTHER_LANDS`, or "" for none) and, for the land count, the
        other lands counted, so the caller can log."""
        t = card.template
        if (t.enters_tapped and t.untap_life_cost == 0
                and t.untap_max_other_lands < 0):
            return ENTERS_TAPPED, 0
        if t.untap_life_cost > 0:
            from engine.optional_costs import offer_optional_costs
            offer_optional_costs(game, player_idx, card, trigger="etb")
            return PAYS_TO_UNTAP, 0
        if t.untap_max_other_lands >= 0:
            # Exclude the land now entering from the count.
            other_lands = len([c for c in game.players[player_idx].battlefield
                               if c.template.is_land
                               and c.instance_id != card.instance_id])
            if other_lands <= t.untap_max_other_lands:
                card.tapped = False
            return COUNTS_OTHER_LANDS, other_lands
        return "", 0

    @staticmethod
    def play_land(game: "GameState", player_idx: int,
                  card: "CardInstance") -> None:
        """Play a land from hand -- or from exile under a permission to
        play it (CR 305.1) -- to the battlefield.

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
        # Only a land is played as a land (CR 305.1): the card itself, or a
        # modal double-faced card's land back face (CR 712).
        face = card.template.playable_land_face
        if face is None:
            return
        # A land is played from hand, or from exile under a permission to
        # play it ("you may play those cards", CR 305.1); either way it is
        # the turn's land play (CR 305.2).
        from . import rules_query
        if card in player.hand:
            src_zone, src_list = "hand", player.hand
        elif (card.zone == "exile" and card in game.players[card.owner].exile
              and rules_query.play_permitted(game, player_idx, card)):
            # In its owner's exile (CR 400.3), whoever plays it.
            src_zone, src_list = "exile", game.players[card.owner].exile
        else:
            return

        src_list.remove(card)
        if src_zone == "exile":
            # CR 400.7: the land is a new object; the permission that
            # named the exiled card no longer names it.
            game.continuous_effects.forget_object(card.instance_id)
        player.lands_played_this_turn += 1
        card.controller = player_idx
        if face is not card.template:
            # The back face is played: the permanent is that face, and only
            # that face (CR 712); the zone funnel restores the card's own
            # template when it leaves the battlefield (CR 712.8a).
            card._front_template = card.template
            card.template = face

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
                 src_zone=src_zone, dst_zone="battlefield",
                 kind=TransferKind.ETB, controller=player_idx)

        # ── Post-entry tapped-state finalisation ──
        # The land's own entry rules (`settle_own_entry_tapped`, the one
        # owner a land put by an effect reads too). Logging is done here
        # so each path's message matches the legacy format and the replay
        # parser is unchanged.
        rule, other_lands = LandManager.settle_own_entry_tapped(
            game, card, player_idx)
        if rule == ENTERS_TAPPED:
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: "
                f"Play {card.name} (enters tapped)")
        elif rule == PAYS_TO_UNTAP:
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: Play {card.name}"
                f" ({'untapped, life: ' + str(player.life) if not card.tapped else 'tapped'})")
        elif rule == COUNTS_OTHER_LANDS:
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: "
                f"Play {card.name} ({'tapped' if card.tapped else 'untapped'}"
                f", {other_lands} other lands)")
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
    def land_type_is_set(game: "GameState", land) -> bool:
        """CR 305.7: a nonbasic land whose type is SET to a basic type
        (Blood Moon family, layer 4) loses its printed abilities and has
        only that type's mana ability — no fetch, no sacrifice-search, no
        other activated ability.  The ONE predicate every activation path
        asks (the fetch path and the engine's residual sacrifice
        heuristic alike).  Reads the live layer state, and the source
        directly for a land used as it enters (before the next
        recalculate())."""
        from .cards import Supertype
        from .continuous_effects import forced_land_type_in_play
        return bool(
            getattr(land, 'cem_land_type_set', None)
            or (forced_land_type_in_play(game)
                and Supertype.BASIC not in (land.template.supertypes or [])))

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
        # A nonbasic land whose type is SET to a basic type (Blood Moon
        # family, CR 305.7) has only that type's mana ability — no fetch.
        if LandManager.land_type_is_set(game, fetch_card):
            game.log.append(
                f"T{game.display_turn} P{player_idx+1}: "
                f"{fetch_name} has no fetch ability (its land type is set)")
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

            paid = (f" (pay {profile.life_cost} life)"
                    if profile.life_cost else "")
            # The fetch's OWN text decides how the found land arrives:
            # "put it onto the battlefield" vs "… onto the battlefield
            # tapped". The land enters as any land does (its own entry
            # rules, an untap payment offered through the optional-cost
            # router: `enter_land`). Then comes any conditional untap rider
            # ("Then if you control four or more lands, untap that land"),
            # whose count includes the land now on the battlefield.
            LandManager.enter_land(game, best_land, player_idx, "library",
                                   profile.target_enters_tapped, "")
            if (profile.target_enters_tapped
                    and profile.untap_target_min_lands
                    and len(player.lands) >= profile.untap_target_min_lands):
                best_land.tapped = False
            if best_land.template.untap_life_cost > 0:
                state = ("untapped" if not best_land.tapped else "tapped")
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Crack {fetch_name}{paid} -> {best_land.name} "
                    f"({state}, life: {player.life})")
            else:
                game.log.append(
                    f"T{game.display_turn} P{player_idx+1}: "
                    f"Crack {fetch_name} -> {best_land.name} "
                    f"({'tapped' if best_land.tapped else 'untapped'})")

            # Its enter abilities, then an untap-on-enter watcher, a
            # karoo's return and "lands you control enter untapped" -- as
            # for a land played.
            LandManager.land_entered(game, best_land, player_idx)
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
    def enter_land(game: "GameState", card, player_idx: int, from_zone: str,
                   tapped: bool, cause: str) -> None:
        """A land an effect puts onto the battlefield enters it under
        `player_idx` from `from_zone`, through the zone funnel. Putting a
        land is not playing one (CR 305.4), but it enters like any land:
        - its own entry rules decide its tapped state
          (`settle_own_entry_tapped`, as for a land played; CR 614.1d,
          614.12);
        - the instruction's "tapped" adds to them, so a land put tapped
          enters tapped and is offered no untap payment that could buy
          nothing.

        Auditor `614.1d/land_entry`. The land's entry triggers and the
        statics that see it follow in `land_entered`; a simultaneous entry
        enters every land before any of them."""
        game.zone_mgr.move_card(game, card, from_zone, "battlefield",
                                cause=cause, controller_override=player_idx)
        if tapped:
            card.tapped = True
        else:
            LandManager.settle_own_entry_tapped(game, card, player_idx)
        LandManager._audit_land_entry(game, card, player_idx, tapped)

    @staticmethod
    def _audit_land_entry(game: "GameState", card, player_idx: int,
                          tapped: bool) -> None:
        """CR 614.1d, restated from the template's printed entry fields:
        a land put tapped, or one whose text says it enters tapped, is
        tapped as it enters. A land whose tapped state is a land count
        is tapped exactly when more other lands are present. A land with
        no entry rule enters untapped. Observation only."""
        from . import rules_audit
        if not rules_audit.enabled():
            return
        t = card.template
        if tapped or (t.enters_tapped and not t.untap_life_cost
                      and t.untap_max_other_lands < 0):
            ok = card.tapped
        elif t.untap_life_cost:
            ok = True       # the payment's choice decides it
        elif t.untap_max_other_lands >= 0:
            others = sum(1 for c in game.players[player_idx].battlefield
                         if c.template.is_land and c is not card)
            ok = card.tapped == (others > t.untap_max_other_lands)
        else:
            ok = not card.tapped
        rules_audit.check(
            "614.1d/land_entry", ok,
            f"{card.name} entered {'tapped' if card.tapped else 'untapped'}"
            f" (put {'tapped' if tapped else 'untapped'})")

    @staticmethod
    def land_entered(game: "GameState", card, player_idx: int) -> None:
        """What a land's entry sets off: its enter abilities (CR 603.6a: the
        ETB fan-out a land played reaches -- a Saga's first lore counter
        and chapter I (CR 714.3a), a registered enter handler, the typed
        enter triggers), then the statics that see it enter (an
        untap-on-enter watcher, a karoo's return, "lands you control enter
        untapped").

        Lands that entered together (a mass search) have their entries
        resolved one at a time. A land that a co-entrant's trigger already
        returned is skipped: it is no longer the object that entered (CR
        400.7), and its own enter triggers are not modelled past its
        leaving."""
        from .zone_transfer import TransferKind, fire_fanout
        if card.zone != "battlefield":
            return
        fire_fanout(game, card, TransferKind.ETB, controller=player_idx)
        LandManager.apply_land_etb_static(game, card, player_idx)
        LandManager.apply_lands_enter_untapped(game, card, player_idx)

    @staticmethod
    def put_land_onto_battlefield(game: "GameState", card, player_idx: int,
                                  from_zone: str, tapped: bool,
                                  cause: str) -> None:
        """A land an instruction puts onto the battlefield (one a search
        found, CR 701.19a; one from a hand, CR 305.4) enters
        (`enter_land`) and its entry sets off what it does
        (`land_entered`). Its caller fires landfall and its own triggers
        in their printed order."""
        LandManager.enter_land(game, card, player_idx, from_zone, tapped,
                               cause)
        LandManager.land_entered(game, card, player_idx)

    @staticmethod
    def put_land_from_library(game: "GameState", card, player_idx: int,
                              tapped: bool, cause: str) -> None:
        """A land a search found enters the battlefield under its searcher
        (`put_land_onto_battlefield` from the library)."""
        LandManager.put_land_onto_battlefield(game, card, player_idx,
                                              "library", tapped, cause)

    @staticmethod
    def note_library_search(game: "GameState", searcher_idx: int) -> None:
        """A player searched their library (CR 701.19): counted, and every
        "whenever an opponent searches their library" watcher triggers."""
        game.players[searcher_idx].library_searches_this_game += 1
        LandManager.trigger_library_search(game, searcher_idx)

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
