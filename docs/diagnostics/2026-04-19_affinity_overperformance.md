---
title: Affinity Overperformance — 5 Hypotheses
status: superseded
priority: historical
session: 2026-04-19
supersedes_fields: []
supersedes: []
superseded_by:
  - docs/design/ev_correctness_overhaul.md
tags:
  - affinity
  - hypothesis
  - ranking
summary: Ranking of why Affinity sim WR (~88%) exceeds real-world (~55%). Input to the overhaul design.
---
# Diagnostic — Affinity's ~80% sim WR vs ~55% real-world

**Date:** 2026-04-19 (continued)
**Status:** Investigation, no code changes. Hypotheses ranked for follow-up.
**Context:** After Bugs 1-6 and recurring-token EV subsystem (C) landed,
Affinity still wins ~80% across Boros / Jeskai / Dimir matchups. Real-world
Modern tournament data shows Affinity at ~55% — the deck is competitive
but not dominant. The 25-30pp overperformance gap is the subject of this
diagnostic.

---

## 1. Audit data (30-game field sample)

```
Win rate: 77% (23/30)
Avg win turn: T5.3  |  Avg loss turn: T8.9
```

Avg win turn T5.3 is very fast — Affinity rarely plays long games. Real
Modern Affinity averages ~T6.5-7 in competitive play.

### Cast / removal rates

| Card | Cast/game | In wins | Countered | Removed | Interaction rate |
|---|---|---|---|---|---|
| Cranial Plating | 1.0 | 83% | 1 | 3 | **13%** |
| Mox Opal | 0.7 | 67% | 0 | 4 | 19% |
| Ornithopter | 0.9 | 100% | 0 | 3 | 11% |
| Nettlecyst | 0.3 | 29% | 0 | 1 | 11% |
| Sojourner's Companion | 0.4 | 21% | 0 | 1 | 9% |
| Thought Monitor | 0.5 | 75% | 0 | 1 | 6% |

**Observation 1 — Cranial Plating has 13% interaction rate.** Plating is
Affinity's win-con. In real play it sees >40% removal rate (opponents bring
Wear/Tear, Kolaghan's Command, Nature's Claim). In sim, it's removed ~1/7
times cast.

**Observation 2 — Opponent removal mis-targets Mox Opal.** Mox Opal is
removed 4× vs Plating's 3×, despite Plating being game-winning. Mox Opal's
lifetime value (1 mana per activation, cast ~T2, survives ~4 turns = ~4
mana total) is an order of magnitude less than Plating's (wins the game
by turn 5-6). Opponent AI is spending removal on the wrong target.

**Observation 3 — Sojourner's Companion rarely removed.** 4/4 artifact
creature with affinity-for-artifacts reliably enters as a 4/4 body for 0
mana on T3 (when Affinity has 6+ artifacts). Real-world, Sojourner's is
mostly **landcycled**, not cast. Sim casts it as a body 0.4x/game.

## 2. Verbose game trace (Boros vs Affinity, seed 50015)

T3 Affinity board:
- Frogmite (2/2), Sojourner's Companion (4/4)
- Mox Opal, Springleaf Drum
- Darksteel Citadel

6 power on T3 against a Boros board with no creatures → 6 damage → life
20 → 14 → 8 over two swings → dies T4.

The sim lets Affinity deploy a 4/4 body (Sojourner) for 0 mana on T3
because affinity-for-artifacts reduces 6 CMC by the artifact count (6
artifacts → cost 0). That interaction alone is probably worth a
measurable slice of the overperformance.

## 3. Ranked hypotheses

### H1 — Opponent removal targeting is miscalibrated for artifact boards (HIGH)

**Signal:** Mox Opal removed 4× vs Plating 3×. Wear/Tear and Kolaghan's
Command spending charges on 2-mana rocks instead of game-ending equipment.

**Likely location:** `ai/ev_player.py` — non-burn removal targeting. When
opp is an artifact deck, the target priority is probably `creature_value`-
based rather than "which piece wins the game." Cranial Plating as an
equipment isn't a creature, so it may not even be in the candidate pool
for creature-removal spells. For destroy-target-artifact spells
(Wear/Tear, Nature's Claim), the target priority needs synergy-weight
like Bug 4's fix.

**Next step:** Extend the Bug-4 synergy-denial premium to
non-creature-removal targeting. "Destroy target artifact" spells should
target the artifact whose removal most hurts the opp's plan — equipment
with scaling clauses (Plating, Nettlecyst) rank higher than mana rocks
(Mox Opal, Springleaf Drum).

**Expected impact:** +5-10pp to opponent WR in Affinity matchups.

### H2 — Sojourner's Companion is cast too often as a body (MEDIUM)

**Signal:** 0.4x/game cast rate as a 4/4 body on T3 for 0 mana.

**Likely location:** `ai/ev_player.py` — cycle vs cast decision. The AI
compares "cast Sojourner as 4/4" vs "artifact-landcycle to fetch
Darksteel Citadel." The cast scores higher because 4/4 power > 1 card
advantage, but in reality the tempo of a 4/4 body on T3 is already
baked into the game being lost — the value is in the landcycle's deck
quality improvement.

**Next step:** Requires thinking about when "immediate body" vs
"deferred deck thinning" should be preferred. For decks where the
landcycle unlocks key colours / synergy, the landcycle is the correct
play most of the time.

**Expected impact:** -3-5pp to Affinity WR.

### H3 — Affinity's mulligan hand threshold is too generous (LOW)

**Signal:** 3% mulligan rate over 30 games. Real-world Affinity mulligans
~15-20% (lands-only hands, or all-expensive-cards hands).

**Next step:** Review `ai/mulligan.py` for Affinity archetype. Likely
the hand-keep criteria are too lax because "free" artifacts count as
playable cards even when they don't advance the plan.

**Expected impact:** -2-3pp to Affinity WR.

### H4 — Board wipe timing is suboptimal (LOW)

**Signal:** Wrath of the Skies sideboarded in for Boros vs Affinity, but
casts/game stats not available in the audit.

**Next step:** Instrument Wrath cast rate and timing. Expected pattern:
Wrath after opponent overcommits a second wave; sim might cast too early
(before overcommit) or too late (when already dead).

**Expected impact:** -2-3pp.

### H5 — Sideboard counts are wrong (LOW)

**Signal:** Existing SB plans have +3 Wear/Tear, +3 Wrath. Real-world
budgets may differ but close.

**Next step:** Cross-check each deck's SB vs Affinity against a recent
tournament top-16 breakdown.

## 4. Recommended sequence

1. **H1 — opponent removal targeting for non-creature artifacts.** Biggest
   expected impact, cleanest fix (extends Bug 4 pattern). Single-commit
   test + fix.
2. **H2 — Sojourner's Companion cycle-vs-cast.** Medium impact, requires
   rethinking tempo value.
3. **H3-H5** — lower-priority tuning, revisit after H1+H2 land.

Do NOT start H1 without a design sketch — targeting heuristics for
non-creature removal are different enough from creature-removal that
they need their own test shape.

## 5. What this diagnostic does NOT tell us

- **Real-world sample size.** Tournament data ~55% is itself variable.
  Affinity's true matchup distribution in the current Modern meta is a
  separate research question.
- **Compound effects.** Fixing H1 may unmask new issues (e.g. Affinity
  is now slower, but its losses come from different patterns).
- **Archetype-specific decisions.** Different Affinity-facing decks
  (Boros aggro vs Dimir control) need different SB strategies. One-size-
  fits-all fixes may shift without resolving.
