# CLAUDE.md — MTG Game Simulator

## Project Overview

Magic: The Gathering Modern-format game simulator with EV-based AI decision-making. Simulates full games between 16 competitive decks with strategic AI (mulligans, spell casting, combat, targeting, counterspells, evoke, storm chains, reanimation, cascade, blink).

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
python run_meta.py --list                              # all 16 decks
python run_meta.py --deck storm                        # deck profile + gameplan
python run_meta.py --matchup storm dimir -n 50         # win rate (N games)
python run_meta.py --field storm -n 30                 # one deck vs all
python run_meta.py --matrix -n 20                      # full 16x16 matrix
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

## Available Decks (16)

Boros Energy, Jeskai Blink, Ruby Storm, Affinity, Pinnacle Affinity, Eldrazi Tron, Amulet Titan, Goryo's Vengeance, Domain Zoo, Living End, Izzet Prowess, Dimir Midrange, 4c Omnath, 4/5c Control, Azorius Control, Azorius Control (WST)

**Known DB gaps:** ~~`The Legend of Roku` and `Sink into Stupor`~~ — both now resolved after ModernAtomic refresh (Apr 2026). All 16 decks sim correctly.

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

**Decklists** (`decks/modern_meta.py`) — mainboard + sideboard for all 16 decks

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

See **`docs/history/audits/2026-04-11_LLM_judge.md`** for the full 6-expert panel report (~168 games). Overall grade: **D+**. Superseded by `PROJECT_STATUS.md` (see Grade in §6).

### P0 — Critical (game-breaking)

| Issue | Location | Summary |
|-------|----------|---------|
| Removal projection kills creature deployment | `ai/ev_evaluator.py:539-572` | `estimate_opponent_response` makes all cheap creatures negative EV (Guide of Souls=-7.6, Memnite=-7.4). Aggro decks pass T1-T3. |
| Storm finisher uncastable | `ai/ev_player.py:393-484` | PiF penalty `gy_fuel/opp_life*15` makes it -5.8 even with 7 mana + 9 GY spells. Storm at 39% WR. |
| Goryo's combo non-functional | `engine/card_effects.py` discard | Faithful Mending never bins Griselbrand → Goryo's has no target. Combo never fires. |
| Living End missing ETBs | `engine/game_state.py:~1710` | `_resolve_living_end()` skips `_handle_permanent_etb()`. Returned creatures get no ETB triggers. |
| Chalice hardcoded X=1 | `engine/game_state.py:1349` | Always X=1 regardless of opponent. Locks Azorius out of own spells (-0.76 win delta). |

### P1 — High (significant strategy errors)

| Issue | Location | Summary |
|-------|----------|---------|
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

**Data source:** `metagame_data.jsx` — the canonical D object with:
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
`--merge` reads `metagame_results.json`, merges wins into `metagame_data.jsx` (preserving all matchup_cards/deck_cards), recomputes WRs, then builds the HTML. Always use `--merge` after running `--matrix`.

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
3. Add wins row/col and basic `matchup_cards` entries to `metagame_data.jsx` D object
4. Run verbose matchups for card-level detail: `run_meta.py --verbose "New Deck" opp -s SEED`
5. Rebuild HTML: `python3 build_dashboard.py metagame_data.jsx`

## Replay Viewer — Pipeline

Canonical pipeline:
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

**Design:** Light theme (white `#ffffff` bg, GitHub light palette), collapsible turn cards, 15 category badges, `.active` for keyboard nav, `boards` keyed by player name. P1 = `#0969da` (blue), P2 = `#d1242f` (red). Reference: `templates/reference_replay.html`.

**build_replay.py features (current):**
- **Scryfall thumbnails** — every card pill and creature badge has a hover image (`api.scryfall.com/cards/named?exact=NAME&version=small`). Card names URL-encoded with `urllib.parse.quote`.
- **Equipment tags** — `⚔Cranial Plating` badge on creature when equipped, tracked from `Equip X to Y` / `falls off` log lines into `equip_map` per turn.
- **Lethal callout** — `☠ LETHAL — N damage → life X → -Y` red banner when combat damage kills a player.
- **Per-attacker damage** — `BREAKDOWN:` lines show each unblocked attacker's P/T + individual damage contribution.
- **Block reasoning** — `🛡 BLOCK:` (normal) and `🚨 BLOCK-EMRG:` (emergency) lines with blocker/attacker P/T and reason (chump/trade/favorable trade).
- **Other permanents row** — equipment, mana rocks, enchantments shown between creatures and lands in board state.
- **Dot-click reasoning** — `·` expands AI goal reasoning per play (unique IDs: `r{game}t{turn}p{pidx}s{step}`).

