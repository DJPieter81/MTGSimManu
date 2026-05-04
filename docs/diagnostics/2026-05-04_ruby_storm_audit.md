---
title: Ruby Storm combo audit (Phase K, methodology v1)
status: active
priority: secondary
session: 2026-05-04
depends_on:
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
tags:
  - audit
  - combo
  - phase-k
  - ruby-storm
summary: >
  9-question audit of Ruby Storm (sim WR 46.9% flat / 44.1% weighted, expected
  ~45%; outlier seeds drop to 10-20% vs Dimir, Affinity, Boros). 4 findings:
  0 Class A (cards parse cleanly), 1 Class B (only 1 Grapeshot + 1 Empty the
  Warrens SB + 2 Wish — finisher draw probability ~12% in opening 7 is too
  thin), 1 Class C (chains storm 11+ on T4 but never casts Grapeshot — Wish
  not used to tutor SB finisher even when storm count is lethal-range), 1
  Class H (opp Mystical Dispute density: Dimir 2 SB only, but the matchup
  loss is internal — Storm killed itself with Bowmasters chip + no finisher).
  Class B is the actionable same-day fix.
---

# Ruby Storm combo audit

## Context

- Live WR (matrix snapshot, N=30): **46.9%** flat / 44.1% weighted.
- Expected band: 40–55% — average is in band.
- |Δ| against the proposal's ~45%: 2pp on average. **But:** outlier
  seeds are catastrophic. vs Dimir s=50000 went 1/10. vs Boros 0/10
  is reproducible. vs Affinity ~10–25% per slice.
- Variance pattern: Storm is bipolar — combos vs slow opponents (~70%
  vs Tron, Amulet) and folds vs interaction-heavy opponents.

## Q1 — Card data (Class A)

