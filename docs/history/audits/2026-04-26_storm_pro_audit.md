---
title: Ruby Storm pro-player audit (5-agent panel)
status: active
priority: primary
session: 2026-04-26
depends_on:
  - docs/history/audits/2026-04-11_LLM_judge.md
  - docs/diagnostics/2026-04-21_ruby_storm_underperformance.md
tags: [storm, audit, pro-review, multi-agent]
summary: >
  Five specialist pro-player agents audited 30 Storm Bo3 traces across
  5 matchups. Surfaced one regression (Medallion deferral filter) and
  three structural issues (combo shock-pay bypass, tutor hold-penalty,
  mulligan combo-set short-circuit). Auto-fixes target +5-8pp Storm WR.
---

# Ruby Storm pro-player audit — 2026-04-26

**Type:** 5-agent specialist pro-player panel (mulligan, chain execution, finisher selection, life management, mana sequencing)
**Trigger:** Post-iteration follow-up. Three deferral-gate sister-fixes (PRs #194-#196) lifted Storm field N=50 from 29.2% → 39.8% (+10.6pp). User requested pro audit to verify whether the remaining gap is "structural" or whether more surgical wins exist.
**Corpus:** 30 trace files at `replays/audit_storm_vs_*.txt` — 5 matchups (Boros Energy, Affinity, Eldrazi Tron, Dimir Midrange, Domain Zoo) × 6 seeds (60500–60550). Storm record: **6-24 (20% on this corpus)**.
**Reports:** `/tmp/audit_a1_mulligan.md`, `/tmp/audit_a2_chain_execution.md`, `/tmp/audit_a3_finisher.md`, `/tmp/audit_a4_life_management.md`, `/tmp/audit_a5_mana_sequencing.md`.

## Executive summary

The 39.8% baseline is **not structural** — the audit identified one P0 **regression** introduced by the deferral-gate work (Medallion filtered out as no-signal), one P0 **gameplay bug** (combo decks unconditionally pay 2 life for shock-untapped on T1-T8), and one **shared root cause** independently flagged by two agents (Wish/tutor hold-penalty too aggressive in `combo_calc.py`). All three have surgical, principled fixes.

**Top 3 findings (all P0):**

1. **Ruby Medallion is filtered out by the no-signal deferral gate** (`ai/ev_evaluator.py:710-724` + `ai/ev_player.py:417-422`). Storm's #1 engine card scores +11 EV on T2/T3 but is discarded because the deferral signal `combo_continuation` requires `storm_count > 0 OR has_reducer_on_board` — neither holds when deploying the FIRST reducer. **Regression** introduced by PR #194-#196. Trace evidence: `replays/audit_storm_vs_dimir_midrange_s60510.txt:64-72` shows Medallion at +11.0 with `<--` selection marker but `>>> CAST_SPELL: Manamorphose` (+0.1) actually executed. Across 6 dimir + 6 boros traces Medallion deploys T6-T8 instead of T2-T3.

2. **Combo decks unconditionally pay 2 life for untapped shock on T1-T8** (`engine/game_runner.py:80-81`). The hard `if archetype == "combo" and game.turn_number <= 8: return True` bypasses the entire `enables_spell` check 50 lines below. Trace evidence: every T2 Storm turn in 18 sampled aggro games pays 3 life (fetch + shock untapped) and casts no spell — 18 free damage points donated to aggro across the corpus. Magic-constant override that contradicts the principled spell-enablement check that already exists in the same function.

3. **Wish hold-penalty too aggressive — Storm holds the tutor until hand is dry** (`ai/combo_calc.py:657-668`). Independently surfaced by A2 (chain execution) and A3 (finisher selection). The hold penalty `-non_tutor_fuel/opp_life * combo_value` keeps Wish at -13 to -33 EV with 2-5 fuel cards in hand, well below `pass_threshold = -5.0`. Storm passes T10 at 1 life with 2 Wishes uncast (`audit_storm_vs_dimir_midrange_s60500.txt:343-361`). Wish is itself a chain-extender (+1 storm, +1 card) — it should not be scored identically to a STORM-keyword closer.

**Estimated WR delta if top 3 fixes land:** 39.8% → 47-50% (per agent estimates, conservative aggregation).

## Findings by severity

### P0 — auto-fix candidates this session

| ID | Source | Site | Fix shape | Auto-fix? |
|---|---|---|---|---|
| **F5.1** | A5 (mana seq) | `ai/ev_evaluator.py` `_enumerate_this_turn_signals` | Add cost-reducer permanent signal — fires when hand contains a non-land spell that would benefit. Generalises to Goblin Electromancer, Sapphire Medallion, Baral. | **YES** — auto-fix #1 |
| F4.1 | A4 (life mgmt) | `engine/game_runner.py:80-81` | Delete the `archetype=="combo" and turn<=8 → return True` bypass; trust the `enables_spell` check below. | **REVERTED** — caused −11.3pp regression on Goryo's Vengeance in the matrix gate (15% → 3.7%). Goryo's needs untapped mana to assemble Mending + Vengeance across multiple turns; the principled `enables_spell` check only validates THIS turn's spell. Refinement to "look-ahead aware" deferred to next session. |
| F2.1 / F3.1 | A2 + A3 (consensus) | `ai/combo_calc.py:657-668` (tutor branch) | Lower the hold penalty for tutor spells, since a tutor itself contributes +1 to storm. Quantitative: divide the existing penalty by tutor_chain_value (already +2 in the lethal-fire branch). | DEFER — combo_calc.py is delicate; needs targeted tests first. |
| F2.2 | A2 | `ai/combo_calc.py:683-710` (cost-reducer branch) | Drop the `improvement <= 0 and dmg_with > 0 → positive` floor when `dmg_with == dmg_without` (redundant 2nd/3rd Medallion). | DEFER — interacts with Phase 2c.3 cost-reducer logic; risk of regressing first-Medallion EV. |

### P1 — auto-fix candidates this session

| ID | Source | Site | Fix shape | Auto-fix? |
|---|---|---|---|---|
| F1.2 | A1 (mulligan) | `ai/mulligan.py:100-102` | Replace `return True` with `continue` so missing-combo-piece tolerance doesn't bypass `mulligan_max_lands` / `cheap_spells` / `critical_pieces` gates downstream. 1-line. | DEFER — code-correctness fix but no current deck declares both `mulligan_combo_sets` AND a downstream gate (e.g. `mulligan_require_creature_cmc`) that fires at hand_size=6. Zero behaviour change. Ship when first such deck appears. |
| F1.1 | A1 | `ai/mulligan.py:89-94` | Tighten `always_early` slack from `+2` to `+1`, require `action ≥ 2` rituals/cantrips. Targets specific 4-land + Medallion + PiF keep observed in 3 matchups. | DEFER — needs concrete test first. |
| F1.3 | A1 | `ai/mulligan.py:133-138` | Drop the `flashback+combo` clause from finisher-backup check, or require pairing with raw STORM/tutor finisher. | DEFER — paired with F1.1, same area of code. |
| F2.3 | A2 | `ai/combo_calc.py:540-583` `_has_viable_pif` | Extend PiF viability check to require `mana_after_pif >= min(gy_ritual_cost)`. PiF without flashback-reach is dead. | DEFER — same cluster as F2.1. |
| F4.5 | A4 | `ai/ev_player.py` storm_patience filter | Add desperation override: when `combo_clock <= 2` AND `opp_clock_discrete <= 2`, suppress storm_patience. | DEFER — needs clock-primitive smoke test. |
| F3.5 | A3 | `ai/ev_player.py:425-426` | When `am_dead_next` AND no other cast above threshold, lower threshold for tutor/finisher tags. | DEFER — overlaps with F4.5. |
| F5.4 | A5 | `ai/ev_player.py:_score_land` | Defer fetch when 1-mana cantrip is castable and `lands_played_this_turn==0`. | DEFER — needs land-vs-cantrip ordering test. |

### P2 — documented, not auto-fixed

- F1.4 — opponent-aware mulligan (Dimir Counter-decks vs aggro). Out of scope for 1-line fix.
- F2.4 — Manamorphose-first preference (mana-neutral cantrip > ritual). Soft.
- F2.5 — off-by-one in `storm_coverage` math. Compounds with F2.1 but tiny standalone.
- F3.2 — Empty-the-Warrens survival heuristic doesn't read sweeper oracle text from opp hand/library. Generalisation: same predicate as `creature_threat_value`.
- F3.3 — finisher comparison maximises expected damage instead of P(close).
- F3.4 — Galvanic Relay hardcoded in priority list but not in SB. Dead-code.
- F4.2 — fetches auto-crack at `engine/land_manager.py:104` — no `should_crack_fetchland` AI callback. Significant work; documented for next session.
- F4.3 — fetch crack at 2 life (covered by F4.2's callback shape).
- F4.4 — `CYCLING_FREE_COST_BONUS = 2.0` flat for life-paid cycling. Should use `clock.life_as_resource` delta.
- F5.2 — Sunbaked Canyon never cycles. Engine + parser issue; significant work.
- F5.3 — Off-color fetch tiebreaker (B/R Mire vs R/U Tarn in mono-R Storm). Cosmetic.
- F5.5 — Fetch + shock taken with no payoff this turn. Symptom of F4.1 root cause.

## Findings by domain

### A1 — Mulligan (3 findings, 1 auto-fix)

Storm's mulligan logic has a logic bug (F1.2 `return True` bypasses downstream gates) and an over-permissive `always_early` slack (F1.1). The `flashback+combo` finisher heuristic (F1.3) credits Past in Flames as a "finisher path" with empty graveyard.

### A2 — Chain execution (5 findings, 0 auto-fix this session)

The big one: tutor branch holds Wish at -13 to -33 EV through entire chains (F2.1). Combined with redundant Medallion casting mid-chain (F2.2), `_has_viable_pif` not checking flashback-reach mana (F2.3), no Manamorphose-first preference (F2.4), and storm-coverage off-by-one (F2.5). All in `ai/combo_calc.py`.

### A3 — Finisher selection (5 findings, 0 auto-fix this session)

Confirms F2.1 from independent angle (F3.1 — Wish never fires in 12/12 traces vs Tron and Dimir). Plus survival heuristic in `engine/card_effects.py:1097-1099` ignores hidden-zone sweepers (F3.2), Empty-vs-Grapeshot finisher comparison maximises wrong objective (F3.3), Galvanic Relay dead-code (F3.4), pass-threshold gate doesn't relax under terminal pressure (F3.5).

### A4 — Life management (5 findings, 1 auto-fix)

The headline P0: combo decks unconditionally pay 2 life for shock untapped pre-T9 (F4.1). Auto-crack fetch with no AI callback (F4.2). Floor-only fetch crack at 2-life (F4.3). Sunbaked cycling has no life-budget gate (F4.4). Storm passes at low life with full hand instead of Hail Mary (F4.5).

### A5 — Mana sequencing (5 findings, 1 auto-fix)

The deferral-gate regression: Ruby Medallion filtered out as no-signal (F5.1). Sunbaked Canyon never cycles (F5.2). Off-color fetch tiebreaker (F5.3). Land-before-cantrip on T1 with fetch in hand (F5.4). Fetch + shock taken with no payoff (F5.5 — symptom of F4.1).

## Auto-fix plan (this session)

One PR shipped:

1. **F5.1** — `ai/ev_evaluator.py` add cost-reducer signal. Fixes the Medallion deferral regression. New test: `tests/test_medallion_not_deferred.py` (3 cases: signal-fires + EVPlayer end-to-end + empty-hand-still-defers regression anchor).

Follows Option C (failing test first → fix → green suite → commit test + fix together). Matrix-gated (no deck regresses >5pp from prior staging baseline).

Two further candidates were prepared but reverted/dropped:

- **F4.1 (reverted)** — `engine/game_runner.py` delete combo shock-pay bypass. Initial fix passed Storm field N=50 (+2.2pp) but matrix gate showed −11.3pp regression on Goryo's Vengeance (15% → 3.7%). Goryo's combo turns need untapped mana ready across multiple turns; the `enables_spell` check only validates THIS turn. The agent diagnosis was correct (Storm donates 3 free life on T2 vs aggro) but the fix shape was insufficient — it needs look-ahead awareness to preserve combo decks that legitimately want headroom. Deferred.
- **F1.2 (dropped)** — `ai/mulligan.py` `return True` → `continue`. Code-correctness fix but no current deck declares both `mulligan_combo_sets` AND a downstream gate that fires at hand_size=6, so behaviour is unchanged. Documented for re-pickup.

## Deferred to next session

- F1.1, F1.3 — companion mulligan tightening (paired with F1.2 area).
- F2.1, F2.2, F2.3 — combo_calc.py tutor + cost-reducer + PiF gates. Highest impact among P0s but combo_calc.py is delicate; needs Storm-specific test scaffolding first.
- F3.5, F4.5 — desperation override (combined fix shape).
- F4.2 — `should_crack_fetchland` AI callback. Significant cross-cutting change; affects 10+ decks.
- F5.2 — Sunbaked Canyon cycling parser. Engine + AI work.
- All P2 items.

## Verification protocol

For each auto-fix PR:

```bash
# Existing suite green
python -m pytest tests/ -q

# Storm field smoke
python run_meta.py --field "Ruby Storm" -n 20

# Matrix gate (no deck regresses >5pp from prior staging)
python /tmp/run_matrix_gate.py
```

Final: Storm field N=50 measurement after all 3 PRs land, comparison vs 39.8% baseline, recorded in this audit doc's update.

## Reading order for review

1. This document (executive summary + top-3 findings)
2. `/tmp/audit_a5_mana_sequencing.md` — Medallion regression (highest priority)
3. `/tmp/audit_a4_life_management.md` — combo shock-pay bypass
4. `/tmp/audit_a2_chain_execution.md` and `/tmp/audit_a3_finisher.md` — Wish hold-penalty consensus (deferred but documented)
5. `/tmp/audit_a1_mulligan.md` — mulligan logic bugs

## Update log

- 2026-04-26 (initial): audit doc created with findings from 5-agent panel.
- 2026-04-26 (post-F5.1+F4.1 N=50): Storm field avg WR =
  **42.0%** (up from 39.8% baseline, +2.2pp).
- 2026-04-26 (matrix gate failure): F4.1 caused −11.3pp Goryo's
  regression. F4.1 reverted.
- 2026-04-26 (post-F5.1-only N=50): Storm field avg WR =
  **39.8%** (matches baseline — F5.1 is matchup-shifting,
  WR-neutral). Shipped as a regression repair.

### Storm field N=50 — F5.1 only (FINAL, after F4.1 revert)

| Matchup | Pre (39.8% baseline) | F5.1 only (**39.8%**) | Δ |
|---|---:|---:|---:|
| vs Goryo's Vengeance | 80% | 90% | **+10** |
| vs 4/5c Control | 75% | 84% | +9 |
| vs Living End | ~50% | 68% | **+18** |
| vs Azorius Control | 85% | 82% | −3 |
| vs Amulet Titan | 55% | 52% | −3 |
| vs Domain Zoo | 40% | 42% | +2 |
| vs Boros Energy | 25% | 16% | **−9** |
| vs Dimir Midrange | 25% | 18% | **−7** |
| vs 4c Omnath | 25% | 14% | **−11** |
| vs Affinity | 10% | 12% | +2 |
| vs Eldrazi Tron | 5% | 4% | −1 |

**Δ +0pp overall** — F5.1 is *net-neutral* on Storm field WR. The
fix shifts the matchup distribution: slow matchups (Goryo's +10,
Living End +18, 4/5c Control +9) gain because Storm now reliably
deploys Medallion T2/T3 and assembles its engine; aggro matchups
(Boros −9, Dimir −7, 4c Omnath −11) lose because the same
faster engine deployment exposes downstream chain-execution bugs
(F2.1 / F3.1 — Wish hold-penalty in `combo_calc.py`) that were
masked when Medallion sat in hand uncast.

The F5.1 fix is shipped as a **regression repair** rather than a
WR lift: PR #194-#196's deferral gate filtered out Storm's #1
engine card by treating its static cost-reduction as no-signal.
Restoring the signal is correct behaviour even when the WR
delta is zero. The remaining gap (vs Boros 16%, Affinity 12%,
Tron 4%, Dimir 18%, Omnath 14%) is now squarely the deferred
**combo_calc.py** fixes' surface area — the next session.

### Storm field N=50 — F5.1+F4.1 reading (BEFORE F4.1 revert, archived)

| Matchup | Pre (39.8%) | F5.1+F4.1 (42.0%) | Δ |
|---|---:|---:|---:|
| vs Azorius Control | 85% | 92% | **+7** |
| vs Living End | ~50% | 66% | **+16** |
| vs 4/5c Control | 75% | 84% | **+9** |
| vs Goryo's Vengeance | 80% | 84% | +4 |
| vs Amulet Titan | 55% | 60% | +5 |
| vs Domain Zoo | 40% | 44% | +4 |
| vs Boros Energy | 25% | 18% | **−7** (noise/regression mix) |
| vs Dimir Midrange | 25% | 20% | **−5** |

The post-revert reading (F5.1 only) is below in the next subsection.

### Matrix gate N=20 — F5.1 + F4.1 vs prior staging baseline

| Deck | Pre | Post | Δ |
|---|---:|---:|---:|
| **Goryo's Vengeance** | 15.0% | **3.7%** | **−11.3** ⚠ blocking |
| Living End | 35.3% | 42.3% | +7.0 |
| Jeskai Blink | 59.3% | 61.7% | +2.3 |
| Ruby Storm | 38.0% | 39.7% | +1.7 |
| Other 12 decks | — | — | within ±1.5pp |

Symmetry violation count: 46 (within normal range; matrix N=20
sample variance is intrinsic).

The Goryo's regression is the trace-confirmed F4.1 side-effect:
Goryo's plays Watery Grave / Hallowed Fountain TAPPED instead of
paying shock, because the principled `enables_spell` check
returns False whenever the held Mending is castable without the
shock — it doesn't account for "I want untapped mana ready for
next turn's Vengeance." F4.1 reverted on this branch; trace
evidence preserved at `replays/audit_storm_vs_*.txt` for the
next-session refinement.
