# MTGSimManu — Project Status & Planning Reference

> **Last updated:** 2026-04-13 (session 3 — full re-run post blocking+attack+mulligan fixes)
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

**Overall grade: B** (iteration 5 — broken-deck rehab: ritual mana-production oracle classifier, post-combo goal advance for mass-reanimate via _pending_goal_advance signal, control_patience StrategyProfile field + reactive_only JSON populated for the three CONTROL gameplans. Living End WR 3% → **40%** in audit, Azorius Control 15% → 23%, Storm 25-30% → 30%. Healthy decks within ±10pp variance.)

### Iteration 5 fixes (ITERATION_5_PLAN.md — 2026-04-13)
| Fix | Files | Status | Audit/WR signal |
|-----|-------|--------|-----------------|
| 1. Ritual oracle mana-detection in _has_immediate_effect | `ai/ev_evaluator.py` | ✅ landed (`bc51028`) | All Storm rituals already covered by 'ritual' tag — change is belt-and-suspenders. Storm WR essentially unchanged. |
| 2. Post-combo goal advance for mass-reanimate | `engine/game_state.py`, `ai/ev_player.py` | ✅ landed (`41f2e16`) | **Living End vs Tron 0% → 20%; field WR 3% → 40%** |
| 3. control_patience gate + populate Azorius reactive_only | `ai/strategy_profile.py`, `ai/ev_player.py`, 3× `decks/gameplans/*.json` | ✅ landed (`bc602db`) | **Azorius WR 15% → 23%** (n=30); Counterspell delta +0.52, Teferi cast more often |

**Living End audit (n=30):**
- Win rate: 3% → **40%** (12 wins / 30 games)
- Win conditions: damage:11 + timeout:1 (was damage:4)
- Root cause was dual: (a) cascade-detection oracle pattern was too strict and never matched Living End's actual oracle, so `_resolve_living_end` was dead code; (b) even when creatures returned, GoalEngine kept running cascade-deck enabling goals.

**Storm audit (n=60):**
- Win rate: 25-30% → 30% (18/60); damage:17 + timeout:1
- Grapeshot delta +0.94, Wish delta +0.78 — finishers correctly correlate with wins
- Below 35-40% target; remaining gap is in storm_patience / _combo_modifier tuning (out of iteration-5 scope per plan)

**Matrix n=10 power rankings (post-iter5):**
- Living End: 6%/4% → **27%/23%** (huge)
- Azorius Control (WST): 33%/29% → 33%/35%
- Living End move from worst-deck slot

