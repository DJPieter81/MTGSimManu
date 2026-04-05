"""Import a deck from a pasted decklist.

Usage:
    # From CLI — paste a decklist file:
    python import_deck.py "Deck Name" decklist.txt

    # From CLI — paste directly:
    python import_deck.py "Deck Name" <<'EOF'
    4 Ragavan, Nimble Pilferer
    4 Lightning Bolt
    ...
    SB: 2 Wear // Tear
    EOF

    # From Python:
    from import_deck import import_deck
    import_deck("My Deck", '''
        4 Ragavan, Nimble Pilferer
        4 Lightning Bolt
        SB: 2 Wear // Tear
    ''')

Accepts mtgtop8, MTGO, Moxfield, or plain "4 Card Name" formats.
Auto-detects archetype from card tags and generates a starter gameplan.
"""
import json
import os
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

# ─── Parse any decklist format ─────────────────────────────────

def parse_decklist(text: str) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Parse a raw decklist string into mainboard + sideboard dicts.

    Handles:
    - "4 Card Name" (plain)
    - "4x Card Name" (x notation)
    - "4 [SET] Card Name" (mtgtop8 with set codes)
    - "SB: 2 Card Name" or "Sideboard" header
    - Blank lines and comments (// lines)
    """
    mainboard = {}
    sideboard = {}
    in_sideboard = False

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        # Skip comments and deck metadata
        if line.startswith('//') or line.startswith('#'):
            # Check for deck name in comment
            if 'NAME' in line or 'CREATOR' in line or 'FORMAT' in line:
                continue
            continue

        # Detect sideboard section
        if line.lower() in ('sideboard', 'sideboard:', 'sb:', 'side:'):
            in_sideboard = True
            continue
        if line.lower().startswith('sb:') or line.lower().startswith('sideboard:'):
            line = re.sub(r'^(?:sb|sideboard)\s*:\s*', '', line, flags=re.IGNORECASE)
            in_sideboard = True

        # Parse "N Card Name" or "Nx Card Name" or "N [SET] Card Name"
        m = re.match(r'^\s*(\d+)\s*x?\s*(?:\[[^\]]*\]\s*)?(.+)$', line)
        if not m:
            continue

        count = int(m.group(1))
        name = m.group(2).strip()

        # Normalize "Wear / Tear" → "Wear // Tear"
        name = re.sub(r'\s*/\s*', ' // ', name)
        # Remove trailing set info like "(MH3)" or "[MH3]"
        name = re.sub(r'\s*[\(\[][A-Z0-9]+[\)\]]\s*$', '', name)

        if in_sideboard:
            sideboard[name] = sideboard.get(name, 0) + count
        else:
            mainboard[name] = mainboard.get(name, 0) + count

    return mainboard, sideboard


# ─── Auto-detect archetype from card composition ──────────────

def detect_archetype(mainboard: Dict[str, int], db=None) -> str:
    """Guess the deck archetype from card tags."""
    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase()

    tags_count = {}
    creature_count = 0
    total_cmc = 0
    card_count = 0

    for name, count in mainboard.items():
        t = db.get_card(name)
        if not t:
            continue
        card_count += count
        total_cmc += (t.cmc or 0) * count
        if t.is_creature:
            creature_count += count
        for tag in getattr(t, 'tags', set()):
            tags_count[tag] = tags_count.get(tag, 0) + count

    avg_cmc = total_cmc / max(card_count, 1)
    ritual_count = tags_count.get('ritual', 0)
    reanimate_count = tags_count.get('reanimate', 0)
    combo_count = tags_count.get('combo', 0)
    removal_count = tags_count.get('removal', 0)
    counterspell_count = tags_count.get('counterspell', 0)
    cantrip_count = tags_count.get('cantrip', 0)

    # Storm: lots of rituals + cantrips
    if ritual_count >= 8 and cantrip_count >= 8:
        return 'combo'  # will be overridden to 'storm' if needed

    # Reanimator: reanimate spells + high CMC creatures
    if reanimate_count >= 3:
        return 'combo'

    # Combo: combo-tagged cards
    if combo_count >= 6:
        return 'combo'

    # Control: lots of removal + counterspells, few creatures
    if counterspell_count >= 4 and removal_count >= 4 and creature_count < 15:
        return 'control'

    # Ramp: high avg CMC, few cheap creatures
    if avg_cmc > 3.5:
        return 'ramp'

    # Aggro: many cheap creatures, low avg CMC
    if creature_count >= 20 and avg_cmc < 2.5:
        return 'aggro'

    # Tempo: mix of creatures + counters
    if counterspell_count >= 2 and creature_count >= 10:
        return 'tempo'

    # Default: midrange
    return 'midrange'


# ─── Auto-generate gameplan from card analysis ────────────────

def generate_gameplan(deck_name: str, mainboard: Dict[str, int],
                      archetype: str, db=None) -> dict:
    """Generate a starter gameplan JSON from the decklist."""
    if db is None:
        from engine.card_database import CardDatabase
        db = CardDatabase()

    # Categorize cards
    creatures = []
    removal = []
    counterspells = []
    cantrips = []
    rituals = []
    engines = []
    lands = []
    other = []
    high_cmc = []  # CMC 4+
    low_cmc = []   # CMC 1-2

    for name, count in mainboard.items():
        t = db.get_card(name)
        if not t:
            other.append(name)
            continue

        tags = getattr(t, 'tags', set())
        cmc = t.cmc or 0

        if t.is_land:
            lands.append(name)
            continue

        if 'ritual' in tags:
            rituals.append(name)
        if 'removal' in tags and not t.is_creature:
            removal.append(name)
        if 'counterspell' in tags:
            counterspells.append(name)
        if 'cantrip' in tags and not t.is_creature:
            cantrips.append(name)
        if 'cost_reducer' in tags or 'ramp' in tags:
            engines.append(name)
        if t.is_creature:
            creatures.append(name)
            if cmc >= 4:
                high_cmc.append(name)
            elif cmc <= 2:
                low_cmc.append(name)
        elif name not in removal and name not in counterspells and name not in cantrips and name not in rituals and name not in engines:
            other.append(name)

    # Build goals based on archetype
    goals = []

    if archetype in ('aggro', 'tempo'):
        goals = [
            {
                "goal_type": "CURVE_OUT",
                "description": f"Deploy creatures on curve",
                "card_roles": {
                    "enablers": low_cmc[:5],
                    "payoffs": high_cmc[:3] or creatures[-3:],
                    "interaction": removal[:3],
                },
            },
            {
                "goal_type": "PUSH_DAMAGE",
                "description": "Attack aggressively, remove blockers",
                "card_roles": {
                    "interaction": removal + counterspells[:2],
                },
            },
            {
                "goal_type": "CLOSE_GAME",
                "description": "Close the game with damage",
            },
        ]
    elif archetype in ('control',):
        goals = [
            {
                "goal_type": "INTERACT",
                "description": "Interact early with removal and counters",
                "card_roles": {
                    "interaction": removal[:5] + counterspells[:3],
                    "engines": engines[:3],
                    "payoffs": high_cmc[:3],
                },
                "hold_mana": True,
            },
            {
                "goal_type": "GRIND_VALUE",
                "description": "Generate card advantage and stabilize",
                "card_roles": {
                    "payoffs": high_cmc[:3] or creatures[-3:],
                    "enablers": cantrips[:3],
                    "interaction": removal[:3],
                },
                "hold_mana": True,
            },
            {
                "goal_type": "CLOSE_GAME",
                "description": "Close with threats",
            },
        ]
    elif archetype in ('combo', 'storm'):
        goals = [
            {
                "goal_type": "DEPLOY_ENGINE",
                "description": "Deploy enablers and engines",
                "card_roles": {
                    "engines": engines[:3],
                    "enablers": cantrips[:5],
                },
            },
            {
                "goal_type": "EXECUTE_PAYOFF",
                "description": "Execute the combo",
                "card_roles": {
                    "payoffs": high_cmc[:3] or other[:3],
                    "enablers": rituals[:3] + cantrips[:3],
                },
            },
            {
                "goal_type": "PUSH_DAMAGE",
                "description": "Close the game",
            },
        ]
    else:  # midrange, ramp
        goals = [
            {
                "goal_type": "INTERACT" if removal else "CURVE_OUT",
                "description": "Deploy threats while interacting",
                "card_roles": {
                    "enablers": low_cmc[:5],
                    "payoffs": high_cmc[:3],
                    "interaction": removal[:3],
                },
            },
            {
                "goal_type": "GRIND_VALUE",
                "description": "Grind value with threats and card advantage",
                "card_roles": {
                    "payoffs": high_cmc[:3] or creatures[-3:],
                    "enablers": cantrips[:3] + engines[:2],
                },
            },
            {
                "goal_type": "CLOSE_GAME",
                "description": "Close with threats",
            },
        ]

    # Mulligan keys: best early plays
    mulligan_keys = sorted(set(
        low_cmc[:4] + engines[:2] + removal[:2]
    ))[:6]

    # Reactive only: pure counterspells
    reactive = sorted(set(counterspells))

    # Always early: 1-drops and key engines
    always_early = sorted(set(
        [n for n in low_cmc if (db.get_card(n) and (db.get_card(n).cmc or 0) <= 1)][:3]
        + engines[:2]
    ))

    # Mulligan land range
    min_lands = 1 if archetype in ('aggro', 'combo', 'storm') else 2
    max_lands = 3 if archetype in ('aggro', 'combo', 'storm') else 4

    gameplan = {
        "deck_name": deck_name,
        "archetype": archetype,
        "goals": goals,
        "mulligan_keys": mulligan_keys,
        "mulligan_min_lands": min_lands,
        "mulligan_max_lands": max_lands,
        "reactive_only": reactive,
        "always_early": always_early,
    }

    return gameplan


# ─── Import: write files ──────────────────────────────────────

def import_deck(deck_name: str, decklist_text: str,
                archetype: str = None, write_files: bool = True) -> dict:
    """Import a deck from pasted text. Returns the generated gameplan.

    If write_files=True, writes:
    - decks/gameplans/<slug>.json
    - Prints the modern_meta.py entry to add manually
    """
    from engine.card_database import CardDatabase
    db = CardDatabase()

    mainboard, sideboard = parse_decklist(decklist_text)

    if not mainboard:
        print("ERROR: No cards parsed from decklist")
        return {}

    total_main = sum(mainboard.values())
    total_side = sum(sideboard.values())
    print(f"Parsed: {total_main} mainboard, {total_side} sideboard")

    if total_main != 60:
        print(f"WARNING: Mainboard has {total_main} cards (expected 60)")
    if total_side > 0 and total_side != 15:
        print(f"WARNING: Sideboard has {total_side} cards (expected 15)")

    # Check for missing cards
    missing = []
    for name in list(mainboard) + list(sideboard):
        t = db.get_card(name)
        if not t or 'placeholder' in getattr(t, 'tags', set()):
            missing.append(name)
    if missing:
        print(f"WARNING: {len(missing)} cards not in database: {missing[:5]}...")

    # Detect archetype
    if archetype is None:
        archetype = detect_archetype(mainboard, db)
    print(f"Archetype: {archetype}")

    # Generate gameplan
    gameplan = generate_gameplan(deck_name, mainboard, archetype, db)

    if write_files:
        # Write gameplan JSON
        slug = deck_name.lower().replace(' ', '_').replace("'", '').replace('/', '_')
        gameplan_path = os.path.join('decks', 'gameplans', f'{slug}.json')
        with open(gameplan_path, 'w') as f:
            json.dump(gameplan, f, indent=2)
        print(f"Wrote gameplan: {gameplan_path}")

        # Print modern_meta.py entry
        print(f"\n--- Add to decks/modern_meta.py MODERN_DECKS dict ---\n")
        print(f'    "{deck_name}": {{')
        print(f'        "mainboard": {{')
        for name, count in sorted(mainboard.items()):
            print(f'            "{name}": {count},')
        print(f'        }},')
        if sideboard:
            print(f'        "sideboard": {{')
            for name, count in sorted(sideboard.items()):
                print(f'            "{name}": {count},')
            print(f'        }},')
        print(f'    }},')

        print(f"\n--- Add to METAGAME_SHARES ---\n")
        print(f'    "{deck_name}": 3.0,')

        print(f"\n--- Add to DECK_ARCHETYPES in ai/strategy_profile.py ---\n")
        arch_enum = archetype.upper()
        print(f'    "{deck_name}": ArchetypeStrategy.{arch_enum},')

        print(f"\n--- Update test counts (13 → 14) in tests/ ---")

    return gameplan


# ─── CLI ──────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Import a deck from pasted decklist')
    parser.add_argument('name', help='Deck name (e.g., "Mardu Midrange")')
    parser.add_argument('file', nargs='?', default='-',
                        help='Decklist file (default: stdin)')
    parser.add_argument('--archetype', '-a', default=None,
                        help='Override archetype (aggro/midrange/control/combo/ramp/tempo)')
    args = parser.parse_args()

    if args.file == '-':
        text = sys.stdin.read()
    else:
        with open(args.file) as f:
            text = f.read()

    import_deck(args.name, text, archetype=args.archetype)
