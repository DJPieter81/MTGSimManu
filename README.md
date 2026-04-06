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

| Tool | Command |
|------|---------|
| **List decks** | `python run_meta.py --list` |
| **Deck profile** | `python run_meta.py --deck storm` |
| **Head-to-head** | `python run_meta.py --matchup storm dimir -n 50` |
| **Field sweep** | `python run_meta.py --field storm -n 30` |
| **Meta matrix** | `python run_meta.py --matrix --decks 8 -n 30` |
| **Game log** | `python run_meta.py --verbose storm dimir -s 42000` |
| **AI reasoning** | `python run_meta.py --trace storm dimir -s 42000` |
| **HTML Bo3** | `python simulate_match.py "Ruby Storm" "Domain Zoo" --seed 55555` |
| **Import deck** | `python import_deck.py "Deck Name" decklist.txt` |
| **Save results** | `python run_meta.py --matrix -n 50 --save` |
| **Load results** | `python run_meta.py --results` |
| **Run tests** | `python -m pytest tests/ -q` |

Aliases: `storm`, `zoo`, `dimir`, `omnath`, `4c`, `5c`, `energy`, `boros`, `jeskai`, `blink`, `tron`, `amulet`, `goryos`, `prowess`, `affinity`, `cascade`

## Docs

- **[QUICKSTART.md](QUICKSTART.md)** — Full setup, all commands, Python API, seed conventions
- **[CLAUDE.md](CLAUDE.md)** — Architecture, design conventions, debugging (auto-loaded by Claude Code)

## Decks (13)

Boros Energy, Jeskai Blink, Ruby Storm, Affinity, Eldrazi Tron, Amulet Titan, Goryo's Vengeance, Domain Zoo, Living End, Izzet Prowess, Dimir Midrange, 4c Omnath, 4/5c Control
