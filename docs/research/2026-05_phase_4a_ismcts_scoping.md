---
title: "Phase 4A — Information-Set MCTS prototype scoping"
status: active
priority: secondary
session: 2026-05-09
depends_on:
  - docs/research/2026-05_mtg_ai_landscape.md
tags: [research, ai, mcts, prototype, architecture]
summary: >
  Concrete file plan, interface contracts, and acceptance gate for
  the ISMCTS prototype. Designed for parallel work alongside the
  heuristic ratchet on a separate research branch.
---

# Phase 4A — ISMCTS Prototype

## Problem statement

The current AI is a single-ply scorer. `ai/turn_planner.py`'s
5-ordering enumeration is the closest thing to multi-ply lookahead
the engine has, and it operates on a fixed orderings menu, not a
search tree.

For Affinity, this means:
- T2 holdback decisions (cast Mox vs hold for metalcraft) are
  resolved from a single-ply EV, missing the compound effect of
  "T3 metalcraft + Plating + carrier" that a 2-ply search would
  surface.
- Block / counter / removal-target decisions are scored
  individually rather than searched as a sequence.
- The opponent's hidden-hand uncertainty is captured by a
  Bayesian distribution (`ai/bhi.py`) but never expanded into
  determinizations the AI can search across.

A 1000-rollout ISMCTS at ~50 ms / rollout = 50-second-per-decision
budget runs **opt-in** (replay analysis, "deep think" mode, golden-
fixture validation), NOT in the matrix-sim hot loop. This document
scopes that opt-in mode.

## Interface contract

```python
# ai/search/ismcts.py

@dataclass
class SearchConfig:
    n_rollouts: int = 1000
    n_determinizations: int = 50  # per-info-set hand samples
    uct_c: float = 1.4            # UCB1 exploration constant
    rollout_depth: int = 4        # turns to roll out before scoring
    seed: int = 0

class ISMCTSPlanner:
    """Drop-in replacement for TurnPlanner's 5-ordering pick.

    Public API mirrors TurnPlanner.plan_turn so callers can swap
    via `--mcts` CLI flag without other changes.
    """

    def __init__(self, config: SearchConfig | None = None,
                 fallback: TurnPlanner | None = None):
        ...

    def plan_turn(self, game: GameState, player_idx: int) -> TurnPlan:
        """Return a TurnPlan for the player's main phase.

        Internally:
          1. Enumerate root actions via the existing
             turn_planner.legal_plays (no new action enumerator).
          2. For each root action, sample n_determinizations
             opponent hands via bhi.sample_hand_distribution.
          3. UCT search to depth rollout_depth in each
             determinization, average regret across.
          4. Return the action with highest mean visit-weighted
             value.
        """
        ...
```

## Reuse map

| Existing module | New module's use |
|---|---|
| `engine.game_state.GameState` | Root state. We do NOT clone — we use `EVSnapshot.fast_replace` for cheap hypothetical replay. |
| `ai.ev_evaluator.EVSnapshot` | Forward-simulate state without engine mutation. |
| `ai.ev_evaluator.snapshot_from_game` | Initial snapshot. |
| `ai.bhi` | Determinization sampler. New helper: `bhi.sample_n(snapshot, n)`. |
| `ai.turn_planner.legal_plays` | Action enumerator. No new enumerator written. |
| `ai.ev_player.score_play` | Rollout policy (fast heuristic eval at the leaf). |
| `ai.clock.creature_clock_impact_from_card` | Terminal evaluation when search runs out of depth. |

## File plan

```
ai/search/
├── __init__.py
├── ismcts.py             ~250 LOC core
├── uct_node.py           ~80 LOC UCB1 statistics
├── determinizer.py       ~60 LOC bhi → sampled hand
└── rollout_policy.py     ~40 LOC heuristic adapter

tests/
├── test_ismcts_smoke.py             unit-level UCT correctness
├── test_ismcts_determinizer.py      bhi-driven sampling shape
└── test_ismcts_acceptance.py        golden 12-fixture pass gate
```

