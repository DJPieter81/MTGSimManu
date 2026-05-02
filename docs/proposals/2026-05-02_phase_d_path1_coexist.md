---
title: Phase D path 1 coexist — Wish-target picker uses simulator
status: active
priority: primary
session: 2026-05-02
depends_on: [docs/PHASE_D_FOURTH_ATTEMPT.md]
tags: [phase-d, simulator, path-1, coexist]
summary: |
  After 9 failed migration attempts, ship the simulator as a TOOL
  for one local decision: Wish's sideboard-target choice in
  `engine/card_effects.py:wish_resolve`.  card_combo_modifier
  remains the live combo scorer — untouched.  The simulator
  replaces only the heuristic-driven finisher_priority ordering
  inside Wish resolution, where chain-aware EV picking strictly
  improves on the current count-fuel-and-compare arithmetic.
---

# Chosen decision point

Replace the `finisher_priority` heuristic inside `wish_resolve`
(engine/card_effects.py:1053-1230) with a `simulate_finisher_chain`
ranking — picks the SB target that maximises projected damage when
fetched and cast next-in-chain.

# Current code

`engine/card_effects.py:1075-1192`. Today's logic:

```
estimated_storm = current_storm + 1 + min(total_fuel, 8)
grapeshot_damage = estimated_storm
empty_power     = 2 * estimated_storm
... (survival factor, hold_margin, pif_extends_to_lethal branches)
finisher_priority = [...one of 5 hardcoded orderings...]
```

The arithmetic uses a flat `min(total_fuel, 8)` without chain
constraints (mana cost, cost-reducer interaction, GY availability)
and never queries the simulator that already understands these.

# Proposed change (pseudocode)

```
inside wish_resolve, after computing `sb` and `lib`:

  proj_by_target = {}
  for target in sb (filtered to instants/sorceries):
      hypothetical_hand = current_hand - Wish + target
      proj = simulate_finisher_chain(
          snap, hand=hypothetical_hand, ...,
          sideboard=sb_minus_target, library=lib,
          storm_count=current_storm + 1)
      proj_by_target[target] = proj.expected_damage * proj.success_probability

  best_target = argmax(proj_by_target)
  if best_target's projected EV > 0:
      chosen = best_target
  else:
      fall through to existing finisher_priority logic (no regression)
```

Justification: each call to `simulate_finisher_chain` is one
hypothetical post-fetch projection — exactly what the simulator
was built for. Picking the target that maximises projected damage
times success probability is a strictly local decision; it does
NOT change `card_combo_modifier`'s scoring of any cast in hand.
The fallback to the existing heuristic when no target projects
positive EV preserves baseline behavior in pathological cases.

# Test gate

```
python run_meta.py --field "Ruby Storm" --games 10 -s 60000
# expected: Storm WR >= 36% (matches current baseline of 36.9%)

python run_meta.py --bo3 "Ruby Storm" "Affinity" -s 60500
# expected: Storm wins replay scenario at T6 life=5 (currently passes)
```

Unit tests:
1. `test_wish_picker_uses_simulator_when_chain_extends`: SB has
   {Grapeshot, Past in Flames}; hand has rituals + GY fuel; assert
   simulator picks Past in Flames (chain-extends to lethal) over
   Grapeshot (sublethal direct).
2. `test_wish_picker_falls_back_when_simulator_silent`: SB has
   only non-storm spells; assert legacy finisher_priority used.

# Failure protocol

ANY deck regresses >5pp on field N=10: revert immediately. Same
panic-patch protocol as Phase D attempts 1-9. The change is
isolated to `wish_resolve`; revert is a single-file rollback.

# Why this DOES NOT repeat the 9-attempt failure

All 9 failed attempts shared one structural property: they
replaced or modified `card_combo_modifier`'s pre-cast scoring of
chain-fuel cards (rituals, cantrips, tutors). The collapse mode
was always "build-up turn cards score 0 → AI passes". This
proposal NEVER touches `card_combo_modifier`. It changes only
what Wish fetches AFTER `card_combo_modifier` has already decided
to cast Wish. Chain-fuel scoring stays exactly as it is at the
36.9% baseline. The simulator is invoked at a completely different
decision boundary (during spell resolution, not during card
ranking), so the loop-break failure mode is structurally
unreachable here.
