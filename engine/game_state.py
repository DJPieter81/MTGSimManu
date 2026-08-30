"""
MTG Game State - v2 with full mechanics
Core game state management: players, zones, turn structure, and game loop.
Implements proper MTG turn phases with priority passing.

v2 additions:
- Storm copies
- Cascade chains
- Living End (graveyard/battlefield swap)
- Reanimation (Goryo's Vengeance, Persist, etc.)
- Token generation
- Energy counters (produce + spend)
- Planeswalker loyalty abilities
- Prowess triggers
- Annihilator triggers
- Undying / Persist on death
- Ritual mana (spells that add mana to pool)
"""
from __future__ import annotations
import random
import re
from typing import Dict, List, Optional, Tuple, Set, Any, Callable
from enum import Enum
from .mana import ManaPool, ManaCost, Color
from .cards import (
    CardTemplate, CardInstance, CardType, Keyword, Ability, AbilityType, Supertype
)
from .stack import Stack, StackItem, StackItemType
from .zone_manager import ZoneManager
from .sba_manager import SBAManager
from .turn_manager import TurnManager, TurnStep
from .card_effects import EFFECT_REGISTRY, EffectTiming
from .continuous_effects import ContinuousEffectsManager
from .delayed_triggers import (
    DelayedTrigger, DelayedTriggerQueue, DelayedTriggerStep,
)
from .callbacks import GameCallbacks, DefaultCallbacks
from .constants import (
    STARTING_LIFE, MAX_HAND_SIZE, MAX_TURNS, SBA_MAX_ITERATIONS,
    FETCH_LAND_LIFE_COST,
)
# PlayerState, TOKEN_DEFS, and _parse_planeswalker_abilities were extracted
# to engine/player_state.py. Re-exported here so existing importers of
# `engine.game_state.PlayerState` (14 call sites across ai/ and tests/) and
# the late `from .game_state import _parse_planeswalker_abilities` in
# game_runner.py continue to resolve without edits.
from .player_state import PlayerState, TOKEN_DEFS, _parse_planeswalker_abilities
from .mana_payment import ManaPayment
from .land_manager import LandManager
from .cast_manager import CastManager
from .spell_resolution import ResolutionManager
from .permanent_effects import PermanentEffects
from .triggers import TriggerManager
from .planeswalker_manager import PlaneswalkerManager
from .cycling import CyclingManager


class Phase(Enum):
    UNTAP = "untap"
    UPKEEP = "upkeep"
    DRAW = "draw"
    MAIN1 = "main1"
    BEGIN_COMBAT = "begin_combat"
    DECLARE_ATTACKERS = "declare_attackers"
    DECLARE_BLOCKERS = "declare_blockers"
    COMBAT_DAMAGE = "combat_damage"
    END_COMBAT = "end_combat"
    MAIN2 = "main2"
    END_STEP = "end_step"
    CLEANUP = "cleanup"


PHASE_ORDER = [
    Phase.UNTAP, Phase.UPKEEP, Phase.DRAW,
    Phase.MAIN1,
    Phase.BEGIN_COMBAT, Phase.DECLARE_ATTACKERS,
    Phase.DECLARE_BLOCKERS, Phase.COMBAT_DAMAGE, Phase.END_COMBAT,
    Phase.MAIN2,
    Phase.END_STEP, Phase.CLEANUP,
]


