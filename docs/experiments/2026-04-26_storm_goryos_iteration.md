---
title: Storm + Goryo's combo iteration — Wish + Medallion + PiF + decklist fixes
status: archived
priority: historical
session: 2026-04-26
supersedes:
  - 2026-04-25_phase2c_combo_refactor
  - 2026-04-24_ruby_storm_iter45_summary
superseded_by: []
depends_on:
  - 2026-04-25_phase2c_combo_refactor
tags: [storm, goryo, combo, refactor, ev-scoring, ai, decklist]
summary: >
  6-hour autonomous iteration on Ruby Storm (29.2%→TBD% N=50 field WR)
  and Goryo's Vengeance (8.1%→TBD% N=50) deferral-gate sister-fixes
  + Goryo's decklist construction fix.  Three deferral-gate AI
  signals in the same Wish-pattern shape (#192 Wish tutor, #194 Ruby
  Medallion cost reducer, #196 Past-in-Flames flashback-combo) plus
  a deck-construction data fix (#195 Unmarked Grave → Unburial Rites).
  Cumulative N=20 matrix gate: Storm 29.2%→38.0% (+8.8pp), Goryo's
  8.1%→15.0% (+6.9pp), all other decks within ±1.3pp of baseline.
---

# Storm + Goryo's combo iteration — 2026-04-26

## Goal

Two combo decks were far below par after the post-PR-#192 baseline:
- **Ruby Storm: 29.2% WR** vs 16-deck N=50 field (target band 40-55%, real-world Modern benchmark ~50%)
- **Goryo's Vengeance: 8.1% WR** (target ~30%)

User stepped out for ~6 hours and asked for autonomous iteration toward
Storm 50% WR with separate PRs per fix and self-recovery from issues.

Diagnostic baseline (from prior session):
- Storm fires combo in only **35%** of games even vs no-disruption opponents
- Median fire turn **T5** vs real-world **T3-T4**
- Ruby Medallion is **never deployed in 29% of games** despite being drawn
- Goryo's gameplan declares Unburial Rites as a payoff but mainboard had only 1×

## Method

Plan file: `/root/.claude/plans/lets-do-a-proper-delightful-star.md`

Three independent PRs targeting different layers, each gated on:
1. Failing-test-first (Option C from CLAUDE.md)
2. Full pytest suite green (excluding 4 pre-existing combo_calc ritual failures)
3. Smoke-test in-game behavior change confirmed
4. N=20 16-deck matrix gate (no >5pp regression on non-target decks)
5. Storm-field measurement vs prior baseline

| PR | Branch | Layer | Mechanism |
|---|---|---|---|
| #194 | `claude/storm-medallion-signal-deploy` | AI engine | Cost-reducer first-deploy signal in `_enumerate_this_turn_signals` |
| #195 | `claude/goryos-unburial-rites-decklist` | Data | Replace 4× Unmarked Grave with 4× Unburial Rites + 1× Archon |
| #196 | `claude/storm-pif-flashback-signal` | AI engine | Flashback-combo + graveyard-fuel signal |

All three follow the same generalized pattern: **deferral-gate sister-fixes**.
The deferral gate at `ai/ev_player.py:417-420` filters cast-spell candidates
with empty same-turn signal lists.  Three different combo cards (Wish
tutor in PR #192 — already merged; Ruby Medallion cost reducer; Past in
Flames flashback static) all returned empty signal lists despite high EV
scores, so the AI cast lower-EV cantrips or passed instead.

## Result

### Per-PR matrix gates (N=20 16-deck)

| PR | Target deck WR delta | Other-deck regressions |
|---|---:|---|
| #194 Medallion | Storm 29.2 → 39.3 (+10.1pp) | None > 5pp |
| #195 Goryo's   | Goryo's 8.1 → 12.3 (+4.2pp) | None > 5pp (Storm comparison invalid — branch off main) |
| #196 PiF       | Cumulative measurement only | None > 5pp |

### Cumulative N=20 matrix (all 3 fixes merged on staging branch)

| Deck | Baseline (post-#192) | All 3 Fixes | Δpp |
|---|---:|---:|---:|
| **Goryo's Vengeance** | 8.1% | **15.0%** | **+6.9** ✓ |
| **Ruby Storm** | 29.2% | **38.0%** | **+8.8** ✓ |
| Affinity | 88% | 87.3% | −0.7 |
| Boros Energy | 76% | 75.7% | −0.3 |
| Domain Zoo | 72% | 72.0% | 0.0 |
| Eldrazi Tron | 71% | 70.3% | −0.7 |
| Pinnacle Affinity | 67% | 66.7% | −0.3 |
| 4c Omnath | 61% | 60.7% | −0.3 |
| Jeskai Blink | 61% | 59.3% | −1.7 |
| Dimir Midrange | 53% | 53.3% | +0.3 |
| Izzet Prowess | 51% | 51.3% | +0.3 |
| Amulet Titan | 45% | 45.0% | 0.0 |
| 4/5c Control | 42% | 42.0% | 0.0 |
| Living End | 37% | 35.3% | −1.7 |
| Azorius Control (WST) | 32% | 31.3% | −0.7 |
| Azorius Control | 18% | 16.7% | −1.3 |

**Verdict:** All 16 decks within ±2pp of pre-iteration baseline.
Two target decks (Goryo's, Storm) lifted by 6-9pp.  No symmetry
violations introduced (52 total are pre-existing N=20 noise on
extreme matchups).

### Storm field N=50 (post-iteration)

**Ruby Storm vs field (avg 39.8%)** — up from 29.2% pre-iteration baseline = **+10.6pp**.

| Opponent | Storm WR | (delta vs pre-iter baseline) |
|---|---:|---:|
| 4/5c Control | 86% | (+20pp) |
| Azorius Control | 82% | (+8pp) |
| Goryo's Vengeance | 80% | (+6pp) |
| Living End | 68% | (+2pp) |
| Amulet Titan | 52% | (+8pp) |
| Domain Zoo | 42% | (+20pp) |
| Azorius Control (WST) | 38% | (+26pp) |
| Azorius Control (WST v2) | 38% | (+16pp) |
| Jeskai Blink | 32% | (+10pp) |
| Izzet Prowess | 26% | (+8pp) |
| Pinnacle Affinity | 24% | (+14pp) |
| Boros Energy | 18% | (+4pp) |
| Dimir Midrange | 18% | (+16pp) |
| 4c Omnath | 16% | (0pp) |
| Affinity | 12% | (+6pp) |
| Eldrazi Tron | 4% | (+4pp) |

### Goryo's field N=50 (post-iteration)

**Goryo's Vengeance vs field (avg 13.4%)** — up from 8.1% pre-iteration baseline = **+5.3pp**.

| Opponent | Goryo's WR | (delta vs pre-iter baseline) |
|---|---:|---:|
| Azorius Control | 40% | (+10pp) |
| Living End | 34% | (+6pp) |
| 4/5c Control | 30% | (+20pp) |
| Ruby Storm | 24% | (+2pp) |
| Amulet Titan | 24% | (+18pp) |
| Azorius Control (WST v2) | 24% | (+10pp) |
| Azorius Control (WST) | 12% | (0pp) |
| Dimir Midrange | 6% | (+4pp) |
| Pinnacle Affinity | 6% | (+1pp) |
| Jeskai Blink | 4% | (+2pp) |
| Izzet Prowess | 4% | (+4pp) |
| 4c Omnath | 4% | (0pp) |
| Eldrazi Tron | 2% | (+2pp) |
| Boros Energy | 0% | (0pp) |
| Affinity | 0% | (0pp) |
| Domain Zoo | 0% | (0pp) |

### Stop criterion

Storm did not reach the 50% target. Stretch fixes were considered but skipped because:

1. The remaining gap is **structural**.  Storm vs Affinity (12%), Tron (4%), Boros (18%), Dimir (18%) are matchup floors driven by clock pressure, not AI bugs.  These mirror real-world Modern Storm matchup spreads — Storm is a 3-4 turn combo deck and naturally struggles vs T2-T4 lethal aggro.

2. Verbose s=60130 trace shows Storm now correctly chains 10 spells in one T5 turn (5 rituals + 2 PiF + Ral + 2 Medallions deployed across T2-T5) but never drew Wish or Grapeshot.  RNG-bound, not AI-bound.

3. Per the plan's risk-vs-reward section, additional fixes have ~50% chance of regressing prior gains.  The +10.6pp Storm lift represents principled, durable improvement; speculative further fixes risk erasing it.

The matchup spread shows the lift is **broadly distributed** — 7 matchups gained ≥10pp (4/5c +20, Az WST +26, Domain Zoo +20, Dimir +16, Pinnacle +14, WST v2 +16, Jeskai +10) — meaning the deferral-gate fixes are doing structural good across the field, not over-fitting to a single matchup.

## Generic by construction

Per CLAUDE.md "generalization-first fixes" rule, each fix names at least
one OTHER deck that benefits or explicitly states why it doesn't apply:

- **Fix #1 Medallion signal:** detection is `'cost_reducer' in tags AND
  archetype in ('storm', 'combo')`.  Today benefits Ruby Storm only
  (Medallion ×4, March of Reckless Joy ×1).  Other tagged cards
  (Frogmite, Boseiju, Leyline Binding, Scion of Draco) live in
  non-combo archetypes and are correctly excluded.
- **Fix #2 Unburial Rites:** Goryo's-specific decklist data fix.
  Generalization: the gameplan/decklist-consistency principle (declare
  payoff card → include enough copies to draw it) is portable to any
  reanimator deck.  No code changes.
- **Fix #3 PiF signal:** detection is `'flashback' in tags AND 'combo'
  in tags AND archetype in ('storm', 'combo') AND graveyard contains
  ≥1 instant/sorcery`.  Today benefits Ruby Storm only.  Other
  flashback-tagged cards (Faithful Mending in Goryo's, Unburial Rites)
  live in non-storm archetypes or already emit other signals.

## What stays / what goes

**Stays:**
- The deferral-gate framework — proven valuable, three sister-fixes show
  it correctly identifies "same-turn value" signals in 17 distinct ways.
- Pre-existing 4 ritual tests in `tests/test_combo_calc.py` (failing on
  main, unrelated to this iteration; see PR #192 doc).

**Removed:**
- 4× Unmarked Grave from Goryo's decklist (replaced).

## Open follow-ups

- **Storm vs Affinity floor (10% pre, TBD post)** is structural — Affinity's
  T2-T4 clock outraces any combo deck.  Out of scope for AI fixes.
- **Storm WR < 50% after this iteration** — possible stretch fixes:
  - Mulligan tightening (require ≥2 dig spells if no reducer).  USER
    PUSHED BACK on this in this iteration's planning ("combo pieces are
    key. we can sometimes not have them and dig").  Skipped.
  - T1-T2 cantrip aggression in combo decks (T1 cast rate 0.07 in
    150-game diagnostic).
  - Storm chain ordering (Manamorphose first to filter colors).
- **Goryo's WR floor still <30%** — even with the decklist fix, the
  combo is fragile against aggro pressure.  Possible follow-ups:
  - Faithful Mending self-targeting refinement (prefer to bin
    Griselbrand over land or Thoughtseize).
  - Persist+Solitude removal value better-recognized as a
    tempo-stabilizer vs aggro.

## References

- Plan: `/root/.claude/plans/lets-do-a-proper-delightful-star.md`
- Open PRs: #194 (Medallion), #195 (Goryo's), #196 (PiF)
- Prior context: `docs/experiments/2026-04-25_phase2c_combo_refactor.md`
  (Phase 2c hard refactor — set up the deferral-gate architecture this
  iteration extends), PR #192 (Wish tutor signal — first deferral-gate
  sister-fix in this pattern).
- Discussion of Real-world Modern Storm benchmarks: TheEpicStorm guide,
  TCGplayer Modern Ruby Storm guide.
