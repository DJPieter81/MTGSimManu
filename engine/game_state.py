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
from dataclasses import dataclass, field
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


@dataclass
class PlayerState:
    """Complete state for one player."""
    player_idx: int
    life: int = STARTING_LIFE
    mana_pool: ManaPool = field(default_factory=ManaPool)
    lands_played_this_turn: int = 0
    extra_land_drops: int = 0  # from Azusa, Dryad, etc.
    # Zones
    library: List[CardInstance] = field(default_factory=list)
    hand: List[CardInstance] = field(default_factory=list)
    battlefield: List[CardInstance] = field(default_factory=list)
    graveyard: List[CardInstance] = field(default_factory=list)
    exile: List[CardInstance] = field(default_factory=list)
    sideboard: List[CardInstance] = field(default_factory=list)
    # Counters
    energy_counters: int = 0
    poison_counters: int = 0
    # Tracking
    spells_cast_this_turn: int = 0
    nonartifact_spells_cast_this_turn: int = 0
    creatures_died_this_turn: int = 0
    life_gained_this_turn: int = 0
    damage_dealt_this_turn: int = 0
    cards_drawn_this_turn: int = 0
    # Storm count is spells_cast_this_turn (both players contribute)
    # Energy tracking
    energy_produced_this_game: int = 0
    energy_spent_this_game: int = 0
    library_searches_this_game: int = 0
    silenced_this_turn: bool = False
    silenced_next_turn: bool = False  # Orim's Chant + Scepter lock
    temp_cost_reduction: int = 0  # temporary "spells cost N less" (Ral PW +1), cleared end of turn
    deck_name: str = ""
    # Effective CMC overrides from gameplan (e.g. domain cost reduction)
    effective_cmc_overrides: Dict[str, int] = field(default_factory=dict)
    # Deck composition densities (set at game start for lookahead)
    counter_density: float = 0.0    # fraction of deck that is counterspells
    removal_density: float = 0.0    # fraction of deck that is single-target removal
    exile_density: float = 0.0      # fraction that exiles (ignores toughness)
    # Transient combat-aggression flag: set by board-refill events like Living End
    # so the next combat phase swings hard. Decremented after combat.
    aggression_boost_turns: int = 0

    @property
    def is_alive(self) -> bool:
        return self.life > 0

    @property
    def creatures(self) -> List[CardInstance]:
        return [c for c in self.battlefield
                if c.template.is_creature and not getattr(c, 'is_transformed', False)]

    @property
    def lands(self) -> List[CardInstance]:
        return [c for c in self.battlefield if c.template.is_land]

    @property
    def planeswalkers(self) -> List[CardInstance]:
        return [c for c in self.battlefield
                if (CardType.PLANESWALKER in c.template.card_types
                    or getattr(c, 'is_transformed', False))]

    @property
    def untapped_lands(self) -> List[CardInstance]:
        return [c for c in self.lands if not c.tapped]

    @property
    def available_mana_estimate(self) -> int:
        """Rough estimate of available mana from untapped lands.
        Includes conditional mana bonuses (e.g., Tron assembly) detected
        from oracle text on land card templates."""
        base = len(self.untapped_lands)
        bonus = self._conditional_mana_bonus()
        return base + bonus

    def _conditional_mana_bonus(self) -> int:
        """Calculate bonus mana from lands with conditional mana production.
        Uses the conditional_mana field parsed from oracle text on each land template.
        For example, Urza's Tron lands produce bonus mana when all required
        companion lands are on the battlefield.
        Returns total bonus mana from untapped conditional-mana lands."""
        # Build a set of all land names on battlefield (for condition checking)
        all_land_names = {c.name for c in self.lands}
        # Also build a set of all land subtypes for subtype-based conditions
        all_land_subtypes = set()
        for c in self.lands:
            all_land_subtypes.update(c.template.subtypes)

        bonus = 0
        for land in self.untapped_lands:
            cm = land.template.conditional_mana
            if cm is None:
                continue
            # Check if the condition is met
            requires = cm.get("requires", set())
            if not requires:
                continue
            # Check: each required name must match a land name OR a land subtype combo
            condition_met = True
            for req in requires:
                # Match against land names (e.g., "Urza's Tower")
                # Also match against subtype combos (e.g., subtypes ["Urza's", "Tower"])
                found = req in all_land_names
                if not found:
                    # Try matching as subtype pattern ("Urza's Power-Plant" -> subtypes)
                    req_parts = req.split()
                    if all(p in all_land_subtypes for p in req_parts):
                        found = True
                if not found:
                    condition_met = False
                    break
            if condition_met:
                bonus += cm.get("bonus", 0)
        return bonus

    # Backward-compatible alias
    def _tron_mana_bonus(self) -> int:
        return self._conditional_mana_bonus()

    def _compute_conditional_bonus_per_land(self) -> dict:
        """Compute per-land conditional mana bonus.
        Returns a dict mapping land object id -> bonus mana amount.
        Uses the conditional_mana field parsed from oracle text."""
        all_land_names = {c.name for c in self.lands}
        all_land_subtypes = set()
        for c in self.lands:
            all_land_subtypes.update(c.template.subtypes)

        result = {}
        for land in self.lands:
            cm = land.template.conditional_mana
            if cm is None:
                continue
            requires = cm.get("requires", set())
            if not requires:
                continue
            condition_met = True
            for req in requires:
                found = req in all_land_names
                if not found:
                    req_parts = req.split()
                    if all(p in all_land_subtypes for p in req_parts):
                        found = True
                if not found:
                    condition_met = False
                    break
            if condition_met:
                result[id(land)] = cm.get("bonus", 0)
        return result

    def available_mana_colors(self) -> Dict[str, int]:
        """Get available mana by color from untapped lands."""
        colors: Dict[str, int] = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
        for land in self.untapped_lands:
            for color in land.template.produces_mana:
                colors[color] += 1
        return colors

    def add_energy(self, amount: int):
        """Add energy counters."""
        self.energy_counters += amount
        self.energy_produced_this_game += amount

    def spend_energy(self, amount: int) -> bool:
        """Spend energy counters. Returns True if successful."""
        if self.energy_counters >= amount:
            self.energy_counters -= amount
            self.energy_spent_this_game += amount
            return True
        return False

    def reset_turn_tracking(self):
        self.lands_played_this_turn = 0
        self.extra_land_drops = 0
        self.spells_cast_this_turn = 0
        self.nonartifact_spells_cast_this_turn = 0
        self.creatures_died_this_turn = 0
        self.life_gained_this_turn = 0
        self.damage_dealt_this_turn = 0
        self.cards_drawn_this_turn = 0
        self.silenced_this_turn = False
        # Consume a pending silence from Orim's Chant cast on the previous
        # opponent turn (Isochron Scepter lock pattern).
        if getattr(self, 'silenced_next_turn', False):
            self.silenced_this_turn = True
            self.silenced_next_turn = False
        self.temp_cost_reduction = 0
        self._landfall_count_this_turn = 0


# ─── Named card constants for special handling ───
# RITUAL_CARDS removed — now derived from card.template.ritual_mana
# (populated by oracle_parser.py at card load time)

# Cycling costs: mana = total mana CMC, life = life to pay, colors = required color set
# CYCLING_COSTS removed — now oracle-derived (template.cycling_cost_data)

# ENERGY_PRODUCERS and ENERGY_SPENDERS removed — now oracle-derived
# (template.energy_production populated by oracle_parser.py)

# X_COST_SPELLS removed — now oracle-derived (template.x_cost_data)

# Planeswalker loyalty ability definitions: (plus_amount, minus_amount, ult_amount)
def _parse_planeswalker_abilities(oracle_text: str, loyalty: int = 0) -> dict:
    """Parse planeswalker abilities from oracle text.

    Detects [+N], [-N], [0] loyalty ability patterns.
    Returns dict with 'plus', 'minus', 'ult', 'zero', 'starting_loyalty'.
    """
    import re
    result = {"starting_loyalty": loyalty or 0}
    if not oracle_text:
        return result

    # Find all loyalty abilities: [+1]: text, [-3]: text, [0]: text
    abilities = re.findall(r'\[([+\-−]?\d+)\]:\s*([^\[]+?)(?=\[|$)', oracle_text)

    plus_found = False
    for cost_str, desc in abilities:
        cost_str = cost_str.replace('−', '-')  # unicode minus
        cost = int(cost_str)
        desc = desc.strip().rstrip('.')

        if cost > 0 and not plus_found:
            result["plus"] = (cost, desc)
            plus_found = True
        elif cost == 0:
            result["zero"] = (0, desc)
        elif cost < 0:
            if "minus" not in result:
                result["minus"] = (cost, desc)
            else:
                result["ult"] = (cost, desc)

    return result


# PLANESWALKER_ABILITIES removed — now parsed from oracle text via
# _parse_planeswalker_abilities() called at ETB time.