class GameState:
    """Complete state of an MTG game between two players."""

    def __init__(self, rng: random.Random = None, callbacks: GameCallbacks = None):
        self.players: List[PlayerState] = [
            PlayerState(player_idx=0),
            PlayerState(player_idx=1),
        ]
        self.callbacks: GameCallbacks = callbacks or DefaultCallbacks()
        self.stack = Stack()
        self.active_player: int = 0
        self.priority_player: int = 0
        self.current_phase: Phase = Phase.UNTAP
        self.turn_number: int = 1  # internal half-turn counter (increments each player turn)
        self.game_over: bool = False
        self.winner: Optional[int] = None
        self.rng = rng or random.Random()
        self._next_instance_id: int = 1
        self._triggers_queue: List[Tuple[Ability, CardInstance, int]] = []
        # Global storm count (all spells cast this turn by both players)
        self._global_storm_count: int = 0
        # Delayed one-shot triggers: "exile it at the beginning of the
        # next end step" (temporary-reanimation / put-onto-battlefield
        # riders). Entries are (card, controller, battlefield_entry_seq):
        # the seq captures which OBJECT the rider tracks (CR 400.7) — if
        # the card re-enters the battlefield the rider goes stale.
        self._end_of_turn_exiles: List[Tuple[CardInstance, int, int]] = []
        # Delayed sacrifice rider: "sacrifice at the beginning of the next
        # end step" (Mobilize, CR 702 Mobilize reminder text). Entries are
        # CardInstance objects; processed and cleared in end_of_turn_cleanup.
        self._end_of_turn_sacrifices: List["CardInstance"] = []
        # General delayed-trigger queue (CR 603.7) — "at the beginning of
        # <a later step>, <effect>". The two lists above are the engine's
        # two pre-queue special cases (end-step-only, effect hard-coded at
        # the firing site); everything new goes here, where the timing rule
        # and the fire-once guarantee are stated once. See
        # engine/delayed_triggers.py for the measured card class.
        self.delayed_triggers = DelayedTriggerQueue()
        # Game log
        self.log: List[str] = []
        self.max_turns: int = MAX_TURNS
        # ── New rules engine modules ──
        self.zone_mgr = ZoneManager()
        self.sba_mgr = SBAManager(self.zone_mgr)
        self.turn_mgr = TurnManager()
        self.continuous_effects = ContinuousEffectsManager()

    def next_instance_id(self) -> int:
        iid = self._next_instance_id
        self._next_instance_id += 1
        return iid

    def register_end_of_turn_sacrifice(self, card: "CardInstance") -> None:
        """Register a token for 'sacrifice at the beginning of the next end
        step' (Mobilize CR 702).  Only the explicit CardInstance is sacrificed;
        other tokens are unaffected.
        """
        self._end_of_turn_sacrifices.append(card)

    def register_delayed_trigger(self, trigger: "DelayedTrigger") -> None:
        """Queue a delayed triggered ability (CR 603.7).

        The trigger's `effect` closes over everything it needs at creation
        time, so it keeps working after its source has left the battlefield
        — CR 603.7d, and the reason a self-sacrificing source (Mishra's
        Bauble pays its own cost by sacrificing itself) still delivers.
        """
        self.delayed_triggers.register(trigger)

    def fire_delayed_triggers(self, step: "DelayedTriggerStep") -> int:
        """Drain every delayed trigger due at `step`. Returns how many fired.

        Called from the two places the turn loop reaches those steps — the
        upkeep step in `GameRunner`, the end step in
        `TurnManager.end_of_turn_cleanup` — so a delayed effect fires at its
        printed moment no matter which subsystem created it.
        """
        return self.delayed_triggers.fire_for_step(self, step)

    def register_end_of_turn_exile(self, card: CardInstance,
                                   controller: int) -> None:
        """Register a delayed 'exile it at the beginning of the next end
        step' rider against the card's CURRENT battlefield object.

        CR 400.7: the rider tracks an object, not a card. Capturing
        `battlefield_entry_seq` at registration lets the end step detect
        that the object it was tracking left the battlefield (blink,
        bounce, death) and drop the rider even if the same CardInstance
        is back on the battlefield as a new object.
        """
        self._end_of_turn_exiles.append(
            (card, controller, card.battlefield_entry_seq)
        )

    def get_card_by_id(self, instance_id: int) -> Optional[CardInstance]:
        """Find a card instance by its unique ID across all zones."""
        for player in self.players:
            for zone in [player.battlefield, player.hand, player.graveyard,
                         player.exile, player.library]:
                for card in zone:
                    if card.instance_id == instance_id:
                        return card
        for item in self.stack.items:
            if item.source.instance_id == instance_id:
                return item.source
        return None

    # ─── ORACLE-DRIVEN CONTINUOUS HATE EFFECTS ─────────────────────
    # Grafdigger's Cage (and any functional reprint) exposes two
    # continuous effects while on the battlefield. We detect them via
    # oracle-text patterns rather than card names so future hate cards
    # with the same clause text are gated automatically.
    #
    # Clause 1: "Creature cards in graveyards and libraries can't enter
    # the battlefield." → blocks reanimation and cast-to-play of
    # creatures sourced from graveyard or library.
    #
    # Clause 2: "Players can't cast spells from graveyards or
    # libraries." → blocks flashback, escape, and similar non-hand
    # cast routes for any spell (creature or not).

    def _gy_reanimation_hate_source(self) -> Optional[CardInstance]:
        """Return the first permanent on any battlefield whose oracle
        text bans creature cards from entering from graveyards/libraries.
        None if no such permanent is in play."""
        for player in self.players:
            for card in player.battlefield:
                # Grafdigger's Cage pattern: typed field parsed at DB load.
                if getattr(card.template, 'prevents_graveyard_etb', False):
                    return card
        return None

    def _gy_library_cast_hate_source(self) -> Optional[CardInstance]:
        """Return the first permanent on any battlefield that PRINTS the
        static "Players can't cast spells from graveyards or libraries"
        (the Grafdigger's Cage clause). None if no such permanent is in
        play.

        Reads the narrow, parse-once `prevents_graveyard_casting` field —
        NOT `has_graveyard_hate`. The latter is a deliberately broad
        sideboard-advice predicate matched by 446 Modern permanents
        (anything whose oracle exiles a graveyard); using it here made
        every graveyard-REMOVAL permanent a symmetric, permanent Cage that
        switched off flashback and escape for both players, its own
        controller included. Removal takes the fuel away when the ability
        is ACTIVATED; it bans no cast. See
        tests/test_graveyard_cast_prevention_static.py.
        """
        for player in self.players:
            for card in player.battlefield:
                if getattr(card.template, 'prevents_graveyard_casting',
                           False):
                    return card
        return None

    # ─── Sorcery-speed-lockout static-effect registry (R4) ──────────
    # Per-game registry of player indices currently restricted to
    # sorcery-speed casts by an opposing battlefield permanent. Rebuilt
    # on demand from battlefield permanents whose classifier tag is
    # ``Tag.SORCERY_SPEED_LOCKOUT`` (cached in
    # ``decks/gameplans/_oracle_classifier.json``). Consulted by
    # ``CastManager.can_cast`` — opponents in the set cannot cast
    # outside sorcery-speed windows. Card-name branches, oracle-text
    # parsing, and per-card flags are forbidden by the abstraction
    # contract; the classifier tag IS the dispatch.
    def _sorcery_speed_lockout_set(self) -> set[int]:
        """Return player indices currently restricted to sorcery-speed
        casts (R4).

        For every battlefield permanent whose classifier tag includes
        ``Tag.SORCERY_SPEED_LOCKOUT``, the permanent's *opponents* are
        added to the set. No oracle-text parse, no card-name check.
        """
        # Late import: ai.oracle_classifier is in the ai/ layer; an
        # engine module importing from ai/ is acceptable because the
        # classifier is a pure-data loader (no scoring/strategy logic).
        from ai.oracle_classifier import Tag, tags_for

        restricted: set[int] = set()
        for player in self.players:
            for card in player.battlefield:
                if Tag.SORCERY_SPEED_LOCKOUT in tags_for(card.template.name):
                    for opp_idx in range(len(self.players)):
                        if opp_idx != card.controller:
                            restricted.add(opp_idx)
        return restricted

    def setup_game(self, deck1: List[CardTemplate], deck2: List[CardTemplate],
                    forced_first_player: Optional[int] = None):
        """Initialize the game with two decks.

        forced_first_player: if given (0 or 1) sets that player as the
        active/priority player, bypassing the opening die roll. Used by
        Bo3 match orchestration so the loser of game N chooses who plays
        game N+1 (CR 103.2). None preserves legacy random-die behaviour
        for single-game runs.
        """
        for template in deck1:
            card = CardInstance(
                template=template, owner=0, controller=0,
                instance_id=self.next_instance_id(), zone="library",
            )
            card._game_state = self
            # Innate flashback (Lava Dart, Lingering Souls, etc.)
            if 'flashback' in template.tags:
                card.has_flashback = True
            self.players[0].library.append(card)

        for template in deck2:
            card = CardInstance(
                template=template, owner=1, controller=1,
                instance_id=self.next_instance_id(), zone="library",
            )
            card._game_state = self
            if 'flashback' in template.tags:
                card.has_flashback = True
            self.players[1].library.append(card)

        self.rng.shuffle(self.players[0].library)
        self.rng.shuffle(self.players[1].library)

        for p_idx in range(2):
            self.draw_cards(p_idx, 7)

        if forced_first_player is not None:
            self.active_player = forced_first_player
        else:
            self.active_player = self.rng.randint(0, 1)
        self.priority_player = self.active_player

    def draw_cards(self, player_idx: int, count: int) -> List[CardInstance]:
        """Draw cards from library to hand (CR 121.1).

        Per-card trigger fan-out is owned by
        `engine.zone_transfer._fire_on_draw_triggers` (registered for
        `TransferKind.DRAW`). The fan-out reads classifier tags
        (`ON_DRAW_DAMAGE`, `ON_OPP_DRAW_LIFE_LOSS`,
        `ON_OWN_DRAW_LIFE_GAIN`) — no inline regex matching on
        oracle text lives here. The legacy regex chain was the
        R1+M1-engine bug surface from the 2026-05-16 audit.
        """
        from .zone_transfer import TransferKind, transfer
        player = self.players[player_idx]
        drawn: List[CardInstance] = []
        for _ in range(count):
            if not player.library:
                self.game_over = True
                self.winner = 1 - player_idx
                self.log.append(f"P{player_idx+1} loses: empty library")
                return drawn
            card = player.library.pop(0)
            player.cards_drawn_this_turn += 1
            drawn.append(card)
            # The pop above already detached the card from library;
            # transfer's `_remove_from_zone` is tolerant of that. The
            # call places the card in hand and runs the DRAW fan-out.
            transfer(self, card, src_zone="library", dst_zone="hand",
                     kind=TransferKind.DRAW, controller=player_idx)
            if self.game_over:
                # A per-draw trigger's damage/life loss ended the game
                # (CR 704.5a, checked inside the DRAW fan-out). The
                # remaining draws of this effect never happen.
                break
        return drawn

    def surveil(self, player_idx: int, n: int) -> List[CardInstance]:
        """CR 701.42 — Surveil N.

        "Look at the top N cards of your library, then put any number
        of them into your graveyard and the rest on top of your
        library in any order."

        The deterministic AI policy bins every surveiled card to the
        graveyard. This matches the existing convention from the
        creature-spell-cast surveil branch (which always binned the
        top to GY for delirium / GY-payoff density) and is the right
        default for the simulator's current decision layer (no card
        is "saved" because we have no surveil-evaluator yet; a future
        AI hook can refine via `callbacks.choose_surveil_bins`).

        Class size of callers: every "When ~ enters, surveil N" land
        (the surveil-dual cycle — Meticulous Archive, Elegant Parlor,
        Thundering Falls, Hedge Maze, Underground Mortuary, Raucous
        Theater, Commercial District, Undercity Sewers, Shadowy
        Backstreet, Lush Portico — and any future printing) plus
        every "Whenever you cast a noncreature spell, surveil N"
        creature (DRC, Lightshell Duo, Garland, Cruel Witness).

        Returns the list of cards that were binned. Empty if the
        library was empty.
        """
        player = self.players[player_idx]
        binned: List[CardInstance] = []
        # Look at up to N cards from the top. Library is a list with
        # index 0 == top, matching the engine's convention.
        looked = player.library[:n]
        # Policy: bin all of them. The library indices shift as we
        # remove, so iterate over the slice (which is a separate list).
        for card in looked:
            self.zone_mgr.move_card(self, card, "library", "graveyard")
            binned.append(card)
        if binned:
            names = ", ".join(c.name for c in binned)
            self.log.append(
                f"T{self.display_turn} P{player_idx+1}: "
                f"surveil {n} → {names} to GY"
            )
        return binned

    def scry(self, player_idx: int, n: int) -> List[CardInstance]:
        """CR 701.18 — Scry N.

        "Look at the top N cards of your library. Put any number of them
        on the bottom of your library in any order and the rest on top
        of your library in any order."

        Deterministic AI policy: put card on the bottom when it is a land
        AND the player already controls SCRY_LAND_STABILITY_THRESHOLD or
        more lands on the battlefield (mana-stable, doesn't need more
        sources right now). Everything else goes on top.

        This is principled rather than card-name-keyed — any card type
        that is a land gets the filter; spells always go on top. A future
        AI hook can refine via `callbacks.choose_scry_ordering` once the
        game has a full hand-evaluation pass.

        Class size of callers: Opt (scry 1 before draw), Serum Visions
        (scry 2 after draw), Deliberate (scry 2), Omen of the Sea (scry
        2 on ETB and via activation), Telling Time (scry 1), and every
        other Modern-legal card whose oracle text contains the scry
        keyword — dozens of blue cantrips, planeswalker abilities, and
        modal cards.

        Returns the list of cards sent to the bottom. Empty if library
        was empty.
        """
        # Rules constant: minimum lands in play to consider the player
        # mana-stable for scry filtering purposes. At 4+ lands most
        # decks have all the mana they need; additional lands have
        # lower marginal value than any spell. This matches the same
        # threshold used in the AI's land-drop desperation logic and
        # is derivable from "turn 4 is typically when most Modern plans
        # come online", making 4 lands the natural saturation point.
        SCRY_LAND_STABILITY_THRESHOLD = 4

        player = self.players[player_idx]
        if not player.library or n <= 0:
            return []

        # Look at the top N cards (or fewer if library is small)
        look_count = min(n, len(player.library))
        looked = player.library[:look_count]

        # Policy: keep spells on top; bottom-deck lands when mana-stable.
        land_count = sum(1 for c in player.battlefield if c.template.is_land)
        mana_stable = land_count >= SCRY_LAND_STABILITY_THRESHOLD

        top_cards: List[CardInstance] = []
        bottom_cards: List[CardInstance] = []
        for card in looked:
            if card.template.is_land and mana_stable:
                bottom_cards.append(card)
            else:
                top_cards.append(card)

        # Rebuild library: top_cards on top, rest of library unchanged,
        # bottom_cards appended at the bottom.
        rest = player.library[look_count:]
        player.library = top_cards + rest + bottom_cards

        if looked:
            looked_names = ", ".join(c.name for c in looked)
            bottom_desc = (", ".join(c.name for c in bottom_cards)
                           if bottom_cards else "none")
            self.log.append(
                f"T{self.display_turn} P{player_idx+1}: "
                f"scry {n} ({looked_names}) → bottom: {bottom_desc}"
            )
        return bottom_cards

    # ─── MANA SYSTEM ─────────────────────────────────────────────

    def tap_lands_for_mana(self, player_idx: int, cost: ManaCost,
                           card_name: str = None,
                           held_instant_colors: Optional[set] = None,
                           exclude_instance_id: Optional[int] = None) -> bool:
        """Delegate to ManaPayment.tap_lands_for_mana.

        held_instant_colors (Bundle 3 A5): optional set of color codes
        the AI wants preserved (colors of held instants / flash
        permanents). When supplied, among otherwise-equivalent land
        orderings the engine prefers the one that leaves these colors
        available untapped. Engine stays neutral when `None`.
        """
        return ManaPayment.tap_lands_for_mana(
            self, player_idx, cost, card_name=card_name,
            held_instant_colors=held_instant_colors,
            exclude_instance_id=exclude_instance_id,
        )

    def can_cast(self, player_idx: int, card: CardInstance) -> bool:
        return CastManager.can_cast(self, player_idx, card)

    def can_suspend(self, player_idx: int, card: CardInstance) -> bool:
        """LE-E2: is this card suspend-castable by player_idx?"""
        return CastManager.can_suspend(self, player_idx, card)

    def suspend_card(self, player_idx: int, card: CardInstance) -> bool:
        """LE-E2: pay the suspend cost and exile the card with time counters."""
        return CastManager.suspend_card(self, player_idx, card)

    def tick_suspend_upkeep(self, player_idx: int) -> None:
        """LE-E2: upkeep hook — decrement one counter on each suspended
        card the player controls; when the last is removed, cast for free."""
        CastManager.tick_suspend_upkeep(self, player_idx)

    def play_land(self, player_idx: int, card: CardInstance):
        LandManager.play_land(self, player_idx, card)

    def _crack_fetchland(self, player_idx: int, fetch_card: CardInstance):
        LandManager.crack_fetchland(self, player_idx, fetch_card)

    def _trigger_library_search(self, searcher_idx: int):
        LandManager.trigger_library_search(self, searcher_idx)

    def _trigger_landfall(self, player_idx: int):
        LandManager.trigger_landfall(self, player_idx)

    def equip_creature(self, player_idx: int, equipment: CardInstance,
                       creature: CardInstance) -> bool:
        """Equip an equipment to a creature. Costs mana (equip_cost).

        In real MTG, equipping is a sorcery-speed activated ability that
        costs mana and attaches the equipment to a creature you control.
        When the equipped creature dies, the equipment stays on the
        battlefield unattached.
        """
        player = self.players[player_idx]
        template = equipment.template

        # Validate
        if equipment not in player.battlefield:
            return False
        if creature not in player.creatures:
            return False
        if template.equip_cost is None:
            return False

        # Check mana
        available = player.untapped_mana_capacity() + player.mana_pool.total() + player._tron_mana_bonus()
        if available < template.equip_cost:
            return False

        # Pay mana — use pool first, then tap lands
        remaining = template.equip_cost
        pool_total = player.mana_pool.total()
        if pool_total > 0:
            from_pool = min(pool_total, remaining)
            # Remove generic mana from pool (colorless first, then colored)
            to_remove = from_pool
            for attr in ["colorless", "green", "red", "black", "blue", "white"]:
                avail = getattr(player.mana_pool, attr)
                take = min(avail, to_remove)
                if take > 0:
                    setattr(player.mana_pool, attr, avail - take)
                    to_remove -= take
                if to_remove <= 0:
                    break
            remaining -= from_pool
        for land in player.untapped_lands:
            if remaining <= 0:
                break
            land.tapped = True
            remaining -= 1

        if 'equipment' in getattr(template, 'tags', set()) or 'pump' in getattr(template, 'tags', set()):
            # Use instance_id-based tag so stacking the same equipment works correctly.
            # Format: equipped_{equipment.instance_id}  (unique per equipment object)
            equip_tag = f"equipped_{equipment.instance_id}"
            # Remove this specific equipment from any creature it was previously on
            for c in player.creatures:
                c.instance_tags.discard(equip_tag)
            # Attach to new creature
            creature.instance_tags.add(equip_tag)
            # Mark equipment as attached
            equipment.instance_tags.discard("equipment_unattached")
            equipment.instance_tags.add("equipment_attached")

        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                        f"Equip {equipment.name} to {creature.name} "
                        f"(cost {template.equip_cost})")
        return True

    def cast_spell(self, player_idx: int, card: CardInstance,
                   targets: List[int] = None, free_cast: bool = False) -> bool:
        return CastManager.cast_spell(
            self, player_idx, card, targets, free_cast
        )

    # ─── SPELL RESOLUTION ────────────────────────────────────────

    def resolve_stack(self):
        ResolutionManager.resolve_stack(self)

    def _handle_permanent_etb(self, card: CardInstance, controller: int,
                               item: StackItem = None):
        ResolutionManager._handle_permanent_etb(self, card, controller, item=item)

    def _handle_storm(self, item: StackItem):
        CastManager._handle_storm(self, item)

    # ─── CASCADE ─────────────────────────────────────────────────

    def _handle_cascade(self, item: StackItem):
        CastManager._handle_cascade(self, item)

    def _resolve_living_end(self, controller: int):
        ResolutionManager._resolve_living_end(self, controller)

    def reanimate(self, *args, **kwargs):
        return PermanentEffects.reanimate(self, *args, **kwargs)

    def animate_land(self, *args, **kwargs):
        return PermanentEffects.animate_land(self, *args, **kwargs)

    def create_token(self, *args, **kwargs):
        return PermanentEffects.create_token(self, *args, **kwargs)

    def activate_planeswalker(self, *args, **kwargs):
        return PlaneswalkerManager.activate_planeswalker(self, *args, **kwargs)

    def _apply_land_etb_static(self, permanent: "CardInstance",
                               controller: int):
        LandManager.apply_land_etb_static(self, permanent, controller)

    # Backward-compatible alias — the untap watcher now runs inside the
    # uniform land-entry hook (with the karoo return clause after it).
    def _apply_untap_on_enter_triggers(self, permanent: "CardInstance",
                                        controller: int):
        LandManager.apply_land_etb_static(self, permanent, controller)

    def _apply_lands_enter_untapped(self, land: "CardInstance",
                                     controller: int):
        LandManager.apply_lands_enter_untapped(self, land, controller)

    # ─── ENERGY SYSTEM ───────────────────────────────────────────

    def produce_energy(self, *args, **kwargs):
        return PermanentEffects.produce_energy(self, *args, **kwargs)

    def spend_energy_for_effect(self, *args, **kwargs):
        return PermanentEffects.spend_energy_for_effect(self, *args, **kwargs)

    def gain_life(self, *args, **kwargs):
        return PermanentEffects.gain_life(self, *args, **kwargs)
    def _execute_spell_effects(self, item: StackItem):
        ResolutionManager._execute_spell_effects(self, item)

    def _blink_permanent(self, card: CardInstance, controller: int):
        ResolutionManager._blink_permanent(self, card, controller)

    def _creature_dies(self, creature: CardInstance):
        PermanentEffects._creature_dies(self, creature)

    def _permanent_destroyed(self, permanent: CardInstance):
        PermanentEffects._permanent_destroyed(self, permanent)

    def _exile_permanent(self, permanent: CardInstance):
        PermanentEffects._exile_permanent(self, permanent)

    def _bounce_permanent(self, permanent: CardInstance):
        PermanentEffects._bounce_permanent(self, permanent)

    def _force_discard(self, player_idx: int, count: int, self_discard: bool = False):
        """Discard cards from hand. The per-card choice is delegated
        to self.callbacks.choose_discard — the AI wire-up installs
        ai.discard_advisor.choose_discard, the default picks the
        highest-CMC card.

        self_discard=True means the player chose to discard (Faithful
        Mending, etc.). self_discard=False means an opponent forced
        the discard (Thoughtseize, etc.).

        Bug E2 fix: opponent-forced discard (self_discard=False) routes
        through ai.discard_advisor, which delegates scoring to
        ai.ev_evaluator.choose_card_to_strip. The caster picks by THREAT
        to itself rather than raw printed CMC. Engine layer stays
        scoring-free; all heuristics live in ai/.
        """
        player = self.players[player_idx]
        for _ in range(min(count, len(player.hand))):
            if not player.hand:
                break
            card = self.callbacks.choose_discard(
                self, player_idx, list(player.hand), self_discard)
            if card is None:
                # Opponent-forced discard on an all-lands hand: the
                # Thoughtseize-text "nonland card" clause means nothing
                # else can be discarded. Stop the loop.
                break
            self.zone_mgr.move_card(
                self, card, "hand", "graveyard",
                cause="forced discard" if not self_discard else "discard"
            )

    # ─── TRIGGERS ────────────────────────────────────────────────

    def trigger_etb(self, card: CardInstance, controller: int):
        TriggerManager.trigger_etb(self, card, controller)

    def trigger_attack(self, attacker: CardInstance, controller: int):
        TriggerManager.trigger_attack(self, attacker, controller)

    def process_triggers(self):
        TriggerManager.process_triggers(self)

    def queue_trigger(self, trigger_reg):
        TriggerManager.queue_trigger(self, trigger_reg)

    def check_state_based_actions(self) -> bool:
        """CR 704.3 fixpoint: check and perform state-based actions,
        repeating until a pass performs none. Bounded by
        SBA_MAX_ITERATIONS as a safety valve (same constant/pattern as
        the dead SBAManager.check_and_perform_loop this ports).

        Needed now that ContinuousEffectsManager (0b) is live: an
        earlier-checked rule in a single pass (e.g. lethal-damage/
        toughness, rules g/h) can miss a condition caused by a
        later rule in the SAME pass (e.g. legend rule, j — sacrificing
        a permanent whose continuous effect was keeping a DIFFERENT
        creature alive). A single pass only catches that on the NEXT
        external check_state_based_actions() call; the fixpoint loop
        catches it within this one.
        """
        actions_taken = False
        iterations = 0
        while iterations < SBA_MAX_ITERATIONS:
            performed = self._check_sba_once()
            if not performed:
                break
            actions_taken = True
            iterations += 1
        return actions_taken

    def _check_sba_once(self) -> bool:
        """Single CR 704.3 pass. Rules with a single shared
        implementation are SBAManager statics called from here AND
        from the dead SBAManager.check_and_perform_loop:
        perform_poison_check (704.5c), perform_deathtouch_check
        (704.5i). Creature death routes through _creature_dies so
        Undying/Persist replacement is preserved. End-state (single
        CR 704.3 fixpoint over shared statics):
        docs/proposals/resolver_sba_unification.md §6.
        """
        actions_taken = False

        # Continuous effects (0b) must be fresh before checking P/T-
        # dependent SBAs (lethal damage, toughness) below — a
        # retraction from a rule checked LATER in the previous pass
        # (e.g. legend rule) must be visible to those checks THIS pass.
        self.continuous_effects.recalculate(self)

        # Player life totals (SBA 704.5a)
        for i, player in enumerate(self.players):
            if player.life <= 0 and not self.game_over:
                self.game_over = True
                self.winner = 1 - i
                self.log.append(f"P{i+1} loses: life total {player.life}")
                actions_taken = True

        if self.game_over:
            return actions_taken

        # Lethal poison (SBA 704.5c) — single implementation in SBAManager
        if SBAManager.perform_poison_check(self):
            actions_taken = True

        if self.game_over:
            return actions_taken

        # Creatures with lethal damage (use legacy path for Undying/Persist)
        for player in self.players:
            dead_creatures = [c for c in player.creatures if c.is_dead]
            for creature in dead_creatures:
                self._creature_dies(creature)
                actions_taken = True

        # Creatures with 0 or less toughness
        for player in self.players:
            zero_tough = [c for c in player.creatures
                          if c.toughness <= 0 and c.zone == "battlefield"]
            for creature in zero_tough:
                self._creature_dies(creature)
                actions_taken = True

        # Creatures dealt damage by a deathtouch source (SBA 704.5i) —
        # single implementation in SBAManager; routes death through
        # _creature_dies so Undying/Persist are preserved.
        if SBAManager.perform_deathtouch_check(self):
            actions_taken = True

        # Planeswalkers with 0 or less loyalty (SBA 704.5p)
        for player in self.players:
            dead_pws = [c for c in player.planeswalkers
                        if c.loyalty_counters <= 0 and c.zone == "battlefield"]
            for pw in dead_pws:
                self.zone_mgr.move_card(
                    self, pw, "battlefield", "graveyard",
                    cause="SBA 704.5p: zero loyalty"
                )
                actions_taken = True

        # Tokens off the battlefield cease to exist (SBA 704.5f) —
        # single implementation lives in SBAManager.
        if SBAManager.perform_token_cleanup(self):
            actions_taken = True

        # Legend rule (SBA 704.5j)
        for player in self.players:
            legendaries_by_name = {}
            for c in list(player.battlefield):
                if Supertype.LEGENDARY in c.template.supertypes:
                    name = c.template.name
                    if name not in legendaries_by_name:
                        legendaries_by_name[name] = []
                    legendaries_by_name[name].append(c)

            for name, cards in legendaries_by_name.items():
                if len(cards) > 1:
                    cards.sort(key=lambda c: c.instance_id)
                    for old in cards[:-1]:
                        if old.zone == "battlefield":
                            self.zone_mgr.move_card(
                                self, old, "battlefield", "graveyard",
                                cause=f"SBA 704.5j: legend rule ({name})"
                            )
                            actions_taken = True

        return actions_taken

    # ─── TURN STRUCTURE ──────────────────────────────────────────

    def untap_step(self, player_idx: int):
        self.turn_mgr.untap_step(self, player_idx)

    def end_of_turn_cleanup(self):
        self.turn_mgr.end_of_turn_cleanup(self)

    def cleanup_step(self):
        self.turn_mgr.cleanup_step(self)

    def switch_active_player(self):
        self.active_player = 1 - self.active_player
        self.priority_player = self.active_player
        self.turn_number += 1

    @property
    def display_turn(self) -> str:
        """MTG-correct turn label: 'T1' means both players had turn 1.

        Internal turn_number counts half-turns (each player switch).
        Display: round = ceil(turn_number / 2), active player shown separately.
        """
        return str((self.turn_number + 1) // 2)

    # ─── QUERIES ─────────────────────────────────────────────────

    def get_legal_plays(self, player_idx: int) -> List[CardInstance]:
        player = self.players[player_idx]
        legal = []
        for card in player.hand:
            if card.template.is_land:
                if player.lands_played_this_turn < (1 + player.extra_land_drops) and \
                   self.current_phase in (Phase.MAIN1, Phase.MAIN2) and \
                   self.active_player == player_idx and \
                   self.stack.is_empty:
                    legal.append(card)
            elif self.can_cast(player_idx, card):
                legal.append(card)
        # Include flashback and escape cards from graveyard
        for card in player.graveyard:
            if (card.has_flashback or card.template.escape_cost is not None) and \
               self.can_cast(player_idx, card):
                legal.append(card)
        # Include Warp-exiled cards: a creature cast via Warp is exiled at end
        # of turn with card._warped=True and may be re-cast from exile on
        # later turns (CR 702.Warp). can_cast handles the has-artifact + cost
        # gate; this branch surfaces those cards to the legal-play set.
        for card in player.exile:
            if getattr(card, '_warped', False) and self.can_cast(player_idx, card):
                legal.append(card)
        # Include cycling cards from hand (cycling is a special action, not casting)
        for card in player.hand:
            if card not in legal and self.can_cycle(player_idx, card):
                legal.append(card)
        # Include suspend cards from hand. Suspend is a sorcery-speed
        # special action (CR 702.62a) — paid by exiling the card with N
        # time counters. Without this branch, suspend-only CMC-0 cards
        # (Living End, Ancestral Vision, Lotus Bloom, Restore Balance,
        # Wheel of Fate, Crashing Footfalls) are unreachable: they are
        # not hand-castable so the cast branch above filters them out.
        # Phase / stack / active-player gates mirror the land branch
        # since suspend is sorcery-speed.
        if self.current_phase in (Phase.MAIN1, Phase.MAIN2) and \
                self.active_player == player_idx and \
                self.stack.is_empty:
            for card in player.hand:
                if card not in legal and self.can_suspend(player_idx, card):
                    legal.append(card)
        return legal

    def can_cycle(self, player_idx: int, card: "CardInstance") -> bool:
        return CyclingManager.can_cycle(self, player_idx, card)

    def activate_cycling(self, player_idx: int, card: "CardInstance") -> bool:
        return CyclingManager.activate_cycling(self, player_idx, card)

    def _cycling_tutor_search(self, player_idx, variant):
        return CyclingManager._cycling_tutor_search(self, player_idx, variant)

    def _has_leyline_of_guildpact(self, player_idx: int) -> bool:
        return ManaPayment.has_leyline_of_guildpact(self, player_idx)

    def _effective_produces_mana(self, player_idx: int, card) -> list:
        return ManaPayment.effective_produces_mana(self, player_idx, card)

    def _count_domain(self, player_idx: int) -> int:
        return ManaPayment.count_domain(self, player_idx)

    def get_valid_attackers(self, player_idx: int) -> List[CardInstance]:
        from .combat_manager import CombatManager
        return CombatManager.valid_attackers(self, player_idx)

    def get_valid_blockers(self, player_idx: int) -> List[CardInstance]:
        from .combat_manager import CombatManager
        return CombatManager.valid_blockers(self, player_idx)

