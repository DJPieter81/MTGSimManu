# MTG Simulator — Quick Start

## Setup

```bash
git clone https://github.com/DJPieter81/MTGSimManu.git
cd MTGSimManu
git checkout main && git pull
```

Reassemble the card database (required once):
```bash
python -c "
import json
merged = {}
for i in range(1, 9):
    with open(f'ModernAtomic_part{i}.json') as f:
        merged.update(json.load(f)['data'])
with open('ModernAtomic.json', 'w') as f:
    json.dump({'meta': {}, 'data': merged}, f)
print(f'Loaded {len(merged)} cards')
"
```

## Commands

```bash
# List all 13 decks
python run_meta.py --list

# Deck profile (decklist, gameplan, strategy, card tags)
python run_meta.py --deck storm

# Matchup win rate (N games)
python run_meta.py --matchup storm dimir -n 50

# One deck vs entire field
python run_meta.py --field storm -n 30

# Full metagame matrix
python run_meta.py --matrix -n 20            # all 13 decks
python run_meta.py --matrix --decks 8 -n 50  # top 8 by meta share

# Single game — actions log
python run_meta.py --verbose zoo omnath -s 42000

# Single game — full AI reasoning (hand, EV scores, choices)
python run_meta.py --trace zoo omnath -s 42000

# BO3 match → HTML play-by-play
python simulate_match.py "Ruby Storm" "Domain Zoo" --seed 55555

# Save results for future sessions
python run_meta.py --matrix -n 50 --save

# Load last saved results (instant, no sim)
python run_meta.py --results

# Import a new deck from pasted decklist
python import_deck.py "Deck Name" decklist.txt
python import_deck.py "Deck Name" --archetype control < decklist.txt
```

## Importing a New Deck

Paste any decklist format (mtgtop8, MTGO, Moxfield, plain `4 Card Name`):

```bash
python import_deck.py "Mardu Midrange" decklist.txt
```

This auto-generates:
- `decks/gameplans/<slug>.json` — starter gameplan with goals, mulligan keys, card roles
- Prints the `modern_meta.py` entry to paste in
- Prints `DECK_ARCHETYPES` and `METAGAME_SHARES` entries to add
- Auto-detects archetype from card composition

After importing, manually:
1. Paste the printed entries into `decks/modern_meta.py` and `ai/strategy_profile.py`
2. Update test counts in `tests/` (13 → 14)
3. Refine the generated gameplan if needed

## Deck Aliases

Short names work everywhere:

| Alias | Deck |
|-------|------|
| `zoo` | Domain Zoo |
| `storm` | Ruby Storm |
| `dimir` | Dimir Midrange |
| `omnath`, `4c` | 4c Omnath |
| `5c` | 4/5c Control |
| `energy`, `boros` | Boros Energy |
| `jeskai`, `blink` | Jeskai Blink |
| `tron`, `eldrazi` | Eldrazi Tron |
| `amulet`, `titan` | Amulet Titan |
| `goryos`, `reanimator` | Goryo's Vengeance |
| `prowess`, `izzet` | Izzet Prowess |
| `affinity`, `robots` | Affinity |
| `cascade` | Living End |

## Python API

```python
from run_meta import (
    run_meta_matrix,    # full NxN → {matrix, rankings, names}
    run_matchup,        # 2 decks → {wins, pct1, pct2, avg_turn, turn_dist}
    run_field,          # 1 vs all → {deck, matchups: {opp: pct}, average}
    run_verbose_game,   # game log → str
    run_trace_game,     # game + AI reasoning → str
    inspect_deck,       # decklist + gameplan → str
    print_matrix,       # pretty-print matrix
    print_matchup,      # pretty-print matchup
    print_field,        # pretty-print field
)

from run_meta import save_results, load_results, print_saved_results
from import_deck import import_deck  # paste decklist → gameplan + meta entry

matrix = run_meta_matrix(top_tier=8, n_games=100)
print_matrix(matrix)

result = run_matchup("Ruby Storm", "Dimir Midrange", n_games=50)
print_matchup(result)

print(inspect_deck("Ruby Storm"))
print(run_trace_game("zoo", "omnath", seed=42000))

import_deck("New Deck", open("decklist.txt").read())
```

## Reproducible Seeds

Standard seed ranges for consistent results across sessions:

| Use | seed_start | step | Example |
|-----|-----------|------|---------|
| Matchups | 50000 | 500 | `run_matchup("storm", "dimir", n_games=50, seed_start=50000)` |
| Matrix | 40000 | 500 | `run_meta_matrix(n_games=20, seed_start=40000)` |
| Single game | 42000 | — | `--seed 42000` or `-s 42000` |

## Key Files

| File | Purpose |
|------|---------|
| `run_meta.py` | All analysis commands (CLI + API) |
| `import_deck.py` | Import new decks from pasted decklists |
| `simulate_match.py` | BO3 match → HTML viewer |
| `decks/modern_meta.py` | All 13 decklists + meta shares |
| `decks/gameplans/*.json` | Per-deck strategy (goals, mulligan, roles) |
| `ai/strategy_profile.py` | Per-archetype AI weights + deck overrides |
| `ai/ev_player.py` | AI decision engine (EV scoring) |
| `engine/card_effects.py` | 80+ card-specific effect handlers |
| `engine/card_database.py` | Card template loading from MTGJSON |
| `engine/game_state.py` | Core rules engine (stack, combat, mana) |
| `engine/game_runner.py` | Turn loop, phase transitions, game runner |
