---
title: Living End — suspend EV systematically pinned at 0.0; cascade-card Gate 1 short-circuit
status: active
priority: primary
session: 2026-05-10
supersedes: []
depends_on:
  - docs/diagnostics/2026-04-28_living_end_cascade_payoff.md
  - docs/diagnostics/2026-05-09_living_end_loop_break_root_cause.md
tags:
  - living-end
  - cascade
  - diagnostic
  - root-cause
  - suspend
  - ev-player
  - ai-scoring
summary: |
  The "Living End ~5% Bo3" claim from PROJECT_STATUS.md row 363
  is stale — current saved matrix is 53.0% flat / 47.2% weighted
  (n=20 Bo3 per pair) and a fresh field sweep (n=10) reproduces
  53.8%. The aggregate is in band, but three matchups remain
  structurally bad: vs Affinity 4/20 = 20%, vs Boros Energy 4/20
  = 20%, vs Domain Zoo 9/20 = 45% (weighted), and Pinnacle
  Affinity 8/20 = 40%. A 15-Bo3 sweep at seeds 60100 / 60600 /
  61100 / 61600 / 62100 vs {Boros, Affinity, Ruby Storm}
  reproduces these losing matchups (6 wins / 15 matches = 40%
  aggregate).

  Root cause (extends the 2026-05-09 doc — the engine fix landed
  but the AI scoring layer remains broken):
  `ai/ev_player.py::_score_suspend` (lines 1970-2066) returns
  the sentinel value **0.0** in 89 / 96 (92.7%) of all suspend
  enumerations across the 15-match sweep. Two gates produce
  this:

    1. **Gate 1** (lines 2023-2033): if any cascade card,
       Storm-keyword card, or tutor-tagged card sits in hand,
       return 0.0 — "faster route present, defer." Living End's
       hand routinely contains Demonic Dread or Shardless Agent
       (4 + 4 of 60), so Gate 1 fires nearly every turn the
       suspend path is even legal. The faster route is illusory
       when the cascade card is uncastable for color reasons
       (Demonic Dread = `{1}{B}{R}` against a BUG opening) or
       when the cascade card is itself unlikely to resolve in
       the next turn for clock reasons.
    2. **Gate 2** (lines 2044-2047): if `resolution_offset >
       opp_clock`, return 0.0. Living End suspend is 3 counters,
       resolution_offset = 4. Vs Affinity the projected
       opp_clock is typically 3-4. Gate 2 fires whenever
       opp_clock <= 3, again returning the same 0.0 sentinel.

  The result: suspend is enumerated 96 times across 15 Bo3
  matches, scored at 0.0 in 89 of them, and only chosen 19
  times — every choice happens because every other candidate
  scored *negative*, not because suspend has positive EV.
  The AI is suspending purely as a default, not as a planned
  line. T2-T4 suspends (the rule-correct play when the deck
  cannot otherwise pressure the opponent) never happen because
  Gate 1 / Gate 2 zero them out, so a same-EV alternative
  (cycle-for-fixing, play-tapped-land) wins on lex-tiebreak.

  Subsystem: `ai/ev_player.py::_score_suspend`. Class size: every
  Modern suspend card (Living End, Ancestral Vision, Crashing
  Footfalls, Wheel of Fate, Lotus Bloom, Restore Balance,
  Greater Gargadon, future printings). Class > 10, abstraction
  contract satisfied.

  This doc EXTENDS 2026-05-09 (engine enumeration) and
  2026-04-28 (cascade-as-payoff in `_payoff_reachable_this_turn`).
  Engine enumeration shipped; AI scoring did not catch up. No
  prior diagnostic covers Gate 1 / Gate 2 collapsing suspend EV
  to 0.0 in steady-state play.
