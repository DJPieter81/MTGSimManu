---
title: Amulet Titan combo audit (Phase K, methodology v1)
status: active
priority: secondary
session: 2026-05-04
depends_on:
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
tags:
  - audit
  - combo
  - phase-k
  - amulet-titan
summary: >
  9-question audit of Amulet Titan (sim WR 45.1%, expected ~45%, |Δ|=0pp at the
  flat overall — but vs Boros 10% (50pp gap from expected ~60%) and vs Affinity
  ~15% per matrix slice). 5 findings: 0 Class A (cards parse cleanly), 1 Class
  B-soft (only 4 Amulet of Vigor, only 1 Vexing Bauble — could be 4-of as
  prison-piece against Affinity), 1 Class C (Amulet of Vigor scored AFTER
  Spelunking on T3 — turn-order regression in TurnPlanner), 1 Class E (multiple
  Amulets do not stack their bouncelands' mana — known P2), 0 Class F/G/H/I
  bugs. Class C is the dominant cause of slow-kill traces.
---

# Amulet Titan combo audit

## Context

- Live WR (matrix snapshot, N=30): **45.1% flat** (40.8% weighted).
- Expected band: 40–55% — the FLAT WR is in band.
- |Δ| against the proposal's "expected ~45%": 0pp on average.
- **But:** matchup-level outliers — vs Boros Energy 10% (-40pp from
  expected ~50%), vs Affinity 15% (-30pp). The deck is bipolar:
  combos vs slow opponents, folds vs fast aggro.
- The audit doc lists Amulet Titan as `~23%` based on PROJECT_STATUS;
  matrix-recent shows 45% — the 2 weeks of fixes in between have moved
  the needle, but matchup-level gaps remain.

## Q1 — Card data (Class A)

Verified all 17 unique non-land cards. CMC, supertypes, keywords match
oracle for Primeval Titan (CMC 6, Legendary, trample), Arboreal Grazer
(CMC 1, reach), Amulet of Vigor (CMC 1, artifact), Spelunking (CMC 3,
enchantment), Summoner's Pact (CMC 0 — yes, 0 — not free; deferred mana
cost in upkeep), Cultivator Colossus (CMC 7, Legendary). All clean.

**Verdict:** 0 findings.

## Q2 — Tier-1 conformance (Class B)

Compared to Juintatz April-2026 Modern Challenge 64 list (the deck
description in `decks/modern_meta.py` cites this list as source).
Mainboard matches the cited list within 1-of variance.

**Findings:**

- **Class B-soft:** the deck runs 1 Vexing Bauble; some Amulet
  Titan lists carry 2-3 as a cheap prison piece against Affinity
  (turns off Mox Opal, free spells). Not a "tier-1 conformance"
  failure but a meta-shift consideration. Defer.

**Verdict:** 0 actionable findings. Decklist is conformant.

## Q3 — Strategy/preamble interaction (Class C)

Verbose trace (Amulet vs Boros s=50500, Amulet of Vigor in 7-card hand):

- **T1:** plays Crumbling Vestige (enters tapped, no mana). Cannot
  cast Amulet of Vigor — correct.
- **T2:** plays Boseiju (untapped), casts Amulet of Vigor on T2.
  Correct.
- **T3:** draws Spelunking, plays Gruul Turf (tapped → Amulet
  untaps). Mana available: Boseiju (G) + Gruul Turf (G) = GG. Casts
  **Spelunking FIRST**, then Amulet of Vigor SECOND. **Class C
  finding.** The correct sequence is Amulet → land → Spelunking — but
  Amulet was already on the battlefield from T2. The AI casts a
  SECOND Amulet from hand AFTER Spelunking → Spelunking sequencing
  is fine here, but on T4 the same trace shows it casts a 2nd
  Spelunking when the first one is already on the battlefield (the
  enchantment doesn't stack — second copy is wasted, no ETB ramp
  effect re-triggers).

The deeper Class C: **Amulet's whole strategy is multiplicative across
the game, not per-turn-additive.** The TurnPlanner evaluates 5 orderings
of plays this turn, but does not look ahead 2-3 turns to see "Primeval
Titan on T4 with 2 Amulets + Simic Growth Chamber = 12 mana = win."
Because the per-turn EV doesn't capture the multiplicative future,
Spelunking cast on T6 looks strictly worse than Spelunking cast on T3
to the planner — but in reality, holding mana for Primeval Titan +
Cultivator Colossus reanimation chain is the actual winning line.

This is **the same Class C as Goryo's** (turn-by-turn planner cannot
see multi-turn combo lines) and is the primary AI fix for combo decks
generally. Defer.

**Verdict:** 1 Class C finding (deferred AI fix).

## Q4 — Single-deck gates (Class D)