## Planning Reference — PROJECT_STATUS.md

**Read `PROJECT_STATUS.md` before any session.** It is the single-source-of-truth for Claude Code planning mode. Contains: architecture diagram with line counts, AI decision flow, Python API signatures with return shapes, runtime benchmarks, all bugs with fix status + commit hashes, deck status table (working vs underperforming), generic engine patterns (oracle-driven, no hardcoding), "never do / always do" rules, and post-action verification scripts.

**Current grade: C** (session 3: blocking+attack+mulligan fixes validated; full 16×16 N=50 re-run; Affinity 93% and Living End 5% are new P0s)

Related docs: `MODERN_PROPOSAL.md` (6 infra proposals from Legacy), `docs/history/audits/2026-04-11_LLM_judge.md` (original D+ audit), `LEGACY_MODERNISATION_PROPOSAL.md` (Legacy adoption plan).

## Sister Project — MTGSimClaude (Legacy)

Repository: `github.com/DJPieter81/MTGSimClaude` (38 decks, 2.5ms/game, G1-only matrix).

Both projects share the same Claude skills (`/mtg-meta-matrix`, `/mtg-deck-guide`, `/mtg-bo3-replayer-v2`, `/mtg-dashboard-refresh`) and cross-pollinate architecture ideas. See cross-project proposals in `MODERN_PROPOSAL.md` and `LEGACY_MODERNISATION_PROPOSAL.md`.

Key differences: Legacy has per-deck strategy functions (deeper knowledge), Modern has EV scoring + BHI + combat sim (better architecture). Neither is strictly better.

## AI Decision Architecture (summary)

```
EVSnapshot ← snapshot_from_game()          # ev_evaluator.py
    ↓
GoalEngine.current_goal                    # gameplan.py (JSON-driven)
    ↓
Enumerate legal plays → Play objects       # ev_player.py
    ↓
Score: heuristic EV + clock Δ + combo mod  # ev_player.py + clock.py + combo_calc.py
    ↓
Discount by P(countered), P(removed)       # bhi.py (Bayesian hand inference)
    ↓
TurnPlanner: 5 orderings evaluated         # turn_planner.py
    ↓
Execute best → log reasoning               # strategic_logger.py
```

**Known weakness:** Generic `_score_spell()` has no per-card overrides → planeswalkers score ~0 (P0), storm rituals penalised mid-chain (P1). Fix: `card_ev_overrides` in gameplan JSON + combo chain EV bypass.

## Deck Guide Minimum Spec

Guides must match the Legacy Burn guide (`guide_burn.html`) feature-for-feature:

1. **Hero** — 4-col: format, sim WR (flat + weighted), rank/tier, best/worst
2. **Decklist** — Mainboard with role badges + card notes + Scryfall hover popups, SB with "vs" targets
3. **Deck construction findings** — ±pp values derived from sim data
4. **Game plan** — 3-phase timeline with colored dots and turn-by-turn descriptions
5. **Kill turn distribution** — Bar chart from sim data
6. **Hand archetype WR** — Horizontal bars with baseline marker
7. **Real sim hands** — 2-3 keep + 1 mull, each with turn-by-turn play sequence and strategic commentary
8. **Metagame strategy** — Archetype WR bars + matchup triptych (prey/competitive/danger)
9. **Matchup spread** — All opponents tiered T1/T2/Field, with archetype type + meta% columns
10. **Provenance footer** — Date, deck count, games/pair, engine version, attribution

Scryfall hovers: `<span class="card-tip" data-card="Card Name">Card Name</span>` + JS popup using `api.scryfall.com/cards/named?fuzzy=NAME&format=image&version=normal`.

## Post-Action Verification

Run after every major operation:

```bash
# After dashboard rebuild
python3 -c "
import re
with open('metagame_data.jsx') as f: c=f.read()
n = re.search(r'const N = (\d+)', c)
d = re.findall(r'\"decks\":\[(.+?)\]', c)
print(f'N={n.group(1) if n else \"MISSING\"}, decks={len(d[0].split(\",\")) if d else \"MISSING\"}')"

# After deck import
python3 -c "
from decks.modern_meta import MODERN_DECKS, METAGAME_SHARES
print(f'Decks: {len(MODERN_DECKS)}, Shares: {len(METAGAME_SHARES)}')
assert len(MODERN_DECKS) == len(METAGAME_SHARES), 'MISMATCH'"

# Smoke test (both orderings should sum to ~100%)
python run_meta.py --matchup NEW_DECK dimir -n 10
python run_meta.py --matchup dimir NEW_DECK -n 10
```

