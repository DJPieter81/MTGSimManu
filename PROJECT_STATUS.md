# MTGSimManu — Project Status & Planning Reference

> **Last updated:** 2026-04-26 (Storm + Goryo's deferral-gate iteration: Storm 29.2→39.8% +10.6pp, Goryo's 8.1→13.4% +5.3pp, three sister-fix PRs open #194 #195 #196)
> **Purpose:** Single-source-of-truth for Claude Code planning mode. Read this before any session.
> **Sister project:** MTGSimClaude (Legacy format, 38 decks, see LEGACY_MODERNISATION_PROPOSAL.md)

---

## Recent work — Storm + Goryo's deferral-gate iteration (2026-04-26)

Three deferral-gate sister-fixes in the same Wish-pattern shape, plus
a Goryo's deck-construction fix.  All on independent `claude/*` branches
with separate PRs (per session protocol — no auto-merge).

The deferral gate at `ai/ev_player.py:417-420` filters cast-spell
candidates with empty same-turn signal lists.  Three different combo
cards (Wish tutor PR #192 — already merged; Ruby Medallion cost
reducer PR #194; Past in Flames flashback static PR #196) all
returned empty signal lists despite high EV scores, so the AI cast
lower-EV cantrips or passed instead.

| PR | Branch | Mechanism |
|---|---|---|
| #194 | `claude/storm-medallion-signal-deploy` | Cost-reducer first-deploy signal (#17) |
| #195 | `claude/goryos-unburial-rites-decklist` | 4× Unmarked Grave → 4× Unburial Rites + 1× Archon |
| #196 | `claude/storm-pif-flashback-signal` | PiF flashback + GY-fuel signal (#18) |

**Cumulative N=20 16-deck matrix gate** (all 3 fixes merged on staging branch):

| Deck | Pre-iteration | All 3 Fixes | Δpp |
|---|---:|---:|---:|
| **Goryo's Vengeance** | 8.1% | **15.0%** | **+6.9** ✓ |
| **Ruby Storm** | 29.2% | **38.0%** | **+8.8** ✓ |
| Affinity | 88% | 87.3% | −0.7 |
| Boros Energy | 76% | 75.7% | −0.3 |
| All others | (within ±2pp) | (within ±2pp) | — |

No deck regressed >2pp.  No symmetry violations introduced.

**Storm field N=50 (final precise measurement):** **39.8%** (+10.6pp
vs pre-iteration baseline 29.2%).  Lift broadly distributed: 7
matchups gained ≥10pp (4/5c +20, Az WST +26, Domain Zoo +20,
Dimir +16, Pinnacle +14, WST v2 +16, Jeskai +10).  Did not reach
the 50% target — remaining gap is structural (Affinity 12%, Tron
4%, Boros 18% are matchup floors driven by clock pressure).

**Goryo's field N=50:** **13.4%** (+5.3pp vs baseline 8.1%).
Control matchups 30-40%, aggro 0%.

**Generic by construction:** every fix uses oracle text + tag
detection.  No card-name hardcoding.  See
`docs/experiments/2026-04-26_storm_goryos_iteration.md` for the
full session log including matchup spreads, smoke-test traces,
and stop-criterion analysis.

**Plan file:** `/root/.claude/plans/lets-do-a-proper-delightful-star.md`

---

## Recent work — Phase 2c combo refactor complete (2026-04-25)

The Phase 2 series (`/root/.claude/plans/lets-first-do-a-curried-rocket.md`)
unified the combo-scoring logic onto a single principled module.  The
legacy 440-LOC `_combo_modifier` in `ai/ev_player.py` is gone; its role
is now owned by `ai/combo_calc.py::card_combo_modifier`, which is
zone-aware (storm / graveyard / mana), role-aware (payoff / fuel /
engine / dig), and arithmetic-derived (no per-card scoring tables).

| PR | Phase | Outcome |
|---|---|---|
| #181 | engine fix | Living End graveyard mutation race (merged) |
| #182 | engine fix | Seed `runner.rng` for matrix determinism (merged) |
| #184 | PR-A | Subtlety ETB references `game.stack._items`, not `.items` (merged) |
| #185 | PR-B (2c.1) | State-query routing + bridge calibration — closed, superseded by #189 |
| #186 | PR-C (2c.2-prep) | `card_combo_modifier` hardened with 14 new unit tests (merged) |
| #189 | PR-D+E hard | Delete `_combo_modifier`, port 5 logic blocks, identity cache (merged) |

**Matrix gate (N=20):** all 17 decks within ±5pp tolerance vs pre-2c
baseline.  Headlines: Ruby Storm **+1.1pp**, Goryo's Vengeance
**+4.1pp**, Living End −0.4pp, Amulet Titan −3.2pp.  Compared to the
abandoned Phase 2b retry (#183) which regressed Storm −20.4pp and
Goryo's −13.0pp, the hard-refactor approach restored or improved
every combo deck.

**What was ported into `card_combo_modifier`:**
- `STORM_HARD_HOLD = -1000.0` rules constant (phase-end mana empty
  is strictly worse than passing the turn)
- `_has_storm_finisher(card, me)` — direct STORM keyword OR tutor
  with valid SB ∪ library target (no hardcoded Wish / Grapeshot)
- `_has_viable_pif(card, me, snap, …)` — flashback-combo card
  requires GY fuel + mana to cast + finisher access (no hardcoded
  Past in Flames)
- `_has_draw_in_hand(card, me)` — cantrip / card_advantage / draw
- Storm=0 ritual chain gate (proper SB-validation)
- Storm≥1 mid-chain gate with hard-hold + soft-penalty + storm-coverage
  escalation (`HALF_LETHAL=0.5`) + draw-miss cascade risk
  (`MIN_CHAIN_DEPTH=3`, `CASCADE_DRAW_FLOOR=1`)

**Performance:** identity-based per-snapshot cache (`id(snap)`) on
`assess_combo`.  All spells scored within one `decide_main_phase`
call share a snap so the assessment runs once per decision instead
of once per spell.  3.6× speedup vs. uncached: 5 Storm vs Azorius
games dropped from 102s → 28s (≈ baseline 5s/game).

**Phase 2 superseded artefacts:**
- Phase 2a (`build_combo_distribution` dispatcher, `OUTCOME_DIST_COMBO`
  flag, PR #179 merged) — flag stays `False`; the dispatcher is
  dormant after #189 and may be revisited in a future phase if the
  single-turn distribution model gains multi-turn lookahead.
- Phase 2b (PR #183, closed) — single-turn distribution couldn't
  represent multi-turn combo setup.
- Phase 2c.1 (PR #185, closed) — state-query routing + bridge
  calibration; obsoleted by the hard refactor.

Anti-patterns rejected during this work:
- Magic constants in `card_combo_modifier` — every numeric value is
  derived from CR damage rules, ritual_mana oracle parsing, or
  STORM-profile fuel thresholds (with inline justification)
- Hardcoded card names — Past in Flames, Wish, Grapeshot, Empty the
  Warrens all detected by tags / oracle text patterns
- Per-card EV tables — would have re-introduced the `card_ev_overrides`
  pattern retired in EV correctness Phase 5

---

## Recent work — EV Correctness Overhaul complete (2026-04-20)

The EV-correctness design doc (`docs/design/ev_correctness_overhaul.md`,
`status: superseded`) is closed.  Nine phases shipped across PRs #122,
#128, #130, #132, #133:

| Phase | Focus | Outcome |
|---|---|---|
| 1 | Deferral baseline + pass-preference tiebreaker (Bugs A, B, E.1) | Signal framework + `_enumerate_this_turn_signals` |
| 2 | Conditional artifact-count term (Bug D) | `EVSnapshot` + `position_value` extended |
| 3 | Marginal-destruction X optimizer (Bug C) | Wrath of the Skies picks best X |
| 4 | Landcycling / typecycling resolver (Bug E.2) | Sojourner's Companion tutors lands |
| 4.5 | Signal-based mulligan escape (Bug F) | Anti-matchup hands kept |
| 5 | Retire card_ev_overrides prototype | Phase 2's artifact term subsumes it |
| 6 | N=20 matrix validation | Flagged Storm / Amulet Titan / Pinnacle Affinity |
| 7 | Pinnacle Emissary `other_enters` trigger | Closed last failing test — suite 226/226 |
| 8 | Life / energy persistent_power | Closed Phase 7's Boros regression |
| 9 | Phase 6 follow-ups | Storm finisher patience + Amulet engine signal + Pinnacle hypothesis falsified |

Full suite **232/232** (from baseline 196/197 with 1 pre-existing failure).
Matrix trend: weighted WRs up (more balanced meta), flat WRs down
(AI defers junk casts across the board).  Affinity still top (~83% wtd),
Boros stable, Storm / Amulet Titan / Goryo's all recovered from their
post-Phase-6 lows.

Experiment log chain (all `status: archived`):
`docs/experiments/2026-04-20_phase6_matrix_validation.md` →
`docs/experiments/2026-04-20_phase7_pinnacle_emissary_fix.md` →
`docs/experiments/2026-04-20_phase8_life_energy_persistent.md` →
`docs/experiments/2026-04-20_phase9_phase6_followups.md` (tip).

---

## Current work — frontmatter registry

Session priorities, active work, and falsified hypotheses are all declared in YAML frontmatter on every doc under `docs/`. See `CLAUDE.md` → "Session Priorities (discovery protocol)" for the grep commands. The frontmatter IS the registry — no curated list in this file to drift.

**To find current active work:**
```
grep -rEl '^status: active' docs/ --include='*.md' | xargs grep -l '^priority: primary'
```

**To avoid re-running dead hypotheses:**
```
grep -rEl '^status: falsified' docs/ --include='*.md'
```

Historical session content (architecture, API signatures, past bug queues, WR history) follows below. For what's active *right now*, use the grep commands above.

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

**Overall grade: B** (post-iter6 — Affinity matchup plan iteration 2: extended `_has_high_threat_target` to also consider noncreature permanents (scaling equipment like Cranial Plating, Nettlecyst, planeswalkers, stax) in addition to creatures. Leyline Binding added to Domain Zoo's reactive_only list so it holds for CP instead of burning on Ornithopters T2. Affinity field WR 82% → **77%** (−5pp); CP removal count nearly doubled; CP delta +0.44.)

**Unified refactor session (2026-04-17 — branch `claude/review-refactoring-docs-piRZ5`):** synthesised the 6 in-flight planning docs (ORACLE_REFACTOR_PLAN_V2, AI_IMPROVEMENT_PLAN_V2, ITERATION_7_PLAN, TRANSFORM_FIX_PLAN, CONTROL_DECK_PLAN, AFFINITY_MATCHUP_PLAN) into one ordered plan and shipped Phases A-I. ~60% of the items were already present from prior sessions; the new work is summarised below.

**Phases shipped this session (commits 5a04771, 873c493, d7441dd, 58308ad, ac92259, dcb9bb6):**

| Phase | Plan source | Outcome |
|-------|-------------|---------|
| A — EVSnapshot smoothing | Iter7 #4, #5 | `opp_clock` continuous + `opp_clock_discrete` companion; `urgency_factor` exponential `1-exp(-slack/2)`. |
| B — Live-snapshot creature_value | Iter7 #3 | `creature_value(card, snap=None)` plumbed through removal/threat call chain. |
| C — Generic damage target picker | Oracle V2 #1 | `_pick_damage_target()` in oracle_resolver routes "any target" damage to threat-scored creature or face. Phlage ETB delete. |
| D — Transform mechanics | Transform #1, #2, #3 | Generic `_transform_permanent()` helper + subtype-death + count-based-cast triggers + Fable Ch.II/III. |
| E — Tap-ability dispatch | Iter7 #8, #9, #11, #12 | `_activate_tap_abilities()` (Endbringer ping/draw, Emry GY-cast); opponent-untap-step; flashback-sacrifice cost; Witch Enchanter ETB destroy oracle pattern. |
| F — ETB suppression | Iter7 #13 | Doorkeeper Thrull static checked from `_handle_permanent_etb` before dispatch. |
| G — Living End post-combo push | Iter7 #6 | `PlayerState.post_combo_push_turns=3` + `GoalEngine.current_goal` override forces PUSH_DAMAGE while flag set. |
| H — Wrath self-wipe + control_patience | CONTROL_DECK_PLAN, audit | Self-wipe hard gate (don't board-wipe when ahead+not-dying); `has_big_target` overrides `control_patience` for reactive-only single-target removal. |
| I — Oracle deletions (Groups A+B) | Oracle V2 #2, #3 | 7 handlers deleted: Preordain, Sleight of Hand, Wall of Omens, Reckless Impulse, Wrenn's Resolve, Glimpse the Impossible, Heroes' Hangout. `resolve_spell_from_oracle` gained generic draw + exile-and-may-play patterns and is now invoked as a SPELL_RESOLVE fallback. |

**Phase I scope adjustment:** Groups C-F (removal, reanimate, tokens, mana) deferred. Inspection showed the handlers encode meaningful per-card restrictions (Prismatic Ending devotion-based CMC; Abrupt Decay CMC≤3; Celestial Purge R/B only; Persist CMC≤3 reanimate; Empty the Warrens storm-dependent token count; rituals coupled to mana production system). A blanket "destroy any nonland permanent" oracle pattern would over-power them and regress correctness. A future pass needs restriction-aware regex (e.g., `mana value (\d+) or less`, `red or black permanent`) before these are safe to delete.

**Handler count: 115 → 108 (target ≤25 deferred)**.
**`card_effects.py`: 2,788 → ~2,770 LOC (placeholder comments offset deletions; cleanup pass pending)**.
**`oracle_resolver.py`: 485 → ~720 LOC (added damage picker, transform helper, draw + exile-may-play + ETB-destroy patterns)**.

**Affinity matchup iter2 (post-iter6 — 2026-04-13):**
| Metric | iter5/iter6 | Current |
|--------|------------|---------|
| Affinity field WR | 82% | **77%** |
| CP delta (audit) | +0.30 | **+0.44** |
| CP removed count (60 games) | 5 | **9** |
| Affinity vs Zoo WR | 93% | 87% |
| Affinity vs Boros WR | 77% | 80% |

**Affinity matchup re-verify (2026-04-16, branch `claude/affinity-matchup-plan-qQfUQ`):**

All three AFFINITY_MATCHUP_PLAN.md fixes confirmed live in branch (commits
`572d9d5`, `35256f8`, `7551c5b`). Re-ran the plan's verification checklist
against current `main`-HEAD:

| Metric | Plan target | Re-verified n=60 |
|--------|-------------|------------------|
| Affinity field WR | 55-65% | **82%** (49/60) |
| Cranial Plating delta | > 0 | **+0.09** |
| Signal Pest delta | ≥ 0 | **+0.23** (was −0.30 baseline) |
| Springleaf Drum delta | ≥ 0 | **+0.15** (was −0.39 baseline) |
| Engineered Explosives delta | > 0 | **−0.29** (was −0.54 baseline) — improved but still negative |
| Affinity vs Boros WR (n=30) | 65-70% | **80%** |
| Affinity vs Zoo WR (n=30) | 60-70% | **80%** |
| Affinity vs Prowess WR (n=20) | n/a | 75% |
| Affinity vs Dimir WR (n=20) | n/a | 100% |

Fix 1 (CP threat scaling) verified in `bo3 zoo affinity -s 60200`: Zoo casts
Leyline Binding twice (T3 on Signal Pest, T5 on Thought Monitor) instead of
sitting in hand. SB swap-in of Wear // Tear in Boros vs Affinity (verified in
`bo3 energy affinity -s 60200`).

Fix 3 (CP equip evasion preference) verified: Cranial Plating equips to
Ornithopter (T4, T5, T6, T7 across the two replays), Thought Monitor (T5,
flying), and Frogmite/Construct only as fallback when no flier is available.

**Engine fix (this session):** removed orphan `_discarded = []` /
`_discarded.append(card)` lines in `engine/game_state.py::_force_discard`
(introduced in `9a237d7`, scope-leaked outside its function and raised
`NameError` on every Thoughtseize / Inquisition resolution — was blocking
`run_meta.py --audit` entirely). Variable was never read.

**Open work** (not in scope for AFFINITY_MATCHUP_PLAN.md):
- EE delta still negative (−0.29). Cast 13× over 60 games; loss-rate per cast
  (5/11 = 0.45×) still exceeds win-rate per cast (8/49 = 0.16×). The reactive
  gate is releasing EE in marginal spots — a tighter gate or `expected target
  EV` floor would push this positive.
- WRs vs Boros/Zoo still 10-15pp above plan's 65-70% target. Structural
  opponent-side work (Boros wrath threshold, Zoo combat aggression vs small
  artifacts) remains for a follow-up plan.

**Iteration 6 fixes (ITERATION_6_PLAN.md — 2026-04-13)**

### Iteration 6 fixes (ITERATION_6_PLAN.md — 2026-04-13)
| Fix | Files | Status |
|-----|-------|--------|
| A. Living End aggression timing | `engine/game_state.py`, `engine/combat_manager.py` | ✅ landed (`24fb118`) |
| B. Ritual immediate-effect | `ai/ev_evaluator.py` | already in iter5 (`bc51028`) |
| C1. control_patience gate | `ai/strategy_profile.py`, `ai/ev_player.py` | already in iter5 (`bc602db`) |
| C2. Teferi untap bonus | `ai/ev_player.py` | ✅ landed (`24fb118`) |
| D. Undying Evil → reactive_only | `decks/gameplans/goryos_vengeance.json` | already present |

Aggression-flag semantics: combat_manager.end_combat now only decrements the **active player**'s aggression_boost_turns (was: both). Combined with `=2` (was: =1), the flag survives one wasted same-turn combat (creatures with summoning sickness) into the next turn's combat.



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
| 4 | Chalice of the Void (and other stax permanents) undervalued by `_score_spell` — treated as generic 2-mana artifact. First attempt with `ai/stax_ev.py` built but not wired; see "Failed attempt" below. Next try needs threat-gating. | `ai/ev_player.py`, `ai/stax_ev.py` |
| 5 | Removal target selection inverted vs Affinity-style boards — `_threat_score` rates mana rocks (Springleaf Drum) above 1/1 attackers (Memnite). Affects all removal (March/PE/Solitude/Verdict). Repro state drift — hand-built state scores correctly (Δ=4.3× favoring Memnite), live sim inverts. See session 2 writeup bug A. | `engine/card_effects.py:683`, `ai/permanent_threat.py` |
| 6 | `March of Otherworldly Light` `x_val` computed as `len(lands)` instead of X actually paid — makes March at X=1 resolve as X=total-lands. See session 2 bug B. | `engine/card_effects.py:675` |
| 7 | Wrath of the Skies cast on T3 with 0 mana for X, kills own Chalice and leaves CMC-2 threats alive. AI doesn't weigh "cast now" vs "hold mana for Counterspell next turn." See session 2 bug C. | `ai/ev_player.py` (sweeper-timing EV) |
| 8 | T2 Chalice-over-pass misplay — AI jams Chalice @ X=1 on T2 with Counterspell in hand and no opp threat on stack. Cast-vs-pass EV threshold is wrong; gameplan priority tweak didn't fix it. See session 2 bug D. | `ai/ev_player.py` (pass EV) |
| 9 | Mulligan heuristic doesn't deduplicate legendaries — keeps 3×Wan Shi Tong + 2 land as a valid 7. See session 2 bug E. | `decks/gameplan_loader.py` / mulligan scorer |

#### P2
| # | Bug | Location |
|---|-----|----------|
| 4 | Amulet of Vigor multiple copies don't stack (only 1 untap applied per land ETB) | `engine/game_state.py:_apply_untap_on_enter_triggers()` |
| 5 | Spelunking "Lands you control enter untapped" not applied to normal `play_land` path consistently | `engine/game_state.py` |
| 6 | Elesh Norn trigger doubling not implemented | `engine/game_state.py` |
| 7 | Phelia blink-on-attack not fully implemented | `engine/card_effects.py` |

---

### Failed attempt — Chalice/Stax EV overlay (session, 2026-04-20)

**What was tried:** Oracle-driven stax EV module (`ai/stax_ev.py`) covering Chalice of the
Void, Blood Moon, Ethersworn Canonist/Rule of Law, Torpor Orb. Family detection by oracle
pattern (no hardcoded names). Chalice valuator picks best X by `opp_cmcs[X] - my_cmcs[X]`,
mirroring `engine/game_state.py:1557`. Turn decay zeroes the bonus by T5. Capped at 6 net
locked spells. 13 unit tests, all passing. Module wired into `_score_spell` via one-line
call alongside the existing duplicate-Chalice penalty.

**Why it failed:** At n=30 field sweep, WST v1 regressed from ~36% → 32% field WR after
the overlay was added in isolation. Direct cause identified in Bo3 replay
`v2_vs_boros_60100.txt` and `v1_vs_boros_60100.txt`:
  - **Same seed, same opening hand, same opp T2 Ajani cast.**
  - v1 (no overlay): holds mana T2, Counterspells Ajani on cast → opp enters T3 with
    empty board.
  - v2 (with overlay): taps out for Chalice @ X=1 on T2 → Ajani resolves, creates Cat
    token → opp enters T3 with Ajani + 2/1 Cat.

G2 T2 is even clearer: v2 has Prismatic Ending in hand, Ragavan already in play stealing
cards, and casts Chalice instead of PE on the Ragavan. The concrete answer on the actual
threat is always beaten by the projected lock value on future draws.

**Root cause of the miscalibration:** Stax EV is computed from opponent's library
composition in a vacuum, without reference to the current-turn threat picture. On T2 vs
Boros, when Counterspell/PE has a concrete target, the overlay makes Chalice EV compete
with and often beat Counterspell's heuristic score — so the AI swaps the concrete answer
for a probabilistic lock. In Storm (where there's no board threat and Chalice really does
lock the whole game), the same overlay is correct and v2 gains +7pp.

**Next attempt must be threat-gated:** stax EV should only fire when
(a) no active opp threat requires this turn's mana, or (b) the AI would otherwise idle
the turn. Effectively: "stax is downtime insurance, not on-curve tempo."

**What was shipped (this session):**
  - `ai/stax_ev.py` — module present but **not** imported by any caller. Kept as
    reference for the oracle patterns + 13 unit tests that verify sign/magnitude. Anyone
    picking up the Chalice problem edits this file rather than starting fresh.
  - `tests/test_stax_ev.py` — passes standalone.
  - `Azorius Control (WST v2)` — new deck entry with `METAGAME_SHARES = 0.0`. +4 Solitude
    MD, −3 Sanctifier (→SB), −1 Supreme Verdict vs v1 WST. At n=30 post-overlay: 34.6%
    field WR. Pre-overlay comparison unavailable because the WST v2 deck was introduced
    in the same session as the overlay; the 34.6% number is NOT directly comparable to
    v1's ~36% baseline.
  - This writeup.

**What was NOT shipped:**
  - Wiring in `_score_spell`. Reverted.
  - Any change to v1 WST.

---

### Deep audit — WST v2 play-by-play bugs (session, 2026-04-20 #2)

Read 6 Bo3 replays: seeds 60100/60200/60400 vs Boros Energy and vs Affinity. Five
distinct misplay patterns found, documented below so they don't get lost.

**Bug A — March of Otherworldly Light picks wrong target.** Seed 60200 G2 T2: P1
March @ X=1 exiles Springleaf Drum instead of Memnite (the 1/1 attacker). Live-sim
instrumentation of `_threat_score` confirms scoring inversion:
  - Drum: 1.333  (picked)
  - Mox Opal: 1.333 (tied)
  - Memnite: **1.150** (not picked)

Isolated reproduction of the same battlefield state — without the turn-by-turn game
history — scores Memnite at 1.15 and Drum at 1.00, the *correct* order. So there
is state drift between reproduction and live sim: something cumulative inflates
non-creature artifact threat. The `position_value` delta for actually removing
Memnite is 5.12 vs Drum's 1.18 — the marginal-contribution formula is correct in
isolation. Upstream state in the live sim is distorting the snapshot.

**Next step:** dump the full EVSnapshot at live-sim decision point, compare field-
by-field to the isolated reproduction, identify the inflation source. Caller:
`engine/card_effects.py:683`. Likely affects all removal spells (PE, Solitude,
Supreme Verdict), not just March.

**Bug B — March `x_val` computed from lands, not mana paid.** `engine/card_effects.py:675`:
```python
x_val = len(game.players[controller].lands)
```
This treats X as `total lands controlled` regardless of X actually paid. A T5 March
cast with 5 lands at X=1 (pay 1W + W) resolves as if X=5, widening the candidate
pool to CMC ≤ 5. Orthogonal to Bug A but also wrong; may benefit Boros/Affinity
sims by making March look stronger than it is.

**Bug C — Wrath fires on T3 on a low-value board, kills own Chalice.** Seed 60200
G1 T3: P1 has Chalice + 2 Fountains. Opp board: Voice of Victory (CMC 2, 1/3),
2 Warrior tokens. P1 casts Wrath of the Skies (WW) with 0 mana for X → picks X=0
which destroys 2 tokens + own Chalice but leaves Voice of Victory alive. Net: −1
card (Chalice), traded for 2 tokens, and Voice continues attacking.

The X-choice logic at `engine/game_state.py:1601` is correct given available mana
(X=0 is the best X possible with 0 mana left); the misplay is casting Wrath at
all. Pattern: AI treats "opp has creatures + I have Wrath" as sufficient trigger
to sweep, without weighing tempo cost vs holding mana for Counterspell next turn
(which would have caught Ranger-Captain of Eos on T4).

**Bug D — T2 Chalice-over-Counterspell still fires in 4/5 seeds.** Seed 60100 G1
T2 and analogous seeds (60200, 60400): AI jams Chalice @ X=1 on T2 with Counterspell
in hand and no opp threat on stack. Demoting Chalice priority (this session's
gameplan tweak from 24 → 14 with `always_early` cleared) fixed *some* hands (seed
60100 G2 now correctly PE's Ragavan) but not the fundamental cast-vs-pass decision.
The AI's "pass and hold mana" EV is lower than Chalice's projection. Gameplan
priorities only affect relative ranking between cast choices, not the cast-vs-hold
threshold.

**Bug E — Mulligan keeps redundant-legendary hands.** Seed 60400 G1: P1 keeps
7-card hand with 3× Wan Shi Tong + 2 lands. Legend rule means 2 of 3 WSTs are
dead. Effectively a 5-card keep. The mulligan heuristic at
`decks/gameplan_loader.py` doesn't deduplicate legendaries.

**Session 2 outcome:** Gameplan patch landed in a separate commit (Chalice
priority 24 → 14, `always_early` cleared). v2 field WR improved from ~42% →
~45% at n=30+ pooled across two seed ranges. Bugs A–E remain open.

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
- Hardcode card names in engine — always detect from oracle text or template field. Enforced by `tools/check_abstraction.py` ratchet (see CLAUDE.md ABSTRACTION CONTRACT). Pre-commit hook blocks any commit that increases the hardcoded-name count.

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

*See also: docs/ARCHITECTURE.md · CLAUDE.md · docs/history/audits/2026-04-11_LLM_judge.md*
