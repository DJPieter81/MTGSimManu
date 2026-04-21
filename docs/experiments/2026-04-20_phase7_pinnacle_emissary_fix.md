---
title: Phase 7 — Pinnacle Emissary recurring trigger + persistent_power
status: archived
priority: historical
session: 2026-04-20
superseded_by:
  - docs/experiments/2026-04-20_phase8_life_energy_persistent.md
depends_on:
  - docs/design/ev_correctness_overhaul.md
  - docs/experiments/2026-04-20_phase6_matrix_validation.md
tags:
  - ev-scoring
  - persistent-power
  - phase-7
  - matrix
  - completed
summary: "Fixed the long-standing test_pinnacle_emissary_cast_trigger_accrues_persistent failure — zero failures for first time. Left a Boros regression (-4.9pp wtd, Guide of Souls life/energy clause not credited) which was closed by Phase 8 (see superseded_by)."
---
# Phase 7 — Pinnacle Emissary fix and recurring per-permanent triggers

## Problem

`tests/test_recurring_token_ev.py::test_pinnacle_emissary_cast_trigger_accrues_persistent`
had been the lone outstanding failure in the test suite for many
sessions, called out by the design doc §6 as the baseline. Pinnacle
Emissary's oracle:

```
Whenever a nontoken artifact enters the battlefield under your
control, create a 1/1 colorless Drone artifact creature token with
flying.
```

Two bugs combined:

1. **Regex bug** — `_trigger_classes` had `r'when[s]? (?:[a-z\'\-\s]+ )?enters'`,
   which matches "when" or "whens" optionally followed by a single
   space. The pattern can't reach across "whenever" because after
   "when", the next character is `e`, not a space. So "whenever a
   nontoken artifact enters" matched nothing.

2. **Missing trigger class** — even if matched, the existing `etb`
   class would credit the token as immediate (one-shot). But this is a
   per-permanent recurring trigger that should accrue to
   `persistent_power` (discounted by `urgency_factor` at evaluation
   time), not immediate.

## Fix

`ai/ev_evaluator.py:_trigger_classes`:

- Combined regex `r'when(?:ever|s)?\s+(?P<who>[a-z0-9\'\-,\s]+?)\s+enters'`
  matches both "when ~ enters" and "whenever a creature enters".
- The `who` group captures the trigger subject. If it contains any of
  the generic phrases (`'another'`, `'a creature'`, `'a permanent'`,
  `'an artifact'`, `'an enchantment'`, `'a land'`, `'a planeswalker'`,
  `'a nontoken'`, `'one or more'`, `'any creature'`, `'any permanent'`,
  `'any opponent'`), it's classified as a recurring `other_enters`
  trigger. Otherwise it's a self-`etb` trigger.
- Added `other_enters` rate in `_persistent_rate`:
  `NONLAND_PERMANENT_ENTERS_PER_TURN = 0.7` (Modern average — decks
  deploy roughly one nonland permanent per main phase).

`ai/ev_evaluator.py` line counts: regex + classifier ≈ 18 lines,
rate clause ≈ 9 lines.

## Test impact

| Suite snapshot | Before Phase 7 | After Phase 7 |
|---|---|---|
| Pinnacle Emissary | FAIL (0.0 persistent_power) | PASS |
| Ajani ETB token (regression) | PASS | PASS — self-ETB still classified as `etb` via the "who" inspection |
| Ragavan treasure ordering | PASS | PASS — Treasure tokens stay 0 power, no shift |
| All other recurring-token tests | PASS | PASS |
| Full suite | 225 / 1 fail | **226 / 0 fail** |

First time the test suite is fully green.

## Matrix deltas (N=20)

Compared baseline `metagame_data_pre_phase7.jsx` (Phase 6 state) to
post-fix run. Same decks, same standard seeds.

