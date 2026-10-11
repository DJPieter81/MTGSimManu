"""
MTG Zone Manager
Centralized zone transition handling.

ALL card movements between zones MUST go through this manager.
This ensures:
  1. Replacement effects are checked (CR 614)
  2. Zone-change triggers fire (CR 603)
  3. State cleanup happens consistently (flags, counters, combat state)
  4. The game log is updated

Replaces the scattered pattern of:
    player.hand.remove(card)
    card.zone = "graveyard"
    player.graveyard.append(card)
"""
from __future__ import annotations
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState


class ZoneManager:
    """Handles all card movement between zones."""

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────────────

    def move_card(
        self,
        game: "GameState",
        card: "CardInstance",
        from_zone: str,
        to_zone: str,
        cause: str = "",
        controller_override: Optional[int] = None,
        transformed: bool = False,
        dying: bool = False,
    ) -> bool:
        """Move a card from one zone to another.

        This is the ONLY sanctioned way to change a card's zone.

        Args:
            game: The current game state.
            card: The card instance to move.
            from_zone: The zone the card is currently in.
            to_zone: The destination zone.
            cause: Human-readable reason for the move (for logging).
            controller_override: If set, change the card's controller on ETB.
            transformed: Put a double-faced card onto the battlefield with
                its back face up ("return it to the battlefield
                transformed", CR 712).
            dying: The death owner (`PermanentEffects._creature_dies`) is
                performing a creature's death; any other battlefield ->
                graveyard move of a creature is handed to it (CR 700.4).

        Returns:
            True if the move was performed, False if prevented.
        """
        owner = card.owner

        # A card physically sits in its OWNER's zone for every zone EXCEPT
        # the battlefield, where a stolen / opponent-cast permanent sits
        # under its CONTROLLER (CR 108.4 — control differs from ownership
        # only on the battlefield/stack). The SOURCE list must be located
        # where the card actually is; the DESTINATION still routes to the
        # owner (CR 400.3, handled below).
        source_owner = card.controller if from_zone == "battlefield" else owner

        # Validate: card should be in from_zone
        source_list = self._get_zone_list(game, source_owner, from_zone)
        if card not in source_list:
            # Card is not where we expect — find where it actually is,
            # across ALL players (not just the owner), so an owner!=
            # controller permanent on the controller's battlefield is found.
            located = self._find_card_location(game, card)
            if located is None:
                return False
            source_owner, from_zone = located
            source_list = self._get_zone_list(game, source_owner, from_zone)

        # ── A creature dies, whatever moves it (CR 700.4) ───────────
        # "Dies" means put into a graveyard from the battlefield: a
        # sacrifice paid as a cost or demanded by an effect is a death like
        # any other, so the death owner performs the move and its effects
        # (undying, persist, modular, the death count, dies triggers,
        # observers).
        if not dying and self._dies(card, from_zone, to_zone):
            game._creature_dies(card, cause=cause)
            return True
        was_creature = self._creature_on_battlefield(card, from_zone)

        # ── Graveyard-to-exile replacement (CR 614.1a, 614.6) ───────
        # Decided before the card leaves its zone: a replacement that
        # modifies how a permanent leaves the battlefield applies from
        # the game state before the event (CR 614.12, 603.10), so a
        # destroyed Rest in Peace exiles itself.
        actual_to = to_zone
        replaced_by = None
        if to_zone == "graveyard":
            replaced_by = self.graveyard_exile_source(game, card, from_zone)
            if replaced_by is not None:
                actual_to = "exile"

        # ── Remove from source zone ────────────────────────────────
        if card in source_list:
            source_list.remove(card)
        if from_zone == "exile":
            # CR 400.7: a card leaving exile is a new object; an effect
            # that named the exiled object (a permission to play it) no
            # longer names it.
            game.continuous_effects.forget_object(card.instance_id)

        # ── Clean up state when leaving battlefield ─────────────────
        if from_zone == "battlefield":
            # CR 702.139 revolt: "a permanent left the battlefield under your
            # control this turn". This is the ONE funnel every battlefield
            # departure passes through — fetch cracks, sacrifice, bounce,
            # exile, creature deaths — so the per-turn revolt tally advances
            # here exactly once and no caller counts per card. Credit the
            # permanent's controller (control, not ownership: CR 108.4).
            game.players[card.controller].permanents_left_battlefield_this_turn += 1
            self._cleanup_leaving_battlefield(card)

        # ── Per-turn discard accounting (CR 701.8a) ─────────────────
        # Every hand -> graveyard transition is a discard by definition
        # (cycling included, CR 702.29a); this is the ONE place the
        # per-turn counter advances, so no caller counts per card.  The
        # OWNER is credited — "cards you've discarded" is about whose
        # hand the card left, not who forced it.
        if from_zone == "hand" and to_zone == "graveyard":
            game.players[owner].cards_discarded_or_cycled_this_turn += 1

        # ── Add to destination zone ─────────────────────────────────
        # A permanent sits on its controller's battlefield (CR 108.4): one
        # put onto the battlefield under a player's control goes there.
        card.zone = actual_to
        dest_owner = (controller_override if actual_to == "battlefield"
                      and controller_override is not None else owner)
        dest_list = self._get_zone_list(game, dest_owner, actual_to)
        dest_list.append(card)

        self._after_graveyard_bound_move(game, card, actual_to, replaced_by)
        if was_creature and actual_to == "graveyard":
            self._audit_death(game, card, dying)

        # ── Handle entering battlefield ─────────────────────────────
        if actual_to == "battlefield":
            if controller_override is not None:
                card.controller = controller_override
            if transformed:
                card.is_transformed = True     # back face up (CR 712)
            card.enter_battlefield()
            card._game_state = game
            self._audit_entry_loyalty(game, card)
            self._audit_new_object(game, card)
        else:
            self._audit_front_face(game, card)

        # Log the move
        if cause:
            game.log.append(
                f"T{game.display_turn}: {card.name} moved "
                f"{from_zone} -> {actual_to} ({cause})"
            )

        return True

    def move_card_to_graveyard(
        self, game: "GameState", card: "CardInstance", cause: str = ""
    ) -> bool:
        """Convenience: move a card from its current zone to graveyard."""
        return self.move_card(game, card, card.zone, "graveyard", cause=cause)

    def move_card_to_exile(
        self, game: "GameState", card: "CardInstance", cause: str = ""
    ) -> bool:
        """Convenience: move a card from its current zone to exile."""
        return self.move_card(game, card, card.zone, "exile", cause=cause)

    def move_card_to_hand(
        self, game: "GameState", card: "CardInstance", cause: str = ""
    ) -> bool:
        """Convenience: move a card from its current zone to hand."""
        return self.move_card(game, card, card.zone, "hand", cause=cause)

    def move_card_to_battlefield(
        self, game: "GameState", card: "CardInstance",
        from_zone: str = "stack", cause: str = "",
        controller: Optional[int] = None,
    ) -> bool:
        """Convenience: move a card to the battlefield."""
        return self.move_card(
            game, card, from_zone, "battlefield",
            cause=cause, controller_override=controller,
        )

    def move_card_from_stack(
        self,
        game: "GameState",
        card: "CardInstance",
        to_zone: str,
        cause: str = "",
    ) -> bool:
        """Move a card that has already been popped from the stack.

        Spell resolution and counterspell targeting both pop the
        StackItem *before* moving the source card to its new home —
        so the card is not in any zone list at this point, even though
        ``card.zone`` is still ``"stack"``.  This method is the
        sanctioned exit path for those transitions:

        * Fires a ZONE_CHANGE event so CR 614 replacement effects
          (e.g. Rest in Peace → exile instead of graveyard) can
          redirect the destination.
        * Sets ``card.zone`` to the (possibly redirected) destination.
        * Appends the card to the destination zone list on the card's
          owner.

        Special case — ``to_zone == "expired_copy"`` (CR 707.10a):
        a resolved or countered spell *copy* ceases to exist; it never
        enters any zone list.  ``card.zone`` is set to ``"expired_copy"``
        and True is returned with no list mutation.

        Does NOT fire ETB or LTB events — those belong to
        ``move_card()``.  Stack-exit transitions are instant/sorcery
        resolution paths where neither ETB nor LTB applies.
        """
        owner = card.owner

        if to_zone == "expired_copy":
            # CR 707.10a: spell copies cease to exist on resolution or counter.
            # They don't enter any zone — mark as expired so callers can
            # detect this state and take no further list action.
            card.zone = to_zone
            return True

        actual_to = to_zone
        replaced_by = None
        if to_zone == "graveyard":
            replaced_by = self.graveyard_exile_source(game, card, "stack")
            if replaced_by is not None:
                actual_to = "exile"

        card.zone = actual_to
        dest_list = self._get_zone_list(game, owner, actual_to)
        dest_list.append(card)
        self._after_graveyard_bound_move(game, card, actual_to, replaced_by)

        if cause:
            game.log.append(
                f"T{game.display_turn}: {card.name} moved "
                f"stack -> {actual_to} ({cause})"
            )
        return True

    # ── Graveyard-to-exile replacement (CR 614.1a, 614.6, 700.4) ────

    def graveyard_exile_source(
        self, game: "GameState", card: "CardInstance", from_zone: str,
    ) -> Optional["CardInstance"]:
        """The battlefield permanent whose static graveyard-to-exile
        replacement covers `card` as it would be put into its owner's
        graveyard from `from_zone`, or None. The one matcher: both funnel
        exits and the death owner ask it.

        A rule (`CardTemplate.graveyard_exile_replacements`) covers an
        object by event scope (from anywhere; from the battlefield; a
        creature dying), by whose graveyard receives it (CR 404.2: its
        owner's), by colour, card type, token-ness and, for a death, by
        who controls the creature. A transformed permanent has only its
        back face's abilities (CR 712.8e)."""
        for player in game.players:
            for perm in player.battlefield:
                t = perm.template
                rules = getattr(t, "graveyard_exile_replacements", None)
                if not rules or perm.zone != "battlefield":
                    continue
                if getattr(perm, "is_transformed", False) and t.back_face_oracle:
                    continue
                for rule in rules:
                    if self._rule_covers(rule, card, from_zone,
                                         perm.controller):
                        return perm
        return None

    @staticmethod
    def _rule_covers(rule: dict, card: "CardInstance", from_zone: str,
                     ctrl: int) -> bool:
        on_battlefield = from_zone == "battlefield"
        types = (card.effective_card_types if on_battlefield
                 else card.template.card_types)
        type_names = {ct.value for ct in types}
        scope = rule["scope"]
        if scope == "dies" and not (on_battlefield
                                    and "creature" in type_names):
            return False
        if scope == "battlefield" and not on_battlefield:
            return False
        is_token = getattr(card, "is_token", False)
        if is_token and not rule["tokens"]:
            return False
        if rule["nontoken"] and is_token:
            return False
        whose = rule["whose"]
        if whose == "opponents" and card.owner == ctrl:
            return False
        if whose == "you" and card.owner != ctrl:
            return False
        by = rule["controlled_by"]
        if by == "opponents" and card.controller == ctrl:
            return False
        if by == "you" and card.controller != ctrl:
            return False
        if rule["colors"] is not None and not (
                {c.value for c in card.colors} & rule["colors"]):
            return False
        if rule["types"] is not None and not (type_names & rule["types"]):
            return False
        return True

    def _after_graveyard_bound_move(self, game: "GameState",
                                    card: "CardInstance", actual_to: str,
                                    replaced_by) -> None:
        """Log a replaced move; record a graveyard-to-exile static the
        engine refused to type (an unusual variant: "with a void counter
        on it", "and you gain 2 life") that saw a card arrive; and audit
        the arrival (CR 614.6)."""
        if replaced_by is not None:
            game.log.append(
                f"T{game.display_turn}: {card.name} is exiled instead of "
                f"going to a graveyard ({replaced_by.name}, CR 614.6)")
            return
        if actual_to != "graveyard":
            return
        for _p in game.players:
            for _perm in _p.battlefield:
                if (getattr(_perm.template,
                            "exiles_cards_bound_for_graveyard", False)
                        and not getattr(_perm.template,
                                        "graveyard_exile_replacements", None)):
                    from .effect_diagnostics import record_unhandled_effect
                    record_unhandled_effect(_perm.template.name, "replacement")
        self._audit_graveyard_arrival(game, card)

    @staticmethod
    def _audit_graveyard_arrival(game: "GameState",
                                 card: "CardInstance") -> None:
        """Rules audit (CR 614.6): no card reaches a graveyard while a
        static "if a card [or token] would be put into a / an opponent's
        graveyard from anywhere, exile it instead" covers it. Restated
        from the raw typed rules with no colour, type or controller
        filter, not through the matcher. Observes only."""
        from .rules_audit import enabled as _audit_on, check as _audit_check
        if not _audit_on():
            return
        token = getattr(card, "is_token", False)

        def _covers(rule, ctrl) -> bool:
            if (rule["scope"] != "anywhere" or rule["colors"]
                    or rule["types"] or rule["controlled_by"]
                    or rule["nontoken"] or (token and not rule["tokens"])):
                return False
            return {"any": True, "opponents": card.owner != ctrl,
                    "you": card.owner == ctrl}[rule["whose"]]

        source = next((perm for p in game.players for perm in p.battlefield
                       if any(_covers(r, perm.controller) for r in
                              getattr(perm.template,
                                      "graveyard_exile_replacements", None)
                              or ())), None)
        _audit_check("614.6/graveyard_exile_replacement", source is None,
                     f"{card.name} reached a graveyard while "
                     f"{getattr(source, 'name', '')} exiles it instead",
                     game=game)

    @staticmethod
    def _dies(card: "CardInstance", from_zone: str, to_zone: str) -> bool:
        """CR 700.4: is this move a creature dying -- a creature put into
        a graveyard from the battlefield?"""
        return (from_zone == "battlefield" and to_zone == "graveyard"
                and (card.effective_is_creature
                     or getattr(card, "is_animated", False)))

    @staticmethod
    def _creature_on_battlefield(card: "CardInstance", from_zone: str) -> bool:
        """Restated from the raw face fields for the audit: the object is
        a creature on the face it shows, or an animated land."""
        if from_zone != "battlefield":
            return False
        t = card.template
        back = bool(getattr(card, "is_transformed", False) and t.back_face_types)
        types = t.back_face_types if back else t.card_types
        return (any(getattr(ct, "name", "") == "CREATURE" for ct in types)
                or bool(getattr(card, "is_animated", False)))

    @staticmethod
    def _audit_death(game: "GameState", card: "CardInstance",
                     dying: bool) -> None:
        """Rules audit (CR 700.4): a creature that reached a graveyard from
        the battlefield died -- the death owner moved it. Observes only."""
        from .rules_audit import enabled as _audit_on, check as _audit_check
        if not _audit_on():
            return
        _audit_check("700.4/dies", dying,
                     f"{card.name} reached a graveyard from the battlefield "
                     f"without dying", game=game)

    @staticmethod
    def _audit_new_object(game: "GameState", card: "CardInstance") -> None:
        """Rules audit (CR 400.7, 611.2c): an object entering the
        battlefield is a new object that no earlier type-adding effect
        names -- it carries no added type or subtype. Observes only."""
        from .rules_audit import enabled as _audit_on, check as _audit_check
        if not _audit_on():
            return
        _audit_check("400.7/new_object_added_types",
                     not (getattr(card, "cem_types_added", None)
                          or getattr(card, "cem_subtypes_added", None)),
                     f"{card.name} entered carrying added types from an "
                     f"earlier object", game=game)

    @staticmethod
    def _audit_entry_loyalty(game: "GameState", card: "CardInstance") -> None:
        """Rules audit (CR 306.5b): a planeswalker enters with the loyalty
        printed on the face it shows -- its back face when it entered
        transformed. Restated from the raw face fields. Observes only."""
        from .rules_audit import enabled as _audit_on, check as _audit_check
        if not _audit_on():
            return
        t = card.template
        back = bool(card.is_transformed and t.back_face_types)
        types = t.back_face_types if back else t.card_types
        if not any(getattr(ct, "name", "") == "PLANESWALKER" for ct in types):
            return
        printed = (t.back_face_loyalty if back else t.loyalty) or 0
        _audit_check("306.5b/entry_loyalty", card.loyalty_counters == printed,
                     f"{card.name} entered with {card.loyalty_counters} loyalty "
                     f"(printed {printed})", game=game)

    @staticmethod
    def _audit_front_face(game: "GameState", card: "CardInstance") -> None:
        """Rules audit (CR 712.8a): a double-faced card that arrives in a
        zone other than the battlefield has only its front face. Observes
        only."""
        from .rules_audit import enabled as _audit_on, check as _audit_check
        if not _audit_on():
            return
        _audit_check("712.8a/front_face_off_battlefield",
                     not getattr(card, "is_transformed", False)
                     and getattr(card, "_front_template", None) is None,
                     f"{card.name} arrived in {card.zone} showing its back "
                     f"face", game=game)

    def _blink_zone_transition(
        self,
        game: "GameState",
        card: "CardInstance",
        to_controller: int,
    ) -> None:
        """Perform the zone bookkeeping for a blink effect (battlefield →
        exile → battlefield) as a single atomic operation.

        The caller is responsible for:
        - Calling ``game._handle_permanent_etb(card, to_controller)``
          to fire ETB effects after this returns.
        - Logging the blink event.

        Rules notes:
        - The card briefly "passes through" exile but never truly
          occupies it long enough for any player to receive priority —
          Ephemerate-style blinks are simultaneous leave-and-return.
          We therefore do *not* add the card to the exile list.
        - We call ``_cleanup_leaving_battlefield`` so all combat flags,
          counters, and temporary effects are reset before re-entry.
        - We call ``card.enter_battlefield()`` to re-apply summoning
          sickness and similar entry-state setup.
        - ``card.zone`` is updated to reflect the transit through
          ``"exile"`` and then ``"battlefield"``; both assignments live
          here inside zone_manager.py which is excluded from the
          zone-mutation ratchet (as the sanctioned funnel
          implementation).
        """
        from_controller = card.controller

        # ── Leave battlefield ───────────────────────────────────────
        if card in game.players[from_controller].battlefield:
            game.players[from_controller].battlefield.remove(card)
        self._cleanup_leaving_battlefield(card)
        # Transit through exile — no list entry (simultaneous return).
        card.zone = "exile"

        # ── Re-enter battlefield under new controller ───────────────
        card.controller = to_controller
        card.enter_battlefield()
        card._game_state = game
        card.zone = "battlefield"
        game.players[to_controller].battlefield.append(card)

    # ── Internal Helpers ────────────────────────────────────────────

    def _get_zone_list(
        self, game: "GameState", player_idx: int, zone_name: str
    ) -> List["CardInstance"]:
        """Get the list representing a player's zone."""
        player = game.players[player_idx]
        zone_map = {
            "library": player.library,
            "hand": player.hand,
            "battlefield": player.battlefield,
            "graveyard": player.graveyard,
            "exile": player.exile,
        }
        return zone_map.get(zone_name, [])

    def _find_card_zone(
        self, game: "GameState", card: "CardInstance"
    ) -> Optional[str]:
        """Find which zone a card is actually in (owner's zones only).

        Retained for callers that only need the zone name; prefer
        ``_find_card_location`` when the card may be controlled by a
        non-owner (its battlefield presence is under the controller).
        """
        located = self._find_card_location(game, card)
        return located[1] if located is not None else None

    def _find_card_location(
        self, game: "GameState", card: "CardInstance"
    ) -> Optional[tuple]:
        """Find (player_idx, zone_name) for where a card actually sits.

        Searches every player's zones, not just the owner's, so a
        permanent controlled by a non-owner (stolen / opponent-cast) is
        found on the CONTROLLER's battlefield rather than reported missing
        (CR 108.4). The owner's own zones are checked first as the common
        case.
        """
        order = [card.owner] + [
            i for i in range(len(game.players)) if i != card.owner
        ]
        for player_idx in order:
            player = game.players[player_idx]
            for zone_name in ["library", "hand", "battlefield",
                              "graveyard", "exile"]:
                if card in getattr(player, zone_name):
                    return (player_idx, zone_name)
        return None

    def _cleanup_leaving_battlefield(self, card: "CardInstance"):
        """Reset all battlefield-specific state when a card leaves."""
        # Combat state
        card.attacking = False
        card.blocking = None
        card.blocked_by = []

        # Damage
        card.damage_marked = 0

        # Temporary effects (until end of turn effects end when leaving)
        card.temp_power_mod = 0
        card.temp_toughness_mod = 0
        card.temp_keywords.clear()

        # Summoning sickness
        card.summoning_sick = False
        card.entered_battlefield_this_turn = False
        card.attacked_this_turn = False

        # Tapped state
        card.tapped = False

        # Alternative cast flags
        card._dashed = False
        card._evoked = False
        card._escaped = False

        # Instance tags (equipment, etc.)
        card.instance_tags.clear()

        # Note: continuous effects from this source are cleaned up
        # by ContinuousEffectsManager._cleanup_stale_effects() on next recalculate()

        # Counters are removed when leaving battlefield
        card.plus_counters = 0
        card.minus_counters = 0
        card.loyalty_counters = 0
        card.other_counters.clear()

        # Off the battlefield a double-faced card has only its front face
        # (CR 712.8a); a later entry shows the face that entry names.
        card.is_transformed = False

        # Types an effect added named the object that left (CR 400.7).
        card.cem_types_added = set()
        card.cem_subtypes_added = set()

        # A modal double-faced card played as its back face is its front
        # face again off the battlefield (CR 712.8a).
        front = getattr(card, "_front_template", None)
        if front is not None:
            card.template = front
            card._front_template = None

        # Clear game state reference
        card._game_state = None
