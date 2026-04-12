# MTGSimManu — MTG Modern Format Game Simulator

Magic: The Gathering Modern-format game simulator with EV-based AI. Simulates full Bo3 matches between 15 competitive Modern decks with mulligans, spell casting, combat, counterspells, evoke, storm chains, reanimation, cascade, and blink.

**Grade: C** · 149 tests passing · 15 decks · 21,795 cards · 0 external deps

## Quick Start

```bash
git clone https://github.com/DJPieter81/MTGSimManu.git
cd MTGSimManu
# Reassemble card database (one-time):
python3 -c "
import json, glob; merged = {}
for p in sorted(glob.glob('ModernAtomic_part*.json')):
    with open(p) as f: merged.update(json.load(f)['data'])
with open('ModernAtomic.json', 'w') as f: json.dump({'meta': {}, 'data': merged}, f)
print(f'Loaded {len(merged)} cards')
"
```

## Usage

| Tool | Command |
|------|---------|
| **Head-to-head** | `python3 run_meta.py --matchup "Boros Energy" dimir -n 50` |
| **Field sweep** | `python3 run_meta.py --field storm -n 30` |
| **Meta matrix** | `python3 run_meta.py --matrix -n 50 --save` |
| **Build dashboard** | `python3 build_dashboard.py --merge` |
| **Game log** | `python3 run_meta.py --verbose boros dimir -s 50000` |
| **AI reasoning** | `python3 run_meta.py --trace boros dimir -s 50000` |
| **Bo3 replay** | `python3 run_meta.py --bo3 storm dimir -s 55555 > replays/log.txt && python3 build_replay.py replays/log.txt out.html 55555` |
| **Import deck** | `python3 import_deck.py "Deck Name" decklist.txt` |
| **Run tests** | `python3 -m pytest tests/ -q` |

Aliases: `storm`, `zoo`, `dimir`, `omnath`, `4c`, `energy`, `boros`, `jeskai`, `blink`, `tron`, `amulet`, `goryos`, `prowess`, `affinity`, `cascade`, `wst`

## Docs

- **[PROJECT_STATUS.md](PROJECT_STATUS.md)** — Planning reference: architecture, all bugs with fix status, deck status table, "never do / always do" rules. **Read before each session.**
- **[QUICKSTART.md](QUICKSTART.md)** — Full setup, all commands, Python API, seed conventions
- **[CLAUDE.md](CLAUDE.md)** — Claude Code instructions, workflow, skills (auto-loaded by Claude Code)

## Decks (15)

Boros Energy, Jeskai Blink, Ruby Storm, Affinity, Eldrazi Tron, Amulet Titan, Goryo's Vengeance, Domain Zoo, Living End, Izzet Prowess, Dimir Midrange, 4c Omnath, 4/5c Control, Azorius Control (WST)
