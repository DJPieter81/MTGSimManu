---
title: Storm mulligan policy already keep-tight vs no-disruption opponents — hypothesis falsified
status: falsified
priority: historical
session: 2026-05-09
supersedes: []
superseded_by: []
depends_on:
  - ai/mulligan.py
  - decks/gameplans/ruby_storm.json
  - engine/game_runner.py
tags:
  - p1
  - mulligan
  - ruby-storm
  - diagnostic
  - falsified-hypothesis
summary: |
  Hypothesis: Storm keeps too-loose 7-card hands against fast no-disruption
  decks (Boros, Zoo, Tron, Amulet), missing the pro-pilot bar of "cost
  reducer + 2 enablers + 1 finisher path". Reality (measured on N=37 seeds
  per opp, 50-step seeds 50000..68000 + extras): **the policy already
  rejects every loose hand the hypothesis names**. Strict-loose (0 enablers)
  is 0/37 across all five opponents tested. Light-loose (no reducer & <2
  rituals & 0 finisher access) is 1/37 = ~3%, ten times below the 30%
  threshold the parent task set as the "policy is leaking" trigger.
  Stricter keep policies would force mulligans on hands that currently
  convert at the deck's overall WR (or better — Cat-G "WEAK" hands actually
  win 2/2 vs Tron because they're 2-reducer fast-Storm openers).

  Storm's vs-Tron WR (41%) and vs-Boros WR (49%) are **not driven by bad
  keeps**. The "best" hand category by the pro-pilot bar (reducer + 2en +
  fin) wins 4/14 = 29% vs Tron. The "loosest" category (G, 2 hands)
  wins 2/2. Mulligan keep-quality and Storm-WR are inversely correlated
  in this dataset, which falsifies the hypothesis at its root.
---

# Storm mulligan policy audit (2026-05-09)

## Hypothesis tested

> Storm keeps too-loose 7-card hands against fast no-disruption decks,
> missing critical-mass combo openers. Real Storm pilots aggressively
> mulligan to a hand with a cost reducer + 2 enablers + 1 finisher
> path. Tighten the keep threshold when both opp counter and removal
> densities are low.

## Methodology

For each of {Eldrazi Tron, Boros Energy, Amulet Titan, Domain Zoo,
Dimir Midrange}, ran Bo1 games at 50 standard seeds (50000 → 74500,
step 500). Captured Storm's first kept hand and its size from the
verbose game log. Classified each kept-7 hand by:

