---
title: Affinity overperformance — consolidated findings (11-agent audit)
status: superseded
priority: primary
session: 2026-04-23
supersedes:
  - docs/diagnostics/2026-04-23_affinity_mana_holdback_bug.md
superseded_by:
  - docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
tags:
  - p0
  - wr-outlier
  - affinity
  - mana-holdback
  - engine
  - consolidated
  - phase-12
summary: "Consolidated findings from 11 parallel investigation agents (5 matchup traces: Boros/Dimir/Jeskai/Tron/Amulet; 6 code audits: holdback/response-gate/mana-planner/self-EV/engine-sweep/mulligan). Affinity's 86% WR has at least 12 distinct root causes across 4 layers: engine rules bugs (3), AI holdback/mana-planning bugs (4), response-gate bugs (3), scoring/mulligan asymmetry (2). Biggest individual-impact finding: Mox Opal template permanently mutated on ETB (engine/card_effects.py:291-307), producing 5 colors forever once metalcraft fires. Biggest systemic finding: the holdback penalty in ai/ev_player.py:735-752 is scalar (-2.0), color-blind, and doesn't cover cycling/equip paths; every defender matchup traced (AzCon/Boros/Dimir/Jeskai/Tron) fails at this layer."
---

# Affinity consolidated findings — 11-agent audit

## Method

Eleven parallel investigation agents spawned on 2026-04-23, all
read-only. Five ran matchup traces instrumenting `ResponseDecider.
decide_response` on Bo3s at seeds 50000/50500/51000. Six did code
audits on specific subsystems. No code was changed during the
investigation.

## Bugs found — by layer

### Layer 1 — Engine rules bugs (direct wins for Affinity)

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 1 | **E1** | `engine/card_effects.py:291-307` | Mox Opal metalcraft is checked only at ETB and mutates `template.produces_mana` permanently. After ETB with 3+ artifacts, Mox produces 5 colors forever — even if artifacts drop to 0. **Rules violation.** | **3-5 pp** |
| 2 | **E2** | `engine/game_state.py:2916` (`_force_discard`) | Thoughtseize / Duress / Inquisition blindly pick the highest-CMC card from opp's hand. Against Affinity the highest CMC is Sojourner's Companion (printed 7, effective 2 after affinity) — the LEAST valuable card to strip. Should pick Cranial Plating or Mox Opal. Meanwhile `_choose_self_discard:2923` has sophisticated EV scoring that isn't reused. | **1-2 pp** |
| 3 | **E3** | `engine/game_state.py:821` (evoke gate in `can_cast`) | Evoke gate requires `total_mana < effective_cmc`. Solitude's evoke cost is "exile a white card" (no mana) so `evoke_cost = ManaCost(0,0,0,0,0,0)`. When player has ≥ 5 mana (hardcast Solitude CMC), the gate blocks the evoke path even though evoke would be strictly better. | **0.5-1 pp** |
| 4 | **E4** (minor) | `engine/game_state.py:1197` (`equip_creature`) | No phase check — would allow instant-speed equip if called outside main phase. Mitigated today because AI only calls from `decide_main_phase`. Engine-layer rules violation. | <0.1 pp |

### Layer 2 — AI holdback / mana planning (all 5 matchups affected)

