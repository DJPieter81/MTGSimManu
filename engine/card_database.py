"""
MTG Card Database
Loads and processes MTGJSON ModernAtomic data into simulation-ready card templates.
Parses oracle text to extract structured mechanics, effects, and abilities.
"""
from __future__ import annotations
import json
import re
import os
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from .mana import ManaCost, Color
from .cards import (
    CardTemplate, CardType, Supertype, Keyword, Ability, AbilityType
)

# Map MTGJSON type strings to our enums
TYPE_MAP = {
    "Creature": CardType.CREATURE,
    "Instant": CardType.INSTANT,
    "Sorcery": CardType.SORCERY,
    "Enchantment": CardType.ENCHANTMENT,
    "Artifact": CardType.ARTIFACT,
    "Planeswalker": CardType.PLANESWALKER,
    "Land": CardType.LAND,
}

SUPERTYPE_MAP = {
    "Legendary": Supertype.LEGENDARY,
    "Basic": Supertype.BASIC,
    "Snow": Supertype.SNOW,
}

KEYWORD_MAP = {
    "Flying": Keyword.FLYING,
    "First strike": Keyword.FIRST_STRIKE,
    "First Strike": Keyword.FIRST_STRIKE,
    "Double strike": Keyword.DOUBLE_STRIKE,
    "Double Strike": Keyword.DOUBLE_STRIKE,
    "Deathtouch": Keyword.DEATHTOUCH,
    "Lifelink": Keyword.LIFELINK,
    "Trample": Keyword.TRAMPLE,
    "Haste": Keyword.HASTE,
    "Vigilance": Keyword.VIGILANCE,
    "Reach": Keyword.REACH,
    "Menace": Keyword.MENACE,
    "Flash": Keyword.FLASH,
    "Hexproof": Keyword.HEXPROOF,
    "Indestructible": Keyword.INDESTRUCTIBLE,
    "Defender": Keyword.DEFENDER,
    "Cascade": Keyword.CASCADE,
    "Convoke": Keyword.CONVOKE,
    "Affinity": Keyword.AFFINITY,
    "Prowess": Keyword.PROWESS,
    "Undying": Keyword.UNDYING,
    "Persist": Keyword.PERSIST,
    "Unearth": Keyword.UNEARTH,
    "Evoke": Keyword.EVOKE,
    "Suspend": Keyword.SUSPEND,
    "Storm": Keyword.STORM,
    "Annihilator": Keyword.ANNIHILATOR,
}

COLOR_CHARS = {"W", "U", "B", "R", "G"}

COLOR_MAP = {
    "W": Color.WHITE,
    "U": Color.BLUE,
    "B": Color.BLACK,
    "R": Color.RED,
    "G": Color.GREEN,
}

# Mana symbols in oracle text for lands
LAND_MANA_PATTERNS = {
    r"\{T\}:\s*Add\s*\{W\}": ["W"],
    r"\{T\}:\s*Add\s*\{U\}": ["U"],
    r"\{T\}:\s*Add\s*\{B\}": ["B"],
    r"\{T\}:\s*Add\s*\{R\}": ["R"],
    r"\{T\}:\s*Add\s*\{G\}": ["G"],
    r"\{T\}:\s*Add\s*\{C\}": ["C"],
    r"\{T\}:\s*Add\s*\{W\}\s*or\s*\{U\}": ["W", "U"],
    r"\{T\}:\s*Add\s*\{W\}\s*or\s*\{B\}": ["W", "B"],
    r"\{T\}:\s*Add\s*\{W\}\s*or\s*\{R\}": ["W", "R"],
    r"\{T\}:\s*Add\s*\{W\}\s*or\s*\{G\}": ["W", "G"],
    r"\{T\}:\s*Add\s*\{U\}\s*or\s*\{B\}": ["U", "B"],
    r"\{T\}:\s*Add\s*\{U\}\s*or\s*\{R\}": ["U", "R"],
    r"\{T\}:\s*Add\s*\{U\}\s*or\s*\{G\}": ["U", "G"],
    r"\{T\}:\s*Add\s*\{B\}\s*or\s*\{R\}": ["B", "R"],
    r"\{T\}:\s*Add\s*\{B\}\s*or\s*\{G\}": ["B", "G"],
    r"\{T\}:\s*Add\s*\{R\}\s*or\s*\{G\}": ["R", "G"],
}

# Basic land subtypes produce specific mana
BASIC_LAND_SUBTYPES = {
    "Plains": ["W"],
    "Island": ["U"],
    "Swamp": ["B"],
    "Mountain": ["R"],
    "Forest": ["G"],
}

# Fetch land colors: derived from oracle text at module load time.
# Pattern: "Sacrifice this land: Search your library for a [types] card"
# Populated by _build_fetch_land_colors() after DB loads.
FETCH_LAND_COLORS: Dict[str, List[str]] = {}

# Basic land type → color mapping for fetch target resolution
_BASIC_TYPE_TO_COLOR = {
    "plains": "W", "island": "U", "swamp": "B",
    "mountain": "R", "forest": "G",
}


def _parse_fetch_colors_from_oracle(oracle_text: str) -> Optional[List[str]]:
    """Parse fetchable colors from oracle text.

    Returns list of color codes, or None if not a fetch land.
    """
    if not oracle_text:
        return None
    ot = oracle_text.lower()
    if 'sacrifice this land' not in ot or 'search your library' not in ot:
        return None

    # "search your library for a basic land card" → all colors
    if 'basic land card' in ot:
        return ["W", "U", "B", "R", "G"]

    # "search your library for a Plains or Island card" → W, U
    import re
    m = re.search(r'search your library for (?:a|an) (.+?) card', ot)
    if m:
        type_text = m.group(1).lower()
        colors = []
        for basic_type, color in _BASIC_TYPE_TO_COLOR.items():
            if basic_type in type_text:
                colors.append(color)
        if colors:
            return colors

    return None

# Hardcoded land sets removed — all land entry logic is now derived from
# oracle text via template properties: enters_tapped, untap_life_cost,
# untap_max_other_lands, tap_damage.


@dataclass
class OracleEffect:
    """Parsed effect from oracle text."""
    effect_type: str  # "damage", "destroy", "exile", "draw", "gain_life", "lose_life",
                      # "create_token", "counter", "bounce", "discard", "search_library",
                      # "pump", "buff_all", "ramp", "mill", "sacrifice", "tap", "untap",
                      # "energy", "treasure", "copy", "cascade", "storm", "reanimate"
    amount: int = 0
    target_type: str = ""  # "any", "creature", "player", "opponent", "self", "all_creatures"
    condition: str = ""
    raw_text: str = ""


