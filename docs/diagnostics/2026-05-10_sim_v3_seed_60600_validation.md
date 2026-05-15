---
title: Sim v3 validation against seed 60600 G3 T4 chain-blindness trace
status: active
priority: secondary
session: 2026-05-10
supersedes: []
superseded_by: []
depends_on:
  - docs/design/2026-05-10_simulator_v3.md
  - docs/PHASE_D_DEFERRED.md
tags:
  - phase-d
  - simulator-v3
  - validation
  - seed-60600
summary: |
  Validates the multi-turn rollout in `ai/finisher_simulator_v3.py`
  against the chain-blindness trace at seed 60600 G3 T4 (decisions
  `g3t4d76` and `g3t4d78`). Confirms the rollout produces strictly
  positive chain-fuel score (5.0) where v2 produces zero, closing
  the projection-layer gap at the canonical state. Documents one
  residual gap — closer-in-library-only chain damage still
  collapses to 0 because `_tutor_access_contribution` remains a
  stub. Tests in `tests/test_finisher_simulator_v3_seed_60600_validation.py`
  pin the arithmetic and serve as the regression anchor for PR3c.
---

# Sim v3 validation — seed 60600 G3 T4

## What this doc validates

The Phase D plan (`docs/PHASE_D_DEFERRED.md`) cites the seed 60600
G3 T4 trace as the canonical instance of the projection-layer chain
-blindness gap: `compute_play_ev` returns ~-10 EV for chain-fuel
spells (Desperate Ritual, Past in Flames, Manamorphose, Reckless
Impulse) because the underlying simulator's single-turn projection
returns ``expected_damage = 0`` whenever the chain cannot fire on
the current turn.

