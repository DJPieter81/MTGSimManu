---
title: Amulet Titan 29.8% field — payoff-blind Scapeshift/tutor scoring is the primary sink; haste line unreachable is secondary
status: active
priority: primary
session: 2026-08-26
supersedes: docs/diagnostics/2026-07-06_amulet_titan_root_cause.md
depends_on: docs/diagnostics/2026-07-06_amulet_titan_root_cause.md
tags: [amulet, scapeshift, tutor, titan, haste, conversion, diagnosis]
summary: >
  Bo3 replay re-diagnosis (6 matches, 17 valid games, 12 losses walked;
  1 timeout game discarded). Primary subsystem: ai/ev_player.py's
  payoff-blind tutor scoring — the land-sacrifice tutor (Scapeshift
  shape) is gated only by a fizzle check (land count >= 4) and fires
  with zero payoff on board in 6 of 12 losses, halving the deck's own
  mana base at the moment it should be deploying Titan. July claim #2
  (Titan sits home) is FALSIFIED — the combat rework fixed it. July
  claim #1 (no haste line) is confirmed but demoted to secondary:
  fetch priority + missing haste-grant activation cost exactly the two
  losses where the deck otherwise played a perfect curve.
---

# Amulet Titan re-diagnosis — 2026-08-26

## Context

Matrix row (`metagame_results.json`, timestamp 2026-08-25T21:47, Bo3,
n=8 matches/pair; rankings entry `[29.8, 'Amulet Titan', 26.3]`):
Amulet Titan sits at **29.8% flat field WR** against an expectation
band of [45–60] (real Tier 1, 4.8% meta share). Worst matchups at 0%:
Boros Energy, Domain Zoo, 4/5c Control, Pinnacle Affinity, Broodscale
Bloodchief. Best: Creatures Toolbox 100%, Azorius Control 62%.
Mid-band: Affinity, Living End, Instant Reanimator at 50%.

Since the July doc landed, the engine gained: zone funnel, continuous
effects, activation subsystem tranches 1–2, combat rework,
soft-counter/holdback fixes, **two-phase Scapeshift land entry**, saga
fixes. Every July claim was therefore re-verified from fresh replays.

## Evidence base

Six Bo3 replays, worst realistic matchup (Boros Energy, 0% row) and
one mid matchup (Affinity, 50% row), three seeds each:

- `replays/amulet_titan_vs_boros_energy_s55505.txt` — loss 1–2
- `replays/amulet_titan_vs_boros_energy_s60000.txt` — loss 0–2
- `replays/amulet_titan_vs_boros_energy_s60500.txt` — loss 1–2
- `replays/amulet_titan_vs_affinity_s55505.txt` — loss 1–2
- `replays/amulet_titan_vs_affinity_s60000.txt` — loss 1–2 (G1 ended
  "wins … via timeout" — **discarded as evidence** per protocol; the
  two clean losses in that match are used)
- `replays/amulet_titan_vs_affinity_s60500.txt` — loss 1–2

Amulet lost all six matches: 4 valid game wins in 17 valid games
(23.5%), consistent with the matrix row. All 12 losses were walked
turn by turn.

## Loss-by-loss first divergence

