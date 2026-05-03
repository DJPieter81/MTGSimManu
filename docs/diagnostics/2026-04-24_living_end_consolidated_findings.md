---
title: Living End 24% underperformance — consolidated findings (11-agent audit)
status: superseded
priority: primary
session: 2026-04-24
superseded_by:
  - docs/diagnostics/2026-04-28_living_end_cascade_payoff.md
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
  - docs/diagnostics/2026-04-23_affinity_consolidated_findings.md
tags:
  - p0
  - wr-outlier
  - living-end
  - cascade
  - reanimator
  - phase-12
  - consolidated
summary: "Eleven parallel read-only investigation agents (5 matchup traces: Boros/Dimir/Jeskai/Tron/Affinity; 6 code audits: engine-resolution/gameplan/cycling/cascade-EV/counter-anticipation/combat). Pre-existing P0 engine bugs are RESOLVED by the refactor (PR #141): _resolve_living_end fires ETB triggers correctly (engine/spell_resolution.py:235); Chalice X-selection is oracle-driven, not hardcoded. Real root cause is AI-side: Living End cascades too eagerly with insufficient graveyard fuel, returns a summoning-sick board that can't block the opponent's decisive turn, and the turn_planner's VirtualBoard incorrectly counts summoning-sick creatures as attackers (ai/turn_planner.py:1085). Seven distinct findings across 4 layers; fix order proposed in bundle structure similar to Affinity."
---

# Living End consolidated findings — 11-agent audit

## Method

Eleven parallel read-only investigation agents spawned 2026-04-24 after the
Affinity-session fixes (7 PRs) merged to main. Five matchup traces
(Boros/Dimir/Jeskai/Tron/Affinity at seeds 50000/50500/51000), six code
audits (engine resolution, gameplan+mulligan, cycling decisions, cascade
EV scoring, counter-anticipation, combat). No code changed during the
investigation.

## Confirmed-FIXED (no further work needed)

Prior P0s that the engine refactor (PR #141) already resolved:

| ID | File:line | Status |
|---|---|---|
| Engine P0 (pre-refactor) | `engine/spell_resolution.py:235` (`_resolve_living_end`) | ✅ RESOLVED. ETB triggers fire correctly for returned creatures. Architects' draw, Fury's burn, Solitude's exile all fire. Confirmed in Boros matchup trace (L1). |
| Chalice hardcoded X=1 | `engine/cast_manager.py:750-791` | ✅ RESOLVED. X is picked by oracle-driven net-disruption formula (opponent_count minus own_count for each CMC); fallback to 1 only if no candidates (line 791). Confirmed in Tron matchup trace (L4). |
| First-strike missing from AI combat sim | `ai/turn_planner.py::_simulate_combat:483-494` | ✅ STALE. `_simulate_combat` uses two sequential loops (first strike, then regular damage). CLAUDE.md P1 line 289 can be closed. (L-combat) |

## Bugs found — by layer

### Layer 1 — Engine rules bugs

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 1 | **LE-E1** | engine does not implement Grafdigger's Cage | Searching shows zero references to the card's continuous effect ("creatures can't enter the battlefield from graveyards or be cast from graveyards"). If opp's Karn the Great Creator tutors Cage from sideboard, the Living End resolver at `engine/spell_resolution.py:235` silently runs anyway — returns creatures in violation of rules. | <1 pp (games don't usually reach T6 Cage resolution before LE dies) |
| 2 | **LE-E2** (partial) | `engine/cast_manager.py:91-93` | Suspend-only cards (Violent Outburst, CMC 0 + SUSPEND keyword) are blocked from hand cast, but the engine does not track suspend counters or resolve them on upkeep. Living End's second-best cascade enabler (Violent Outburst) is therefore non-functional; only Shardless Agent and Demonic Dread work today. | 1-2 pp |

