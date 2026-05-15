---
title: ISMCTS budget ceiling on snapshot-only fixtures — gate cannot clear via budget tuning alone
status: active
priority: secondary
session: 2026-05-10
supersedes: []
depends_on:
  - docs/research/2026-05_phase_4a_ismcts_scoping.md
tags:
  - ismcts
  - budget
  - acceptance-gate
  - phase-4a
  - phase-5
summary: >
  Across rollouts ∈ {100, 500, 1000, 2000, 5000} × depth ∈ {1, 2, 3, 4}
  the 12-fixture acceptance gate never lands ≥4 strict ISMCTS wins
  with 0 regressions. The binding failure mode is fixture-intrinsic,
  not budget-bound: at every cell the snapshot-only fixtures either
  match the heuristic (ties) or surface a structural regression on
  Storm's T3 ritual chain. Budget tuning alone cannot clear the
  Phase-4A acceptance criterion; the next step is to extend the
  fixture / scoring contract, not to grow the search budget.
---

# ISMCTS budget ceiling on snapshot-only fixtures

## Background

PR #365 wired the `MTGSIM_USE_MCTS` opt-in flag for `ISMCTSPlanner`.
PR #367 wired the production-scorer adapter so the
`tests/test_ismcts_acceptance_real.py` 12-fixture gate compares ISMCTS
against the *production* `evaluate_board` baseline (not just the
synthetic 1-ply `position_value` scorer).

At the default budget documented in
`docs/research/2026-05_phase_4a_ismcts_scoping.md` — `n_rollouts=500`,
`rollout_depth=2`, `n_determinizations=50` — the gate produces

```
ISMCTS strict wins   : 2
Heuristic strict wins: 1
Ties                 : 9
Passed               : False
```

