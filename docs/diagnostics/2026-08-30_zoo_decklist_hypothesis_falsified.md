---
title: Domain Zoo overperformance — the DECKLIST hypothesis is falsified (real list is stronger)
status: falsified
priority: diagnostic
session: 2026-08-30
depends_on:
  - docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md
  - docs/diagnostics/2026-08-26_decider_loss_root_cause.md
supersedes: []
superseded_by: []
tags:
  - domain-zoo
  - decklist-fidelity
  - metagame-share
  - falsified
  - wr-outlier
summary: >
  Tested whether Domain Zoo's 79-85% outlier is a decklist artefact by
  swapping in the real mtgtop8 "4/5c Aggro" list. FALSIFIED — the real list
  scores HIGHER (84.7% vs 79.2%), so our build is not the cause. Two real
  defects were found while testing and are recorded separately: the 6.07%
  meta share is assigned to an archetype absent from the real top-16 (nearest
  real bucket is 4.0%), and Scion of Draco's keyword static is completely
  inert. Neither explains the outlier — the share is a weighting error, and
  the inert keywords would make Zoo STRONGER if fixed. Do not re-run the
  decklist swap.
---

# Domain Zoo: the decklist is not the cause (2026-08-30)

## Why this was worth testing

Two prior diagnoses (2026-08-20, 2026-08-26) both concluded the gap is on
the DEFENDING side and is structural, with the 2026-08-26 verdict naming the
fix lane explicitly as "deck-list/gameplan configuration; no further
engine/AI code at this outlier". Nobody then checked the deck list. It was
the one unexamined lane, and the same check had just caught a real problem
in Izzet Prowess the same day.

## The hypothesis

Our registered Domain Zoo is a pure-beatdown build. The real mtgtop8
"4/5c Aggro" list (event 89330 deck 877936, fetched 2026-08-08 by
`tools/fetch_tier1_decklists.py`) is a MIDRANGE build:

| | real list | our registered list |
|---|---|---|
| creatures | 4 Psychic Frog, 3 Quantum Riddler, 4 Ragavan, 4 Scion of Draco, 4 Territorial Kavu | 4 Doorkeeper Thrull, 4 Wild Nacatl, 4 Ragavan, 4 Scion of Draco, 4 Territorial Kavu |
| interaction | **3 Fatal Push, 2 Wrath of the Skies**, 4 Stubborn Denial, 4 Leyline Binding | 4 Lightning Bolt, 2 Stubborn Denial, 4 Leyline Binding |

Shared core: 20 cards (Ragavan, Scion of Draco, Territorial Kavu, Leyline
Binding, Leyline of the Guildpact). Half the spells differ, directionally —
the real deck has spot removal and a sweeper, ours has neither.

Predicted: our more aggressive build inflates the win rate; the real list
would fall toward the [50-65] band.

## Result — FALSIFIED

Field sweep, n=6 Bo3, 24 opponents, same engine code as the baseline,
quiet box:

| arm | field WR |
|---|---|
| our registered list (baseline) | 79.2% |
| **real mtgtop8 4/5c Aggro list** | **84.7%** |

The real list is **5.5pp STRONGER**. The decklist is exonerated; if anything
our build understates the archetype.

## What the spread shows instead

Real-list arm, by opponent:

* **100%** vs Jeskai Blink, Goryo's Vengeance, Dimir Midrange, Azorius
  Control, Azorius Control (WST v2), Boros Ponza, Eldrazi Ramp, Creatures
  Toolbox, Azorius Blink
* 83% vs Affinity, Eldrazi Tron, Izzet Prowess, Azorius Control (WST),
  Pinnacle Affinity, Hollow One
* 67% vs Amulet Titan, Living End, Instant Reanimator, Broodscale
  Bloodchief, Grixis Reanimator
* **50%** vs Boros Energy, Ruby Storm

Zoo beats every interactive/fair deck 83-100% and only draws even against
the two decks that RACE it. That is invariant to the spell suite, so the
shared domain core carries the deck. This is the same defending-side pattern
both prior docs described, re-confirmed from a new direction.

## Two real defects found while testing (neither is the cause)

### 1. The 6.07% meta share is assigned to an archetype that is not in the real top-16

`data/tier1_decklists/2026-08-08/DIFF_REPORT.md` lists the mtgtop8 Modern
top-16 breakdown. **"Domain Zoo" does not appear in it.** The sixteen are
Boros Aggro, Eldrazi Ramp, Broodscale Bloodchief, UR Aggro, Dimir Control,
Instant Reanimator, Blink, Creatures Toolbox, Affinity, 4/5c Aggro, UW
Control, Reanimator, Amulet Titan, Hollow One, UrzaTron, Living End.

The nearest real bucket is **4/5c Aggro at 4.0%**. Our registry gives Domain
Zoo **6.07%**, one of the largest weights in the field.

`decks/modern_meta.py`'s header records the decision (2026-08-09): the
"Domain Aggro" (2.78%) and "4/5c Aggro" (3.29%) buckets were merged onto
Domain Zoo because the fetched 4/5c Aggro list is "a near-exact card match"
for it. The archetype-identity call is defensible — same domain shell — but
"near-exact" overstates a 20-card overlap in which half the spells differ,
and the share was merged while the LIST was never updated.

This is a genuine weighting error and it distorts every weighted win rate in
the dashboard, not only Zoo's. It is NOT the cause of the flat-WR outlier
(flat WR is unweighted), so it is recorded here and left as a decision for
the project owner rather than changed unilaterally.

### 2. Scion of Draco's keyword static is completely inert

Oracle: "Each creature you control has vigilance if it's white, hexproof if
it's blue, lifelink if it's black, first strike if it's red, and trample if
it's green."

Probed on a board with Leyline of the Guildpact (domain 5) and Scion of
Draco resolved:

```
Ragavan, Nimble Pilferer   2/1  colors=['R']       keywords=[]   # expected first strike
Territorial Kavu           5/5  colors=['G','R']   keywords=[]   # expected trample + first strike
Wild Nacatl                3/3  colors=['G']       keywords=[]   # expected trample
```

No creature receives any keyword. Related: `leyline_guildpact_etb`
(`engine/card_effects.py`) sets only the domain flag — its docstring claims
"all nonland permanents you control are all colors" but the code does not
implement that half, so even a working Scion would grant off printed colours
rather than all five.

Both are real bugs. **Neither can explain the outlier, because both are
DEFLATIONARY** — Zoo is posting 79-85% without its keyword package, and
fixing either would make it stronger. Domain P/T maths is correct (domain=5,
Kavu 5/5, Nacatl 3/3), so the CDA side is fine.

## Verdict and loop-break

Per CLAUDE.md's loop-break rule, this is the third consecutive effort at this
outlier without moving its win rate, so no further code goes at Zoo from
this direction. Established so far, cumulatively:

* NOT a Zoo over-buff (2026-08-20, replay-verified)
* NOT the held-removal deployment window (2026-08-20, retracted)
* NOT decisional; structural (2026-08-26)
* NOT the decklist (this doc)
* NOT the keyword package (this doc — it is not even active)

What remains, and where the next session should look: Zoo's 100% column
against every interactive deck sits alongside Azorius Control's 21% field WR
measured the same day. These are almost certainly one phenomenon seen from
both ends, and the cheaper handle is the Azorius side, where a single deck's
failure is easier to instrument than a whole field's.

## Do not re-run

The decklist swap. It is measured, the real list is stronger, and the arm is
preserved on `scratch-zoo-reallist` (`659331d`) if anyone wants the numbers
again.
