---
title: "Phase 4H — Counterfactual Regret Minimization (CFR) prototype scoping"
status: active
priority: secondary
session: 2026-05-10
depends_on:
  - docs/research/2026-05_mtg_ai_landscape.md
  - docs/research/2026-05_phase_4a_ismcts_scoping.md
tags: [research, ai, cfr, prototype, response, architecture]
summary: >
  Concrete file plan, abstraction strategy, and acceptance gate
  for a tabular CFR+ implementation targeting MTG response sub-
  problems (block allocation, counterspell-vs-hold, removal-
  target-in-response). Designed to land after Phase 4A (ISMCTS)
  proves out the search infrastructure.
---

# Phase 4H — CFR Prototype Scoping

## Why CFR for MTG response decisions

Counterfactual Regret Minimization (CFR) is the algorithm that
solved heads-up no-limit poker (Brown & Sandholm 2017,
Pluribus 2019). It iterates over information sets (visible
states) and updates a regret table per (infoset, action) pair;
the average strategy converges to a Nash equilibrium for two-
player zero-sum games. CFR+ adds non-negativity constraints
that empirically converge ~2× faster than vanilla CFR.

For MTG, **CFR is most applicable to the response sub-problem
class**:

  - **Counterspell-vs-hold** — opponent casts a threat. Should
    I counter now or hold for a bigger threat? Information set
    = (visible game state, my hand summary). Actions = {counter,
    hold}. Game value = post-resolution position EV.
  - **Block allocation** — opponent attacks with a swarm. Which
    of my creatures block, and against whom? Information set =
    (attacker stats, blocker stats, my hand size, opp hand
    size, my life, opp life). Actions = {block-assignment ID}.
    Game value = post-combat position + next-turn projection.
  - **Removal-target-in-response** — opponent casts a creature.
    Should I Bolt the carrier in response, or save it for a
    bigger problem? Information set = (visible threats, my
    hand contents, removal stack).

These are **small information-set games** (10⁴–10⁶ infosets
with smart bucketing), which is exactly the regime where tabular
CFR+ converges in minutes on commodity hardware.

CFR is **less suitable for full-game search** — the game-tree
size is too large; ISMCTS (Phase 4A) dominates there.

## Why this is Phase 4H, not 4G

The plan's recommendation is to ship Phase 4A (ISMCTS) first.
Reasons:

1. **Faster value**: ISMCTS at 12 fixtures gives qualitative
   feedback in week 4. CFR needs an abstraction definition +
   convergence time before the first measurable result.
2. **Information-set abstraction risk**: defining a good
   abstraction is the hard part of CFR. A bad abstraction
   undermines the convergence guarantee. Phase 4A ships a
   working search platform that informs what abstractions
   matter.
3. **Phase 4A may already cover the response problem**: ISMCTS
   with a small-depth rollout on response decisions might be
   sufficient. CFR adds value only if Phase 4A acceptance
   shows ISMCTS underperforms on response sub-problems.

## Information-set abstraction strategy

CFR's table size is `O(infosets × actions)`. For MTG response
problems, the raw state space is enormous, but the **decision-
relevant state** is much smaller.

### Block-allocation abstraction (~10⁴ infosets)

```python
@dataclass(frozen=True)
class BlockInfoset:
    # Attacker bucket: (count, total_power, total_toughness,
    # has_evasion). Power/toughness binned into [0-1, 2-3, 4-5, 6+].
    n_attackers: int                    # 0-4 (4+ truncated)
    attackers_power_bucket: int         # 0-3
    attackers_evasion_count: int        # 0-2

    # Blocker bucket: same shape.
    n_blockers: int
    blockers_power_bucket: int
    blockers_toughness_bucket: int

    # Defender life bucket: critical / safe / behind.
    defender_life_bucket: int           # 0-2
    defender_hand_size_bucket: int      # 0-2

    # Opp hand summary from BHI (counter/removal density buckets).
    opp_threat_density_bucket: int      # 0-2
```

