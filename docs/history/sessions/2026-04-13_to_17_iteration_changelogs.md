---
title: Iteration changelogs — 2026-04-13 through 2026-04-17 (sessions 3-5, unified refactor, Affinity iter2, iter5/6)
status: archived
priority: historical
session: 2026-04-13
tags:
  - iteration-changelog
  - ai-strategy
  - affinity
  - oracle-refactor
  - phase-a-through-i
summary: |
  Detailed iteration changelogs lifted out of PROJECT_STATUS.md §6 during
  doc cleanup. Covers: unified refactor session Phases A-I (2026-04-17),
  Affinity matchup iter2 + re-verify (2026-04-13/16), Iteration 6 fixes,
  Iteration 5 fixes, Session 5 / Session 4 fixes, WR shifts from session 3
  full re-run. PROJECT_STATUS.md §6 now contains only the current grade
  snapshot.
---

# AI strategy accuracy — historical iteration log

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
