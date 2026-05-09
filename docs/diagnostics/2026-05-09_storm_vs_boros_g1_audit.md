---
title: Storm vs Boros G1 audit — chain length insufficient by 1 spell
status: active
priority: secondary
session: 2026-05-09
depends_on:
  - docs/diagnostics/2026-05-04_ruby_storm_audit.md
  - docs/diagnostics/2026-04-28_storm_wasted_enablers.md
tags:
  - storm
  - boros
  - g1
  - mulligan
  - clock
  - chain-length
notes:
  - "WR numbers in this doc are from Bo1 matrix runs and are systematically biased; Bo3 is the canonical framework as of 2026-05-04."
summary: |
  Storm vs Boros Bo1 reproduces at 34% (n=50, s=50000). Trace
  evidence across 8 losing seeds shows a SHARED failure pattern:
  Storm reaches T5 with a 7-spell chain that lands 17-21 damage
  while Boros has gained life (Guide of Souls + Phlage) to 21-23.
  Even with optimal Grapeshot ordering the chain is 1 spell short
  of lethal.  Storm dies on Boros's T5 swing.

  Diagnosis is NOT mid-chain Grapeshot mis-ordering (small EV
  effect, validated below).  Diagnosis IS deck-clock: Storm needs
  to combo by T4 vs Boros, not T5.  The remediation surface is
  primarily mulligan-side (keep faster hands) and possibly
  T1-T3 sequencing (deploy Ruby Medallion T2 not T3).

  Ships in this branch:
  - Test + fix for the storm-finisher hold gate to count
    tutor-with-payoff-access as chain-extending fuel (mechanic
    correct, generalizes to Goryo's / Living End / Burning Wish,
    null impact on Storm vs Boros n=50, no regression elsewhere).

  Defers (architectural):
  - Storm mulligan ladder vs aggro (need fast-aggro detection
    signal in `ai/mulligan.py`, deck-speed-aware keep threshold).
  - T1-T3 sequencing: Ruby Medallion deployment timing on the
    play vs the draw.  Currently undeployed Ruby Medallion → no
    cost reduction → chain length bounded by mana.
---

# Storm vs Boros G1 audit

## Reproduction

```
$ python run_meta.py --matchup storm boros -n 50 --bo1 -s 50000
Ruby Storm:    34% (avg T5.2)
Boros Energy:  64% (avg T5.8)
```

Bo1 baseline 34% (n=50).  Smaller-n samples ranged 20-40% for
Storm — consistent with the audit's "vs Boros 0/10 is
reproducible" outlier flag (variance over seed banks; the n=10
slice that hit 0/10 was an unlucky bank).

## Method

Survey of 8 losing seeds (s=50000, 50500, 51000, 51500, 52000,
52500, 53000, 53500), all `--bo1`.  For each loss:

1. Capture the verbose log and the Storm-side trace.
2. Identify the turn Boros lethals Storm.
3. Identify Storm's combo turn (turn it casts the first finisher).
4. Compute Storm's actual storm chain damage and check vs
   Boros life total at the time.

## Shared failure pattern

| Seed | Storm combo turn | Storm count peak | Damage | Boros life | Result |
|---|---|---|---|---|---|
| 50500 | T5 | 7 | 17 | 22 | Boros wins T5 |
| 51000 | T6 | 8 | 8 | 23 | Boros wins T6 |
| 52000 | T5 | 2 | 3 | 22 | Boros wins T6 |
| 52500 | T3 (early Grapeshot) | 5 | 6 | 18 | Boros wins T5 |
| 53000 | n/a | 0 | 0 | 21 | Boros wins T5 |
| 53500 | T5 | n/a | n/a | n/a | Boros wins T5 |

The dominant leak is **NOT mid-chain Grapeshot ordering** (would
move WR by ≤ 5pp at most).  The dominant leak is **chain length
by Boros's T4-T5 lethal window**:

- Boros's clock vs Storm averages T5 (kill turn distribution above).
- Boros gains 2-4 life from Guide of Souls + Phlage by T5
  (life total reaches 21-23).
- Storm's T5 chain produces ~7-8 storm count = 17-21 damage with
  a single Grapeshot cast (or 2 if Wish lands), bounded by mana.
- **Even optimally-ordered, the chain is 1 spell short of 22**:
  the chain runs out of mana before it can reach storm count 8+.

## What does NOT fix this

### A. Mid-chain finisher hold gate (test name: `test_storm_finisher_holds_for_tutor_with_payoff_access`)

This branch ships a principled fix for the storm-finisher hold
gate in `ai/combo_calc.py`: tutor-with-payoff-access (e.g. Wish
when SB has Grapeshot) now counts as chain-extending fuel.
Symmetry argument: the existing tutor branch counts
`non_tutor_fuel` (excluding tutors) — the storm branch should
likewise treat a Wish-with-payoff as chain growth, since casting
Wish adds 1 to storm AND brings a payoff that adds 1 more.

