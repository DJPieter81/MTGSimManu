# CLAUDE.md — MTG Game Simulator

## Project Overview

Magic: The Gathering Modern-format game simulator with EV-based AI decision-making. Simulates full games between 14 competitive decks with strategic AI (mulligans, spell casting, combat, targeting, counterspells, evoke, storm chains, reanimation, cascade, blink).

**Python 3.11** — no external dependencies beyond the standard library.

## Required Data File

**`ModernAtomic.json`** must be in the project root. If missing, reassemble from parts:

```bash
python3 -c "
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

## Quick Reference — run_meta.py

```bash
python run_meta.py --list                              # all 13 decks
python run_meta.py --deck storm                        # deck profile + gameplan
python run_meta.py --matchup storm dimir -n 50         # win rate (N games)
python run_meta.py --field storm -n 30                 # one deck vs all
python run_meta.py --matrix -n 20                      # full 13x13 matrix
python run_meta.py --matrix --decks 8 -n 50            # top 8 only
python run_meta.py --verbose zoo omnath -s 42000        # game log
python run_meta.py --trace zoo omnath -s 42000          # full AI reasoning
```

| Tool | Command |
|------|---------|
| **List decks** | `python run_meta.py --list` |
| **Deck profile** | `python run_meta.py --deck storm` |
| **Head-to-head** | `python run_meta.py --matchup storm dimir -n 50` |
| **Field sweep** | `python run_meta.py --field storm -n 30` |
| **Meta matrix** | `python run_meta.py --matrix --decks 8 -n 30` |
| **Detailed Bo3** | `python run_meta.py --bo3 storm dimir -s 55555` |
| **Deck audit** | `python run_meta.py --audit affinity -n 60` |
| **Game log** | `python run_meta.py --verbose storm dimir -s 42000` |
| **AI reasoning** | `python run_meta.py --trace storm dimir -s 42000` |
| **HTML Bo3** | `python simulate_match.py "Ruby Storm" "Domain Zoo" --seed 55555` |
| **Import deck** | `python import_deck.py "Deck Name" decklist.txt` |
| **Save results** | `python run_meta.py --matrix -n 50 --save` |
| **Load results** | `python run_meta.py --results` |
| **Run tests** | `python -m pytest tests/ -q` |

**Detailed game analysis:** When asked for a detailed game log, play-by-play, Bo3 match log, game simulation, or match audit, use:
```bash
python run_meta.py --bo3 storm affinity -s 55555
```
Synonyms: `--bo3`, `--match`, `--play-by-play`, `--pbp`, `--detailed`, `--game-log`, `--simulate`

This produces a comprehensive log with: die roll, mulligan decisions, opening hands, turn-by-turn board states (creatures, permanents, life totals, hand sizes, lands), all spells cast, stack resolution, combat, and game result. Best-of-3 format with proper alternation.

**Aliases work:** storm, zoo, dimir, omnath, 4c, 5c, energy, boros, jeskai, blink, tron, amulet, goryos, prowess, affinity, cascade

**Python API:**
```python
from run_meta import run_meta_matrix, run_matchup, run_field, inspect_deck
from run_meta import run_verbose_game, run_trace_game, run_bo3, audit_deck
from run_meta import print_matrix, print_matchup, print_field
```

**Standard seeds:** matchups start at 50000 (step 500), matrix at 40000 (step 500).

**Import a new deck:**
```bash
python import_deck.py "Deck Name" decklist.txt
python import_deck.py "Deck Name" --archetype control < decklist.txt
```
Auto-detects archetype, generates gameplan, prints code to paste into modern_meta.py.

## Available Decks (14)

Boros Energy, Jeskai Blink, Ruby Storm, Affinity, Eldrazi Tron, Amulet Titan, Goryo's Vengeance, Domain Zoo, Living End, Izzet Prowess, Dimir Midrange, 4c Omnath, 4/5c Control, Azorius Control

**Known DB gaps:** `The Legend of Roku` (Azorius Control) and `Sink into Stupor` missing from ModernAtomic.json — deflates WR for those decks.

## Architecture

### Layer 1: Engine (rules enforcement)

The engine enforces Magic rules. It does NOT make decisions.

**GameState** (`engine/game_state.py`) — central mutable game object:
- `play_land(player_idx, card)` — land onto battlefield
- `cast_spell(player_idx, card, targets)` — resolve spell via EFFECT_REGISTRY
- `can_cast(player_idx, card)` — mana + color check (backtracking color solver for 4+ colors)
- `check_state_based_actions()` — lethal damage, legend rule
- `resolve_stack()` — resolve top of stack (handles storm, cascade, flashback, rebound)
- `combat_damage(attackers, blockers)` — first strike, trample, lifelink, deathtouch
- `_trigger_landfall(player_idx)` — multi-trigger landfall (Omnath pattern)
- `reanimate(controller, card)` — put creature from GY to battlefield

**GameRunner** (`engine/game_runner.py`) — turn loop:
- Untap → Upkeep (rebound) → Draw → Main1 → Combat → Main2 → End Step → Cleanup
- Mana pools empty between phases (CR 500.4)
- Main phase loops `EVPlayer.decide_main_phase()` until AI passes
- Response windows after each spell for counterspells

**EFFECT_REGISTRY** (`engine/card_effects.py`) — 80+ card-specific handlers:
```python
@EFFECT_REGISTRY.register("Orcish Bowmasters", EffectTiming.ETB,
                           description="Deal 1 damage, create Orc Army token")