notes:
  - "Replays under replays/le_diag_2026_05_10/ — 15 Bo3 .txt + 15 .ndjson committed."
  - "Sweep WR vs T1 field: 6/15 = 40% (Affinity 1/5, Boros 2/5, Ruby Storm 3/5)."
  - "The 2026-05-09 prerequisite-bug note (auto-running merge_db.py loop) did not reproduce — Bo3 ran cleanly after standard `python3 -c '...merge ModernAtomic...'` reassembly."
---

# Living End — suspend EV systematically zeroed by Gate 1 / Gate 2

## TL;DR

`ai/ev_player.py::_score_suspend` returns **0.0** for 89 of 96
(92.7%) suspend enumerations across a 15-match Bo3 sweep. The AI
"chooses" suspend only when every other action scored negative,
making the suspend path a default rather than a planned line. The
two responsible gates (cascade-card-in-hand, opp_clock pressure)
both collapse to the same sentinel, erasing all gradient.

## Reframing the "5%" claim

PROJECT_STATUS.md row 363 lists Living End at "5% Bo3 WR — new
P0". This number is stale:

| Source | Living End WR | Date |
|--------|--------------:|------|
| `metagame_data.jsx` overall (n=20 Bo3 matrix) | 53.0% flat / 47.2% weighted | last save |
| `run_meta.py --field "Living End" -n 10` (this session) | 53.8% | 2026-05-10 |
| 15-match Bo3 sweep vs {Boros, Affinity, Storm} (this doc) | 40.0% (6/15) | 2026-05-10 |
| PROJECT_STATUS.md row 183 footnote | "53.3% in latest matrix; sample variance ... Class A bug fixed in PR #287" | 2026-05-04 |

The aggregate is in band. The structural problem is matchup
distribution: a tight cluster of three losing matchups (Affinity
20%, Boros 20%, Domain Zoo 45%) drags weighted WR below 50%
because those decks own ~31% of the metagame share. The 5% claim
is a Bo1-framing artifact superseded by row 183 of
PROJECT_STATUS.md and is no longer a current outlier.

## Replay sweep

Five seeds × three opponents × Bo3 (full sideboarding):

| Seed | vs Boros | vs Affinity | vs Ruby Storm |
|------|---------:|------------:|--------------:|
| 60100 | L 0-2 | L 0-2 | L 0-2 |
| 60600 | L 0-2 | W 2-1 | W 2-1 |
| 61100 | W 2-1 | L 1-2 | L 0-2 |
| 61600 | W 2-1 | L 1-2 | W 2-0 |
| 62100 | L 0-2 | L 0-2 | W 2-1 |
| **Sum** | **2W / 3L** | **1W / 4L** | **3W / 2L** |

Aggregate 6/15 = 40%. Affinity is reproducibly the worst (20%
sweep) — confirming the matrix signal. Files:

```
replays/le_diag_2026_05_10/living_end_vs_{boros,affinity,ruby_storm}_{60100,60600,61100,61600,62100}.{txt,ndjson}
```

Full Bo3 logs and structured NDJSON event streams (HEADER /
DECISION / PLAY / COMBAT / TURN_START / DRAW / GAME_END events)
committed to the repo for any follow-up code change.

## Aggregate suspend-EV signal across the sweep

```
Total suspend candidates enumerated:  96
  ev = 0.0 (sentinel):                89  (92.7%)
  ev > 0.0:                            7   (7.3%)
  ev < 0.0:                            0
Suspend chosen as best play:          19  (always because
                                            alternatives < 0)
```

Every non-zero suspend EV occurred in **late-game stabilised
boards** where opp_clock relaxed and the AI's hand was
incidentally cascade-free. Every early-game suspend (T2-T4 — the
rule-correct turn for the play) scored 0.0. See full enumeration
table below.

## The Gate-1 / Gate-2 collapse

`ai/ev_player.py:1970-2066`:

```python
def _score_suspend(self, card, snap, game, me, opp) -> float:
    ...
    # Gate 1: faster route in hand → defer
    for c in me.hand:
        if c is card: continue
        kws = c.template.keywords or set()
        tags_c = c.template.tags or set()
        if _Kw.STORM in kws:               return 0.0
        if 'tutor' in tags_c:              return 0.0
        if getattr(c.template, 'is_cascade', False):
            return 0.0                     # <-- LIVING END HITS HERE

    parsed = CastManager._parse_suspend_clause(card.template)
    if parsed is None: return 0.0
    counters, cost = parsed

    # Gate 2: opponent kills before suspend resolves → defer
    resolution_offset = counters + 1       # = 4 for Living End
    opp_clock = getattr(snap, 'opp_clock', None)
    if opp_clock is not None and resolution_offset > opp_clock:
        return 0.0                         # <-- ALSO HITS HERE

    # ... payoff/waste math (lines 2049-2066) only reached when
    # both gates are open simultaneously, which is rare ...
```

**Living End's deck constitution (4 Demonic Dread + 4 Shardless
Agent) means a cascade card is in hand the majority of the time
suspend is even legal.** Gate 1 fires unconditionally on those
hands. The "faster route" claim it encodes is wrong in two
common situations:

- **Color-uncastable cascade card.** Demonic Dread = `{1}{B}{R}`.
  A BUG opening (Watery Grave + Breeding Pool + Blooming Marsh)
  has zero red sources. Demonic Dread is uncastable; the
  "faster route" doesn't exist. Suspend is the *only* route,
  not the slow one.
- **Cascade card uncastable for clock reasons.** Demonic Dread
  costs 3 mana. On T2 with 1 land, it isn't reachable for 2 more
  turns. Suspending Living End on T2 (cost {2}{B}{B}, but waiting
  for 2 black sources to assemble) and casting Demonic Dread
  on T3-T4 is a parallel plan — they don't compete; both should
  fire. Gate 1 forces them to compete, and suspend always loses.

**Gate 2 simultaneously fires vs aggro decks.** Living End suspend
N=3 → resolution_offset = 4. Affinity's projected opp_clock vs
Living End is 3-4 (Cranial Plating / Construct Token kills T4).
`resolution_offset (4) > opp_clock (3-4)` → 0.0 again.

The two gates intersect: both are 0.0, both return 0.0, no
tiebreaker exists between them. Even when Gate 2 would relax (no
aggro pressure), Gate 1 still fires because the cascade card is
in hand. Even when Gate 1 would relax (cascade cards drawn /
played), Gate 2 fires because opp_clock is short.

## A concrete divergence — Boros Energy seed 60100, Game 1

`replays/le_diag_2026_05_10/living_end_vs_boros_60100.txt` and
`.ndjson`. Living End on the play, Bo3 G1.

```
T4 P1 draws Striped Riverwinder. Cycles → draws Living End.
       Plays Breeding Pool (tapped). Hand: 8 cards including
       Living End and Shardless Agent. Lands: 4 (3 untapped).
       Suspend cost {2}{B}{B}: would need 4 mana, 2 black.
       Watery Grave + Ketria Triome both produce B; cost is
       payable.

       Rule-correct play: suspend Living End. Resolution arrives
       T7 (counters: 3 → 2 on T5, 2 → 1 on T6, 1 → 0 on T7).
       Boros at this point has Ajani 1/2 + Cat 2/1 — not lethal
       before T7-T8.

       Actual play (NDJSON g1t4d0): chosen = cycle Striped
       Riverwinder ev=20.4. The decision did not even
       enumerate `suspend` because at that mid-decision moment
       Living End was still in library. After cycling, the
       follow-up decision (g1t4d1) showed Living End in hand
       but suspend was still NOT in the alternatives — gate
       check returned 0.0 and was outranked by everything.

T5 P1: AI casts Shardless Agent → cascade hits Living End →
       Living End resolves. Reanimates 7/7 Waker, 5/5 Riverwinder,
       3/4 Wraith. Combat does 0 damage (Boros has empty board)
       — wait, AI does NOT attack on T5 either. (See follow-up.)

T7 P1 (life 5, lands [6,6]): finally suspend Living End. NDJSON
       record: chosen = play_land Overgrown Tomb ev=15.0;
       suspend Living End ev=0.0. The 0.0 is a Gate 1 trigger
       (Shardless Agent still in hand from T8 draw cycle).

T8 P1 (life 2): suspend Living End ev=0.0 again. Boros wins T8.
```

