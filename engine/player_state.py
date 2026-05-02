"""
Player state — extracted from engine/game_state.py.

Contains:
- class PlayerState (dataclass; per-player zones, life, mana, counters,
  per-turn tracking).
- TOKEN_DEFS (token archetype table consumed by create_token).
- _parse_planeswalker_abilities (oracle-text → loyalty ability dict).

Re-exported from engine/game_state.py so existing importers of
`engine.game_state.PlayerState` etc. continue to work unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .cards import CardInstance, CardType, Keyword
from .constants import STARTING_LIFE
from .mana import ManaPool


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
    # Diminishing-return budget for `_eval_evoke`: each successful
    # removal-class evoke this turn ramps the cost of the next one.
    # See `ai/board_eval.py::_eval_evoke` for consumption.
    removal_evokes_resolved_this_turn: int = 0
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
    post_combo_push_turns: int = 0  # sustained PUSH_DAMAGE window after mass reanimate

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
        self.removal_evokes_resolved_this_turn = 0
        self.silenced_this_turn = False
        # Consume a pending silence from Orim's Chant cast on the previous
        # opponent turn (Isochron Scepter lock pattern).
        if getattr(self, 'silenced_next_turn', False):
            self.silenced_this_turn = True
            self.silenced_next_turn = False
        self.temp_cost_reduction = 0
        self._landfall_count_this_turn = 0


# Planeswalker loyalty ability definitions: (plus_amount, minus_amount, ult_amount)
def _parse_planeswalker_abilities(oracle_text: str, loyalty: int = 0) -> dict:
    """Parse planeswalker abilities from oracle text.

    Detects [+N], [-N], [0] loyalty ability patterns.
    Returns dict with 'plus', 'minus', 'ult', 'zero', 'starting_loyalty'.
    """
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