against *both* baselines (the synthetic and the production picker
produce identical pick / regression signatures because the snapshot
adapter's `evaluate_board` and `position_value` agree at this fidelity).

The Phase-4A acceptance criterion (scoping doc §"Acceptance gate") is
**≥4 strict ISMCTS wins AND 0 strict heuristic wins** (regressions).
We are short by 2 wins *and* over by 1 regression at the default.

PR #367's honest-blocker note asked whether the budget shape was the
binding constraint. This diagnostic answers that question.

## Sweep

`tools/ismcts_budget_sweep.py` runs the same gate harness over a
cross of:

- `n_rollouts ∈ {100, 500, 1000, 2000, 5000}`
- `rollout_depth ∈ {1, 2, 3, 4}`
- `n_determinizations = 50` (pinned at the scoping-doc default)
- Both heuristic baselines (synthetic + production)

Per-cell cap: 60 s wall clock for the main grid; the 5000-rollout
row was run separately with a 200 s cell cap because its per-fixture
cost exceeds 13 s on a single CPU (~165 s for the full 12-fixture
gate). Full sweep cap: 15 min — within reach for CI / acceptance
runs, but the 5000-rollout × depth ∈ {2, 3} combos exceed the budget
and are written as `budget_exhausted` rather than re-run.

CSV output: `data/ismcts_budget_sweep_2026_05_10.csv`. 40 cells
total: 35 `ok` (full grid for rollouts ≤ 2000 plus 3 of 8 cells at
rollouts=5000); 5 `budget_exhausted` (the remaining 5000-rollout
combos). Columns: `baseline, rollouts, depth, determinizations,
ismcts_wins, ties, heuristic_wins, regressions, passed,
wall_clock_total, wall_clock_per_fixture, status`.

## Findings

### 1. No (rollouts, depth) combo clears the gate

Across all 35 successfully-evaluated sweep cells, `passed=true` is
never reached. Two findings hold across the entire grid:

- `heuristic_wins ≥ 1` at every cell. The gate's "0 regressions"
  arm is the **binding** constraint, not the "≥4 wins" arm.
- `ismcts_wins` stalls at 2 (depth ∈ {1, 2}) or 3 (depth ∈ {3, 4}
  with rollouts ≥ 500 — specifically rollouts ∈ {500, 1000, 2000,
  5000} × depth=4 each return wins=3, regr=2). It never reaches 4.
- The pattern is *identical* between the synthetic baseline and the
  production baseline at every rollout × depth combo. The Phase-5
  scorer adapter does not change which fixture regresses (it
  matches the synthetic adapter's pick on every fixture in the
  corpus).

The two-axis evidence:

| rollouts | depth=1 | depth=2 | depth=3 | depth=4 |
|---------:|---------|---------|---------|---------|
|      100 | 2W/1R   | 2W/1R   | 2W/2R   | 2W/2R   |
|      500 | 2W/1R   | 2W/1R   | 2W/2R   | 3W/2R   |
|     1000 | 2W/1R   | 2W/1R   | 2W/2R   | 3W/2R   |
|     2000 | 2W/1R   | 2W/2R   | 2W/2R   | 3W/2R   |
|     5000 | 2W/1R   | exh     | exh     | 3W/2R   |

(Identical across the synthetic and production baselines; `W` = ISMCTS
strict wins, `R` = strict heuristic wins / regressions, `exh` =
`budget_exhausted`.)

### 2. The regression is fixture-intrinsic

Inspection of the per-fixture trace at the default budget shows the
sole strict heuristic win is **fixture #3 (T3_ritual_chain)**:

```
✗ 3 T3_ritual_chain:
    H='Lightning Bolt opp'    (heuristic picks Bolt at opp)
    M='pass turn'             (ISMCTS picks pass)
    Δ=-0.02, p=0.000
```

The fixture's payload is a Storm-archetype mid-chain decision: the
right play is to fire Lightning Bolt at the opponent's life total to
finish the storm-count chain. ISMCTS picks "pass turn" because:

1. `enumerate_actions` always appends a synthetic `pass` token.
2. Storm fixtures have low immediate `position_value` on most
   pre-storm-payoff actions, so the rollout policy
   (`heuristic_rollout`) sees a flat scoring landscape.
3. The 1-ply `position_value` proxy used in rollouts cannot project
   the storm-count payoff (Grapeshot at storm 10+), because Grapeshot
   is not represented in the action token set for this fixture — the
   fixture asks the planner to make a *single* mid-chain decision
   in isolation.
4. With a flat reward landscape, deeper search amplifies noise; at
   depths ≥ 3 a *second* fixture regresses (giving 2 heuristic wins
   at the higher-rollout cells).

This is a fixture-design property, not a search-algorithm property:
the snapshot-only `SearchState` has no notion of "storm count this
turn" beyond the `EVSnapshot.storm_count` field, and the production
combo-payoff scoring lives outside the snapshot domain in
`ai/combo_calc.py` / `ai/combo_chain.py`. ISMCTS optimises the
metric the rollout policy supplies; the rollout policy is the same
1-ply `position_value` used at the leaves; therefore deeper search
does not surface new combo-line value the leaf scorer cannot see.

### 3. Higher depths surface a second regression, not new wins

At `rollouts ≥ 1000, depth ≥ 3` a *second* fixture flips from tie to
strict heuristic win. This is the higher-depth amplification of the
same fixture-scoring blind spot: when the per-step reward signal is
near-zero, MCTS's UCB exploration favours arms whose rollout variance
puts them above the mean of the dominant arm — exactly the failure
mode predicted in
`docs/research/2026-05_phase_4a_ismcts_scoping.md` §"Risks and
mitigations" row 4 ("MCTS underperforms heuristic at equal budget").

### 4. Wall-clock cost scales superlinearly with rollouts × depth

At the default `500 × depth=2`: ~0.16 s / fixture.
At `5000 × depth=4`: well beyond the 60-second cell cap on a single
CPU; superlinear because `rollout_depth × n_rollouts` is the dominant
cost driver and each transition allocates a fresh `SearchState`.

## Conclusion

**Budget tuning alone cannot clear the Phase-4A acceptance gate** on
the 12-fixture snapshot-only corpus. The binding constraint is the
fixture-scoring contract:

- The fixtures encode single-decision pivots, not multi-card combo
  sequences. Storm's storm-count payoff (Grapeshot at storm 10+) is
  literally not in the action token set on the regressing fixture.
- The rollout policy and terminal evaluator both bottom out at
  `position_value` / `evaluate_board` on the snapshot, neither of
  which surfaces combo-chain payoff value the way
  `ai/combo_calc.py`'s `_estimate_combo_chain` does on real
  `CardInstance` hands.
- ISMCTS faithfully optimises the rollout signal. When the signal is
  blind to a payoff, more rollouts and deeper depth amplify noise
  rather than surfacing the right pick.

## Recommended next steps (not done in this diagnostic)

1. **Extend the snapshot adapter to surface combo-chain payoff** —
   the production path's `compute_play_ev` consults
   `_estimate_combo_chain` for storm-archetype fixtures; the snapshot
   adapter's `evaluate_board` does not. Wiring that through to
   `_score_action_production` would make the production baseline AND
   the ISMCTS rollout aware of the storm payoff, removing the
   flat-reward-landscape failure mode on fixture #3.

2. **Add multi-token compound actions to fixture #3** — the current
   fixture offers `Lightning Bolt opp` but no Grapeshot token,
   forcing both planners to pick from a sub-set of the real
   decision space. Restoring the full storm action menu would let
   ISMCTS discover the chain-completion line via search.

3. **Replace the snapshot rollout policy with a `CardInstance` proxy
   path** — the in-flight Phase-5 work (PR #367 → planned follow-up,
   per its commit message) constructs synthetic `CardInstance`
   objects from action tokens so `compute_play_ev` runs as the
   rollout. That removes the snapshot/production-path divergence
   entirely.

All three are scope expansions beyond budget tuning; they are
captured here so the next session opens with a concrete plan rather
than re-running budget sweeps.

## Acceptance-gate disposition

The acceptance gate stays at its current parameters
(`n_rollouts=500`, `rollout_depth=2`) and remains gated behind
`ISMCTS_ACCEPTANCE=1` (no change to CI). Updating the gate to a
budget that clears it is not feasible from this diagnostic; the
budget that *would* clear it (per the sweep CSV) does not exist
within the tested grid.

This document closes the budget-tuning hypothesis as **falsified for
the snapshot-only fixture corpus**. The next sequenced work is
listed under "Recommended next steps" above.

## Files

- Tool: `tools/ismcts_budget_sweep.py`
- Data: `data/ismcts_budget_sweep_2026_05_10.csv`
- Gate test: `tests/test_ismcts_acceptance_real.py`
- Production adapter: `ai/search/evplayer_scorer_adapter.py`
- ISMCTS planner: `ai/search/ismcts.py`
- A/B harness: `ai/search/ab_compare.py`
- Snapshot adapter: `ai/search/snapshot_adapter.py`