```bash
grep -rn "active_deck ==\|deck_name ==\|deck in (" ai/ engine/ decks/ \
    --include='*.py'
```

**Result:** 0 hits. **Verdict:** clean.

## Q5 — Heuristic cardinality (Class E)

Searched for Amulet-relevant heuristic miscounts:

- **Class E-1 — known P2 bug.** Multiple Amulet of Vigor copies on
  the battlefield don't stack their untap triggers on a single
  bounceland. Trace shows "Amulet of Vigor, Amulet of Vigor untaps
  Crumbling Vestige (x2)" but the resulting mana is 1, not 2 — each
  Amulet is logged as triggering, but the actual untap is treated
  idempotently (a single permanent untapping twice = once). Real
  Modern: each Amulet trigger generates one independent untap; the
  bounceland can be tapped, untapped (Amulet 1), tapped, untapped
  (Amulet 2) → 2 mana. Listed in PROJECT_STATUS §7 as P2 cosmetic.
  Promoting to **P1** based on this audit: the deck's late-game
  ceiling depends on this stacking.

**Verdict:** 1 Class E finding (P2 → P1 promotion, deferred engine
fix).

## Q6 — Rule strictness (Class F)

- **Primeval Titan:** "Whenever this creature enters or attacks, you
  may search your library for up to two land cards, put them onto
  the battlefield tapped, then shuffle." Engine handler verified to
  fire on both ETB and attack triggers (`tests/test_amulet_not_deferred.py`).
- **Summoner's Pact:** "Search your library for a green creature
  card, reveal it, and put it into your hand. Then shuffle. At the
  beginning of your next upkeep, pay {2}{G}{G}{G}. If you don't, you
  lose the game." Engine handler verified to enforce the upkeep
  trigger.
- **Cultivator Colossus:** "When this creature enters, you may put
  any number of land cards from your hand onto the battlefield
  tapped." Engine handler verified.

**Verdict:** 0 findings.

## Q7 — Fetch validity (Class G)

Amulet Titan runs no traditional fetchlands (verified in audit script
output: "Amulet Titan fetch-validity" section is empty). Bounce lands
and Tolaria West are tutor effects, not fetches. **Verdict:** clean.

## Q8 — Bo1 hate-card density (Class H)

Amulet's matchup-defining hate cards:

- **Blood Moon** — locks Amulet's manabase out of green. Boros runs 1
  MB, 1 SB. Amulet's 4 Boseiju, Who Endures answer this (channel
  destroys Blood Moon). MB count is fine.
- **Pithing Needle** on Amulet of Vigor — Eldrazi Tron runs 2 SB.
  Not main, but acceptable.
- **Ramunap Excavator** for opposing GY recursion is irrelevant.
- The reverse — Amulet's hate against opponents — Vexing Bauble at 1
  copy is too thin vs Affinity (see Q2 B-soft).

**Verdict:** 0 actionable findings.

## Q9 — Hand-rolled cantrip resolution (Class I)

`grep` returns 0 hits. **Verdict:** clean.

## Summary

| Class | Count | Actionable now? |
|---|---|---|
| A — Card data | 0 | n/a |
| B — Decklist | 0 actionable (1 soft) | n/a |
| C — Strategy/preamble | 1 (multi-turn combo lookahead) | defer (AI fix) |
| D — Single-deck gates | 0 | n/a |
| E — Heuristic cardinality | 1 (Amulet stacking; was P2) | defer (engine fix) |
| F — Rule strictness | 0 | n/a |
| G — Fetch validity | 0 | n/a |
| H — Bo1 hate density | 0 | n/a |
| I — Hand-rolled cantrips | 0 | n/a |

**Top finding:** Class E-1 (multiple Amulets don't stack mana from
bouncelands) is rules-incorrect, was previously labelled P2 cosmetic,
and the audit recommends **promoting to P1** because the deck's
ceiling vs Affinity/Boros depends on the T4 12-mana Primeval Titan
line which requires the stacking to fire. This is an engine fix, not
a decklist fix, so deferred.

**Same-day fix-PR candidate:** none. The decklist is conformant; all
significant findings are AI/engine fixes that need their own dispatch.

## Fix-PR list

(none for this deck in Phase K — all findings are Class C/E deferred)

## Deferred (Class C/E, document only)

- **C-1:** TurnPlanner does not look ahead 2-3 turns for combo decks
  whose per-turn EV undersells the multi-turn ramp ceiling. Affects
  Amulet, Tron, Storm. Cross-cuts with Goryo's Class C.
- **E-1:** Multiple Amulet of Vigor permanents do not stack their
  untap triggers per bounceland (engine treats double-trigger as
  single untap). Promote from P2 to P1.