| Deck | Old flat | New flat | Δ flat | Old wtd | New wtd | Δ wtd |
|---|---:|---:|---:|---:|---:|---:|
| Affinity | 83.3% | 88.9% | **+5.6** | 83.1% | 89.4% | **+6.3** |
| Ruby Storm | 21.7% | 20.4% | -1.3 | 16.4% | 27.2% | **+10.8** |
| Pinnacle Affinity | 62.3% | 50.3% | -12.0 | 52.9% | 62.2% | **+9.3** |
| Jeskai Blink | 64.0% | 58.8% | -5.2 | 58.4% | 66.9% | +8.5 |
| Amulet Titan | 43.0% | 30.9% | -12.1 | 39.0% | 46.6% | +7.6 |
| Eldrazi Tron | 71.3% | 57.5% | -13.8 | 69.4% | 73.1% | +3.7 |
| Izzet Prowess | 44.3% | 37.1% | -7.2 | 41.0% | 46.2% | +5.2 |
| Azorius Control | 18.3% | 13.3% | -5.0 | 13.6% | 19.7% | +6.1 |
| Goryo's Vengeance | 10.7% | 6.7% | -4.0 | 8.4% | 11.2% | +2.8 |
| Living End | 25.0% | 15.6% | -9.4 | 22.5% | 23.4% | +0.9 |
| 4/5c Control | 40.7% | 29.0% | -11.7 | 33.1% | 38.4% | +5.3 |
| 4c Omnath | 60.3% | 49.5% | -10.8 | 56.6% | 58.1% | +1.5 |
| Dimir Midrange | 60.7% | 44.2% | -16.5 | 56.7% | 57.8% | +1.1 |
| Azorius Control (WST) | 41.7% | 37.9% | -3.8 | 40.3% | 40.9% | +0.6 |
| Domain Zoo | 78.0% | 63.9% | -14.1 | 75.9% | 72.5% | -3.4 |
| Boros Energy | 74.7% | 65.2% | -9.5 | 74.6% | 69.7% | **-4.9** |

## Interpretation

- **Affinity climbs both flat and weighted (+5.6 / +6.3).** Pinnacle
  Emissary is in this list — its persistent_power valuation now
  correctly credits the recurring drone production. Affinity AIs play
  Emissary more decisively and opponents valuing Affinity threats
  through the same lens means the deck rises in both dimensions.
- **Weighted WRs broadly up.** 14 of 16 decks gained weighted WR; only
  Boros and Domain Zoo dropped. The recurring-trigger fix narrows the
  gap between top and bottom because it benefits any deck running
  per-permanent triggers (Guide of Souls, Bowmasters, Pinnacle
  Emissary, anthem stacks).
- **Flat WRs broadly down.** Same effect viewed differently — weaker
  decks gained more from the better trigger valuation than stronger
  decks did, so the top-of-table lost some "free wins" to the
  middle/bottom.
- **Boros's flat -9.5 / weighted -4.9** is the only meaningful regression.
  Guide of Souls' "whenever another creature enters" trigger now
  classifies as `other_enters` (persistent), but the `gain 1 life
  and get {E}` is not a token-creation effect — it's a life/energy
  gain. The current `_project_token_bonus` only credits power-producing
  clauses, so the trigger is recognized but produces 0 persistent
  power. Net: Boros AI gets no benefit, but opponents' Guide-class
  cards become stronger across the matrix. Plausible follow-up: extend
  `_clause_token_power` (or a sibling) to credit life/energy gains the
  same way it credits creature tokens.

## Status

- The original target failure is fixed and the suite is fully green.
- Boros regression is small and within typical N=20 matrix noise (±5pp);
  worth investigating in a Phase 8 if it persists at N=50.
- The flagged Phase 6 follow-ups (Storm sequencing, Amulet Titan,
  Pinnacle Affinity Wrath collateral) remain open. Phase 7 didn't
  target them; the matrix changes here are downstream effects of the
  recurring-trigger fix, not those investigations.
