---
title: Three measurements — Ruby Storm band, control sideboard, activation tranche 2
status: active
priority: secondary
session: 2026-08-25
supersedes: []
superseded_by: []
depends_on: []
tags: [measurement, bo3, sideboard, activation, calibration]
summary: >
  Three questions measured rather than assumed. (1) Ruby Storm's apparent
  above-band 58.2% was a Bo1 artifact — canonical Bo3 puts it at 42.4%, inside
  its [40-55] band, for exactly the reason the Bo1/Bo3 directive describes.
  (2) Control's sideboard against aggro is deterministic: the identical swap in
  7/7 games. (3) Activation tranche 2 as planned (add cost payers) would unlock
  3 abilities across all registered decks; the real bottleneck is cost-PARSING
  coverage, not payers. Recommendation: do not build tranche 2 as scoped.
---

# Three measurements (2026-08-25)

No code changed. All three items were measured to decide whether work was
warranted; two came back "the concern was wrong" and one came back "do not
build this yet". Recording them so the conclusions are not re-derived.

## 1. Ruby Storm is IN band — the 58.2% was a Bo1 artifact

An earlier pass in this session flagged Ruby Storm at **58.2%**, above its
[40-55] band, as a possible regression from the mana-estimate change. That
number was **Bo1**.

Canonical Bo3, n=8 per matchup:

| Opponent | Bo3 WR |
|---|---|
| Dimir Midrange | 12% |
| Boros Energy | 62% |
| Domain Zoo | 25% |
| 4/5c Control | 88% |
| Affinity | 25% |
| **mean** | **42.4%** |

**42.4% is inside [40-55].** The flagged risk does not exist.

This is the textbook case the Bo3 directive in CLAUDE.md describes: Bo1
systematically over-rewards decks whose worst matchups are answered by
SIDEBOARD hate the opponent lacks in the mainboard, and Storm is named
explicitly there as a hate-sensitive archetype. The 12%-to-88% spread is also
the variance profile expected of a combo deck, which is a further reason not to
read any single Bo1 matchup as a calibration signal.

**Do not re-open this as a regression.** If Storm is re-measured, use Bo3.

## 2. Control's sideboard against aggro is deterministic

Observed across Bo3 games (n=6 matches each):

| Matchup | Frequency | Swap |
|---|---|---|
| 4/5c Control vs Domain Zoo | **7 of 7** | `+2 Mystical Dispute, -2 Orim's Chant` |
| Azorius Control vs Domain Zoo | 6 of 7 | same |
| | 1 of 7 | `+1 Wrath of the Skies, -1 Orim's Chant` |

The same swap is made in essentially every game regardless of board state or
which games were lost — the sideboard logic is not adapting.

Verified context, **not** a causal claim: Domain Zoo runs **4 cards with a blue
pip in 60**. Mystical Dispute counters any spell but is discounted only against
blue, so against this deck it boards in as an expensive soft counter rather
than efficient interaction. Cutting Orim's Chant is correct — it was separately
observed being cast uselessly at 5 life.

**What is NOT established:** whether this swap explains control losing deciders
after winning game one. That needs a controlled test (e.g. suppress the swap
and re-measure in Bo3). An earlier hypothesis that Dispute was a *dead* card
was checked and is **overstated** — it is weak, not dead.

## 3. Activation tranche 2: measured footprint is 3 abilities — do not build

The plan for tranche 2 was to add cost PAYERS (sacrifice, pay-life, counters),
on the reasoning that `unpayable` was designed to make later tranches a payer
addition rather than a re-parse. Measured against the 25 registered decks
(285 distinct cards):

| Blocked-ability cost combo | Count |
|---|---|
| `unrecognised` | 35 |
| `pay_life` + `unrecognised` | 10 |
| `pay_life` alone | 1 |
| `put_counter` alone | 1 |
| `remove_counter` alone | 1 |
| **total blocked** | **48** |
| **unlockable by adding payers alone** | **3** |

45 of 48 are blocked by `unrecognised` — the cost PARSER does not understand
the cost, so no payer can help. The two abilities a payer would unlock are
Walking Ballista (`remove_counter`) and Griselbrand (`pay_life`); Walking
Ballista is separately documented as dying to its own ETB handler in real
games.

**Recommendation: do not build tranche 2 as scoped.** If the activation
subsystem is taken further, the next step is cost-parser coverage, not payers.
That is a different piece of work than the plan assumed.

### Method note

A first probe of the `unrecognised` fragments sampled the whole card pool and
suggested the blockers were ability-word prefixes (`Boast — {1}{W}`,
`Cohort — {T}`). A second probe printed the first colon-bearing line per card
rather than the specific flagged ability, giving a different and also
unreliable picture. Both were discarded. The table above is derived from the
parsed `ActivatedAbility.cost.unpayable` values directly, which is the only
reading that reflects what the parser actually produced.
