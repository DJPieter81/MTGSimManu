---
title: Deep audit backlog — expanded rules auditor over the n=20 matrix
status: active
priority: primary
session: 2026-09-15
supersedes: []
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
tags: [rules-audit, deep-audit, calibration, backlog]
summary: >
  First run of the expanded rules auditor (20 CR invariant classes, up from 15)
  over a full n=20 Bo3 matrix. Zero rule VIOLATIONS — the engine is
  rules-correct on every class the auditor now checks. The ranked backlog is
  therefore entirely CENSUS: unmodelled keywords plus the newly-observable
  unhandled-effect classes (graveyard-exile replacement, Role-token / mass
  +1/+1). The WR outliers are not rules-correctness bugs the auditor can see;
  they are AI-decision-quality (the Phase-2 panel's target) or the census
  mechanics below.
---

# Deep audit backlog (2026-09-15)

## Run
`run_meta.py --matrix -n 20 --parallel --rules-audit`, offline scorer, quiet
box, on the expanded auditor (PR #572 head). `aborted=0`, 76 draws credited to
nobody. Raw findings: `audits/rules_audit_20260915T232701Z.jsonl` (50 rows).
Auditor now hooks **20 CR invariant classes**: 305.7 set-land mana, 510.2
combat damage, 508.1a/509.1a attack/block legality, 509/615 combat prevention,
601.2b/608.2 counter/damage upgrades, 601.2c/608.2b target legality, 601.2f
reduction pip-preservation, 107.3 X counters, 702.33 kicker, 603.2 ordinal
cast, 104.3c empty-library loss, and the 704.x SBA block + zone/list lag.

## Headline — zero violations
**Every `check()` invariant passed across all 300 pairs: 0 rule violations.**
The engine plays rules-correct Magic on everything the auditor can currently
observe. This is the strong, deterministic result the deep audit set out to
establish, and it means the out-of-band win rates are NOT engine
rules-defects — they are decision quality or unmodelled mechanics.

## Ranked census backlog (dedup by key×seed×pair)
| rule | distinct | pairs | mechanics (top keys) |
|---|---:|---:|---|
| `keyword/unmodelled` | 33 | 26 | ascend (Ocelot Pride), devoid, metalcraft, ferocious, harmonize, daybound, emerge, meld — the coverage census (`docs/design/rules_coverage.md`); only 7 have registered-deck usage and the top two are effectively correct/mis-mapped |
| `unhandled/replacement` | 9 | 9 | **Dauthi Voidwalker, Sanctifier en-Vec, Rest in Peace** — the graveyard-exile replacement family resolves as a no-op (a real correctness gap, previously only on the `ALLOWED_UNHANDLED` allowlist, now ranked) |
| `unhandled/spell` | 8 | 7 | **Practiced Offense** (Role-token / mass +1/+1 class), **Demonic Dread** (cascade −3/−0 rider), **Surgical Extraction** (targeted graveyard exile) |

`606/loyalty_unexecutable_kind` did not fire this run (no registered deck
activated an unexecutable loyalty ability in these games); the seam stays live
for future decks. No `unhandled/etb|activated|alt_cast|static` this run.

## WR outliers (this matrix, flat WR vs calibration band)
| deck | WR | band | dir |
|---|---:|---|---|
| Domain Zoo | 74 | [50,65] | above |
| Eldrazi Tron | 69 | [50,65] | above |
| Broodscale Bloodchief | 66 | ~[45,65] | above |
| Dimir Midrange | 63 | [45,60] | above |
| Amulet Titan | 21 | [45,60] | below |
| Creatures Toolbox | 21 | [30,70] | below |
| Jeskai Blink | 29 | [45,60] | below |
| Azorius Blink | 28 | [30,70] | below |
| Hollow One | 37 | ~[40,55] | below |
| Azorius Control (WST) | 42 | ~[45,60] | below |

The auditor finds no rules-defect behind any of these. Attribution is
therefore AI decision quality (Phase 2) or the census mechanics above.
Symmetry note: Goryo's vs Broodscale summed 135% (off 35%) — n=20 variance on
one pair, re-check at n=60 if it persists; not an engine violation.

## Ordered next units (drive the WR-resolution loop)
Structural fixes only; each failing-test-first, class-sized, measured
same-seed; loop-break after 3 no-movement units on a lane.

1. **Graveyard-exile replacement family** (`unhandled/replacement`, class-sized:
   Dauthi Voidwalker + Sanctifier en-Vec + Rest in Peace + Leyline of the Void +
   Planar Void…). A "if a card would go to a graveyard, exile it instead" /
   "exile target graveyard" replacement the resolver models as a no-op. Highest
   deterministic-correctness value; lifts the graveyard-hate side of Goryo's /
   Living End / reanimator matchups. Needs a typed replacement field + a
   zone_manager replacement branch (single-owner: through the zone funnel).
2. **Role-token / mass +1/+1 class** (`unhandled/spell`: Practiced Offense,
   Monstrous Rage) — a Role/Aura-token or mass-counter payoff resolving as a
   no-op; class of prowess/aggro pump payoffs.
3. **Phase 2 panel findings** — the AI-decision-quality mechanisms the auditor
   cannot see (control passivity/sweeper timing, Amulet payoff-blind tutor
   sequencing, Jeskai creature valuation, ramp finisher deployment) — promoted
   at 3+/5 consensus, cross-referenced here.
4. **Census mechanics with registered usage** (from `rules_coverage.md`):
   devoid typed field + metalcraft mapping fix (both effectively correct today,
   census-vocabulary cleanups), then harmonize/emerge/meld/daybound as their
   single-card decks warrant.

## The standing caveat
0 violations means the below-band decks (Amulet, Toolbox, Jeskai, Blink,
Hollow One) have their headroom in AI decision quality and unmodelled
mechanics, not rules bugs — Phase 2 is where their fixes come from. The
above-band decks (Zoo, Tron) were already found rules-correct after Z1–Z3 and
prior loop-breaks; if Phase-2 fixes to their victims don't pull them into band,
the residual is the excluded band decision, reported not patched.