Total infosets: 5 × 4 × 3 × 5 × 4 × 4 × 3 × 3 × 3 ≈ 21,600.
Actions per infoset: 1 + n_blockers (no-block + 1-block-each
+ 2-block combos). Capped at ~16 per infoset for tractability.

Total table size: 21,600 × 16 = 345,600 (regret + strategy
floats × 4 bytes ≈ 2.7 MB). Easy fit.

### Counterspell-vs-hold abstraction (~10³ infosets)

```python
@dataclass(frozen=True)
class CounterInfoset:
    # The spell on the stack.
    spell_threat_bucket: int            # 0-3 (low / mid / high / lethal)
    spell_cmc_bucket: int               # 0-3

    # My hand: how many counters do I have?
    my_counter_count: int               # 0-3 (3+ truncated)
    my_mana_bucket: int                 # 0-3 (0 / 1-2 / 3-4 / 5+)

    # Game state.
    my_life_bucket: int                 # 0-2
    opp_threat_density_bucket: int      # 0-2

    # Turn pressure.
    turns_to_lethal_bucket: int         # 0-3 (1 / 2 / 3 / 4+)
```

Total: 4 × 4 × 4 × 4 × 3 × 3 × 4 ≈ 9,216. Actions: {counter,
hold}. Table ~73 KB.

## File plan

```
ai/cfr/
├── __init__.py
├── infoset.py            # @dataclass infosets + bucketing helpers
├── solver.py             # CFR+ iteration loop (~150 LOC)
├── strategy.py           # frozen strategy table + lookup
└── trainer.py            # offline training harness, saves to disk

docs/research/
└── 2026-05_phase_4h_cfr_scoping.md     # this doc

tests/
├── test_cfr_solver_smoke.py           # converges on Kuhn poker
├── test_cfr_block_abstraction.py      # bucketing correctness
└── test_cfr_acceptance.py             # block-decision fixtures
```

## Acceptance gate

The Phase-5 A/B harness in `ai/search/ab_compare.py` already
exists; CFR uses the same pattern with a different planner:

  - Build 12 block-decision fixtures (mirror the ISMCTS corpus
    structure).
  - Compare CFR-solved strategy vs the heuristic
    `_predict_blocks` from `ai/turn_planner.py`.
  - Pass criteria (mirror Phase 4A): ≥ 4 strict CFR wins, 0
    heuristic wins.

Wall-clock budget: training to ε-Nash convergence ~5 min,
acceptance run ~3 min. Total < 10 min — fits the inner-loop
verification protocol.

## Sequencing within Q4 2026

```
Week 1: Kuhn-poker smoke test for the solver (validates
        correctness on the canonical 6-card 2-player game).
Week 2: BlockInfoset + bucketing. Train on 12 fixtures.
Week 3: A/B fixtures + acceptance harness wired.
Week 4: CounterInfoset + a second sub-problem (counter-vs-hold).
        Document tradeoffs between MCTS and CFR per problem.
```

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Abstraction loses too much info → poor solve | Medium | Validate on synthetic hand-tuned cases first; widen buckets if regret stays high. |
| Train-time-too-long for daily use | Low | Pre-compute strategies offline, save to JSON; runtime is a table lookup. |
| Game-tree-size explosion in block alloc | Medium | Cap n_attackers at 4 (truncate the rest into a pooled bucket); cap action set at 16 per infoset. |
| MCTS already handles the problem well | Low | Phase 4A acceptance reveals this; CFR descopes if so. |

## What this document supersedes

Nothing — extends `docs/research/2026-05_mtg_ai_landscape.md`
§2 (CFR survey) with concrete scoping. The landscape doc stays
as the high-level reference; this doc is the per-phase
deliverable record.

## Frontmatter for discovery

This doc carries `status: active` so future sessions running
the discovery protocol (CLAUDE.md section "Session Priorities")
will surface it. When Phase 4H ships, transition `status:
superseded` and add a `superseded_by:` pointer to the
implementation PR.
