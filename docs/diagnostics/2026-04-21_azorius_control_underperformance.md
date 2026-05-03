---
title: Azorius Control 15% underperformance — EV divergence diagnostic
status: superseded
priority: primary
session: 2026-04-21
superseded_by: docs/diagnostics/2026-05-01_azcon_followup.md
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
  - docs/diagnostics/2026-04-21_affinity_overperformance.md
tags:
  - p0
  - wr-outlier
  - azorius-control
  - diagnostic
  - mulligan
  - phase-12
summary: "AzCon posts 14.8% flat / 11.7% weighted at N=50. Two replay seeds (60102 Affinity vs AzCon, 60120 AzCon vs Affinity) show two distinct divergences: (1) rigid mulligan heuristic — mulls a Counterspell/Teferi/Solitude anti-Affinity hand for 'too few lands (1<2)', settles on a 5-card 6-land Teferi-only hand; (2) the same response-gate issue flagged in the Affinity doc — active Counterspell + UU untapped + CMC-7 incoming threat = pass with no response. Both are AI-layer problems; the decklist is fine."
---

# P0 WR outlier diagnostic — Azorius Control 15% flat / 11.7% weighted

## Headline

AzCon has the cards to stop Affinity (Counterspell × 4, Wrath of
the Skies × 2, Solitude × 4, Teferi + Supreme Verdict) but loses
the matchup at two distinct decision points:

1. **Mulligan rejects anti-matchup hands** for land-count reasons;
2. **Response gate never fires** when interaction is live and mana
   is open.

## Evidence (seed 60120 — Azorius Control vs Affinity, G1)

Replay: `replays/azorius_control_vs_affinity_s60120.txt`
HTML: `replays/replay_azorius_control_vs_affinity_s60120.html`

Mulligan log (line 22-28):
```
Opening hand 7:
  • Solitude, Teferi Hero of Dominaria, Prismatic Ending, Prismatic
    Ending, Teferi Time Raveler, Supreme Verdict, Consult the Star
    Charts       ← 0 lands
→ P1 MULLIGANS (0 lands)                       ← correct

Mulligan 6:
  • Teferi Time Raveler, Supreme Verdict, Flooded Strand, Consult
    the Star Charts, Counterspell, Teferi Time Raveler, Solitude
→ P1 MULLIGANS (too few lands (1 < 2))         ← WRONG
```

The mulligan-6 hand is actually excellent vs Affinity:
- 1 fetchable land (Flooded Strand) + cantrip (Consult Star Charts)
  functions as a 2-land hand after T1 cantrip.
- Full anti-matchup suite: Supreme Verdict, 2× Teferi, Solitude,
  Counterspell.

The rigid check `mulligan_min_lands == 2` in the gameplan JSON
(`decks/gameplans/azorius_control*.json`) doesn't differentiate
cantrips from dead cards. Mulling this hand ships a dramatically
worse 5-card hand (6 lands + Teferi TR only, `Keeps: Plains, Steam
Vents, Plains, Teferi Time Raveler, Monumental Henge`) that has
**zero interaction** vs the Affinity curve-out.

Result: AzCon loses G1 T5 because the mulliganed hand is
mana-flood with no responses.

## Evidence (seed 60102 — Affinity vs Azorius Control, G1)

Same response-gate pattern as the Affinity diagnostic. Replay:
`replays/affinity_vs_azorius_control_s60102.txt` line 157-165:
```
T3 P1 (Affinity): Cast Sojourner's Companion (7)    ← CMC 7 threat
    [Priority] P2 passes (no response)              ← UU + Counterspell in hand
T3: Resolve Sojourner's Companion
T3 P1: Cast Cranial Plating (2)
    [Priority] P2 passes (no response)              ← UU + Counterspell still in hand
T3: Resolve Cranial Plating
```

Hand at that moment includes Counterspell (opening hand, line 29);
available mana includes Island + Hallowed Fountain both untapped
(UU); incoming spell is a 4/4 with equipment incoming. Response
gate returned "pass" three consecutive times during Affinity's main
phase on T3 and T4.

## Diagnosis — two distinct layers

### (A) Mulligan layer — `ai/mulligan.py` + gameplan JSON

The `mulligan_min_lands` constant is a rigid inequality. It does
not:
- Count cantrips / fetchlands as "virtual lands".
- Check whether remaining hand content is matchup-appropriate
  (Counterspell + Solitude is a *keep* vs Affinity, not a mull).
- Respect the "at 6 cards, relax by 1" softening already used by
  other archetypes.

The earlier `tests/test_mull_keeps_anti_matchup_hand.py` (commit
`06ebd33`) targeted this class of bug but for different archetypes;
AzCon was not covered.

### (B) Response layer — `ai/response.py` + `ai/ev_evaluator.py`

Same root cause as the Affinity overperformance diagnostic. The
response gate scores incoming spells individually, without factoring
the **carrier pool already on the battlefield**. For Affinity, every
individual spell looks cheap; the lethal threat only materialises
after the equip chain, which is past the priority window.

## Candidate fix locations

Not fix proposals — diagnostic only.

- **Mulligan:** `ai/mulligan.py` scoring for "at least one source
  of interaction" vs the matchup archetype, weighting cantrips as
  1-virtual-land on 7- and 6-card hands.
- **Response:** shared fix with Affinity diagnostic — see
  `2026-04-21_affinity_overperformance.md` §"Candidate fix location".

## Relation to Affinity diagnostic

Fixing the response-gate threshold should pull AzCon's matchup
toward 40-50% vs Affinity (current WR implies the deck isn't
casting its interaction at all). Combined with a mulligan tweak,
AzCon should reach mid-30s flat — not back to competitive but no
longer a structural outlier.

## Non-negotiables

- Option C: failing test before any change.
- No hardcoded card names.
- N=50 matrix validation before merge.
