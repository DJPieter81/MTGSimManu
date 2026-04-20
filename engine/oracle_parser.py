"""Oracle Text Parser — derive card properties from oracle text.

Extracts structured data from oracle text at card load time:
- Ritual mana production
- Cycling costs
- Energy production
- Cascade status
- X-cost spell properties
- Token definitions

This replaces the hardcoded data tables in game_state.py
(RITUAL_CARDS, CYCLING_COSTS, ENERGY_PRODUCERS, etc.)
"""
from __future__ import annotations
import re
from typing import Dict, List, Optional, Set, Tuple


def parse_ritual_mana(oracle: str) -> Optional[Tuple[str, int]]:
    """Parse mana production from oracle text.

    Returns (color, amount) or None if not a ritual.
    E.g., "Add {R}{R}{R}" → ("R", 3)
    """
    oracle = oracle.lower()
    # Use word boundaries to avoid matching "additional" (e.g. in "Kicker {W}
    # (You may pay an additional {W}..." on Orim's Chant, which was being
    # mis-parsed as a 2-W ritual and producing mana instead of silencing).
    if not re.search(r'\badd\b', oracle):
        return None

    # Only look at the first sentence containing a standalone "add"
    add_sentence = ''
    for sentence in oracle.split('.'):
        if re.search(r'\badd\b', sentence):
            add_sentence = sentence
            break
    if not add_sentence:
        return None

    # Count mana symbols in the add clause only
    for color in ['R', 'G', 'U', 'B', 'W', 'C']:
        pattern = '{' + color.lower() + '}'
        count = add_sentence.count(pattern)
        if count >= 2:
            return (color, count)

    # "Add two mana in any combination" (Manamorphose)
    m = re.search(r'\badd\s+(\w+)\s+mana', oracle)
    if m:
        word_to_num = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}
        amount = word_to_num.get(m.group(1), 0)
        if amount > 0:
            return ('any', amount)

    return None


def parse_cycling_cost(oracle: str) -> Optional[Dict]:
    """Parse cycling cost from oracle text.

    Returns {'mana': int, 'life': int, 'colors': set} or None.
    """
    oracle = oracle.lower()
    if 'cycling' not in oracle:
        return None

    # "Cycling {1}{U}" → mana=2, colors={'U'}
    m = re.search(r'cycling[—\s]+(?:pay\s+)?(.+?)(?:\s*\(|$)', oracle)
    if not m:
        return None

    cost_str = m.group(1).strip()
    mana = 0
    life = 0
    colors = set()

    # Count mana symbols
    for color in ['W', 'U', 'B', 'R', 'G']:
        pattern = '{' + color.lower() + '}'
        count = cost_str.count(pattern)
        mana += count
        if count > 0:
            colors.add(color)

    # Generic mana {1}, {2}, etc.
    for gm in re.findall(r'\{(\d+)\}', cost_str):
        mana += int(gm)

    # Life payment: "pay N life"
    lm = re.search(r'pay\s+(\d+)\s+life', cost_str)
    if lm:
        life = int(lm.group(1))
    elif '—pay' in oracle.replace(' ', ''):
        # "Cycling—Pay 2 life"
        lm2 = re.search(r'pay\s+(\d+)\s+life', oracle)
        if lm2:
            life = int(lm2.group(1))

    return {'mana': mana, 'life': life, 'colors': colors}


# Basic land subtypes that appear as typecycling prefixes
# (e.g. "Swampcycling" → search for a Swamp card).  Case-sensitive
# values match the subtype strings stored on CardTemplate.subtypes.
_BASIC_LAND_SUBTYPES = {
    'plains': 'Plains', 'island': 'Island', 'swamp': 'Swamp',
    'mountain': 'Mountain', 'forest': 'Forest',
    # Non-basic but valid land subtypes that appear as typecycling prefixes
    # in the wider multiverse (e.g. Desertcycling in Amonkhet).
    'desert': 'Desert',
}


