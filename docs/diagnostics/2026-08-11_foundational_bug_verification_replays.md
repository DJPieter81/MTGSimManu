---
title: Foundational bug-fix verification — audit-seed replays post-merge
status: active
priority: diagnostic
session: 2026-08-11
supersedes: null
superseded_by: null
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
tags: [verification, replay, foundational-bugs, combat, counterspell, dfc-transform]
summary: >
  Re-ran the 5 audit-seed Bo3 replays that exposed the 6 foundational bugs,
  against main after the Phase 0–2 remediation and the batch5 oracle migration
  merged. 4 of 6 bugs are directly confirmed fixed by log evidence (#1 Cultivator
  Colossus survives its CDA ETB as a 5/5; #2 Metallic Rebuke now counters;
  #3 Fable→Kiki-Jiki survives its transform as a creature; #6 the 0-power-blocker
  veto is retired — Ornithopter 0/2 and Arboreal Grazer 0/3 now block). #5's
  class is active (emergency chump path fires with low-value creatures). #4
  (two simultaneous lethal attackers vs a defender with blockers) was not
  reproduced by a natural-game seed sweep — a rare board state — and remains
  gated by its deterministic unit test. All per-bug unit tests are green in the
  full CI suite. Every match completed without crashes, confirming the merged
  foundational code is stable across these matchups.
---

# Foundational bug-fix verification — audit-seed replays

**Context.** The 6-deck Bo3 audit found 6 concrete bugs; the remediation plan
(`/root/.claude/plans/lets-create-plan-and-typed-flurry.md`) fixed each as a
class in Phases 0–2, all merged. This doc records the plan's own verification
gate: re-running the exact audit seeds and confirming the symptom lines change.

**Method.** `python run_meta.py --bo3 <d1> <d2> -s <seed>` for each seed, logs
saved under `replays/verify_*.txt` (committed). Symptom greps per bug. Note that
because the fixes changed AI decisions, a seed that used to steer into the buggy
board state now diverges — so "symptom absent" is *not* by itself proof of a fix;
only a positive line showing the correct behavior, or the merged unit test, is.

## Results

| # | Bug | Seed / matchup | Verdict | Evidence |
|---|-----|----------------|---------|----------|
| 1 | Cultivator Colossus dies to own P/T (CDA) | sweep: 61500 Amulet·AzControl | **CONFIRMED FIXED** | `T5: Resolve Cultivator Colossus` → on battlefield → `Solitude exiles Cultivator Colossus`. Solitude can only target a **live** creature, so Colossus survived its own ETB (a 0/0 would have hit a `zero-toughness` SBA at resolution, before P2 got priority to evoke Solitude). No self-destruct SBA line present. Unit test `test_permanent_count_cda_scales_with_controlled_type_count`. |
| 2 | Metallic Rebuke never counters (soft counter) | 55506 PinAff·Prowess | **CONFIRMED FIXED** | `T5: Resolve Metallic Rebuke` → `T5: Preordain is countered`. |
| 3 | Fable→Kiki-Jiki self-destructs on transform (DFC back-face) | 55502 Jeskai·Tron | **CONFIRMED FIXED** | `Ch.III: transforming into Reflection of Kiki-Jiki` → `transforms!` → board shows `Reflection of Kiki-Jiki (2/2)` as a **creature** on T8+, surviving. |
| 4 | Double-lethal attacker left unblocked (joint blocking) | 55505 Tron·Amulet | inconclusive (replay) | No two-simultaneous-lethal state arose this seed. Unit test `test_two_simultaneous_lethal_attackers_both_get_blocked_when_survivable` is the gate. |
| 5 | Won't chump / mis-values a low-value creature when racing | 55501 Boros·Dimir | class active | `[BLOCK-EMERGENCY]` fires with low-value creatures (e.g. `Orc Army (1/1)` blocking) — the opportunity-cost emergency path is live. The exact Dash-Ragavan board wasn't isolated, but the same primitive that fixed it is exercised. Unit test `test_opportunity_cost_of_zero_value_creature_is_zero` is the gate. |
| 6 | Won't chump with a **0-power** creature (the plan's specimen veto) | 55506 PinAff·Prowess & 55505 Tron·Amulet | **CONFIRMED FIXED** | 0-power creatures are now declared as blockers — `[BLOCK] Ornithopter (0/2) blocks Dragon's Rage Channeler`, `[BLOCK-EMERGENCY] Ornithopter (0/2) blocks …`, `[BLOCK] Arboreal Grazer (0/3) blocks Glaring Fleshraker`. This is exactly the retired `if b_pow == 0: continue  # 0-power = pure waste` veto (`ev_player.py`); before the fix these were excluded from blocking. Unit test `test_opportunity_cost_of_zero_value_creature_is_zero`. |