### Layer 2 — AI cascade EV + graveyard asymmetry

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 3 | **LE-A1** | `ai/ev_evaluator.py:1016-1262` (`_project_spell`) | Cascade spells (Shardless Agent, Violent Outburst) are scored as vanilla 3-mana creatures. The projection does NOT model "cascade hits Living End → massive board reset". The `_free_cast_opportunity` flag at `engine/cast_manager.py:431` is set post-resolution — too late for the cascade-candidate decision. | 2-3 pp |
| 4 | **LE-A2** | `ai/ev_evaluator.py::EVSnapshot:32-82` — has `my_gy_creatures` but NO `opp_gy_creatures` | Living End returns ALL graveyards' creatures symmetrically. EV scoring at `ai/ev_evaluator.py:1118-1136` only credits `me.graveyard`. Against Dimir with 3 discarded creatures (Bowmasters, Ragavan, random), AI would cascade thinking "+2 power for me" when reality is "-1 net (opp gains 3, I gain 2)". | 2-3 pp |
| 5 | **LE-A3** | `ai/ev_player.py:440-442` (cascade +1.5 free-cast bonus) vs `ai/ev_player.py:850-876` (Storm patience gate from PR #142) | Cascade gets unconditional +1.5 free-cast bonus. Storm has a conditional gate that clamps mid-chain rituals below pass_threshold when finisher access is exhausted. Living End has no equivalent: it cascades even when (a) graveyard is thin, (b) opp has cleanup removal ready, (c) returned board would be summoning-sick through opp's killing turn. | 3-4 pp |

### Layer 3 — AI turn planning + combat

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 6 | **LE-T1** | `ai/turn_planner.py:1085` (`to_virtual_creature`) | VirtualBoard sets `is_tapped=card.tapped` — **does not check `card.summoning_sick`**. `plan_attack()` at line 203-204 filters by `not c.is_tapped`, so summoning-sick creatures appear as valid attackers in strategic planning. The game engine itself correctly respects summoning sickness in `decide_attackers()` → `get_valid_attackers()` → `can_attack()`, so actual gameplay is correct. But the AI's race evaluation is off-by-one on EVERY cascade / reanimation turn. **This is the single highest-leverage bug in this diagnostic — one-line fix.** | 4-6 pp (likely most of the 24% → 30%+ move) |
| 7 | **LE-T2** | `ai/ev_player.py:1627` (comment) | Comment says "creatures came back with summoning sickness gone"; this is wrong. Cascaded creatures enter WITH summoning sickness. Comment needs to be corrected to match engine behavior. | 0 pp (comment hygiene, but flag as doc bug) |

### Layer 4 — Gameplan + mulligan

| # | ID | File:line | Description | Est. impact |
|---|---|---|---|---|
| 8 | **LE-G1** | `decks/gameplans/living_end.json::critical_pieces` | Missing "Violent Outburst". The JSON lists Demonic Dread and Shardless Agent, but Violent Outburst (suspend cascade) is a distinct strategic line (faster kill via T2-suspend → T3-resolve). | 1-2 pp (but compounds with LE-E2 engine suspend gap — fix both or neither) |
| 9 | **LE-G2** | `ai/clock.py::combo_clock:117-144` | Living End archetype=combo uses the 8-resource-assembly model from Ruby Storm / Amulet Titan. Reality: LE needs 3 mana + ~3 GY creatures + cascade spell = 6 resource points. Returns ~5-turn kill estimate when actual is ~3 turns. Same class of bug as **Amulet O1** (2026-04-23 findings). | 1-2 pp |
| 10 | **LE-G3** | `ai/gameplan.py:510-529` (half-target fallback) | Living End's FILL_RESOURCE goal has `resource_target: 2`. The half-target fallback at line 524 advances to EXECUTE_PAYOFF after `self.turns_in_goal >= 2 and resource_progress >= half_target=1`. Seed 50500 trace (L1) showed cascade firing with 2 GY creatures, returning 0. Raise `resource_target` to 3 or 4 in the JSON. | 2-3 pp (config-only fix) |
| 11 | **LE-G4** | `ai/gameplan.py:486-489` (DISRUPT goal auto-advance) | Opponent-side finding: Dimir's DISRUPT goal auto-advances after `turns_in_goal >= 2` regardless of whether disruption has landed. Against LE (T4-5 combo), DISRUPT truncates before Thoughtseize fires on the cascade enabler. Not Living End's bug; a defender-side bug that compounds with LE overperformance. | not LE; cross-deck |

### Layer 5 — Cross-fix interactions (from Affinity fixes we just merged)

| Fix | Effect on Living End |
|---|---|
| **E2 Thoughtseize** (merged #143) | Dimir / Thoughtseize decks now strip cascade enablers instead of cyclers → **hurts Living End** (structural) |
| **E3 Evoke gate** (merged #146) | Jeskai Solitude evoke now fires post-cascade → **hurts Living End** (cleaner board wipes) |
| **Bundle 3 holdback** (merged #149) | Cycling holdback fires when holding instants. If Living End's SB has Force of Negation, cycling Street Wraith may be suppressed → **slight hurt Living End** in SB games |
| **R1 carrier-pool** (merged #145) | No direct effect (Living End isn't an equipment deck) |
| **R2 prefers cheaper counter** (merged #148) | Jeskai / control decks fire counters more efficiently against the cascade → **hurts Living End** |
| **R3 equip-carrier priority** (merged #144) | No direct effect |

Net expected effect on Living End WR post-Affinity-fixes: −2 to −5 pp (deck gets worse before it gets better). LE WR at 24% could drop to 19-22% until Living End's own fixes land.

## Recommended fix ordering

Each bundle is a separate branch + PR, Option C (failing test first).

**Bundle LE-1 — Combat planner one-line fix (highest-leverage, safest)**
1. **LE-T1**: `ai/turn_planner.py:1085` — add summoning-sickness check to `is_tapped=card.tapped or card.summoning_sick,`. Single-line change. Estimated +4-6 pp for Living End (and positive for any reanimator/cascade deck). Test: VirtualBoard `plan_attack` with summoning-sick creature should exclude it from attackers.

**Bundle LE-2 — Gameplan config tuning (fast, low-risk)**
2. **LE-G3**: raise `living_end.json::goals[0].resource_target` from 2 to 3 (or 4). Config-only.
3. **LE-G1**: add "Violent Outburst" to `living_end.json::critical_pieces`. Config-only.
4. **LE-T2**: fix the stale comment at `ai/ev_player.py:1627`.

**Bundle LE-3 — AI scoring refactor (medium complexity)**
5. **LE-A1**: extend `_project_spell` for cascade spells to include expected cascade hit. Requires modeling "what card does this cascade hit?" — probabilistic. Use the deck list + exile + library constraints.
6. **LE-A2**: add `opp_gy_creatures: int` to `EVSnapshot`. Update `snapshot_from_game`. Use in reanimation-tag projection at `ai/ev_evaluator.py:1118-1136` — subtract opp's expected return from my delta.
7. **LE-A3**: add Living End patience gate in `_score_spell` for cascade spells (mirror the Storm gate from PR #142). Gate fires when: cascade in hand, opp has removal available, own GY < some threshold, own SS creatures can't block opp's next turn lethal.

**Bundle LE-4 — Engine gaps (deferred, separate effort)**
8. **LE-G2**: adjust `combo_clock` for Living End archetype. Requires per-deck override or archetype sub-type.
9. **LE-E2**: implement suspend counter tracking + upkeep resolution. Non-trivial engine work; may defer.
10. **LE-E1**: implement Grafdigger's Cage continuous effect. Low impact, low priority.

## Non-goals

- Don't add Living End-specific special cases to `_score_spell`. The patience
  gate should be generic ("cascade spells with no finisher path"),
  applicable to any cascade deck (future Cascade Zenith etc.).
- Don't tune individual card EV values. Derive from `ai/clock.py`, `ai/bhi.py`,
  oracle text.

## Expected net effect

Rough math:
- Bundle LE-1 alone (summoning-sickness fix): +4-6 pp (24% → 28-30%)
- Bundle LE-1 + LE-2: +6-9 pp (24% → 30-33%)
- All bundles: +10-15 pp (24% → 34-39%)

Target for Living End: **30-40% flat WR** (mid-tier combo, acceptable).

## Scope note

Fixing LE-T1 (summoning-sickness in VirtualBoard) also benefits:
- Goryo's Vengeance (reanimator, 24% WR in previous matrix)
- Ruby Storm (when Empty the Warrens tokens enter via Wish)
- Any deck that creates tokens or reanimates mid-combat

So this is a cross-deck fix with benefits beyond the 24% Living End outlier.