def bowmasters_etb(game, card, controller, targets=None, item=None):
    ...
```

### Layer 2: AI (EV-based decisions)

**EVPlayer** (`ai/ev_player.py`) — the AI decision engine:
- `decide_main_phase(game)` → `("cast_spell", card, targets)` or `None`
- Scores every legal play via `_score_spell()` using `StrategyProfile` weights
- Picks the highest-EV play above `pass_threshold`
- Archetype-specific modifiers: aggro curves out, combo holds fuel, control holds up mana

**StrategyProfile** (`ai/strategy_profile.py`) — per-archetype numerical weights:
- Profiles: AGGRO, MIDRANGE, CONTROL, COMBO, STORM, RAMP, TEMPO
- Per-deck overrides: `DECK_ARCHETYPE_OVERRIDES` (Ruby Storm → "storm")
- Key parameters: `pass_threshold`, `burn_face_mult`, `storm_patience`, `holdback_penalty`

**GoalEngine** (`ai/gameplan.py`) — strategic planning:
- Each deck has ordered Goals loaded from `decks/gameplans/*.json`
- Goals define card_roles (enablers, payoffs, interaction, engines)
- GoalEngine tracks which goal is active

**Key scoring flow:**
1. `decide_main_phase()` gets legal plays from `game.get_legal_plays()`
2. Each spell scored by `_score_spell()` → base EV + archetype modifier
3. Storm patience gate: at storm=0, hold rituals/tutors unless ready to go off
4. Landfall deferral: hold land play when landfall creature is castable
5. Best play above `pass_threshold` is selected

### Layer 3: Deck Configuration

**Decklists** (`decks/modern_meta.py`) — mainboard + sideboard for all 13 decks

**Gameplans** (`decks/gameplans/*.json`) — per-deck strategy:
```json
{
  "deck_name": "Ruby Storm",
  "archetype": "combo",
  "goals": [...],
  "mulligan_keys": ["Ruby Medallion", "Desperate Ritual", ...],
  "mulligan_min_lands": 1,
  "mulligan_max_lands": 3,
  "reactive_only": [],
  "always_early": ["Ruby Medallion"],
  "critical_pieces": ["Grapeshot", "Empty the Warrens"]
}
```

**card_roles** in each goal:
- **enablers** — deployed proactively to support the plan
- **payoffs** — high-impact cards the deck builds toward
- **interaction** — removal, counterspells, disruption
- **engines** — card advantage or mana engines
- **fillers** — role players, cantrips

## Counterspell Targeting

Counterspells validate targeting restrictions from oracle text:
- `noncreature` in oracle → can't counter creature spells (Spell Pierce, Negate)
- `instant or sorcery` in oracle → can't counter permanents
- Checked at both AI layer (response.py) and engine layer (game_state.py)

## Storm Mechanics

Ruby Storm uses a dedicated `STORM` strategy profile with:
- **storm_patience**: hold rituals at storm=0 unless enough fuel + finisher access
- **storm_go_off_bonus/penalty**: gate the "go off" decision
- **PiF sequencing**: hold Past in Flames until GY has fuel, don't cast with empty GY
- **Finisher gating**: reduce ritual commitment when no Wish/Grapeshot in hand

Other combo decks (Goryo's, Amulet, Living End) use base COMBO profile WITHOUT storm patience.

## Testing

```bash
python -m pytest tests/ -q          # 73 tests
```

Tests include: deck loading, gameplan loading, matchup balance, card effects, game completion.

## Debugging

```bash
# Full AI reasoning trace
python run_meta.py --trace storm dimir -s 42000

# Game actions log
python run_meta.py --verbose zoo omnath -s 42000

# Legacy debug dump
python dump_game.py

# BO3 match → HTML play-by-play
python simulate_match.py "Ruby Storm" "Domain Zoo" --seed 55555
```

## Important Conventions

- **Engine layer enforces rules; AI layer makes choices.** Never add strategic decisions to engine code.
- **Card effects use EFFECT_REGISTRY decorator pattern.** Register with `@EFFECT_REGISTRY.register("Card Name", EffectTiming.ETB)`.
- **Strategy profiles are pure data.** All AI weights live in `ai/strategy_profile.py`. Per-deck tuning goes in `decks/gameplans/*.json`.
- **Seeds for reproducibility.** Standard: matchups=50000, matrix=40000, step=500.
- **Sideboards must be passed** to `run_game()` for Wish/tutor effects to find sideboard cards.
- **Keyword detection uses word boundaries** — "flash" won't match "flashback".
- **Color solver uses re-sorting greedy** — handles 4-color WURG correctly.

## Dashboard — modern_meta_matrix_full.html

The interactive metagame dashboard is a **standalone vanilla JS HTML file** (no React, no Babel).

**Data source:** `metagame_14deck.jsx` — the canonical D object with:
- `wins[i][j]` — win counts (out of `matches_per_pair=100`)
- `matchup_cards["i,j"]` — per-matchup detail: insight, avg_turns, sweeps, went_to_3, g1_wins, comebacks, top_casts, finishers, top_damage, sideboard IN/OUT with cast counts + post-board WR delta
- `deck_cards[idx]` — per-deck: mvp_casts, mvp_damage, finishers with descriptions, summary
- `overall[idx]` — flat WR, weighted WR, meta share, delta
- `meta_shares` — tournament representation %

**Build command (each session):**
```python
# Extract D from JSX, embed in HTML template
import re, json
with open('metagame_14deck.jsx') as f: src = f.read()
D = json.loads(re.search(r'const D = (\{.*?\});\nconst N', src, re.DOTALL).group(1))
# ... embed into standalone HTML with vanilla JS render engine
```

**Dashboard features:**
- Slide-in detail panel (CSS `translateX` transition, 420px)
- T1/T2/T3/T4 tier chips above matrix (clickable, weighted or flat toggle)
- HSL heatmap color scale: red (0%) → amber (50%) → green (100%)
- Hover tooltip: WR%, archetype labels, reverse WR, symmetry check
- Sticky row headers + sticky avg WR column
- Sort (weighted/flat/A-Z), archetype filter, highlight-deck, weighted toggle
- Opacity dimming of non-selected rows/columns
- Deck profile: tier badge, flat/weighted WR + delta pp, MVP cards, finishers, matchups by opponent tier
- Matchup detail: large WR, insight narrative, stats grid, key cards per side, sideboard guide
- Fonts: Outfit (UI) + JetBrains Mono (numbers/cards)

**Adding a new deck:**
1. Run `run_meta.py --field "New Deck" -n 100 --save` to get win data
2. Add wins row/col and basic `matchup_cards` entries to `metagame_14deck.jsx` D object
3. Run verbose matchups for card-level detail: `run_meta.py --verbose "New Deck" opp -s SEED`
4. Rebuild HTML from updated JSX

## Replay Viewer — Pipeline

**Do NOT use `simulate_match.py` output** — wrong color scheme and table layout.

**Correct pipeline:**
```bash
# 1. Run verbose Bo3 and save log
python run_meta.py --bo3 "Ruby Storm" "Affinity" -s 55555 > /tmp/bo3_log.txt

# 2. Parse log with Python → build HTML following replay_burn_vs_sneak_a.html spec
```

**Parser rules (critical):**
- Store board state keyed by **player name** (`boards['Boros Energy']`), never by `active`/`opp` — the active player swaps every turn, causing columns to flip if you use relative keys
- Display end-of-turn board using **next turn's header** (turn N's header = state before N's plays; turn N+1's header = state after)
- `╔══ TURN N ══╗` header sections are labelled `║ PlayerName board:` — match exactly

**Design:** GitHub-dark (`#0d1117`), collapsible turn cards, 15 category badges, `.active` for keyboard nav, `boards` keyed by player name. See `/mtg-bo3-replayer-v2` skill for full spec.