| Game | First divergence from the known-correct Amulet line | Owning subsystem |
|---|---|---|
| Boros s55505 G1 | Perfect curve (T2 Amulet, T4 Titan) — but Titan fetch takes Simic/Vestige, never a combat line; 6 dmg/turn from T5 loses the race **by exactly one turn** (dead T7, opp at 9) | engine `_primeval_titan_search` + missing haste activation |
| Boros s55505 G3 | T5 Scapeshift sacrifices **7 lands with no payoff** → 3 tapped karoos survive; follow-up is a second Spelunking; dead T6 | ai land-sac tutor gate |
| Boros s60000 G1 | T5 Scapeshift sacrifices 4 lands, no payoff, **Primeval Titan in hand since T1** (Pact) → board collapses 4→2 lands; Titan never cast; dead T7 | ai land-sac tutor gate |
| Boros s60000 G2 | GSZ cast at X=2, X=4, X=4 — all three find Arboreal Grazer; the deck's payoff-access is burned below Titan's CMC; no payoff ever resolves; dead T8 | ai tutor X/target selection |
| Boros s60500 G1 | Engines only (2 Amulet + Spelunking), payoff never found (Titan is a 3-of) | draw/structural |
| Boros s60500 G3 | T5 Scapeshift sacrifices **6 lands incl. both Urza's Sagas**, no payoff → 3 tapped karoos; T6 double Spelunking; dead T7 | ai land-sac tutor gate |
| Affinity s55505 G1 | T2 Amulet, T4 Titan **vs a mull-to-5 opponent** — still dead T7 (opp at 5): no haste, no second threat, 6 dmg/turn | engine `_primeval_titan_search` + missing haste activation |
| Affinity s55505 G3 | T2 Amulet then no action found; dead T6 | draw/structural |
| Affinity s60000 G2 | T4 Scapeshift sacrifices 6 lands, no payoff; T5 Spelunking; dead T6 | ai land-sac tutor gate |
| Affinity s60000 G3 | Keep 7 on "**has critical piece(s): Scapeshift, 5 lands**"; T5 Scapeshift sacrifices 5, no payoff; dead T6 | ai mulligan (critical_pieces) + land-sac gate |
| Affinity s60500 G1 | Slow game — Titan T8, Scapeshift T10; dead T11 | structural/slow |
| Affinity s60500 G3 | GSZ X=3 → Grazer (T4); GSZ X=7 → Colossus only at T6, one turn too late; dead T7 | ai tutor X/target selection |

Tally of first-divergence causes: **Scapeshift-with-no-payoff 6/12**,
payoff-blind GSZ 2/12, Titan-conversion-speed (haste line) 2/12,
draw/structural 2–3/12 (the 3-of Titan + 4-of GSZ list makes GSZ
misuse count double).

## PRIMARY subsystem: payoff-blind tutor scoring — `ai/ev_player.py`

The single most frequent, most catastrophic divergence is the AI
casting Scapeshift with zero payoff permanents on board and (in
s60000 G1) the actual payoff sitting uncast in hand. The only gate on
the Scapeshift shape is `_overlay_land_sacrifice_fizzle`
(`ai/ev_player.py:841`), which checks **land count only**
(`LAND_SACRIFICE_MIN_LANDS = 4`, `ai/scoring_constants.py:2072`) —
i.e. it prevents the fizzle case and nothing else. There is no
"net position improves" / payoff-reachability predicate, which is
exactly what the July doc's claim #3 called for.

What a no-payoff Scapeshift does to this deck (quoted,
s55505 vs Boros G3, T5):

```
T5 P2: Scapeshift sacrifices 7 lands
T5: Simic Growth Chamber moved battlefield -> hand (Simic Growth Chamber ETB returns Simic Growth Chamber to hand)
T5: Gruul Turf moved battlefield -> hand (Simic Growth Chamber ETB returns Gruul Turf to hand)
T5: Gruul Turf moved battlefield -> hand (Gruul Turf ETB returns Gruul Turf to hand)
T5: Gruul Turf moved battlefield -> hand (Simic Growth Chamber ETB returns Gruul Turf to hand)
T5 P2: Scapeshift fetches 7 lands onto battlefield
```

Next-turn board: **3 tapped Simic Growth Chambers** where 7 lands
stood, and no payoff was ever cast. The engine's fetch priority
(`scapeshift_resolve`, `engine/card_effects.py:3143`) picks bounce
lands first whenever an untap watcher is in play, and the karoo ETBs
then bounce fetched co-entrants — the two-phase entry + LIFO ordering
(landed since July) caps retention at ~N/2, all tapped-then-untapped
karoos, with the bounced lands stranded in hand (only one land drop
per turn). Net effect of "ramp"-scored Scapeshift: **halves the mana
base**. In s60000 G1 this directly locked the hand-held Titan out of
the game: T5 pre-Scapeshift the deck had 4 lands + 2 Amulets (Titan
castable T6); post-Scapeshift it never reached 6 mana again and died
on T7 with Titan in hand.

Two engine-side notes belong to the same diagnosis but are NOT the
primary fix surface:

1. `scapeshift_resolve` **sacrifices all lands unconditionally**
   (`# Sacrifice all lands`, `engine/card_effects.py:3117`). The card
   says "sacrifice any number" — choosing N is a strategic decision
   hardwired into the engine layer, a boundary violation of "engine
   enforces rules; AI makes choices". Both Urza's Sagas were fed to it
   in s60500 G3.
2. The same payoff-blindness shows in Green Sun's Zenith: the AI casts
   it at whatever X current mana allows (X=1..4 → Arboreal Grazer,
   three times in one game) instead of treating a 4-of tutor as
   payoff access for the 3-of Titan (hold until X≥6, or fetch only
   when the found body advances the plan). Same mechanism class:
   tutor EV is not conditioned on what the tutor can actually deliver.

Class size: every land-sacrifice tutor and every X-tutor in Modern
(Scapeshift, GSZ, Finale-class, Chord-class) across every ramp/toolbox
deck — well above the 10-card patch threshold. The fix belongs in the
tutor-scoring path of `ai/ev_player.py` (payoff-reachability
predicate, shaped like the existing `_gate_x_cost_board_wipe` /
`_overlay_cascade_patience` gates), with the sacrifice-count choice
lifted to an AI hook.

## SECONDARY: Titan conversion speed — fetch line + missing haste activation

In the two losses where the deck played a textbook curve (Amulet T2,
Titan T4) it still died on T7, both times **one attack step short**
(opp at 9 and 5 life). Root: a resolved Titan converts at 6 dmg/turn
starting T+1 because the haste line does not exist end-to-end:

- `_primeval_titan_search` (`engine/card_effects.py:1777`) scores
  bounce/tapped mana lands 10(+3) over haste-granting lands 7(+3), so
  Hanweir Battlements is strictly dominated while any karoo/Vestige
  remains. Across all six replays: **37 Titan fetches, Battlements
  fetched once** (T9, karoos exhausted). Note the top-tier predicate
  `enters_tapped and produces_mana` also matches Crumbling Vestige
  (10 fetches) — it is a "tapped mana land" proxy, not a bounce-land
  predicate.
- Even when fetched, **no engine mechanism activates a haste-granting
  land ability** — `engine/activation.py` tranches 1–2 have no
  "gains haste" grant support (grep: no non-dash haste-grant path in
  `engine/` or `ai/`). The line is unreachable regardless of fetch
  priority.

Race math from the replays: with a T4 Battlements fetch + activation,
Titan attacks T4–T6 for 18 — both games end on T6, before the T7
death. This is the July doc's claim #1, confirmed and quantified at
~2/12 losses.

## July doc claim verdicts (docs/diagnostics/2026-07-06_amulet_titan_root_cause.md)

| July claim | Verdict (2026-08-26 replays) |
|---|---|
| Fixed-in-PR: self-discard binned Primeval Titans (`ai/discard_advisor.py` keystone protection) | **CONFIRMED FIXED** — 12 losses, zero keystone discards; observed hand-size discards were lands (Boseiju s55505-Affinity G1 T4, Mirrorpool s60500-Boros G3 T2) |
| #1 Titan fetch has no haste/aggro line | **CONFIRMED, refined** — a haste tier now exists in the fetch priority but is strictly dominated by the bounce/tapped tier (1/37 fetches), and no activation path for haste-granting lands exists at all. Secondary cause: 2/12 losses, each lost by exactly one turn |
| #2 Attack prioritization — Titan sits home while opponent races | **FALSIFIED** — in every replay Titan attacked on its first legal combat and every one thereafter (s55505 Boros: T5, T6; s55505 Affinity: T5, T6), and emergency-blocked when correct. The combat rework resolved this; do not re-diagnose |
| #3 Scapeshift enumeration lacks a payoff predicate | **CONFIRMED — PROMOTED TO PRIMARY** — 6/12 losses have a no-payoff Scapeshift as the first catastrophic divergence; the only existing gate is fizzle-count (`ai/ev_player.py:841`) |
| #4 Keep policy accepts land-flooded hands | **PARTIALLY CONFIRMED, secondary** — s60000-Affinity G3 kept "critical piece(s): Scapeshift, 5 lands"; `critical_pieces` in `decks/gameplans/amulet_titan.json` lists Scapeshift, so the mulligan treats a land-sac tutor as a win condition. One loss traceable |