## Project Showcase — Skill

Skill: `/mtg-project-showcase` — generates interactive marketing/portfolio HTML pages for the project.

**Triggers:** "showcase", "marketing page", "show off", "visualize the project", "share with friends"

**Pipeline:** git pull → read docs → run benchmarks → extract JSX stats → generate 3 artifacts (dashboard, replay, deck guide) → build showcase HTML → present files

**Output:** 4 cross-linked HTML files in same directory:
- `mtgsimmanu_showcase.html` — main page (70K, 10 interactive sections)
- `modern_meta_matrix.html` — dashboard artifact (132K)
- `boros_energy_guide.html` — deck guide artifact (32K)
- `replay_*.html` — Bo3 replay artifact (143K)

**Re-run after:** new matrix sim, P0/P1 fixes, new decks, proposal acceptance changes.

**Design:** Light cream theme, Playfair Display + DM Sans + JetBrains Mono, Chart.js for graphs, 6 layer color codes, scroll-triggered animations, IntersectionObserver reveals.

### Deck Guide Strategic Insights — Pro-Level Methodology

Guides target pro tour players. The metagame strategy section requires 6+ non-obvious findings mined from `matchup_cards` data. Each finding must be data-backed, counterintuitive, and actionable.

**Required findings template:**
1. **Damage-to-kill efficiency paradox** — compare `mvp_damage` vs `finishers` kill counts, calculate dmg/kill ratio
2. **Closer changes per matchup** — group `d1_finishers[0]` by `avg_turns`, show counterintuitive patterns
3. **G1 → Match WR swing** — calculate `match_wr - g1_wr`, identify whether YOUR or OPPONENT's SB caused the swing
4. **Structural removal blind spots** — cross-reference `d2_top_damage` in losing matchups against your removal suite
5. **Hidden damage sources** — find tokens/unexpected cards in `mvp_damage`, derive boarding rules
6. **Weighted WR gap** — compare `weighted_wr - win_rate` across all decks for meta positioning insight

**Quality bar:** If a finding would be obvious to someone who's played 10 matches with the deck, it's not good enough. Target findings that require 700+ simulated games to discover.

## Templates — Reference Outputs

```
templates/
├── reference_deck_guide.html    — Boros Energy guide (canonical, 348 lines)
└── reference_showcase.html      — Project showcase page (canonical, ~800 lines)
```

**Usage:** When generating a new deck guide or showcase, read the reference template first to match structure, design, and insight depth. Do NOT regenerate from scratch — use the template's CSS, JS (Scryfall hovers), and section order, then swap data.

**Deck guide template features:**
- Stars of the Sim (4 card thumbnails from Scryfall: 2 MVPs + 2 overperformers)
- Card-level sim stats in decklist notes (casts, dmg, finisher count from `deck_cards`)
- SB "vs" targets citing actual `matchup_cards.d1_sb` cast counts
- 6 non-obvious strategic findings mined from `matchup_cards` (see methodology above)
- Matchup spread tiered T1/T2/Field with archetype type + meta%
- Provenance footer tracing to exact JSX keys

**Showcase template features:**
- 10+ interactive sections (architecture, AI pipeline, heatmap, validation, roadmap)
- Product cards linking to artifacts via relative URLs (same directory)
- Cross-project comparison with acceptance status
- Chart.js charts (WR bars, resolution doughnut, AI radar, game length)

**When to update templates:** After major design changes, new section types, or insight methodology upgrades. Always commit updated templates alongside the code that generated them.

## Deck Guide Builder — build_guide.py

```bash
# Single deck
python build_guide.py "Boros Energy" /mnt/user-data/outputs/guide_boros_energy.html

# All T1/T2 decks (meta ≥ 3%)
python build_guide.py --all /mnt/user-data/outputs/
```

Reads `metagame_data.jsx` D object. Generates: hero stats, Stars of Sim (Scryfall thumbnails), G1→match swing table, danger cards (removal blind spots), tiered matchup spread, provenance footer. All data traced to JSX keys.

**Note:** `build_guide.py` produces the data-driven skeleton. The hand-crafted Boros guide in `templates/reference_deck_guide.html` has additional depth: real sim hands, game plan phases, hand archetype WR bars, and 6 pro-level findings. For tournament-grade guides, use the template as the reference and augment `build_guide.py` output.

## Post-Sim Replay Generation — Triggered Pipeline

After every matrix sim or matchup run, automatically generate Bo3 replay logs for investigation. This replaces manual "save a replay" decisions.