All 5 matchup traces (Boros, Dimir, Jeskai, Tron, AzCon) show the
same root cause in different colors: defender holds an appropriate
response in hand but arrives at opp's priority window without
sufficient untapped lands in the right color.

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 5 | **A1** | `ai/ev_player.py:735-752` | Holdback penalty is a flat `-2.0`. CONTROL pass threshold is `-5.0`; a CMC-2 play with base EV 5 stays at EV 3 after penalty. Does not clear threshold. | **8-12 pp** (bundled) |
| 6 | **A2** | `ai/ev_evaluator.py::EVSnapshot.my_mana` | Mana is tracked as a scalar int, not per-color. Counterspell needs UU; Galvanic Discharge needs R. The holdback check `remaining_mana < 2` can't distinguish "2 generic available" from "UU available". | |
| 7 | **A3** | `ai/ev_player.py:1352-1405` (`_score_cycling`); `ai/ev_player.py:2353-2410` (`_consider_equip`) | Cycling and equip paths have zero holdback coverage. AzCon cycles Lórien Revealed on T2 tapping its U source, no penalty applied. | |
| 8 | **A4** | `ai/ev_player.py:738-739` | Trigger threshold `opp_hand_size >= 4` is too strict; opp can empty to 3 cards and still hold a threat. | |
| 9 | **A5** | `ai/mana_planner.py:372-413` (`choose_fetch_target`) + `engine/game_state.py:479-627` (`tap_lands_for_mana`) | Fetch-crack targeting has no awareness of held instants' color requirements. AzCon cracks Arid Mesa for Hallowed Fountain (W/U) then taps Steam Vents for cycling — no pathway computes "keep UU open for Counterspell". | **2-3 pp** |

### Layer 3 — Response-gate + targeting bugs

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 10 | **R1** | `ai/response.py:235-349` (`evaluate_stack_threat`) | Linear P/T counting; doesn't model carrier-pool synergy (Sojourner's onto 2 Platings is scored as a vanilla CMC-7 4/4, not as "enters the game-winning attack"). | **1-2 pp** |
| 11 | **R2** | `ai/response.py:147-153` (response gate) | `response_value >= 3.0 or (response_value >= 1.5 and cost <= 2)`. Blind to colored-mana availability — passes gate even when actual UU not available; downstream `can_cast` then filters the counter out. Gate not threat-blind to counter CMC either. | **0.5 pp** |
| 12 | **R3** | `ai/ev_player.py:2223-2249` (`_pick_best_removal_target`) | Ranks by raw `creature_threat_value`; doesn't give priority to equipment-carrier creatures. Fatal Push targets Sojourner's (high raw threat) when killing the Plating-wearer would break the combo. | **1-2 pp** |
| 13 | **R4** | `engine/game_state.py:3153-3163` (`process_triggers`) | ETB triggers are put on stack without giving opponent priority. Can't respond to an ETB trigger before it resolves. CR 603.3 requires priority after triggers go on the stack. | **<0.5 pp** |

### Layer 4 — Affinity's own scoring + mulligan asymmetry

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 14 | **S1** | `ai/ev_evaluator.py::_project_spell:1033` | Uses `t.cmc or 0` (printed CMC), not effective CMC after affinity/improvise reduction. For Sojourner's: scorer projects CMC 7, actual CMC is 2. | ambiguous (could inflate or deflate; symptom unclear) |
| 15 | **S2** | `ai/ev_evaluator.py::creature_threat_value:282-340` | Signal Pest battle-cry is counted for removal-evaluation + combat but NOT for self-deployment EV. Affinity underestimates its own clock when deploying. | deflationary — *offsets* other bugs |
| 16 | **M1** | `decks/gameplans/affinity.json` `mulligan_min_lands` | Set to `1`, while other aggro decks default to `2`. Affinity keeps 1-land hands other aggros would mull. | **3-5 pp** |
| 17 | **M2** | `decks/gameplans/affinity.json` `mulligan_keys` | 5 keys, all 0-3 CMC, 4-of's. "Any key + 2 cheap spells" condition met by ~80% of 7-card hands. Makes the mulligan effectively a no-op. | bundled with M1 |

### Layer 5 — Orthogonal finding (Amulet-specific, not Affinity-benefiting)

| # | ID | File:line | Description |
|---|---|---|---|
| — | **O1** | `ai/gameplan.py:210-219` (combo thresholds), `ai/clock.py:117-144` (`combo_clock`) | Amulet Titan is archetype=combo with `dying_clock=4`. `combo_clock` treats Amulet as a deferred 8-resource assembly, not a race. Against Affinity's T4-5 kill, Amulet should mulligan for Titan-by-T4 or concede the race. Amulet WR 49% flat. |

## Evidence quality

