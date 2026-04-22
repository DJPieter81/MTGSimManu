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
                           card_name: str = None) -> bool:
        return ManaPayment.tap_lands_for_mana(
            self, player_idx, cost, card_name=card_name
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

    def activate_planeswalker(self, controller: int, pw_card: CardInstance,
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

        self.log.append(f"T{self.display_turn} P{controller+1}: "
                        f"{pw_name} [{loyalty_change:+d}] -> {effect_desc}")

        # Execute effect based on description keywords
        # Each handler is matched by keywords in the effect description string.
        # Order matters: more specific checks first.

        if "return land from graveyard" in effect_desc:
            # Wrenn and Six +1: return a land from graveyard to hand
            player = self.players[controller]
            lands_in_gy = [c for c in player.graveyard if c.template.is_land]
            if lands_in_gy:
                land = lands_in_gy[0]
                player.graveyard.remove(land)
                land.zone = "hand"
                player.hand.append(land)
                self.log.append(f"T{self.display_turn} P{controller+1}: "
                               f"Wrenn and Six returns {land.name} from GY to hand")

        elif "exile all colored" in effect_desc:
            # Ugin -X: exile all colored permanents (simplified as -7)
            for p in self.players:
                to_exile = [c for c in p.battlefield
                            if c.template.color_identity and c.template.name != pw_name]
                for c in to_exile:
                    self._exile_permanent(c)

        elif "exile opponent library" in effect_desc:
            # Jace -12 ult
            opp = self.players[opponent]
            while opp.library:
                card = opp.library.pop(0)
                card.zone = "exile"
                opp.exile.append(card)
            self.game_over = True
            self.winner = controller

        elif "bounce" in effect_desc and "draw" in effect_desc:
            # Teferi -3: bounce target nonland permanent AND draw a card
            opp = self.players[opponent]
            if opp.battlefield:
                nonlands = [c for c in opp.battlefield if not c.template.is_land]
                if nonlands:
                    target = max(nonlands, key=lambda c: c.template.cmc)
                    self._bounce_permanent(target)
            self.draw_cards(controller, 1)

        elif "bounce" in effect_desc:
            # Jace -1: bounce target creature
            opp = self.players[opponent]
            if opp.creatures:
                target = max(opp.creatures, key=lambda c: c.template.cmc)
                self._bounce_permanent(target)

        elif "brainstorm" in effect_desc:
            # Jace 0: draw 3, put 2 back on top
            self.draw_cards(controller, 3)
            player = self.players[controller]
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
            opp = self.players[opponent]
            if opp.library:
                # Simplified: always put on bottom (deny opponent their draw)
                card = opp.library.pop(0)
                opp.library.append(card)

        elif "instants and sorceries cost" in effect_desc:
            # Ral +1: instants/sorceries cost 1 less until next turn
            player = self.players[controller]
            player.temp_cost_reduction += 1
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                           f"{pw_name} +1 — instants and sorceries cost {{1}} less")

        elif "damage" in effect_desc:
            import re
            dmg_match = re.search(r'(\d+)\s+damage', effect_desc)
            if dmg_match:
                dmg = int(dmg_match.group(1))
            elif "equal to instants" in effect_desc:
                # Ral -2: damage = instants/sorceries cast this turn
                dmg = self._global_storm_count
            else:
                dmg = 1  # fallback

            # Smart targeting: kill a creature if the damage is lethal,
            # otherwise go face
            opp = self.players[opponent]
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
                    self.log.append(f"T{self.display_turn} P{controller+1}: "
                                   f"{pw_name} deals {dmg} to {target.name}")
                    if target.is_dead:
                        self._creature_dies(target)
                else:
                    # Can't kill anything, go face
                    opp.life -= dmg
                    self.players[controller].damage_dealt_this_turn += dmg
            else:
                opp.life -= dmg
                self.players[controller].damage_dealt_this_turn += dmg

        elif "gain" in effect_desc and "draw" in effect_desc:
            # Ugin -10 ult: gain 7 life, draw 7, put 7 permanents
            import re
            life_match = re.search(r'gain\s+(\d+)\s+life', effect_desc)
            draw_match = re.search(r'draw\s+(\d+)', effect_desc)
            if life_match:
                self.gain_life(controller, int(life_match.group(1)), pw_name)
            if draw_match:
                self.draw_cards(controller, int(draw_match.group(1)))
            # Simplified: skip the "put permanents onto battlefield" part

        elif "exile the top" in effect_desc and "cast" in effect_desc:
            # Ral -8 ultimate: exile top N, cast instants/sorceries for free
            import re
            n_match = re.search(r'top\s+(\d+)', effect_desc)
            n_cards = int(n_match.group(1)) if n_match else 8
            player = self.players[controller]
            exiled = []
            for _ in range(min(n_cards, len(player.library))):
                card = player.library.pop(0)
                card.zone = "exile"
                player.exile.append(card)
                exiled.append(card)
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                           f"{pw_name} ultimate — exiles top {len(exiled)} cards")
            # Cast all instants and sorceries for free
            for card in list(exiled):
                if card.template.is_instant or card.template.is_sorcery:
                    if card in player.exile:
                        player.exile.remove(card)
                    card.zone = "hand"
                    player.hand.append(card)
                    self.log.append(f"T{self.display_turn} P{controller+1}: "
                                   f"  Free-cast {card.name} from exile")
                    self.cast_spell(controller, card, free_cast=True)

        elif "draw a card" in effect_desc.lower() and "untap" in effect_desc.lower():
            # Teferi, Hero of Dominaria +1: draw a card, untap 2 lands
            self.draw_cards(controller, 1)
            player = self.players[controller]
            untapped = 0
            for land in player.lands:
                if land.tapped and untapped < 2:
                    land.tapped = False
                    untapped += 1
            if untapped:
                self.log.append(f"T{self.display_turn} P{controller+1}: "
                               f"  untap {untapped} lands")

        elif "put target" in effect_desc.lower() and "library" in effect_desc.lower():
            # Teferi Hero -3: tuck nonland permanent into library
            opp = self.players[opponent]
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
                self.log.append(f"T{self.display_turn} P{controller+1}: "
                               f"  tucks {target.name} into library")

        elif "emblem" in effect_desc.lower() and "exile" in effect_desc.lower():
            # Teferi Hero -8 / generic emblem: exile an opponent's permanent
            opp = self.players[opponent]
            targets = [c for c in opp.battlefield]
            if targets:
                target = max(targets, key=lambda c: (c.template.cmc or 0, c.power or 0))
                if target in opp.battlefield:
                    opp.battlefield.remove(target)
                target.zone = "exile"
                opp.exile.append(target)
                self.log.append(f"T{self.display_turn} P{controller+1}: "
                               f"  emblem exiles {target.name}")

        # Planeswalker dies at 0 loyalty (SBA will catch this)

    # ─── ENTERS-TAPPED UNTAP TRIGGER ─────────────────────────────

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
        """Discard cards from hand.

        self_discard=True means the player chose to discard (Faithful Mending, etc.)
        self_discard=False means opponent forced the discard (Thoughtseize, etc.)
        """
        player = self.players[player_idx]
        for _ in range(min(count, len(player.hand))):
            if not player.hand:
                break
            if self_discard:
                card = self._choose_self_discard(player)
            else:
                player.hand.sort(key=lambda c: c.template.cmc, reverse=True)
                card = player.hand[0]
            self.zone_mgr.move_card(
                self, card, "hand", "graveyard",
                cause="forced discard" if not self_discard else "discard"
            )

    def _choose_self_discard(self, player):
        """Choose the best card to discard for self-discard effects.
        
        Priority (discard first):
        1. Cards that WANT to be in the graveyard (flashback, escape, reanimation targets)
        2. Excess lands (more than 4 in hand)
        3. Redundant copies of cards already on battlefield
        4. Lowest-priority spells
        """
        hand = player.hand
        if len(hand) == 1:
            return hand[0]
        
        def discard_score(card):
            """Higher score = discard first."""
            t = card.template
            score = 0
            
            # Cards with flashback/escape WANT to be in the graveyard
            if t.escape_cost is not None:
                score += 100  # Escape cards (Phlage, etc.) - great to discard
            if 'flashback' in t.tags:
                score += 90  # Flashback cards (Unburial Rites, etc.)
            
            # High-CMC creatures are reanimation targets - great to discard
            if t.is_creature and t.cmc >= 5:
                score += 80 + t.cmc  # Higher CMC = better reanimate target
            
            # Excess lands (if we have 4+ lands in hand, discard extras)
            if t.is_land:
                lands_in_hand = sum(1 for c in hand if c.template.is_land)
                lands_on_field = len(player.lands)
                if lands_in_hand > 1 and lands_on_field >= 3:
                    score += 50  # Excess land
                elif lands_in_hand > 2:
                    score += 40
            
            # Protection/reactive spells are lower priority to keep
            if 'counterspell' in t.tags and not t.is_creature:
                score += 20  # Counterspells less important in hand
            
            # Combo pieces and key spells should be kept (low discard score)
            # Exception: high-CMC creatures are reanimation targets — they WANT the GY
            if any(tag in t.tags for tag in ('combo', 'tutor')):
                if not (t.is_creature and t.cmc >= 5):
                    score -= 30
            
            # Removal is moderately important
            if 'removal' in t.tags:
                score += 10
            
            return score
        
        return max(hand, key=discard_score)

    # ─── TRIGGERS ────────────────────────────────────────────────

    def trigger_etb(self, card: CardInstance, controller: int):
        # Elesh Norn / Panharmonicon family: detect any controller-side permanent
        # whose oracle says "triggers an additional time". Each such permanent
        # causes ETB-induced triggers to fire one extra time. Generic — no
        # hardcoding. Excludes the entering card itself (it can double its own
        # triggers only once it has fully entered, which is fine in this impl).
        doublers = sum(
            1 for c in self.players[controller].battlefield
            if c.instance_id != card.instance_id
            and 'triggers an additional time' in (c.template.oracle_text or '').lower()
        )
        trigger_multiplier = 1 + doublers

        for ability in card.template.abilities:
            if ability.ability_type == AbilityType.ETB:
                for _ in range(trigger_multiplier):
                    self._triggers_queue.append((ability, card, controller))
        # Generic "whenever another creature enters" triggers from oracle
        if card.template.is_creature:
            player = self.players[controller]
            for c in player.battlefield:
                if c.instance_id == card.instance_id:
                    continue
                oracle = (c.template.oracle_text or '').lower()
                if 'another creature' in oracle and 'enters' in oracle:
                    if 'gain' in oracle and 'life' in oracle:
                        import re
                        m = re.search(r'gain\s+(\d+)\s+life', oracle)
                        gain = int(m.group(1)) if m else 1
                        for _ in range(trigger_multiplier):
                            self.gain_life(controller, gain, c.name)
                    # Energy trigger (Guide of Souls: "get {E}" after life gain).
                    # The parse_energy_production static was stripped of this
                    # clause to stop Guide auto-producing energy on its own
                    # ETB — re-wire the trigger here so the proper CR behavior
                    # still applies: energy lands when another creature enters.
                    if '{e}' in oracle:
                        import re
                        em = re.search(r'(?:get|gets?)\s+((?:\{e\})+)', oracle)
                        if em:
                            amt = em.group(1).count('{e}')
                            for _ in range(trigger_multiplier):
                                self.produce_energy(controller, amt, c.name)

        # Generic "whenever this creature or another [Subtype] you control enters"
        # Covers Risen Reef (Elemental) and any future cards with this pattern.
        # Crucially: the watcher CAN be the entering card itself ("whenever THIS
        # creature ... enters" means it triggers on its own ETB too).
        import re as _re
        entering_subtypes = {s.lower() for s in (card.template.subtypes or [])}
        player = self.players[controller]
        for watcher in list(player.battlefield):
            w_oracle = (watcher.template.oracle_text or '').lower()
            # Detect pattern: "whenever this creature or another [Subtype] you control enters"
            m = _re.search(
                r'whenever this creature or another (\w+) you control enters',
                w_oracle
            )
            if not m:
                continue
            required_subtype = m.group(1).lower()
            # Fire if the entering card has the required subtype
            if required_subtype not in entering_subtypes:
                continue
            # Skip if the watcher is NOT the entering card but also lacks the subtype
            # (guards against non-Elemental watchers firing on Elemental entries)
            watcher_subtypes = {s.lower() for s in (watcher.template.subtypes or [])}
            if watcher.instance_id != card.instance_id and required_subtype not in watcher_subtypes:
                continue
            # Execute the "look at top card → land to battlefield tapped / else to hand" effect.
            # Elesh Norn family: resolve once per trigger_multiplier.
            for _ in range(trigger_multiplier):
                if ('top card' in w_oracle or 'top of your library' in w_oracle) and player.library:
                    top = player.library[0]
                    if top.template.is_land:
                        player.library.pop(0)
                        top.zone = 'battlefield'
                        top.tapped = True
                        player.battlefield.append(top)
                        self.log.append(
                            f"T{self.display_turn} P{controller+1}: "
                            f"{watcher.name} → {top.name} enters tapped (land)")
                        self._trigger_landfall(controller)
                    else:
                        self.draw_cards(controller, 1)
                        self.log.append(
                            f"T{self.display_turn} P{controller+1}: "
                            f"{watcher.name} → draws a card")

    def trigger_attack(self, attacker: CardInstance, controller: int):
        """Trigger attack abilities."""
        # Energy on attack: only fire when the "get {E}" clause is actually in
        # the attack sentence. Guide of Souls has "get {E}" in its "whenever
        # another creature enters" clause and a SEPARATE "whenever you attack,
        # you may PAY {E}{E}{E}" clause — the old loose regex matched the
        # former and fired on attacks, giving Boros free energy every swing.
        oracle = (attacker.template.oracle_text or '').lower()
        if '{e}' in oracle and 'attack' in oracle and 'get' in oracle:
            import re
            for m in re.finditer(r'(?:get|gets?)\s+((?:\{e\})+)', oracle):
                # Find this sentence's bounds
                sentence_start = max(
                    oracle.rfind('.', 0, m.start()),
                    oracle.rfind('\n', 0, m.start()),
                    -1
                ) + 1
                sentence_end = m.end()
                # Look for the sentence's full text from start to end
                for term in ('.', '\n'):
                    idx = oracle.find(term, m.end())
                    if idx != -1:
                        sentence_end = min(sentence_end if sentence_end > m.end() else idx, idx)
                        break
                clause = oracle[sentence_start:m.end()]
                # Fire only if this clause is an attack trigger, not an
                # "enters"/"dies"/other trigger.
                if 'attack' in clause and 'whenever' in clause:
                    # Also skip if the clause contains "may pay" (it's a payment
                    # opportunity, not a production).
                    if 'may pay' in clause or 'pay {' in clause:
                        continue
                    energy_count = m.group(1).count('{e}')
                    self.produce_energy(controller, energy_count, f"{attacker.name} attack")
                    break

        # Annihilator
        if Keyword.ANNIHILATOR in attacker.keywords:
            opponent = 1 - controller
            # Parse annihilator amount from oracle text
            import re
            oracle = attacker.template.abilities
            ann_amount = 2  # default
            for ab in oracle:
                m = re.search(r'annihilator\s+(\d+)', ab.description.lower())
                if m:
                    ann_amount = int(m.group(1))
                    break
            # Opponent sacrifices N permanents
            opp = self.players[opponent]
            sacrificed = 0
            # Sacrifice least valuable permanents
            sortable = sorted(opp.battlefield, key=lambda c: c.template.cmc)
            for perm in sortable[:ann_amount]:
                if perm in opp.battlefield:
                    opp.battlefield.remove(perm)
                    perm.zone = "graveyard"
                    self.players[perm.owner].graveyard.append(perm)
                    sacrificed += 1
            if sacrificed:
                self.log.append(f"T{self.display_turn}: Annihilator {ann_amount} - "
                                f"P{opponent+1} sacrifices {sacrificed} permanents")

        # Complex attack-trigger land search (oracle: "search...two land cards")
        oracle = (attacker.template.oracle_text or '').lower()
        if 'attack' in oracle and 'search' in oracle and 'two land' in oracle:
            from .card_effects import _primeval_titan_search
            _primeval_titan_search(self, controller)

        # Generic oracle-text-based attack triggers (handles ALL cards)
        # Phlage, Ocelot Pride, battle cry, etc. all resolved from oracle text
        from .oracle_resolver import resolve_attack_trigger
        resolve_attack_trigger(self, attacker, controller)

        # Card-specific ATTACK handlers (e.g. Phelia blink-on-attack)
        EFFECT_REGISTRY.execute(
            attacker.template.name, EffectTiming.ATTACK, self, attacker, controller
        )

        # Generic attack triggers from ability objects
        for ability in attacker.template.abilities:
            if ability.ability_type == AbilityType.ATTACK:
                self._triggers_queue.append((ability, attacker, controller))

    def process_triggers(self):
        while self._triggers_queue:
            ability, source, controller = self._triggers_queue.pop(0)
            stack_item = StackItem(
                item_type=StackItemType.TRIGGERED_ABILITY,
                source=source,
                controller=controller,
                ability=ability,
                description=ability.description,
            )
            self.stack.push(stack_item)

    # ─── TRIGGER QUEUE (for ZoneManager integration) ──────────────

    def queue_trigger(self, trigger_reg):
        """Queue a triggered ability from the event system.

        This bridges the new EventBus trigger system with the existing
        _triggers_queue / process_triggers workflow.
        """
        from .event_system import TriggerRegistration
        if isinstance(trigger_reg, TriggerRegistration):
            # Create a synthetic Ability to wrap the event-based trigger
            ability = Ability(
                ability_type=AbilityType.TRIGGERED,
                description=trigger_reg.description,
                effect=trigger_reg.effect,
            )
            self._triggers_queue.append(
                (ability, trigger_reg.card, trigger_reg.controller)
            )

    # ─── STATE-BASED ACTIONS ─────────────────────────────────────

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
        """Check if a player can cycle a card from hand."""
        if card.zone != "hand":
            return False
        # Use oracle-derived cycling data from template
        cost = card.template.cycling_cost_data
        if cost is None:
            return False
        player = self.players[player_idx]
        # Life cost check
        if cost["life"] > 0 and player.life <= cost["life"]:
            return False
        # Mana cost check
        if cost["mana"] > 0:
            untapped = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()
            if untapped < cost["mana"]:
                return False
            # Color check for colored cycling costs
            if cost["colors"]:
                has_color = False
                for land in player.untapped_lands:
                    if cost["colors"] & set(land.template.produces_mana):
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

    def activate_cycling(self, player_idx: int, card: "CardInstance") -> bool:
        """Activate cycling: pay cost, discard card, draw a card.
        
        Cycling is a special action (not casting a spell). The card goes
        to the graveyard and the player draws a card. This does NOT count
        as casting a spell (no storm count, no prowess triggers).
        """
        if not self.can_cycle(player_idx, card):
            return False
        cost = card.template.cycling_cost_data or {"mana": 0, "life": 0, "colors": set()}
        player = self.players[player_idx]
        # Pay life cost
        if cost["life"] > 0:
            player.life -= cost["life"]
        # Pay mana cost
        if cost["mana"] > 0:
            if cost["colors"]:
                # Tap a land that produces the required color
                for color in cost["colors"]:
                    for land in player.untapped_lands:
                        if color in land.template.produces_mana:
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
            found = self._cycling_tutor_search(player_idx, variant)
            # CR 701.18d — shuffle after the search, whether or not a
            # matching card was found.
            self.rng.shuffle(player.library)
            player.library_searches_this_game += 1
            self._trigger_library_search(player_idx)
            if found is not None:
                self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                               f"Cycle {card.name} ({cost_desc}, "
                               f"tutor: {found.name})")
            else:
                self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                               f"Cycle {card.name} ({cost_desc}, "
                               f"tutor: none found)")
            return True
        # Plain cycling — draw a card; include the drawn card's name in
        # the log so that any card "appearing from nowhere" on a later
        # turn can be traced back to the cycle that produced it
        # (conservation-invariant).
        drawn = self.draw_cards(player_idx, 1)
        drawn_name = drawn[0].name if drawn else "—"
        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                       f"Cycle {card.name} ({cost_desc}, draw: {drawn_name})")
        return True

    def _cycling_tutor_search(self, player_idx: int,
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
        player = self.players[player_idx]
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

    # ─── GRISELBRAND ACTIVATED ABILITY ───────────────────────────

    def activate_griselbrand(self, controller: int, card: CardInstance):
        """Pay 7 life, draw 7 cards."""
        player = self.players[controller]
        if player.life >= 8:  # Keep at least 1 life
            player.life -= 7
            self.draw_cards(controller, 7)
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                            f"Griselbrand: pay 7 life, draw 7")