Sim v3 (`ai/finisher_simulator_v3.py`, shipped PR #382 and PR #383)
adds a multi-turn rollout (`_project_multi_turn`) that projects
T+0, T+1, T+2, T+3 with snapshot deltas (+1 mana/land per offset,
storm reset per CR 500.4, life debited by `opp_power × offset`) and
picks the offset maximising `damage × survival × closer_reachable`.

This doc records the **exact v3 projection numbers** at the d76 and
d78 fixture states, compared against v2's single-turn projection at
the same state, and answers the question:

> Does sim v3 close the chain-blindness gap demonstrated in the
> seed 60600 G3 T4 trace?

**Answer: yes for the canonical d76 case (closer in hand, chain
mana-blocked this turn).** v3 returns argmax score 5.0 where v2
returns 0.0. **One residual gap remains** for the closer-in-library
case where no tutor is in hand — see §"Residual gap" below.

## Trace decisions cited

From `replays/affinity_vs_storm_60600.ndjson`:

### `g3t4d76` — end of Main1, storm=6

* State: Ruby Storm life=10, Affinity life=20, mana exhausted after
  6-spell chain (Ral → Wrenn's Resolve → Glimpse → Desperate Ritual
  + splice → Glimpse → Ruby Medallion).
* Hand size 8; Grapeshot is in hand (alternative scored at -5.63).
* NDJSON alternatives at d76:

| Action            | EV     | Source                          |
|-------------------|--------|---------------------------------|
| pass (chosen)     |  0.00  | tiebreaker default              |
| Grapeshot         | -5.63  | combo modifier hold             |
| Manamorphose      | -10.00 | **base projection, no credit**  |
| Desperate Ritual  | -10.07 | **base projection, no credit**  |
| Reckless Impulse  | -10.31 | **base projection, no credit**  |

### `g3t4d78` — end of Main2, storm=7

* State: 2 floating mana from Manamorphose; storm count = 7;
  graveyard contains every chain-spell already cast.
* Hand size 8; Past in Flames in hand (alternative scored at
  -10.28).
* NDJSON alternatives at d78:

| Action            | EV     | Source                          |
|-------------------|--------|---------------------------------|
| pass (chosen)     |  0.00  | tiebreaker default              |
| Desperate Ritual  | -9.95  | **base projection, no credit**  |
| Reckless Impulse  | -10.25 | **base projection, no credit**  |
| Past in Flames    | -10.28 | **base projection, no credit**  |

## v2 vs v3 projection numbers (this fixture)

All numbers from
`tests/test_finisher_simulator_v3_seed_60600_validation.py` run
locally on the commit at the head of this branch. The fixture
state mirrors the NDJSON board state at the cited decisions, but
hand composition uses oracle/keyword/tag-equivalent mocks (no
card-name dependence in the test — per the abstraction contract).

### d76 fixture (storm=6, my_mana=0, closer in hand)

**v2 single-turn projection** (`simulate_finisher_chain`):

| Field                    | Value     |
|--------------------------|-----------|
| `pattern`                | `"none"`  |
| `expected_damage`        | 0.0       |
| `success_probability`    | 0.0       |
| `next_turn_damage`       | 0.0       |
| `hold_value`             | 0.0       |
| **`v2_score`** (ED × P)  | **0.0**   |

Reason: at mana=0, no spell in hand is castable. `find_all_chains`
returns the empty set, `_project_storm` flags no chain reachable,
and the top-level entry returns `pattern="none"`. The chain-fuel
signal in `compute_play_ev` falls to its raw "card-from-hand minus
mana-spent" score (~-10), matching the NDJSON.

**v3 multi-turn rollout** (`_project_multi_turn`):

| offset | expected_damage | survival_p | closer_reachable_p | score  | mana_at_offset |
|--------|-----------------|-----------|--------------------|--------|----------------|
| 0      | 0.0             | 1.0       | 1.0                | 0.0    | 0              |
| 1      | 0.0             | 1.0       | 1.0                | 0.0    | 1              |
| 2      | 5.0             | 1.0       | 1.0                | **5.0**| 2              |
| 3      | 5.0             | 1.0       | 1.0                | 5.0    | 3              |

**v3 argmax: offset=2, score=5.0.**

The chain-blindness gap is closed at this state: v3 produces a
strictly positive score (5.0 > 0.0 = v2). Score formula:
`expected_damage × survival × closer_reachable` = 5 × 1.0 × 1.0.

Mechanic in plain English: at T+2 the +2 land drops give Storm 2
mana; the in-hand Grapeshot is castable; even without rituals
(which won't fire at mana<2 then mana=2 because Grapeshot itself
consumes the available mana), the chain finder finds the single-
spell Grapeshot chain (storm-count contribution from the
medallion-reduced cantrips that will be drawn). The 5-damage
projection reflects the storm closer firing at the +2-mana state.

### d78 fixture (storm=7, my_mana=2, PiF in hand, GY full)

**v2 single-turn projection**:

| Field                    | Value      |
|--------------------------|------------|
| `pattern`                | `"storm"`  |
| `expected_damage`        | 11.0       |
| `success_probability`    | 1.0        |
| `next_turn_damage`       | 4.0        |
| `hold_value`             | 3.96       |
| **`v2_score`** (ED × P)  | **11.0**   |

v2 *does* find a chain at d78: rituals + cantrips + Grapeshot are
castable at 2 mana with cost-reducer medallions; the chain finder
counts the splice-into-arcane shape and the GY flashback fuel from
PiF, returning 11 expected damage.

**v3 multi-turn rollout**:

| offset | expected_damage | survival_p | closer_reachable_p | score | mana_at_offset |
|--------|-----------------|-----------|--------------------|-------|----------------|
| 0      | 4.0             | 1.0       | 1.0                | 4.0   | 2              |
| 1      | 4.0             | 1.0       | 1.0                | 4.0   | 3              |
| 2      | 5.0             | 1.0       | 1.0                | **5.0** | 4            |
| 3      | 5.0             | 1.0       | 1.0                | 5.0   | 5              |

**v3 argmax: offset=2, score=5.0.**

Here v3 is **strictly conservative** vs v2: v3's offset-0 projection
sees only 4.0 expected damage because it resets `storm_count=0` per
CR 500.4 — the chain restarts the storm count, ignoring the 7
already-cast spells from this turn. v2's projection keeps
`storm_count=7` because the v2 call is for "fire NOW with the
current chain" — v3's "fire on this offset (= a future turn)" is a
semantically different question.

This is **not a regression** — both are rule-correct. v2 answers
"if I cast Grapeshot right now, what damage?". v3 answers "what
does the chain look like if I commit it to a fresh turn?". The
diagnostic doc records this so PR3c's wire-up does not naively
substitute one for the other.

### Residual gap — no closer in hand, closers in library

**State**: 4 mana, storm=0, hand has 2 rituals + PiF + 1 cantrip,
no Grapeshot in hand. Library composition declares 3 storm closers.

**v2**: `pattern="storm"`, `expected_damage=0`,
`success_probability=0` (no in-hand closer → `find_all_chains`
finds no chain that reaches a payoff).

**v3**:

| offset | expected_damage | closer_reachable_p | score |
|--------|-----------------|--------------------|-------|
| 0      | 0.0             | 0.0000             | 0.0   |
| 1      | 0.0             | 0.0857             | 0.0   |
| 2      | 0.0             | 0.1664             | 0.0   |
| 3      | 0.0             | 0.2422             | 0.0   |

`closer_reachable_p` correctly grows with offset (hypergeometric on
3 closers in a 35-card library), but `expected_damage = 0` at every
offset because the chain finder still requires a closer in hand to
compute storm damage. **Score therefore collapses to 0** —
the chain-blindness gap is **not** closed for this case.

## Does v3 close the chain-blindness gap?

**Mixed — yes for the canonical d76 case, no for closer-in-library-
only with no tutor.**

Explicit yes/no:

| Case                                                       | Closed? | Evidence                                                       |
|------------------------------------------------------------|---------|----------------------------------------------------------------|
| Closer in hand, current-turn mana blocks chain (d76)       | **YES** | v3=5.0, v2=0.0; rollout finds chain at offset>=2               |
| Closer in hand, current-turn chain fires (d78)             | **YES** | v3=5.0, v2=11.0; both are rule-correct, different questions    |
| Closer in library only, library closers exist              | **NO**  | v3=0.0, v2=0.0; chain finder requires in-hand closer           |
| Closer in SB, tutor in hand                                | UNKNOWN | `_tutor_access_contribution` is `NotImplementedError`           |

## Open issues

1. **`_tutor_access_contribution` is still a stub** (`ai/finisher_simulator_v3.py:490`).
   The design (`docs/design/2026-05-10_simulator_v3.md` §4) calls
   for Wish-in-hand to credit SB-Grapeshot access at `+wish.cmc`
   mana cost with `1 - p_counter` resolution probability.  Without
   this, the closer-in-SB chain-fuel signal is invisible to v3.

2. **v2 chain finder requires closer in hand to produce
   non-zero damage** even when `closer_reachable_p > 0` from
   library composition. The architecturally clean fix is to
   inject a synthetic drawn closer into the chain finder's hand
   argument when `closer_reachable_p > P_threshold`. The cleanly-
   factored version is the v2 sketch
   `_project_storm_with_tutor_access` (`ai/finisher_simulator.py:456-572`)
   — generalising it to "drawn closer" instead of "tutored closer"
   is the next implementation step.

3. **d78 v3 conservatism vs v2** — v3 strictly resets storm count
   for fresh-turn projections (CR 500.4-correct). v2's call site
   passes the current `storm_count=7`. When the callsite migrates
   to v3, the migration MUST decide whether "fire now with current
   storm" should be a special-cased offset-0 projection (with
   actual current `storm_count`) or whether the v3 reset is the
   right model for the AI's hold-vs-fire decision. The design doc
   §5.1 step 3 chose the reset; PR3c's wire-up must validate this
   choice does not regress Storm field WR.