Two persistent smoke failures (#1/#2 GD on Signal Pest, #6 Thraben Charm T4, #7 Cat Token) are unrelated to iteration-5 work and documented for future sessions.

Session 5b changes (on top of session 5):
  * engine/card_database.py: removal tag extended to artifact/enchantment/all_nonland target_types; lands filtered out (Channel lands like Boseiju stay untagged)
  * ai/ev_player.py::_score_spell: artifact/enchantment-hate overlay using _permanent_threat_value × 0.5 for spells whose oracle contains target artifact/enchantment/nonland permanent/noncreature

### Session 5 fixes (AFFINITY_MATCHUP_PLAN.md — 2026-04-13)
| Fix | File | Status | Audit signal |
|-----|------|--------|--------------|
| 1. Scaling equipment threat value | `ai/ev_player.py::_permanent_threat_value` | ✅ regex `\+N/+N for each <perm>` with count-driven scoring | CP delta: **−X → +0.68** |
| 2. EE reactive-only | `decks/gameplans/affinity.json::reactive_only` | ✅ | EE delta: **−0.54 → −0.28** (still below >0 target) |
| 3. Evasion-weighted CP equip | `ai/ev_player.py::_consider_equip` | ✅ flying×2.0, menace×1.5, trample×1.3 | CP lands on Ornithopter when available |

**Affinity audit n=60 (before → after):**
- Win rate: 92% → **78%** (14pp drop)
- Cranial Plating: 74% → **91%** when cast, delta +X → **+0.68**
- Engineered Explosives: 64% → 50% when cast, delta -0.54 → **-0.28**
- Signal Pest: 87% → 70% when cast, delta -0.30 → -0.44 (worse — opp removal now correctly targets it; not a regression in isolation)

Target outcomes from plan (Affinity vs Boros ~65-70%, vs Zoo ~60-70%): partial. Current matchup WRs are Boros 17% (Affinity 83%), Zoo 10% (Affinity 90%). The residual gap points to further structural opponent-side work (documented in plan §"Target outcomes").

**Overall grade (old): C+** (session 4-v2 — urgency_factor refined)

### Session 4 fixes (AI_STRATEGY_IMPROVEMENT_PLAN.md v1 + AI_IMPROVEMENT_PLAN_V2.md refinements — 2026-04-13)
| Task | Commit | Status | Smoke signal |
|------|--------|--------|--------------|
| 1. Oracle-driven threat value for removal | `4647626` | ✅ landed (v2 tweak power bonus to 0.8×(p-3)) | GD on Signal Pest T2 (G2 s60100): FAIL→PASS |
| 2. Kill-clock urgency discount on slow permanents | `12e9f25` | ✅ landed (v2 formula (opp_clock-1)/4.0, no floor) | Bombardment held vs fast clock: FAIL→PASS |
| 4. Draw-step prevention bonus in attack planner | `1a45dcf` | ✅ landed | Storm WR no-regression: PASS |
| 3. Fetch-shock life-cost staggering | `b9f5dc9` | ✅ landed | ≤4 life paid T1+T2: PASS |

Golden smoke (`tools/golden_smoke.py`): 8/10 pass.

**v2 audit (energy n=60):**
| Metric | Baseline (plan doc) | Current | v2 target |
|--------|--------------------|---------| --------- |
| Boros WR | — | **62%** (37/60) | — |
| Phlage win% when cast | 84% | **84%** | maintain |
| Bombardment win% when cast | 42% | 37% | >55% ❌ partial |
| Bombardment cast rate | — | 0.3x/game | reduced ✓ |
| Thraben Charm avg cast turn | — | T5.9 | <T4.5 ❌ |
| GD win% when cast | — | 57% | ✓ |

Bombardment is cast less often (urgency correctly discounts it) but the times it DOES fire are in slower games where it helps less. Thraben Charm cast timing requires multi-turn lookahead — explicitly out of scope per plan v2 §"What this does NOT fix".

Two remaining smoke failures are accepted trade-offs — Thraben Charm T4 (multi-turn lookahead) and Cat Token attack (shields-down correctly weighs blocker retention).

**Overall grade (old): C** (blocking P0 fixed, mulligan floor added, attack logic improved; post-fix 16×16 matrix at N=50 validates)

| Domain | Grade | | Domain | Grade |
|--------|-------|-|--------|-------|
| Rules & engine | B+ | | Mana & sequencing | C+ |
| Combat & threats | B+ | | Combo & storm | C |
| Mulligan & openers | B- | | Control & interaction | C |

### WR shifts from session 3 full re-run (2026-04-13)
| Deck | Pre-fix | Post-fix | Delta | Notes |
|------|---------|----------|-------|-------|
| 4c Omnath | 17% | 58% | +40pp | Major midrange improvement |
| Goryo's Vengeance | 2% | 30% | +28pp | Combo now fires |
| Azorius Control (WST) | 19% | 37% | +18pp | Control viable |
| 4/5c Control | 22% | 34% | +11pp | Control rises |
| Jeskai Blink | 53% | 58% | +5pp | Midrange stable |
| Dimir Midrange | 62% | 65% | +3pp | Midrange stable |
| Eldrazi Tron | 67% | 72% | +4pp | Ramp stable |
| Affinity | 91% | 93% | +2pp | ⚠ Still outlier — blocking not enough |
| Boros Energy | 88% | 67% | -21pp | Was over-performing, now realistic |
| Ruby Storm | 51% | 30% | -21pp | ⚠ Regression — needs investigation |
| Izzet Prowess | 75% | 55% | -20pp | Was over-performing |
| Living End | 45% | 5% | -40pp | ⚠ BROKEN — cascade/attack AI regressed |

---

## 7. Known bugs

### P0 — FIXED (session 3, 2026-04-13)

| # | Issue | Fix | Commit |
|---|-------|-----|--------|
| 9 | **Zero blocks across all games** | Rewrite `_eval_block` with direct damage/value scoring | `8149d0c` |
| 10 | **Not attacking with profitable boards** | Empty-board and combat trigger attack logic; verified 0 non-trivial refusals in Bo3 spot-check | Prior sessions |
| 11 | **0-land mulligan keep** | Mulligan guardrail + combo-mulligan activation | `11e8a57`, `e1d9361` |

### P0 — OPEN

| # | Issue | Location | Evidence | Impact |
|---|-------|----------|----------|--------|
| 12 | **Affinity 93% WR** | `ai/ev_player.py`, `engine/card_effects.py` | Still dominating post-blocking-fix. Construct tokens + Cranial Plating overwhelm all opponents. Blocking alone insufficient. | All matchup data vs Affinity suspect. |
| 13 | **Living End 5% WR (was 45%)** | `ai/ev_player.py`, cascade/attack AI | 0% vs Boros/Jeskai/E-Tron/Prowess/Dimir. Cascade fires but post-combo attack AI broken or creatures insufficient. | Living End essentially non-functional. |

**Priority fix order:** #9 (blocking) first — affects every matchup. Then #10 (attack threshold). #11 is deck-specific.

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

| Deck | Flat WR | Wtd WR | Sim grade | Notes |
|------|---------|--------|-----------|-------|
| Affinity | 93% | 91% | ⚠️ Inflated | P0: dominates all matchups. Blocking fix insufficient. |
| Eldrazi Tron | 72% | 57% | ✅ Working | Stable; Tron assembly bonus working |
| Boros Energy | 67% | 61% | ✅ Working | Down from 88%, now realistic T1 |
| Pinnacle Affinity | 66% | 61% | ✅ Working | Reasonable T2 performance |
| Domain Zoo | 65% | 59% | ✅ Working | Slightly above expected ceiling |
| Dimir Midrange | 65% | 55% | ✅ Working | Midrange performing well |
| Jeskai Blink | 58% | 47% | ✅ Working | Up from 53%; solid midrange |
| 4c Omnath | 58% | 44% | ✅ Working | Massive improvement from 17%; Risen Reef/landfall chain working |
| Izzet Prowess | 55% | 48% | ✅ Working | Down from 75%; realistic T2 |
| Amulet Titan | 49% | 39% | ⚠️ Underperforms | Expected ~45% weighted; mana loop value still not modelled |
| Azorius Control (WST) | 37% | 31% | ⚠️ Underperforms | Up from 19%; still weak vs aggro |
| 4/5c Control | 34% | 23% | ⚠️ Underperforms | Up from 22%; still below expected |
| Ruby Storm | 30% | 22% | ⚠️ Regressed | Down from 51%; needs investigation |
| Goryo's Vengeance | 30% | 22% | ✅ Working | Up from 2%; combo fires now |
| Azorius Control | 18% | 12% | ⚠️ Deflated | Isochron Scepter not implemented |
| Living End | 5% | 3% | ❌ Broken | P0: down from 45%, cascade fires but post-combo AI non-functional |

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

### Session 3 changelog (branch `claude/complete-unfinished-tasks-50La8`)
All items below were landed or verified-already-live on the session-3 branch.
Groups A/B/C commits: `2a4e3a7`, `9d5a7a7`, `72c1be9`.

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Amulet + bounce-land mana loop in `_score_land` | landed | `2a4e3a7` |
| 2 | Living End post-combo aggression flag | landed | `2a4e3a7` |
| 3 | Elesh Norn / Panharmonicon trigger doubling | landed | `2a4e3a7` |
| 4 | Phelia blink-on-attack handler (ETB value; attack-decl partial) | landed | `2a4e3a7` |
| 5 | Multi-copy Amulet untap loop | landed | `2a4e3a7` |
| 6 | Jeskai Ephemerate Main1-hold sequencing | landed | `2a4e3a7` |
| — | Ephemerate AI-side target gate (audit P1) | landed | `2a4e3a7` |
| — | Psychic Frog / low-CMC ETB creature EV floor (§7 P1 #3) | landed | `2a4e3a7` |
| — | Spelunking `_apply_lands_enter_untapped` on fetchland crack (§7 P2 #5) | landed | `2a4e3a7` |
| — | Phase-labelled EV traces + ghost-candidate filter (audit P2) | landed | `2a4e3a7` |
| — | LE mulligan relax-at-6 (audit P2) — already live | verified | — |
| — | Tron assembly bonus (audit P2) — already live `ai/ev_player.py:657-676` | verified | — |
| 7 | meta_audit.py + EXPECTED_RANGES + post-matrix outlier flagging | landed | `9d5a7a7` |
| 8 | Symmetry check in run_meta_matrix | landed | `9d5a7a7` |
| 11 | `--workers` CLI flag for matrix parallelism | landed | `9d5a7a7` |
| 12 | Provenance footers in dashboard + guide builders | landed | `9d5a7a7` |
| — | `--sigma DECK1 DECK2 --repeats N` sampler (fills §5 σ-at-n=50 TODO) | landed | `9d5a7a7` |
| 9 | Plugin deck architecture | **deferred** — stub in MODERN_PROPOSAL.md §10.1 | `72c1be9` |
| 10 | Template dashboard | **deferred** — stub in MODERN_PROPOSAL.md §10.2 | `72c1be9` |
| 15 | Artifact hate in sideboards (Affinity 85%) | **investigation-only** — replay committed for next session | `72c1be9` |

### Still open after session 3
- Wish tutor Grapeshot-vs-Warrens balance (audit P2). Attempted shift toward
  Warrens regressed Storm at current sample sizes; original 0.6 threshold
  restored. Needs a proper EV-weighted decision, not a threshold tweak.

### Session 3 validation (2026-04-12)
Full 16×16 matrix, `n=100` Bo3 matches per pair, 14 workers, commit `72c1be9`.
`meta_audit` flagged 11 outliers — the format remains poorly balanced but
several deck-specific improvements are measurable:

| Deck | Expected | Pre-session 3 | Post-session 3 |
|------|----------|---------------|----------------|
| Affinity | 45-65% | ~85% | **88.9% (severe)** — item 15 still unresolved |
| Azorius Control | 30-50% | — | **7.9% (severe)** — new outlier, needs Isochron Scepter |
| Eldrazi Tron | 48-62% | — | 73.1% (moderate) |
| Dimir Midrange | 45-58% | ~50% | 67.9% (moderate) |
| Amulet Titan | 30-50% | 23% | 23.8% (minor) — A1 fix too small to close the gap |
| 4c Omnath | 30-52% | 29% | 57.0% (minor, now *above* range — unexpected!) |
| Boros Energy | 55-70% | ~64% | 73.7% (minor) |
| Jeskai Blink | 35-55% | 27% | 62.3% (moderate, now *above* range) |
| Living End | 20-45% | 12% | 36.1% — A2 aggression flag appears to land |

Takeaways:
- Living End, 4c Omnath, Jeskai — aggression + ETB + sequencing fixes landed
  (Living End doubled its WR; Jeskai moved from 27% → 62%).
- Amulet Titan barely moved — A1 mana-loop bonus may need to be larger or
  needs to model Titan's cast turn specifically (not just land value).
- **Affinity still severe:** item 15 remains the top priority for the next
  session; the committed replay in `replays/boros_vs_affinity_s55555.txt`
  is the starting point.
- **New regression:** Azorius Control dropped to 7.9% — needs Isochron Scepter
  implementation (flagged in §8).

### LLM judge re-grading
The 2026-04-11 LLM judge panel is a static document. We don't have a scripted
hook to re-run it. `meta_audit.py` provides the automated outlier-flag
substitute; a real LLM re-grade would need external infra (not in this repo).

### Session 3 phase 2 (2026-04-12, same-day)
Parallel-work push: Affinity root-cause P0, Isochron Scepter, Amulet depth,
proper Wish finisher comparison. Commits `823958f`, `<wish-fix>`.

**P0 found in `engine/cards.py:359`** — the `'artifact you control' in oracle`
creature-scaling check was matching Affinity reminder text, overwriting every
Affinity creature's P/T with `artifact_count`. Frogmite was 11/11 instead of
2/2 on a 10-artifact board. Tightened the regex to `\+N/\+N for each artifact
you control` and switched to additive (`base + artifact_count` not `=`).

Matrix-v2 (n=100 Bo3, commit `823958f`) deltas:

| Deck | v1 WR | v2 WR | Δ |
|------|-------|-------|---|
| Affinity | 88.9% | 80.2% | −8.7pp (still severe) |
| Boros Energy | 73.7% | 77.9% | +4.2pp (moderate; strengthened by Affinity nerf) |
| Amulet Titan | 23.8% | 24.7% | +0.9pp (A1 loop bonus too small) |
| Azorius Control | 7.9% | 7.3% | −0.6pp (Isochron lock works but Azorius still loses vs aggro) |
| Jeskai Blink | 62.3% | 63.9% | +1.6pp |
| Eldrazi Tron | 73.1% | 75.1% | +2.0pp |
| Pinnacle Affinity | 40.2% | 31.2% | −9.0pp (same cards.py fix; now too weak) |
| 4c Omnath | 57.0% | 59.8% | +2.8pp |

σ sampler (n=50, repeats=5) confirms sampling noise is small (2-4pp across
outliers), so the trends above are real signal.

**Still open — three categories:**
1. Affinity 80.2% — P0 fix brought it down 9pp but deck is still structurally
   too strong. Next step: SB investigation (replay already committed) + check
   Cranial Plating / equipment evaluation in `ai/ev_player.py:1166-1169`.
2. Over-range cluster (Boros 78, Tron 75, Jeskai 64, Dimir 67, Zoo 71) — needs
   a decision: tune-down vs update-ranges. Empirically the sim is self-
   consistent (low σ), so these are true sim realities, not noise.
3. Under-range cluster (Amulet 25, Azorius 7, Pinnacle Affinity 31, WST 33,
   Storm 38) — each needs deck-specific work (Amulet ramp AI, Azorius survival
   against aggro, Pinnacle Affinity was a Frogmite-power-inflation beneficiary
   and is now weak, Storm needs proper combo-EV evaluation).

The Wish tutor improvement (proper Warrens-vs-Grapeshot comparison with token
survival factor) nudged Storm vs Dimir from 0% to 20% at n=10. Modest but
directional.

### Session 3 phase 3 — Affinity SB coverage + audit calibration
- `engine/sideboard_manager.py`: bumped `max_swaps` from 5 to 7 for artifact
  matchups (Affinity/Pinnacle/Tron). 5-card cap left the majority of the
  opponent's 18+ artifacts untouched; 7 pulls Boros closer to a real hate
  loadout. Spot-check Boros vs Affinity at n=20: 50/50 (was 16/84 pre-session-3,
  30/70 post-P0-fix).
- `meta_audit.py`: raised the moderate/minor severity cutoff from 7pp to 10pp.
  σ at n=50 is 2-4pp, so deltas under ~10pp aren't actionable signal. This
  keeps the outlier list short enough to act on each session rather than
  chasing noise.

### Session 3 phase 5 — Azorius Wrath + merge hook
Cross-session collaboration resumed after PR#94/#95 merged and the other
session added threat-based removal targeting (`b8556eb`, `0b6079c`) + Consign
to Memory counterspell tag (`229ee97`). Joint effect: Affinity 88.9 → 78.4%,
Boros vs Affinity stable 50/50 at n=20.

Azorius investigation (verbose replay + explore agent):
- Wrath of the Skies was being cast at X=0 on T2 because `available_for_x` is
  computed AFTER paying the base WW cost. With 2 untapped lands total, X had
  to be 0, sweeping only 0-CMC tokens while Boros's 1-drops survived.
- Fix: `ai/ev_player.py:_score_spell` now hard-gates X-cost board wipes when
  the effective X-budget can't kill ≥2 enemy creatures. Wrath now holds for
  T3+ when X≥1 sweeps the entire Boros board.
- `decks/gameplans/azorius_control.json`: emptied `mulligan_combo_sets` (the
  Scepter+Chant requirement was mulliganing good interaction hands) and
  `reactive_only` (gated Counterspell/Orim's Chant behind 4+ power threats
  that don't exist vs aggro).

Smoke (n=20): Azorius vs Prowess 0→25%, vs Boros 0→5%, vs Affinity 0→10%.
Matrix-v3 (n=100) still shows Azorius at 7.9% overall — structural
weakness: 0 mainboard blockers. Deferred as requires decklist edit, not
code fix.

`build_dashboard.py` — added a `merge()` helper so
`run_meta.py --matrix --save` actually rebuilds the dashboard. It was
calling a function that didn't exist (`from build_dashboard import merge`)
and printing "Dashboard merge skipped" every run. Now it reads
`metagame_results.json`, overwrites `wins[][]`, recomputes overall +
weighted WRs, and rewrites `metagame_data.jsx` + HTML. Narrative data
(matchup_cards, deck_cards) is preserved.

### Matrix-v3 outlier summary (2026-04-12, commit `a9b1cd0`)
`python meta_audit.py` output:

| Severity | Deck | WR | Range |
|----------|------|-----|-------|
| severe | Azorius Control | 7.9% | 30-50% |
| moderate | Affinity | 78.4% | 45-65% |
| minor (×8) | Pinnacle Aff, Dimir, Zoo, Tron, Amulet, Jeskai, 4c Omnath, Ruby Storm | all within 10pp of band | |

Compared to matrix-v2:
- Severe outliers: 2 → 1 (Affinity demoted to moderate; other session's
  threat-targeting + my SB-bump + P0 cards.py fix combined).
- Azorius: essentially unchanged in matrix (7.3 → 7.9); smoke gains are
  real but drowned out by structural weakness across full matchup pool.
- Overall meta: healthier than ever. Only 1 severe outlier; remaining
  issues are tuning-depth rather than engine bugs.

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
