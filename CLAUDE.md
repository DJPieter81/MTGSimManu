# CLAUDE.md — MTG Game Simulator

## Critical Instructions (read first)

**GAME LOG OUTPUT — NEVER SKIP THIS:**
When the user asks to see a game, match, Bo3, simulation, play-by-play, or any game log:
1. Run `python run_meta.py --bo3 <deck1> <deck2> -s <seed>`
2. Output the COMPLETE raw text log — every single line
3. NEVER summarize, paraphrase, abbreviate, or skip parts of the log
4. NEVER replace log lines with your own narrative or commentary
5. Show the log FIRST in full, then add commentary AFTER if needed
6. If the output is too large for one message, split across multiple messages — do NOT truncate

The user wants to read the actual log text with phases, draws, mana taps, priority passes, board states, goals, and mulligan rationale. This applies every time, without exception. Failure to show the full log is considered a regression.

## Project Overview

Magic: The Gathering Modern-format game simulator with EV-based AI decision-making. Simulates full games between 13 competitive decks with strategic AI (mulligans, spell casting, combat, targeting, counterspells, evoke, storm chains, reanimation, cascade, blink).

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
python run_meta.py --list                              # all available decks
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

**(See Critical Instructions at top of file for game log output requirements.)**

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

## Available Decks

Run `python run_meta.py --list` to see all decks with meta shares. Decklists live in `decks/modern_meta.py`. Check WARNING lines in game output for cards missing from ModernAtomic.json (placeholder cards deflate WR).

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

**Decklists** (`decks/modern_meta.py`) — mainboard + sideboard for all decks

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

- **NEVER hardcode card names, deck names, or card counts.** All card behavior must be derived from oracle text, template properties, tags, and keywords. If you find yourself writing `if card.name == "Something"`, stop — use a tag, keyword, or oracle pattern instead. Deck counts, land sets, and card lists must be discovered at runtime from `MODERN_DECKS`, `CardDatabase`, and oracle parsing. The CLAUDE.md itself should not list specific deck counts or card names that go stale — use `python run_meta.py --list` to discover what's available.
- **Engine layer enforces rules; AI layer makes choices.** Never add strategic decisions to engine code.
- **Card effects use EFFECT_REGISTRY decorator pattern.** Register with `@EFFECT_REGISTRY.register("Card Name", EffectTiming.ETB)`.
- **All AI scoring derives from clock mechanics (`ai/clock.py`).** No arbitrary weight constants. Spell value = projected position change. Land value = spells it enables × clock impact. Creature value = power/opp_life × keyword modifiers.
- **Strategy profiles are minimal.** Only fields that can't be derived from game mechanics: combo flags, burn mode, pass threshold, mulligan config. See `ai/strategy_profile.py`.
- **Seeds for reproducibility.** Standard: matchups=50000, matrix=40000, step=500.
- **Sideboards must be passed** to `run_game()` for Wish/tutor effects to find sideboard cards.
- **Land entry logic is oracle-derived.** `template.untap_life_cost`, `template.untap_max_other_lands`, `template.tap_damage`, `template.enters_tapped` — no hardcoded land sets.
- **Fetch land colors parsed at DB load time** from oracle text `"search your library for a [type] card"`. See `FETCH_LAND_COLORS` in `card_database.py`.
- **Opponent modeling uses Bayesian Hand Inference** (`ai/bhi.py`). Prior from deck density, updated on priority passes and spells cast.
- **Keyword detection uses word boundaries** — "flash" won't match "flashback".
- **Color solver uses re-sorting greedy** — handles 4-color WURG correctly.

## Dashboard — modern_meta_matrix_full.html

The interactive metagame dashboard is a **standalone vanilla JS HTML file** (no React, no Babel).

**Data source:** `metagame_14deck.jsx` (or current deck-count variant) — the canonical D object with:
- `wins[i][j]` — win counts (out of `matches_per_pair=100`)
- `matchup_cards["i,j"]` — per-matchup detail: insight, avg_turns, sweeps, went_to_3, g1_wins, comebacks, top_casts, finishers, top_damage, sideboard IN/OUT with cast counts + post-board WR delta
- `deck_cards[idx]` — per-deck: mvp_casts, mvp_damage, finishers with descriptions, summary
- `overall[idx]` — flat WR, weighted WR, meta share, delta
- `meta_shares` — tournament representation %

**Build command (each session):**
```python
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

## Replay Viewer — simulate_match.py

Produces standalone HTML Bo3 replay. GitHub-dark theme (`#0d1117`), collapsible turn cards (NOT tables), gradient header, game tabs with winner dots, opening hand pills, SVG life chart, numbered plays with category badges, board state grid, result box.

```bash
python simulate_match.py "Ruby Storm" "Domain Zoo" --seed 55555 -o replay.html
```

Debug outlier matchups: loop seeds until the underdog wins, save that seed. Check WARNING lines in output for missing cards (0/0 stats = card not in ModernAtomic.json).
