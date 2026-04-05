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
    if 'add' not in oracle:
        return None

    # Only look at the first sentence containing "add"
    add_sentence = ''
    for sentence in oracle.split('.'):
        if 'add' in sentence:
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
    m = re.search(r'add\s+(\w+)\s+mana', oracle)
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


def parse_energy_production(oracle: str) -> int:
    """Count energy production from oracle text.

    Returns the number of {E} symbols in the first energy-producing clause.
    """
    oracle = oracle.lower()
    if '{e}' not in oracle and 'energy' not in oracle:
        return 0

    # Count {e} symbols in "get {e}{e}{e}" patterns
    m = re.search(r'(?:get|gets?)\s+((?:\{e\})+)', oracle)
    if m:
        return m.group(1).count('{e}')

    # "you get {e}" anywhere
    return oracle.count('{e}')


def has_cascade(oracle: str) -> bool:
    """Check if oracle text has cascade keyword."""
    return 'cascade' in oracle.lower()


def parse_x_cost(oracle: str, name: str) -> Optional[Dict]:
    """Parse X-cost spell properties from oracle text."""
    oracle = oracle.lower()
    if '{x}' not in oracle:
        return None

    # Detect XX costs (Chalice of the Void)
    multiplier = 2 if '{x}{x}' in oracle else 1

    return {
        'multiplier': multiplier,
        'min_x': 1 if multiplier == 2 else 0,
    }


def is_living_end_cascader(oracle: str, card_types: list) -> bool:
    """Check if this card cascades into Living End."""
    return has_cascade(oracle)


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
