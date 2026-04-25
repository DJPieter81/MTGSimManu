---
title: Phase 2c combo refactor — delete `_combo_modifier`, port to `card_combo_modifier`
status: archived
priority: historical
session: 2026-04-25
supersedes:
  - 2026-04-25_phase2b_retry  # implicit; PR #183 closed without merging
superseded_by: []
depends_on: []
tags: [combo, storm, refactor, ev-scoring, ai]
summary: >
  Replaced the 440-LOC `_combo_modifier` in ai/ev_player.py with the
  principled `card_combo_modifier` from ai/combo_calc.py.  Ported five
  load-bearing logic blocks (storm-finisher predicates, mid-chain
  gate, storm-coverage escalation, draw-miss cascade risk) and added
  identity-based per-snapshot caching for performance.  Matrix gate
  N=20 passed: all 17 decks within ±5pp; Storm +1.1pp, Goryo's +4.1pp.
---

# Phase 2c combo refactor

## Goal

Unify combo-deck spell scoring onto a single principled module.
The `_combo_modifier` method was a 440-LOC bag of zone, role, and
finisher-access logic with five `storm_patience` clamps.  The
parallel `card_combo_modifier` in `ai/combo_calc.py` had cleaner
architecture (zone-aware, role-aware, arithmetic-derived) but was
dead code — never wired into `_score_spell`.

This phase deleted the legacy method and made `card_combo_modifier`
the sole combo-scoring path.

## Method

Phase plan in `/root/.claude/plans/lets-first-do-a-curried-rocket.md`:

| PR | Scope |
|---|---|
| PR-A (#184) | Subtlety engine fix — prerequisite for deterministic matrix runs |
| PR-B (#185) | Phase 2c.1 state-query routing — proved the OutcomeDistribution dispatcher needed bridge calibration; closed without merge |
| PR-C (#186) | Hardened `card_combo_modifier` with 14 unit tests covering 8 previously-uncovered branches |
| PR-D+E (#189) | Delete `_combo_modifier`, port 5 logic blocks into `card_combo_modifier`, add identity-based cache |

The hard delete (PR-D+E #189) was committed and tested incrementally:

1. WIP commit `491d09a` — wire `card_combo_modifier` *additively* alongside `_combo_modifier`
2. WIP commit `b9565d9` — delete `_combo_modifier` (broke 6 unit tests + made Storm games take 30s vs ~5s baseline)
3. Commit `018daa7` — port the 5 missing logic blocks into `card_combo_modifier`; tests pass; games still 20s/game
4. Commit `4ea268c` — identity-based per-snapshot cache; games back to 5.6s

## Result

Matrix gate N=20 (vs pre-2c baseline at `/tmp/phase2c1_baseline.json`):

| Deck | Pre | Post | Δ |
|---|---:|---:|---:|
| Goryo's Vengeance | 6.5% | 10.6% | **+4.1** |
| Pinnacle Affinity | 64.4% | 68.4% | +4.0 |
| 4/5c Control | 43.4% | 46.9% | +3.5 |
| Izzet Prowess | 47.5% | 50.0% | +2.5 |
| Affinity | 87.9% | 89.4% | +1.5 |
| Jeskai Blink | 63.6% | 65.0% | +1.4 |
| Dimir Midrange | 54.2% | 55.3% | +1.1 |
| **Ruby Storm** | 18.9% | 20.0% | **+1.1** |
| Azorius Control | 18.8% | 19.7% | +0.9 |
| 4c Omnath | 61.9% | 62.2% | +0.3 |
| Eldrazi Tron | 73.1% | 72.8% | −0.3 |
| Living End | 39.5% | 39.1% | −0.4 |
| Azorius Control (WST v2) | 39.5% | 39.1% | −0.4 |
| Boros Energy | 76.1% | 75.3% | −0.8 |
| Domain Zoo | 76.1% | 75.3% | −0.8 |
| Azorius Control (WST) | 35.5% | 34.1% | −1.4 |
| Amulet Titan | 49.1% | 45.9% | −3.2 |

**Verdict:** Pass.  All 17 decks within ±5pp tolerance.  Both
protected combo decks (Ruby Storm, Goryo's Vengeance) above the
−2pp floor.

## Comparison with prior attempts

| Deck | 2b retry (#183) | 2c.1 (#185) | 2c.3 hard (#189) |
|---|---:|---:|---:|
| Ruby Storm | −20.4 | −7.3 | **+1.1** |
| Goryo's Vengeance | −13.0 | +1.5 | **+4.1** |
| Living End | +18.6 | +0.6 | −0.4 |

The structural diagnosis embedded in PR #183's body — that the
`OutcomeDistribution` is a single-turn model and cannot represent
multi-turn combo setup — held up.  Phase 2c.1 narrowed the dispatcher
to lethal-this-turn chains only, which fixed Goryo's and Living End
but left Storm regressing because the bridge constant (`LETHAL_VALUE
* dist.expected_value()`) discounted by `p_disrupted` and
under-committed to lethals.  The hard refactor abandoned the
dispatcher path entirely; with `card_combo_modifier` carrying the
same multi-turn semantics legacy did, no bridge is needed.

## Performance

Identity-based per-snapshot cache on `assess_combo` (the chain-search
function called by every spell evaluation in combo decks):

| Configuration | 5 Storm vs Azorius BO3 | Per game |
|---|---:|---:|
| Main (with `_combo_modifier`) | ~25s | ~5s |
| Hard delete (no port, no cache) | timeout / 5hr matrix | ~30s |
| Port only (no cache) | 102s | ~20s |
| **Port + cache (#189)** | **28s** | **~5.6s** |

The cache is correct because all spells scored within one
`decide_main_phase` call share the same `EVSnapshot` object (created
upstream by the caller).  When the snap changes, `id(snap)` changes
and the cache invalidates automatically.

## What stays / what goes

**Stays in `ai/outcome_ev.py`:**
- `OUTCOME_DIST_COMBO = False` flag (Phase 2a-style dispatcher remains
  dormant; revisit if multi-turn lookahead is added to
  `build_combo_distribution`)
- `build_combo_distribution(...)` and the 5-outcome distribution
  framework — useful research artefact, low maintenance cost

**Removed:**
- `_combo_modifier` and its 440 LOC
- All 5 `storm_patience` clamps (the `storm_patience` field on
  `StrategyProfile` is now unused but retained for `run_meta.py`'s
  diagnostic print at `run_meta.py:434`)

## Open follow-ups

- Run N=50 matrix on main for statistical confirmation (the merge
  used N=20 for iteration speed)
- Reassess `StrategyProfile.storm_patience` — currently dead data;
  either delete or repurpose as a docstring-only marker
- Consider deleting `OUTCOME_DIST_COMBO` flag + `build_combo_distribution`
  if no future phase uses them within ~3 sessions

## References

- Plan: `/root/.claude/plans/lets-first-do-a-curried-rocket.md`
  (Phase 2c section)
- Closed PRs: #183, #185
- Merged PRs: #181, #182, #184, #186, #189
