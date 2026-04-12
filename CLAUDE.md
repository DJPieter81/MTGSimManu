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
python run_meta.py --list                              # all 14 decks
python run_meta.py --deck storm                        # deck profile + gameplan
python run_meta.py --matchup storm dimir -n 50         # win rate (N games)
python run_meta.py --field storm -n 30                 # one deck vs all
python run_meta.py --matrix -n 20                      # full 14x14 matrix
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
| **HTML Bo3** | `python run_meta.py --bo3 "D1" "D2" -s SEED > replays/log.txt && python build_replay.py replays/log.txt out.html SEED` |
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

**Before registering any new deck or updating meta shares — check DB freshness:**
```bash
# Check when the card DB was last updated
python3 -c "
import json, glob, os
parts = sorted(glob.glob('ModernAtomic_part*.json'))
if parts:
    age_days = (os.path.getmtime(parts[0]))
    import time
    age = (time.time() - age_days) / 86400
    print(f'ModernAtomic parts last modified: {age:.0f} days ago')
    if age > 14:
        print('WARNING: DB may be stale — new sets may be missing. Run update_modern_atomic.py')
    else:
        print('DB is recent — OK to proceed')
else:
    print('No ModernAtomic parts found — run update_modern_atomic.py')
"
# If stale or new sets released since last update:
python3 update_modern_atomic.py
git add ModernAtomic_part*.json
git commit -m "chore: refresh ModernAtomic for new sets"
git push origin main
```

**New Modern-legal sets to watch (2026):** Lorwyn Eclipsed (Jan 2026), TMNT (Feb 2026), Secrets of Strixhaven (Apr 24 2026). Run `update_modern_atomic.py` if any of these postdate your last DB refresh.

**Import a new deck:**
```bash
python import_deck.py "Deck Name" decklist.txt
python import_deck.py "Deck Name" --archetype control < decklist.txt
```
Auto-detects archetype, generates gameplan, prints code to paste into modern_meta.py.

## Available Decks (15)

Boros Energy, Jeskai Blink, Ruby Storm, Affinity, Eldrazi Tron, Amulet Titan, Goryo's Vengeance, Domain Zoo, Living End, Izzet Prowess, Dimir Midrange, 4c Omnath, 4/5c Control, Azorius Control, Azorius Control (WST)

**Notes:**
- Azorius Control = Yuri Anichini Isochron Scepter + Orim's Chant build (1st place Modern Monster, Feb 2026). Isochron Scepter mechanic NOT simulated — WR deflated.
- Azorius Control (WST) = Wan Shi Tong + Chalice of the Void draw-go build. Field run only (not in full matrix yet).
- All DB gaps resolved (Apr 2026 refresh).

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

**Decklists** (`decks/modern_meta.py`) — mainboard + sideboard for all 15 decks

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

