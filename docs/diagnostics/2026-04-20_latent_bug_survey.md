---
title: Latent-bug engine survey (Phase 11c)
status: active
priority: diagnostic
session: 2026-04-21
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
  - docs/history/audits/2026-04-11_LLM_judge.md
tags:
  - engine
  - audit
  - backlog
  - phase-11
summary: "Read-only survey of engine/*.py for latent bugs with weak/no test coverage. 10 prioritized items; S-2 (SBA constant) and S-8 (March x_val) resolved 2026-04-21 with Option-C test-first fixes. Remaining open: S-1 (counterspell post-bounce), S-3/S-10 (rebound cleanup), S-4/S-5 (escape/cascade target-loss), S-6 (Living End ETB SBA batching), S-7 (62 handlers with no tests), S-9 (counterspell during sorcery-speed lock)."
---
# Phase 11c — Latent-bug engine survey

## Method

Read-only Explore-agent survey of `engine/*.py` against `tests/`. Goal
was to surface surfaces that have **zero or weak test coverage and
could produce silent wrong results**, not to validate any specific
bug. Every item below is a *hypothesis* derived from code reading;
none have been reproduced in a failing test yet.

Out of scope for the survey (kept untouched):
- AI/EV layer — had deep attention in Phases 1-10.
- Bugs already tracked in `PROJECT_STATUS.md` P0/P1 section.
- Deck balance issues from the N=50 matrix (Azorius Control 15%, Living
  End 27%, Goryo's 23%). Those are open P0s from the LLM-judge audit,
  not new findings.

## High priority — candidate silent wrong results

### [S-1] Counterspell targeting not re-validated post-bounce
- **Symptom:** A counterspell whose target creature bounces during the
  response window may fizzle correctly *or* crash silently depending
  on path. Validation at `engine/game_state.py:2703-2714` checks stack
  targets by `instance_id` but doesn't re-validate that the target
  still exists on the stack, or that a prior bounce already fizzled
  the target.
- **Repro sketch:** Cast Ephemerate on own creature → opponent
  Counterspells Ephemerate → blink bounces creature first → does
  Counterspell fizzle cleanly or proceed with stale target?
- **Lines:** `engine/game_state.py:754-762`, `2703-2714`.
- **Fix cost:** M. No dedicated test; recommend repro test first.

### [S-2] SBA iteration bound hardcoded (20), not `SBA_MAX_ITERATIONS` — **RESOLVED (2026-04-21)**
- **Symptom:** `engine/sba_manager.py:42` hardcodes `max_iterations =
  20` instead of consuming the declared `SBA_MAX_ITERATIONS` constant
  from `engine/constants.py`. Maintenance gap: bumping the constant
  won't propagate. Low blast radius today but a trap for future work.
- **Fix cost:** S. One import + one replacement.
- **Note:** Probably should land with a regression test that exercises
  an SBA loop of length > 1 and asserts termination.
- **Resolution:** Import added, loop now binds `max_iterations =
  SBA_MAX_ITERATIONS`. Tests:
  `tests/test_sba_uses_max_iterations_constant.py` (3 cases: source
  import check, literal-20 absence, monkeypatch propagation).

### [S-3] Rebound cards accumulate — no end-of-turn cleanup path
- **Symptom:** `_rebound_cards` list grows at
  `engine/game_state.py:1856-1858`. `end_of_turn_cleanup()` at line
  3465+ only cleans Ragavan, Dash, and Goryo's exile paths. No code
  path observably consumes rebound exile on the scheduled upkeep, so
  the mechanic may be a no-op after a turn passes.
- **Repro sketch:** Cast any rebound spell → end turn → opponent's
  turn → own upkeep → assert card is castable from the exile zone
  (or at least offered to the AI).
- **Fix cost:** M (need upkeep trigger that consumes the list).
- **See also:** [S-10] below — possibly dead code path.

## Medium priority — interaction gaps

### [S-4] Escape doesn't re-validate post-bounce target loss
- **Symptom:** Escape (Phlage) exiles cost cards then creates the
  creature. If a spell bounces the target / breaks the escape cost
  mid-resolution, the cost is paid but the creature may not enter.
  No post-resolution validation.
- **Lines:** `engine/game_state.py:1294-1312`, `1896-1905`.

### [S-5] Cascade free-cast doesn't re-validate target legality
- **Symptom:** Cascade at `engine/game_state.py:2005-2070` picks a
  free-cast card and hands it to the stack. If the cascade parent
  (e.g., Living End) already changed the board in a way that
  invalidates the free-cast card's targets, cascade may still resolve
  with stale targeting.
- **Lines:** `engine/game_state.py:2055-2062`.

### [S-6] Living End post-ETB SBA batching may let 0-toughness creatures act
- **Symptom:** `_resolve_living_end()` calls `_handle_permanent_etb()`
  for each returned creature but SBAs are only checked at the end of
  the turn loop cycle, not per-ETB. A returned creature that ETBs at
  0 toughness (from a replacement effect) could persist through the
  batch resolution and be available for combat.
- **Lines:** `engine/game_state.py:2075-2135`,
  `engine/game_runner.py:474`.

## Medium priority — handler coverage gaps

### [S-7] 62 EFFECT_REGISTRY handlers with zero test references

Top 10 by approximate deck-wide copy count:

| Handler | Approx copies | Reason to prioritize |
|---|---:|---|
| Snapcaster Mage | ~16 (2×8 decks) | flashback cast + post-bounce target loss |
| Arboreal Grazer | ~8 (Amulet × 2 lists) | ETB land → Amulet mana loop depends on correct firing |
| Walking Ballista | ~8 | X-cost death trigger; potential infinite loop |
| Explore | ~4 | ETB land + scry ordering with deck search |
| Scapeshift | ~3 | cascaded ETBs on N lands |
| Isochron Scepter | ~2 | imprint + exile cast interaction |
| Seasoned Pyromancer | ~2 | discard output fuels Ruby Storm combo |
| Emry, Lurker of the Loch | ~2 | cast-artifact-from-graveyard |
| Quantum Riddler | ~2 | foretell path unverified |
| All Is Dust | ~2 | mass exile; suspension not implemented |

**Fix cost:** M per handler, but most failures are quick to repro once
a handler is selected. A session-sized chunk is probably "test the
top-3 from this table, fix any reproducing bug."

## Lower priority — future-proofing

### [S-8] March of Otherworldly Light X from land count, not mana paid — **RESOLVED (2026-04-21)**
- **Symptom:** `engine/card_effects.py:675` uses `x_val =
  len(players[controller].lands)`. March cast for X=1 at 5 lands
  exiles permanents with CMC ≤ 5 instead of ≤ 1. Audit P1 bug B.
- **Fix cost:** S. Listed here (not in P0) because it's already tracked.
- **Resolution:** Resolver now reads `x_val = item.x_value if item
  and hasattr(item, 'x_value') else 0`, mirroring the Wrath of the
  Skies pattern. Tests:
  `tests/test_march_x_from_item_not_lands.py` (2 cases: X=0 preserves
  Ragavan CMC 1, X=1 exiles highest-threat target).

### [S-9] Counterspell not gated during opponent-controlled
"sorcery-speed-only" effect
- **Symptom:** Teferi-style static is checked by
  `engine/game_runner.py:30-42` but only in `AICallbacks`; core
  `decide_response()` path in `ai/response.py` doesn't consult it.
- **Note:** Straddles AI/engine. May already be partially handled;
  needs a confirming test rather than a speculative fix.

### [S-10] Rebound upkeep-trigger handler absent
- **Symptom:** See [S-3]. `_rebound_cards` has no consumer. If this
  is dead code, delete it and the `has_flashback` innate-flashback
  logic may already subsume any needed behaviour; if it's live, the
  upkeep handler is missing.
- **Fix cost:** M (either delete or implement).

## Recommended session slicing

One session per group is roughly correct:

- **Session N+1 — validation.** Repro [S-1], [S-3], [S-5], [S-6] with
  targeted failing tests. Confirm or falsify before touching code.
  Expected outcome: 2-3 of them are real, 1-2 are false alarms from
  the survey.
- **Session N+2 — fixes for confirmed.** Option-C test-first fixes for
  whatever validated in N+1. Include [S-2] and [S-8] as small
  cleanup riders.
- **Session N+3 — handler coverage.** Walk the top-3 from [S-7] —
  Snapcaster, Arboreal Grazer, Walking Ballista — write one test per
  handler; fix anything that fails.

## What we did NOT do in 11c

- Did not write any tests.
- Did not modify any engine code.
- Did not run `run_meta.py` for repros.
- Did not attempt to validate any item against live game state.

The diagnostic is the deliverable. Validation is for the next session.
