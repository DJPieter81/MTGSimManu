---
title: Phase D — fourth attempt failure (Sprint 2 wire-up)
status: active
priority: primary
session: 2026-04-27
supersedes: []
superseded_by: []
depends_on: [docs/PHASE_D_DEFERRED.md]
tags: [phase-d, simulator, combo-evaluator, loop-break]
summary: |
  Fourth Phase D migration attempt collapsed Storm field N=10
  from ~41% to 0.6%.  Same root cause as the prior three:
  simulator-derived `expected_damage = 0` when no closer is in
  hand, leading the chain-fuel scorer to return 0 for build-toward-
  closer plays (rituals, cantrips, tutors).  Multi-turn helpers
  (`best_turn_damage`, `chain_lethal_turn`) didn't fix this
  because they walk projections that are themselves all 0 in the
  pre-closer state.  Loop-break protocol triggered: halting code
  on the simulator-replacement direction; documenting the exact
  EV-divergence point.
---

# Phase D fourth attempt — Sprint 2 wire-up reverted

## Loop-break compliance

Per `CLAUDE.md` ABSTRACTION CONTRACT, three consecutive commits
on the same outlier without WR movement triggers halt + Bo3
root-cause + docs/ name-the-subsystem.  This is the **fourth**
attempt; we are well past that threshold.  This document IS the
named-the-subsystem step.  No further code on the simulator-as-
replacement direction without addressing what's named below.

## What was tried (Sprint 2)

A background agent on `claude/sprint-2-wire-up` rewrote
`ai/combo_evaluator.py` to use the multi-turn helpers shipped in
PR #203:

```python
lethal_turn = chain_lethal_turn(baseline_proj, opp_life)
best_value, best_turn = best_turn_damage(baseline_proj)

if lethal_turn == 0:                # lethal THIS turn
    fire NOW — chain_credit > 0
elif lethal_turn is not None:       # lethal at future turn
    HOLD — chain fuel scores 0
elif best_turn == 0:                # this turn highest EV
    fire chip damage
else:                               # future turn higher EV
    HOLD
```

Failing-test-first compliance: 4 unit tests written, all
pass.  Abstraction ratchet: still 7 (no new violations).
Storm field N=10 gate: **0.6%** — collapse.

## EV-divergence point (Bo3 root cause)

Ran `python run_meta.py --bo3 "Ruby Storm" "Affinity" -s 50000`
on the working baseline (Storm 37.5%) and compared against the
new evaluator's reasoning.  The divergence is **immediate, on
turn 1's first main-phase decision**.

In the baseline, Storm with hand `[Pyretic Ritual, Manamorphose,
Reckless Impulse, Wish, Wrenn's Resolve]`:
* `card_combo_modifier` returns small POSITIVE EV for each
  ritual / cantrip / tutor — encoding "build toward future
  chain via Wish→tutor".
* AI casts Reckless Impulse (cantrip, draws 2) → builds chain
  fuel → eventually Wishes for Past in Flames or Grapeshot.

In the new evaluator (Sprint 2), same hand:
* `simulate_finisher_chain` finds NO chain (no closer in hand;
  Wish's SB target invisible to the simulator).
* `expected_damage = 0`, `chain_lethal_turn = None`,
  `best_turn_damage = (0, 0)`.
* Branch 3 fires: "no lethal projected, this turn is highest EV
  → fire chip damage".  But there's NO damage to fire — chain
  is empty.
* `chain_credit = (0 / opp_life) * combo_value * relevance = 0`.
* AI uses default scoring (no combo nudge); rituals score below
  pass_threshold; AI passes the turn doing nothing.

The chain extends: every subsequent turn, hand grows but no
closer ever lands in hand (closer is in SB).  Storm passes
forever.  Affinity races.  Storm wins ~0%.

## Responsible subsystem

**`ai/finisher_simulator.py:_project_storm`**, specifically the
`expected_damage = 0 when payoff_names is empty` invariant.

The simulator answers: "what damage CAN I deal this turn?"
With no closer in hand, the answer is 0.  But Storm's actual
intent is: "build chain THIS turn, fetch closer NEXT turn via
Wish→SB lookup".  The simulator has no model of that intent.

What's needed:

* **Library composition modelling**: the simulator must know
  the deck's contents (mainboard + sideboard).  When a tutor is
  in hand, the simulator should project "tutor fetches closer
  → chain damage = (storm_count + tutor_bonus) × ..." even
  though the closer isn't yet in hand.  This is what
  `card_combo_modifier`'s `_tutor_has_payoff` branch
  (`combo_calc.py:670-695`) encodes; the simulator doesn't.

* **Multi-turn intent modelling**: even WITHOUT a tutor, Storm
  builds chains across turns.  The simulator's
  `next_turn_proj` increments mana but uses the SAME hand —
  it can't represent "draw a Grapeshot next turn off a
  cantrip."  Probabilistic library composition (P(draw Grapeshot
  in N turns | Grapeshot count in deck)) is the principled
  fix.

Without these two upgrades, the simulator-driven evaluator will
keep collapsing chain-fuel scores to 0, and Storm will keep
passing turns.

## Recommended path forward

**Stop attempting full migration of `card_combo_modifier`.** The
hand-rolled tutor-as-finisher-access and ritual-chain-gate
branches encode multi-turn intent that the current simulator
architecture cannot express.

**Two-step alternative**:

1. **Extract `card_combo_modifier`'s tutor-access logic** into
   the simulator — call it `_project_storm_with_tutor_access(
   snap, hand, sideboard, library)`.  This adds the missing
   library-composition bit one piece at a time, on a *test
   bench*, not a live wire-up.  No Storm risk.
2. Once the extracted projection produces non-zero
   `expected_damage` for tutor-only Storm hands (verifiable on
   a unit test: "Storm with Wish in hand and Grapeshot in SB
   reports expected_damage > 0"), THEN attempt Sprint 2
   migration again.  Without that test passing, the migration
   will collapse Storm exactly as it has four times now.

## Cross-references

* `docs/PHASE_D_DEFERRED.md` — original deferral diagnosis
* `docs/AFFINITY_REGRESSION.md` — separate Affinity 88% root-
  cause investigation (still active)
* `ai/finisher_simulator.py:_project_storm` — the function that
  needs library-composition modelling
* `ai/combo_calc.py:670-695` — the existing tutor-as-finisher
  logic the simulator must absorb before migration is viable
