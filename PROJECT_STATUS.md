# MTGSimManu — Project Status & Planning Reference

> **Last updated:** 2026-04-12
> **Purpose:** Single-source-of-truth for Claude Code planning mode. Read this before any session.
> **Sister project:** MTGSimClaude (Legacy format, 38 decks, see LEGACY_MODERNISATION_PROPOSAL.md)

---

## 1. What this project is

A **Modern-format Magic: The Gathering game simulator** with EV-based AI decision-making. Pure Python 3.11, zero external dependencies. Simulates full Bo3 matches between 15 competitive Modern decks, produces interactive dashboards, deck guides, and replay viewers.

**Repository:** `github.com/DJPieter81/MTGSimManu` (branch: `main`)

**Origin:** Initial engine shell and card database integration by [ManusAI](https://manus.im). Strategy layer, EV scoring, output products, Claude skills, and ongoing development by DJPieter81 + Claude.

---

## 2. Architecture (6 layers)

```
┌─────────────────────────────────────────────────────────────┐
│  SKILLS LAYER (Claude automation)                           │
│  /mtg-meta-matrix  /mtg-dashboard-refresh                   │
│  /mtg-deck-guide   /mtg-bo3-replayer-v2                     │
├─────────────────────────────────────────────────────────────┤
│  OUTPUT PIPELINE                                            │
│  build_dashboard.py → metagame_14deck.jsx → HTML heatmap    │
│  build_replay.py → Bo3 HTML replayer (light theme)          │
│  commentary_engine.py → strategic annotations               │
├─────────────────────────────────────────────────────────────┤
│  SIMULATION RUNNER                                          │
│  run_meta.py (CLI + Python API)                             │
│  --matrix --matchup --bo3 --field --audit --verbose --trace │
│  import_deck.py  simulate_match.py  match_trace.py          │
├─────────────────────────────────────────────────────────────┤
│  AI LAYER — EV-based decision engine (14 modules, 7757 ln)  │
│  ev_player.py (1224 ln) — score plays, pick best            │
│  gameplan.py (545 ln) — GoalEngine, goal sequences          │
│  turn_planner.py (1113 ln) — combat sim, 5 turn orderings   │
│  ev_evaluator.py (712 ln) — EVSnapshot, board projection    │
│  combo_calc.py (652 ln) — storm/graveyard/mana zones        │
│  clock.py (328 ln) — turns-to-kill position evaluation      │
│  bhi.py (275 ln) — Bayesian hand inference                  │
│  response.py (267 ln) — counterspell decisions              │
│  mulligan.py (210 ln) — keep/mull per archetype             │
│  board_eval.py (468 ln) — assess + evoke/dash/combo eval    │
│  mana_planner.py (373 ln) — fetch/land selection            │
│  combo_chain.py (359 ln) — storm chain simulation           │
│  strategic_logger.py (279 ln) — reasoning traces            │
│  strategy_profile.py — per-archetype weights                │
├─────────────────────────────────────────────────────────────┤
│  ENGINE LAYER — rules & state machine                       │
│  game_state.py (3160 ln)  game_runner.py  card_effects.py   │
│  card_database.py  combat_manager.py  event_system.py       │
│  continuous_effects.py  sideboard_manager.py                │
│  zone_manager.py  stack.py  sba_manager.py  oracle_parser.py│
├─────────────────────────────────────────────────────────────┤
│  DATA LAYER                                                 │
│  ModernAtomic.json (21,795 cards, 8 parts merged)           │
│  decks/modern_meta.py (15 decks + METAGAME_SHARES)          │
│  decks/gameplans/*.json (15 goal sequences)                 │
│  ai/strategy_profile.py (archetype AI weights)              │
│  decks/card_knowledge.json (card role tags)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. AI decision architecture

### Decision flow (per main phase)
```
EVSnapshot ← snapshot_from_game()          # ev_evaluator.py
    ↓
GoalEngine.current_goal                    # gameplan.py
    ↓
Enumerate legal plays → Play objects       # ev_player.py
    ↓
Score each: heuristic EV + clock Δ         # ev_player.py + clock.py
    + combo_modifier (if combo goal)       # combo_calc.py
    ↓
Discount by P(countered), P(removed)       # bhi.py
    ↓
TurnPlanner: evaluate 5 orderings          # turn_planner.py
    (deploy→attack, remove→attack,
     attack→deploy, hold mana, lethal)
    ↓
Pick highest EV → execute → log            # strategic_logger.py
```

### Known AI weakness: generic scoring loses deck-specific knowledge
The _score_spell() function uses archetype weights but has no per-card overrides. This causes:
- Planeswalkers score ~0 EV (no CardType.PLANESWALKER case) → P0 bug
- Storm rituals penalised mid-chain (generic scorer sees "no board impact") → P1 bug
- Wrath cast on empty board (penalty too soft) → P0 bug

**Fix pattern (from Legacy comparison):** Add `card_ev_overrides` to gameplan JSON. EVPlayer reads overrides before generic scoring. For combo turns: when GoalEngine.current_goal is combo and readiness > threshold, bypass _score_spell() and use combo_calc.py exclusively.

---

## 4. Python API signatures

```python
# ── Simulation ──
from run_meta import (
    run_meta_matrix,     # (top_tier=14, n_games=50, seed_start=40000) → {matrix, rankings, names}
    run_matchup,         # (deck1, deck2, n_games=50, seed_start=50000) → {wins, pct1, pct2, avg_turn, turn_dist}
    run_field,           # (deck, n_games=30) → {deck, matchups: {opp: pct}, average}
    run_verbose_game,    # (d1, d2, seed=42000) → str
    run_trace_game,      # (d1, d2, seed=42000) → str (+ AI reasoning)
    run_bo3,             # (d1, d2, seed=55555) → str
    inspect_deck,        # (deck_name) → str
    audit_deck,          # (deck_name, n_games=60) → str
    resolve_deck_name,   # (alias) → str (canonical name)
    save_results, load_results,
)

# ── AI internals ──
from ai.ev_evaluator import EVSnapshot, snapshot_from_game, evaluate_board
#   EVSnapshot fields: my_life, opp_life, my_power, opp_power, my_creature_count,
#     opp_creature_count, my_hand_size, my_mana, turn_number, storm_count, my_energy
#   Properties: .my_clock, .opp_clock, .has_lethal, .am_dead_next
from ai.clock import combat_clock, life_as_resource
from ai.bhi import BayesianHandTracker, HandBeliefs
from ai.gameplan import GoalEngine, create_goal_engine, get_gameplan
from ai.strategy_profile import get_profile, StrategyProfile, DECK_ARCHETYPES
from engine.card_database import CardDatabase  # singleton pattern
```

---

## 5. Runtime performance

| Metric | Value | Notes |
|--------|-------|-------|
| DB load | 6.5s | 21,795 cards from 8 JSON parts |
| Per Bo3 match | ~0.68s | Avg across aggro/combo/control |
| 50-pair batch | ~170s | Tool timeout limit per batch |
| Full 14×14 × 50 | ~95 min | 4,550 Bo3 matches |
| σ at n=50 | Not measured | **TODO:** run 5× same matchup to quantify |

---

## 6. AI strategy accuracy

**Overall grade: C-** (up from D+) · 6-expert LLM judge panel · 210 games

| Domain | Grade | | Domain | Grade |
|--------|-------|-|--------|-------|
| Rules & engine | B | | Mana & sequencing | C |
| Combat & threats | B- | | Combo & storm | C |
| Mulligan & openers | C+ | | Control & interaction | D |

### Missing validation (learned from Legacy)

| Validation | Action needed |
|------------|---------------|
| Spot-check vs consensus | Pick 10 matchups, compare to mtgtop8 expected ranges, document pass/fail |
| Symmetry test | Run both orderings per matchup, flag |d1+d2−100| > 10% |
| Noise floor | Run 5× same matchup at n=50, measure σ |

---

## 7. Known bugs

### P0
1. No planeswalker EV scoring → `ai/ev_player.py:_score_spell()` → 4c Omnath 29% WR
2. Wrath on empty board → `ai/ev_evaluator.py:272-275` → hard gate needed
3. Ocelot Pride energy on ETB → Card DB oracle wrong
4. Wrath of Skies X=0 kills all → Engine bug

### P1
5. Storm ritual 20x mid-chain → `ai/ev_player.py:428` → change to 5.0
6. Affinity 82% → add artifact hate to sideboards
7. Ephemerate no target validation → `game_state.py`
8. Evasion/lifelink not subtracted in removed state → `ai/ev_evaluator.py:491-492`

---

## 8. Never do / always do

### Never do
- Read meta shares from JSON — always from METAGAME_SHARES in `decks/modern_meta.py`
- Edit metagame_14deck.jsx manually — always `python build_dashboard.py --merge`
- Force-push to GitHub
- Mix data sources — every figure traces to one function + one data file
- Use heuristic SB tips — only game log data
- Replace deck variant without being told — run alongside existing
- Skip `git pull origin main`

### Always do
- `git pull origin main` before any work
- Merge ModernAtomic_part*.json before first sim run
- Confirm metrics at each stage before proceeding

### Post-action verification
```bash
# After dashboard rebuild
python3 -c "
import re
with open('metagame_14deck.jsx') as f: c=f.read()
n = re.search(r'const N = (\d+)', c)
d = re.findall(r'\"decks\":\[(.+?)\]', c)
print(f'N={n.group(1) if n else \"MISSING\"}, decks={len(d[0].split(\",\")) if d else \"MISSING\"}')"

# After deck import
python3 -c "
from decks.modern_meta import MODERN_DECKS, METAGAME_SHARES
print(f'Decks: {len(MODERN_DECKS)}, Shares: {len(METAGAME_SHARES)}')
assert len(MODERN_DECKS) == len(METAGAME_SHARES), 'MISMATCH'"

# Smoke test new/edited deck (both orderings — should sum to ~100%)
python run_meta.py --matchup NEW_DECK dimir -n 10
python run_meta.py --matchup dimir NEW_DECK -n 10
```

---

## 9. Infrastructure proposals (from Legacy cross-pollination)

Six concrete improvements from MTGSimClaude, scoped to infrastructure only — no changes to EV engine.

### 9a. Plugin deck architecture (~2h)
Drop-file deck registration. No more editing 3 files to add a deck.
```python
# decks/boros_energy.py — single source of truth per deck
DECK_META = {
    'name': 'Boros Energy', 'key': 'boros_energy', 'archetype': 'aggro',
    'meta_share': 0.12,
    'decklist': { ... },        # mainboard + sideboard
    'gameplan': { ... },         # goal sequence (currently gameplans/*.json)
    'strategy_weights': { ... }, # currently strategy_profile.py
    'sideboard_plan': { ... },   # currently sideboard_manager.py
}

# deck_registry.py — auto-discovers on import (~60 lines)
import importlib, pathlib
DECKS = {}
for path in pathlib.Path('decks').glob('*.py'):
    if path.name.startswith('_'): continue
    mod = importlib.import_module(f'decks.{path.stem}')
    if hasattr(mod, 'DECK_META'):
        DECKS[mod.DECK_META['key']] = mod.DECK_META
```
Migration: one-time extraction from modern_meta.py + gameplans/ + strategy_profile.py.

### 9b. Template-driven dashboard (~3h)
Separate data from presentation. Design lives in template, never regenerated.
```
templates/reference_modern_matrix.html  ← design lives here
build_dashboard.py                      ← swaps D,DA,C,I,ARCH constants only
```
Eliminates "dashboard looks different after rebuild" bugs permanently.

### 9c. Parallel processing (~1h + profiling)
```python
from multiprocessing import Pool
def run_matrix_parallel(decks, n_games=50, workers=4):
    pairs = [(d1,d2) for d1 in decks for d2 in decks if d1!=d2]
    with Pool(workers) as pool:
        results = pool.map(partial(_run_pair, n_games=n_games), pairs)
    return {(d1,d2): wr for d1,d2,wr in results}
```
Constraint: CardDatabase (400MB) must load per-worker. Est. 95min → ~32min (3×).

### 9d. Meta audit with expected ranges (~2h)
```python
EXPECTED_RANGES = {
    'boros_energy': (0.50, 0.65), 'affinity': (0.45, 0.60),  # NOT 82%
    'ruby_storm': (0.45, 0.58),  '4c_omnath': (0.48, 0.62),  # NOT 29%
}
def audit_matrix(results):
    return [(d,actual,lo,hi) for d,(lo,hi) in EXPECTED_RANGES.items()
            if not lo <= results[d]['flat_wr'] <= hi]
```
Would have caught Affinity (82%), 4c Omnath (29%), Storm (37%) immediately.

### 9e. Symmetry measurement (~30min)
```python
def check_symmetry(results):
    return [(d1,d2,wr,results[(d2,d1)]) for (d1,d2),wr in results.items()
            if (d2,d1) in results and abs(wr + results[(d2,d1)] - 1.0) > 0.10]
```
Run both orderings. Flag |d1+d2−100| > 10% as engine fairness bug.

### 9f. Provenance footer (~20min)
Every output gets: `Simulated: DATE | Decks: N | Games/pair: N | Seeds: range | Engine: vX`

### What NOT to adopt from Legacy
| Legacy approach | Modern already does it better |
|----------------|-------------------------------|
| Hardcoded strategy functions (19×) | EV scoring — one engine for all decks |
| Manual card builders (119K cards.py) | MTGJSON auto-load (21,795 cards) |
| G1-only matrix | Full Bo3 with bool-flag SB |
| 4-level threat classification | Continuous EV scoring |
| Tag-based card identity (73 tags) | Oracle text + card_effects.py |

---

## 10. Recommended next work (unified backlog)

### AI fixes (P0/P1)
| # | Task | Impact | Effort | Location |
|---|------|--------|--------|----------|
| 1 | PW EV scoring | P0 fix | LOW | `ev_player.py:_score_spell()` |
| 2 | Hard-gate Wrath empty board | P0 fix | LOW | `ev_evaluator.py:272` |
| 3 | Fix Ocelot Pride + Wrath X=0 | P0 fix | LOW | Card DB + engine |
| 4 | Storm penalty 20→5 | P1 fix | LOW | `ev_player.py:428` |
| 5 | card_ev_overrides in gameplans | Architecture | MED | gameplan JSON |
| 6 | Combo chain EV bypass | Architecture | MED | ev_player.py |

### Infrastructure (from Legacy proposal)
| # | Task | Impact | Effort | Deps |
|---|------|--------|--------|------|
| 7 | Meta audit + expected ranges | HIGH | 2h | None |
| 8 | Symmetry measurement | HIGH | 30m | None |
| 9 | Plugin deck architecture | HIGH | 2h | None |
| 10 | Template dashboard | HIGH | 3h | None |
| 11 | Parallel processing | MED | 1h+ | Memory audit |
| 12 | Provenance footer | LOW | 20m | Template (#10) |

### Validation
| # | Task | Impact | Effort |
|---|------|--------|--------|
| 13 | Spot-check 10 matchups vs consensus | MED | LOW |
| 14 | Investigate Amulet -18% regression | MED | MED |
| 15 | Artifact hate in sideboards | P1 fix | MED |
| 16 | Re-run full matrix after fixes | Production | HIGH |

**Total infrastructure effort: ~9 hours.** No changes to AI engine.

---

## 11. Codebase stats

27,358 Python LOC · 66 files · 14 AI modules (7,757 ln) · 21,795 cards · 15 decks · 15 gameplans · 7 test suites · 4 Claude skills · 0 external deps

---

*See also: ARCHITECTURE.md · QUICKSTART.md · LLM_JUDGE_STRATEGY_AUDIT.md · LEGACY_MODERNISATION_PROPOSAL.md*
