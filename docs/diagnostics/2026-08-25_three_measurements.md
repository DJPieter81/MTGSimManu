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

---

## CORRECTION (same day) — item 3 was measured against a bug; the conclusion is REVERSED

The recommendation above ("do not build tranche 2 as scoped; only 3 of 48 are
payer-unlockable; the real bottleneck is cost-parser coverage") was derived
from corrupted parser output and is **withdrawn**.

Six of the eleven `_UNPAYABLE_COST_PATTERNS` contained a literal BACKSPACE
(`0x08`) where a regex word-boundary was intended — written through a non-raw
Python string literal, which converts `\b` into `\x08`. Fixed in `7246c14`.

Why it survived three separate investigations in this session:

* **Invisible to inspection.** A backspace does not render, so reading the
  source showed `r'sacrifice this'` — exactly what it was meant to say. That
  table was read twice without noticing.
* **Invisible to behaviour.** An unmatched cost falls through to
  `unrecognised`, which lands in `unpayable`, which `can_activate` refuses —
  the same outcome as a genuinely unsupported cost. A broken pattern degraded
  silently into "not supported yet" and no test failed.
* **The two unreliable probes noted above were chasing this.** They were
  looking for *what kind of cost* the parser could not handle; the answer was
  that it could handle them and the regex was damaged.

### Corrected numbers

| Measure | Reported above | Actual |
|---|---|---|
| Registered decks: blocked abilities unlockable by PAYERS | 3 of 48 | **41 of 48** |
| DB-wide abilities marked `unrecognised` | 2017 | **462** |
| DB-wide `sacrifice_self` correctly named | ~0 | **664** |
| DB-wide `sacrifice_another` | ~0 | 438 |

### Corrected recommendation

**Activation tranche 2 (cost payers) is worth building.** It would unlock 41 of
the 48 blocked abilities across the registered decks, not 3. The `unpayable`
field's design intent — that a later tranche is a payer *addition* rather than
a re-parse — holds; it simply could not be seen through the escape defect.

The remaining `unrecognised` (462 DB-wide, 1 in registered decks) is now a
genuine coverage tail rather than the dominant blocker.

### What guards this now

`tests/test_activation_cost_patterns_are_valid.py` asserts no pattern contains
a control character, and that each pattern classifies a representative cost
phrase rather than dropping it to `unrecognised`. The first rule is the one
that would have caught this.

**Lesson worth keeping:** a silent fallback (`unrecognised`) that is
behaviourally identical to a legitimate state will hide a bug indefinitely.
Where a fallback exists, something must assert that the non-fallback path is
actually reachable.
