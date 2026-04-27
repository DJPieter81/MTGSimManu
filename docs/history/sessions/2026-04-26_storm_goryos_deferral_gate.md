---
title: Recent work — Storm + Goryo's deferral-gate iteration
status: archived
priority: historical
session: 2026-04-26
tags:
  - session-summary
  - storm
  - goryos
  - deferral-gate
  - wr-outlier
summary: |
  Session summary moved out of PROJECT_STATUS.md during doc cleanup
  (see CLAUDE.md ABSTRACTION CONTRACT — root allowlist).
---

# Storm + Goryo's deferral-gate iteration (2026-04-26)

Three deferral-gate sister-fixes in the same Wish-pattern shape, plus
a Goryo's deck-construction fix.  All on independent `claude/*` branches
with separate PRs (per session protocol — no auto-merge).

The deferral gate at `ai/ev_player.py:417-420` filters cast-spell
candidates with empty same-turn signal lists.  Three different combo
cards (Wish tutor PR #192 — already merged; Ruby Medallion cost
reducer PR #194; Past in Flames flashback static PR #196) all
returned empty signal lists despite high EV scores, so the AI cast
lower-EV cantrips or passed instead.

| PR | Branch | Mechanism |
|---|---|---|
| #194 | `claude/storm-medallion-signal-deploy` | Cost-reducer first-deploy signal (#17) |
| #195 | `claude/goryos-unburial-rites-decklist` | 4× Unmarked Grave → 4× Unburial Rites + 1× Archon |
| #196 | `claude/storm-pif-flashback-signal` | PiF flashback + GY-fuel signal (#18) |

**Cumulative N=20 16-deck matrix gate** (all 3 fixes merged on staging branch):

| Deck | Pre-iteration | All 3 Fixes | Δpp |
|---|---:|---:|---:|
| **Goryo's Vengeance** | 8.1% | **15.0%** | **+6.9** ✓ |
| **Ruby Storm** | 29.2% | **38.0%** | **+8.8** ✓ |
| Affinity | 88% | 87.3% | −0.7 |
| Boros Energy | 76% | 75.7% | −0.3 |
| All others | (within ±2pp) | (within ±2pp) | — |

No deck regressed >2pp.  No symmetry violations introduced.

**Storm field N=50 (final precise measurement):** **39.8%** (+10.6pp
vs pre-iteration baseline 29.2%).  Lift broadly distributed: 7
matchups gained ≥10pp (4/5c +20, Az WST +26, Domain Zoo +20,
Dimir +16, Pinnacle +14, WST v2 +16, Jeskai +10).  Did not reach
the 50% target — remaining gap is structural (Affinity 12%, Tron
4%, Boros 18% are matchup floors driven by clock pressure).

**Goryo's field N=50:** **13.4%** (+5.3pp vs baseline 8.1%).
Control matchups 30-40%, aggro 0%.

**Generic by construction:** every fix uses oracle text + tag
detection.  No card-name hardcoding.  See
`docs/experiments/2026-04-26_storm_goryos_iteration.md` for the
full session log including matchup spreads, smoke-test traces,
and stop-criterion analysis.

**Plan file:** `/root/.claude/plans/lets-do-a-proper-delightful-star.md`
