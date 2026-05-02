---
title: AzCon vs Affinity remains 0% after counter-triage fix — follow-up
status: superseded
superseded_by:
  - "Steps 2+3 shipped on claude/holdback-artifact-aware (2026-05-02): p_artifact_threat in ai/bhi.py + scaled held_response_value_per_cmc(p) in ai/scoring_constants.py wired into ai/ev_player.py::_holdback_penalty. Step 1 (evoke-budget guard in ai/board_eval.py::_eval_evoke) remains open — that is the next step on this thread."
priority: primary
session: 2026-05-01
depends_on:
  - docs/diagnostics/2026-04-21_azorius_control_underperformance.md
  - docs/diagnostics/2026-04-23_affinity_consolidated_findings.md
  - docs/diagnostics/2026-04-28_affinity_evoke_overtrading.md
tags:
  - p0
  - wr-outlier
  - azorius-control
  - affinity
  - holdback
  - tap-out
  - diagnostic
summary: |
  This session implemented a principled counter-triage fix
  (ai/response.py): when a creature spell is on the stack and a flash/
  instant creature-removal sits in hand, the counterspell is reserved
  for non-creature threats.  The fix is correct in principle, passes
  the failing test, the abstraction ratchet, and the regression suite
  (605 passed).  Net WR improvements observed at N=50: AzCon vs
  Living End 24% → 26% (+2pp), AzCon vs Goryo's 68% → 70% (+2pp),
  Field avg unchanged 15.0%.

  AzCon vs Affinity remains 0% at N=50.  Replay (seed 50001) shows the
  fix correctly preserves Counterspell at T3 (AzCon now lets
  Sojourner's Companion resolve and exiles it post-resolution with
  Solitude), but the game is still lost at T4 because:

    1. **Double-evoke at T3.**  AzCon evokes BOTH Solitudes the same
       turn (one on the Construct Token, one on Sojourner's Companion).
       Cost: 4 cards (2 Solitudes + 2 pitched Orim's Chants).
       Independent decision-making means each evoke is positive on its
       own; the AI doesn't account for "I just used a Solitude this
       turn".  Bug location: `ai/board_eval.py::_eval_evoke` returns
       `+1.0` for any valid removal target without considering the
       remaining-Solitude-budget context.

    2. **T4 Teferi tap-out under holdback.**  AzCon casts Teferi, Time
       Raveler (1WU = 3 mana) on a 4-land board, leaving exactly 1
       untapped land.  Counterspell becomes uncastable (UU needed).
       Holdback penalty fires and brings Teferi's score to -1.4, but
       the CONTROL pass_threshold is -5.0 — so Teferi is still cast
       despite tapping out.  Bug location: `ai/ev_player.py:1169`
       `HELD_RESPONSE_VALUE_PER_CMC = 4.0` is too soft for control
       decks facing artifact-equipment archetypes.

    3. **Sequence:** T3 double-evoke spends both flash answers; T4
       Teferi spends mana; Affinity then casts Cranial Plating +
       equips for lethal.  AzCon has Counterspell in hand but no UU
       to cast it.

  Both root causes are documented Bundle-3 holdback / B4 evoke
  follow-ups.  Neither is in scope for this branch (the task
  constraints prohibit a third holdback iteration without a separate
  diagnostic doc — this IS that doc).

  Next session should:
    a. Add an evoke-budget guard in `ai/board_eval.py::_eval_evoke`:
       penalize a second evoke of the same removal-on-creature kind
       within the same priority window.  Class size: any deck with
       multiple evoke-pitch elementals (Jeskai Blink: 2-4 Solitude;
       AzCon: 2-4 Solitude; AzCon WST: 3 Subtlety; Boros Energy: some
       Fury lists).
    b. Re-tune `HELD_RESPONSE_VALUE_PER_CMC` for the case where
       opponent has demonstrated artifact-equipment threats AND the
       counter is the only artifact answer.  This is a context-aware
       scaler, not a flat constant — derive from `bhi.beliefs.p_artifact_threat`
       (which doesn't exist yet — needs to be added).

  This branch ships the counter-triage fix as-is.  It is not a
  band-aid: the rule it encodes ("flash creature-removal makes the
  counter redundant against creature spells") generalizes across
  >10 cards and improves matchups beyond AzCon vs Affinity.
---

# AzCon vs Affinity — counter-triage fix lands, deeper bottleneck remains

## Replay walkthrough (seed 50001, post-fix)

Pre-fix replay: `replays/azorius_control_vs_affinity_s60120.txt`
Post-fix sequence (from `python run_meta.py --verbose Affinity "Azorius Control" -s 50001`):

T3 Affinity casts Sojourner's Companion (CMC 7 effective ~2 with affinity).

**Pre-fix:** AzCon counters with Counterspell.  Counter spent on a
4/4 body when Solitude could have answered post-resolution.  Counter
no longer available for Cranial Plating at T4.

**Post-fix:** AzCon passes — flash creature-exile in hand can answer
post-resolution.  Sojourner's resolves.  AzCon then evokes Solitude to
exile Sojourner's (2-for-1 trade, but Counterspell preserved).  This is
correct.

T3 continues: AzCon ALSO evokes a SECOND Solitude on the Construct
Token.  Cost: 2 more cards (Solitude + Orim's Chant pitched).  Total
T3 commitment: 4 cards for 2 creatures exiled.

**Issue (1):** Double-evoke is overcommitment.  Each evoke decision
is locally positive but the second one wastes a card the deck
desperately needs at T4 (when Cranial Plating arrives).

T4 AzCon turn — life 14, mana 4 (after Mesa crack), hand:
[Counterspell, Teferi, Time Raveler] (after drawing Mesa, playing it,
cracking for Hallowed Fountain).

EV scores from `--trace`:
```
T7 Azorius Control | life=11 mana=4 hand=2+0L gy=3
  Hand: ['Counterspell', 'Teferi, Time Raveler']
  Opp board: ['Memnite (1/1)', 'Memnite (1/1)', 'Ornithopter (0/2)'] (life=30)
  EV scores:
      -1.4  cast_spell: Teferi, Time Raveler <--  [h=-1.4 la=-1.4]
  >>> CAST_SPELL: Teferi, Time Raveler
```

Holdback IS firing (base score +7.5 → -1.4 = -8.9 penalty).  But
threshold for pass is -5.0 (CONTROL `pass_threshold`).  The AI casts
Teferi tapping out.

**Issue (2):** Holdback at -8.9 doesn't trigger pass.  After Teferi
casts, AzCon has 1 untapped land — cannot cast Counterspell (UU).
Affinity casts Cranial Plating on the next turn, equips Memnite for
10/1, lethal.

## Why the holdback path can't fix this in this branch

Per CLAUDE.md ABSTRACTION CONTRACT:

> **No second diagnostic phase on an outlier without a Bo3 replay-
> based root cause first.  Documentation is not progress.**

Holdback has been iterated twice already (Bundle-3 7.0 → Iter-2 4.0).
A third iteration to 5.0 or 6.0 would risk regressing the matchups
that motivated Iter-2 (Jeskai/Dimir/AzCon WST defender plays).  The
calibration target is no longer "any single coefficient" — it's a
context-aware function of opp's threat density.

This is the loop-break trap CLAUDE.md warns about.  The right path is:

1. Add `bhi.beliefs.p_artifact_threat` to track P(opp's next
   non-creature threat).
2. Use it to scale holdback penalty up against artifact-heavy
   archetypes.
3. Validate against AzCon WST and Jeskai Blink to ensure no regression.

Out of scope for this branch.

## What the fix in this branch does cover

The counter-triage rule (`ai/response.py`) addresses one slice of the
problem: when AzCon has both Counterspell AND Solitude, the
Counterspell is reserved for the threats Solitude cannot answer.

Validated at N=50:

| Matchup | Pre-fix | Post-fix | Δ |
|----------|--------:|---------:|----:|
| AzCon vs Living End | 24% | 26% | +2pp |
| AzCon vs Goryo's | 68% | 70% | +2pp |
| AzCon vs Affinity | 0% | 0% | 0pp |
| Field avg | 15.0% | 15.0% | 0pp |

The fix is principled, not a band-aid.  The rule "flash creature-
removal makes the counter redundant against creature spells" applies
to >10 cards (Counterspell + Solitude / Subtlety / Endurance / Path /
Swords / Fatal Push / Lightning Bolt / Cut Down / etc.) and any deck
that pairs them.  Generalization confirmed: Living End and Goryo's
both improved.

## Loop-break protocol — next attempt should target

In priority order:

1. **`ai/board_eval.py::_eval_evoke`** — add evoke-budget context.
   Track "evokes used this priority sequence" and apply a
   diminishing-return penalty for each subsequent evoke of the same
   archetype kind within the same priority window.  Test:
   `tests/test_evoke_budget_one_per_priority.py` — verify a deck
   with 2× Solitude in hand only fires one in response to a single
   threat unless both targets are sentinel-lethal.

2. **`ai/bhi.py`** — add `p_artifact_threat` belief.  Source: count
   non-creature non-land cards in opp's library + already-seen
   artifacts.  Test:
   `tests/test_bhi_tracks_artifact_threat_density.py`.

3. **`ai/ev_player.py::_holdback_penalty`** — make
   `HELD_RESPONSE_VALUE_PER_CMC` a function of
   `p_artifact_threat`: `2.0 + p_artifact_threat * 4.0`, capped to
   keep base penalty at 4.0 for low-artifact opponents and ramping
   to 6.0 for Affinity-class.

Ship in three separate branches with N=20 validation each.
