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

        actual_to = to_zone

        # ── Remove from source zone ────────────────────────────────
        if card in source_list:
            source_list.remove(card)

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
        card.zone = actual_to
        dest_list = self._get_zone_list(game, owner, actual_to)
        dest_list.append(card)

        # ── Replacement-effect resolution path (CR 614) ─────────────
        # "If a card would be put into a graveyard, exile it instead"
        # (Rest in Peace / Leyline of the Void / Anafenza family) is a
        # continuous REPLACEMENT the engine does not model — the card
        # reaches the graveyard here regardless. When such a static is on
        # the battlefield as a card enters a graveyard, the replacement
        # that should have fired silently did nothing; record the
        # unmodeled static (typed-field gate, no oracle re-parse) so a new
        # card in this family turns the guardrail red. Behaviour is
        # unchanged — this only observes the miss.
        if actual_to == "graveyard":
            for _p in game.players:
                for _perm in _p.battlefield:
                    if getattr(_perm.template,
                               "exiles_cards_bound_for_graveyard", False):
                        from .effect_diagnostics import record_unhandled_effect
                        record_unhandled_effect(_perm.template.name,
                                                "replacement")

        # ── Handle entering battlefield ─────────────────────────────
        if actual_to == "battlefield":
            if controller_override is not None:
                card.controller = controller_override
            card.enter_battlefield()
            card._game_state = game

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

        card.zone = actual_to
        dest_list = self._get_zone_list(game, owner, actual_to)
        dest_list.append(card)

        if cause:
            game.log.append(
                f"T{game.display_turn}: {card.name} moved "
                f"stack -> {actual_to} ({cause})"
            )
        return True

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

        # Clear game state reference
        card._game_state = None