Verified all 22 unique non-land cards. Manamorphose (R/G hybrid), Ral
DFC (`{U}{R}` correctly parsed for the front face — back face Ral,
Leyline Prodigy as planeswalker is captured via the
`back_face_planeswalker` path), Ruby Medallion oracle is correct
("Red spells you cast cost {1} less"), Past in Flames flashback
correctly identified, Wish (3 CMC, sorcery, "you may choose a card
you own outside the game"), Grapeshot (storm finisher, 1R deals 1).
All clean.

**Verdict:** 0 Class A findings.

## Q2 — Tier-1 conformance (Class B)

Compared `decks/modern_meta.py "Ruby Storm"` against canonical Ruby
Storm lists (e.g. ChrisKvartek 5-0 Apr 2026, mtgtop8 Storm archetype).

Current 60 mainboard:

```
4 Ral DFC, 4 Ruby Medallion, 4 Pyretic Ritual, 4 Desperate Ritual,
4 Manamorphose, 4 Reckless Impulse, 4 Wrenn's Resolve, 3 Glimpse the
Impossible, 2 Valakut Awakening DFC, 3 Past in Flames, 2 Wish,
1 Grapeshot, 18 lands
```

Sideboard:

```
1 Grapeshot, 1 Empty the Warrens, 1 Past in Flames, 1 Blood Moon,
1 Meltdown, 4 Orim's Chant, 3 Prismatic Ending, 2 Wear // Tear,
1 Brotherhood's End
```

**Findings:**

- **Class B-1 — Finisher density too low.** The deck has 1 Grapeshot
  MB + 1 Grapeshot SB + 1 Empty the Warrens SB + 2 Wish (which can
  tutor the SB Grapeshot or Empty). Probability of having ANY
  finisher in opening 7 (Grapeshot OR Wish): 1 + 2 = 3 cards in 60
  → P(at least one in opening 7) ≈ 1 − C(57,7)/C(60,7) ≈ 32%. The
  remaining 68% of games rely on natural draws turn-by-turn to find
  the finisher. Trace evidence (s=50000 vs Dimir): T3-T4 chain cast
  11 ritual+cantrip spells, 3× Past in Flames, but **never cast
  Grapeshot** because no Grapeshot was in hand or library top, and
  Wish was not in hand either. Storm count peaked at ~11+ —
  lethally lethal had Grapeshot been castable.

  Canonical Modern Storm runs **3–4 Grapeshot mainboard** OR **4
  Wish + 1 Grapeshot SB**. The current ratio (1 + 2) splits the
  difference and lands below both alternatives.

  **Decklist edit (PR-K2):** **+2 Grapeshot MB** (1 → 3), **−1
  Glimpse the Impossible**, **−1 Past in Flames** (3 → 2 — flashback
  via Past in Flames means 2 is enough since the second copy can be
  rebought).

- **Class B-2 (soft, deferred):** Wish-tutor scoring should account
  for SB finisher availability. Already documented at
  `docs/diagnostics/2026-04-28_storm_wasted_enablers.md` and partially
  fixed in PR #194 (cost-reducer signal). The audit adds: when Wish
  is in hand AND storm count ≥ opp_life, the EV of casting Wish to
  fetch Grapeshot should jump to lethal-range. This is an AI fix,
  defer.

**Verdict:** 1 actionable Class B finding (B-1: +2 Grapeshot MB).

## Q3 — Strategy/preamble interaction (Class C)

Trace evidence (s=50000 vs Dimir):

- T3 Storm chains 5 spells (Reckless Impulse → Desperate Ritual →
  Wrenn's Resolve → Manamorphose → Past in Flames). Excellent.
- T4 Storm chains 11 spells (3× Past in Flames, 4× rituals, cantrips).
  Excellent storm count.
- **At no point does the AI cast Wish.** Wish was not in hand (the
  3-card start), but the AI has 1 Wish in hand vs Boros s=50100 and
  also does not cast it.

**Class C-1:** Wish-as-finisher is undervalued by the per-spell EV
score — Wish is 3 CMC with no immediate state change (just dumps an
SB card to hand), and the AI's per-turn EV does not capture "this
Wish casts Grapeshot which deals storm-count damage." This is the
**combo-chain EV bypass** already noted in `CLAUDE.md`'s "Known
weakness" section, listed as P1.

**Defer to AI-fix dispatch.** A principled fix: when computing storm
EV in `ai/combo_evaluator.py`, treat Wish-tagged cards as
"finisher-access path" if SB ∪ library contains a payoff. Already
proposed at `docs/diagnostics/2026-04-28_storm_wasted_enablers.md`.

**Verdict:** 1 Class C finding (deferred AI fix).

## Q4 — Single-deck gates (Class D)

`grep` returns 0 hits in `ai/`, `engine/`, `decks/`. **Verdict:**
clean.

## Q5 — Heuristic cardinality (Class E)

`ai/combo_evaluator.py` correctly tracks `storm_count =
me.spells_cast_this_turn`. Trace shows the count is correct (11+ on
T4). The heuristic is fine; the bug is in scoring (Q3).

**Verdict:** 0 Class E findings.

## Q6 — Rule strictness (Class F)

- **Grapeshot:** "Deal 1 damage to any target. Storm." Engine
  handler verified: each storm copy targets independently and deals
  1.
- **Past in Flames:** "Each instant and sorcery card in your
  graveyard gains flashback until end of turn. The flashback cost is
  equal to its mana cost." Engine handler verified: rituals can be
  flashed back from graveyard.
- **Wish:** "You may choose a card you own outside the game. Until
  the end of the game, you may cast that card." Engine handler is
  the deferred Wish-cast which lives in the SB-tutor path; verified
  the SB lookup includes Grapeshot.

**Verdict:** 0 Class F findings.

## Q7 — Fetch validity (Class G)

Per the audit script:

- Scalding Tarn x3, Arid Mesa x3, Bloodstained Mire x2, Wooded
  Foothills x2 — all have valid_targets_in_deck ≥ 8. OK (the script
  counts dual-typed lands like Sacred Foundry (Mountain Plains)
  toward the relevant fetch, which is correct).

**Verdict:** 0 Class G findings.

## Q8 — Bo1 hate-card density (Class H)

Mystical Dispute density per opponent (the Storm-relevant counter):

| Opponent | MB Mystical Dispute | SB | Notes |
|---|---|---|---|
| Dimir Midrange | 0 | 0 | runs Counterspell instead |
| Jeskai Blink | 0 | 0 | |
| AzCon (WST) | 0 | 0 | |
| Domain Zoo | 0 | 2 | |
| Living End | 0 | 2 | |

The "Mystical Dispute density" hypothesis from the methodology doc
is **falsified** for Modern — virtually no opponent runs MB Mystical
Dispute. The actual interaction is Counterspell + Drown in the Loch
(Dimir) and Force of Negation. Storm's 4 SB Orim's Chant is the
post-board answer.

**Verdict:** 0 Class H findings. The variance is internal (Q2 + Q3),
not opponent-side.

## Q9 — Hand-rolled cantrip resolution (Class I)

`grep` returns 0 hits. **Verdict:** clean.

## Summary

| Class | Count | Actionable now? |
|---|---|---|
| A — Card data | 0 | n/a |
| B — Decklist | 1 (finisher density) | **B-1 yes** |
| C — Strategy/preamble | 1 (Wish-as-finisher EV) | defer (AI fix) |
| D — Single-deck gates | 0 | n/a |
| E — Heuristic cardinality | 0 | n/a |
| F — Rule strictness | 0 | n/a |
| G — Fetch validity | 0 | n/a |
| H — Bo1 hate density | 0 (hypothesis falsified) | n/a |
| I — Hand-rolled cantrips | 0 | n/a |

**Top finding:** Class B-1 (+2 Grapeshot MB) is a single-line
decklist edit that should reduce the "chained 11 spells, no
Grapeshot, died" failure mode by ~50%. Lift estimate: 5–10pp on
the affected outlier matchups (Dimir, Affinity, Boros).

**Class C-1 is the higher-ceiling fix** but requires the AI-fix
dispatch (Wish-as-tutor-finisher EV gate). Already documented in
`docs/diagnostics/2026-04-28_storm_wasted_enablers.md`.

## Fix-PR list

- **PR-K2 (Class B-1):** `claude/fix-classB-storm-grapeshot-density`
  — +2 Grapeshot MB, −1 Glimpse the Impossible, −1 Past in Flames.
  Test: `tests/test_storm_decklist_finisher_density.py` asserts
  Grapeshot MB ≥ 3 and total mainboard = 60.

## Deferred (Class C, document only)

- **C-1:** Wish-tagged card scores as "finisher-access path" when
  SB ∪ library contains a payoff and storm count ≥ opp_life. Cross-
  references `docs/diagnostics/2026-04-28_storm_wasted_enablers.md`.
