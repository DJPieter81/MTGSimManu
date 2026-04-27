---
title: Combo + simulator unification — multi-session rewrite
status: active
priority: primary
session: 2026-04-27
supersedes: []
superseded_by: []
depends_on: [docs/PHASE_D_DEFERRED.md, PR #204, commit 15e928a]
tags: [combo, finisher-simulator, migration, abstraction-contract, multi-session]
summary: |
  Rewrite plan that retires `ai.combo_calc.card_combo_modifier` (310
  LOC of patches at `ai/combo_calc.py:616`) and unifies the four
  combo modules (`combo_calc`, `combo_chain`, `combo_evaluator`,
  `finisher_simulator`) behind a single entry point
  `evaluate_combo(state) → ComboEval`, then collapses the
  ev_player → combo_evaluator → finisher_simulator dispatch into a
  single rollout call. Sequenced across five sessions so the WR
  matrix stays green between sessions and Phase D's failure mode
  (Storm 44.8% → 5.3%) does not recur.
---

# Combo + simulator unification — multi-session rewrite

## 0. Anchors

- Branch: `claude/rewrite-combo-simulator-f4ecR`
- Live caller: `ai/ev_player.py:516` (single site —
  `ev += card_combo_modifier(card, ...)`)
- Replacement scaffolding already in tree but **not wired**:
  - `ai/combo_evaluator.py:191 card_combo_evaluation`
  - `ai/finisher_simulator.py:simulate_finisher_chain`
- Reverted attempt: commit `c9e3769` (Phase D), root cause in
  `docs/PHASE_D_DEFERRED.md`
- Last green Sprint: `15e928a feat(simulator): Sprint 1 — multi-turn
  rollout via depth-bounded next_turn_proj`
- Abstraction baseline: 7 (post-`55508de`). The five sessions must
  ratchet down, never up.

## 1. Why a single big rewrite is the wrong shape

The slogan "rip out card_combo_modifier in one PR" sounds clean but
collides with the loop-break protocol in `CLAUDE.md`. Phase D already
proved that a single-PR cutover regresses Storm from 44.8% → 5.3%
because the marginal-delta approach loses information that the patch
pile encodes implicitly (hold-vs-fire, ritual patience at storm=0 vs
storm≥1, search-tax awareness). Splitting the rewrite across five
sessions, with each session ending on a green matrix, is the
discipline that prevents another revert.

## 2. Session sequence

Each session ends with: green pytest, abstraction baseline ≤ current,
N=20 matrix on the affected decks within ±5pp of the prior session's
WR. Between sessions, the previous session's commits stay merged on
`main`; this branch rebases forward.

### Session 1 — Failing-test scaffolding, no behaviour change

**Goal:** Lock the contract before any code moves.

- Add `tests/test_combo_evaluator_contract.py`. Each test names a
  rule, not a card. Required tests:
  - `test_chain_fuel_credited_when_chain_reachable`
  - `test_storm_hard_hold_when_chain_unreachable`
  - `test_tutor_scores_as_closer_when_sb_or_library_has_payoff`
  - `test_cost_reducer_arithmetic_matches_storm_count`
  - `test_ritual_patience_at_storm_zero` and `…_at_storm_geq_one`
  - `test_flip_transform_stack_batching`
  - `test_search_tax_awareness`
- Each test runs against the **current** `card_combo_modifier` and
  pins its output. They go green on session 1.
- No production code change. Test-only commit.

**Exit criteria:** `pytest tests/test_combo_evaluator_contract.py -q`
green, 7 tests passing, abstraction baseline unchanged.

### Session 2 — Wire combo_evaluator behind a feature flag

**Goal:** Two implementations live side-by-side, default off.

- Add `STRATEGY_PROFILE.combo_evaluator_enabled: bool = False` in
  `ai/strategy_profile.py`.
- In `ev_player.py:510-516`, branch on the flag:
  - off → existing `card_combo_modifier` path
  - on → new `card_combo_evaluation` path
- Re-run Session 1 contract tests against **both** paths
  (parameterise the test fixture). Both must pass.
- Run Storm field N=20 with the flag on. If WR drops > 5pp, the
  contract test that should have caught it is missing; add it,
  fix `card_combo_evaluation`, repeat. Do not flip the default.

**Exit criteria:** Both paths pass the contract suite. Flag stays
off in `main`.

### Session 3 — Flip the default, delete `card_combo_modifier`

**Goal:** Single live path.

- Flip `combo_evaluator_enabled = True`.
- Run full 16-deck field at N=20 against the previous session's
  baseline. Tolerance ±5pp per deck.
- Delete `card_combo_modifier` from `ai/combo_calc.py` (lines
  ~451-880, ~430 LOC including comments). Keep `assess_combo` and
  `_compute_combo_value` — those are pure math used by both paths.
- Remove the feature flag.
- Ratchet the abstraction baseline if any name-checks dropped out
  with the deletion.

**Exit criteria:** `card_combo_modifier` does not appear in `git
grep`. Storm field N=20 within 5pp of the Session 2 baseline.
`combo_calc.py` shrinks by ≥ 300 LOC.

### Session 4 — Re-attempt Phase D on the cleaned-up surface

**Goal:** Rescue the marginal-delta idea now that the legacy code
path is gone, using the lessons from `docs/PHASE_D_DEFERRED.md`.

The previous failure was that `Δ = sim(after) − sim(before)` returned
0 for chain pieces because the chain finder included the candidate
card in *both* projections. Two fixes:

1. **Per-card chain marginal:** in `simulate_finisher_chain`,
   return `chain_relevance: Dict[card_id, float]` so the caller
   reads the candidate's contribution directly, no diffing.
2. **Hold-vs-fire projection:** extend `FinisherProjection` with
   `next_turn_chain_value` (already exists from Sprint 1's
   `next_turn_proj`) and let the evaluator compare *fire-now-EV*
   vs *hold-and-chain-next-turn-EV* explicitly.

This is the work that Phase D deferred. It now has a clean
substrate.

**Exit criteria:** Storm hold-vs-fire decision changes on at least
one seed in a way that improves WR. No regression > 5pp on any
deck. The two paragraphs in `docs/PHASE_D_DEFERRED.md` documenting
the original failure mode get a follow-up frontmatter
(`status: superseded`, `superseded_by: [combo_simulator_unification]`).

### Session 5 — Simulator unification

**Goal:** Collapse the four combo modules into one entry point.

Today the call graph is:

```
ev_player._score_spell
  → combo_evaluator.card_combo_evaluation
    → finisher_simulator.simulate_finisher_chain
      → combo_chain.find_all_chains
      → combo_calc._compute_combo_value
```

Replace with a single `ai/combo.py` module exposing:

```python
def evaluate_combo(state: GameState, player_idx: int,
                   candidate: Optional[CardInstance] = None
                   ) -> ComboEval: ...
```

`ComboEval` is a Pydantic model (extending the schema convention in
`ai/schemas.py`) with: `value: float`, `chain_relevance:
Dict[str, float]`, `hold_vs_fire: Literal["fire", "hold", "n/a"]`,
`pattern: Literal["storm", "cascade", "reanimate", …]`.

The four old modules either disappear or become private helpers
inside `ai/combo.py`. `combo_calc.assess_combo` survives as
`ai/combo.py:_assess_combo` (private). `combo_chain.find_all_chains`
becomes `ai/combo.py:_find_chains`. `finisher_simulator` and
`combo_evaluator` are deleted.

**Exit criteria:** `ai/combo*.py` reduces to one file ≤ 600 LOC.
Full 16×16 N=50 matrix within 3pp of Session 4 baseline on every
deck. Abstraction baseline ratcheted to its new floor.

## 3. Risk register

| Risk | Mitigation |
|---|---|
| Storm regresses again at the Session 3 flip | Session 1 contract tests catch it before flip |
| Phase D fails again at Session 4 | Per-card chain_relevance (not diffing) sidesteps the original failure mode |
| Cross-session drift on `main` | Each session rebases this branch onto `main`, re-runs Session 1 contract suite |
| Abstraction baseline creeps up mid-rewrite | CI ratchet blocks any session that adds a name-check |
| 5 sessions stretches into 10 | Loop-break: if Sessions 3, 4, 5 each regress on the same deck, halt and write a `docs/` post-mortem before continuing |

## 4. What this proposal does NOT do

- Does not touch `engine/game_runner.py`'s turn loop. The "simulator"
  here is the *combo finisher* simulator (`finisher_simulator.py`),
  not the rules engine.
- Does not change BHI, clock, or strategy_profile semantics.
- Does not introduce new card-name checks. Every detection stays
  oracle/keyword/tag-driven per the abstraction contract.

## 5. Approval gate

This is a proposal. No code lands until the user OKs the session
sequence. After approval, Session 1 lands as a standalone PR titled
`combo: contract tests for finisher evaluator (rewrite session 1/5)`.