## Is Phase D migration (PR3c) safe to attempt?

**Not yet.** Two prerequisites:

1. Implement `_tutor_access_contribution` (currently
   `NotImplementedError`). The seed 60600 trace has Wish in
   Storm's mainboard and SB Grapeshot — without tutor wiring,
   v3 still scores the Wish→SB path at 0, the same as v2.

2. Either (a) generalise the in-hand closer requirement so that
   `closer_reachable_p > 0` injects a synthetic drawn closer
   into the chain finder, OR (b) wire the v2
   `_project_storm_with_tutor_access` path into `_project_multi_turn`
   so the SB/library closer can produce damage.

The d76 chain-blindness closure IS demonstrated by these tests —
that's the most important structural validation. The next session
can target either prerequisite as the next step.

## Evidence locations

* Trace NDJSON: `replays/affinity_vs_storm_60600.ndjson`
  (decisions `g3t4d76` line 391, `g3t4d78` line 397).
* v2 simulator: `ai/finisher_simulator.py`.
* v3 simulator: `ai/finisher_simulator_v3.py`
  (rollout at L496, library composition L273, p_draw_closer L371,
   tutor stub L453, top-level stub L770).
* Tests: `tests/test_finisher_simulator_v3_seed_60600_validation.py`
  (8 tests, all green on this branch).
* Design doc: `docs/design/2026-05-10_simulator_v3.md`.
* Phase D plan: `docs/PHASE_D_DEFERRED.md` §"Concrete trace examples"
  (Trace 2 is the seed 60600 narrative).