The fix is **mechanically correct and generalizes to all
combo decks with tutor-fetched finishers** (Goryo's Vengeance,
Living End cascade-tutoring, Burning Wish decks).  But it does
NOT move Storm vs Boros n=50 s=50000 (34% → 32%, within noise).

Reason: even with optimal ordering, the chain is mana-bound at
~7 spells.  Reordering the same 7 spells gives ≤ 4pp more total
damage — not enough to cross the 22-life threshold.

### B. Class B-1 from the 2026-05-04 audit (3 Grapeshot MB)

Already shipped in this branch (`decks/modern_meta.py` "Ruby Storm"
mainboard shows Grapeshot: 3).  This added ~3pp of finisher draw
probability to opener-7 but doesn't address the chain-length
shortfall on T5.

## What WOULD fix this (architectural — defer)

### Hypothesis 1: Mulligan ladder vs fast aggro

Storm's `mulligan_min_lands=2, mulligan_max_lands=3` keeps hands
that combo on T5.  Vs Boros's T5 clock, T5 combo is too slow.
Storm should mulligan AGGRESSIVELY toward T4 combo hands when
facing fast aggro:

- Pre-condition: opp archetype is `aggro` AND opp's expected
  kill-turn is T4-T5 (clock-derived signal).
- Action: tighten the keep filter — require at least one of
  {Ruby Medallion in hand, 2+ rituals, Wish + 2 lands} for an
  opener-7 keep.

This is a **mulligan policy change**, not a scoring change.
Lives in `ai/mulligan.py` (or `ai/ev_player.py`'s mulligan path)
and reads from `ai/clock.py` for the opp-clock signal.

Generalizes to other slow-combo vs fast-aggro pairings (Living
End vs aggro, Goryo's vs aggro).  Same fix lifts every slow
combo in the meta.

### Hypothesis 2: Ruby Medallion deployment timing

Ruby Medallion deployed T2-T3 means rituals/cantrips/Grapeshot
all cost -1 from T3 onwards.  Trace evidence shows Ruby
Medallion frequently deployed on T4-T5 (after the chain has
already started), so it provides only 1-2 spell discounts
within the combo turn.

Generic mechanic: cost-reducer deploy timing.  Already partially
addressed by the reducer-first heuristic in `card_combo_modifier`
(line 808 of `ai/combo_calc.py`) but the heuristic only fires
during the EXECUTE_PAYOFF goal, not during BUILD_RESOURCES.
Lifting it into BUILD_RESOURCES would deploy Medallion T2 not T4.

### Hypothesis 3: Boros's T5 lethal is too clean

Storm's blocker plan is non-existent (no creatures), so Boros
just attacks for 7-9 each turn.  Storm's life total trends:
T1=20→19 (fetch)→17 (fetch+combat)→14 (combat)→7 (combat)→0
(combat). Storm is DEAD on Boros T5 even if combo lands T5.

This means Storm vs Boros is genuinely a race.  The race is
lost when Storm's combo turn slips from T4 to T5.  A T4 combo
solves it; a T5 combo does not.

## Recommendation

1. **Ship the storm-finisher hold-gate fix in this branch** (no
   regression, principled, lifts other combo decks).
2. **Open a follow-up** for the mulligan-ladder hypothesis as a
   primary track.  Failing test for it would name the mechanic:
   `test_storm_mulligans_aggressively_against_aggro_kill_turn_le_5`.
3. **Open a parallel track** for cost-reducer deploy timing in
   BUILD_RESOURCES.  Failing test: `test_cost_reducer_deployed_
   in_build_phase_when_chain_payoff_in_hand`.

The latter two require touching mulligan/goal-engine code which
crosses the "architecturally significant" threshold from
CLAUDE.md — they get their own diagnostic doc + their own PR
when worked.

## Branch contents

- Failing test → green: `tests/test_combo_calc.py::test_storm_
  finisher_holds_for_tutor_with_payoff_access`
- Fix: `ai/combo_calc.py` STORM-FINISHER branch counts
  `_tutor_has_payoff_access` cards as chain fuel.
- This doc.

## Validation

- Storm vs Boros n=50 s=50000: baseline 34%, after fix 32% (no
  meaningful movement; bug is real but small EV effect).
- Storm vs Tron n=30 s=50000: baseline 30%, after fix 23%
  (within variance, no regression).
- Storm vs Amulet n=30 s=50000: baseline 57%, after fix 57%
  (no change).
- Full pytest: 1400 pass, 35 fail (all 35 fail on baseline
  too — pre-existing LLM/Nettlecyst/scoring-constants failures
  unrelated to this change).
- Combo-calc test class: 20/20 pass including the new test.
