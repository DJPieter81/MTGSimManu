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
  merged. 2 of 6 bugs are directly confirmed fixed by log evidence (Metallic
  Rebuke now counters; Fable→Kiki-Jiki survives its transform as a creature).
  The other 4 are inconclusive-by-replay because the merged fixes changed the
  AI's plays, so the seeds no longer reproduce the exact board states that
  exposed them — their deterministic per-bug unit tests remain the binding gate
  and are green in the full CI suite. All 5 matches complete without crashes,
  confirming the merged foundational code is stable across these matchups.
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
| 1 | Cultivator Colossus dies to own P/T (CDA) | 55505 Tron·Amulet | inconclusive (replay) | Colossus never cast — games ended T7, Amulet lost 2-0. Unit test `test_permanent_count_cda_scales_with_controlled_type_count` is the gate. |
| 2 | Metallic Rebuke never counters (soft counter) | 55506 PinAff·Prowess | **CONFIRMED FIXED** | `T5: Resolve Metallic Rebuke` → `T5: Preordain is countered`. |
| 3 | Fable→Kiki-Jiki self-destructs on transform (DFC back-face) | 55502 Jeskai·Tron | **CONFIRMED FIXED** | `Ch.III: transforming into Reflection of Kiki-Jiki` → `transforms!` → board shows `Reflection of Kiki-Jiki (2/2)` as a **creature** on T8+, surviving. |
| 4 | Double-lethal attacker left unblocked (joint blocking) | 55505 Tron·Amulet | inconclusive (replay) | No two-simultaneous-lethal state arose this seed. Unit test `test_two_simultaneous_lethal_attackers_both_get_blocked_when_survivable` is the gate. |
| 5 | Won't chump / mis-values Dash-Ragavan when racing | 55501 Boros·Dimir | inconclusive (replay) | Ragavan cast and attacking; no forced chump decision exercised. Unit test `test_opportunity_cost_of_zero_value_creature_is_zero` is the gate. |
| 6 | Won't chump with a dead Ornithopter | 55504 Affinity·Omnath | inconclusive (replay) | Affinity lost 2-0 fast (T30 total headers across match); no Ornithopter block decision reached. Same unit test as #5. |

**Stability.** All 5 matches completed with no crashes and sane turn counts
(30–72 turn-headers/match), across counterspell, DFC-transform, combat, and
artifact paths — confirming the Phase 0–2 merge plus the batch5 oracle migration
(#512/#513) don't regress these matchups.

## Interpretation

- Bugs **#2 and #3** are the strongest signal available: a positive log line
  showing the corrected mechanic, on the very seed that exposed the failure.
- Bugs **#1, #4, #5, #6** need either the audit's original per-turn game states
  (not preserved) or, better, their deterministic unit tests — which is exactly
  what the plan mandated ("failing test first, rule-phrased") and what CI now
  runs green in the full suite. Replay divergence here is consistent with the
  fixes working (the AI no longer walks into the losing line), but is not itself
  proof; the unit tests are.

## Follow-up (optional)

To convert #1/#4/#5/#6 from inconclusive to replay-confirmed, construct
deterministic mini-scenarios (fixed board + forced draw) rather than relying on
full-game seeds — a targeted-scenario harness would pin the exact decision point
each bug lived at. Tracked, not blocking.
