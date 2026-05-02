---
title: Affinity 88.1% Overall WR — Diagnostic & Hypothesis List
status: active
priority: primary
session: 2026-05-02
depends_on:
  - docs/design/2026-05-02_unified_target_solver.md
tags:
  - affinity
  - diagnostic
  - p0-outlier
  - threat-evaluation
  - ai-blocking
  - ai-removal-targeting
summary: "Affinity sits at 88.1% overall WR on current main (vs expected 45-60%). Removal-target validation bug ruled out as cause (see PR #?? for fix; no WR impact). Single-mechanic checks (Construct token P/T, Cranial Plating equipment scaling, Affinity cost reduction, Mox Opal metalcraft) all verify mathematically correct. The remaining 30+pp gap is AI-interaction, not mechanic-level. This doc enumerates the falsified-and-untested hypotheses for next-session investigation in Claude Code."
---

# Affinity 88.1% Overall WR — Diagnostic & Hypothesis List

## Problem statement

Current main (post all merges through PR #221) shows Affinity at
**88.1% overall WR** in `--field "Affinity" -n 20 -s 40000`.
Expected band based on real Modern T1 metagame data: 45-60%.
Gap: ~30pp. This is the largest P0 outlier in the matrix and has
been the focal point for ~100 prior diagnostic iterations.

### Per-matchup WR (current main, 2026-05-02)

```
Affinity vs field — overall 88.1%
  vs Goryo's Vengeance        100%
  vs Azorius Control          100%
  vs Boros Energy              95%
  vs Ruby Storm                95%
  vs Eldrazi Tron              95%
  vs Amulet Titan              95%
  vs Azorius Control (WST v2)  95%
  vs Izzet Prowess             90%
  vs Dimir Midrange            90%
  vs 4c Omnath                 90%
  vs Pinnacle Affinity         90%
  vs Living End                85%
  vs 4/5c Control              85%
  vs Azorius Control (WST)     80%
  vs Jeskai Blink              75%
  vs Domain Zoo                50%   ← only normal matchup
```

The Domain Zoo result is the diagnostic anchor: when the opponent
runs efficient creatures + 1-mana burn (Lightning Bolt, Bonecrusher
Giant) and a faster clock, the matchup normalizes. Slow decks are
all at 80%+. **The signal is a clock+answers gap, not a math gap.**

## Falsified hypotheses (do not re-investigate)

### H_FAL_1: Removal spells castable on empty boards (CR 601.2c)

**Falsified 2026-05-02.** A real bug — confirmed via direct repro
that 250+ DB cards (Wear // Tear, Disenchant, Vindicate, Maelstrom
Pulse, Galvanic Discharge, etc.) are castable with no legal
battlefield target. Patch landed on
`fix/cast-time-target-permanent-artifact-validation` with 15 new
tests. **Empirical: 0 WR impact across 8-matchup spot-check (n=30
each, same seed).** AI scoring layer (`ai/ev_evaluator.score_spell`)
already pre-filters target-less casts. Patch is correctness
improvement only; not the Affinity WR mover.

### H_FAL_2: Construct token P/T scaling

**Falsified 2026-05-02.** Direct test:

```python
setup 9 artifacts on board; create_token "construct"
→ Construct Token .power=9, .toughness=9
```

Symmetric +N/+N scaling, includes self-count, matches CR. The 17/8
trace observed in `replays/affinity_vs_boros_energy_s60100`
unpacks correctly: 8 artifacts × 2 (own scaling + Plating equipment
scaling) + 1 (Signal Pest battle cry) = 17 power, 8 toughness.
This is real Modern Affinity output and matches paper Magic.

### H_FAL_3: Cranial Plating equipment scaling asymmetry

**Falsified 2026-05-02.** Plating is "+1/+0 for each artifact you
control". `_dynamic_base_power` adds artifact_count to power; the
toughness path correctly parses the `/+M` token and only adds when
M ≠ 0. Direct test: Memnite + Plating with 10 artifacts → 11/1
(expected 11/1). No off-by-one in the equipment dispatch.

### H_FAL_4: Affinity-keyword cost reduction stacking

**Falsified 2026-05-02.** Direct test confirms Frogmite (CMC 4)
casts for 0 with 4+ artifacts; Thought Monitor (6U) casts for U
with 5+ artifacts. Affinity reduces only the generic portion.
Matches CR 702.40. The T4 trace where Affinity casts Thought
Monitor + Frogmite + Mox Opal + Signal Pest in one turn is real
Modern Affinity output, not a bug.

### H_FAL_5: Mox Opal metalcraft over-activation

**Falsified 2026-05-02.** Mox Opal oracle: "Activate only if you
control three or more artifacts." DB shows 0 cost (correct). Mox
Opal cast on T4 when board already has 4+ artifacts is real
metalcraft. No early-activation bug.

## Active hypotheses (untested, ordered by suspected impact)

### H_ACT_1: Opponent removal-targeting prioritization (HIGHEST)

**Hypothesis.** When facing Affinity, the opponent's AI selects
targets for removal sub-optimally. Concretely: Galvanic Discharge
killing Memnite (1/1, no synergy) on T1 instead of holding for
Thought Monitor (T4 4/4 with draw 2), or Wrath of the Skies on a
3-artifact board instead of waiting for the 6-artifact post-Saga
state.

**Diagnostic.** Trace `replays/affinity_vs_boros_energy_s60100.html`
shows Boros casting Thraben Charm on Saga (T3) but the resulting
board still has Construct Token + 4 artifact creatures. Boros's
T2 was a land drop only — no proactive disruption.

**Concrete next-session steps.**
1. Run `--bo3 "Boros Energy" "Affinity" -s 60100..60110` (10 seeds)
   with `--trace` enabled, capture P2's hand at start of each turn,
   and check whether Boros HAD removal in hand it didn't cast.
2. Inspect `ai/ev_evaluator.score_spell` for the Affinity-vs-X
   removal scoring path. Specifically: does the score consider
   *future* board state (Affinity will have N artifacts in 1 turn)
   or only *current* state?
3. Check `ai/discard_advisor` for whether opponents discard
   removal that would have been useful against Affinity later.

**Class-of-bug shape.** If found, the fix likely lives in
`ai/ev_evaluator.score_spell` and is oracle-driven (no card-name
conditionals). Removal-decks across the meta would benefit, not
just vs Affinity.

### H_ACT_2: Opponent block decisions vs evasive Affinity attackers

**Hypothesis.** Signal Pest (battle cry, can't be blocked except
by flying/reach) and Construct Token (huge, equipped to Memnite via
Plating) overwhelm the opponent's blocking math. The AI either:
- Chump-blocks the wrong creature (e.g., trades a 4/4 Phlage with
  Signal Pest instead of letting Pest through and saving Phlage
  for Construct), or
- Doesn't block at all because trading is "negative EV" but the
  alternative (taking 17 to face) is worse.

**Diagnostic.** Trace S60100 G1 T5: Affinity attacks with 4
creatures including Signal Pest and Construct Token (10/9). Boros
blocks with Ocelot Pride (1/1) chump-blocking Construct (good!) but
takes 12 to face from the rest. Boros's life total drops to single
digits in 2 turns. Did Boros consider the no-block-take-everything
line vs the chump line correctly?

**Concrete next-session steps.**
1. Capture `assign_blockers` decisions across 20 Bo3 games. For
   each, log the chosen block + the "no block, take damage" line's
   damage. Manual inspection: are chump blocks defensible (delayed
   lethal) or wasted?
2. Inspect `ai/blocker_assigner.py` (or wherever the block AI
   lives) — does it consider the lethal-clock differential when
   deciding to chump?
3. Cross-check: does the AI know Signal Pest's "can't be blocked
   except by flying/reach" oracle? If it tries to block Signal
   Pest with non-flying creatures, that's a wasted defender.

### H_ACT_3: Sideboard package strength vs Affinity

**Hypothesis.** Boros sideboards in `+2 Wear // Tear, -2 Undying
Evil` (per trace's `[Sideboard]` log). That's only 2 dedicated
artifact-removal cards across 75. Real Modern Boros vs Affinity
sideboards bring 3-4 hate pieces (Stony Silence, Hidetsugu's Spite,
Wear // Tear). The current sim sideboard plan is under-tuned for
the Affinity matchup.

**Diagnostic.** Compare sideboard plans across Boros / Azorius /
Dimir for the Affinity matchup:

```python
# decks/modern_meta.py:
Boros sideboard: Blood Moon×1, Celestial Purge×1, Damping Sphere×1,
                 High Noon×1, Obsidian Charmaw×2, Orim's Chant×2,
                 Surgical Extraction×1, The Legend of Roku×1,
                 Vexing Bauble×1, Wear // Tear×2, Wrath of the
                 Skies×2  (15 cards)
```

Wrath of the Skies is a sweeper that scales with non-creature
permanents — should be excellent vs Affinity. Is the sideboard AI
bringing it in?

**Concrete next-session steps.**
1. Inspect `engine/sideboard_manager.py` — count the actual
   side-in/side-out for each top-3 anti-Affinity card. Is Wrath
   of the Skies coming in?
2. If yes — measure WR with manually forced sideboard
   (`--matchup "Boros Energy" "Affinity" -n 30 --force-sb
   "Wrath of the Skies×2,Damping Sphere×1,Wear // Tear×2"` if such
   a flag exists; otherwise temporarily edit the SB).
3. If WR moves +20pp from sideboard tuning, the bug is in the SB
   AI, not the gameplay AI.

### H_ACT_4: Affinity discount applies to alternative-cost spells

**Hypothesis.** Affinity for artifacts reduces the *generic* mana
of casts. But Living End (suspend/cascade), Thought Monitor (delve-
adjacent), and other spells with alternative costs may also be
reducing in ways that compound. Need to verify the cost-reduction
order: affinity → delve → improvise vs delve → affinity → improvise.

**Diagnostic.** This was hinted at in `1f310cd fix(ai): board_eval
fallback uses player_idx (not me.index)` but is a different bug
class. May require dedicated inspection.

**Concrete next-session steps.**
1. Find every `mana_cost.cmc - reduction` site in `engine/
   cast_manager.py` and `ai/mana_planner.py`.
2. Verify the order of operations: keyword reductions (affinity)
   should apply BEFORE alternative-cost flips (delve, escape,
   evoke), not after. CR 601.2f is specific.
3. Unit-test: Frogmite (4-cost affinity for artifacts) with 3
   artifacts + 2 cards in graveyard delve — what's the effective
   cost? Should be 4-3=1 generic, then delve covers 1 → 0. Not
   4-3-2 = -1 → 0 (which would also work but is wrong order).

### H_ACT_5: Saga timing — token created before Ch.III sacrifice

**Hypothesis.** Urza's Saga Ch.III says "tutor an artifact card
into play, sacrifice this Saga." If the engine processes the
sacrifice AFTER the tutor + token creation, the artifact-count
swings briefly (Saga + tutored card both exist as artifacts when
the Construct token's P/T is computed). Could inflate the
Construct power by 1-2 vs a strict CR-correct order.

**Diagnostic.** Reread `engine/game_runner.py:1325-1363` (Saga
Ch.III handler). It creates a Construct token, then tutors a
card, then `sagas_to_sacrifice.append(card)`. The sacrifice
appears to be deferred — but is the token's P/T snapshotted at
creation, or computed dynamically every read?

The Construct's P/T is `_dynamic_base_power`, computed every
read. So at the moment of attack, the artifact count includes
whatever's on the battlefield then — which is correct unless the
SAGA itself is still on the battlefield (it shouldn't be after
Ch.III).

**Concrete next-session steps.**
1. Verify `sagas_to_sacrifice` is processed in the same priority
   pass before any combat or P/T evaluation.
2. Ensure the Saga is moved to the graveyard *before* the
   Construct's P/T is evaluated for combat.

### H_ACT_6: Battle cry stacking with multiple Signal Pests

**Hypothesis.** 2× Signal Pest both attacking, both trigger
battle cry. Each "other attacking creature gets +1/+0". Real
Magic: each Pest gives +1/+0 to OTHER attackers (the second Pest
does NOT trigger a self-buff). So 2 Pests + Construct attacking
= Construct gets +2/+0 (from each Pest), each Pest gets +1/+0
(from the OTHER Pest only). Verify the engine respects "other".

**Diagnostic.** Trace S60100 G2 T5: "Construct Token (17/8) → 17
dmg to player". With 2 Signal Pests attacking and 8 artifacts:
- Construct base = 0 + 8 = 8/8
- Plating equipped = +8/+0 → 16/8
- Battle cry from 2 Pests = +2/+0 → 18/8

But trace shows 17/8. So 1 battle cry, not 2. Either only 1 Pest
attacked, or the engine correctly disallows self-stacking. **The
trace suggests this is currently CORRECT** (off-by-one in our
favor). H_ACT_6 is likely a non-issue but flagged for completeness.

### H_ACT_7: Mulligan: opponent keeps too many slow hands vs Affinity

**Hypothesis.** Boros's mulligan logic doesn't prioritize fast
removal in the matchup-aware mulligan plan. Boros keeps a hand
with Phlage (4-cost) and Ajani (3-cost) but no T1 disruption,
gets run over by Affinity's T4 lethal.

**Diagnostic.** Trace S60100 G1 Boros opening: "Spire of Industry,
Signal Pest, Memnite, Sojourner's Companion, Frogmite, Cranial
Plating, ?". Wait — that's the AFFINITY hand. Boros's hand isn't
explicitly shown. Need a different trace flag.

**Concrete next-session steps.**
1. Run `--trace "Boros Energy" "Affinity" -s 60100` and grep
   Boros's opening hand. Are mulligans aggressive enough?
2. Check if there's a per-matchup `mulligan_anti_matchup` plan in
   `decks/gameplans/boros_energy.json`. If present, does it
   prioritize Galvanic Discharge / Lightning Helix-style answers?

## Recommended next-session investigation order

1. **H_ACT_3 (sideboard)** — fastest to verify, biggest single
   leverage if found. ~30 min: dump SB transformations across 10
   matchups, check if Wrath of the Skies / Damping Sphere are
   coming in.
2. **H_ACT_1 (removal targeting)** — most likely root cause of
   the AI gap. ~60-90 min: trace 10 Bo3 games, log every removal
   spell cast and target choice, find missed-target patterns.
3. **H_ACT_2 (block decisions)** — second most likely. ~60 min:
   capture every block decision in 5 Bo3 games, check chump-block
   correctness.
4. **H_ACT_4 (cost-reduction order)** — unit-testable in
   isolation, ~45 min if a bug is found.
5. **H_ACT_5 (Saga timing)** — likely fine, ~20 min to confirm.
6. **H_ACT_6 (battle cry)** — likely fine, ~15 min to confirm.
7. **H_ACT_7 (opponent mulligan)** — covered partially by previous
   mulligan typed-paths fix; ~30 min to verify Boros's
   anti-Affinity plan exists.

Total estimated investigation budget: 4-5 hours of focused work in
Claude Code (full machine, parallel matrix runs, iterative test
feedback). After investigation, expect 1-2 fix PRs in the same
session, plus matrix re-baseline.

## What success looks like

- Affinity overall WR moves from 88.1% to 60-70% (real Modern
  T1 band).
- Boros Energy / Azorius Control / Dimir Midrange WRs each move
  +5 to +10pp.
- 8-matchup spot-check at same seed shows quantitative WR shifts
  (not the 0pp seen for the removal-target patch).
- New tests-first regression suite added — at minimum 1 test per
  hypothesis confirmed-as-bug.
- Findings doc updated with `status: superseded` and
  `superseded_by` references to the resulting experiment logs
  per project doc-hygiene rules.

## What does NOT look like success

- Adding card-name conditionals to fix specific Affinity threats.
  The abstraction contract forbids this; if the fix shape needs
  card-name special-casing, the diagnosis is wrong.
- Patching specific Affinity matchups one at a time. The bug is
  AI behavior generalizable across decks — fix it in `ai/` for
  the whole field.
- Lowering Affinity's mainboard threat count. The decklist is a
  real tournament list; the bug is opponents not coping with it,
  not Affinity over-tuning.

## Linked work

- **PR (this session):** `fix/cast-time-target-permanent-artifact-validation`
  — tactical patch closing the 250+ card removal-target validation
  bug class. No WR impact but correctness-improving and prerequisite
  for the unified target_solver refactor.
- **Design doc:** `docs/design/2026-05-02_unified_target_solver.md` —
  unified target validation refactor (1 working session in Claude
  Code). Independent of Affinity diagnosis but should land first
  because it removes a confounding variable from any future
  cast-time evidence.
- **Past Affinity work:** `docs/history/sessions/2026-04-19_affinity_investigation.md`
  (depended-on by the EV correctness overhaul). Phase 7 fixed
  Pinnacle Affinity scoring; this work is for *standard* Affinity.