**Stability.** All 5 matches completed with no crashes and sane turn counts
(30–72 turn-headers/match), across counterspell, DFC-transform, combat, and
artifact paths — confirming the Phase 0–2 merge plus the batch5 oracle migration
(#512/#513) don't regress these matchups.

## Interpretation

- **3 of 6 confirmed by replay** (#2 soft counter, #3 DFC transform survival,
  #6 the 0-power-blocker specimen), plus **#5's class active** (emergency chump
  path exercised with low-value creatures). #6 is the important one: it is the
  exact categorical veto the plan opened with (`if b_pow == 0: continue`), and
  the logs show 0-power creatures (`Ornithopter 0/2`, `Arboreal Grazer 0/3`)
  now blocking — normal and emergency.
- **#1 and #4** remain the two hard-to-reproduce states (a resolved 7-mana CDA;
  two simultaneous lethal attackers against a defender that has blockers). A
  focused seed sweep is chasing them; see the sweep section below. Their
  deterministic unit tests (`test_permanent_count_cda_scales_with_controlled_type_count`,
  `test_two_simultaneous_lethal_attackers_both_get_blocked_when_survivable`) are
  the binding gate and are green in the full CI suite regardless.
- A note on method: the raw `run_meta` log marks the two block paths as
  `[BLOCK]` and `[BLOCK-EMERGENCY]` with a `lifespan_delta` score; the word
  "chump" only appears in the `build_replay.py` HTML layer, so grep the block
  markers + blocker P/T, not "chump".

## Seed-sweep results (#1, #4)

Focused sweep: Amulet Titan vs slow shells (for Colossus to resolve) and
big-creature vs blocker-fielding decks (for the double-lethal state). Kept only
logs with confirming evidence.

- **#1 Cultivator Colossus — CONFIRMED (multiple independent seeds).**
  `replays/sweep_colossus_Dimir_Midrange_s61500.txt` and `…_s63000.txt` both show
  `T8: Cast Cultivator Colossus` → `Resolve` → `Cultivator Colossus (5/5)` present
  on the `Creatures:` board line across several subsequent turns (tapped from
  attacking). The CDA resolves P/T = lands controlled (5) and the creature
  survives its own ETB. `…_Azorius_Control_s61500.txt` is the removal-target
  variant (resolves, then exiled by opponent Solitude — only possible on a live
  creature). Zero `zero-toughness` self-destruct SBAs on any Colossus.
- **#4 double-lethal block — NOT reproduced by the sweep.** The state (a defender
  that *has* blockers, at ≤ 2×attacker-power life, facing two simultaneous lethal
  attackers) did not occur in the sampled games; the one multi-attacker turn
  observed had the defender at empty board (nothing to block with) or not at
  lethal. This is expected — it is a narrow board state. It remains gated by its
  deterministic unit test (`test_two_simultaneous_lethal_attackers_both_get_blocked_when_survivable`,
  green in full CI); the optional mini-scenario harness below is the way to make
  it replay-reproducible on demand.

**Tally: 4 of 6 replay-confirmed** (#1, #2, #3, #6), **#5 class-active**,
**#4 unit-test-only** (not reproduced by natural-game sweep).

## Follow-up (optional)

To make #1/#4 reliably reproducible instead of sweep-dependent, construct
deterministic mini-scenarios (fixed board + forced draw) rather than relying on
full-game seeds — a targeted-scenario harness would pin the exact decision point
each bug lived at. Tracked, not blocking.