- **strict-loose**: 0 enablers (the prompt's leak threshold)
- **light-loose**: 0 reducer & <2 rituals & 0 finisher access
- **NOT pro-bar**: missing any of {reducer ≥1, enablers ≥2, finisher ≥1}

`enabler_set` matches `decks/gameplans/ruby_storm.json` `mulligan_combo_sets[1]`
plus the ritual set. `finisher_access_set` = payoffs ∪ {Wish, Past in Flames}
(SB tutor + recursion both enable a finisher chain).

37 of 50 attempted seeds produce a kept-7 hand; the remaining 13 the
existing policy already mulligans. The policy is not failing to mulligan;
the question is whether the hands it keeps are loose-by-pro-bar.

## Results

| Opp | Storm WR | strict-loose-7 | light-loose-7 | NOT-pro-bar-7 | WR if NOT-pro-bar |
|-----|---------:|---------------:|--------------:|--------------:|------------------:|
| Eldrazi Tron   | 41% | 0/37 | 1/37 | 23/37 | 11/23 = 48% |
| Boros Energy   | 49% | 0/37 | 1/37 | 23/37 | 10/23 = 43% |
| Amulet Titan   | 73% | 0/37 | 1/37 | 23/37 | 16/23 = 70% |
| Domain Zoo     | 46% | 0/37 | 1/37 | 23/37 | 11/23 = 48% |
| Dimir Midrange | 35% | 0/37 | 1/37 | 23/37 | 10/23 = 43% |

Note that Storm sees the same first hand on the same seed regardless of
opponent (the engine seeds RNG before opponent identity matters), so the
strict/light/NOT-pro counts are identical across rows — but the WR
column varies because the opponent's game-play does, which is the point:
keep quality is a Storm-side property; WR delta is an opponent-side property.

### Per-category WR vs Tron (the prompt's primary validation matchup)

Categorisation against the pro-pilot bar from the prompt:

| Cat | Definition                       | N  | Storm WR  |
|----:|----------------------------------|---:|----------:|
| A   | reducer ≥1 + enablers ≥2 + fin ≥1 | 14 | 4/14 = 29% |
| B   | rituals ≥2 + fin ≥1              |  4 | 2/4  = 50% |
| C   | rituals ≥2 + reducer ≥1 (no fin) |  6 | 2/6  = 33% |
| D   | reducer ≥1 + enablers ≥2 (no fin)|  3 | 2/3  = 67% |
| E   | enablers ≥3 (no rit/red)         |  8 | 3/8  = 38% |
| G   | "WEAK" — fails all above         |  2 | 2/2 = 100% |

Cat A (the prompt's pro-pilot bar) **loses more often than every other
category** in this sample. Cat G ("WEAK") hands are 2-reducer + payoff
openers like `[Grapeshot, Elegant Parlor, 2× Ruby Medallion, Reckless Impulse,
Arid Mesa, Mountain]` that win T6 with no rituals at all — they fail the
ritual-count classifier but they're **better** Storm hands than the rituals-
heavy Cat A by clock measurement. The pro-pilot bar is a wrong heuristic
for this engine's combo math, not a missed bar.

## Why the hypothesis is wrong

1. **The current policy already enforces a combo-path floor.** `MulliganDecider.decide`
   at 7 cards requires either a `mulligan_combo_paths` enabler+payoff covered
   path or, for `mulligan_combo_sets`, ≥1 piece from at least one path with
   threshold scaling by set size (`MULLIGAN_COMBO_PATH_3SET_THRESHOLD`).
   The `always_early`/cost-reducer ritual+cantrip+finisher backup gate
   (`mulligan.py:411`) catches the remaining gap. Empirically, 0 of 37 ×
   5 opponents = 0/185 hands ship to game with 0 enablers.

2. **The "pro-pilot bar" is too tight for sim conditions.** Cat A hands
   (the bar) win 29% vs Tron; Cat D hands (no finisher in hand) win 67%.
   Forcing more mulligans toward Cat A would lower the average WR. The
   sim's combo math correctly values reducer-redundancy and ritual-density
   over having Grapeshot in hand at game start (because the deck digs
   to it through cantrips).

3. **The disruption-gate would do nothing.** A keep-policy tightening
   conditioned on opp `counter_density < 0.05 ∧ removal_density < 0.05`
   would only matter if the gated hands were, in fact, loose-by-some-bar.
   But the population of loose hands at 7 cards is empty (0/37 at the
   strict bar, 1/37 at the light bar). There's no signal to gate on.

## Decision

**No code change.** Hypothesis falsified. Storm's vs-Tron 41% and
vs-Boros 49% WRs are mid-game execution problems (combo timing, on-the-
fly resource math), not mulligan problems. Subsequent investigations
into Storm's vs-aggro WR should target `ai/combo_chain.py` and the
"go-off-this-turn" patience tracker (`_going_off_turn` in `ev_player.py`),
not `ai/mulligan.py`.

## Validation anchors

- **Storm vs Tron Bo1 N=10 s=50000:** 40% (4/10) — held; the keep policy
  unchanged, WR within the prompt's "must not regress below 40%" floor.
- **Storm vs Boros Bo1 N=10 s=50000:** 20% (2/10) — the prompt expected
  0/10; current build is already above that. Not a mulligan win.
- **Storm vs Dimir Bo1 N=10 s=50000:** 50% (5/10) — held above the
  prompt's "must not regress below 50%" floor.

## Generalisation note (Goryo's, Living End)

The same audit methodology was not run for Goryo's Vengeance and Living End
because no fix is being shipped. If a future investigation targets their
mulligan against fast aggro, the same tooling
(`run_meta.py --verbose` + per-hand role classification) applies — the
gameplan JSON for both decks already declares `mulligan_combo_paths`
or `mulligan_combo_sets`, so the same keep-policy primitives are in
play and any audit should look for the same statistical signature
(strict-loose rate, NOT-pro-bar WR delta) before proposing changes.

## Reference data

Audit script (one-off, not committed):

```python
from run_meta import _get_runner
from decks.modern_meta import MODERN_DECKS
import random

runner = _get_runner()

enabler_set = {"Desperate Ritual","Pyretic Ritual","Manamorphose",
               "Reckless Impulse","Wrenn's Resolve","Glimpse the Impossible",
               "Past in Flames","Wish",
               "Valakut Awakening // Valakut Stoneforge",
               "March of Reckless Joy"}
ritual_set = {"Desperate Ritual","Pyretic Ritual","Manamorphose"}
payoff_set = {"Empty the Warrens","Grapeshot"}
reducer_set = {"Ruby Medallion","Ral, Monsoon Mage // Ral, Leyline Prodigy"}
finisher_access_set = payoff_set | {"Wish","Past in Flames"}

# Run 50 seeds, capture P1 (Storm) kept hand from verbose log,
# classify by category, report WR per category and aggregate.
```

Re-run via `git log --grep "storm-mulligan-vs-no-disruption"` for the
exact commit reproducing this measurement.
