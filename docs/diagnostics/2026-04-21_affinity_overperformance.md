---
title: Affinity 87% overperformance — EV divergence diagnostic (ORIGINAL, superseded)
status: superseded
priority: historical
session: 2026-04-21
falsified_on: 2026-04-23
superseded_by:
  - docs/diagnostics/2026-04-23_affinity_mana_holdback_bug.md
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
tags:
  - p0
  - wr-outlier
  - affinity
  - diagnostic
  - phase-12
  - hypothesis-falsified
summary: "FALSIFIED + ROOT-CAUSED 2026-04-23. Original hypothesis (response-gate undervalues creature-with-carrier-pool synergy) was falsified by unit test. True root cause found via decide_response instrumentation: AzCon has only 1 untapped land at opp's priority window because it taps out on its own turn (cycling Lórien Revealed at T2, casting Isochron Scepter at T3). Counterspell requires UU — uncastable, filtered by can_cast. Bug is mana holdback for control decks with counter in hand, not response-gate scoring. See 2026-04-23_affinity_mana_holdback_bug.md."
---

> **STATUS — SUPERSEDED 2026-04-23.** Initial hypothesis (response-gate
> scoring) was falsified. The real bug IS mana-management — AzCon taps
> out on its own turn and has no mana open for opponent's priority
> window, even though the opponent's threat is scored correctly.
> See `2026-04-23_affinity_mana_holdback_bug.md` for the corrected
> diagnostic. The rest of this document is preserved for historical
> context only.


# P0 WR outlier diagnostic — Affinity 87% flat

## Headline

Affinity games aren't won by Affinity doing something exotic. They're
won by opponents **not casting available counters/removal** against
the pieces that matter. The first EV divergence is consistently on
the defender side, at a priority pass where a counterspell or single
removal would swing the game.

## Evidence (seed 60102 — Affinity vs Azorius Control, G1)

Replay: `replays/affinity_vs_azorius_control_s60102.txt`
HTML: `replays/replay_affinity_vs_azorius_control_s60102.html`

Azorius opening hand (line 24-32):
```
• Consult the Star Charts, Teferi Hero of Dominaria, Lórien Revealed,
• Arid Mesa, Counterspell, Meticulous Archive, Steam Vents
→ P2 KEEPS 7 — has key card(s): Counterspell, 2 cheap spells
```

Critical turn — T3 Affinity (line 157-166):
```
T3 P1: Cast Sojourner's Companion (7)        ← CMC-7 artifact creature, cost-reduced
    [Priority] P2 passes (no response)       ← Azorius has UU untapped + Counterspell
T3: Resolve Sojourner's Companion
T3 P1: Cast Cranial Plating (2)
    [Priority] P2 passes (no response)       ← Azorius still has UU + Counterspell
T3: Resolve Cranial Plating
T3 P1: Cranial Plating enters the battlefield (unattached)
```

One turn later, Affinity attacks for 24 (Sojourner equipped with two
Platings, 18 artifacts on board → 4 + 2×9 = 22 power on Sojourner
alone) and wins T4.

**First divergence point:** the priority pass at line 158. Azorius
has Counterspell in hand, Island + Hallowed Fountain untapped (UU
available), and the incoming spell is a CMC-7 attacker that lethal
with any equipment. `decide_response()` returned "no response".

## Evidence (seed 60100 — Affinity vs Boros, G1)

Replay: `replays/affinity_vs_boros_energy_s60100.txt`

T3 Affinity curve-out (line 172-196):
```
T3 P1: Cast Thought Monitor (6U)            ← cost-reduced via artifact count
T3 P1: Cast Springleaf Drum (1)
T3 P1: Cast Sojourner's Companion (7)       ← cost-reduced to 0-1 mana
T3 P1: Cast Cranial Plating (2)
T3 P1: Equip Cranial Plating to Signal Pest (cost 1)
    Signal Pest (8/1)                        ← 8-power flyer attacker
T3 P2: [BLOCK] Ajani Nacatl Pariah blocks Signal Pest (8/1) — trade (chump)
```

Boros's Thraben Charm is cast only in main 2 (line 224) after combat,
so it doesn't prevent the 8-power Plating swing. With multiple
artifact creatures entering T3, Thraben Charm had ≥3 valid "destroy
target artifact" targets and could have been cast in Affinity's main
1 to break up the equip — it wasn't.

## Evidence (seed 60101 — Affinity vs Dimir, G2)

Affinity wins 2-1; the only loss is G1 where Dimir's Orcish Bowmasters
pings the initial Memnite/Ornithopter. In G2 and G3, Dimir passes
priority through the key Plating equip turns.

## Diagnosis — AI layer, response side

The response-layer EV gate is built around the `opp_power` and
`turns_to_lethal` signals from `ai/clock.py`. Affinity's cheap
artifacts (Ornithopter, Memnite, Signal Pest, Frogmite) read as
low-threat individually: ≤2 power, short clock on their own. The
Plating equip turn is where the threat materialises — but by that
point, the carrier is already resolved and the Plating itself
resolves unopposed.

The structural gap: `decide_response()` scores **the spell being
cast** in isolation, not the board state it produces in combination
with the carrier already in play. A CMC-2 Plating targeting a
battlefield with 5 artifact creatures is a different response
problem than a CMC-2 Plating on an empty board.

## Candidate fix location

Not a fix proposal — diagnostic only. Likely loci for the next
session:

1. `ai/response.py` — response gate for instant-speed interaction.
   Need a term that accounts for **combined threat after resolution**:
   if the opponent's battlefield already has N artifact creatures,
   an incoming Plating's marginal power is proportional to N, not
   to its own CMC.
2. `ai/ev_evaluator.py` — `creature_threat_value()` for Plating-like
   equipment should factor the carrier pool, not just the equipment's
   intrinsic stats.
3. Possibly `ai/bhi.py` — Bayesian hand inference for Affinity: after
   seeing T1-T2 zero-cost artifacts, the opponent model should
   raise `p(Plating_in_hand)` and gate more tightly at the response
   window.

## Relation to AzCon diagnostic

This is the mirror: Affinity overperforms because opponents fail to
interact. The largest single contributor is Azorius Control (see
`2026-04-21_azorius_control_underperformance.md`). Fixing the
response-gate threshold is likely a joint fix for both outliers.

## Non-negotiables

- No hardcoded card names.
- Test-first (Option C) before any change.
- Per-deck N=50 matrix validation before merging any fix.