def parse_cycling_variant(oracle: str) -> Optional[Dict]:
    """Classify a cycling variant and return the library-search predicate.

    Plain cycling (``Cycling {cost}``) and no-cycling cards return ``None``:
    the resolver should just draw a card.
    Landcycling / typecycling returns a predicate dict whose fields a
    library card must satisfy to be a legal tutor target::
        {
            'require_types':     set[str],  # lowercase CardType values
            'require_supertypes': set[str], # lowercase Supertype values
            'require_subtypes':   set[str], # case-sensitive subtype names
        }
    All three sets are ANDed; empty set = no constraint.  Examples::
        "Artifact landcycling"  → types={land, artifact}
        "Basic landcycling"     → types={land}, supertypes={basic}
        "Landcycling"           → types={land}
        "Swampcycling"          → types={land}, subtypes={Swamp}
        "Slivercycling"         → subtypes={Sliver}   (creature type)
    The parser deliberately ignores "When you cycle this card, you may
    search your library..." triggered abilities (e.g. Krosan Tusker):
    those resolve after a plain-cycling draw, not in place of it.
    """
    oracle_lower = oracle.lower()
    if 'cycling' not in oracle_lower:
        return None

    # Landcycling comes in three forms; check most specific first.
    if re.search(r'\bartifact\s+landcycling\b', oracle_lower):
        return {'require_types': {'land', 'artifact'},
                'require_supertypes': set(),
                'require_subtypes': set()}
    if re.search(r'\bbasic\s+landcycling\b', oracle_lower):
        return {'require_types': {'land'},
                'require_supertypes': {'basic'},
                'require_subtypes': set()}
    if re.search(r'\blandcycling\b', oracle_lower):
        return {'require_types': {'land'},
                'require_supertypes': set(),
                'require_subtypes': set()}

    # Typecycling: "<prefix>cycling {cost}" where <prefix> is not
    # "land"/"artifact"/"basic".  Bound to followed-by "{" so we do not
    # capture the word "cycling" itself or an in-prose triggered clause.
    m = re.search(r'\b(\w+)cycling\b\s*\{', oracle_lower)
    if not m:
        return None
    prefix = m.group(1)
    if prefix in _BASIC_LAND_SUBTYPES:
        return {'require_types': {'land'},
                'require_supertypes': set(),
                'require_subtypes': {_BASIC_LAND_SUBTYPES[prefix]}}
    # Creature-type cycling (Slivercycling, Wizardcycling, ...) —
    # derive the subtype from the in-reminder-text search phrase to
    # preserve canonical capitalisation.
    m2 = re.search(
        r'search your library for a[n]?\s+([a-z]+)\s+card',
        oracle_lower,
    )
    if m2:
        return {'require_types': set(),
                'require_supertypes': set(),
                'require_subtypes': {m2.group(1).capitalize()}}
    # Unrecognised typecycling variant — fall back to plain cycling so
    # the engine does not silently drop the draw.
    return None


def parse_energy_production(oracle: str) -> int:
    """Count energy production from oracle text.

    Returns the number of {E} symbols in the first energy-producing clause.
    Skips clauses gated by "Whenever ... enters/attacks/dies" — those are
    triggered abilities that fire in response to other events, not static
    ETB production. Guide of Souls was being credited 1 energy at its own
    ETB because its triggered "whenever another creature you control enters"
    clause matched the raw "get {e}" regex.
    """
    oracle = oracle.lower()
    if '{e}' not in oracle and 'energy' not in oracle:
        return 0

    # Look for "get {E}" clauses. Return the first one whose sentence is
    # NOT gated by a "whenever" trigger.
    for m in re.finditer(r'(?:get|gets?)\s+((?:\{e\})+)', oracle):
        # Find the boundary of this "sentence" — the last sentence-terminator
        # (period, newline) before the match, or start of string.
        sentence_start = max(
            oracle.rfind('.', 0, m.start()),
            oracle.rfind('\n', 0, m.start()),
            -1
        ) + 1
        clause = oracle[sentence_start:m.end()]
        if 'whenever' in clause or 'when ' in clause.lstrip()[:5]:
            continue  # triggered ability — not static ETB
        return m.group(1).count('{e}')

    return 0


def has_cascade(oracle: str) -> bool:
    """Check if oracle text has cascade keyword."""
    return 'cascade' in oracle.lower()


def parse_x_cost(oracle: str, name: str, mana_cost_str: str = "") -> Optional[Dict]:
    """Parse X-cost spell properties from oracle text and mana cost."""
    oracle_lower = oracle.lower()
    mana_lower = mana_cost_str.lower() if mana_cost_str else ""
    # Check both {X} in mana cost format and "X" in oracle text
    if ('{x}' not in oracle_lower and ' x ' not in oracle_lower
            and not oracle_lower.startswith('x ')
            and '{x}' not in mana_lower):
        return None

    # Detect XX costs from mana cost string (e.g. Chalice {X}{X})
    multiplier = 2 if '{x}{x}' in mana_lower else 1
    # Fallback: also check oracle text for {X}{X}
    if multiplier == 1 and '{x}{x}' in oracle_lower:
        multiplier = 2

    # Determine counter type from oracle text
    effect = ""
    if 'charge counter' in oracle_lower:
        effect = "charge_counters"
    elif '+1/+1 counter' in oracle_lower:
        effect = "plus1_counters"

    return {
        'multiplier': multiplier,
        'min_x': 1 if multiplier == 2 else 0,
        'effect': effect,
    }