| Bug | Confidence | Verification done |
|---|---|---|
| E1 (Mox Opal) | HIGH | Code inspection; template mutation observed |
| E2 (Thoughtseize) | HIGH | Code inspection; `_force_discard` sort key = cmc |
| E3 (evoke gate) | MEDIUM | Code inspection; no test reproduction |
| A1-A5 (holdback) | HIGH | Live instrumentation of `decide_response` on 5 matchups at 3 seeds each; every failure fits the pattern |
| R1-R4 | MEDIUM | Code inspection; not reproduced in isolation |
| S1-S2 | MEDIUM | Code inspection; effect direction unclear |
| M1-M2 | HIGH | `mulligan_min_lands` is a single-line config diff from peers |
| O1 (Amulet) | MEDIUM | Replay analysis; clock.py math confirmed |

## Recommended fix ordering (by risk × impact)

Fix in this order. Each bundle is a separate branch + PR, Option C
(failing test first, then fix).

**Bundle 1 — Engine rules bugs (fastest, lowest risk)**
1. E1 Mox Opal metalcraft re-evaluation. Test: deploy Mox Opal with 3 artifacts, sac 2 → tap Mox should only produce 1 color (or fail).
2. E2 Thoughtseize intelligent target. Test: Thoughtseize vs (Sojourner's, Cranial Plating, Mox Opal, lands) should pick Plating/Mox, not Sojourner's.
3. E3 evoke gate fix. Test: Solitude in hand with 5 mana, opp casts Sojourner's → Solitude can evoke in response.

**Bundle 2 — Mulligan config**
4. M1 change `affinity.json::mulligan_min_lands` from 1 to 2. Test: keep statistics on 10 random seeds show no 1-land keeps for Affinity.

**Bundle 3 — Holdback overhaul (highest impact, most complex)**
5. A1+A2+A3+A4 rework holdback to:
   - Track per-color mana in `EVSnapshot` (or compute from lands on demand)
   - Scale penalty by `(counter_count × counter_cmc × P(opp_threat_next_turn))`
   - Apply at cycling + equip paths
   - Lower trigger threshold to `opp_hand_size >= 3`
6. A5 add `held_instant_colors` to `ManaNeeds`; thread through `choose_fetch_target` and `tap_lands_for_mana`.

**Bundle 4 — Response gate polish**
7. R1 add carrier-pool multiplier to `evaluate_stack_threat`.
8. R2 response gate colored-mana awareness.
9. R3 equipment-carrier priority in `_pick_best_removal_target`.

**Defer**
- R4 (ETB trigger priority) — engine change, touches stack semantics. Out of scope for Affinity fix.
- S1/S2 — direction unclear. Revisit after bundles 1-3 to see if overall WR landed in range.
- O1 (Amulet) — separate diagnostic + fix branch; unrelated to Affinity per se.

## Expected net effect

Rough math from estimated impacts, capped for overlapping effects:

| Bundle | Est. Affinity WR change |
|---|---|
| Bundle 1 (E1+E2+E3) | −4 to −8 pp |
| Bundle 2 (M1) | −3 to −5 pp |
| Bundle 3 (A1-A5) | −8 to −12 pp (bundled) |
| Bundle 4 (R1-R3) | −2 to −4 pp |
| **Total (after overlap)** | **−12 to −18 pp** |

Target: Affinity 86% → 68-74% flat. Weighted WR reduction similar.
AzCon/Boros/Dimir/Jeskai/Tron should all pick up 2-5 pp each as
defenders stop arriving tapped-out.

## Non-goals

- Don't tune individual card EV values. Everything goes through principled
  subsystems per CLAUDE.md (clock, BHI, oracle-driven).
- Don't add per-Affinity special cases to `_score_spell` — the fix must
  be deck-agnostic (e.g., cost-reduction math applies to any Improvise
  spell, not just Sojourner's).
- Don't touch Living End (24%), Ruby Storm (24%, already fixed in PR
  #142), Azorius Control (16%), or Goryo's Vengeance (24%). Separate
  diagnostics needed.