# Token definitions: (name, types, power, toughness, keywords)
TOKEN_DEFS = {
    "goblin": ("Goblin", [CardType.CREATURE], 1, 1, set()),
    "soldier": ("Soldier", [CardType.CREATURE], 1, 1, set()),
    "spirit": ("Spirit", [CardType.CREATURE], 1, 1, {Keyword.FLYING}),
    "construct": ("Construct", [CardType.CREATURE], 0, 0, set()),  # P/T = artifact count
    # Nettlecyst creates "a 0/0 black Phyrexian Germ" per oracle. Previously
    # absent from TOKEN_DEFS, falling through to the generic 1/1 default in
    # create_token() — Germ tokens ended up +1/+1 larger than intended,
    # contributing ~1-2pp to Affinity's overall WR.
    "germ": ("Germ", [CardType.CREATURE], 0, 0, set()),
    "cat": ("Cat", [CardType.CREATURE], 1, 1, set()),
    "elemental": ("Elemental", [CardType.CREATURE], 1, 1, set()),
    "treasure": ("Treasure", [CardType.ARTIFACT], 0, 0, set()),
    "food": ("Food", [CardType.ARTIFACT], 0, 0, set()),
    "clue": ("Clue", [CardType.ARTIFACT], 0, 0, set()),
}


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

    def setup_game(self, deck1: List[CardTemplate], deck2: List[CardTemplate]):
        """Initialize the game with two decks."""
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
        """Tap lands to pay a mana cost. Returns True if successful."""
        player = self.players[player_idx]

        # Cost reductions
        reduction = 0
        # Domain cost reduction (from oracle-derived template property)
        # Replaces hardcoded "Scion of Draco" / "Leyline Binding" checks
        if card_name:
            for c in list(self.players[player_idx].hand) + list(self.players[player_idx].graveyard):
                if c.template.name == card_name and c.template.domain_reduction > 0:
                    domain = self._count_domain(player_idx)
                    reduction += c.template.domain_reduction * domain
                    break
        # Ruby Medallion and Affinity cost reductions
        player = self.players[player_idx]
        if card_name:
            # Check hand, graveyard, and stack for the card (flashback casts are from GY)
            all_cards = list(player.hand) + list(player.graveyard)
            for c in all_cards:
                if c.template.name == card_name:
                    # Generic cost reduction from permanents
                    from .oracle_resolver import count_cost_reducers
                    reduction += count_cost_reducers(self, player_idx, c.template)
                    # Temporary cost reduction (Ral PW +1 "until your next turn")
                    if c.template.is_instant or c.template.is_sorcery:
                        reduction += player.temp_cost_reduction
                    # Affinity for artifacts
                    if Keyword.AFFINITY in c.template.keywords:
                        artifact_count = sum(
                            1 for b in player.battlefield
                            if CardType.ARTIFACT in b.template.card_types
                        )
                        reduction += artifact_count
                    break
        if reduction > 0:
            from .mana import ManaCost as MC
            new_generic = max(0, cost.generic - reduction)
            cost = MC(
                white=cost.white, blue=cost.blue, black=cost.black,
                red=cost.red, green=cost.green, colorless=cost.colorless,
                generic=new_generic
            )
        untapped = [l for l in player.lands if not l.tapped]

        if not untapped and player.mana_pool.total() == 0:
            return cost.cmc == 0

        # Check if mana pool already has enough (from rituals)
        if player.mana_pool.can_pay(cost):
            return player.mana_pool.pay(cost)

        # Leyline of the Guildpact: all lands produce WUBRG
        has_leyline = self._has_leyline_of_guildpact(player_idx)
        def _produces(land):
            return self.ALL_COLORS if has_leyline else land.template.produces_mana

        # Sort lands: most restrictive first (fewest colors produced)
        untapped.sort(key=lambda l: len(_produces(l)))

        needed = cost.to_dict()
        lands_to_tap = []

        # Pay colored costs using MRV (Most Constrained Variable) heuristic:
        # Process colors with the FEWEST available land sources first.
        # This prevents greedy misassignment where a dual land is used for
        # a color that has many sources, leaving a color with few sources
        # unable to be paid.
        #
        # Example: Faithful Mending costs WU.
        #   Lands: Hallowed Fountain (W/U), Godless Shrine (W/B), Godless Shrine (W/B)
        #   Fixed order (W first): Fountain→W, then no U source → FAIL
        #   MRV order (U first, only 1 source): Fountain→U, then Shrine→W → SUCCESS

        # First, use mana pool for colored costs
        pool_used = {}
        for color in ["W", "U", "B", "R", "G", "C"]:
            remaining = needed.get(color, 0)
            if remaining > 0:
                pool_avail = player.mana_pool.get(color)
                use_pool = min(pool_avail, remaining)
                pool_used[color] = use_pool
                needed[color] = remaining - use_pool

        # Collect colors that still need land sources
        colors_needed_list = []
        for color in ["W", "U", "B", "R", "G", "C"]:
            for _ in range(needed.get(color, 0)):
                colors_needed_list.append(color)

        # Assign with re-sorting: most constrained color first each step
        used_lands = set()

        while colors_needed_list:
            # Re-sort by scarcity each step (fixes 4-color dual land issues)
            colors_needed_list.sort(
                key=lambda c: sum(1 for l in untapped if l not in used_lands and c in _produces(l))
            )
            color = colors_needed_list.pop(0)
            # Find least-flexible unused land for this color
            best_land = None
            best_flex = 999
            for land in untapped:
                if land in used_lands:
                    continue
                lp = _produces(land)
                if color in lp:
                    flex = len(lp)
                    if flex < best_flex:
                        best_flex = flex
                        best_land = land
            if best_land is None:
                return False
            lands_to_tap.append((best_land, color))
            used_lands.add(best_land)

        # Pay generic
        generic_remaining = needed.get("generic", 0)
        # Use pool first
        pool_total = player.mana_pool.total()
        # Subtract what we already committed from pool for colored
        for color in ["W", "U", "B", "R", "G", "C"]:
            pool_avail = player.mana_pool.get(color)
            use_pool = min(pool_avail, needed.get(color, 0))
            pool_total -= use_pool

        use_pool_generic = min(pool_total, generic_remaining)
        generic_remaining -= use_pool_generic

        # Pre-compute conditional mana bonus for each land
        # (uses the data-driven conditional_mana field parsed from oracle text)
        cond_bonus_cache = player._compute_conditional_bonus_per_land()

        for land in untapped:
            if generic_remaining <= 0:
                break
            if land not in used_lands:
                lp = _produces(land)
                if lp:
                    lands_to_tap.append((land, lp[0]))
                    used_lands.add(land)
                    # Base 1 + any conditional bonus from oracle text
                    mana_from_land = 1 + cond_bonus_cache.get(id(land), 0)
                    generic_remaining -= mana_from_land

        if generic_remaining > 0:
            return False

        # Tap lands and add mana
        tapped_names = []
        for land, color in lands_to_tap:
            land.tap()
            player.mana_pool.add(color)
            tapped_names.append(f'{land.name}→{color}')
            bonus = cond_bonus_cache.get(id(land), 0)
            if bonus > 0:
                player.mana_pool.add("C", bonus)
            # Pain land: self-damage when tapping for colored mana
            if land.template.tap_damage > 0 and color != "C":
                player.life -= land.template.tap_damage

        # Verbose: log which lands were tapped for mana
        if getattr(self, 'verbose', False) and tapped_names and card_name:
            remaining_mana = len(player.untapped_lands) + player.mana_pool.total()
            self.log.append(f'    [Mana] Tap {", ".join(tapped_names)} '
                            f'(paying for {card_name}, {remaining_mana} mana remaining)')

        return player.mana_pool.pay(cost)

    def can_cast(self, player_idx: int, card: CardInstance) -> bool:
        """Check if a player can cast a card."""
        player = self.players[player_idx]
        template = card.template

        if card.zone != "hand" and card.zone != "graveyard":
            return False

        # Graveyard casting: Flashback or Escape
        if card.zone == "graveyard":
            # Escape: can cast from graveyard if we have enough mana AND enough
            # other cards in graveyard to exile
            if template.escape_cost is not None:
                other_gy_cards = sum(1 for c in player.graveyard if c != card)
                if other_gy_cards < template.escape_exile_count:
                    return False  # Not enough cards to exile
                # Check mana for escape cost
                untapped_lands = player.untapped_lands
                total_mana = len(untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()
                if total_mana < template.escape_cost:
                    return False
                return True  # Can escape
            elif not card.has_flashback:
                return False  # No flashback, no escape — can't cast from graveyard
        # Cards with no mana cost cannot be cast from hand (MTG CR 202.1a)
        # This covers suspend-only cards (Living End, Ancestral Vision, etc.)
        # that can only be cast via cascade, suspend, or other special means.
        # Detection: has Suspend keyword AND CMC == 0 (meaning no mana cost printed).
        if card.zone == "hand" and template.cmc == 0 and Keyword.SUSPEND in template.keywords:
            return False

        if template.is_land:
            max_lands = 1 + player.extra_land_drops
            return player.lands_played_this_turn < max_lands

        # Ethersworn Canonist: block nonartifact spells if one was already cast
        if CardType.ARTIFACT not in template.card_types:
            canonist_active = any(
                "canonist_active" in c.instance_tags
                for p in self.players for c in p.battlefield
            )
            if canonist_active and player.nonartifact_spells_cast_this_turn >= 1:
                return False

        is_main_phase = self.current_phase in (Phase.MAIN1, Phase.MAIN2)
        is_active = self.active_player == player_idx

        if template.is_instant or template.has_flash:
            pass
        elif template.is_creature or template.is_sorcery or \
             CardType.ENCHANTMENT in template.card_types or \
             CardType.ARTIFACT in template.card_types or \
             CardType.PLANESWALKER in template.card_types:
            if not (is_main_phase and is_active and self.stack.is_empty):
                return False

        # Target validation (CR 601.2c): a spell with a required target
        # cannot be cast if no legal target exists. Without this, the AI
        # casts e.g. Ephemerate into an empty board — the spell fizzles at
        # resolution but mana and the card are wasted. Applies to instants
        # and sorceries only; permanents with ETB targets are handled by
        # their own effect handlers.
        if template.is_instant or template.is_sorcery:
            oracle_l = (template.oracle_text or "").lower()
            if 'target creature you control' in oracle_l:
                if not player.creatures:
                    return False
            elif ('target creature' in oracle_l
                  and 'target creature or planeswalker' not in oracle_l
                  and 'up to' not in oracle_l.split('target creature')[0][-20:]):
                # "target creature" (any controller) — need at least one creature on board
                opp = self.players[1 - player_idx]
                if not player.creatures and not opp.creatures:
                    return False

        # Check mana (pool + untapped lands + Tron bonus)
        untapped_lands = player.untapped_lands
        total_mana = len(untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()

        # X-cost spells: require minimum mana to cast meaningfully
        if template.x_cost_data:
            x_info = template.x_cost_data
            min_mana = x_info["multiplier"] * max(x_info["min_x"], 1)
            if total_mana < min_mana:
                return False

        # Cost reductions
        effective_cmc = template.mana_cost.cmc
        # Domain cost reduction (from oracle-derived template property)
        if template.domain_reduction > 0:
            domain = self._count_domain(player_idx)
            effective_cmc = max(0, effective_cmc - template.domain_reduction * domain)
        # Generic cost reduction from permanents on battlefield
        # Replaces hardcoded Ruby Medallion / Ral checks
        from .oracle_resolver import count_cost_reducers
        generic_reduction = count_cost_reducers(self, player_idx, template)
        if generic_reduction > 0:
            effective_cmc = max(0, effective_cmc - generic_reduction)
        # Affinity for artifacts: reduce cost by artifact count
        if Keyword.AFFINITY in template.keywords:
            artifact_count = sum(
                1 for c in player.battlefield
                if CardType.ARTIFACT in c.template.card_types
            )
            effective_cmc = max(0, effective_cmc - artifact_count)
        # Delve: exile cards from graveyard to reduce generic mana cost
        # Murktide Regent {5}{U}{U} → effectively {U}{U} with 5 cards in GY
        if template.has_delve:
            gy_count = len(player.graveyard)
            # Delve can only reduce generic mana (not colored), so reduce by
            # min(graveyard size, generic portion of cost)
            colored_cost = (template.mana_cost.white + template.mana_cost.blue +
                           template.mana_cost.black + template.mana_cost.red +
                           template.mana_cost.green)
            generic_portion = max(0, effective_cmc - colored_cost)
            delve_reduction = min(gy_count, generic_portion)
            effective_cmc = max(colored_cost, effective_cmc - delve_reduction)

        # Phyrexian mana: can pay 2 life per Phyrexian symbol instead of mana
        # Mutagenic Growth ({G/P}), Dismember ({1}{B/P}{B/P}), etc.
        oracle = (template.oracle_text or '')
        if '/P}' in oracle or '/p}' in oracle.lower():
            phyrexian_count = oracle.lower().count('/p}')
            life_cost = phyrexian_count * 2
            if player.life > life_cost:
                effective_cmc = max(0, effective_cmc - phyrexian_count)

        # Check evoke as alternative cost (Solitude, Endurance, Grief, etc.)
        # Unified board evaluation: can we evoke this creature?
        can_evoke = False
        if template.evoke_cost is not None and total_mana < effective_cmc:
            exile_candidates = [
                c for c in player.hand
                if c != card 
                and not c.template.is_land
                and c.template.color_identity & template.color_identity
            ]
            if exile_candidates:
                can_evoke = True
                # Target validation: don't allow evoke if the card needs a target
                # and no valid target exists (e.g., Solitude on empty board)
                from decks.card_knowledge_loader import requires_target as _req_target
                oracle_lower = (template.oracle_text or "").lower()
                needs_target = (
                    _req_target(template.name)
                    or ('target creature' in oracle_lower)
                    or ('creature spell' in oracle_lower and 'removal' not in (template.tags or set()))
                )
                if needs_target:
                    opp_idx = 1 - player_idx
                    if not self.players[opp_idx].creatures:
                        can_evoke = False  # No targets for evoke
                if can_evoke:
                    can_evoke = self.callbacks.should_evoke(self, player_idx, card)

        if can_evoke:
            return True  # Can cast via evoke

        # Dash: alternative casting cost (e.g., Ragavan {1}{R})
        # Can cast via Dash if we have enough mana for the dash cost
        if template.dash_cost is not None and total_mana >= template.dash_cost:
            return True

        # Warp: alternative cost (e.g., Pinnacle Emissary "Warp {1}")
        # Can cast for {1} if you control an artifact
        oracle = (template.oracle_text or "").lower()
        if "warp" in oracle:
            has_artifact = any(
                'Artifact' in str(getattr(c.template, 'card_types', []))
                for c in player.battlefield
            )
            if has_artifact and total_mana >= 1:
                return True

        # Improvise: tap artifacts to help pay (Kappa Cannoneer, Metallic Rebuke)
        # Each untapped non-land artifact can pay {1} of generic cost
        if "improvise" in oracle:
            untapped_artifacts = sum(
                1 for c in player.battlefield
                if hasattr(c, 'template')
                and 'Artifact' in str(getattr(c.template, 'card_types', []))
                and not c.template.is_land
                and not getattr(c, 'tapped', False)
                and c != card
            )
            improvise_cmc = max(0, effective_cmc - untapped_artifacts)
            if total_mana >= improvise_cmc:
                return True

        # Force alternate cost: "exile a [color] card from your hand rather
        # than pay this spell's mana cost" — only on opponent's turn
        oracle_lower = (template.oracle_text or '').lower()
        if 'exile a' in oracle_lower and 'rather than pay' in oracle_lower:
            # Only works on opponent's turn (not your turn)
            if self.active_player != player_idx:
                # Find required color to exile
                import re
                m = re.search(r'exile an? (\w+) card from your hand', oracle_lower)
                if m:
                    color_word = m.group(1)
                    color_map = {'blue': 'U', 'green': 'G', 'red': 'R',
                                 'white': 'W', 'black': 'B'}
                    req_color = color_map.get(color_word, '')
                    if req_color:
                        from .cards import Color
                        color_enum = {'U': Color.BLUE, 'G': Color.GREEN, 'R': Color.RED,
                                      'W': Color.WHITE, 'B': Color.BLACK}.get(req_color)
                        has_exile_target = any(
                            c != card and color_enum in c.template.color_identity
                            for c in player.hand
                        )
                        if has_exile_target:
                            return True  # Can cast for free

        if total_mana < effective_cmc:
            return False

        # Detailed color check using greedy constraint solving.
        # Dual lands can produce one color OR another, so naive counting
        # double-counts them. We solve by assigning each land to a color need.
        #
        # Color constraint check: only check colored requirements.
        # Generic mana is already validated by the total_mana >= effective_cmc check.
        # Cost reductions (Ruby Medallion) reduce the generic portion, which is
        # already handled above. Here we only need to verify colored sources.
        cost = template.mana_cost
        color_needs = []
        for color, needed in [("W", cost.white), ("U", cost.blue),
                               ("B", cost.black), ("R", cost.red),
                               ("G", cost.green), ("C", cost.colorless)]:
            for _ in range(needed):
                color_needs.append(color)

        # Build list of mana sources: each is a set of colors it can produce
        has_leyline = self._has_leyline_of_guildpact(player_idx)
        all_colors_set = set(self.ALL_COLORS)
        sources = []
        for land in untapped_lands:
            sources.append(all_colors_set if has_leyline else set(land.template.produces_mana))
        # Add mana pool as fixed-color sources
        for color in ["W", "U", "B", "R", "G", "C"]:
            pool_amount = player.mana_pool.get(color)
            for _ in range(pool_amount):
                sources.append({color})

        if len(sources) < effective_cmc:
            return False

        # Color assignment: greedy with re-sorting after each step.
        # Re-sorting fixes the classic 4-color dual land problem where
        # a stale sort order causes greedy misassignment (e.g., Omnath WURG).
        used = [False] * len(sources)

        remaining_needs = list(color_needs)
        while remaining_needs:
            # Re-sort by scarcity: colors with fewest remaining sources first
            remaining_needs.sort(
                key=lambda c: sum(1 for i, s in enumerate(sources) if c in s and not used[i])
            )
            c = remaining_needs.pop(0)
            # Find least-flexible unused source for this color
            best_idx = -1
            best_flex = 999
            for i, s in enumerate(sources):
                if not used[i] and c in s:
                    flex = len(s)
                    if flex < best_flex:
                        best_flex = flex
                        best_idx = i
            if best_idx == -1:
                return False  # Can't satisfy this color requirement
            used[best_idx] = True

        # Check total mana (generic portion)
        remaining_sources = sum(1 for u in used if not u)
        generic_needed = effective_cmc - len(color_needs)
        if remaining_sources < generic_needed:
            return False

        # Blink spells (Ephemerate etc.) require a friendly creature target
        if 'blink' in (template.tags or set()):
            if not player.creatures:
                return False  # No friendly creature to target

        return True

    def play_land(self, player_idx: int, card: CardInstance):
        """Play a land from hand to battlefield."""
        from .card_database import FETCH_LAND_COLORS
        player = self.players[player_idx]
        max_lands = 1 + player.extra_land_drops
        if player.lands_played_this_turn >= max_lands:
            return
        if card not in player.hand:
            return

        player.hand.remove(card)
        player.lands_played_this_turn += 1
        card.controller = player_idx

        # ── Always-tapped lands (from oracle text: "enters tapped") ──
        if card.template.enters_tapped and card.template.untap_life_cost == 0 and card.template.untap_max_other_lands < 0:
            card.enter_battlefield()
            card.tapped = True
            self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name} (enters tapped)")
        # ── Lands with optional life payment to enter untapped (shock lands etc.) ──
        elif card.template.untap_life_cost > 0:
            life_cost = card.template.untap_life_cost
            should_pay = self.callbacks.should_pay_life_for_untapped(self, player_idx, card)
            if should_pay:
                player.life -= life_cost
                card.enter_battlefield()
                card.tapped = False
                self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name} (pay {life_cost} life, untapped, life: {player.life})")
            else:
                card.zone = "battlefield"
                card.summoning_sick = True
                card.entered_battlefield_this_turn = True
                card.tapped = True
                self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name} (tapped, no spells need mana)")
        # ── Conditional untap: untapped if ≤ N other lands (fast lands etc.) ──
        elif card.template.untap_max_other_lands >= 0:
            other_lands = len([c for c in player.battlefield if c.template.is_land])
            card.enter_battlefield()
            if other_lands <= card.template.untap_max_other_lands:
                card.tapped = False
                self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name} (untapped, {other_lands} other lands)")
            else:
                card.tapped = True
                self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name} (tapped, {other_lands} other lands)")
        # ── Fetchland: play then immediately crack ──
        elif card.name in FETCH_LAND_COLORS:
            card.enter_battlefield()
            player.battlefield.append(card)
            self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name}")
            # Trigger landfall for the fetch itself
            self._trigger_landfall(player_idx)
            # Immediately crack the fetchland
            self._crack_fetchland(player_idx, card)
            return  # Don't append again or trigger landfall again below
        else:
            card.enter_battlefield()
            self.log.append(f"T{self.display_turn} P{player_idx+1}: Play {card.name}")

        # Add to battlefield (non-fetch path)
        if card.name not in FETCH_LAND_COLORS:
            player.battlefield.append(card)

        # ── Generic "untap enters tapped" (Amulet of Vigor pattern) ──
        self._apply_untap_on_enter_triggers(card, player_idx)
        # ── "Lands you control enter untapped" static (Spelunking pattern) ──
        self._apply_lands_enter_untapped(card, player_idx)

        # ── Landfall triggers ──
        self._trigger_landfall(player_idx)

    def _crack_fetchland(self, player_idx: int, fetch_card: CardInstance):
        """Sacrifice a fetchland, pay 1 life, search library for a land."""
        from .card_database import FETCH_LAND_COLORS
        player = self.players[player_idx]
        fetch_name = fetch_card.name
        fetch_colors = FETCH_LAND_COLORS.get(fetch_name, [])

        # Pay 1 life (Prismatic Vista, Fabled Passage, Evolving Wilds, Terramorphic Expanse
        # don't cost life, but Zendikar/Onslaught fetches do)
        no_life_fetches = {"Prismatic Vista", "Fabled Passage", "Evolving Wilds", "Terramorphic Expanse"}
        if fetch_name not in no_life_fetches:
            # Safety: if paying 1 life would kill us, don't crack the fetch
            # (This should be caught by AI land selection, but as a safety net)
            if player.life <= 1:
                self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                               f"{fetch_name} not cracked (life too low: {player.life})")
                return
            player.life -= 1

        # Sacrifice the fetchland (triggers revolt)
        if fetch_card in player.battlefield:
            player.battlefield.remove(fetch_card)
        fetch_card.zone = "graveyard"
        player.graveyard.append(fetch_card)
        # Track that a permanent left the battlefield (for revolt)
        player.creatures_died_this_turn = max(player.creatures_died_this_turn, 1)

        # ── Hand-aware fetch target selection via callbacks ──
        best_land = self.callbacks.choose_fetch_target(
            self, player_idx, fetch_card, player.library, fetch_colors
        )

        if best_land:
            player.library.remove(best_land)
            best_land.controller = player_idx

            # Lands with optional life payment to enter untapped
            if best_land.template.untap_life_cost > 0:
                life_cost = best_land.template.untap_life_cost
                should_pay = self.callbacks.should_pay_life_for_untapped(self, player_idx, best_land)
                if should_pay:
                    player.life -= life_cost
                    best_land.enter_battlefield()
                    best_land.tapped = False
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"Crack {fetch_name} (pay 1 life) -> {best_land.name} "
                                   f"(pay {life_cost} life, untapped, life: {player.life})")
                else:
                    best_land.zone = "battlefield"
                    best_land.summoning_sick = True
                    best_land.entered_battlefield_this_turn = True
                    best_land.tapped = True
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"Crack {fetch_name} (pay 1 life) -> {best_land.name} (tapped, life: {player.life})")
            else:
                # Fabled Passage: tapped if < 4 lands; Zendikar fetches: always untapped
                best_land.enter_battlefield()
                if fetch_name in no_life_fetches and len(player.lands) < 4:
                    best_land.tapped = True
                self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                               f"Crack {fetch_name} -> {best_land.name} "
                               f"({'tapped' if best_land.tapped else 'untapped'})")

            player.battlefield.append(best_land)
            # Amulet of Vigor and similar untap triggers
            self._apply_untap_on_enter_triggers(best_land, player_idx)
            # Spelunking / "lands you control enter untapped" static must apply
            # on the fetchland-crack path too — matches the play_land path.
            self._apply_lands_enter_untapped(best_land, player_idx)
            # Bounce land ETB (return a land to hand)
            if best_land.template.is_land:
                from .oracle_resolver import resolve_etb_from_oracle
                resolve_etb_from_oracle(self, best_land, player_idx)
            # Shuffle library
            self.rng.shuffle(player.library)
            # Track library search and trigger opponent's search triggers
            player.library_searches_this_game += 1
            self._trigger_library_search(player_idx)
            # Trigger landfall for the fetched land
            self._trigger_landfall(player_idx)
        else:
            # No valid land found (shuffle anyway)
            self.rng.shuffle(player.library)
            player.library_searches_this_game += 1
            self._trigger_library_search(player_idx)
            self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                           f"Crack {fetch_name} (no valid land found)")

    def _trigger_library_search(self, searcher_idx: int):
        """Trigger effects for opponents when a player searches their library.

        Handles cards like Wan Shi Tong that grow when opponents search.
        """
        opp_idx = 1 - searcher_idx
        opp = self.players[opp_idx]
        for c in opp.battlefield:
            oracle = (c.template.oracle_text or '').lower()
            if 'whenever an opponent searches' in oracle and 'library' in oracle:
                # +1/+1 counter
                c.plus_counters += 1
                # Draw a card if oracle says so
                if 'draw a card' in oracle:
                    self.draw_cards(opp_idx, 1)
                self.log.append(
                    f"T{self.display_turn} P{opp_idx+1}: "
                    f"{c.name} triggers (opponent searched) — "
                    f"+1/+1 counter ({c.power}/{c.toughness}), draw a card")

    def _trigger_landfall(self, player_idx: int):
        """Process landfall triggers for the given player."""
        player = self.players[player_idx]
        opponent_idx = 1 - player_idx

        # Track landfall count this turn (initialize if needed)
        if not hasattr(player, '_landfall_count_this_turn'):
            player._landfall_count_this_turn = 0
        player._landfall_count_this_turn += 1
        landfall_num = player._landfall_count_this_turn

        # Generic multi-landfall triggers from oracle text
        # Handles: "first time...gain life", "second time...add mana", "third time...damage"
        for perm in player.battlefield:
            oracle = (perm.template.oracle_text or '').lower()
            if 'landfall' not in oracle and 'land enters' not in oracle and 'whenever a land' not in oracle:
                continue
            if 'first time' in oracle or 'second time' in oracle or 'third time' in oracle:
                # Multi-trigger landfall (Omnath pattern)
                import re
                if landfall_num == 1 and 'first time' in oracle:
                    m = re.search(r'gain\s+(\d+)\s+life', oracle)
                    if m:
                        self.gain_life(player_idx, int(m.group(1)), f"{perm.name} landfall")
                        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                       f"{perm.name} 1st landfall: +{m.group(1)} life")
                elif landfall_num == 2 and 'second time' in oracle:
                    # Add mana — parse colors from oracle
                    for color in ['R', 'G', 'W', 'U', 'B']:
                        if '{' + color.lower() + '}' in oracle:
                            player.mana_pool.add(color, 1)
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"{perm.name} 2nd landfall: add mana")
                elif landfall_num == 3 and 'third time' in oracle:
                    m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
                    if m:
                        dmg = int(m.group(1))
                        self.players[opponent_idx].life -= dmg
                        player.damage_dealt_this_turn += dmg
                        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                       f"{perm.name} 3rd landfall: {dmg} damage")

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
        """Cast a spell: pay costs and put on stack. free_cast skips mana payment."""
        player = self.players[player_idx]
        template = card.template

        if not free_cast and not self.can_cast(player_idx, card):
            return False

        # Pay mana cost (unless free cast)
        evoked = False
        dashed = False
        if not free_cast:
            untapped = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()

            # Decide whether to use Dash (e.g., Ragavan)
            # Dash strategy: use Dash when...
            #   1) We can't afford the normal cost but can afford Dash
            #   2) Opponent has removal-heavy hand (we want to protect Ragavan)
            #   3) We want haste for an immediate attack
            # Don't Dash when...
            #   1) We want a permanent body and opponent has few threats
            #   2) We're low on mana and Dash costs more than normal
            if template.dash_cost is not None:
                can_normal = untapped >= template.mana_cost.cmc
                can_dash = untapped >= template.dash_cost

                if not can_dash and not can_normal:
                    return False

                dashed = self.callbacks.should_dash(self, player_idx, card, can_normal, can_dash)

            # Check if we should cast via Escape (from graveyard)
            escaped = False
            if card.zone == "graveyard" and template.escape_cost is not None:
                # Exile other cards from graveyard as additional cost
                exile_targets = [c for c in player.graveyard if c != card]
                if len(exile_targets) >= template.escape_exile_count:
                    # Exile the least valuable cards
                    exile_targets.sort(key=lambda c: c.template.cmc)
                    for i in range(template.escape_exile_count):
                        ex = exile_targets[i]
                        player.graveyard.remove(ex)
                        ex.zone = "exile"
                        player.exile.append(ex)
                    escaped = True
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"Escape {card.name} (exile {template.escape_exile_count} cards)")
                else:
                    return False

            # Check if we should evoke instead of paying mana
            # Unified board evaluation: evoke when the body isn't worth waiting for
            should_evoke = (
                not dashed and not escaped
                and template.evoke_cost is not None
                and untapped < template.mana_cost.cmc
                and self.callbacks.should_evoke(self, player_idx, card)
            )
            # Target validation: don't evoke if the card needs a target and none exists
            if should_evoke:
                from decks.card_knowledge_loader import requires_target as _requires_target
                oracle_lower = (template.oracle_text or "").lower()
                needs_target = (
                    _requires_target(template.name)
                    or ('target creature' in oracle_lower)
                    or ('creature spell' in oracle_lower and 'removal' not in (template.tags or set()))
                )
                if needs_target:
                    opp_idx = 1 - player_idx
                    if not self.players[opp_idx].creatures:
                        should_evoke = False  # No targets, skip evoke
            if should_evoke:
                # Evoke: exile a card from hand that shares a color
                exile_candidates = [
                    c for c in player.hand
                    if c != card 
                    and not c.template.is_land  # Lands are colorless, can't be exiled for evoke
                    and c.template.color_identity & template.color_identity
                ]
                if exile_candidates:
                    # Generic evoke exile scoring — no hardcoded card names.
                    # Uses tag-based heuristics (combo pieces > threats > filler).
                    # Reanimate decks: big creatures are irreplaceable combo targets
                    deck_has_reanimate = any(
                        'reanimate' in (h.template.tags or set())
                        for h in player.hand
                    ) or any(
                        'reanimate' in (h.template.tags or set())
                        for h in player.graveyard
                    )
                    def exile_priority(c):
                        """Lower score = more willing to exile this card."""
                        score = c.template.cmc or 0  # prefer exiling cheap cards
                        tags = c.template.tags or set()
                        # Planeswalkers are sticky card-advantage engines —
                        # never pitch them to evoke. Observed: 4c Omnath was
                        # pitching Wrenn and Six to Endurance.
                        if CardType.PLANESWALKER in c.template.card_types:
                            score += 50
                        # Tag-based protection
                        if any(t in tags for t in ('combo', 'finisher')):
                            score += 50  # never exile combo pieces
                        if Keyword.STORM in c.template.keywords:
                            score += 50
                        if Keyword.CASCADE in c.template.keywords:
                            score += 40  # cascade spells are critical
                        # Reanimate targets: big creatures in a reanimate deck
                        if (deck_has_reanimate and c.template.is_creature
                                and (c.template.power or 0) >= 5):
                            score += 50  # irreplaceable reanimate target
                        if any(t in tags for t in ('threat', 'removal', 'board_wipe')):
                            score += 10
                        if any(t in tags for t in ('ritual', 'cost_reducer', 'ramp')):
                            score += 15  # enablers are important
                        if any(t in tags for t in ('cantrip', 'cycling')):
                            score += 5  # replaceable card draw
                        # Duplicate protection: if we have 2+ copies, one is expendable
                        dupes = sum(1 for h in player.hand
                                    if h.name == c.name and h != c)
                        if dupes > 0:
                            score -= 20  # redundant copy is safe to exile
                        return score

                    exile_candidates.sort(key=exile_priority)
                    best_exile = exile_candidates[0]
                    # Don't exile if the best candidate is a critical piece
                    if exile_priority(best_exile) >= 40:
                        return False  # all candidates are too important
                    # Lethal check: allow exiling important pieces under pressure
                    if exile_priority(best_exile) >= 20:
                        opp_idx = 1 - player_idx
                        opp_power = sum(
                            (c.power or c.template.power or 0)
                            for c in self.players[opp_idx].creatures
                        )
                        if opp_power < player.life:
                            return False  # not under pressure, keep synergy piece
                    player.hand.remove(best_exile)
                    best_exile.zone = "exile"
                    player.exile.append(best_exile)
                    evoked = True
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"Evoke {card.name} (exile {best_exile.name})")
                else:
                    return False

            # Delve: exile cards from graveyard to reduce generic mana cost
            delve_exiled = 0
            if template.has_delve and not evoked and not dashed and not escaped:
                colored_cost = (template.mana_cost.white + template.mana_cost.blue +
                               template.mana_cost.black + template.mana_cost.red +
                               template.mana_cost.green)
                generic_portion = max(0, template.mana_cost.cmc - colored_cost)
                exile_targets = [c for c in player.graveyard if c != card]
                delve_exiled = min(len(exile_targets), generic_portion)
                # Exile least valuable cards first
                exile_targets.sort(key=lambda c: c.template.cmc)
                delved_spells = 0
                for i in range(delve_exiled):
                    ex = exile_targets[i]
                    player.graveyard.remove(ex)
                    ex.zone = "exile"
                    player.exile.append(ex)
                    if ex.template.is_instant or ex.template.is_sorcery:
                        delved_spells += 1
                # Store count for Murktide Regent ETB (+1/+1 per delved spell)
                card._delved_spells = delved_spells
                if delve_exiled > 0:
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"Delve {delve_exiled} cards for {card.name}")

            # Pay mana
            if escaped:
                # Pay escape cost instead of normal cost
                from .mana import ManaCost
                # Escape cost for Phlage: {R}{R}{W}{W} = 4 CMC (2R + 2W)
                escape_mana = ManaCost(red=2, white=2)  # Phlage-specific
                if not self.tap_lands_for_mana(player_idx, escape_mana,
                                                 card_name=template.name):
                    return False
            elif dashed:
                # Pay Dash cost instead of normal cost
                from .mana import ManaCost
                dash_mana = ManaCost(generic=template.dash_cost - 1, red=1)  # {1}{R} for Ragavan
                if not self.tap_lands_for_mana(player_idx, dash_mana,
                                                 card_name=template.name):
                    return False
            elif not evoked:
                # Force alternate cost: exile a card from hand instead of mana
                oracle_lower = (template.oracle_text or '').lower()
                force_cast = False
                if ('exile a' in oracle_lower and 'rather than pay' in oracle_lower
                        and self.active_player != player_idx):
                    import re
                    m = re.search(r'exile an? (\w+) card from your hand', oracle_lower)
                    if m:
                        color_word = m.group(1)
                        color_map = {'blue': 'U', 'green': 'G', 'red': 'R',
                                     'white': 'W', 'black': 'B'}
                        req_color = color_map.get(color_word, '')
                        if req_color:
                            from .cards import Color
                            color_enum = {'U': Color.BLUE, 'G': Color.GREEN, 'R': Color.RED,
                                          'W': Color.WHITE, 'B': Color.BLACK}.get(req_color)
                            exile_candidates = [
                                c for c in player.hand
                                if c != card and color_enum in c.template.color_identity
                            ]
                            if exile_candidates:
                                # Exile the least valuable card
                                exile_candidates.sort(key=lambda c: c.template.cmc or 0)
                                exiled = exile_candidates[0]
                                player.hand.remove(exiled)
                                exiled.zone = "exile"
                                player.exile.append(exiled)
                                force_cast = True
                                self.log.append(
                                    f"T{self.display_turn} P{player_idx+1}: "
                                    f"Pay alternate cost: exile {exiled.name} for {template.name}")

                if not force_cast:
                    # Delve: pay reduced cost if we exiled cards
                    if delve_exiled > 0:
                        from .mana import ManaCost
                        reduced_generic = max(0, template.mana_cost.generic - delve_exiled)
                        delve_cost = ManaCost(
                            white=template.mana_cost.white,
                            blue=template.mana_cost.blue,
                            black=template.mana_cost.black,
                            red=template.mana_cost.red,
                            green=template.mana_cost.green,
                            generic=reduced_generic,
                        )
                        if not self.tap_lands_for_mana(player_idx, delve_cost,
                                                         card_name=template.name):
                            return False
                    else:
                        # Phyrexian mana: pay 2 life per Phyrexian symbol instead of colored mana
                        oracle_lower = (template.oracle_text or '').lower()
                        phyrexian_count = oracle_lower.count('/p}')
                        if phyrexian_count > 0 and player.life > phyrexian_count * 2:
                            life_cost = phyrexian_count * 2
                            player.life -= life_cost
                            # Reduce the effective cost — Mutagenic Growth {G/P} becomes free
                            remaining_cmc = max(0, template.mana_cost.cmc - phyrexian_count)
                            if remaining_cmc > 0:
                                from .mana import ManaCost
                                phyrexian_cost = ManaCost(generic=remaining_cmc)
                                if not self.tap_lands_for_mana(player_idx, phyrexian_cost,
                                                                 card_name=template.name):
                                    player.life += life_cost  # refund
                                    return False
                            self.log.append(
                                f"T{self.display_turn} P{player_idx+1}: "
                                f"Pay {life_cost} life (Phyrexian mana) for {template.name}")
                        elif not self.tap_lands_for_mana(player_idx, template.mana_cost,
                                                         card_name=template.name):
                            return False

        # Remove from zone and track cast-from-graveyard for flashback exile
        cast_with_flashback = False
        if card in player.hand:
            player.hand.remove(card)
        elif card in player.graveyard:
            player.graveyard.remove(card)
            # If cast from GY via flashback (not escape), mark for exile after resolution
            if card.has_flashback and not (escaped if not free_cast else False):
                cast_with_flashback = True
        card.zone = "stack"
        card._cast_with_flashback = cast_with_flashback
        card._evoked = evoked  # Track for sacrifice after ETB
        card._dashed = dashed  # Track for haste + return to hand at end of turn
        card._escaped = getattr(card, '_escaped', False) or (escaped if not free_cast else False)  # Track for sacrifice-unless-escaped

        # Calculate X value for X-cost spells
        x_value = 0
        if template.x_cost_data and not free_cast and not evoked:
            x_info = template.x_cost_data
            # X = (total mana available) / multiplier
            # For XX spells, X = mana / 2; for X spells, X = mana
            available_for_x = len(player.untapped_lands) + player.mana_pool.total() + player._tron_mana_bonus()
            x_value = available_for_x // x_info["multiplier"]
            # AI chooses optimal X based on oracle text:
            oracle = (template.oracle_text or '').lower()
            if 'charge counter' in oracle and 'whenever' in oracle:
                # Hate permanent (Chalice-style): pick X to maximize disruption.
                # INCLUDE X=0 as a valid candidate — Affinity runs ~8 zero-cost
                # artifacts (Memnite, Ornithopter, Mox Opal, Springleaf Drum).
                # Previously filtered out by `if cm > 0`, so Chalice defaulted
                # to X=1 and never locked Affinity's engine (audit F-R3-1).
                opp = self.players[1 - player_idx]
                cmc_counts = {}
                for c in opp.library:
                    if not c.template.is_land:
                        cm = c.template.cmc or 0
                        cmc_counts[cm] = cmc_counts.get(cm, 0) + 1
                # Pick the CMC with the most spells, capped at available mana.
                # X=0 is always castable (available mana >= 0); accept it
                # when x_value >= 0 (which is always true after the cast).
                if cmc_counts:
                    # Bug guard: cmc_counts non-empty doesn't guarantee any
                    # CMC fits under x_value — the filter can still produce an
                    # empty sequence. Materialize the candidate list and fall
                    # back to X=1 when nothing fits.
                    candidates = [(cnt, cmc) for cmc, cnt in cmc_counts.items()
                                  if cmc <= x_value]
                    if candidates:
                        best_x = max(candidates)
                        x_value = best_x[1]
                    else:
                        x_value = 1
                elif x_value >= 1:
                    x_value = 1  # fallback to X=1 if no data
            # +1/+1 counter creatures: use max mana (Ballista-style)
            # (default x_value is already max)
            # Pay the actual X cost
            actual_cost = x_value * x_info["multiplier"]
            remaining = actual_cost
            # Pay from mana pool first
            from_pool = min(player.mana_pool.total(), remaining)
            if from_pool > 0:
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
            # Pay rest from lands
            for land in player.untapped_lands:
                if remaining <= 0:
                    break
                land.tapped = True
                remaining -= 1

        stack_item = StackItem(
            item_type=StackItemType.SPELL,
            source=card,
            controller=player_idx,
            targets=targets or [],
            x_value=x_value,
        )

        # ── Splice onto Arcane: when casting an Arcane spell, splice cards
        # from hand that have splice_cost. Pay splice cost, add their effects,
        # spliced card stays in hand. ──
        if 'Arcane' in template.subtypes and not free_cast:
            from .oracle_resolver import count_cost_reducers
            for sc in list(player.hand):
                if sc.instance_id == card.instance_id:
                    continue
                splice = sc.template.splice_cost
                if not splice:
                    continue
                # splice is total CMC (int) — apply cost reduction
                reduction = count_cost_reducers(self, player_idx, sc.template)
                reduction += player.temp_cost_reduction
                effective_splice = max(0, splice - reduction)
                available_mana = player.mana_pool.total() + len(player.untapped_lands)
                if available_mana >= effective_splice:
                    # Pay splice cost from mana pool/lands
                    from .mana import ManaCost as MC
                    # Splice for rituals: {1}{R} = generic + 1 red
                    red_portion = min(1, effective_splice)
                    generic_portion = max(0, effective_splice - red_portion)
                    splice_mc = MC(generic=generic_portion, red=red_portion)
                    if not self.tap_lands_for_mana(player_idx, splice_mc,
                                                   sc.template.name):
                        continue
                    stack_item.spliced.append(sc.template)
                    self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                                   f"  Splice {sc.name} onto {card.name}")

        self.stack.push(stack_item)
        player.spells_cast_this_turn += 1
        if CardType.ARTIFACT not in template.card_types:
            player.nonartifact_spells_cast_this_turn += 1
        self._global_storm_count += 1

        # ── Chalice of the Void check ──
        # If opponent controls Chalice with charge counters == spell's CMC, counter it
        opp_idx = 1 - player_idx
        opp = self.players[opp_idx]
        # Generic "counter spell with mana value equal to charge counters" check
        for perm in opp.battlefield:
            perm_oracle = (perm.template.oracle_text or '').lower()
            if 'charge counter' in perm_oracle and 'mana value' in perm_oracle and 'counter' in perm_oracle:
                charge = perm.other_counters.get("charge", 0)
                if charge == template.cmc and template.cmc >= 0:
                    self.stack.pop()
                    card.zone = "graveyard"
                    player.graveyard.append(card)
                    self.log.append(
                        f"T{self.display_turn} P{opp_idx+1}: "
                        f"{perm.name} (X={charge}) counters {card.name}")
                    return True

        dash_label = " (Dash)" if dashed else ""
        x_label = f" (X={x_value})" if x_value > 0 else ""
        cost_parts = []
        mc = card.template.mana_cost
        if x_value > 0:
            x_info = template.x_cost_data or {}
            actual_paid = x_value * x_info.get("multiplier", 1)
            cost_parts.append(str(actual_paid))
        elif mc.generic > 0:
            cost_parts.append(str(mc.generic))
        cost_parts.extend('W' * mc.white + 'U' * mc.blue + 'B' * mc.black + 'R' * mc.red + 'G' * mc.green)
        cost_str = ''.join(cost_parts) if cost_parts else '0'
        self.log.append(f"T{self.display_turn} P{player_idx+1}: Cast {card.name} ({cost_str}){dash_label}{x_label}")

        # ── Prowess and prowess-like triggers (generic from oracle) ──
        if not template.is_creature:
            for creature in player.creatures:
                # Standard prowess keyword
                if Keyword.PROWESS in creature.keywords:
                    creature.temp_power_mod += 1
                    creature.temp_toughness_mod += 1
                    continue
                # Oracle-based prowess variants:
                # "Whenever you cast a noncreature spell, this creature gets +N/+M"
                c_oracle = (creature.template.oracle_text or '').lower()
                if 'noncreature spell' not in c_oracle and 'instant or sorcery' not in c_oracle:
                    continue
                import re
                pump = re.search(r'gets?\s+\+(\d+)/\+(\d+)', c_oracle)
                if pump:
                    creature.temp_power_mod += int(pump.group(1))
                    creature.temp_toughness_mod += int(pump.group(2))
                elif re.search(r'gets?\s+\+(\d+)/\+0', c_oracle):
                    m = re.search(r'gets?\s+\+(\d+)/\+0', c_oracle)
                    creature.temp_power_mod += int(m.group(1))
                # Delirium — check actual GY card types via _has_delirium()
                # _dynamic_base_power() already scales to 3 with delirium; we also
                # need to grant FLYING as a keyword so combat logic sees it.
                if 'delirium' in c_oracle and hasattr(creature, '_has_delirium'):
                    if creature._has_delirium():
                        if Keyword.FLYING not in creature.keywords:
                            creature.keywords.add(Keyword.FLYING)

                # Surveil 1: always bin the top card to GY (AI choice: maximise delirium)
                if 'surveil' in c_oracle and player.library:
                    top = player.library.pop(0)
                    top.zone = 'graveyard'
                    player.graveyard.append(top)
                    self.log.append(
                        f"T{self.display_turn} P{player_idx+1}: "
                        f"{creature.name} surveil 1 → {top.name} to GY")

        # Generic oracle-text-based spell-cast triggers
        from .oracle_resolver import resolve_spell_cast_trigger
        resolve_spell_cast_trigger(self, player_idx, card)

        return True

    # ─── SPELL RESOLUTION ────────────────────────────────────────

    def resolve_stack(self):
        """Resolve the top item on the stack."""
        if self.stack.is_empty:
            return

        item = self.stack.pop()
        card = item.source
        template = card.template

        # Only log "Resolve" for spells — not for triggered/activated abilities
        if item.item_type == StackItemType.SPELL:
            self.log.append(f"T{self.display_turn}: Resolve {card.name}")

        if item.item_type == StackItemType.SPELL:
            if CardType.INSTANT in template.card_types or CardType.SORCERY in template.card_types:
                self._execute_spell_effects(item)
                # Storm: copy the spell for each prior spell this turn
                if Keyword.STORM in template.keywords:
                    self._handle_storm(item)
                # Cascade: exile from top until lower CMC, cast free
                if Keyword.CASCADE in template.keywords:
                    self._handle_cascade(item)
                # Flashback: exile instead of going to graveyard (MTG CR 702.33a)
                if getattr(card, '_cast_with_flashback', False):
                    card.zone = "exile"
                    self.players[card.owner].exile.append(card)
                    card.has_flashback = False  # no longer has flashback
                elif hasattr(card, '_rebound_controller'):
                    # Rebound: exile instead of graveyard, cast for free next upkeep
                    card.zone = "exile"
                    self.players[card.owner].exile.append(card)
                    if not hasattr(self, '_rebound_cards'):
                        self._rebound_cards = []
                    self._rebound_cards.append(card)
                else:
                    card.zone = "graveyard"
                    self.players[card.owner].graveyard.append(card)
            else:
                # Permanent enters battlefield
                card.controller = item.controller
                card.enter_battlefield()
                self.players[item.controller].battlefield.append(card)
                # Place counters for X-cost permanents — only if no dedicated
                # ETB handler exists (Engineered Explosives uses sunburst via its
                # own handler, so don't double-set charge counters here)
                if item.x_value > 0 and template.x_cost_data:
                    has_dedicated_etb = template.name in EFFECT_REGISTRY._handlers
                    x_info = template.x_cost_data
                    effect = x_info.get("effect", "")
                    if effect == "charge_counters" and not has_dedicated_etb:
                        card.other_counters["charge"] = item.x_value
                        self.log.append(
                            f"T{self.display_turn} P{item.controller+1}: "
                            f"{card.name} enters with {item.x_value} charge counter(s)")
                    elif effect == "plus1_counters":
                        card.plus_counters += item.x_value
                        self.log.append(
                            f"T{self.display_turn} P{item.controller+1}: "
                            f"{card.name} enters with {item.x_value} +1/+1 counter(s)")
                self._handle_permanent_etb(card, item.controller)
                # Cascade on permanents too
                if Keyword.CASCADE in template.keywords:
                    self._handle_cascade(item)
                # Evoke: sacrifice after ETB triggers
                if getattr(card, '_evoked', False):
                    if card in self.players[item.controller].battlefield:
                        self.players[item.controller].battlefield.remove(card)
                        card.zone = "graveyard"
                        self.players[card.owner].graveyard.append(card)
                        self.log.append(f"T{self.display_turn} P{item.controller+1}: "
                                       f"{card.name} sacrificed (evoke)")
                # Phlage sacrifice-unless-escaped: if cast normally (not escaped),
                # sacrifice after ETB trigger resolves
                if (template.escape_cost is not None
                        and not getattr(card, '_escaped', False)):
                    if card in self.players[item.controller].battlefield:
                        self.players[item.controller].battlefield.remove(card)
                        card.zone = "graveyard"
                        self.players[card.owner].graveyard.append(card)
                        self.log.append(f"T{self.display_turn} P{item.controller+1}: "
                                       f"{card.name} sacrificed (not escaped)")

        elif item.item_type in (StackItemType.ACTIVATED_ABILITY,
                                 StackItemType.TRIGGERED_ABILITY):
            if item.ability and item.ability.effect:
                item.ability.effect(self, item.source, item.controller, item.targets)
            elif item.effect:
                item.effect(self, item.source, item.controller, item.targets)

    def _handle_permanent_etb(self, card: CardInstance, controller: int):
        """Handle all enter-the-battlefield effects for a permanent."""
        template = card.template

        # Planeswalker: set loyalty counters from template (oracle-derived)
        if CardType.PLANESWALKER in template.card_types:
            card.loyalty_counters = template.loyalty or 0

        # Energy production on ETB (from oracle-derived template property)
        if template.energy_production > 0:
            self.players[controller].add_energy(template.energy_production)
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                            f"{template.name} produces {template.energy_production} energy "
                            f"(total: {self.players[controller].energy_counters})")

        # Torpor Orb: suppress creature ETB abilities
        torpor_active = any(
            "torpor_orb_active" in c.instance_tags
            for p in self.players for c in p.battlefield
        )
        is_creature = CardType.CREATURE in template.card_types

        if torpor_active and is_creature:
            self.log.append(f"T{self.display_turn}: {template.name} ETB suppressed by Torpor Orb")
        else:
            # Dispatch to card effect registry for card-specific ETB logic
            has_specific_handler = template.name in EFFECT_REGISTRY._handlers
            EFFECT_REGISTRY.execute(
                template.name, EffectTiming.ETB, self, card, controller
            )

            # Generic oracle-text-based ETB resolution for cards WITHOUT specific handlers
            if not has_specific_handler:
                from .oracle_resolver import resolve_etb_from_oracle
                resolve_etb_from_oracle(self, card, controller)

            # Generic ETB triggers
            self.trigger_etb(card, controller)

    # ─── STORM ───────────────────────────────────────────────────

    def _handle_storm(self, item: StackItem):
        """Create storm copies. Storm count = spells cast this turn - 1."""
        copies = self._global_storm_count - 1
        if copies <= 0:
            return

        controller = item.controller
        card = item.source
        self.log.append(f"T{self.display_turn}: Storm copies: {copies}")

        for i in range(copies):
            # Execute the spell effect again for each copy
            self._execute_spell_effects(item)
            if self.game_over:
                return

    # ─── CASCADE ─────────────────────────────────────────────────

    def _handle_cascade(self, item: StackItem):
        """Cascade: exile from top until CMC < cascade spell, cast free, rest on bottom."""
        controller = item.controller
        cascade_cmc = item.source.template.cmc
        player = self.players[controller]
        exiled = []
        found_card = None

        self.log.append(f"T{self.display_turn}: Cascade (CMC < {cascade_cmc})")

        while player.library:
            top = player.library.pop(0)
            top.zone = "exile"
            player.exile.append(top)
            exiled.append(top)

            if top.template.is_spell and top.template.cmc < cascade_cmc:
                # Found a castable card
                found_card = top
                break

        if found_card:
            self.log.append(f"T{self.display_turn}: Cascade hits {found_card.name}")

            # Detect "exile all creatures + return from GY" effects (Living
            # End and similar mass-reanimate cards). Oracle pattern: spell
            # mentions 'all creature cards' AND 'graveyard' AND a return-
            # to-battlefield effect (puts/return + battlefield). Generic —
            # works for Living End ("Each player exiles all creature cards
            # from their graveyard ... then puts all cards they exiled this
            # way onto the battlefield") and any future card matching the
            # archetype.
            found_oracle = (found_card.template.oracle_text or '').lower()
            is_mass_reanimate = (
                'all creature cards' in found_oracle
                and 'graveyard' in found_oracle
                and 'battlefield' in found_oracle
            )
            if is_mass_reanimate:
                self._resolve_living_end(controller)
                found_card.zone = "graveyard"
                if found_card in player.exile:
                    player.exile.remove(found_card)
                player.graveyard.append(found_card)
            else:
                # Cast the found card for free
                if found_card in player.exile:
                    player.exile.remove(found_card)
                found_card.zone = "hand"
                player.hand.append(found_card)
                found_card._free_cast_opportunity = True  # cascade: free cast signal
                self.cast_spell(controller, found_card, free_cast=True)
                # Resolve immediately
                while not self.stack.is_empty:
                    self.resolve_stack()
                    self.check_state_based_actions()
                    if self.game_over:
                        return

        # Put remaining exiled cards on bottom in random order
        remaining = [c for c in exiled if c != found_card]
        self.rng.shuffle(remaining)
        for c in remaining:
            if c in player.exile:
                player.exile.remove(c)
            c.zone = "library"
            player.library.append(c)

    # ─── LIVING END ──────────────────────────────────────────────

    def _resolve_living_end(self, controller: int):
        """Living End: exile all creatures from battlefield, return all from graveyard."""
        self.log.append(f"T{self.display_turn}: Living End resolves!")

        # For each player: exile battlefield creatures, return graveyard creatures
        for p_idx in range(2):
            player = self.players[p_idx]

            # Collect creatures on battlefield to exile
            bf_creatures = [c for c in player.battlefield if c.template.is_creature]
            # Collect creatures in graveyard to return
            gy_creatures = [c for c in player.graveyard if c.template.is_creature]

            # Exile battlefield creatures
            for creature in bf_creatures:
                player.battlefield.remove(creature)
                creature.zone = "exile"
                creature.reset_combat()
                creature.cleanup_damage()
                player.exile.append(creature)

            # Return graveyard creatures to battlefield
            for creature in gy_creatures:
                player.graveyard.remove(creature)
                creature.controller = p_idx
                creature.enter_battlefield()
                player.battlefield.append(creature)
                self._handle_permanent_etb(creature, p_idx)
                self.log.append(f"T{self.display_turn}: Living End returns "
                                f"{creature.name} for P{p_idx+1}")

        # Mark the controller's next combat as aggressive. Living End resets the
        # board in our favour; the AI should swing all-in even with blockers back
        # because the opponent has no creatures and any incremental damage is
        # close to lethal.
        #
        # Set to 2 (not 1): the first decrement happens in end_combat on the
        # turn Living End resolves, but the returned creatures have summoning
        # sickness on that turn and can't attack anyway. We need the flag to
        # SURVIVE that wasted decrement so the NEXT turn's combat sees it.
        self.players[controller].aggression_boost_turns = max(
            getattr(self.players[controller], 'aggression_boost_turns', 0), 2
        )

        # Signal the AI's GoalEngine to advance past CURVE_OUT / DEPLOY_ENGINE
        # into PUSH_DAMAGE on the next main-phase entry. Without this the
        # cascade deck keeps casting tutors / ritual fodder instead of
        # closing the game with the board it just produced. Consumed once
        # by ev_player._execute_main_phase.
        if not hasattr(self, '_pending_goal_advance'):
            self._pending_goal_advance = {}
        self._pending_goal_advance[controller] = 'post_combo_aggression'

    # ─── REANIMATION ─────────────────────────────────────────────

    def reanimate(self, controller: int, target_card: CardInstance,
                  exile_at_eot: bool = False, give_haste: bool = False):
        """Put a creature from graveyard onto the battlefield."""
        player = self.players[controller]
        if target_card not in player.graveyard:
            return

        player.graveyard.remove(target_card)
        target_card.controller = controller
        target_card.enter_battlefield()
        if give_haste:
            target_card.temp_keywords.add(Keyword.HASTE)
        player.battlefield.append(target_card)

        self.log.append(f"T{self.display_turn} P{controller+1}: "
                        f"Reanimate {target_card.name}")

        if exile_at_eot:
            self._end_of_turn_exiles.append((target_card, controller))

        # Trigger ETB
        self._handle_permanent_etb(target_card, controller)

    # ─── TOKEN GENERATION ────────────────────────────────────────

    def create_token(self, controller: int, token_type: str,
                     count: int = 1, power: int = None, toughness: int = None,
                     extra_keywords: Set[Keyword] = None) -> List[CardInstance]:
        """Create token creatures on the battlefield."""
        tokens = []
        token_def = TOKEN_DEFS.get(token_type)
        if not token_def:
            # Generic token
            token_def = (token_type.title(), [CardType.CREATURE], power or 1, toughness or 1, set())

        t_name, t_types, t_power, t_toughness, t_keywords = token_def
        if power is not None:
            t_power = power
        if toughness is not None:
            t_toughness = toughness
        kw_set = set(t_keywords)
        if extra_keywords:
            kw_set |= extra_keywords

        # Oracle text on the generated template so _dynamic_base_power's
        # regex can find the scaling pattern. Without this, Construct tokens
        # from Urza's Saga Ch II have no oracle_text, the regex
        # `\+\d+/\+\d+ for each artifact you control` doesn't fire, and they
        # stay 0/0 → die immediately to state-based actions. Root-caused from
        # verbose vs Affinity: "T4: Construct Token dies" on Ch II resolution.
        TOKEN_ORACLES = {
            "construct": "This creature gets +1/+1 for each artifact you control.",
        }
        token_oracle = TOKEN_ORACLES.get(token_type, "")

        for _ in range(count):
            template = CardTemplate(
                name=f"{t_name} Token",
                card_types=list(t_types),
                mana_cost=ManaCost(),
                power=t_power,
                toughness=t_toughness,
                keywords=kw_set,
                tags={"token", "creature"},
                oracle_text=token_oracle,
            )
            instance = CardInstance(
                template=template,
                owner=controller,
                controller=controller,
                instance_id=self.next_instance_id(),
                zone="battlefield",
            )
            instance._game_state = self
            instance.enter_battlefield()
            self.players[controller].battlefield.append(instance)
            tokens.append(instance)

        if count > 0:
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                            f"Create {count}x {t_name} token(s)")
        return tokens

    # ─── PLANESWALKER ABILITIES ──────────────────────────────────

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

    def _apply_untap_on_enter_triggers(self, permanent: "CardInstance", controller: int):
        """Generic 'whenever a permanent you control enters tapped, untap it' trigger.

        Detects any artifact/enchantment on the battlefield with that oracle pattern
        (e.g. Amulet of Vigor) without hardcoding card names.
        """
        if not getattr(permanent, 'tapped', False):
            return
        player = self.players[controller]
        untaps = 0
        for watcher in player.battlefield:
            if watcher.instance_id == permanent.instance_id:
                continue
            w_oracle = (watcher.template.oracle_text or '').lower()
            if ('whenever' in w_oracle and 'enters tapped' in w_oracle
                    and 'untap it' in w_oracle):
                untaps += 1
        if untaps > 0:
            # Each copy of the untap-trigger permanent independently untaps.
            # Idempotent today (tapped = False after any one), but semantically
            # correct: N copies fire N triggers.
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
            self.log.append(
                f"T{self.display_turn} P{controller+1}: "
                f"{', '.join(watcher_names)} untaps {permanent.name}{copies_note}"
            )

    def _apply_lands_enter_untapped(self, land: "CardInstance", controller: int):
        """Generic 'lands you control enter the battlefield untapped' static ability.

        Fires when a land enters; checks for Spelunking and similar permanents.
        Does nothing if land is already untapped.
        """
        if not getattr(land, 'tapped', False) or not land.template.is_land:
            return
        player = self.players[controller]
        for watcher in player.battlefield:
            if watcher.instance_id == land.instance_id:
                continue
            w_oracle = (watcher.template.oracle_text or '').lower()
            if 'lands you control enter' in w_oracle and 'untapped' in w_oracle:
                land.tapped = False
                self.log.append(
                    f"T{self.display_turn} P{controller+1}: "
                    f"{watcher.name} — {land.name} enters untapped")
                break

    # ─── ENERGY SYSTEM ───────────────────────────────────────────

    def produce_energy(self, player_idx: int, amount: int, source_name: str = ""):
        """Add energy counters to a player."""
        self.players[player_idx].add_energy(amount)
        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                        f"+{amount} energy from {source_name} "
                        f"(total: {self.players[player_idx].energy_counters})")

    def spend_energy_for_effect(self, player_idx: int, amount: int,
                                 effect_type: str = "") -> bool:
        """Spend energy for an effect. Returns True if successful."""
        if self.players[player_idx].spend_energy(amount):
            self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                            f"Spend {amount} energy for {effect_type}")
            return True
        return False

    def gain_life(self, player_idx: int, amount: int, source: str = ""):
        """Centralized lifegain with triggers (Ocelot Pride, etc.)."""
        if amount <= 0:
            return
        player = self.players[player_idx]
        player.life += amount
        player.life_gained_this_turn += amount
        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                        f"Gain {amount} life from {source} (life: {player.life})")
        # Generic "whenever you gain life" triggers from oracle
        for creature in list(player.creatures):
            oracle = (creature.template.oracle_text or '').lower()
            if 'whenever you gain life' in oracle and 'create' in oracle and 'token' in oracle:
                # Parse token type from oracle if possible
                token_type = "cat" if "cat" in oracle else "creature"
                self.create_token(player_idx, token_type, count=1)
                break  # once per lifegain event

    # ─── SPELL EFFECTS ───────────────────────────────────────────

    def _execute_spell_effects(self, item: StackItem):
        """Execute the effects of an instant/sorcery spell."""
        card = item.source
        controller = item.controller
        opponent = 1 - controller
        name = card.name

        # Rituals: add mana to pool (oracle-derived from template)
        ritual_data = card.template.ritual_mana
        if ritual_data:
            color, amount = ritual_data
            if color == "any":
                self.players[controller].mana_pool.add("R", 2)
                # Manamorphose draws a card
                if 'cantrip' in card.template.tags:
                    self.draw_cards(controller, 1)
            else:
                self.players[controller].mana_pool.add(color, amount)
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                            f"{name} adds {amount} {color} mana")

            # Splice: add mana from spliced card effects
            for spliced_tmpl in item.spliced:
                splice_ritual = spliced_tmpl.ritual_mana
                if splice_ritual:
                    sc, sa = splice_ritual
                    if sc == "any":
                        self.players[controller].mana_pool.add("R", 2)
                    else:
                        self.players[controller].mana_pool.add(sc, sa)
                    self.log.append(f"T{self.display_turn} P{controller+1}: "
                                    f"  Spliced {spliced_tmpl.name} adds {sa} {sc} mana")
            return

        # Dispatch to card effect registry
        # Snapshot opponent state before resolution to auto-generate target log
        _opp = self.players[1 - controller]
        _pre_life = _opp.life
        _pre_creatures = {c.instance_id: (c.name, c.toughness) for c in _opp.creatures}
        _pre_hand = len(_opp.hand)
        _pre_log_len = len(self.log)
        if EFFECT_REGISTRY.execute(
            name, EffectTiming.SPELL_RESOLVE, self, card, controller,
            targets=item.targets, item=item
        ):
            # Auto-generate target summary if no specific log was written
            # (check if last log entry already describes this spell's effect)
            # Check if handler wrote a meaningful log naming the spell
            _handler_logs = self.log[_pre_log_len:]
            _spell_logged = any(name in l for l in _handler_logs)
            _already_logged = _spell_logged
            if not _already_logged:
                effects = []
                # Creature deaths (prefer over face damage — spell targeted creature)
                killed = [cname for iid, (cname, _) in _pre_creatures.items()
                          if not any(c.instance_id == iid for c in _opp.creatures)]
                if killed:
                    effects.append(f"kills {', '.join(killed)}")
                elif _opp.life < _pre_life:
                    # Only log face damage if no creature died (not a creature spell)
                    effects.append(f"{_pre_life - _opp.life} damage → life {_opp.life}")
                # Discard
                if len(_opp.hand) < _pre_hand:
                    effects.append(f"opponent discards {_pre_hand - len(_opp.hand)}")
                if effects:
                    self.log.append(f"T{self.display_turn} P{controller+1}: "
                                    f"{name} → {', '.join(effects)}")
            return  # Registry handled it

        # ── Generic fallback: parse abilities from oracle text ──
        # All named card effects are now handled by EFFECT_REGISTRY (card_effects.py).
        # Legacy named-card blocks have been removed (Phase 2D migration).
        # Only the generic ability parser below remains as a last resort.
        # (Legacy named-card blocks deleted — all handled by EFFECT_REGISTRY)

        # ── Generic effect handling ──
        effects = []
        for ability in card.template.abilities:
            if ability.description:
                effects.append(ability)

        for ability in effects:
            desc = ability.description.lower()

            if "damage" in desc:
                amount = 0
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue

                if item.targets:
                    for tid in item.targets:
                        target = self.get_card_by_id(tid)
                        if target and target.zone == "battlefield" and target.template.is_creature:
                            target.damage_marked += amount
                            if target.is_dead:
                                self._creature_dies(target)
                elif "each opponent" in desc or "player" in desc:
                    self.players[opponent].life -= amount
                    self.players[controller].damage_dealt_this_turn += amount
                elif amount > 0:
                    self.players[opponent].life -= amount
                    self.players[controller].damage_dealt_this_turn += amount

            elif "destroy" in desc:
                if "all" in desc:
                    for p in self.players:
                        creatures_to_destroy = [c for c in p.creatures
                                                if Keyword.INDESTRUCTIBLE not in c.keywords]
                        for creature in creatures_to_destroy:
                            self._creature_dies(creature)
                elif item.targets:
                    for tid in item.targets:
                        target = self.get_card_by_id(tid)
                        if target and target.zone == "battlefield":
                            if Keyword.INDESTRUCTIBLE not in target.keywords:
                                self._permanent_destroyed(target)

            elif "exile" in desc:
                if "all" in desc:
                    for p in self.players:
                        to_exile = [c for c in p.battlefield
                                    if not c.template.is_land]
                        for c in to_exile:
                            self._exile_permanent(c)
                elif item.targets:
                    for tid in item.targets:
                        target = self.get_card_by_id(tid)
                        if target and target.zone == "battlefield":
                            self._exile_permanent(target)

            elif "counter" in desc:
                # Validate counterspell targeting restrictions
                counter_oracle = (card.template.oracle_text or '').lower()
                target_template = None
                if item.targets:
                    for tid in item.targets:
                        for si in self.stack.items:
                            if si.source.instance_id == tid:
                                target_template = si.source.template
                                break
                elif not self.stack.is_empty:
                    target_template = self.stack.top.source.template if self.stack.top else None

                # Noncreature-only counters can't hit creatures
                if target_template and 'noncreature' in counter_oracle and target_template.is_creature:
                    self.log.append(f"T{self.display_turn}: {card.name} fizzles (can't counter creature)")
                elif target_template and 'instant or sorcery' in counter_oracle and not (target_template.is_instant or target_template.is_sorcery):
                    self.log.append(f"T{self.display_turn}: {card.name} fizzles (wrong target type)")
                elif item.targets:
                    for tid in item.targets:
                        # Find the targeted spell on the stack
                        for i, stack_item in enumerate(self.stack.items):
                            if stack_item.source.instance_id == tid:
                                countered = self.stack.items.pop(i)
                                countered_card = countered.source
                                countered_card.zone = "graveyard"
                                self.players[countered_card.owner].graveyard.append(countered_card)
                                self.log.append(
                                    f"T{self.display_turn}: {countered_card.name} is countered")
                                break
                elif not self.stack.is_empty:
                    # No explicit target — counter the next spell on the stack
                    countered = self.stack.pop()
                    countered_card = countered.source
                    countered_card.zone = "graveyard"
                    self.players[countered_card.owner].graveyard.append(countered_card)
                    self.log.append(
                        f"T{self.display_turn}: {countered_card.name} is countered")

            elif "draw" in desc:
                amount = 1
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue
                self.draw_cards(controller, amount)

            elif "gain" in desc and "life" in desc:
                amount = 0
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue
                self.gain_life(controller, amount, "ability")

            elif "return" in desc and "hand" in desc:
                if item.targets:
                    for tid in item.targets:
                        target = self.get_card_by_id(tid)
                        if target and target.zone == "battlefield":
                            self._bounce_permanent(target)

            elif "search" in desc and "library" in desc and "land" in desc:
                player = self.players[controller]
                for i, card_in_lib in enumerate(player.library):
                    if card_in_lib.template.is_land:
                        land = player.library.pop(i)
                        land.controller = controller
                        land.enter_battlefield()
                        land.tapped = True
                        player.battlefield.append(land)
                        break

            elif "discard" in desc:
                amount = 1
                for word in desc.split():
                    try:
                        amount = int(word)
                        break
                    except ValueError:
                        continue
                target_player = opponent if "opponent" in desc else controller
                self._force_discard(target_player, amount)

            elif "create" in desc and "token" in desc:
                # Try to parse token from description
                import re
                token_match = re.search(r'create\s+(?:a|(\d+))\s+(\d+)/(\d+)', desc)
                if token_match:
                    count = int(token_match.group(1) or 1)
                    p = int(token_match.group(2))
                    t = int(token_match.group(3))
                    self.create_token(controller, "creature", count, p, t)

    # ─── BLINK ───────────────────────────────────────────────────

    def _blink_permanent(self, card: CardInstance, controller: int):
        """Exile a permanent and return it to the battlefield immediately."""
        if card in self.players[card.controller].battlefield:
            self.players[card.controller].battlefield.remove(card)
        card.zone = "exile"
        # Return immediately
        card.controller = controller
        card.enter_battlefield()
        self.players[controller].battlefield.append(card)
        self._handle_permanent_etb(card, controller)
        self.log.append(f"T{self.display_turn}: Blink {card.name}")

    # ─── ZONE CHANGES ────────────────────────────────────────────

    def _creature_dies(self, creature: CardInstance):
        """Handle a creature dying."""
        owner = creature.owner
        controller = creature.controller

        if creature in self.players[controller].battlefield:
            self.players[controller].battlefield.remove(creature)

        # Undying: return with +1/+1 counter
        if Keyword.UNDYING in creature.keywords and creature.plus_counters == 0:
            creature.zone = "graveyard"
            creature.reset_combat()
            creature.cleanup_damage()
            # Return to battlefield with +1/+1 counter
            creature.controller = controller
            creature.enter_battlefield()
            creature.plus_counters += 1
            self.players[controller].battlefield.append(creature)
            self.log.append(f"T{self.display_turn}: {creature.name} returns (undying)")
            return

        # Persist: return with -1/-1 counter
        if Keyword.PERSIST in creature.keywords and creature.minus_counters == 0:
            creature.zone = "graveyard"
            creature.reset_combat()
            creature.cleanup_damage()
            creature.controller = controller
            creature.enter_battlefield()
            creature.minus_counters += 1
            self.players[controller].battlefield.append(creature)
            self.log.append(f"T{self.display_turn}: {creature.name} returns (persist)")
            return

        # Equipment falls off: when equipped creature dies, mark equipment
        # as unattached so the AI must pay to re-equip
        equip_tags_on_creature = [
            t for t in creature.instance_tags
            if t.startswith("equipped_")
        ]
        if equip_tags_on_creature:
            for tag in equip_tags_on_creature:
                # Parse the equipment instance_id from the tag
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    equip_perm = self.get_card_by_id(equip_iid)
                    if equip_perm:
                        equip_perm.instance_tags.discard("equipment_attached")
                        equip_perm.instance_tags.add("equipment_unattached")
                        self.log.append(
                            f"T{self.display_turn}: {equip_perm.template.name} falls off "
                            f"{creature.name} (unattached)")
                except (ValueError, AttributeError):
                    pass

        creature.zone = "graveyard"
        creature.reset_combat()
        creature.cleanup_damage()
        creature._dashed = False  # Clear Dash flag on death
        creature._evoked = False  # Clear Evoke flag on death
        self.players[owner].graveyard.append(creature)
        self.players[controller].creatures_died_this_turn += 1

        # Generic oracle-text-based dies triggers
        if creature.template.name not in EFFECT_REGISTRY._handlers:
            from .oracle_resolver import resolve_dies_trigger
            resolve_dies_trigger(self, creature, controller)

        self.log.append(f"T{self.display_turn}: {creature.name} dies")

    def _permanent_destroyed(self, permanent: CardInstance):
        if permanent.template.is_creature:
            self._creature_dies(permanent)
        else:
            self.zone_mgr.move_card(
                self, permanent, "battlefield", "graveyard",
                cause="destroyed"
            )

    def _exile_permanent(self, permanent: CardInstance):
        self.zone_mgr.move_card(
            self, permanent, "battlefield", "exile",
            cause="exiled"
        )

    def _bounce_permanent(self, permanent: CardInstance):
        self.zone_mgr.move_card(
            self, permanent, "battlefield", "hand",
            cause="bounced"
        )

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
                # Smart discard: prefer cards that are good in the graveyard
                # or least useful in hand
                card = self._choose_self_discard(player)
            else:
                # Opponent forced: discard highest CMC (least castable)
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
        player = self.players[player_idx]
        for card in player.battlefield:
            card.untap()
            card.new_turn()
        player.reset_turn_tracking()
        # Recalculate extra land drops from permanents on battlefield
        # (Azusa gives +2, Dryad of the Ilysian Grove gives +1)
        extra = 0
        for c in player.battlefield:
            if c.template.extra_land_drops > 0:
                extra += c.template.extra_land_drops
        player.extra_land_drops = extra
        player.mana_pool.empty()
        self._global_storm_count = 0

    def combat_damage(self, attackers: List[CardInstance],
                      blockers: Dict[int, List[int]]):
        defending_player = 1 - self.active_player
        attacking_player = self.active_player

        # Split into first-strike and regular damage steps
        first_strikers = [a for a in attackers
                          if Keyword.FIRST_STRIKE in a.keywords
                          or Keyword.DOUBLE_STRIKE in a.keywords]
        regular_strikers = [a for a in attackers
                            if Keyword.FIRST_STRIKE not in a.keywords
                            and Keyword.DOUBLE_STRIKE not in a.keywords]
        # Double strikers also deal regular damage
        double_strikers = [a for a in attackers
                           if Keyword.DOUBLE_STRIKE in a.keywords]

        # First-strike damage step
        if first_strikers:
            self._assign_combat_damage(first_strikers, blockers,
                                       defending_player, attacking_player,
                                       first_strike_step=True)
            self.check_state_based_actions()

        # Regular damage step
        regular_plus_double = regular_strikers + double_strikers
        if regular_plus_double:
            self._assign_combat_damage(regular_plus_double, blockers,
                                       defending_player, attacking_player,
                                       first_strike_step=False)

        # If no first strikers, still need to process all attackers
        if not first_strikers and not regular_plus_double:
            # Edge case: all attackers already processed
            pass

        for attacker in attackers:
            attacker.attacked_this_turn = True

        self.check_state_based_actions()

    def _assign_combat_damage(self, damage_dealers: List[CardInstance],
                               blockers: Dict[int, List[int]],
                               defending_player: int,
                               attacking_player: int,
                               first_strike_step: bool = False):
        """Assign and deal combat damage for a set of attackers."""
        step_label = " (first strike)" if first_strike_step else ""
        for attacker in damage_dealers:
            if attacker.zone != "battlefield":
                continue  # Died in first-strike step
            attacker_id = attacker.instance_id
            blocker_ids = blockers.get(attacker_id, [])
            total_damage_dealt = 0

            if blocker_ids:
                # Blocked: deal damage to blockers
                for blocker_id in blocker_ids:
                    blocker = self.get_card_by_id(blocker_id)
                    if blocker and blocker.zone == "battlefield":
                        blocker.damage_marked += attacker.power
                        total_damage_dealt += attacker.power
                        self.log.append(
                            f"T{self.display_turn} P{attacking_player+1}: "
                            f"{attacker.name} ({attacker.power}/{attacker.toughness}) "
                            f"deals {attacker.power} combat damage to "
                            f"{blocker.name} ({blocker.power}/{blocker.toughness}){step_label}"
                        )
                        # Blocker deals damage back (only in regular step,
                        # or if blocker has first strike)
                        blocker_has_fs = (Keyword.FIRST_STRIKE in blocker.keywords
                                          or Keyword.DOUBLE_STRIKE in blocker.keywords)
                        if (first_strike_step and blocker_has_fs) or \
                           (not first_strike_step and not blocker_has_fs) or \
                           (not first_strike_step and Keyword.DOUBLE_STRIKE in blocker.keywords):
                            attacker.damage_marked += blocker.power
                            self.log.append(
                                f"T{self.display_turn} P{defending_player+1}: "
                                f"{blocker.name} ({blocker.power}/{blocker.toughness}) "
                                f"deals {blocker.power} combat damage to "
                                f"{attacker.name} ({attacker.power}/{attacker.toughness}){step_label}"
                            )

                        # Deathtouch
                        if Keyword.DEATHTOUCH in attacker.keywords and attacker.power > 0:
                            blocker.damage_marked = max(blocker.damage_marked, blocker.toughness)
                            self.log.append(
                                f"T{self.display_turn}: {attacker.name} deathtouch kills {blocker.name}"
                            )
                        if Keyword.DEATHTOUCH in blocker.keywords and blocker.power > 0:
                            attacker.damage_marked = max(attacker.damage_marked, attacker.toughness)
                            self.log.append(
                                f"T{self.display_turn}: {blocker.name} deathtouch kills {attacker.name}"
                            )

                # Trample: excess damage goes to defending player
                if Keyword.TRAMPLE in attacker.keywords:
                    total_blocker_toughness = sum(
                        self.get_card_by_id(bid).toughness
                        for bid in blocker_ids
                        if self.get_card_by_id(bid) and self.get_card_by_id(bid).zone == "battlefield"
                    )
                    excess = attacker.power - total_blocker_toughness
                    if excess > 0:
                        self.players[defending_player].life -= excess
                        self.players[attacking_player].damage_dealt_this_turn += excess
                        total_damage_dealt += excess
                        self.log.append(
                            f"T{self.display_turn} P{attacking_player+1}: "
                            f"{attacker.name} tramples {excess} damage through to P{defending_player+1} "
                            f"(life: {self.players[defending_player].life})"
                        )
            else:
                # Unblocked: deal damage to defending player
                damage = attacker.power
                if damage > 0:
                    self.players[defending_player].life -= damage
                    self.players[attacking_player].damage_dealt_this_turn += damage
                    total_damage_dealt = damage
                    self.log.append(
                        f"T{self.display_turn} P{attacking_player+1}: "
                        f"{attacker.name} ({attacker.power}/{attacker.toughness}) "
                        f"deals {damage} combat damage to P{defending_player+1} "
                        f"(life: {self.players[defending_player].life}){step_label}"
                    )

            # Lifelink: applies to ALL damage dealt (blocked or unblocked)
            if Keyword.LIFELINK in attacker.keywords and total_damage_dealt > 0:
                self.gain_life(attacking_player, total_damage_dealt, f"{attacker.name} lifelink")
                self.log.append(
                    f"T{self.display_turn} P{attacking_player+1}: "
                    f"{attacker.name} lifelink gains {total_damage_dealt} life "
                    f"(life: {self.players[attacking_player].life})"
                )

            # Generic "deals combat damage to a player" triggers from oracle
            is_blocked = attacker.instance_id in blockers and bool(blockers[attacker.instance_id])
            if total_damage_dealt > 0 and not is_blocked:
                a_oracle = (attacker.template.oracle_text or '').lower()
                if 'combat damage to a player' in a_oracle or 'deals damage to' in a_oracle:
                    if 'treasure' in a_oracle or 'create a treasure' in a_oracle:
                        self.create_token(attacking_player, "treasure", count=1)
                        self.log.append(
                            f"T{self.display_turn} P{attacking_player+1}: "
                            f"{attacker.name} creates Treasure token"
                        )
                    # Draw on combat damage (Psychic Frog, Ophiomancer etc.)
                    if 'draw a card' in a_oracle:
                        self.draw_cards(attacking_player, 1)
                        self.log.append(
                            f"T{self.display_turn} P{attacking_player+1}: "
                            f"{attacker.name} deals combat damage — draw a card"
                        )

            # Generic: "Whenever a creature you control deals combat damage to a player,
            # if you have more energy than that player has life, create a 1/1 [type] token."
            # Detects the energy-vs-life token pattern from oracle text.
            if total_damage_dealt > 0 and not is_blocked:
                for watcher in self.players[attacking_player].battlefield:
                    w_oracle = (watcher.template.oracle_text or '').lower()
                    if ('combat damage to a player' in w_oracle
                            and 'energy' in w_oracle
                            and 'life' in w_oracle
                            and 'create' in w_oracle
                            and 'token' in w_oracle):
                        my_energy = self.players[attacking_player].energy_counters
                        opp_life = self.players[defending_player].life
                        if my_energy > opp_life:
                            # Parse token type from oracle (e.g., "1/1 white Cat")
                            import re as _re
                            m = _re.search(r'create a ([\d]+)/([\d]+)\s+\w+\s+(\w+)\s+(?:creature\s+)?token', w_oracle)
                            p, t = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
                            self.create_token(attacking_player, "cat", count=1, power=p, toughness=t)
                            self.log.append(
                                f"T{self.display_turn} P{attacking_player+1}: "
                                f"{watcher.name} — created {p}/{t} token "
                                f"(energy {my_energy} > life {opp_life})"
                            )

    def end_of_turn_cleanup(self):
        """Handle end-of-turn delayed triggers (e.g., Goryo's exile, Dash return)."""
        # Ragavan "may cast this turn": if card is still in hand, exile it
        for player in self.players:
            to_exile = [c for c in list(player.hand)
                        if getattr(c, "_ragavan_return_to_exile", False)]
            for card in to_exile:
                player.hand.remove(card)
                card.zone = "exile"
                player.exile.append(card)
                card._ragavan_return_to_exile = False
                self.log.append(f"T{self.display_turn}: "
                                f"{card.name} returned to exile (uncast)")


        # Dash: return dashed creatures to their owner's hand
        for player in self.players:
            dashed_creatures = [c for c in player.battlefield if getattr(c, '_dashed', False)]
            for card in dashed_creatures:
                self.zone_mgr.move_card(
                    self, card, "battlefield", "hand",
                    cause="Dash return"
                )
                self.log.append(f"T{self.display_turn}: {card.name} returned to hand (Dash)")

        # Goryo's exile
        for card, controller in self._end_of_turn_exiles:
            if card.zone == "battlefield":
                self.zone_mgr.move_card(
                    self, card, "battlefield", "exile",
                    cause="Goryo's end-of-turn exile"
                )
                self.log.append(f"T{self.display_turn}: {card.name} exiled (end of turn)")
        self._end_of_turn_exiles.clear()

    def cleanup_step(self):
        active = self.players[self.active_player]

        # Clean up end-of-turn continuous effects
        self.continuous_effects.cleanup_end_of_turn()

        # Discard to hand size (7) - player chooses, so use smart discard
        while len(active.hand) > 7:
            card = self._choose_self_discard(active)
            self.zone_mgr.move_card(
                self, card, "hand", "graveyard",
                cause="discard to hand size"
            )

        # Remove damage from creatures
        for player in self.players:
            for creature in player.creatures:
                creature.cleanup_damage()

        # Empty mana pools
        for player in self.players:
            player.mana_pool.empty()

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
        # Draw a card
        self.draw_cards(player_idx, 1)
        # Log
        cost_desc = f"pay {cost['life']} life" if cost["life"] > 0 else f"pay {cost['mana']} mana"
        self.log.append(f"T{self.display_turn} P{player_idx+1}: "
                       f"Cycle {card.name} ({cost_desc}, draw a card)")
        return True

    ALL_COLORS = ["W", "U", "B", "R", "G"]

    def _has_leyline_of_guildpact(self, player_idx: int) -> bool:
        """Check if player controls a permanent that makes lands all basic types."""
        # Detects "lands you control are every basic land type" from oracle text
        return any('lands you control are every basic land type' in
                    (c.template.oracle_text or '').lower()
                   for c in self.players[player_idx].battlefield)

    def _effective_produces_mana(self, player_idx: int, land) -> list:
        """Return effective mana colors a land produces, considering Leyline of the Guildpact."""
        if self._has_leyline_of_guildpact(player_idx):
            return self.ALL_COLORS
        return land.template.produces_mana

    def _count_domain(self, player_idx: int) -> int:
        """Count basic land types among lands controlled by a player."""
        # Check for effects that make lands every basic land type
        for c in self.players[player_idx].battlefield:
            if 'lands you control are every basic land type' in (c.template.oracle_text or '').lower():
                # As long as we control at least one land, domain = 5
                if any(l.template.is_land
                       for l in self.players[player_idx].battlefield):
                    return 5
        BASIC_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}
        found = set()
        for land in self.players[player_idx].battlefield:
            if land.template.is_land:
                for st in land.template.subtypes:
                    if st in BASIC_TYPES:
                        found.add(st)
        return len(found)

    def get_valid_attackers(self, player_idx: int) -> List[CardInstance]:
        return [c for c in self.players[player_idx].creatures if c.can_attack]

    def get_valid_blockers(self, player_idx: int) -> List[CardInstance]:
        return [c for c in self.players[player_idx].creatures if c.can_block]

    # ─── GRISELBRAND ACTIVATED ABILITY ───────────────────────────

    def activate_griselbrand(self, controller: int, card: CardInstance):
        """Pay 7 life, draw 7 cards."""
        player = self.players[controller]
        if player.life >= 8:  # Keep at least 1 life
            player.life -= 7
            self.draw_cards(controller, 7)
            self.log.append(f"T{self.display_turn} P{controller+1}: "
                            f"Griselbrand: pay 7 life, draw 7")
