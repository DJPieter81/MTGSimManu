---
title: Phase 8 — life / energy persistent_power for recurring triggers
status: archived
priority: historical
session: 2026-04-20
depends_on:
  - docs/experiments/2026-04-20_phase7_pinnacle_emissary_fix.md
tags:
  - ev-scoring
  - persistent-power
  - phase-8
  - matrix
  - completed
summary: "Closed the Phase 7 follow-up: Guide of Souls' life/energy clause now accrues persistent_power. Boros regression (-4.9pp wtd in Phase 7) resolved (+5.0pp wtd in Phase 8). Suite 227/227."
---
# Phase 8 — Life / energy gain persistent valuation

## Problem

After Phase 7 fixed Pinnacle Emissary, Boros Energy lost 4.9pp
weighted WR. The asymmetry: Guide of Souls' "Whenever another
creature you control enters, you gain 1 life and get {E}" was now
correctly classified as `other_enters` (recurring trigger) but
produced 0 persistent_power because `_clause_token_power` only
credits creature tokens / amass, not life or energy gain.

So opponents of Boros valued THEIR Guide-class engines correctly via
the Phase 7 fix, but Boros's own evaluation of Guide stayed at the
old level — net negative for Boros.

## Fix

`ai/ev_evaluator.py:_project_token_bonus`:

- New helpers `_clause_life_gain` / `_clause_energy_gain` parse
  `gain N life`, `get {E}`, `get N {E}`, `get N energy`.
- New rules constants:
  - `LIFE_TO_POWER_EQUIVALENT = 0.25` — derived from
    `life_as_resource(life, opp_power) = life / (opp_power × 4)`
    in `ai/clock.py`. At opp_power=3 (Modern average), 1 life ≈
    1/12 power-equivalent; rounded up to 0.25 because life also
    buffers against burn / non-combat damage which clock_diff
    doesn't model.
  - `ENERGY_TO_POWER_EQUIVALENT = 0.5` — Guide's activation costs
    {E}{E}{E} for +2/+2 + flying ≈ 3 power, so 1 energy ≈ 1.0
    power-equivalent; capped at 0.5 to avoid over-crediting decks
    that produce energy without finishers.
- Loop now visits any clause with `create` / `amass` / `gain N life`
  / `get {E}`. Self-ETB life/energy is intentionally NOT credited
  in the persistent path — `_project_spell` already applies a flat
  `my_life += 3` heuristic for ETB life-gain, and double-counting
  would inflate Omnath / Thragtusk / Phlage.
- The function's call gate widened from `'token_maker' in tags` to
  also fire on creatures whose oracle has `'whenever'` or `'{e}'` —
  catches Guide of Souls (no token_maker tag) and similar
  energy/life recurring engines.

## Test impact

| Test | Before Phase 8 | After Phase 8 |
|---|---|---|
| `test_guide_of_souls_creature_enters_accrues_persistent` (new) | FAIL (0.0) | PASS |
| Pinnacle Emissary (Phase 7) | PASS | PASS |
| All other recurring-token tests | PASS | PASS |
| **Full suite** | **226/226** | **227/227** |

## Matrix deltas (N=20, vs Phase 7 baseline)

The headline target — Boros recovery — happened.

| Deck | Old flat | New flat | Δ flat | Old wtd | New wtd | Δ wtd |
|---|---:|---:|---:|---:|---:|---:|
| **Boros Energy** | 69.7% | 71.3% | **+1.6** | 67.8% | 72.8% | **+5.0** |
| Pinnacle Affinity | 62.3% | 54.3% | -8.0 | 54.1% | 64.4% | +10.3 |
| 4/5c Control | 38.3% | 30.9% | -7.4 | 31.9% | 42.2% | +10.3 |
| Living End | 23.0% | 17.4% | -5.6 | 19.8% | 27.8% | +8.0 |
| Dimir Midrange | 57.0% | 51.3% | -5.7 | 53.2% | 60.9% | +7.7 |
| Eldrazi Tron | 71.3% | 57.3% | -14.0 | 67.7% | 74.4% | +6.7 |
| Azorius Control | 18.3% | 12.6% | -5.7 | 13.2% | 18.8% | +5.6 |
| 4c Omnath | 57.3% | 44.5% | -12.8 | 55.2% | 60.0% | +4.8 |
| Amulet Titan | 46.0% | 31.7% | -14.3 | 42.3% | 46.9% | +4.6 |
| Izzet Prowess | 47.7% | 38.2% | -9.5 | 43.1% | 45.3% | +2.2 |
| Goryo's Vengeance | 11.3% | 6.3% | -5.0 | 7.8% | 8.8% | +1.0 |
| Ruby Storm | 28.7% | 18.2% | -10.5 | 24.3% | 25.0% | +0.7 |
| Domain Zoo | 72.0% | 60.5% | -11.5 | 69.9% | 69.7% | -0.2 |
| Jeskai Blink | 67.0% | 51.5% | -15.5 | 65.5% | 63.8% | -1.7 |
| Affinity | 89.0% | 82.2% | -6.8 | 89.4% | 87.5% | -1.9 |
| Azorius Control (WST) | 41.0% | 35.5% | -5.5 | 41.9% | 38.8% | -3.1 |

12 of 16 decks gained weighted WR. Boros's regression from Phase 7
fully reversed and then some.

## Interpretation

- **Flat WRs again broadly down, weighted up.** Same balancing trend
  as Phase 7. Decks that aren't running Guide-class engines (Affinity,
  Jeskai Blink, Domain Zoo) lose marginal flat WR because opponents'
  evaluation of THEIR engines is sharper. Decks running such engines
  (Boros, Living End via Curator, Dimir via Bowmasters) gain.
- **Affinity flat -6.8.** The Phase 7 lift came partly from Pinnacle
  Emissary; with Phase 8 raising opponent's Guide-class valuation,
  some of that lift normalizes. Still a top-tier deck (82.2% flat).
- **Eldrazi Tron flat -14 / wtd +6.7.** The largest split. Tron's
  flat WR was inflated by opponents undervaluing its threats; with
  better recurring-trigger valuation across the field, Tron's
  matchups normalize but its meta-share weighting stays favourable.

## Open follow-ups (not blockers)

- Self-ETB life-gain still uses the flat `my_life += 3` heuristic in
  `_project_spell`. Could be derived from oracle parsing for accuracy,
  but no current failing test demands this — out of scope for Phase 8.
- Storm / Amulet Titan / Pinnacle Affinity sequencing concerns from
  Phase 6 remain open. Phase 8's lifts (+10.3 wtd Pinnacle, +0.7 wtd
  Storm) help but don't close the gap.
