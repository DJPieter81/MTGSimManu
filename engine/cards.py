"""
MTG Card Model
Defines card types, subtypes, abilities, and the core Card class.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Callable, Any, TYPE_CHECKING
from enum import Enum, Flag, auto
from .mana import ManaCost, Color

if TYPE_CHECKING:
    from .game_state import GameState


class CardType(Enum):
    CREATURE = "creature"
    INSTANT = "instant"
    SORCERY = "sorcery"
    ENCHANTMENT = "enchantment"
    ARTIFACT = "artifact"
    PLANESWALKER = "planeswalker"
    LAND = "land"


class Supertype(Enum):
    LEGENDARY = "legendary"
    BASIC = "basic"
    SNOW = "snow"


class Keyword(Enum):
    FLYING = "flying"
    FIRST_STRIKE = "first_strike"
    DOUBLE_STRIKE = "double_strike"
    DEATHTOUCH = "deathtouch"
    LIFELINK = "lifelink"
    TRAMPLE = "trample"
    HASTE = "haste"
    VIGILANCE = "vigilance"
    REACH = "reach"
    MENACE = "menace"
    FLASH = "flash"
    HEXPROOF = "hexproof"
    INDESTRUCTIBLE = "indestructible"
    PROTECTION = "protection"
    DEFENDER = "defender"
    CASCADE = "cascade"
    CONVOKE = "convoke"
    AFFINITY = "affinity"
    PROWESS = "prowess"
    UNDYING = "undying"
    PERSIST = "persist"
    UNEARTH = "unearth"
    EVOKE = "evoke"
    SUSPEND = "suspend"
    STORM = "storm"
    ANNIHILATOR = "annihilator"


class AbilityType(Enum):
    ACTIVATED = "activated"
    TRIGGERED = "triggered"
    STATIC = "static"
    MANA_ABILITY = "mana_ability"
    ETB = "etb"  # enters the battlefield
    LTB = "ltb"  # leaves the battlefield
    DIES = "dies"
    ATTACK = "attack"
    CAST = "cast"
    UPKEEP = "upkeep"
    REPLACEMENT = "replacement"


@dataclass
class Ability:
    """Represents a card ability."""
    ability_type: AbilityType
    description: str
    effect: Optional[Callable] = None  # function(game_state, source, controller) -> None
    cost: Optional[ManaCost] = None
    tap_cost: bool = False  # requires tapping
    condition: Optional[Callable] = None  # function(game_state, source) -> bool
    targets_required: int = 0
    target_filter: Optional[Callable] = None  # function(game_state, potential_target) -> bool
    keyword: Optional[Keyword] = None
    trigger_condition: Optional[str] = None  # for triggered abilities
    priority: int = 0  # for ordering simultaneous triggers

    def can_activate(self, game_state: "GameState", source: "CardInstance", controller_idx: int) -> bool:
        if self.condition and not self.condition(game_state, source):
            return False
        if self.tap_cost and source.tapped:
            return False
        if self.cost:
            pool = game_state.players[controller_idx].mana_pool
            if not pool.can_pay(self.cost):
                return False
        return True


@dataclass
class CardTemplate:
    """Template for a card (shared data, not instance-specific)."""
    name: str
    card_types: List[CardType]
    mana_cost: ManaCost
    supertypes: List[Supertype] = field(default_factory=list)
    subtypes: List[str] = field(default_factory=list)
    power: Optional[int] = None
    toughness: Optional[int] = None
    loyalty: Optional[int] = None
    keywords: Set[Keyword] = field(default_factory=set)
    abilities: List[Ability] = field(default_factory=list)
    color_identity: Set[Color] = field(default_factory=set)
    # For lands
    produces_mana: List[str] = field(default_factory=list)  # e.g., ["W", "R"]
    enters_tapped: bool = False
    # Life payment to enter untapped (shock lands = 2, derived from oracle text)
    untap_life_cost: int = 0
    # Conditional untap: max other lands to enter untapped (fast lands = 2)
    # -1 means no conditional check (always tapped or always untapped)
    untap_max_other_lands: int = -1
    # Self-damage when tapping for colored mana (pain lands = 1)
    tap_damage: int = 0
    # For split/modal cards
    is_modal: bool = False
    modes: List[Dict] = field(default_factory=list)
    # Oracle text (raw rules text from card database)
    oracle_text: str = ""
    # Tags for AI strategy
    tags: Set[str] = field(default_factory=set)  # e.g., {"removal", "threat", "ramp"}
    # Evoke cost
    evoke_cost: Optional[ManaCost] = None
    # Dash cost (alternative cast: gains haste, returns to hand at end of turn)
    dash_cost: Optional[int] = None  # CMC of dash cost, e.g. 2 for {1}{R}
    # Escape cost (alternative cast from graveyard)
    escape_cost: Optional[int] = None  # CMC of escape cost, e.g. 4 for {R}{R}{W}{W}
    escape_exile_count: int = 0  # Number of other cards to exile from graveyard
    # Equipment
    equip_cost: Optional[int] = None  # CMC to equip, e.g. 1 for Cranial Plating
    # Delve: exile cards from graveyard to reduce generic mana cost
    has_delve: bool = False
    # Extra land drops per turn (Azusa, Dryad, etc.)
    extra_land_drops: int = 0
    # Conditional mana bonus: extra mana produced when a condition is met
    # Format: {"condition": "tron", "bonus": 2} means +2C when Tron assembled
    # Parsed from oracle text patterns like "If you control an Urza's..."
    conditional_mana: Optional[Dict] = None
    # Oracle-derived properties (populated by oracle_parser at load time)
    # These replace hardcoded data tables in game_state.py
    ritual_mana: Optional[tuple] = None       # (color, amount) e.g. ("R", 3)
    cycling_cost_data: Optional[Dict] = None  # {mana, life, colors}
    energy_production: int = 0                # number of {E} symbols
    is_cascade: bool = False                  # has cascade keyword
    x_cost_data: Optional[Dict] = None        # {multiplier, min_x}
    is_cost_reducer: bool = False             # reduces spell costs (from tags)
    domain_reduction: int = 0                 # cost reduction per basic land type
    back_face_oracle: str = ""                # oracle text for back face (transform cards)
    back_face_loyalty: int = 0                # starting loyalty for back face planeswalker
    power_scales_with: str = ""               # "domain", "tarmogoyf", "delirium", "graveyard"
    # Splice onto Arcane: oracle-derived from "Splice onto Arcane {cost}"
    splice_cost: Optional[int] = None          # mana cost to splice (None = no splice)
    is_arcane: bool = False                    # True if subtype includes Arcane

    @property
    def is_creature(self) -> bool:
        return CardType.CREATURE in self.card_types

    @property
    def is_land(self) -> bool:
        return CardType.LAND in self.card_types

    @property
    def is_instant(self) -> bool:
        return CardType.INSTANT in self.card_types

    @property
    def is_sorcery(self) -> bool:
        return CardType.SORCERY in self.card_types

    @property
    def is_spell(self) -> bool:
        return not self.is_land

    @property
    def cmc(self) -> int:
        return self.mana_cost.cmc

    @property
    def has_flash(self) -> bool:
        return Keyword.FLASH in self.keywords

    @property
    def has_haste(self) -> bool:
        return Keyword.HASTE in self.keywords

    def __hash__(self):
        return hash(self.name)


# These sets are no longer used — scaling is detected from oracle text via
# CardTemplate.power_scales_with ("domain", "tarmogoyf", "delirium", "graveyard").
# Kept as empty dicts for backwards compatibility only; will be removed next refactor.
DOMAIN_POWER_CREATURES: set = set()
TARMOGOYF_CREATURES: set = set()
DELIRIUM_CREATURES: set = set()
GRAVEYARD_SCALING_CREATURES: set = set()

BASIC_LAND_TYPES = {"Plains", "Island", "Swamp", "Mountain", "Forest"}


@dataclass
class CardInstance:
    """A specific instance of a card in a game (tracks state)."""
    template: CardTemplate
    owner: int  # player index
    controller: int  # current controller
    instance_id: int  # unique per game
    zone: str = "library"  # library, hand, battlefield, graveyard, exile, stack
    tapped: bool = False
    summoning_sick: bool = False  # True when first enters battlefield
    # Counters
    plus_counters: int = 0
    minus_counters: int = 0
    loyalty_counters: int = 0
    other_counters: Dict[str, int] = field(default_factory=dict)
    # Combat state
    attacking: bool = False
    blocking: Optional[int] = None  # instance_id of creature being blocked
    blocked_by: List[int] = field(default_factory=list)
    # Damage
    damage_marked: int = 0
    # Temporary effects
    temp_power_mod: int = 0
    temp_toughness_mod: int = 0
    temp_keywords: Set[Keyword] = field(default_factory=set)
    # Tracking
    turned_face_up: bool = True
    entered_battlefield_this_turn: bool = False
    attacked_this_turn: bool = False
    # Energy counters (for energy decks)
    energy_produced: int = 0
    # Flashback (granted by Past in Flames)
    has_flashback: bool = False
    # Targets (when on stack)
    targets: List[int] = field(default_factory=list)  # instance_ids
    # Instance-level tags (for equipment effects etc.)
    instance_tags: Set[str] = field(default_factory=set)
    # Back-reference to game state (set when entering battlefield)
    _game_state: Any = field(default=None, repr=False)
    # Evoke tracking
    _evoked: bool = False
    _dashed: bool = False  # Cast via Dash: has haste, returns to hand at end of turn
    _escaped: bool = False  # Cast via Escape from graveyard

    @property
    def name(self) -> str:
        return self.template.name

    @property
    def power(self) -> int:
        base = self._dynamic_base_power()
        return base + self.plus_counters - self.minus_counters + self.temp_power_mod

    @property
    def toughness(self) -> int:
        base = self._dynamic_base_toughness()
        return base + self.plus_counters - self.minus_counters + self.temp_toughness_mod

    def _get_domain_count(self) -> int:
        """Count basic land types among lands controlled by this card's controller."""
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        # Leyline of the Guildpact makes all lands every basic land type
        for c in player.battlefield:
            if c.name == "Leyline of the Guildpact":
                if any(l.template.is_land for l in player.battlefield):
                    return 5
        found_types: set = set()
        for land in player.battlefield:
            if land.template.is_land:
                for st in land.template.subtypes:
                    if st in BASIC_LAND_TYPES:
                        found_types.add(st)
        return len(found_types)

    def _get_tarmogoyf_count(self) -> int:
        """Count card types among cards in ALL graveyards."""
        if self._game_state is None:
            return 0
        type_set: set = set()
        for player in self._game_state.players:
            for card in player.graveyard:
                for ct in card.template.card_types:
                    type_set.add(ct)
        return len(type_set)

    def _get_artifact_count(self) -> int:
        """Count artifacts controlled by this card's controller."""
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        return sum(1 for c in player.battlefield if CardType.ARTIFACT in c.template.card_types)

    def _get_controller_battlefield(self):
        """Get the controller's battlefield."""
        if self._game_state is None:
            return []
        return self._game_state.players[self.controller].battlefield

    def _has_delirium(self) -> bool:
        """Check if controller has 4+ card types in graveyard (delirium)."""
        if self._game_state is None:
            return False
        player = self._game_state.players[self.controller]
        type_set: set = set()
        for card in player.graveyard:
            for ct in card.template.card_types:
                type_set.add(ct)
        return len(type_set) >= 4

    def _get_gy_instants_sorceries(self) -> int:
        """Count instants and sorceries in controller's graveyard."""
        if self._game_state is None:
            return 0
        player = self._game_state.players[self.controller]
        return sum(1 for c in player.graveyard
                   if c.template.is_instant or c.template.is_sorcery)

    def _dynamic_base_power(self) -> int:
        """Calculate base power, accounting for domain and similar effects.
        Scaling type is detected at template load time from oracle text
        (CardTemplate.power_scales_with) — no card names hardcoded here.
        """
        if self.zone != "battlefield":
            return self.template.power or 0
        scaling = self.template.power_scales_with

        if scaling == "domain":
            return min(self._get_domain_count(), 4)
        if scaling == "tarmogoyf":
            return self._get_tarmogoyf_count()
        if scaling == "delirium":
            if self._has_delirium():
                # Parse bonus from oracle: "gets +N/+M" → use N for power
                import re as _re
                oracle = (self.template.oracle_text or '').lower()
                m = _re.search(r'gets?\s+\+(\d+)/\+(\d+)', oracle)
                bonus = int(m.group(1)) if m else 2
                return (self.template.power or 0) + bonus
            return self.template.power or 0
        if scaling == "graveyard":
            return (self.template.power or 0) + self._get_gy_instants_sorceries()

        base = self.template.power or 0
        # Construct Token and similar: "gets +N/+N for each artifact you control"
        # NB: must match the bonus-per-artifact pattern specifically. The naive
        # `'artifact you control' in oracle` match triggered on Affinity reminder
        # text ("costs {1} less to cast for each artifact you control") and
        # inflated every Affinity creature's power to the controller's artifact
        # count. Scope the match to the actual Construct/Plating pattern.
        import re as _re
        oracle = (self.template.oracle_text or '').lower()
        if _re.search(r'\+\d+/\+\d+\s+for\s+each\s+artifact\s+you\s+control', oracle):
            base = (self.template.power or 0) + self._get_artifact_count()
        # Equipment scaling (Cranial Plating, Nettlecyst, etc.)
        # Tags are equipped_{instance_id} — unique per equipment, supports stacking.
        for tag in self.instance_tags:
            if tag.startswith("equipped_"):
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    if self._game_state is None:
                        continue
                    equip_perm = self._game_state.get_card_by_id(equip_iid)
                    if equip_perm is None:
                        continue
                    eq_oracle = (equip_perm.template.oracle_text or '').lower()
                    if 'for each artifact' in eq_oracle or 'artifact you control' in eq_oracle:
                        base += self._get_artifact_count()
                except (ValueError, AttributeError):
                    pass
        return base

    def _dynamic_base_toughness(self) -> int:
        """Calculate base toughness — mirrors _dynamic_base_power scaling logic."""
        if self.zone != "battlefield":
            return self.template.toughness or 0
        scaling = self.template.power_scales_with

        if scaling == "domain":
            return min(self._get_domain_count(), 4)
        if scaling == "tarmogoyf":
            return self._get_tarmogoyf_count() + 1
        if scaling == "delirium":
            if self._has_delirium():
                import re as _re
                oracle = (self.template.oracle_text or '').lower()
                m = _re.search(r'gets?\s+\+(\d+)/\+(\d+)', oracle)
                bonus = int(m.group(2)) if m else 2
                return (self.template.toughness or 0) + bonus
            return self.template.toughness or 0
        if scaling == "graveyard":
            return (self.template.toughness or 0) + self._get_gy_instants_sorceries()

        base = self.template.toughness or 0
        import re as _re
        oracle = (self.template.oracle_text or '').lower()
        # Same tightening as _dynamic_base_power — see note above.
        if _re.search(r'\+\d+/\+\d+\s+for\s+each\s+artifact\s+you\s+control', oracle):
            base = (self.template.toughness or 0) + self._get_artifact_count()
        # Equipment toughness scaling — only applies when toughness component is non-zero.
        # e.g. Nettlecyst: +1/+1 for each artifact → toughness bonus applies
        # e.g. Cranial Plating: +1/+0 for each artifact → NO toughness bonus
        import re as _re2
        for tag in self.instance_tags:
            if tag.startswith("equipped_"):
                try:
                    equip_iid = int(tag[len("equipped_"):])
                    if self._game_state is None:
                        continue
                    equip_perm = self._game_state.get_card_by_id(equip_iid)
                    if equip_perm is None:
                        continue
                    eq_oracle = (equip_perm.template.oracle_text or '').lower()
                    # Parse +A/+B from the oracle — only add to toughness if B != 0
                    m = _re2.search(r'gets \+(\w+)/\+(\w+)\s+for each', eq_oracle)
                    if m:
                        tou_component = m.group(2)
                        if tou_component != '0':
                            base += self._get_artifact_count()
                except (ValueError, AttributeError):
                    pass
        return base

    @property
    def current_loyalty(self) -> int:
        return (self.template.loyalty or 0) + self.loyalty_counters

    @property
    def keywords(self) -> Set[Keyword]:
        return self.template.keywords | self.temp_keywords

    @property
    def has_summoning_sickness(self) -> bool:
        """A creature has summoning sickness if it entered this turn and doesn't have haste."""
        if not self.template.is_creature:
            return False
        if Keyword.HASTE in self.keywords:
            return False
        if self._dashed:  # Dash grants haste
            return False
        return self.summoning_sick

    @property
    def can_attack(self) -> bool:
        if not self.template.is_creature:
            return False
        if self.tapped:
            return False
        if self.has_summoning_sickness:
            return False
        if Keyword.DEFENDER in self.keywords:
            return False
        return True

    @property
    def can_block(self) -> bool:
        if not self.template.is_creature:
            return False
        if self.tapped:
            return False
        return True

    @property
    def is_dead(self) -> bool:
        if not self.template.is_creature:
            return False
        return self.damage_marked >= self.toughness or self.toughness <= 0

    def tap(self):
        self.tapped = True

    def untap(self):
        self.tapped = False

    def reset_combat(self):
        self.attacking = False
        self.blocking = None
        self.blocked_by = []

    def cleanup_damage(self):
        self.damage_marked = 0
        self.temp_power_mod = 0
        self.temp_toughness_mod = 0
        self.temp_keywords.clear()

    def new_turn(self):
        """Called at the start of controller's turn."""
        self.summoning_sick = False
        self.entered_battlefield_this_turn = False
        self.attacked_this_turn = False

    def enter_battlefield(self):
        self.zone = "battlefield"
        self.summoning_sick = True
        self.entered_battlefield_this_turn = True
        if self.template.is_land and self.template.enters_tapped:
            self.tapped = True

    def __hash__(self):
        return self.instance_id

    def __eq__(self, other):
        if isinstance(other, CardInstance):
            return self.instance_id == other.instance_id
        return False
