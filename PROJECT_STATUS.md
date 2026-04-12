# MTGSimManu — Project Status & Planning Reference

> **Last updated:** 2026-04-12 (session 2)
> **Purpose:** Single-source-of-truth for Claude Code planning mode. Read this before any session.
> **Sister project:** MTGSimClaude (Legacy format, 38 decks, see LEGACY_MODERNISATION_PROPOSAL.md)

---

## 1. What this project is

A **Modern-format Magic: The Gathering game simulator** with EV-based AI decision-making. Pure Python 3.11, zero external dependencies. Simulates full Bo3 matches between 16 competitive Modern decks, produces interactive dashboards, deck guides, and replay viewers.

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
│  build_dashboard.py → metagame_data.jsx → HTML heatmap    │
│  build_replay.py → Bo3 HTML replayer (light theme)          │
│  commentary_engine.py → strategic annotations               │
├─────────────────────────────────────────────────────────────┤
│  SIMULATION RUNNER                                          │
│  run_meta.py (CLI + Python API)                             │
│  --matrix --matchup --bo3 --field --audit --verbose --trace │
│  import_deck.py  match_trace.py  build_replay.py            │
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
│  decks/modern_meta.py (16 decks + METAGAME_SHARES)          │
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

### Key fix (session 2): token removal bug
The `removed` EVSnapshot was subtracting ETB token power alongside the removed creature — tokens persist on battlefield after their parent is removed. Fixing this moved Orcish Bowmasters from -32.7 → +14.7 EV.

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

**Overall grade: C** (up from C-, up from D+) — session 2 fixes

| Domain | Grade | | Domain | Grade |
|--------|-------|-|--------|-------|
| Rules & engine | B+ | | Mana & sequencing | C+ |
| Combat & threats | B | | Combo & storm | C+ |
| Mulligan & openers | C+ | | Control & interaction | C- |

### WR improvements from session 2 fixes
| Matchup | Before | After | Root cause fixed |
|---------|--------|-------|-----------------|
| Affinity vs Izzet Prowess | 97% | ~60-85% | DRC PROWESS misclassification → surveil/delirium |
| 4c Omnath vs Boros | 10% | 30% | Wrong decklist + Risen Reef ETB |
| Dimir vs Boros | broken | 50% | Token removed-state bug |
| Amulet Titan vs Boros | 15% | ~23% | Amulet/Spelunking/bounce land ETBs |

---

## 7. Known bugs

### P0 — FIXED (session 2)
| # | Bug | Fix | Commit |
|---|-----|-----|--------|
| 1 | Wrath of Skies uses stored energy not cast X | Use `item.x_value` | `ba15c11` |
| 2 | Ocelot Pride energy on ETB (wrong trigger + oracle) | Noncreature cast trigger; combat damage Cat token | `ba15c11` |
| 3 | DRC misclassified as PROWESS → surveil/delirium never fires | Fix oracle detection; implement surveil GY bin | `eec7ec8` |
| 4 | EE double ETB (X-counter + sunburst both fire) | Gate X-counter path to cards without dedicated ETB handlers | `1c38354` |

### P1 — FIXED (session 2)
| # | Bug | Fix | Commit |
|---|-----|-----|--------|
| 5 | Token power wrongly subtracted in removed state | Tokens persist when parent removed | `9aff147` |
| 6 | Ragavan never attacks (no trigger bonus) | +1.5 EV combat trigger bonus | `704a671` |
| 7 | Storm tutor 20x mid-chain penalty | 20x → 5x | `704a671` |
| 8 | Holdback only fires when opp_power > 0 | Also fires vs creatureless spell decks | `704a671` |
| 9 | Sanctifier double "Resolve" log | Gate log to SPELL items only | `53d372a` |
| 10 | Ephemerate castable with no friendly creatures | `can_cast` blink tag check | `53d372a` |
| 11 | Duplicate Chalice no penalty | -8.0 EV if same name already on battlefield | `53d372a` |
| 12 | `_resolve_sac_effect` crash (undefined variables) | Fixed scoping | `53d372a` |
| 13 | Ephemerate rebound fires without valid target | Gate rebound on `player.creatures` check | `3d1d8a1` |