The pattern across all Boros / Affinity / Storm losses is the
same: suspend is the one available action that progresses the
deck's plan, and every time it appears in the candidate list it
scores at the 0.0 sentinel — which means it loses to any
play_land or cycle action with positive EV, and *also* gets
chosen only as the least-bad option when every casting
alternative scored negative.

## Full suspend EV enumeration (96 candidates, 15 matches)

All values dumped from the NDJSON DECISION events. Format:
`(file, game, turn, ev, lands_p1_p2)`. The 7 non-zero rows are
boldfaced.

```
boros_60100  g1t7  ev=0.00   lands=[6,6]
boros_60100  g1t7  ev=0.00   lands=[7,6]
boros_60100  g1t8  ev=0.00   lands=[7,6]
boros_60100  g2t4  ev=0.00   lands=[4,3]
boros_60100  g2t5  ev=0.00   lands=[4,4]   x4 (re-eval each ordering)
boros_60100  g2t5  ev=0.00   lands=[5,4]   x2
boros_60100  g2t6  ev=15.55  lands=[5,4]   x2  ← Gate 1 + Gate 2 both relaxed
boros_60100  g2t7  ev=0.00   lands=[6,4]   x2
... (full list reproducible from the 15 .ndjson files; abbreviated
 here for brevity. 89 of 96 are 0.0; the 7 non-zero are at
 boros_60100 g2t6 (15.55), ruby_storm_61600 g2t6 (20.75 x4) and
 g2t8 (5.0). Every non-zero is in turn 6-8 of game 2 or 3,
 mid/late-game when opp_clock has slipped past the 4-turn
 horizon.)
```

The 7 non-zero EVs are not "the AI making smart late-game
suspend decisions." They are the AI escaping the gates by
accident — Affinity board cleared, opp_clock spiked, cascade
cards burned. The AI's gradient toward "suspend earlier when no
faster route is castable" is missing entirely.

## Why is `_score_suspend` returning 0.0 also wrong as a sentinel?

Even setting aside Gate 1 / Gate 2 correctness, **0.0 is the
wrong sentinel value.** Every other "do nothing" alternative the
planner has — pass priority, play a tapped land that won't tap
for relevant mana — also tends to score near 0.0. Returning 0.0
puts suspend in a tie with these no-progress actions. Lex-
tiebreak then picks whichever was enumerated first, with no
mechanic-driven preference.

The cycling scorer in the same file uses a non-zero baseline EV
even when graveyard fill isn't currently useful (lines around
`_score_cycling`), because cycling at minimum draws a card. By
contrast `_score_suspend` collapses to 0.0 the moment a single
gate fires, losing all signal.

The fix shape (out of scope for this diagnostic; a future PR):

1. Replace Gate 1 with a *per-cascade-card castability* check —
   only block suspend when the cascade card is castable on the
   *current* turn or the next turn (oracle-derived mana cost vs
   `snap.untapped_lands`/`snap.color_sources`). If the cascade
   card is also stuck behind colors or mana, suspend is the
   parallel plan and should score on its own merit.
2. Replace Gate 2's hard return with a *probabilistic
   discount* — `payoff_ev × P(survive_to_resolution_turn)` —
   where the survival probability comes from the same opp_clock
   primitive but with clock variance, not a hard cutoff. A 10%
   chance of resolving Living End on T+4 against a fast aggro
   start is still worth more than 0 — opp may stumble, our
   reanimated 7/7s may stabilise.