class OracleTextParser:
    """Parses oracle text to extract structured game effects."""

    # Damage patterns
    DAMAGE_PATTERNS = [
        (r"deals?\s+(\d+)\s+damage\s+to\s+any\s+target", "any"),
        (r"deals?\s+(\d+)\s+damage\s+to\s+target\s+creature", "creature"),
        (r"deals?\s+(\d+)\s+damage\s+to\s+target\s+player", "player"),
        (r"deals?\s+(\d+)\s+damage\s+to\s+each\s+opponent", "each_opponent"),
        (r"deals?\s+(\d+)\s+damage\s+to\s+each\s+creature", "all_creatures"),
        (r"deals?\s+damage\s+equal\s+to\s+its\s+power", "power_based"),
    ]

    # Destroy patterns
    DESTROY_PATTERNS = [
        (r"destroy\s+target\s+creature", "creature"),
        (r"destroy\s+target\s+artifact", "artifact"),
        (r"destroy\s+target\s+enchantment", "enchantment"),
        (r"destroy\s+target\s+nonland\s+permanent", "nonland_permanent"),
        (r"destroy\s+target\s+permanent", "permanent"),
        (r"destroy\s+all\s+creatures", "all_creatures"),
        (r"destroy\s+all\s+nonland\s+permanents", "all_nonland"),
        (r"destroy\s+target\s+land", "land"),
    ]

    # Draw patterns
    DRAW_PATTERNS = [
        (r"draw\s+(\d+)\s+cards?", "self"),
        (r"draw\s+a\s+card", "self"),
        (r"target\s+player\s+draws?\s+(\d+)", "target_player"),
    ]

    # Life patterns
    LIFE_GAIN_PATTERNS = [
        (r"gains?\s+(\d+)\s+life", "self"),
        (r"you\s+gain\s+(\d+)\s+life", "self"),
    ]

    LIFE_LOSS_PATTERNS = [
        (r"loses?\s+(\d+)\s+life", "opponent"),
        (r"pay\s+(\d+)\s+life", "self"),
    ]

    # Counter spell patterns
    COUNTER_PATTERNS = [
        (r"counter\s+target\s+spell", "spell"),
        (r"counter\s+target\s+creature\s+spell", "creature_spell"),
        (r"counter\s+target\s+noncreature\s+spell", "noncreature_spell"),
        (r"counter\s+target\s+instant\s+or\s+sorcery\s+spell", "instant_or_sorcery_spell"),
    ]

    # Bounce patterns
    BOUNCE_PATTERNS = [
        (r"return\s+target\s+creature\s+to\s+its\s+owner's\s+hand", "creature"),
        (r"return\s+target\s+nonland\s+permanent\s+to\s+its\s+owner's\s+hand", "nonland_permanent"),
        (r"return\s+target\s+permanent\s+to\s+its\s+owner's\s+hand", "permanent"),
    ]

    # Exile patterns
    EXILE_PATTERNS = [
        (r"exile\s+target\s+creature", "creature"),
        (r"exile\s+target\s+nonland\s+permanent", "nonland_permanent"),
        (r"exile\s+target\s+permanent", "permanent"),
        (r"exile\s+all\s+creatures", "all_creatures"),
    ]

    # Discard patterns
    DISCARD_PATTERNS = [
        (r"target\s+(?:player|opponent)\s+discards?\s+(\d+)\s+cards?", "opponent"),
        (r"each\s+opponent\s+discards?\s+(\d+)\s+cards?", "each_opponent"),
        (r"discard\s+(\d+)\s+cards?", "self"),
        (r"discard\s+a\s+card", "self"),
    ]

    # Token patterns
    TOKEN_PATTERNS = [
        (r"create\s+(?:a|(\d+))\s+(\d+)/(\d+)\s+(\w+)", "token"),
        (r"create\s+a\s+Treasure\s+token", "treasure"),
        (r"create\s+a\s+Food\s+token", "food"),
        (r"create\s+a\s+Clue\s+token", "clue"),
    ]

    # Pump/buff patterns
    PUMP_PATTERNS = [
        (r"gets?\s+\+(\d+)/\+(\d+)\s+until\s+end\s+of\s+turn", "temp_pump"),
        (r"gets?\s+\+(\d+)/\+(\d+)", "pump"),
        (r"gets?\s+-(\d+)/-(\d+)", "debuff"),
        (r"creatures?\s+you\s+control\s+get\s+\+(\d+)/\+(\d+)", "team_pump"),
    ]

    # Search library patterns
    SEARCH_PATTERNS = [
        (r"search\s+your\s+library\s+for\s+(?:a|up\s+to\s+(\d+))\s+(?:basic\s+)?land", "search_land"),
        (r"search\s+your\s+library\s+for\s+a\s+creature", "search_creature"),
        (r"search\s+your\s+library\s+for\s+a\s+card", "search_any"),
    ]

    # Ritual / mana-producing spell patterns (text is lowercased before matching)
    RITUAL_PATTERNS = [
        (r"add\s*\{r\}\{r\}\{r\}", "RRR"),
        (r"add\s*\{r\}\{r\}", "RR"),
        (r"add\s*\{[wubrg]\}\{[wubrg]\}", "2_mana"),
        (r"add\s+two\s+mana\s+in\s+any\s+combination\s+of\s+colors", "2_any"),
        (r"add\s+three\s+mana\s+of\s+any\s+one\s+color", "3_any"),
        (r"add\s+an\s+amount\s+of\s+\{[wubrgc]\}", "variable"),
    ]

    # Cycling patterns
    CYCLING_PATTERNS = [
        (r"cycling\s*\{([^}]+)\}", "cycling"),
        (r"cycling\s*—", "cycling"),
        (r"cycling\s+(\d+)", "cycling"),
    ]

    # Energy patterns (text is lowercased before matching)
    ENERGY_PATTERNS = [
        (r"you\s+get\s+\{e\}\{e\}\{e\}", 3),
        (r"you\s+get\s+\{e\}\{e\}", 2),
        (r"you\s+get\s+\{e\}", 1),
        (r"get\s+(\d+)\s+\{e\}", 0),  # dynamic
    ]

    # Enters tapped patterns
    ENTERS_TAPPED_PATTERNS = [
        r"enters\s+(?:the\s+battlefield\s+)?tapped",
        r"it\s+enters\s+tapped",
    ]

    # Conditional untapped entry
    CONDITIONAL_UNTAPPED_PATTERNS = [
        r"you\s+may\s+pay\s+(\d+)\s+life.*?If\s+you\s+don't,\s+it\s+enters\s+tapped",
        r"unless\s+you\s+(?:control|reveal|pay)",
    ]

    @classmethod
    def parse(cls, oracle_text: str, card_name: str = "") -> List[OracleEffect]:
        """Parse oracle text and return a list of structured effects."""
        if not oracle_text:
            return []

        effects = []
        text_lower = oracle_text.lower()

        # Parse damage effects
        for pattern, target_type in cls.DAMAGE_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                amount = int(match.group(1)) if match.lastindex and match.group(1).isdigit() else 0
                effects.append(OracleEffect("damage", amount, target_type, raw_text=oracle_text))

        # Parse destroy effects
        for pattern, target_type in cls.DESTROY_PATTERNS:
            if re.search(pattern, text_lower):
                effects.append(OracleEffect("destroy", 0, target_type, raw_text=oracle_text))

        # Parse draw effects
        for pattern, target_type in cls.DRAW_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                amount = int(match.group(1)) if match.lastindex and match.group(1) and match.group(1).isdigit() else 1
                effects.append(OracleEffect("draw", amount, target_type, raw_text=oracle_text))

        # Parse life gain
        for pattern, target_type in cls.LIFE_GAIN_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                amount = int(match.group(1)) if match.lastindex and match.group(1).isdigit() else 0
                effects.append(OracleEffect("gain_life", amount, target_type, raw_text=oracle_text))

        # Parse life loss
        for pattern, target_type in cls.LIFE_LOSS_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                amount = int(match.group(1)) if match.lastindex and match.group(1).isdigit() else 0
                effects.append(OracleEffect("lose_life", amount, target_type, raw_text=oracle_text))

        # Parse counter effects
        for pattern, target_type in cls.COUNTER_PATTERNS:
            if re.search(pattern, text_lower):
                effects.append(OracleEffect("counter", 0, target_type, raw_text=oracle_text))

        # Parse bounce effects
        for pattern, target_type in cls.BOUNCE_PATTERNS:
            if re.search(pattern, text_lower):
                effects.append(OracleEffect("bounce", 0, target_type, raw_text=oracle_text))

        # Parse exile effects
        for pattern, target_type in cls.EXILE_PATTERNS:
            if re.search(pattern, text_lower):
                effects.append(OracleEffect("exile", 0, target_type, raw_text=oracle_text))

        # Parse discard effects
        for pattern, target_type in cls.DISCARD_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                amount = int(match.group(1)) if match.lastindex and match.group(1) and match.group(1).isdigit() else 1
                effects.append(OracleEffect("discard", amount, target_type, raw_text=oracle_text))

        # Parse token creation
        for pattern, target_type in cls.TOKEN_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                effects.append(OracleEffect("create_token", 1, target_type, raw_text=oracle_text))

        # Parse pump effects
        for pattern, target_type in cls.PUMP_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                effects.append(OracleEffect("pump", 0, target_type, raw_text=oracle_text))

        # Parse search library
        for pattern, target_type in cls.SEARCH_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                effects.append(OracleEffect("search_library", 0, target_type, raw_text=oracle_text))

        # Energy (with amount detection)
        energy_amount = 0
        for pattern, amount in cls.ENERGY_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                if amount == 0 and match.lastindex:
                    try:
                        energy_amount = int(match.group(1))
                    except (ValueError, IndexError):
                        energy_amount = 1
                else:
                    energy_amount = max(energy_amount, amount)
        if energy_amount > 0 or "energy" in text_lower or "{e}" in text_lower:
            effects.append(OracleEffect("energy", max(energy_amount, 1), "self", raw_text=oracle_text))

        # Ritual / mana production (non-land spells that add mana)
        for pattern, mana_type in cls.RITUAL_PATTERNS:
            if re.search(pattern, text_lower):
                effects.append(OracleEffect("ritual", 0, mana_type, raw_text=oracle_text))
                break

        # Cycling
        for pattern, _ in cls.CYCLING_PATTERNS:
            if re.search(pattern, text_lower):
                effects.append(OracleEffect("cycling", 0, "self", raw_text=oracle_text))
                break

        # Cascade
        if "cascade" in text_lower:
            effects.append(OracleEffect("cascade", 0, "self", raw_text=oracle_text))

        # Storm
        if "storm" in text_lower:
            effects.append(OracleEffect("storm", 0, "self", raw_text=oracle_text))

        # Reanimate
        if re.search(r"return.*from.*graveyard.*to.*battlefield", text_lower):
            effects.append(OracleEffect("reanimate", 0, "self", raw_text=oracle_text))

        # Mill
        mill_match = re.search(r"mills?\s+(\d+)\s+cards?", text_lower)
        if mill_match:
            effects.append(OracleEffect("mill", int(mill_match.group(1)), "self", raw_text=oracle_text))

        # Sacrifice
        if re.search(r"sacrifice\s+(?:a|target)\s+creature", text_lower):
            effects.append(OracleEffect("sacrifice", 0, "creature", raw_text=oracle_text))

        return effects

    @classmethod
    def detect_enters_tapped(cls, oracle_text: str, card_name: str = "") -> bool:
        """Detect if a land enters the battlefield tapped, from oracle text.

        Lands with optional life payment (shock lands) or conditional untap
        (fast lands) are handled separately via untap_life_cost and
        untap_max_other_lands template properties.
        Fetch lands don't enter tapped (they sacrifice immediately).
        """
        if card_name in FETCH_LAND_COLORS:
            return False

        if not oracle_text:
            return False
        text_lower = oracle_text.lower()

        # "you may pay N life. If you don't, it enters tapped" → not tapped
        # (handled by untap_life_cost; the default is untapped)
        if 'you may pay' in text_lower and 'enters tapped' in text_lower:
            return False

        for pattern in cls.ENTERS_TAPPED_PATTERNS:
            if re.search(pattern, text_lower):
                # Check for conditional untapped (fast lands, check lands, etc.)
                for cond_pattern in cls.CONDITIONAL_UNTAPPED_PATTERNS:
                    if re.search(cond_pattern, text_lower):
                        # Conditional: default tapped, runtime checks land count
                        return True
                return True
        return False

    @classmethod
    def detect_land_mana(cls, oracle_text: str, subtypes: List[str],
                         card_name: str = "") -> List[str]:
        """Detect what colors of mana a land can produce."""
        mana_colors = set()

        # Check fetch lands first
        if card_name in FETCH_LAND_COLORS:
            return FETCH_LAND_COLORS[card_name]

        # Check subtypes first (e.g., Sacred Foundry is a Mountain Plains)
        for subtype, colors in BASIC_LAND_SUBTYPES.items():
            if subtype in subtypes:
                mana_colors.update(colors)

        # Parse oracle text for mana abilities
        if oracle_text:
            text = oracle_text
            for pattern, colors in LAND_MANA_PATTERNS.items():
                if re.search(pattern, text):
                    mana_colors.update(colors)

            # Generic "Add one mana of any color" / "Add three mana of any one color"
            if re.search(r"add\s+(?:one|two|three)\s+mana\s+of\s+any\s+(?:one\s+)?color", text.lower()):
                mana_colors.update(["W", "U", "B", "R", "G"])

            # Add {C} for colorless-only lands
            if re.search(r"\{T\}:\s*Add\s*\{C\}", text):
                mana_colors.add("C")

            # Check for "Add {X} or {Y}" patterns more broadly
            # Match both 'Add {X}' and 'or {Y}' in patterns like 'Add {R} or {W}'
            # Also match consecutive symbols: 'Add {R}{G}' (bounce lands)
            add_matches = re.findall(r'[Aa]dd\s*\{([WUBRGC])\}', text)
            for m in add_matches:
                mana_colors.add(m)
            # Match consecutive {X} after Add: "Add {R}{G}" → also get G
            consecutive = re.findall(r'[Aa]dd\s*(?:\{[WUBRGC]\})+', text)
            for match in consecutive:
                for sym in re.findall(r'\{([WUBRGC])\}', match):
                    mana_colors.add(sym)
            # Also match 'or {X}' that follows an Add pattern
            or_matches = re.findall(r'\bor\s+\{([WUBRGC])\}', text)
            for m in or_matches:
                mana_colors.add(m)

        # Fallback: if land has color identity but no detected mana, use identity
        if not mana_colors and card_name:
            # Will be handled by the caller if needed
            pass

        return sorted(mana_colors)

    @classmethod
    def classify_card_role(cls, card_data: dict, effects: List[OracleEffect]) -> Set[str]:
        """Classify a card's strategic role based on its effects and stats."""
        tags = set()
        types = card_data.get("types", [])
        text = (card_data.get("text") or "").lower()
        keywords = card_data.get("keywords") or []
        power = card_data.get("power")
        toughness = card_data.get("toughness")
        mana_value = card_data.get("manaValue", 0)

        # Creature roles
        if "Creature" in types:
            tags.add("creature")
            if power and isinstance(power, str):
                try:
                    power = int(power)
                except ValueError:
                    power = 0
            if power and power >= 4:
                tags.add("threat")
            if power and mana_value and mana_value > 0 and power / mana_value >= 2:
                tags.add("efficient_threat")
            if mana_value <= 2 and "Creature" in types:
                tags.add("early_play")

        # Removal — but NOT blink spells that exile your own creatures,
        # and NOT lands (Channel lands like Boseiju have a destroy-mode
        # but their primary role is mana production — tagging them as
        # "removal" confuses mulligan / deploy logic).
        is_land = "Land" in types
        is_blink = any("you control" in e.raw_text.lower() and e.effect_type == "exile"
                       and ("return" in e.raw_text.lower() or "then return" in e.raw_text.lower())
                       for e in effects)
        for e in effects:
            if (not is_land) and e.effect_type in ("damage", "destroy", "exile") and e.target_type in (
                "creature", "any", "nonland_permanent", "permanent", "all_creatures",
                "artifact", "enchantment", "all_nonland",
            ):
                if is_blink and e.effect_type == "exile":
                    tags.add("blink")
                else:
                    tags.add("removal")
                if e.target_type in ("all_creatures", "all_nonland"):
                    tags.add("board_wipe")
                break

        # Counter
        for e in effects:
            if e.effect_type == "counter":
                tags.add("counterspell")
                tags.add("interaction")
                break

        # Card advantage
        for e in effects:
            if e.effect_type == "draw" and e.amount >= 2:
                tags.add("card_advantage")
            if e.effect_type == "draw":
                tags.add("cantrip")

        # Impulse draw: "exile the top N cards ... you may play/cast"
        # Covers Reckless Impulse, Wrenn's Resolve, Light Up the Stage, etc.
        import re as _impulse_re
        if _impulse_re.search(r'exile the top.{0,80}(?:you may (?:play|cast)|until)', text):
            tags.add('cantrip')
            if 'two' in text or '2' in text:
                tags.add('card_advantage')

        # Selection draw: "look at the top ... put one ... into your hand"
        # Covers Sleight of Hand, Anticipate, etc.
        if _impulse_re.search(r'(?:look at|reveal).*(?:put .* into your hand|draw)', text):
            if 'cantrip' not in tags:
                tags.add('cantrip')

        # Past in Flames: grants flashback = virtual card advantage
        if 'each instant and sorcery card in your graveyard gains flashback' in text:
            tags.add('cantrip')  # treat as card draw for AI purposes
            tags.add('card_advantage')

        # Pay-life-draw activated abilities (e.g., Griselbrand: "Pay 7 life: Draw seven cards")
        # Detected from oracle text pattern: "pay N life: draw"
        import re as _re
        _word_to_num = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
                        'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10}
        pay_life_draw = _re.search(
            r'pay\s+(\d+)\s+life.*?draw\s+(\d+|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+card',
            text, _re.IGNORECASE)
        if pay_life_draw:
            life_cost = int(pay_life_draw.group(1))
            draw_str = pay_life_draw.group(2).lower()
            draw_count = _word_to_num.get(draw_str, None)
            if draw_count is None:
                try:
                    draw_count = int(draw_str)
                except ValueError:
                    draw_count = 1
            tags.add("pay_life_draw")
            # Store the life cost and draw count as tags for the game runner
            tags.add(f"pay_life_cost_{life_cost}")
            tags.add(f"pay_life_draw_count_{draw_count}")

        # Discard
        for e in effects:
            if e.effect_type == "discard" and e.target_type in ("opponent", "each_opponent"):
                tags.add("discard")
                tags.add("interaction")

        # Silence / spell-lock effects (e.g., "target player can't cast spells")
        if "can't cast spells" in text or "can't cast spell" in text:
            tags.add("silence")

        # Ramp
        for e in effects:
            if e.effect_type == "search_library" and e.target_type == "search_land":
                tags.add("ramp")

        # Combo pieces
        for e in effects:
            if e.effect_type in ("storm", "cascade"):
                tags.add("combo")

        if "Living End" in (card_data.get("name") or ""):
            tags.add("combo")

        # Ritual / mana production
        for e in effects:
            if e.effect_type == "ritual":
                tags.add("ritual")
                tags.add("mana_source")

        # Cycling
        for e in effects:
            if e.effect_type == "cycling":
                tags.add("cycling")

        # Energy
        for e in effects:
            if e.effect_type == "energy":
                tags.add("energy")

        # Reanimation
        for e in effects:
            if e.effect_type == "reanimate":
                tags.add("reanimate")
                tags.add("combo")

        # Detect targeting restrictions from oracle text
        # e.g., "target legendary creature" -> targets_legendary
        # Uses negative lookbehind to exclude "nonlegendary"
        if "reanimate" in tags or "target" in text:
            import re
            if re.search(r'(?<!non)legendary creature', text) or \
               re.search(r'(?<!non)legendary permanent', text):
                tags.add("targets_legendary")

        # Pump spells (target creature gets +X/+X)
        for e in effects:
            if e.effect_type == "pump":
                tags.add("pump")
                break

        # Token creation
        for e in effects:
            if e.effect_type == "create_token":
                tags.add("token_maker")

        # Land
        if "Land" in types:
            tags.add("land")
            tags.add("mana_source")

        # Artifact mana
        if "Artifact" in types and any(e.effect_type == "energy" or "add" in text for e in effects):
            tags.add("mana_source")

        # Instant speed
        if "Instant" in types or "Flash" in keywords:
            tags.add("instant_speed")

        # Evasion
        evasion_keywords = {"Flying", "Trample", "Menace", "Unblockable"}
        for kw in keywords:
            if kw in evasion_keywords:
                tags.add("evasion")
                break

        # Cost reducers
        if any(phrase in text for phrase in [
            "cost {1} less", "cost {2} less", "costs {1} less", "costs {2} less",
            "spells you cast cost", "reduce the cost",
        ]):
            tags.add("cost_reducer")
            tags.add("mana_source")

        # Flashback / retrace / aftermath
        if "Flashback" in keywords or "flashback" in text:
            tags.add("flashback")
        if "retrace" in text:
            tags.add("flashback")  # functionally similar for AI

        # Tutor / search effects (non-land)
        if any(phrase in text for phrase in [
            "search your library for a card", "search your library for a creature",
            "search your library for an instant", "search your library for a sorcery",
        ]):
            tags.add("tutor")

        # Graveyard filler (self-mill, loot, discard-to-draw)
        if any(phrase in text for phrase in [
            "put the top", "mill", "discard a card, then draw",
            "discard a card. draw", "draw a card, then discard",
        ]):
            tags.add("graveyard_filler")

        # ETB value — creatures whose enter-the-battlefield trigger is worth blinking
        if "Creature" in types and any(phrase in text for phrase in [
            "when {this} enters", "when {this} enters the battlefield",
            "enters, ", "enters the battlefield",
        ]):
            # Check if the ETB does something valuable (not just "enters tapped")
            etb_valuable = any(phrase in text for phrase in [
                "draw", "destroy", "exile", "search", "gain", "create",
                "deal", "return", "counter", "discard", "look at",
                "put a", "mill", "each opponent",
            ])
            if etb_valuable:
                tags.add("etb_value")

        # Evoke — creatures that can be cast for an alternate cost from hand
        if "Evoke" in keywords or "evoke" in text:
            tags.add("evoke")

        # Protection / indestructible granters
        if any(phrase in text for phrase in [
            "hexproof", "indestructible", "protection from",
        ]) and "Creature" not in types:
            tags.add("protection")

        return tags


