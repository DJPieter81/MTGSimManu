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

## Update — Phase D fifth attempt also failed

Tried the wire-up again after shipping the simulator gap-closing
work (`08c4e11`, `da16698`, `30c7a18`).  Empirical pre-check
showed the gap WAS closed:

* Unit test: tutor-only Storm hand with SB closer → projection
  returns `expected_damage = 4.0` (was 0.0) ✓
* Direct call: `card_combo_evaluation` for a chain-fuel ritual
  returns `+7.97` (was -50.0) ✓

But the live wire-up still collapsed Storm:

* Storm field N=10 = **2.5%** (was 37.5%) ✗
* Reverted; restored to 37.5%.

So the simulator-layer gap was real but **insufficient**.  There
must be a second gap — somewhere in the live integration the
evaluator returns 0 / negative for chain-fuel cards in hands
that don't trigger the tutor-access path.

Hypotheses to investigate next:

1. **Cache key collisions across snapshots.**  combo_evaluator
   caches projections keyed by `id(snap)`.  Within one main-
   phase iteration the snapshot might be re-built on each
   `_score_spell` call, giving a fresh `id` each time —
   meaning the cache never hits and projection is recomputed
   for each candidate card.  If correct, this is wasteful but
   not incorrect.  Worth verifying.

2. **Hands without tutor or SB closer.**  Storm hands like
   `[Ritual, Ritual, Manamorphose, Past in Flames, Ral]` —
   chain assemblable but no Grapeshot in hand AND no Wish.
   What does `card_combo_evaluation` return for ritual-fuel
   here?  `_project_storm` may still return zero damage; the
   tutor-access fallback won't fire (no tutor).  Result might
   be `STORM_HARD_HOLD = -50` for every chain-fuel card →
   passes turn.

3. **Evaluator's branches don't compose with default scoring.**
   The +7.97 chain-credit value is principled but might be
   double-counted with the projection's natural mana-production
   value, OR the negative branches (hard-hold, hold-lethal)
   might be too aggressive.

Next session must instrument the evaluator to log per-card
scoring decisions for a Storm-vs-Affinity Bo3, identify which
hand state triggers collapse, and fix at the root.  The five-
attempt loop-break is now PERMANENT until that instrumentation
exists.

## Update — instrumentation surfaced the THIRD gap

Added env-gated diagnostic trace in `ai/combo_evaluator.py`
(commit `07e8f18`) and a standalone harness
`tools/diag_combo_evaluator_trace.py`.  Ran:

    MTGSIM_COMBO_TRACE=1 python tools/diag_combo_evaluator_trace.py

For Storm seed 50000 T1 hand:
`[Wrenn's Resolve, Past in Flames, Ral, Elegant Parlor,
  Bloodstained Mire, Ral, Glimpse the Impossible]`

```
Wrenn's Resolve     → branch=hard_hold_no_chain  pattern=none  -50
Past in Flames      → branch=hard_hold_no_chain  pattern=none  -50
Glimpse the Impossible → branch=hard_hold_no_chain  pattern=none  -50
```

Every chain-fuel card sees `pattern=none`.  Storm has Past in
Flames + 2 cantrips in hand, but NO ritual, NO storm closer,
NO tutor → `_project_storm` returns None →
`simulate_finisher_chain` returns `pattern="none"` →
combo_evaluator hard-holds every fuel card.

### The third gap (precise diagnosis)

`_project_storm`'s pattern detection requires (line 228-244):

```python
has_ritual = any('ritual' in tags for c in hand)
has_storm_closer = bool(payoff_names)  # storm-keyword in hand
tutors_in_hand = [c for c in hand if 'tutor' in tags]

if not (has_ritual or has_storm_closer or tutors_in_hand):
    return None
```

But Storm CAN combo via Past in Flames alone:

  1. Cast PiF → grants flashback to all GY instants/sorceries
  2. Cast cantrips THIS turn to fill GY (Wrenn's Resolve, Glimpse)
  3. Flashback those cantrips and any rituals drawn into → storm
     count grows
  4. Eventually draw / Wish for Grapeshot

The simulator doesn't recognise PiF as a pattern enabler.  The
LIVE `card_combo_modifier` does — see `_has_viable_pif` at
`combo_calc.py:555-570`, used in the ritual-chain-gate at
`combo_calc.py:741-808`.

### Recommended path forward

Step 2 (per the original two-step plan) needed a third
substep we didn't see:

* **Step 1.6** — extend `_project_storm` to recognise Past in
  Flames in hand as a chain-pattern enabler.  Without ritual /
  closer / tutor, but WITH PiF + at least one chain-fuel card,
  the pattern IS reachable: cast fuel this turn to fill GY,
  PiF flashes back fuel next turn, chain grows from there.

  Needed predicate (oracle-text-driven):
  ```python
  has_pif_pattern = any(
      'flashback' in oracle and 'graveyard' in oracle
      and ('instant' in oracle or 'sorcery' in oracle)
      for c in hand
  )
  ```

  When `has_pif_pattern` is True AND any chain fuel exists in
  hand+gy, project a NEXT-TURN chain (this turn we cast fuel,
  next turn PiF + flashbacks).

Once step 1.6 ships, attempt #6 wire-up should not collapse on
T1 Storm hands with PiF.  Other failure modes may exist —
the trace will surface them similarly.

## Cross-references

* `docs/PHASE_D_DEFERRED.md` — original deferral diagnosis
* `docs/AFFINITY_REGRESSION.md` — separate Affinity 88% root-
  cause investigation (still active)
* `ai/finisher_simulator.py:_project_storm` — the function that
  needs library-composition modelling
* `ai/combo_calc.py:670-695` — the existing tutor-as-finisher
  logic the simulator must absorb before migration is viable
