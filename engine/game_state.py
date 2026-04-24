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
from .event_system import EventBus, EventType, GameEvent
from .zone_manager import ZoneManager
from .sba_manager import SBAManager
from .turn_manager import TurnManager, TurnStep
from .priority_system import PrioritySystem
from .card_effects import EFFECT_REGISTRY, EffectTiming
from .continuous_effects import ContinuousEffectsManager
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
        # Delayed triggers (e.g., exile at end of turn for Goryo's)
        self._end_of_turn_exiles: List[Tuple[CardInstance, int]] = []
        # Game log
        self.log: List[str] = []
        self.max_turns: int = MAX_TURNS
        # ── New rules engine modules ──
        self.event_bus = EventBus()
        self.zone_mgr = ZoneManager(self.event_bus)
        self.sba_mgr = SBAManager(self.zone_mgr)
        self.turn_mgr = TurnManager()
        self.priority = PrioritySystem()
        self.continuous_effects = ContinuousEffectsManager()

    def next_instance_id(self) -> int:
        iid = self._next_instance_id
        self._next_instance_id += 1
        return iid

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
        """Draw cards from library to hand."""
        player = self.players[player_idx]
        drawn = []
        for _ in range(count):
            if not player.library:
                self.game_over = True
                self.winner = 1 - player_idx
                self.log.append(f"P{player_idx+1} loses: empty library")
                return drawn
            card = player.library.pop(0)
            card.zone = "hand"
            player.hand.append(card)
            player.cards_drawn_this_turn += 1
            drawn.append(card)

            # Generic draw triggers from oracle text
            # Handles: Sheoldred ("gain 2 life" on draw), Bowmasters ("whenever
            # an opponent draws"), and any future draw-trigger cards.
            opp = self.players[1 - player_idx]
            for c in player.battlefield:
                oracle = (c.template.oracle_text or '').lower()
                if 'whenever you draw' in oracle and 'gain' in oracle and 'life' in oracle:
                    import re
                    m = re.search(r'gain\s+(\d+)\s+life', oracle)
                    if m:
                        self.gain_life(player.player_idx, int(m.group(1)), c.name)
            for c in opp.battlefield:
                oracle = (c.template.oracle_text or '').lower()
                # "Whenever you draw" on opponent's side = opponent loses life
                if 'whenever' in oracle and 'draw' in oracle and 'lose' in oracle and 'life' in oracle:
                    import re
                    m = re.search(r'lose\s+(\d+)\s+life', oracle)
                    if m:
                        player.life -= int(m.group(1))
                        opp.damage_dealt_this_turn += int(m.group(1))
                # "Whenever an opponent draws a card except the first one they draw
                # in each of their draw steps" — Bowmasters-style.
                # Trigger on all draws EXCEPT the normal draw-step draw.
                # The draw step sets current_phase = Phase.DRAW; any draw
                # outside that phase always triggers.
                is_draw_step = (self.current_phase == Phase.DRAW)
                first_draw_step_draw = is_draw_step and player.cards_drawn_this_turn <= 1
                if 'whenever an opponent draws' in oracle and not first_draw_step_draw:
                    import re
                    m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
                    dmg = int(m.group(1)) if m else 1
                    player.life -= dmg
                    opp.damage_dealt_this_turn += dmg

        return drawn

    # ─── MANA SYSTEM ─────────────────────────────────────────────

    def tap_lands_for_mana(self, player_idx: int, cost: ManaCost,
                           card_name: str = None,
                           held_instant_colors: Optional[set] = None) -> bool:
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
        )

    def can_cast(self, player_idx: int, card: CardInstance) -> bool:
        return CastManager.can_cast(self, player_idx, card)

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
        available = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()
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

    def create_token(self, *args, **kwargs):
        return PermanentEffects.create_token(self, *args, **kwargs)

    def activate_planeswalker(self, *args, **kwargs):
        return PlaneswalkerManager.activate_planeswalker(self, *args, **kwargs)

    def _apply_untap_on_enter_triggers(self, permanent: "CardInstance",
                                        controller: int):
        LandManager.apply_untap_on_enter_triggers(self, permanent, controller)

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
        """
        player = self.players[player_idx]
        for _ in range(min(count, len(player.hand))):
            if not player.hand:
                break
            card = self.callbacks.choose_discard(
                self, player_idx, list(player.hand), self_discard)
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
        """Check and perform state-based actions.

        Delegates to SBAManager for the proper SBA loop (CR 704.3):
        check all SBAs, if any performed check again, repeat until stable.

        Also preserves the legacy _creature_dies path for Undying/Persist
        handling until those are migrated to the event system.
        """
        # Use the new SBA manager for the core checks
        # But first, handle creatures with lethal damage through the legacy
        # path so Undying/Persist still work correctly.
        actions_taken = False

        # Player life totals (SBA 704.5a)
        for i, player in enumerate(self.players):
            if player.life <= 0 and not self.game_over:
                self.game_over = True
                self.winner = 1 - i
                self.log.append(f"P{i+1} loses: life total {player.life}")
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
        # Include cycling cards from hand (cycling is a special action, not casting)
        for card in player.hand:
            if card not in legal and self.can_cycle(player_idx, card):
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

    def _effective_produces_mana(self, player_idx: int, land) -> list:
        return ManaPayment.effective_produces_mana(self, player_idx, land)

    def _count_domain(self, player_idx: int) -> int:
        return ManaPayment.count_domain(self, player_idx)

    def get_valid_attackers(self, player_idx: int) -> List[CardInstance]:
        from .combat_manager import CombatManager
        return CombatManager.valid_attackers(self, player_idx)

    def get_valid_blockers(self, player_idx: int) -> List[CardInstance]:
        from .combat_manager import CombatManager
        return CombatManager.valid_blockers(self, player_idx)

