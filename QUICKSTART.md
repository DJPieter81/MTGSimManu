# MTGSimManu — Quick Start

> **Planning reference:** Read `PROJECT_STATUS.md` before starting any development session.
> It contains architecture, all bugs with fix status, deck status, "never do / always do" rules.

## Setup

```bash
git clone https://github.com/DJPieter81/MTGSimManu.git
cd MTGSimManu
git checkout main && git pull
```

Reassemble the card database (required once per clone):
```bash
python3 -c "
import json, glob
merged = {}
for p in sorted(glob.glob('ModernAtomic_part*.json')):
    with open(p) as f: merged.update(json.load(f)['data'])
with open('ModernAtomic.json', 'w') as f:
    json.dump({'meta': {}, 'data': merged}, f)
print(f'Loaded {len(merged)} cards')
"
```

## Commands

```bash
# List all 16 decks
python3 run_meta.py --list

# Matchup win rate (N games)
python3 run_meta.py --matchup "Boros Energy" "Dimir Midrange" -n 50

# One deck vs entire field
python3 run_meta.py --field "Ruby Storm" -n 30

# Full metagame matrix (run twice for 100/pair)
python3 run_meta.py --matrix -n 50 --save

# Build dashboard after matrix run
python3 build_dashboard.py --merge

# Single game — action log
python3 run_meta.py --verbose "Boros Energy" "Dimir Midrange" -s 50000

# Single game — full AI reasoning (EV scores, choices)
python3 run_meta.py --trace "Boros Energy" "Dimir Midrange" -s 50000

# Bo3 match → HTML replay
python3 run_meta.py --bo3 "Ruby Storm" "Dimir Midrange" -s 55555 > replays/log.txt
python3 build_replay.py replays/log.txt replay.html 55555

# Audit a deck (card-level stats across the field)
python3 run_meta.py --audit "Boros Energy" -n 60

# Save / load matrix results
python3 run_meta.py --matrix -n 50 --save
python3 run_meta.py --results
```

## Importing a New Deck

```bash
python3 import_deck.py "Deck Name" decklist.txt
```

Then:
1. Paste the printed entry into `decks/modern_meta.py` (mainboard + sideboard + METAGAME_SHARES)
2. Add a gameplan JSON to `decks/gameplans/<slug>.json`
3. Smoke-test: `python3 run_meta.py --matchup "New Deck" boros -n 10`

## Deck Aliases

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
| `wst` | Azorius Control (WST) |

## Key Files

| File | Purpose |
|------|---------|
| `PROJECT_STATUS.md` | **Read first** — planning reference, bug tracker, deck status |
| `CLAUDE.md` | Claude Code instructions — workflow, skills, API patterns |
| `run_meta.py` | All simulation commands (CLI + Python API) |
| `build_dashboard.py` | Rebuild HTML matrix dashboard (`--merge` flag) |
| `build_replay.py` | Convert Bo3 log → interactive HTML replay |
| `import_deck.py` | Import new decks from pasted decklists |
| `decks/modern_meta.py` | All 15 decklists + meta shares |
| `decks/gameplans/*.json` | Per-deck strategy (goals, mulligan keys, card roles) |
| `ai/ev_player.py` | AI decision engine (EV scoring, attack, cycle) |
| `ai/ev_evaluator.py` | EVSnapshot, board projection, opponent response |
| `engine/card_effects.py` | Card-specific ETB/resolve handlers (oracle-driven) |
| `engine/game_state.py` | Core rules engine (stack, combat, mana, triggers) |
| `engine/oracle_resolver.py` | Generic oracle-text-driven ETB/cast/attack effects |

## Reproducible Seeds

| Use | seed_start | step |
|-----|-----------|------|
| Matchups | 50000 | 500 |
| Matrix | 40000 | 500 |
| Single game | 50000 | — |
