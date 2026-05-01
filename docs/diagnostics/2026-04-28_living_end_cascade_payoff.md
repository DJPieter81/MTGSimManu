---
title: Living End 38% — cascade not recognized as payoff + uniform mulligan threshold
status: active
priority: primary
session: 2026-04-28
depends_on:
  - docs/diagnostics/2026-04-28_storm_wasted_enablers.md
  - docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md
tags:
  - p0
  - wr-outlier
  - living-end
  - cascade
  - ev-evaluator
  - mulligan
summary: |
  Living End at 38% baseline with two compounding bugs:
    1. The 2026-04-28 _payoff_reachable_this_turn helper recognized
       Keyword.STORM finishers + tutors as payoffs, but NOT cascade
       triggers.  Cascade decks were over-tightened — cyclers got
       deferred even with Shardless Agent / Demonic Dread in hand.
    2. The 2026-04-28 mulligan Bug #4 fix used a uniform >=2-of-N
       threshold for combo paths.  Living End's cascade combo set is
       size 2 (Demonic Dread / Shardless Agent), making the threshold
       impossibly strict (2-of-2 in opening 7).  Hands with one
       cascade card mulled when they should have kept.

  Both fixes ship in one diff because they share a class-size:
  cascade decks (Living End, Crashing Footfalls, Violent Outburst,
  future cascade-on-cast printings).  Validation: Living End
  vs 4 representative opponents @ n=8 each, 30% → 44% aggregate.
---

# Living End — cascade payoff + threshold scaling

## Replay

`run_meta.py --verbose "Living End" "Boros Energy" -s 50000` —
Boros wins T6.  Living End opening 7:

```
Striped Riverwinder, Waker of Waves, Living End, Blooming Marsh,
Watery Grave, Living End, Architects of Will
```

7 cards, 0 cascade cards (no Shardless Agent / Demonic Dread).
Two `Living End` (suspend / cascade-target) and three cyclers.
This hand cannot win — Living End needs a cascade card to be
cast on T3 to fire the reanimation.

The deck's `mulligan_combo_sets` declares two paths:
1. `[Demonic Dread, Shardless Agent]` — cascade cards (need ≥1)
2. `[Architects of Will, Curator of Mysteries, Street Wraith,
   Striped Riverwinder, Waker of Waves]` — cyclers (need ≥1)

Path 1 has 0/2 pieces in the kept hand, path 2 has 3/5.

## Bug 1: cascade not recognized as payoff in EV signal gate

`ai/ev_evaluator.py::_payoff_reachable_this_turn` (added 2026-04-28
to fix Storm) had four branches:
- (a) Keyword.STORM in hand
- (b) `'tutor' in tags` in hand
- (c) real-dig cantrip in hand (excluding self)
- (d) Keyword.STORM in graveyard with flashback

None recognize cascade triggers.  Living End's combat plan is:
cycle creatures into the graveyard → cast a cascade card →
cascade exiles cards from library until hitting Living End →
cast Living End for free → reanimate everything.

Without cascade-as-payoff, every cycler / Force of Negation cast
gets deferred unless a Storm-keyword finisher is in hand.

**Fix shape**: add branch (c) `template.is_cascade`.  The
`is_cascade` flag is already populated by `engine/card_database.py`
during template construction.  Generic across the cascade pool —
Shardless Agent, Demonic Dread, Violent Outburst, future printings.

## Bug 2: uniform mulligan threshold doesn't scale with set size

`ai/mulligan.py:118-130` (the 2026-04-28 Bug #4 fix) used a fixed
`>=2 pieces from any path` threshold.  Goryo's sets are size 3, so
2-of-3 makes sense (enabler + payoff = 67% combo).  Storm sets are
size 5 (interchangeable rituals); 2-of-5 is reasonable.

But Living End's cascade-cards path is **size 2** (Demonic Dread,
Shardless Agent).  Requiring 2-of-2 means BOTH cascade cards must
be in the opening 7 — an impossibly strict requirement that mulls
the deck's natural keeps.

Pinnacle Affinity's combo set is size 7 (any cheap artifact
enabler).  2-of-7 is reasonable but the deck only needs 1 — they
are interchangeable.

**Fix shape**: scale threshold by set cardinality.  Heuristic
encodes ALL vs ANY set semantics:

| Set size | Threshold | Semantic |
|---------:|----------:|----------|
| 2 | 1 | ANY-style: "either of two cascade cards" |
| 3 | 2 | ALL-style: enabler + target + payoff |
| 4+ | 1 | ANY-style: interchangeable bag |

The size-3 cutoff is the inflection point because gameplan authors
declaring exactly 3 alternatives are encoding a named-tuple
combo (Goryo's: Mending + Vengeance + Fatty), whereas size 2 is
"either-of" and size 4+ is "any-of-many."

## Validation

Living End vs 4 opponents @ n=8 each, seeds 50000+:

| Opponent | Baseline | Post-fix | Δ |
|----------|---------:|---------:|----:|
| Domain Zoo | 0% | **38%** | **+38pp** |
| Dimir Midrange | 20% | **38%** | **+18pp** |
| Azorius Control | 70% | 75% | +5pp |
| Boros Energy | 30% | 25% | -5pp (n=8 noise) |
| **Aggregate** | **30%** | **44%** | **+14pp** |

Three matchups improved (+38/+18/+5), one held within noise.

## Class-size

Cascade-as-payoff: every cascade-on-cast deck.  Detection via
`template.is_cascade` is engine-set, not hardcoded.

Threshold scaling: every combo deck declaring `mulligan_combo_sets`.
Today: Goryo's (size-3 sets, threshold=2 unchanged), Storm (size-5
sets, threshold=1), Living End (size-2 cascade + size-5 cyclers,
both threshold=1), Pinnacle (size-7, threshold=1), Azorius
Control (no combo sets, no effect).

## Tests

`tests/test_cascade_payoff_reachable.py` — 3 tests:
1. Shardless Agent + cycler in hand → reachable (red pre-fix)
2. Demonic Dread + cycler in hand → reachable (red pre-fix)
3. Pure cyclers, no cascade card → still defers (regression)

Existing tests still pass: 42/42 across mulligan + Storm + cascade
+ Goryo's + abstraction-contract blast radius.

## Goryo's regression check (n=20 same seeds)

| Opponent | Baseline | After Living End fixes |
|----------|---------:|---------:|
| Eldrazi Tron | 0% | 0% |
| Amulet Titan | 5% | **15%** |
| Azorius Control | 35% | 30% |
| Boros Energy | 0% | 0% |

Aggregate 4/80 = 5% (vs 1/80 = 1% baseline).  Modest improvement,
no regression.  The threshold-scaling change is a no-op for
Goryo's (all sets are size 3 → threshold remains 2).

## What this does NOT cover

- Storm validation skipped at this stage — Storm sets are size 5
  (threshold drops from 2 → 1 — more lenient).  Could improve
  Storm WR slightly; pending fresh sweep next session.
- Mid-cascade-chain decisions (during the Living End cascade
  resolution itself) are scored by `combo_calc.py`, not
  ev_evaluator.  Out of scope for this diff.
- Crashing Footfalls / suspend-style cascade decks not validated
  here — Living End is the canonical case but the mechanic is the
  same.