# BO3 match → HTML replay (correct pipeline)
python run_meta.py --bo3 "Ruby Storm" "Domain Zoo" -s 55555 > replays/log.txt
python build_replay.py replays/log.txt replay.html 55555
```

## Important Conventions

- **Engine layer enforces rules; AI layer makes choices.** Never add strategic decisions to engine code.
- **Card effects use EFFECT_REGISTRY decorator pattern.** Register with `@EFFECT_REGISTRY.register("Card Name", EffectTiming.ETB)`.
- **Strategy profiles are pure data.** All AI weights live in `ai/strategy_profile.py`. Per-deck tuning goes in `decks/gameplans/*.json`.
- **Seeds for reproducibility.** Standard: matchups=50000, matrix=40000, step=500.
- **Sideboards must be passed** to `run_game()` for Wish/tutor effects to find sideboard cards.
- **Keyword detection uses word boundaries** — "flash" won't match "flashback".
- **Color solver uses re-sorting greedy** — handles 4-color WURG correctly.

## Known Issues — LLM-Judge Strategy Audit

See **`LLM_JUDGE_STRATEGY_AUDIT.md`** for the full 6-expert panel report (~168 games). Overall grade: **D+**.

### P0 — Critical (game-breaking)

| Issue | Location | Summary |
|-------|----------|---------|
| Wrath of the Skies X=0 kills all creatures | `engine/card_effects.py` | At X=0, should only destroy MV≤0. Instead destroys all. Ragavan (MV1) wrongly dies. Fix: compare creature MV against energy paid. |
| Ocelot Pride energy trigger fires on ETB | `engine/card_effects.py` | Registered as ETB trigger giving 1{E}. Real trigger is "whenever you cast a noncreature spell." DB also has wrong oracle (Ixalan version). Fix: re-register as cast trigger + fix oracle. |
| Removal projection kills creature deployment | `ai/ev_evaluator.py:539-572` | `estimate_opponent_response` makes all cheap creatures negative EV (Guide of Souls=-7.6, Memnite=-7.4). Aggro decks pass T1-T3. |
| Storm finisher uncastable | `ai/ev_player.py:393-484` | PiF penalty `gy_fuel/opp_life*15` makes it -5.8 even with 7 mana + 9 GY spells. Storm at 39% WR. |
| Goryo's combo non-functional | `engine/card_effects.py` discard | Faithful Mending never bins Griselbrand → Goryo's has no target. Combo never fires. |
| Living End missing ETBs | `engine/game_state.py:~1710` | `_resolve_living_end()` skips `_handle_permanent_etb()`. Returned creatures get no ETB triggers. |
| Chalice hardcoded X=1 | `engine/game_state.py:1349` | Always X=1 regardless of opponent. Locks Azorius out of own spells (-0.76 win delta). |

### P1 — High (significant strategy errors)

| Issue | Location | Summary |
|-------|----------|---------|
| Sanctifier en-Vec resolves after combat | `engine/game_runner.py` | Spells cast in Main 1 sometimes resolve after combat damage step. Stack should clear before Begin Combat. |
| Ragavan never attacks | `ai/ev_player.py` | AI keeps Ragavan back; entire card value is combat damage triggers. Should prioritise attacking. |
| Wrath on empty board | `ai/ev_evaluator.py:272` | Board wipes with 0 creatures pass the -5.0 threshold at EV=-0.1. |
| Burn face with no clock | `ai/strategy_profile.py:102` | `burn_face_mult=1.5` makes face burn positive EV even on empty board T1. |
| Fatal Push mis-targets | `ai/response.py:156-169` | Targets highest-value battlefield creature, not the incoming spell on the stack. |
| Holdback broken vs spell decks | `ai/ev_player.py:337-349` | Only triggers on `opp_power>0`. Control taps out freely vs Storm. |
| First strike missing from AI combat sim | `ai/turn_planner.py:398-478` | `_simulate_combat` applies all damage simultaneously. Engine is correct; AI evaluation is not. |

### P2 — Medium

- Living End mulligan too aggressive (`ai/mulligan.py:60`): combo_sets should relax at 6 cards, not 5
- Tron lands not differentiated (`ai/ev_player.py:540-616`): no assembly bonus for missing piece
- Empty the Warrens underutilized: Wish tutor too Grapeshot-biased
- Ghost candidates in EV list: stale snapshot after spell resolution within same main phase
- Duplicate EV trace blocks: Main1+Main2 without phase labels

### Confirmed Working

Turn structure, cascade, storm copies, counterspell restrictions, legend rule, Bowmasters ETB, Phlage ETB, ritual mana tracking, fetch land prioritization, land-before-spell sequencing.

## Dashboard — modern_meta_matrix_full.html

The interactive metagame dashboard is a **standalone vanilla JS HTML file** (no React, no Babel).

**Data source:** `metagame_14deck.jsx` — the canonical D object with:
- `wins[i][j]` — win counts (out of `matches_per_pair=100`)
- `matchup_cards["i,j"]` — per-matchup detail: insight, avg_turns, sweeps, went_to_3, g1_wins, comebacks, top_casts, finishers, top_damage, sideboard IN/OUT with cast counts + post-board WR delta
- `deck_cards[idx]` — per-deck: mvp_casts, mvp_damage, finishers with descriptions, summary
- `overall[idx]` — flat WR, weighted WR, meta share, delta
- `meta_shares` — tournament representation %

**Build command (each session):**
```bash
# After a new matrix run — merge wins + preserve card detail, then build:
python3 build_dashboard.py --merge

# Without a new run — just rebuild HTML from existing JSX:
python3 build_dashboard.py
```
`--merge` reads `metagame_results.json`, merges wins into `metagame_14deck.jsx` (preserving all matchup_cards/deck_cards), recomputes WRs, then builds the HTML. Always use `--merge` after running `--matrix`.

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
1. **Check DB freshness first** — run the freshness check above. If any new sets have been released since last update, run `python3 update_modern_atomic.py` before proceeding.
2. Run `run_meta.py --field "New Deck" -n 100 --save` to get win data
3. Add wins row/col and basic `matchup_cards` entries to `metagame_14deck.jsx` D object
4. Run verbose matchups for card-level detail: `run_meta.py --verbose "New Deck" opp -s SEED`
5. Rebuild HTML: `python3 build_dashboard.py metagame_14deck.jsx`

## Replay Viewer — Pipeline

**Do NOT use `simulate_match.py` output** — wrong color scheme and table layout.

**Correct pipeline:**
```bash
# 1. Run verbose Bo3 and save log
python run_meta.py --bo3 "Ruby Storm" "Affinity" -s 55555 > replays/ruby_storm_vs_affinity_s55555.txt

# 2. Build HTML replay
python build_replay.py replays/ruby_storm_vs_affinity_s55555.txt /mnt/user-data/outputs/replay.html 55555
```
Always save logs to `replays/` and commit. `build_replay.py` is in the repo with the full parser — use it, never rewrite from scratch.

**Parser rules (critical):**
- Store board state keyed by **player name** (`boards['Boros Energy']`), never by `active`/`opp` — the active player swaps every turn, causing columns to flip if you use relative keys
- Display end-of-turn board using **next turn's header** (turn N's header = state before N's plays; turn N+1's header = state after)
- `╔══ TURN N ══╗` header sections are labelled `║ PlayerName board:` — match exactly

**Design:** Light theme (white `#ffffff` bg, GitHub light palette), collapsible turn cards, 15 category badges, `.active` for keyboard nav, `boards` keyed by player name. P1 = `#0969da` (blue), P2 = `#d1242f` (red). See `/mtg-bo3-replayer-v2` skill for full spec.
