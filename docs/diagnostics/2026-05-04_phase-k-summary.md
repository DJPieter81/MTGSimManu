---
title: Phase K combo audit summary — cross-deck findings + dispatch
status: active
priority: primary
session: 2026-05-04
depends_on:
  - docs/design/2026-05-04_modern_combo_audit_methodology.md
  - docs/diagnostics/2026-05-04_goryos_vengeance_audit.md
  - docs/diagnostics/2026-05-04_amulet_titan_audit.md
  - docs/diagnostics/2026-05-04_ruby_storm_audit.md
  - docs/diagnostics/2026-05-04_living_end_audit.md
  - docs/diagnostics/2026-05-04_affinity_overperformance_audit.md
tags:
  - audit
  - combo
  - phase-k
  - summary
summary: >
  Phase K applied the Legacy lessons #29 + #30 combo-audit methodology to
  Modern's 5 priority outliers. Total: 14 actionable findings. Class
  distribution: 2 Class A (1 Waker oracle, 1 Wear // Tear split-card CMC),
  2 Class B (Goryos no-Atraxa, Storm finisher density), 1 Class C/E mirror
  per deck (deferred to AI-fix dispatch), 9 Class H on opponents (Affinity
  inverted-H). 12 fix PRs filed in this dispatch (PR-K1..PR-K12).
  Recommended P0/P1 backlog updates: WITHDRAW P1-2 Amulet Titan as currently
  framed (the 45% flat WR matches expected; matchup outliers are Class C/E
  not "scoring fix"). RE-FRAME P0-C Affinity dominance as 9-PR
  decklist-edit dispatch (Class H), not AI-scoring.
---

# Phase K combo audit — summary report

## Cross-deck findings table

| Class | Goryo's | Amulet | Storm | Living End | Affinity | Total |
|---|---|---|---|---|---|---|
| A — Card data | 1 (W//T fuse CMC) | 0 | 0 | 1 (Waker oracle) | 0 | **2** |
| B — Decklist | 2 | 0 (1 soft) | 1 | 0 | 0 | **3** |
| C — Strategy/preamble | 1 (defer) | 1 (defer) | 1 (defer) | 1 (defer) | 0 | **4 deferred** |
| D — Single-deck gates | 0 | 0 | 0 | 0 | 0 | **0** |
| E — Heuristic cardinality | 1 (defer w/C) | 1 (defer; eng promote) | 0 | 1 (defer) | 0 | **3 deferred** |
| F — Rule strictness | 0 | 0 | 0 | 0 | 0 | **0** |
| G — Fetch validity | 0 | 0 | 0 | 0 | 0 | **0** |
| H — Bo1 hate density | 0 | 0 | 0 | 0 | **9 (inverted)** | **9** |
| I — Hand-rolled cantrips | 0 | 0 | 0 | 0 | 0 | **0** |

**Total actionable findings (Class A/B/H):** 14.
**Total deferred (Class C/D/E):** 7.

## Same-day fix PRs filed (PR-K1 through PR-K12)

| PR | Class | Deck | Change | Test |
|---|---|---|---|---|
| PR-K1 | B-1 | Goryo's Vengeance | +4 Atraxa MB, −4 Solitude | `tests/test_goryos_decklist_atraxa.py` |
| PR-K2 | B-1 | Ruby Storm | +2 Grapeshot MB, −1 Glimpse, −1 Past in Flames | `tests/test_storm_decklist_finisher_density.py` |
| PR-K3 | A-1 | Living End | Patch Waker of Waves oracle in `ModernAtomic_part*.json` | `tests/test_waker_of_waves_oracle.py` |
| PR-K4 | H | Boros Energy | +1 Wear // Tear MB, −1 Thraben Charm | `tests/test_boros_decklist_artifact_hate.py` |
| PR-K5 | H | Ruby Storm | +1 Wear // Tear MB, −1 Glimpse | `tests/test_storm_decklist_artifact_hate.py` |
| PR-K6 | H | Eldrazi Tron | +1 Pithing Needle MB, −1 Endbringer | `tests/test_etron_decklist_artifact_hate.py` |
| PR-K7 | H | Goryo's Vengeance | +1 Boseiju MB, −1 Plains | `tests/test_goryos_decklist_boseiju.py` |
| PR-K8 | H | Domain Zoo | +1 Wear // Tear MB, −1 Stubborn Denial | `tests/test_zoo_decklist_artifact_hate.py` |
| PR-K9 | H | Living End | +1 Force of Vigor MB, −1 Subtlety | `tests/test_living_end_decklist_artifact_hate.py` |
| PR-K10 | H | Izzet Prowess | +1 Pick Your Poison MB, −1 Lava Dart | `tests/test_izzet_decklist_artifact_hate.py` |
| PR-K11 | H | Dimir Midrange | +1 Pick Your Poison MB, −1 Drown in the Loch | `tests/test_dimir_decklist_artifact_hate.py` |
| PR-K12 | H | Pinnacle Affinity | +1 Vexing Bauble MB, −1 Lavaspur Boots | `tests/test_pinnacle_decklist_artifact_hate.py` |

## Recommended dispatch order (highest expected lift first)

1. **PR-K3 (Living End Waker oracle):** trivial data-edit; unlocks 2
   cyclers in a 4-of-cycler deck (~14% relative density boost).
2. **PR-K4..PR-K12 (Class H batch — 9 PRs):** ship as a batch via
   single PR if reviewers prefer (titled "decklist: increase MB
   artifact-hate density across 9 outlier opponents per Phase K"),
   or 9 individual PRs. Expected: Affinity drops from 84% → ~65-70%.
3. **PR-K1 (Goryo's +4 Atraxa):** unlocks a card-advantage finisher
   target. Expected: Goryo's 9.6% → ~25%, still well below band but
   the Class C deferred fix is needed for the rest.
4. **PR-K2 (Ruby Storm +2 Grapeshot MB):** reduces "no finisher"
   failure mode by ~50%. Expected: Storm outlier matchups (vs Dimir,
   Boros) move from 10–20% → 25–35%.

## Estimated total WR-matrix lift if all 12 PRs land

- **Affinity:** -19pp (84% → ~65%) — biggest swing, brings into
  expected band.
- **Goryo's Vengeance:** +15pp (9.6% → ~25%) — still below band,
  needs Class C AI fix.
- **Ruby Storm outlier matchups:** +5-15pp on the worst (Dimir, Boros).
- **Living End:** +5pp (better grave-fill via 2 working Wakers).
- **Other decks** (Boros, Dimir, ETron, Domain Zoo, Izzet, Pinnacle):
  −5 to −10pp each (now lose more of their previously-uninteractive
  Affinity matchup), partially offset by stronger anti-Affinity
  matrix entries.

**Net matrix-wide:** Average WR variance compresses; T1 decks (Boros,
Affinity) move toward 55-65%, outliers move toward 30-45%. The matrix
becomes more competitive (Bo1 Affinity is no longer auto-win).

## Recommended P0/P1 backlog updates

Reference: `docs/proposals/2026-05-03_p0_p1_backlog.md`.

### WITHDRAW

- **P1-2 (Amulet Titan low WR ~23%):** the matrix-recent flat WR is
  **45.1%**, in expected band. The previously-cited 23% is stale.
  Matchup outliers (Boros, Affinity) are Class C (multi-turn combo
  lookahead) and Class E (Amulet stacking — promote to P1 engine
  fix), not Class B "Arboreal Grazer not prioritising bouncelands."

- **P1-3 (Living End vs Boros 12% — post-cascade attack AI):** if
  PR-K3 (Waker oracle) and PR-K9 (MB Force of Vigor) land first,
  re-measure before doing AI work. The Class A finding alone may
  account for half the gap.

### RE-FRAME

- **P0-C (Affinity dominance ~85%):** **NOT an AI-scoring bug.** The
  fix is the **9 opponent decklist edits** in PR-K4..PR-K12. After
  these land, re-run the matrix; expected drop to ~65-70%. If still
  >70%, then proceed with P0-A (Counterspell consistency) and P0-B
  (Removal target inversion).

### KEEP AS-IS

- **P0-A (Counterspell consistency):** still primary. Independent of
  Phase K findings. The 14-100% counter hit-rate variance is a
  separate AI-scoring bug.
- **P0-B (Removal target inversion):** still primary. State-drift
  bug in `_threat_score`.

### NEW (added by Phase K)

- **P1-NEW-A (Goryo's Class C — goal-fallback):** when the selected
  GoalEngine goal has no executable plays this turn, evaluate the
  next-priority goal's plays before passing. Affects every combo deck
  whose goal selection mis-prioritises.
- **P1-NEW-B (Storm Class C — Wish-as-finisher EV):** Wish-tagged
  card scores as finisher-access path when SB ∪ library contains a
  payoff and storm count ≥ opp_life. Already proposed at
  `docs/diagnostics/2026-04-28_storm_wasted_enablers.md`.
- **P1-NEW-C (Living End Class C — suspend-as-payoff):** Living End
  AI should suspend Living End on T2 when no cascade enabler is in
  hand and 4 mana are available.
- **P2 → P1 promotion (Amulet of Vigor stacking):** previously cosmetic;
  audit shows it's the load-bearing piece in the deck's late-game
  ceiling. Engine fix.

## Lesson reinforcement

- **Lesson #30 holds for Modern.** Many "AI-scoring bug" framings in
  the P0/P1 backlog dissolve once questions 1, 2, 7, 8 of the
  methodology are run. Affinity's 84% is purely Class H. Amulet's
  matchup-outliers are Class C/E, not Class C "scoring fix."
- **Class A bugs in MTGJSON do exist for Modern** (Waker of Waves) —
  the data refresh isn't perfect and a regression test naming the
  card is the only safety net.
- **Bo1 vs Bo3:** the matrix default is Bo1, which makes mainboard
  hate-card density a **first-class concern**. Real Modern players
  pick MB hate based on expected meta share; the sim's decklists
  should follow the same logic.