### P2 — FIXED (session 2)
| # | Bug | Fix | Commit |
|---|-----|-----|--------|
| 13 | CMC 2 removal scaling 0.6 too high | 0.6 → 0.4 | `9aff147` |
| 14 | Evasion creatures over-penalised | 50% damage-removal discount for conditional flyers | `9aff147` |
| 15 | Dovin's Veto positive EV vs aggro | Cap EV vs creature-heavy low-hand boards | `9aff147` |
| 16 | Tron no assembly bonus | +3/+8/+20 per piece via Urza's subtype | `705ea0b` |
| 17 | `rmv=` trace display not matching main path | Detailed path now mirrors main path scaling | `9aff147` |

### Hardcoding removed (session 2)
| Was hardcoded | Now uses |
|---|---|
| `permanent.name == 'Ocelot Pride'` (2 places) | Oracle: `'{e}' in oracle` + `'noncreature spell'` / `'combat damage'` pattern |
| `tron_lands = {'Urza\'s Tower', ...}` | `"Urza's" in land.template.subtypes` |
| `DELIRIUM_CREATURES = {"Dragon's Rage Channeler"}` | `template.power_scales_with == "delirium"` (oracle-derived at load) |
| `TARMOGOYF_CREATURES`, `DOMAIN_POWER_CREATURES`, `GRAVEYARD_SCALING_CREATURES` | `template.power_scales_with` field |
| `if name == "Construct Token"` | `'artifact you control' in oracle` |
| `c.template.name == "Amulet of Vigor"` (2 places) | `_apply_untap_on_enter_triggers()` — oracle pattern |

### Generic engine patterns added (session 2)
| Pattern | Trigger | Effect |
|---------|---------|--------|
| `"whenever a permanent you control enters tapped, untap it"` | `_apply_untap_on_enter_triggers()` | Covers Amulet of Vigor and any future card |
| `"lands you control enter the battlefield untapped"` | `_apply_lands_enter_untapped()` | Covers Spelunking static |
| `"when this land enters, return a land you control to hand"` | `resolve_etb_from_oracle()` | Covers Gruul Turf, Simic Growth Chamber, all bounce lands |
| `"when this [enters], draw a card, then you may put a land from hand onto battlefield"` | `resolve_etb_from_oracle()` | Covers Spelunking ETB |
| `"whenever this creature or another [Subtype] you control enters"` + top-card effect | `trigger_etb()` | Covers Risen Reef, any future Elemental-chain card |
| `"whenever you cast a noncreature spell, you get {E}"` | `resolve_spell_cast_trigger()` | Energy on noncreature spell cast |
| `"if you have more energy than that player has life, create a 1/1 token"` | `_assign_combat_damage()` | Combat damage energy→token trigger |

### Remaining open bugs

#### P1
| # | Bug | Location |
|---|-----|----------|
| 1 | Amulet Titan WR still low (~23% vs expected ~45%) — Arboreal Grazer not prioritising bounce lands; AI doesn't model Amulet mana loop value | `ai/ev_player.py`, `_score_land()` |
| 2 | Living End ~12% vs Boros — AI doesn't attack aggressively after Living End resolves; Force of Negation not held for protection | `ai/ev_player.py`, `ai/response.py` |
| 3 | Psychic Frog early EV still negative when Orcish Bowmasters is better option (correct priority, but EV magnitude off) | `ai/ev_evaluator.py` |

#### P2
| # | Bug | Location |
|---|-----|----------|
| 4 | Amulet of Vigor multiple copies don't stack (only 1 untap applied per land ETB) | `engine/game_state.py:_apply_untap_on_enter_triggers()` |
| 5 | Spelunking "Lands you control enter untapped" not applied to normal `play_land` path consistently | `engine/game_state.py` |
| 6 | Elesh Norn trigger doubling not implemented | `engine/game_state.py` |
| 7 | Phelia blink-on-attack not fully implemented | `engine/card_effects.py` |

---