def is_living_end_cascader(oracle: str, card_types: list) -> bool:
    """Check if this card cascades into Living End."""
    return has_cascade(oracle)


def parse_splice_cost(oracle: str) -> Optional[int]:
    """Parse splice onto Arcane cost from oracle text.

    "Splice onto Arcane {1}{R}" → 2 (estimated CMC)
    Returns total CMC or None if no splice.
    """
    m = re.search(r'splice onto arcane[—\s]*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    total = 0
    for s in symbols:
        if s.isdigit():
            total += int(s)
        else:
            total += 1  # colored mana = 1
    return total if total > 0 else None


def parse_cost_reduction(oracle: str) -> Optional[Dict]:
    """Parse cost reduction rules from oracle text.

    Returns {'target': str, 'amount': int, 'color': str|None} or None.
    """
    oracle = oracle.lower()
    if 'cost' not in oracle or 'less' not in oracle:
        return None

    amount = 1
    m = re.search(r'cost\s*\{(\d+)\}\s*less', oracle)
    if m:
        amount = int(m.group(1))

    target = 'all'
    if 'instant and sorcery' in oracle or 'instants and sorceries' in oracle:
        target = 'instant_sorcery'
    elif 'creature spell' in oracle:
        target = 'creature'
    elif 'noncreature' in oracle:
        target = 'noncreature'

    color = None
    for c_name, c_code in [('red','R'),('blue','U'),('black','B'),('white','W'),('green','G')]:
        if c_name in oracle:
            color = c_code
            break

    return {'target': target, 'amount': amount, 'color': color}


def parse_domain_reduction(oracle: str) -> Optional[int]:
    """Parse domain-based cost reduction.

    Returns reduction per basic land type, or None.
    """
    oracle = oracle.lower()
    if 'basic land type' not in oracle or 'less' not in oracle:
        return None
    m = re.search(r'costs?\s*\{(\d+)\}\s*less.*basic land type', oracle)
    return int(m.group(1)) if m else 1


def detect_power_scaling(oracle: str) -> str:
    """Detect dynamic P/T scaling from oracle text.

    Returns: "domain", "tarmogoyf", "delirium", "graveyard", or "".
    """
    oracle = oracle.lower()
    if 'basic land type' in oracle and ('power' in oracle or 'toughness' in oracle or 'equal' in oracle):
        return "domain"
    if 'card type' in oracle and ('power' in oracle or 'equal' in oracle) and 'graveyard' in oracle:
        return "tarmogoyf"
    if ('delirium' in oracle or 'four or more card types' in oracle) and 'graveyard' in oracle:
        return "delirium"
    if ('exile' in oracle and ('instant' in oracle or 'sorcery' in oracle)
            and ('graveyard' in oracle or 'from your graveyard' in oracle)):
        return "graveyard"
    return ""


def parse_planeswalker_abilities(oracle: str) -> Optional[Dict]:
    """Parse planeswalker loyalty abilities from oracle text.

    Returns dict with 'plus', 'minus', 'ult', 'starting_loyalty'.
    """
    oracle_lower = oracle.lower()
    if not any(f'[{sign}' in oracle_lower for sign in ['+', '−', '-', '0']):
        return None

    abilities = {}

    # Parse [+N]: effect
    plus_m = re.search(r'\[([+])(\d+)\]\s*:\s*(.+?)(?:\n|\[|$)', oracle, re.IGNORECASE)
    if plus_m:
        abilities['plus'] = (int(plus_m.group(2)), plus_m.group(3).strip()[:60])

    # Parse [−N]: effect or [-N]: effect
    minus_m = re.search(r'\[[−\-](\d+)\]\s*:\s*(.+?)(?:\n|\[|$)', oracle, re.IGNORECASE)
    if minus_m:
        cost = -int(minus_m.group(1))
        abilities['minus'] = (cost, minus_m.group(2).strip()[:60])

    # Parse ultimate (largest negative)
    ult_matches = re.findall(r'\[[−\-](\d+)\]\s*:\s*(.+?)(?:\n|\[|$)', oracle, re.IGNORECASE)
    if len(ult_matches) >= 2:
        # Ultimate is the one with highest cost
        ult = max(ult_matches, key=lambda m: int(m[0]))
        abilities['ult'] = (-int(ult[0]), ult[1].strip()[:60])

    if not abilities:
        return None

    return abilities


def has_delve(oracle: str) -> bool:
    """Check if card has delve keyword."""
    return 'delve' in oracle.lower()


def parse_dash_cost(oracle: str) -> Optional[int]:
    """Parse Dash cost from oracle text.

    "Dash {1}{R}" → 2 (estimated CMC)
    """
    m = re.search(r'dash\s*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if not m:
        return None
    symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    total = 0
    for s in symbols:
        if s.isdigit():
            total += int(s)
        else:
            total += 1  # colored mana = 1
    return total if total > 0 else None


def parse_extra_land_drops(oracle: str) -> int:
    """Parse extra land drops from oracle text.

    "You may play two additional lands" → 2
    "You may play an additional land" → 1
    """
    lower = oracle.lower()
    if 'additional land' not in lower and 'extra land' not in lower:
        return 0
    if 'two additional land' in lower:
        return 2
    if 'three additional land' in lower:
        return 3
    if 'additional land' in lower or 'extra land' in lower:
        return 1
    return 0


def parse_escape_cost(oracle: str) -> Optional[Dict]:
    """Parse Escape cost from oracle text.

    "Escape—{R}{R}{W}{W}, Exile five other cards" → {'cmc': 4, 'exile': 5}
    """
    m = re.search(r'escape[—\-]\s*((?:\{[^}]+\})+),?\s*exile\s+(\w+)\s+other\s+card',
                  oracle, re.IGNORECASE)
    if not m:
        return None
    cost_symbols = re.findall(r'\{([^}]+)\}', m.group(1))
    cmc = 0
    for s in cost_symbols:
        if s.isdigit():
            cmc += int(s)
        else:
            cmc += 1
    # Parse exile count
    word_to_num = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
                   'six': 6, 'seven': 7, 'eight': 8}
    exile_word = m.group(2).lower()
    exile_count = word_to_num.get(exile_word)
    if exile_count is None and exile_word.isdigit():
        exile_count = int(exile_word)
    return {'cmc': cmc, 'exile': exile_count or 5}


def parse_equip_cost(oracle: str) -> Optional[int]:
    """Parse Equip cost from oracle text.

    "Equip {2}" → 2
    """
    m = re.search(r'equip\s*\{(\d+)\}', oracle, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Equip with colored mana: "Equip {B}{B}"
    m = re.search(r'equip\s*((?:\{[^}]+\})+)', oracle, re.IGNORECASE)
    if m:
        symbols = re.findall(r'\{([^}]+)\}', m.group(1))
        return sum(int(s) if s.isdigit() else 1 for s in symbols)
    return None


def derive_tags_from_oracle(oracle: str, keywords: set, card_types: set,
                            subtypes: set, power: int = 0) -> set:
    """Derive semantic tags from oracle text and card properties.

    Returns a set of tags like 'threat', 'ramp', 'token_maker', 'etb_value', etc.
    """
    tags = set()
    lower = oracle.lower()

    # Threat detection: big creatures, evasion, or growing
    if power >= 4:
        tags.add("threat")
    if any(kw in str(keywords).lower() for kw in ('flying', 'trample')) and power >= 3:
        tags.add("threat")
    if '+1/+1 counter' in lower and ('enters' in lower or 'combat damage' in lower):
        tags.add("threat")

    # Ramp: puts lands onto battlefield or adds mana
    if ('land' in lower and 'onto the battlefield' in lower
            and ('search' in lower or 'put' in lower)):
        tags.add("ramp")
    if 'untap' in lower and 'enters tapped' in lower:
        tags.add("ramp")

    # Token maker
    if 'create' in lower and 'token' in lower:
        tags.add("token_maker")
    if 'amass' in lower:
        tags.add("token_maker")

    # ETB value: "when * enters" with a beneficial effect
    etb_triggers = ('when ' in lower and 'enters' in lower)
    if etb_triggers:
        has_value = any(kw in lower for kw in ('draw', 'damage', 'destroy', 'exile',
                                                 'search', 'create', 'return', 'gain'))
        if has_value:
            tags.add("etb_value")

    # Flash detection from oracle (backup if keyword not parsed)
    if 'flash' in lower.split('\n')[0] if lower else False:
        tags.add("instant_speed")

    # Evoke detection
    if 'evoke' in lower:
        tags.add("evoke")
    if re.search(r'evoke.*exile.*card.*from.*hand', lower):
        tags.add("evoke_pitch")

    # Card advantage
    if 'draw' in lower and ('cards' in lower or 'two' in lower or 'three' in lower):
        tags.add("card_advantage")

    # Equipment
    if 'equip' in lower:
        tags.add("equipment")

    return tags