## What the wins look like (control observation)

Of 4 valid game wins, only s55505-Affinity G2 was Titan-driven
(double Titan T6/T7). s60500-Boros G2 was won by **Urza's Saga
Construct tokens** (2×6/6, T7) with three Amulets contributing
nothing — further evidence that the deck's namesake engine is not the
part converting games.

## One fix per diagnosis (not implemented here)

Primary: add a payoff-reachability predicate to the land-sacrifice
tutor gate in `ai/ev_player.py` (and route GSZ X-selection through the
same predicate), test-first per Option C — rule-phrased test:
"land-sacrifice tutor is held when no payoff is on board or made
castable by the fetch". Cross-deck beneficiary: any Valakut/landfall
or toolbox deck; Storm-side robustness case per the
generalization-first rule. The engine-side "sacrifice all lands"
choice and the haste-activation gap are named for their own future
diagnoses.

## PAYOFF-GATE FIX MEASURED (2026-08-26, post-fix)

The primary fix landed (`4fa4de2` + typed-field refactor `b781802`): the
land-sacrifice tutor now requires a reachable payoff (conservative
ceil(N/2) retention without an untapped-entry watcher). Behaviourally
verified: the s60000 replay that previously locked its hand-held Titan out
via a 4-land no-payoff Scapeshift now casts none; the pinned anchor game
shortened T13 -> T9 with the suicide line gone.

**Measured (n=20 Bo3 field): 29.6% -> 31.2% (+1.6pp, within noise).**
Matchup texture improved at the top (Goryo's 75%, Creatures Toolbox 75%,
Azorius 50-55%) while the fast-aggro rows stay catastrophic (Boros 10%,
Zoo 10%, Ruby Storm 5%, Pinnacle 5%). This matches the loss tally: removing
the 6/12 self-destruction mode converts games only where the deck's natural
line then wins the race — against fast aggro the SECONDARY root cause
(conversion speed: fetch priority never takes the haste land, and no
haste-grant activation mechanism exists) still costs the exact one turn
those games die by. The secondary is now the highest-leverage remaining
item for this deck: both perfect-curve losses died one attack short, so its
counterfactual is sharp.

## HASTE-LINE FIX MEASURED (2026-08-27, post-#554)

GRANT_HASTE_TARGET activation + Titan fetch-priority substitution landed
(#554). Seed bases matter for comparison: on the FIELD base (50000+) the
progression is 31.2% (post-payoff-gate) -> **34.8%** (+3.6pp, borderline
significant, directionally right); on the MATRIX base (40000+) the payoff
gate alone measured 29.6 -> 34.4, with the haste addition to be read from
the next matrix run. Matchup texture: the reanimator/toolbox/control rows
now reach 45-75%, while the fast-aggro rows remain the floor (Boros 5%,
Zoo/Pinnacle 10%) — Amulet still loses the pure race even converting one
turn faster.

Remaining known items for this deck, from the original loss tally: the
GSZ-class X-tutor payoff-blindness (2/12 losses — same mechanism class as
the Scapeshift gate, deferred at build time), and the draw/structural tail.
The deck sits ~10pp below band with both primary and secondary subsystem
fixes landed; the next increment is the X-tutor selection fix, after which
any residual is likely list-structural like the Zoo verdict.

## GSZ X-TUTOR FIX MEASURED (2026-08-27, post-#556)

Field base: 34.8 -> **35.2%** (+0.4, noise) — consistent with the X-tutor
mode being 2 of the 12 walked losses. The fix's value is class-wide
correctness (every X-creature-tutor deck; the engine's max-affordable-X
default was wrong for all of them), not this deck's WR. Amulet's cumulative
field-base progression across the three fixes: 31.2 -> 35.2 vs band
[45-60]. Remaining tally is draw/structural plus the race floor vs aggro;
no further code item is identified for this deck — residual is likely
list-structural pending the external-list verification.