## 8. Deck status

| Deck | Sim grade | Notes |
|------|-----------|-------|
| Boros Energy | ✅ Working | T1 deck, ~64% weighted WR |
| Affinity | ✅ Working | Construct tokens, Cranial Plating, Urza's Saga all correct |
| Izzet Prowess | ✅ Working | DRC surveil/delirium fixed; 60% vs Affinity realistic |
| Dimir Midrange | ✅ Working | 50% vs Boros realistic |
| Eldrazi Tron | ✅ Working | 60% vs Boros; Tron assembly bonus added |
| Domain Zoo | ✅ Working | 40% vs Boros realistic |
| Ruby Storm | ✅ Working | 40% vs Dimir realistic after tutor fix |
| Jeskai Blink | ⚠️ Underperforms | 27% vs Boros (n=60); deck list corrected (Witch Enchanter, Fable x3); Ephemerate rebound fizzle fixed; gap is structural — no Fury to reset boards |
| 4c Omnath | ⚠️ Underperforms | 30% vs Boros; deck list now correct; Elesh Norn/Phelia not implemented |
| Amulet Titan | ⚠️ Underperforms | 23% vs Boros; expected ~45%; mana loop value not modelled |
| Goryo's Vengeance | ⚠️ Reasonable | 25% vs Dimir; cascade combo fires correctly |
| Living End | ⚠️ Underperforms | 12% vs Boros; cascade/cycling correct; post-combo attack AI weak |
| Azorius Control | ⚠️ Deflated | Isochron Scepter not implemented |
| Azorius Control (WST) | ✅ Field run | Wrath X now correct; separate from Isochron variant |

---

## 9. Never do / always do

### Never do
- Read meta shares from JSON — always from METAGAME_SHARES in `decks/modern_meta.py`
- Edit metagame_data.jsx manually — always `python build_dashboard.py --merge`
- Force-push to GitHub
- Mix data sources — every figure traces to one function + one data file
- Use heuristic SB tips — only game log data
- Replace deck variant without being told — run alongside existing
- Skip `git pull origin main`
- Hardcode card names in engine — always detect from oracle text or template field

### Always do
- `git pull origin main` before any work
- Merge ModernAtomic_part*.json before first sim run
- Confirm metrics at each stage before proceeding
- Use `_apply_untap_on_enter_triggers()` when putting lands onto battlefield
- Call `resolve_etb_from_oracle()` for lands placed by non-standard paths

### Post-action verification
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

# Smoke test new/edited deck (both orderings — should sum to ~100%)
python run_meta.py --matchup NEW_DECK dimir -n 10
python run_meta.py --matchup dimir NEW_DECK -n 10