### Trigger: after `run_meta.py --matrix` or `build_dashboard.py --merge`

```bash
# Step 1: Identify replay-worthy matchups from JSX data
python3 << 'PY'
import json
with open('metagame_data.jsx') as f: jsx = f.read()
D = json.loads(jsx[jsx.index('const D = ')+10 : jsx.index(';\nconst N')])

EXPECTED = {  # (low, high) — update when meta shifts
    'Boros Energy': (50,70), 'Affinity': (45,60), 'Eldrazi Tron': (50,65),
    'Jeskai Blink': (45,60), 'Ruby Storm': (40,55), 'Domain Zoo': (50,65),
    'Izzet Prowess': (45,60), 'Dimir Midrange': (45,60),
}

targets = set()
for o in D['overall']:
    d = o['deck']
    wr = o['win_rate']
    lo, hi = EXPECTED.get(d, (30, 70))
    if wr < lo or wr > hi:
        # Outlier deck: replay its worst and best matchup
        idx = o['idx']
        wins = D['wins'][idx]
        worst_i = min(range(len(wins)), key=lambda i: wins[i] if i != idx else 999)
        best_i = max(range(len(wins)), key=lambda i: wins[i] if i != idx else -1)
        targets.add((d, D['decks'][worst_i]))
        targets.add((d, D['decks'][best_i]))

    # G1 → match swing > 20pp
    for i in range(len(D['decks'])):
        key = f"{o['idx']},{i}"
        mc = D['matchup_cards'].get(key, {})
        if mc.get('g1_wins'):
            g1 = mc['g1_wins'][0]
            match_wr = round(D['wins'][o['idx']][i] / D['matches_per_pair'] * 100)
            if abs(match_wr - g1) >= 20:
                targets.add((d, D['decks'][i]))

for d1, d2 in sorted(targets):
    print(f"{d1} vs {d2}")
PY

# Step 2: Generate Bo3 logs + HTML for each target
SEED=60100
for pair in $(python3 -c "...targets output..."); do
    d1=$(echo $pair | cut -d'|' -f1)
    d2=$(echo $pair | cut -d'|' -f2)
    slug="${d1// /_}_vs_${d2// /_}_s${SEED}"
    python3 run_meta.py --bo3 "$d1" "$d2" -s $SEED > "replays/${slug,,}.txt"
    python3 build_replay.py "replays/${slug,,}.txt" "/mnt/user-data/outputs/replay_${slug,,}.html" $SEED
    SEED=$((SEED+1))
done

# Step 3: Commit logs
git add replays/*.txt
git commit -m "data: auto-generated replay logs for outlier/swing matchups"
```

### Replay triggers (when to auto-generate)
1. **Outlier decks**: WR outside expected range → replay worst + best matchup
2. **G1→match swing ≥ 20pp**: sideboard is transformational → replay to see the transformation
3. **0 comebacks in 10+ matches**: unwinnable when behind → replay to diagnose
4. **New deck added**: replay vs T1 field (Boros, Jeskai, Affinity)

### Files produced per replay
- `replays/{d1}_vs_{d2}_s{SEED}.txt` — raw Bo3 log (committed to repo)
- `/mnt/user-data/outputs/replay_{...}.html` — interactive HTML viewer (output to user)

## GitHub Pages — Link Rules

Base URL: `https://djpieter81.github.io/MTGSimManu/`

All links in `templates/reference_showcase.html` MUST be absolute GitHub Pages URLs — not relative. The showcase lives in `templates/`, so relative links resolve to `templates/filename.html` which 404s.

Current product links:
- Matrix: `https://djpieter81.github.io/MTGSimManu/modern_meta_matrix_full.html`
- Deck Guide: `https://djpieter81.github.io/MTGSimManu/templates/reference_deck_guide.html`
- Replay: `https://djpieter81.github.io/MTGSimManu/replays/replay_boros_vs_zoo.html`
- Legacy: `https://djpieter81.github.io/MTGSimClaude/results/mtgsimclaude_showcase.html`

**Any new HTML intended for live viewing must be committed to the repo** — `/mnt/user-data/outputs/` is local only.

## Cross-Project Sync

Read `CROSS_PROJECT_SYNC.md` before any cross-project work. It tracks:
- Legacy → Modern adoption (parallel.py, hypothesis_testing.py, deck_registry.py)
- Modern → Legacy adoption (proInsights(), G1/G3/sweep stats, SB guide)
- Shared modules (clock.py, bhi.py, strategic_logger.py, gameplan.py)
- Common standards (file naming, GitHub Pages URLs, skill format)

Same file exists in both repos — keep them in sync.