def parse_mana_cost_mtgjson(mana_cost_str: str) -> ManaCost:
    """Parse MTGJSON format mana cost like '{2}{W}{W}' or '{R}'."""
    if not mana_cost_str:
        return ManaCost()

    cost = ManaCost()
    # Extract all symbols between braces
    symbols = re.findall(r'\{([^}]+)\}', mana_cost_str)
    for sym in symbols:
        if sym == "W":
            cost.white += 1
        elif sym == "U":
            cost.blue += 1
        elif sym == "B":
            cost.black += 1
        elif sym == "R":
            cost.red += 1
        elif sym == "G":
            cost.green += 1
        elif sym == "C":
            cost.colorless += 1
        elif sym == "X":
            pass  # X costs handled separately
        elif sym.isdigit():
            cost.generic += int(sym)
        # Hybrid mana, phyrexian, etc. - simplified
        elif "/" in sym:
            # e.g., W/U, 2/W, W/P
            parts = sym.split("/")
            if parts[1] == "P":
                # Phyrexian - treat as colored
                if parts[0] in COLOR_CHARS:
                    cost.add_color(parts[0])
            elif parts[0].isdigit():
                # Generic/colored hybrid - treat as colored
                if parts[1] in ("W", "U", "B", "R", "G"):
                    cost.generic += 1  # simplified
            else:
                # Color/color hybrid - pick first
                cost.generic += 1  # simplified

    return cost


