---
title: Affinity 55% → 88% regression — root cause + path forward
status: superseded
priority: primary
session: 2026-04-26
supersedes: []
superseded_by:
  - docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md
depends_on: []
tags: [affinity, holdback, mana-management, regression]
summary: |
  User reported Affinity was at ~55% (balanced T1) "some time ago"
  but the current matrix shows 88-89%.  Bisected to commit
  `7dad56c` (2026-04-24) — the merge of seven Affinity-session
  fix PRs (#143-#149).  The commit message itself flagged the
  side effect: control decks regressed by 5-8pp.  Root cause is
  "Bundle 3 holdback overhaul (A1-A5)" + "E1 Mox Opal dynamic
  metalcraft" interacting badly — control decks over-tap on main
  phase, leaving no instant-speed mana to answer Affinity threats.
---

# Affinity 55% → 88% regression

## What the user noticed

> "remember we had affinity down to 55%"

Current state (post 9 commits this session, dashboard data):
* Affinity flat WR: **88%**
* Affinity matchup spread: 80-100% vs every deck except Domain
  Zoo (60% — only check-deck)
* Game 1 Storm vs Affinity: 50/50 (race fair)
* Match WR (post-board) Storm vs Affinity: 10% (was 0%)

## Bisection

```
$ git log --oneline --grep="affinity\|Affinity" -- *.py | head -10
```

Found `7dad56c` (2026-04-24): "data: N=50 matrix + dashboard
after Affinity-session fixes merged".  Commit message:

> Affinity: 86%/82% → 88.0%/83.9% (within ~5pp noise band;
> effectively unchanged). Defenders we tried to help got worse:
> Jeskai -5/-6, Dimir -6/-6, AzCon (WST) -8/-4. **Most likely
> culprit: Bundle 3 holdback is over-gating control decks'
> main-phase plays.**

The seven merged fixes:
* `#143` E2 — Thoughtseize intelligent target
* `#144` R3 — equipment-carrier removal priority
* `#145` R1 — carrier-pool synergy
* `#146` E3 — evoke path in `can_cast`
* `#147` E1 — Mox Opal dynamic metalcraft re-eval
* `#148` R2 — response gate prefers cheaper counter
* `#149` Bundle 3 — holdback overhaul (A1-A5)

## Suspected root causes

### 1. Bundle 3 holdback overhaul (commit `9122af7`)

> *"colored-aware, scaled, covers cycling/equip"*

The holdback gate decides whether to spend mana on main-phase
plays vs. hold up for instant-speed answers (Counterspell,
Path to Exile, Solitude flash, etc.).  When the gate is too
permissive, the AI taps out on creatures/sorceries and has
no mana when Affinity attacks.

Empirical witness: in the Apr-24 commit message, the same
fix block dropped Jeskai -5/-6, Dimir -6/-6, AzCon (WST) -8/-4.
All control / midrange decks that should have instant-speed
answers.

### 2. E1 Mox Opal dynamic metalcraft (commit `434ae15`)

> *"metalcraft is checked at the moment a mana ability is
> activated, not snapshotted at ETB"*

Pre-fix: Mox Opal's metalcraft was evaluated at ETB and frozen.
If you ETB'd Mox Opal before reaching 3 artifacts, it stayed
colorless until ETB'd again.  Affinity often had Mox Opal
sitting useless until later.

Post-fix: Mox Opal correctly checks metalcraft each tap.
Affinity can ETB Mox Opal early and it auto-activates the
moment metalcraft is reached.  This is rules-correct (CR
702.98) but materially accelerates Affinity's mana — T1 Mox
Opal + T2 third artifact = T2 colored mana from Mox Opal.

The fix is correct per the rules but compounds with #1: the
AI for control decks now faces a faster Affinity AND has
worse holdback decisions.

## Why this session's fixes didn't restore 55%

This session shipped:
* SB scoring regex → tag-driven (Force of Vigor / Brotherhood's
  End / Vandalblast etc. now score correctly)
* +2 Wear // Tear in Azorius / Jeskai SBs
* Wish picks Past in Flames at sub-lethal storm
* Goryo's Persist → Inquisition
* Redundant-static-effect penalty for AI

These are all SB-plan and decision-quality improvements.  None
of them re-tunes the holdback coefficient or rolls back the
Mox Opal change.  Storm vs Affinity moved from 0% → 10%; other
matchups were ±5pp.  The 88% flat WR persisted.

To restore Affinity to ~55%, the path forward is:

1. **Re-tune holdback coefficient** — not roll back commit
   `9122af7` outright, but profile the gate against control
   decks at instant-speed-answer-availability and adjust.
   This needs careful sim validation.

2. **Test Mox Opal rules-strictness vs balance** — the
   dynamic-metalcraft fix is rules-correct but materially
   strengthens Affinity.  Possible mitigations:
     * Tighter "T1 Mox Opal" play gate for Affinity AI
       (don't drop Mox Opal turn 1 if metalcraft can't
       activate next turn)
     * Or: leave Mox Opal alone, push the holdback fix
       separately

3. **Bundle 3 holdback per-archetype tuning** — the issue is
   profile-level, not card-level.  Re-tune `holdback_applies`
   / `holdback_coefficient` per archetype so control decks
   hold up answers.

## Recommended next session

* PR3d: holdback coefficient re-tuning, gated on Affinity
  flat WR returning to 60-70% range without regressing T1
  decks more than 5pp.

## Cross-references

* Origin of regression: `git show 7dad56c`
* Bundle 3 commit: `9122af7`
* Mox Opal fix: `434ae15`
* This session's PR: #203
