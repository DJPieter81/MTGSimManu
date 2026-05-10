---
title: Phase D (finisher migration) — deferred until simulator v2
status: active
priority: primary
session: 2026-04-26
supersedes: []
superseded_by: []
depends_on: [PR #204]
tags: [phase-d, finisher-simulator, combo, migration]
summary: |
  The simulator-based replacement for `card_combo_modifier` was
  attempted in this session and reverted after Storm field N=20
  regressed from 44.8% → 5.3% (-39pp).  Root cause is in the
  simulator's API surface, not in the migration code.  Documents
  what simulator v2 must expose before the migration can land.
---

# Phase D — deferred until simulator v2

## What was tried

`ai/combo_evaluator.py` (kept in-tree, not wired) implements a
per-card scoring delta:

```
Δ = simulate_finisher_chain(state_after_cast)
  − simulate_finisher_chain(state_before_cast)
score = Δ × combo_value
```

The intent: replace `ai.combo_calc.card_combo_modifier` (~310 LOC
of patches — storm finisher timing, tutor-as-finisher gating,
cost-reducer arithmetic, ritual chain gates at storm=0 and
storm>=1, flip-transform stack batching, search-tax awareness)
with a uniform projection-delta driven entirely by the simulator.

## What broke

Ruby Storm field N=20:
- Pre-migration baseline: 44.8% (PR #202)
- Post-migration: **7.5%** (-37pp)
- After adding a "wasted-cast" penalty: **5.3%** (-39pp)

Goryo's, Living End, Amulet Titan all expected to follow.  The
panic-patch-or-revert criterion in `CLAUDE.md` ("If a deck
regresses > 5pp: investigate root cause; do NOT add deck-specific
patches") triggered → reverted the live wire-up.

## Why it broke — simulator v2 requirements

The marginal-delta approach assumes
`simulate_finisher_chain(state_after) − simulate_finisher_chain(state_before)`
captures every consideration `card_combo_modifier` made.  It
does not.  The simulator currently lacks:

### 1. Hold-vs-fire Choice projection

`simulate_finisher_chain` projects "fire the chain now".  It
does not project "hold this card and chain next turn instead".

Concrete failure: storm=0 with a single ritual in hand and no
finisher card.  Casting the ritual:
- spends mana
- adds 1 to storm
- still leaves zero finishers in hand

The simulator returns `pattern="none"` for both before and after
states.  Marginal delta = 0.  AI casts the ritual freely and
wastes the mana (CR 500.4 — empties at phase end).

The legacy `card_combo_modifier`'s "RITUAL CHAIN GATE storm=0"
branch (combo_calc.py:741-808) hard-clamped this case with a
`STORM_HARD_HOLD` sentinel.

**Fix shape:** simulator must expose a `hold_value` field equal
to the projected EV of NOT casting the queried card this turn —
the AI then picks `max(fire_now, hold)` per Choice.

### 2. Opportunity cost of spent mana

Mana floats are illegal at phase boundaries.  Casting a card
that doesn't reach a chain is a permanent resource loss.  The
simulator's `expected_damage * success_probability` doesn't
penalise this — both projections (before and after) score 0
when no chain is reachable.

**Fix shape:** add a `wasted_mana` term to the projection that
debits the mana cost of any card cast in a `pattern="none"`
state.  Distinct from the orthogonal "wasted-cast penalty"
attempted in this session — must live INSIDE the simulator so
multi-card chain projections compose correctly.

### 3. Multi-turn projection

Storm sometimes needs T3 setup → T4 finisher.  The simulator is
single-turn.  Casting Past in Flames on T3 (no flashback fuel
yet) is a waste this turn but enables T4's chain.

The legacy modifier's "tutor-as-finisher access" branch
(combo_calc.py:670-695) and "cost-reducer chain improvement"
branch (combo_calc.py:710-734) implicitly model multi-turn value.

**Fix shape:** add a `next_turn_proj` field to
`FinisherProjection` that runs a second simulation with
`storm_count=0`, `my_mana = next_turn_mana`, and the post-cast
hand.  The total projected damage = max(this_turn, next_turn).

### 4. Mid-chain coverage escalation

The legacy modifier's "MID-CHAIN RITUAL GATE storm>=1" branch
(combo_calc.py:818-859) penalises ritual casts mid-chain when
`storm/opp_life > HALF_LETHAL` and no finisher path exists —
investing more rituals into a stranded chain is increasingly
catastrophic.

**Fix shape:** the simulator must expose
`coverage_ratio = projected_damage / opp_life` and
`closer_in_zone` flags so the AI can detect the "all-in but
stranded" state.

## Path forward

Phase D became a three-PR sequence after simulator v2 also failed:

- **PR3b — simulator v2 (shipped 04754d2):**
  - `FinisherProjection.hold_value: float` — projected next-turn
    damage × survival probability
  - `FinisherProjection.next_turn_damage: float` — chain damage
    achievable next turn given +1 land
  - `FinisherProjection.coverage_ratio: float`
  - `FinisherProjection.closer_in_zone: dict[str, bool]` (hand /
    sb / library / graveyard)
  - 8 new tests, simulator pure-additive (no live wire-up)

- **PR3c — third Phase D migration attempt (REVERTED):**
  - Wired `combo_evaluator.card_combo_evaluation` into
    `_score_spell` using v2 `hold_value` / `coverage_ratio` for
    hold-vs-fire decisions.
  - Three iterations of the hold gate all collapsed Storm:
    * `hold_value > fire_value` → Storm holds forever (next turn
      always projects more mana, recursion non-terminating)
    * Lethal-gate (only fire when this turn is lethal) → Storm at
      1.2% (almost never reaches lethal-this-turn projection
      because closer is in SB via Wish, not in hand)
    * `hold_lethal AND not fire_lethal` → Storm at 0% (never
      satisfies hold_lethal because next_turn_damage = 0 when
      no closer in hand)
  - Reverted to `card_combo_modifier`; Storm restored to 40.6%.

- **PR3d — simulator v3 (next session)** must model **intermediate
  value of casting fuel BEFORE the closer is reached.**  The
  current `expected_damage = 0` when no closer is in hand
  collapses every chain-fuel decision to "fire_value = 0", and
  Storm's intent is "build chain THIS turn, find closer NEXT
  turn via Wish/tutor".  Requires:
  - Library composition modelling (P(draw closer | N more draws))
  - Tutor-as-finisher-access semantics (Wish in hand = closer
    in SB at +N mana cost)
  - Multi-turn rollout: simulate 1, 2, 3 turns out, pick the
    turn with highest `damage × survival` product
  - This is genuinely complex.  card_combo_modifier's hand-tuned
    branches encode this knowledge; the simulator needs equivalent
    fidelity before Phase D can ship.
  - Tests covering each new field against the four chain patterns
    (storm / cascade / reanimation / cycling)

- **PR3c — migration**:
  - Re-attempt the wire-up using the v2 API
  - Storm field N=50 must hold ≥ 44%
  - Goryo's / Living End / Amulet must not regress > 5pp
  - Delete `card_combo_modifier`
  - Delete the 4 stale tests in `test_combo_calc.py`

`ai/combo_evaluator.py` from this session stays in-tree as the
sketch of how the migration call site looks.  It will be rewritten
when PR3c lands (the v2 API surfaces enough that the marginal-delta
hack with the wasted-cast term becomes principled).

## Why this is not a panic patch

The plan's stop criterion was followed: 39pp regression →
investigate root cause → identify simulator API gaps → revert
live wire-up.  No deck-specific patches added.  No card-name
hardcoding introduced.  The reverted state matches `PR #202`
post-merge — Storm 44.8%, all 16 decks within their gates.

The simulator scaffolding from PR #204 is preserved on `main` and
its tests still pass.  Migration infrastructure (`combo_evaluator.py`)
preserved in-tree as a starting sketch.

## Concrete trace examples

Two reproducible Bo3 traces showing the same failure mode at
different storm counts. Both confirm the projection-layer issue
described above: `compute_play_ev` returns deep-negative EV
(~-10) for chain-prerequisite spells (rituals, mana-fix,
flashback-enablers like Past in Flames) regardless of whether
the chain payoff is reachable in the same turn or the next.

### Trace 1: storm = 0, no finisher in hand

Verbose seed 50000 T4 (Storm vs Boros Energy). Storm cast Past in
Flames 3x without ever drawing/casting Grapeshot. Mana-burn from
speculative chain. Hard-clamped today by the
`RITUAL CHAIN GATE storm=0` branch in `combo_calc.py:898-908`
(`STORM_HARD_HOLD` sentinel).

### Trace 2: storm >= 1, mid-chain freeze visible in EV table

Reproducer (deterministic from seed):

```
python run_meta.py --bo3 affinity storm -s 60600 \
  --dump-replay replays/affinity_vs_storm_60600.ndjson
```

Game flow at G3 T4 (Ruby Storm on the play, life 11; Affinity
life 20). Storm chains 7 spells in one turn:

| # | Decision | Spell                     | Storm count after | Mana state |
|---|----------|---------------------------|-------------------|------------|
| 1 | g3t4d70  | Ral, Monsoon Mage         | 1                 | -1R cost reducer online |
| 2 | g3t4d71  | Wrenn's Resolve (cantrip) | 2                 | draws 2 |
| 3 | g3t4d72  | Glimpse the Impossible    | 3                 | draws 3 (incl. Grapeshot) |
| 4 | g3t4d73  | Desperate Ritual + splice | 4                 | +6R from doubled ritual |
| 5 | g3t4d74  | Glimpse the Impossible    | 5                 | draws 3 (incl. Ruby Medallion) |
| 6 | g3t4d75  | Ruby Medallion            | 6                 | -1R reducer stacked |
| - | g3t4d76  | **PASS (end of main 1)**  | 6                 | mana exhausted; combat skipped |
| 7 | g3t4d77  | Manamorphose (main 2)     | 7                 | draws 1 + adds 2 any |
| - | g3t4d78  | **PASS (end of main 2)**  | 7                 | 2 floating |

EV table at `g3t4d76` from the NDJSON `alternatives` field
(end of main 1, storm=6, Affinity at 20 life):

| Action                 | EV     | Source                                      |
|------------------------|--------|---------------------------------------------|
| pass (chosen)          |  0.00  | tiebreaker default                          |
| Grapeshot              | -5.63  | combo modifier - fuel-in-hand hold          |
| Manamorphose           | -10.00 | **base projection - no chain credit**       |
| Desperate Ritual       | -10.07 | **base projection - no chain credit**       |
| Reckless Impulse       | -10.31 | **base projection - no chain credit**       |

EV table at `g3t4d78` (end of main 2, storm=7, Affinity at 20):

| Action                 | EV     | Source                                      |
|------------------------|--------|---------------------------------------------|
| pass (chosen)          |  0.00  | tiebreaker default                          |
| Desperate Ritual       | -9.95  | **base projection - no chain credit**       |
| Reckless Impulse (x2)  | -10.25 | **base projection - no chain credit**       |
| Past in Flames         | -10.28 | **base projection - no chain credit**       |

Trace through `compute_play_ev(Past in Flames)` at d78:

1. `_enumerate_this_turn_signals` returns
   `['combo_continuation', 'flashback_enabler']` because:
   - archetype = "storm"
   - storm_count = 7 > 0
   - 'flashback' in tags
   - graveyard contains castable instants/sorceries
     (Wrenn's Resolve, Glimpse, Desperate Ritual...)
2. Signals non-empty -> enters full projection path
   (NOT `-_compute_exposure_cost` shortcut).
3. `evaluate_board(after_pif_resolves) - evaluate_board(now)`
   ~= -10. Projection sees:
   - -1 card from hand (~= -2.5 via `CARD_IN_HAND_VALUE`)
   - -4 mana spent (Past in Flames costs 3R)
   - opponent-response discount (BHI counter/removal density)
   - **no credit for "this card grants flashback to N graveyard
     spells, enabling a much bigger T5 chain"** - the simulator
     projects ONE spell per call; multi-turn chain enablement
     is invisible.
4. `card_combo_modifier(Past in Flames)` returns 0: the storm>=1
   gate at `combo_calc.py:983-1000` checks `_has_storm_finisher`
   first; Grapeshot is in hand, so the penalty branch is skipped
   -> 0.
5. Final EV = -10.28 + 0 = -10.28.

### Why the chosen pass is "right answer, wrong reason"

In this specific seed, pass is also game-theoretically correct
at d76 and d78 - Affinity is at 20 life, Storm has no realistic
path to lethal on T4 (storm=6 + Grapeshot deals 7; storm=7 +
ritual + Grapeshot deals 9). The mid-chain freeze surfaces in
the EV table, but the AI happens to pick the right action via
the `pass = 0.00 > -10` tiebreaker, not because the projection
correctly rated the chain.

The structural issue is that **the same -10 score appears
regardless of whether the chain would close lethal**. In states
where the chain WOULD close (e.g. opp at 15 life with the same
storm count), the AI would still pass at 0.00 over Grapeshot at
-5.63 and rituals at -10. The trace makes the structural
blindness visible; this seed just doesn't punish it.

Storm goes on to win G3 on T5 in this run via a continued chain
off the next draw step - the freeze costs nothing in this
specific game, but the EV pattern is the same one that punts
chains in other seeds and other matchups.

### Sweep context

Five Bo3 matches at seeds 60100 / 60600 / 61100 / 61600 / 62100
(`run_meta.py --bo3 affinity storm -s <SEED>`):

| Seed  | Result                | Notes                       |
|-------|-----------------------|-----------------------------|
| 60100 | Affinity 2-0          | G1 T4, G2 T4 - fastest stomp |
| 60600 | **Ruby Storm 2-1**    | Storm G1 T4, Aff G2 T4, Storm G3 T5 - the diagnostic match |
| 61100 | Affinity 2-0          | G1 T6, G2 T5                 |
| 61600 | Affinity 2-0          | G1 T6, G2 T7                 |
| 62100 | Affinity 2-0          | G1 T5, G2 T5                 |

Aggregate: Affinity 4-1 in matches, 8-2 in games (80% game WR).
Consistent with the ~87% Affinity outlier flagged in
`PROJECT_STATUS.md` - Phase D's structural fix is one
contributing factor among several. Even with chain-vision,
Affinity's T4 Cranial Plating + Signal Pest line frequently
outraces Storm's T4 ceiling; the chain-blindness leaves Storm
below its true ceiling in matches where it would otherwise
close.

## Cross-references

- Original Phase D plan: PR #202 description (deferred section)
- Simulator scaffolding: PR #204 (`ai/finisher_simulator.py`)
- Live-decision modifier: `ai/combo_calc.py:603-911`
  (`card_combo_modifier`)
- Migration sketch: `ai/combo_evaluator.py` (in-tree, not wired)
- Storm gameplan: `decks/gameplans/ruby_storm.json`
- Trace 2 NDJSON: `replays/affinity_vs_storm_60600.ndjson`
  (decisions `g3t4d76`, `g3t4d78`)