# Patch ManaCost to support add_color
def _add_color(self, color: str):
    if color == "W": self.white += 1
    elif color == "U": self.blue += 1
    elif color == "B": self.black += 1
    elif color == "R": self.red += 1
    elif color == "G": self.green += 1
    elif color == "C": self.colorless += 1

ManaCost.add_color = _add_color


class CardDatabase:
    """Loads and manages the complete Modern card pool."""

    def __init__(self, json_path: str = None):
        self.cards: Dict[str, CardTemplate] = {}
        self._raw_data: Dict[str, Any] = {}
        self._effects_cache: Dict[str, List[OracleEffect]] = {}
        if json_path:
            self.load(json_path)
        else:
            # Auto-discover ModernAtomic.json relative to this file or project root
            import os
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidates = [
                os.path.join(project_root, 'ModernAtomic.json'),
                os.path.join(project_root, 'ModernAtomic_mini.json'),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ModernAtomic.json'),
                '/home/ubuntu/mtg_simulator/ModernAtomic.json',
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    self.load(candidate)
                    break
            else:
                print('WARNING: ModernAtomic.json not found. All cards will be placeholders!')

    def load(self, json_path: str):
        """Load card data from MTGJSON ModernAtomic JSON file."""
        with open(json_path, 'r') as f:
            raw = json.load(f)

        card_data = raw.get("data", raw)
        count = 0
        errors = 0

        for card_name, card_entries in card_data.items():
            try:
                # card_entries is a list (multiple printings), take first
                entry = card_entries[0] if isinstance(card_entries, list) else card_entries
                template = self._build_template(card_name, entry)
                if template:
                    # Store back face data for double-faced cards (transform)
                    if (isinstance(card_entries, list) and len(card_entries) >= 2
                            and 'Planeswalker' in card_entries[1].get('types', [])):
                        back = card_entries[1]
                        template.back_face_oracle = back.get('text', '')
                        template.back_face_loyalty = int(back.get('loyalty', 0) or 0)
                    self.cards[card_name] = template
                    self._raw_data[card_name] = entry
                    count += 1
                    # DFC/split: also register under front face name
                    if " // " in card_name:
                        front_face = card_name.split(" // ")[0]
                        if front_face not in self.cards:
                            self.cards[front_face] = template
                            self._raw_data[front_face] = entry
            except Exception as e:
                errors += 1

        # Populate FETCH_LAND_COLORS from oracle text
        global FETCH_LAND_COLORS
        FETCH_LAND_COLORS.clear()
        for cname, tmpl in self.cards.items():
            if tmpl.is_land:
                fetch_colors = _parse_fetch_colors_from_oracle(tmpl.oracle_text)
                if fetch_colors:
                    FETCH_LAND_COLORS[cname] = fetch_colors

        print(f"Loaded {count} cards ({errors} errors)")

        if count < 1000:
            import subprocess, pathlib
            merge = pathlib.Path(__file__).parent.parent / 'merge_db.py'
            if merge.exists():
                print("DB too small — auto-running merge_db.py and reloading...")
                subprocess.run(['python', str(merge)], cwd=str(merge.parent))
                self.cards.clear()
                self._raw_data.clear()
                self.load(json_path)

    def _build_template(self, name: str, data: dict) -> Optional[CardTemplate]:
        """Build a CardTemplate from MTGJSON card data."""
        # Parse types
        card_types = []
        for t in data.get("types", []):
            if t in TYPE_MAP:
                card_types.append(TYPE_MAP[t])
        if not card_types:
            return None

        # Parse supertypes
        supertypes = []
        for st in data.get("supertypes", []):
            if st in SUPERTYPE_MAP:
                supertypes.append(SUPERTYPE_MAP[st])

        # Parse mana cost
        mana_cost = parse_mana_cost_mtgjson(data.get("manaCost", ""))

        # Parse keywords
        keywords = set()
        for kw in (data.get("keywords") or []):
            if kw in KEYWORD_MAP:
                keywords.add(KEYWORD_MAP[kw])

        # Also detect keywords from oracle text
        oracle_text = data.get("text", "") or ""
        text_lower = oracle_text.lower()
        for kw_str, kw_enum in KEYWORD_MAP.items():
            if kw_str.lower() in text_lower and kw_enum not in keywords:
                # Only add if it appears as a standalone keyword (not a substring
                # of another keyword, e.g., "flash" must not match "flashback")
                pattern = r'(?:^|\n)' + re.escape(kw_str.lower()) + r'(?:\s|$|,|\n)'
                if re.search(pattern, text_lower):
                    keywords.add(kw_enum)

        # Parse power/toughness
        power = None
        toughness = None
        if data.get("power") is not None:
            try:
                power = int(data["power"])
            except (ValueError, TypeError):
                power = 0  # * or X
        if data.get("toughness") is not None:
            try:
                toughness = int(data["toughness"])
            except (ValueError, TypeError):
                toughness = 0

        # Parse loyalty
        loyalty = None
        if data.get("loyalty") is not None:
            try:
                loyalty = int(data["loyalty"])
            except (ValueError, TypeError):
                loyalty = 0

        # Parse color identity
        color_identity = set()
        for c in data.get("colorIdentity", []):
            if c in COLOR_MAP:
                color_identity.add(COLOR_MAP[c])

        # Parse oracle effects
        effects = OracleTextParser.parse(oracle_text, name)
        self._effects_cache[name] = effects

        # Classify card role
        tags = OracleTextParser.classify_card_role(data, effects)

        # Land mana production
        subtypes = data.get("subtypes", [])
        produces_mana = []
        enters_tapped = False
        untap_life_cost = 0
        untap_max_other_lands = -1
        tap_damage = 0
        if CardType.LAND in card_types:
            produces_mana = OracleTextParser.detect_land_mana(oracle_text, subtypes, card_name=name)
            enters_tapped = OracleTextParser.detect_enters_tapped(oracle_text, card_name=name)
            # Detect land entry conditions from oracle text
            if oracle_text:
                import re as _re
                ot = oracle_text.lower()
                # Optional life payment: "you may pay N life. If you don't, it enters tapped"
                life_match = _re.search(r'you may pay (\d+) life.*enters tapped', ot)
                if life_match:
                    untap_life_cost = int(life_match.group(1))
                    enters_tapped = False  # can enter untapped (default)
                # Conditional on land count: "enters tapped unless you control N or fewer other lands"
                lands_match = _re.search(r'enters tapped unless you control (\w+) or fewer other lands', ot)
                if lands_match:
                    word_to_num = {"two": 2, "three": 3, "one": 1, "zero": 0, "four": 4}
                    untap_max_other_lands = word_to_num.get(lands_match.group(1), 2)
                # Pain land: "this land deals 1 damage to you"
                if 'deals 1 damage to you' in ot or 'this land deals 1 damage' in ot:
                    tap_damage = 1

        # Detect conditional mana production from oracle text
        # Pattern: "If you control an Urza's ... add {C}{C}{C} instead"
        # This detects Tron lands and any similar conditional mana producers
        conditional_mana = None
        if CardType.LAND in card_types and oracle_text:
            conditional_mana = self._detect_conditional_mana(oracle_text, name)

        # Build abilities from effects
        abilities = self._build_abilities(effects, oracle_text, name, data)

        # Parse evoke cost
        evoke_cost = None
        if Keyword.EVOKE in keywords:
            evoke_match = re.search(r'[Ee]voke[—\-]\s*(.+?)(?:\s*\(|$)', oracle_text)
            if evoke_match:
                evoke_str = evoke_match.group(1).strip()
                # Try to parse evoke cost
                evoke_cost = parse_mana_cost_mtgjson(evoke_str)

        template = CardTemplate(
            name=name,
            card_types=card_types,
            mana_cost=mana_cost,
            supertypes=supertypes,
            subtypes=subtypes,
            power=power,
            toughness=toughness,
            loyalty=loyalty,
            keywords=keywords,
            abilities=abilities,
            color_identity=color_identity,
            produces_mana=produces_mana,
            enters_tapped=enters_tapped,
            untap_life_cost=untap_life_cost,
            untap_max_other_lands=untap_max_other_lands,
            tap_damage=tap_damage,
            oracle_text=oracle_text,
            tags=tags,
            evoke_cost=evoke_cost,
            conditional_mana=conditional_mana,
        )

        # ── Oracle-derived properties (replaces per-card if/elif) ──
        from .oracle_parser import (
            has_delve, parse_dash_cost, parse_extra_land_drops,
            parse_escape_cost, parse_equip_cost, derive_tags_from_oracle,
            parse_splice_cost,
        )
        oracle_text = template.oracle_text or ''
        oracle_lower = oracle_text.lower()

        # Delve
        if has_delve(oracle_text):
            template.has_delve = True

        # Splice onto Arcane
        splice = parse_splice_cost(oracle_text)
        if splice is not None:
            template.splice_cost = splice
        # Arcane subtype
        if 'Arcane' in template.subtypes:
            template.is_arcane = True

        # Dash
        dash = parse_dash_cost(oracle_text)
        if dash is not None:
            template.dash_cost = dash

        # Extra land drops
        extra_lands = parse_extra_land_drops(oracle_text)
        if extra_lands > 0:
            template.extra_land_drops = extra_lands

        # Escape
        escape_data = parse_escape_cost(oracle_text)
        if escape_data:
            template.escape_cost = escape_data['cmc']
            template.escape_exile_count = escape_data['exile']

        # Equip cost
        equip = parse_equip_cost(oracle_text)
        if equip is not None:
            template.equip_cost = equip
            template.tags.add("equipment")

        # Prowess from oracle (backup: "noncreature spell" + "+1/+1" pump only)
        # Note: "surveil" alone does NOT indicate prowess — DRC has surveil but
        # its size bonus comes from delirium, not a +1/+1 pump on spells.
        if ('noncreature spell' in oracle_lower and '+1/+1' in oracle_lower):
            template.keywords.add(Keyword.PROWESS)

        # Oracle-derived tags (threat, ramp, token_maker, etb_value, etc.)
        derived_tags = derive_tags_from_oracle(
            oracle_text, template.keywords, template.card_types,
            template.subtypes, template.power or 0)
        template.tags.update(derived_tags)

        # ── Tag overrides for cards whose oracle text wasn't parsed correctly ──
        TAG_OVERRIDES = {
            "Galvanic Discharge": {"removal", "instant_speed", "energy"},
            "Dismember": {"removal", "instant_speed"},
            "Solitude": {"removal", "creature", "instant_speed", "evoke", "etb_value"},
            "Fury": {"removal", "creature", "instant_speed", "evoke", "etb_value"},
            "Grief": {"discard", "interaction", "creature", "instant_speed", "evoke", "etb_value"},
            "Subtlety": {"interaction", "creature", "instant_speed", "evasion", "evoke"},
            "Endurance": {"creature", "instant_speed", "evoke", "etb_value"},
            "Thoughtseize": {"discard", "interaction"},
            "Inquisition of Kozilek": {"discard", "interaction"},
            "Engineered Explosives": {"removal", "board_wipe"},
            "Wrath of the Skies": {"removal", "board_wipe", "energy"},
            "Supreme Verdict": {"removal", "board_wipe"},
            "Terminus": {"removal", "board_wipe"},
            # Storm pieces
            "Ruby Medallion": {"cost_reducer", "mana_source", "combo"},
            "Past in Flames": {"flashback", "combo"},
            "Gifts Ungiven": {"tutor", "combo", "instant_speed"},
            "Wish": {"tutor", "combo"},
            # Graveyard enablers
            "Unmarked Grave": {"tutor", "graveyard_filler", "combo"},
            "Faithful Mending": {"graveyard_filler", "cantrip", "instant_speed", "flashback"},
            # ETB value creatures
            "Omnath, Locus of Creation": {"creature", "etb_value", "threat", "cantrip"},
            "Snapcaster Mage": {"creature", "etb_value", "instant_speed", "early_play"},
            "Ice-Fang Coatl": {"creature", "etb_value", "instant_speed", "cantrip"},
            "Wall of Omens": {"creature", "etb_value", "cantrip"},
            "Eternal Witness": {"creature", "etb_value"},
            "Blade Splicer": {"creature", "etb_value", "token_maker"},
            "Flickerwisp": {"creature", "etb_value", "evasion"},
            "Thragtusk": {"creature", "etb_value", "threat"},
            "Mulldrifter": {"creature", "etb_value", "cantrip", "evoke"},
            "Stoneforge Mystic": {"creature", "etb_value", "tutor", "early_play"},
            "Seasoned Pyromancer": {"creature", "etb_value", "token_maker"},
            "Orcish Bowmasters": {"creature", "etb_value", "threat", "token_maker", "instant_speed"},
            "Primeval Titan": {"creature", "etb_value", "threat", "ramp"},
            "Atraxa, Grand Unifier": {"creature", "etb_value", "threat", "card_advantage"},
            "Griselbrand": {"creature", "threat", "card_advantage"},
            # Counterspells missing auto-detection
            "Flusterstorm": {"counterspell", "interaction", "instant_speed", "combo"},
            "Consign to Memory": {"counterspell", "interaction", "instant_speed"},
            # Stax pieces
            "Chalice of the Void": {"stax", "interaction"},
            # Board wipes
            "Supreme Verdict": {"board_wipe", "removal"},
            # Interactive permanents
            "Teferi, Time Raveler": {"etb_value", "interaction", "threat"},
            "Goblin Bombardment": {"removal", "combo", "threat"},
            "Blood Moon": {"stax", "interaction"},
            "Thraben Charm": {"removal", "graveyard_hate", "instant_speed"},
            "Celestial Purge": {"removal", "instant_speed"},
            # ETron
            "All Is Dust": {"board_wipe", "removal"},
            "Ratchet Bomb": {"removal", "interaction"},
            # Affinity sideboard
            "Dispatch": {"removal", "instant_speed"},
            "Hurkyl's Recall": {"removal", "instant_speed"},
            "Metallic Rebuke": {"counterspell", "interaction", "instant_speed"},
            "Relic of Progenitus": {"graveyard_hate", "cantrip"},
            "Torpor Orb": {"stax"},
            "Ethersworn Canonist": {"creature", "stax", "early_play"},
            "Haywire Mite": {"removal", "creature", "early_play"},
            "Thought Monitor": {"creature", "etb_value", "card_advantage"},
            # Domain Zoo
            "Leyline of the Guildpact": {"stax", "combo"},
            "Scion of Draco": {"creature", "etb_value", "evasion", "threat"},
            "Territorial Kavu": {"creature", "threat", "early_play"},
            "Doorkeeper Thrull": {"creature", "stax", "evasion", "instant_speed", "early_play"},
            # Amulet Titan
            "Scapeshift": {"combo", "ramp"},
            "Amulet of Vigor": {"combo", "ramp", "early_play"},
            "Spelunking": {"ramp", "cantrip", "combo"},
        }
        if name in TAG_OVERRIDES:
            template.tags.update(TAG_OVERRIDES[name])

        # ── Ability overrides for removal spells missing targeting ──
        ABILITY_OVERRIDES = {
            "Galvanic Discharge": [("Deal damage to creature", 1)],
            "Dismember": [("Destroy creature", 1)],
            "Solitude": [("Exile creature", 1)],
            "Fury": [("Deal damage to creature", 1)],
            "Thoughtseize": [("Discard from opponent", 0)],
            "Inquisition of Kozilek": [("Discard from opponent", 0)],
            "Dispatch": [("Exile creature", 1)],
            "Hurkyl's Recall": [("Bounce all artifacts", 0)],
        }
        if name in ABILITY_OVERRIDES and not template.abilities:
            for desc, targets in ABILITY_OVERRIDES[name]:
                template.abilities.append(Ability(
                    ability_type=AbilityType.CAST,
                    description=desc,
                    targets_required=targets,
                ))

        # ── Populate oracle-derived properties ──
        from .oracle_parser import (
            parse_ritual_mana, parse_cycling_cost, parse_cycling_variant,
            parse_energy_production, has_cascade, parse_x_cost,
            parse_domain_reduction, detect_power_scaling, parse_splice_cost,
        )
        oracle = template.oracle_text or ''
        template.ritual_mana = parse_ritual_mana(oracle)
        template.cycling_cost_data = parse_cycling_cost(oracle)
        template.cycling_variant_data = parse_cycling_variant(oracle)
        template.energy_production = parse_energy_production(oracle)
        template.is_cascade = has_cascade(oracle)
        template.x_cost_data = parse_x_cost(oracle, name, data.get("manaCost", ""))
        template.is_cost_reducer = 'cost_reducer' in template.tags
        template.domain_reduction = parse_domain_reduction(oracle) or 0
        template.splice_cost = parse_splice_cost(oracle)
        template.power_scales_with = detect_power_scaling(oracle)

        return template

    def _detect_conditional_mana(self, oracle_text: str, name: str) -> Optional[dict]:
        """Detect conditional mana production from oracle text.

        Parses patterns like:
          'If you control an Urza's Mine and an Urza's Power-Plant, add {C}{C}{C} instead.'
        Returns a dict with:
          - condition: str identifying the condition type (e.g., 'tron')
          - requires: set of card subtypes/names that must be present
          - bonus: int, extra mana produced beyond the base 1
        Returns None if no conditional mana detected.
        """
        if not oracle_text:
            return None
        text_lower = oracle_text.lower()

        # Detect Urza's Tron pattern: "if you control an urza's ... add {C}{C}... instead"
        if "if you control" in text_lower and "urza's" in text_lower:
            # Count the {C} symbols in the "instead" clause
            import re
            instead_match = re.search(r'add\s+((?:\{[CWUBRG]\})+)\s+instead', oracle_text, re.IGNORECASE)
            if instead_match:
                mana_str = instead_match.group(1)
                c_count = mana_str.count('{C}')
                if c_count > 1:
                    # Extract required Urza's land names from the condition
                    # Pattern: "If you control an Urza's X and an Urza's Y"
                    required = set()
                    urza_matches = re.findall(r"Urza's\s+[\w-]+", oracle_text)
                    for m in urza_matches:
                        required.add(m)
                    return {
                        "condition": "tron",
                        "requires": required,
                        "bonus": c_count - 1,  # -1 because base production is 1
                    }
        return None

    def _build_abilities(self, effects: List[OracleEffect], oracle_text: str,
                         name: str, data: dict) -> List[Ability]:
        """Build Ability objects from parsed effects."""
        abilities = []
        text_lower = (oracle_text or "").lower()

        for effect in effects:
            if effect.effect_type == "damage":
                ability = Ability(
                    ability_type=AbilityType.CAST if "Instant" in data.get("types", []) or "Sorcery" in data.get("types", []) else AbilityType.ETB,
                    description=f"Deal {effect.amount} damage to {effect.target_type}",
                    targets_required=1 if effect.target_type not in ("all_creatures", "each_opponent") else 0,
                )
                abilities.append(ability)

            elif effect.effect_type == "destroy":
                ability = Ability(
                    ability_type=AbilityType.CAST if "Instant" in data.get("types", []) or "Sorcery" in data.get("types", []) else AbilityType.ETB,
                    description=f"Destroy {effect.target_type}",
                    targets_required=1 if "all" not in effect.target_type else 0,
                )
                abilities.append(ability)

            elif effect.effect_type == "draw":
                ability = Ability(
                    ability_type=AbilityType.CAST,
                    description=f"Draw {effect.amount} card(s)",
                )
                abilities.append(ability)

            elif effect.effect_type == "counter":
                ability = Ability(
                    ability_type=AbilityType.CAST,
                    description=f"Counter target {effect.target_type}",
                    targets_required=1,
                )
                abilities.append(ability)

            elif effect.effect_type == "exile":
                ability = Ability(
                    ability_type=AbilityType.CAST if "Instant" in data.get("types", []) or "Sorcery" in data.get("types", []) else AbilityType.ETB,
                    description=f"Exile {effect.target_type}",
                    targets_required=1 if "all" not in effect.target_type else 0,
                )
                abilities.append(ability)

            elif effect.effect_type == "bounce":
                ability = Ability(
                    ability_type=AbilityType.CAST,
                    description=f"Return {effect.target_type} to hand",
                    targets_required=1,
                )
                abilities.append(ability)

            elif effect.effect_type == "gain_life":
                ability = Ability(
                    ability_type=AbilityType.CAST,
                    description=f"Gain {effect.amount} life",
                )
                abilities.append(ability)

            elif effect.effect_type == "search_library":
                ability = Ability(
                    ability_type=AbilityType.CAST if "Instant" in data.get("types", []) or "Sorcery" in data.get("types", []) else AbilityType.ETB,
                    description=f"Search library for {effect.target_type}",
                )
                abilities.append(ability)

        # ETB trigger detection
        if re.search(r"when\s+(?:this\s+creature|.*)\s+enters", text_lower):
            if not any(a.ability_type == AbilityType.ETB for a in abilities):
                abilities.append(Ability(
                    ability_type=AbilityType.ETB,
                    description="ETB trigger",
                ))

        # Attack trigger detection
        if re.search(r"whenever\s+(?:this\s+creature|.*)\s+attacks", text_lower):
            abilities.append(Ability(
                ability_type=AbilityType.ATTACK,
                description="Attack trigger",
            ))

        # Dies trigger detection
        if re.search(r"when\s+(?:this\s+creature|.*)\s+dies", text_lower):
            abilities.append(Ability(
                ability_type=AbilityType.DIES,
                description="Dies trigger",
            ))

        return abilities

    def get_card(self, name: str) -> Optional[CardTemplate]:
        """Get a card template by name."""
        return self.cards.get(name)

    def get_effects(self, name: str) -> List[OracleEffect]:
        """Get parsed effects for a card."""
        return self._effects_cache.get(name, [])

    def get_raw(self, name: str) -> Optional[dict]:
        """Get raw MTGJSON data for a card."""
        return self._raw_data.get(name)

    def search(self, **kwargs) -> List[CardTemplate]:
        """Search cards by various criteria."""
        results = []
        for card in self.cards.values():
            match = True
            if "card_type" in kwargs:
                if kwargs["card_type"] not in card.card_types:
                    match = False
            if "color" in kwargs:
                if kwargs["color"] not in card.color_identity:
                    match = False
            if "max_cmc" in kwargs:
                if card.cmc > kwargs["max_cmc"]:
                    match = False
            if "min_cmc" in kwargs:
                if card.cmc < kwargs["min_cmc"]:
                    match = False
            if "keyword" in kwargs:
                if kwargs["keyword"] not in card.keywords:
                    match = False
            if "tag" in kwargs:
                if kwargs["tag"] not in card.tags:
                    match = False
            if "name_contains" in kwargs:
                if kwargs["name_contains"].lower() not in card.name.lower():
                    match = False
            if match:
                results.append(card)
        return results

    def __len__(self):
        return len(self.cards)

    def __contains__(self, name: str):
        return name in self.cards