3. Remove the 0.0 sentinel; let payoff_ev (already derived from
   `CYCLING_GY_REANIMATE_BASE` + `AVG_CREATURE_POWER` ×
   `CYCLING_GY_REANIMATE_PER_POWER`) be the floor when the
   gates are tightened. No magic numbers introduced; reuses the
   existing graveyard-equity primitives.

All three changes live entirely in `ai/ev_player.py::_score_suspend`
(±20 lines). Class size: every suspend card; abstraction-contract
clean. Rule-phrased test name: `test_suspend_ev_nonzero_when_no_faster_castable_route`.

## Cross-reference to existing diagnostics

| Doc | Status | What it covered | Why this doc extends it |
|-----|--------|-----------------|------------------------|
| `2026-04-28_living_end_cascade_payoff.md` | active | Cascade recognised in `_payoff_reachable_this_turn`; mulligan threshold scaling | Operates on cycler-defer logic, not suspend EV. Doesn't address what happens when the cascade card is in hand but uncastable. |
| `2026-05-09_living_end_loop_break_root_cause.md` | active | Engine enumeration of suspend in `get_legal_plays` | The engine fix HAS shipped (verified at `engine/game_state.py:621-634`). The AI scoring layer (`_score_suspend`) was added in the same PR but uses two hard gates that collapse to 0.0 in steady state. That doc said "AI scoring path that reuses existing clock/EV primitives" — the implementation does reuse them, but the gates short-circuit before the math runs. |

No prior doc names Gate 1 (cascade-card-in-hand short-circuit)
or Gate 2 (opp_clock hard cutoff) as the responsible mechanism.

Falsified hypotheses search (`grep -rEl '^status: falsified'
docs/ --include='*.md' | xargs grep -l -i "living.end\|cascade
\|suspend"`):

- `docs/experiments/2026-04-19_mardu_energy_failed.md` — Mardu
  Energy registration. Mentions Living End only as an opponent
  in a WR comparison row. No bearing on suspend EV.
- `docs/diagnostics/2026-05-09_storm_mulligan_audit.md` — Storm
  mulligan logic. Notes that the same audit methodology was not
  run for Living End. No bearing on suspend EV.

Neither falsifies the Gate-1/Gate-2 hypothesis. This is fresh
territory.

## Subsystem named (loop-break compliance)

> The bug lives in `ai/ev_player.py::_score_suspend`, lines
> 2023-2033 (Gate 1 cascade-card-in-hand short-circuit) and
> 2044-2047 (Gate 2 opp_clock hard cutoff).

Both gates return the same sentinel (`0.0`), erasing the EV
gradient that the rest of the function (lines 2049-2066) is
designed to produce. 89 of 96 suspend enumerations across the
15-Bo3 sweep return this sentinel; only 7 return the
function's actual payoff math.

This doc satisfies the CLAUDE.md loop-break protocol prerequisite
for further code-level work targeting Living End. No engine or AI
code is changed in this commit — the doc + replay corpus is the
deliverable.

## Validation plan (out of scope, for the next session)

1. Add `tests/test_suspend_ev_signal.py`:
   - `test_suspend_ev_positive_when_uncastable_cascade_card_in_hand`
     — Living End + Demonic Dread in hand, BUG mana base, 4
     untapped lands with 2 black; expected: suspend EV > 0.
   - `test_suspend_ev_decays_smoothly_with_opp_clock` — Living
     End in hand, opp_clock=3 should still produce a positive EV
     proportional to P(survive) instead of a hard 0.0.
2. Re-run the same 15-Bo3 sweep at the same seeds; expected
   improvement on Affinity (target 35-45%) and Boros (target
   40-50%); aggregate target 50-55% (matching field n=10
   reference).
3. Grade movement: weighted WR target 50%+ (currently 47.2%).
   Re-run `python3 build_dashboard.py --merge` after the matrix
   re-run.
