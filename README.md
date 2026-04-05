# MTGSimManu — MTG Modern Format Game Simulator

Magic: The Gathering Modern-format game simulator with EV-based AI. Simulates full games between 13 competitive decks with mulligans, spell casting, combat, counterspells, evoke, storm chains, reanimation, cascade, and blink.

## Quick Start

```bash
git clone https://github.com/DJPieter81/MTGSimManu.git
cd MTGSimManu
# Reassemble card database (one-time):
python -c "
import json; merged = {}
for i in range(1, 9):
    with open(f'ModernAtomic_part{i}.json') as f: merged.update(json.load(f)['data'])
with open('ModernAtomic.json', 'w') as f: json.dump({'meta': {}, 'data': merged}, f)
"
```

## Usage

```bash
python run_meta.py --list                              # all 13 decks
python run_meta.py --deck storm                        # deck profile + gameplan
python run_meta.py --matchup storm dimir -n 50         # win rate (N games)
python run_meta.py --field storm -n 30                 # one deck vs all
python run_meta.py --matrix -n 20                      # full 13x13 matrix
python run_meta.py --verbose zoo omnath -s 42000        # game log
python run_meta.py --trace zoo omnath -s 42000          # full AI reasoning
python import_deck.py "Deck Name" decklist.txt         # import new deck
```

Aliases: `storm`, `zoo`, `dimir`, `omnath`, `4c`, `5c`, `energy`, `boros`, `jeskai`, `blink`, `tron`, `amulet`, `goryos`, `prowess`, `affinity`, `cascade`

## Docs

- **[QUICKSTART.md](QUICKSTART.md)** — Full setup, all commands, Python API, seed conventions
- **[CLAUDE.md](CLAUDE.md)** — Architecture, design conventions, debugging (auto-loaded by Claude Code)

## Decks (13)

Boros Energy, Jeskai Blink, Ruby Storm, Affinity, Eldrazi Tron, Amulet Titan, Goryo's Vengeance, Domain Zoo, Living End, Izzet Prowess, Dimir Midrange, 4c Omnath, 4/5c Control
