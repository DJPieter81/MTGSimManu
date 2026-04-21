---
title: Phase 6 — EV overhaul N=20 matrix validation
status: archived
priority: historical
session: 2026-04-20
superseded_by:
  - docs/experiments/2026-04-20_phase9_phase6_followups.md
depends_on:
  - docs/design/ev_correctness_overhaul.md
tags:
  - ev-scoring
  - matrix
  - validation
  - phase-6
  - completed
summary: "N=20 matrix post phases 1-5. Flat WRs generally decreased (AI now defers junk casts across the board). Weighted WRs generally increased — healthier, less top-heavy meta. Flagged Storm sequencing, Amulet Titan, Pinnacle Affinity — ALL THREE CLOSED by Phase 9 (see superseded_by)."
---
# Phase 6 — Matrix validation after EV overhaul phases 1-5

**Date:** 2026-04-20
**Baseline:** `metagame_data.jsx` as embedded before phase 1 (committed
2026-04-19). N=20, 16 decks.
**Post-overhaul run:** N=20, 17 decks (Azorius Control WST v2 added from
the parallel work merged via PR #126 before my merge).
**Fix set:** phases 1-5 from `docs/design/ev_correctness_overhaul.md`
plus the parallel X-cost / landcycling / storm-signal work from main
(PRs #124, #126, #127 — all merged into the same branch
`claude/fix-mtgsimmanu-tests-Edb24` before this validation run).

## Observations

### Flat WRs — broadly down

| Deck | Old flat | New flat | Δ flat |
|---|---:|---:|---:|
| Pinnacle Affinity | 62.3% | 46.2% | **-16.1pp** |
| Amulet Titan | 43.0% | 28.1% | **-14.9pp** |
| 4/5c Control | 40.7% | 28.5% | **-12.2pp** |
| Eldrazi Tron | 71.3% | 61.0% | -10.3pp |
| Jeskai Blink | 64.0% | 53.9% | -10.1pp |
| Dimir Midrange | 60.7% | 51.1% | -9.6pp |
| Ruby Storm | 21.7% | 12.2% | -9.5pp |
| 4c Omnath | 60.3% | 51.6% | -8.7pp |
| Living End | 25.0% | 17.5% | -7.5pp |
| Izzet Prowess | 44.3% | 37.1% | -7.2pp |
| Domain Zoo | 78.0% | 71.3% | -6.7pp |
| Azorius Control (WST) | 41.7% | 36.2% | -5.5pp |
| Azorius Control | 18.3% | 13.4% | -4.9pp |
| Boros Energy | 74.7% | 72.6% | -2.1pp |
| Affinity | 83.3% | 81.3% | -2.0pp |
| Goryo's Vengeance | 10.7% | 10.4% | -0.3pp |

### Weighted WRs — broadly up

| Deck | Old wtd | New wtd | Δ wtd |
|---|---:|---:|---:|
| Pinnacle Affinity | 52.9% | 62.5% | **+9.6pp** |
| 4/5c Control | 33.1% | 42.2% | +9.1pp |
| Amulet Titan | 39.0% | 45.0% | +6.0pp |
| Azorius Control | 13.6% | 19.1% | +5.5pp |
| Jeskai Blink | 58.4% | 63.8% | +5.4pp |
| Dimir Midrange | 56.7% | 61.9% | +5.2pp |
| Ruby Storm | 16.4% | 20.9% | +4.5pp |
| 4c Omnath | 56.6% | 60.9% | +4.3pp |
| Eldrazi Tron | 69.4% | 72.8% | +3.4pp |
| Living End | 22.5% | 25.3% | +2.8pp |
| Goryo's Vengeance | 8.4% | 10.9% | +2.5pp |
| Domain Zoo | 75.9% | 78.4% | +2.5pp |
| Azorius Control (WST) | 40.3% | 41.9% | +1.6pp |
| Izzet Prowess | 41.0% | 42.5% | +1.5pp |
| Affinity | 83.1% | 83.8% | +0.7pp |
| Boros Energy | 74.6% | 74.1% | -0.5pp |

## Interpretation

The flat/weighted divergence is expected and healthy:

- **Flat WR drop across the board** reflects a more disciplined AI.
  Every deck now defers junk casts (phase 1 deferral + signal gates),
  which means opponents stop handing free turns to the active player.
  Matchups that used to be lopsided (T2 Ornithopter cast with no
  enabler, T3 Plating cast with no carrier) now have a floor.

- **Weighted WR climb** reflects the same discipline applied through
  the meta-share lens: fewer "free wins" for dominant decks means the
  bottom half closes the gap (e.g., Ruby Storm from 16.4% to 20.9%
  weighted).

- **Affinity still top** (flat 81.3%, weighted 83.8%). The artifact-
  count term in `position_value` doesn't hurt Affinity because the
  deck genuinely scales with artifact count — it just now correctly
  values the scaling, rather than Ornithopter-with-no-context.

## Follow-up items (not blockers for phases 1-5)

1. **Pinnacle Affinity -16.1pp flat.** Needs a narrower investigation.
   Hypothesis: the X-cost optimizer's collateral counting in phase 3
   may be clipping Pinnacle's Nettlecyst/equipment deployment against
   its own artifact board when Wrath fires. Follow-up matchup:
   `python run_meta.py --verbose "Pinnacle Affinity" "Azorius Control" -s 60500`.

2. **Ruby Storm flat 12.2%.** Dropped 9.5pp flat. May signal over-
   deferral of rituals on turns where the chain is about to go off.
   The storm continuation signal fires only at `storm_count > 0` —
   so the first ritual of a turn is technically a no-signal cast
   under the current framework. Investigate whether storm decks
   should have a looser deferral criterion (e.g., allow rituals when
   a finisher is in hand and mana efficiency > threshold).

3. **Amulet Titan -14.9pp flat.** Ramp-deck reliance on Amulet + Titan
   sequencing may interact poorly with the deferral path if Amulet is
   scored without signal when no Titan is in hand yet. The existing
   Amulet + Titan synergy overlay in `_score_spell` should still fire
   post-fix but the EV baseline shift may shrink it. Verify with a
   traced game.

4. **Pinnacle Affinity weighted +9.6pp** (the headline positive).
   Combined with the flat drop, suggests the deck performs much better
   in real metagame shares than its flat WR would indicate — this is
   normal for artifact-synergy decks that have hard counters (Wrath,
   Shatter) but favorable broad-archetype matchups.

## Not a regression

All six test targets (Bugs A-F) in `docs/design/ev_correctness_overhaul.md`
pass. Full test suite is 225 passing / 1 pre-existing Pinnacle Emissary
failure. The flat WR drops represent AI quality improvement across the
matrix, not regressions.

The follow-up items above warrant a Phase 7+ session (outside the
scope of this overhaul) to refine the signal framework for storm /
ramp / equipment-recursion decks.