## Acceptance gate (no matrix runs)

`tests/test_ismcts_acceptance.py` runs 12 archetype fixtures:

| # | Matchup | What this fixture pins |
|---|---|---|
| 1 | Affinity vs Boros (T2 hold-Mox-or-cast) | Multi-ply hold decision |
| 2 | Affinity vs Boros (T4 attack-or-equip) | Combat planning depth |
| 3 | Storm vs Dimir (T3 ritual chain) | Combo-line sequencing |
| 4 | Living End vs Boros (cycle-or-cast cascade) | Self-mill timing |
| 5 | Eldrazi Tron vs Zoo (T3 Karn-or-Ulamog) | Threat priority |
| 6 | Omnath vs Affinity (T4 Leyline Binding choice) | Removal target |
| 7 | Goryo's Vengeance vs Boros (T2 Atraxa setup) | Combo prep |
| 8 | Amulet Titan vs Storm (T3 Titan-or-Pact) | Race vs disruption |
| 9 | Jeskai Blink vs Dimir (T4 Solitude evoke target) | Evoke target choice |
| 10 | Pinnacle Affinity vs Living End (T3 Nettlecyst attach) | Equipment decision |
| 11 | Boros Energy vs Tron (T3 burn-or-pressure) | Damage allocation |
| 12 | Domain Zoo vs Storm (T2 Ragavan vs Tarmo) | Tempo tradeoff |

For each fixture:
1. Both **heuristic** TurnPlanner and **MCTS** plan the next
   turn.
2. Replay each plan from the same seed forward to game-end (Bo1).
3. Repeat with 20 reseeded forward simulations (variance
   reduction).

**Pass criteria**: MCTS strictly dominates on ≥ 4 of 12 fixtures
(MCTS wins more games at p < 0.10) AND has 0 strict regressions.

**Pilot wall clock**: ~30 sec / fixture × 12 = 6 minutes. Total
acceptance run < 10 minutes.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Determinization sampling explodes with n_hand_cards | High | Cap n_determinizations at 50; sample by importance weight from `bhi.posterior`. |
| Rollout policy too slow | Medium | Use score_play (no full game-state mutation), not full simulation. |
| State copy via EVSnapshot leaks stale references | Medium | Unit-test EVSnapshot.fast_replace round-trip on a 16-deck cohort. |
| MCTS underperforms heuristic at equal budget | Medium | Acceptance gate catches this. If failure, escalate to widening rollouts (Hsueh-style RAVE) before re-evaluating. |
| Integration breaks matrix sim | Low | Opt-in only via `--mcts` flag. Default path unchanged. |

## Out of scope

- AlphaZero-style policy/value networks (Phase 5+).
- Sample-efficient learning (Hsueh's RAVE/AMAF) — defer to second
  iteration if vanilla UCT clears the acceptance gate.
- Full-game search to depth > 4 turns. Combat-step MCTS will be
  added separately if the main-phase pilot succeeds.

## Sequencing within Q3 2026

```
Week 1: Skeleton (uct_node, ismcts, determinizer) + smoke tests.
        No game integration yet.
Week 2: Action-set wiring through legal_plays. End-to-end on
        single Affinity vs Boros fixture. Iterate on UCB
        constant + rollout depth.
Week 3: Full 12-fixture acceptance run. Tune to pass
        (≥ 4 strict wins, 0 regressions).
Week 4: Integration as opt-in CLI flag. CLI smoke +
        documentation. Open PR.
```

## Decision points the prototype will inform

1. **MCTS vs CFR**: if MCTS clears the acceptance gate, CFR for
   the response sub-problem is a smaller subsequent project.
   If MCTS doesn't, CFR may be more direct.
2. **Default-on policy**: if MCTS proves robust at 100-rollout
   budget (~10 ms / decision), consider default-on for matrix
   sims.
3. **Combat planner unification**: today combat lives in
   `CombatPlanner._predict_blocks` (separate from main-phase
   planning). MCTS would unify these.