# Rules audit: check for double ETB, wrong triggers, incorrect P/T
python run_meta.py --verbose DECK OPPONENT -s 50000 | grep -E "Resolve.*Resolve|damage.*dies|X=0.*dies"
```

---

## 10. Deck guide minimum spec

Guides must match the Legacy Burn guide (`guide_burn.html`) feature-for-feature. Reference: `/mtg-deck-guide` skill.

| # | Section | Data source | Interactive? |
|---|---------|-------------|-------------|
| 1 | Hero 4-col grid | Matrix: flat WR, weighted WR, rank, best/worst | — |
| 2 | Mainboard with role badges + Scryfall hovers | `modern_meta.py` decklist + card tags | Hover → card image popup |
| 3 | Sideboard with "vs" targets | `sideboard_manager.py` bool flags | — |
| 4 | Deck construction findings (±pp) | Matrix: compare hand archetypes | — |
| 5 | Game plan (3 phases with timeline) | `gameplans/*.json` goal sequences | — |
| 6 | Kill turn distribution chart | Matrix: `turn_dist` from matchup data | Bar chart |
| 7 | Hand archetype WR bars + baseline | 2,000 games hand analysis (run_matchup loop) | Baseline marker |
| 8 | Real sim hands (2 keep + 1 mull) | `run_verbose_game()` with specific seeds | Turn-by-turn |
| 9 | Metagame strategy | Matrix: archetype WRs + triptych (prey/competitive/danger) | — |
| 10 | Matchup spread tiered T1/T2/Field | Matrix WRs + `METAGAME_SHARES` + `DECK_ARCHETYPES` | Bars with type+meta% |
| 11 | Provenance footer | Sim params: date, N, seeds, engine version, attribution | — |

### Scryfall hover implementation
```html
<span class="card-tip" data-card="Ragavan, Nimble Pilferer">Ragavan</span>
```
```javascript
// JS: mouseover → fetch api.scryfall.com/cards/named?fuzzy=NAME&format=image&version=normal
// Display in fixed popup div (244×340px, border-radius:8px, box-shadow)
```

### Game plan derivation
Game plans come from `decks/gameplans/*.json` goal sequences, NOT from manual writing. Each goal has `enablers`, `interaction`, and `payoffs` arrays. The 3-phase timeline maps to goals 1-2-3 in the JSON. Card names in the guide must match the gameplan entries.

### Hand analysis pipeline (for full guide)
```python
# Run 2,000 games across all opponents, weighted by meta share
for _ in range(2000):
    opp = random.choices(opponents, weights=meta_shares)[0]
    result = run_matchup(deck, opp, n_games=1, seed_start=next_seed)
    # Record: hand composition (lands/creatures/spells), won/lost, kill turn
# Group by formula (e.g. "2L-1C-4S"), calculate WR per group vs baseline
```

---

## 11. Infrastructure proposals (from Legacy cross-pollination)

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
```

### 9b. Template-driven dashboard (~3h)
Separate data from presentation. Design lives in template, never regenerated.

### 9c. Parallel processing (~1h + profiling)
Est. 95min → ~32min (3×). CardDatabase (400MB) must load per-worker.

### 9d. Meta audit with expected ranges (~2h)
```python
EXPECTED_RANGES = {
    'boros_energy': (0.50, 0.65), 'affinity': (0.45, 0.60),
    'ruby_storm': (0.40, 0.55),  '4c_omnath': (0.40, 0.55),
    'izzet_prowess': (0.40, 0.55), 'amulet_titan': (0.40, 0.50),
}
```

### 9e. Symmetry measurement (~30min)
Run both orderings. Flag |d1+d2−100| > 10% as engine fairness bug.

### 9f. Provenance footer (~20min)
Every output gets: `Simulated: DATE | Decks: N | Games/pair: N | Seeds: range | Engine: vX`

---

## 12. Recommended next work (unified backlog)

### Engine fixes (remaining P1/P2)
| # | Task | Impact | Effort | Location |
|---|------|--------|--------|----------|
| 1 | Amulet Titan mana loop value in `_score_land` | P1 fix | MED | `ai/ev_player.py` |
| 2 | Living End post-combo attack aggression | P1 fix | MED | `ai/ev_player.py` |
| 3 | Elesh Norn trigger doubling | P1 fix | HIGH | `engine/game_state.py` |
| 4 | Phelia blink-on-attack | P2 fix | LOW | `engine/card_effects.py` |
| 5 | Multiple Amulet copies stack correctly | P2 fix | LOW | `_apply_untap_on_enter_triggers()` |
| 6 | Jeskai Blink WR gap (~27% vs expected ~45%) — no Fury in list; Galvanic Discharge/Wrath are self-fueling energy (correct); AI sequencing suboptimal | P2 | LOW | AI strategy |

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
| 13 | Full matrix re-run after session 2 fixes | HIGH | 95 min |
| 14 | Spot-check 10 matchups vs consensus WR | MED | LOW |
| 15 | Artifact hate in sideboards (Affinity 85% still high) | P1 fix | MED |

---

## 13. Codebase stats

~28,500 Python LOC · 66 files · 14 AI modules · 21,795 cards · 16 decks · 16 gameplans · 149 passing tests · 4 Claude skills · 0 external deps

---

*See also: docs/ARCHITECTURE.md · QUICKSTART.md · docs/history/audits/2026-04-11_LLM_judge.md · LEGACY_MODERNISATION_PROPOSAL.md*
