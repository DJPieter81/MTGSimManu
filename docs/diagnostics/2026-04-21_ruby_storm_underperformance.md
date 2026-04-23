---
title: Ruby Storm 25% underperformance — EV divergence diagnostic
status: active
priority: primary
session: 2026-04-21
depends_on:
  - docs/experiments/2026-04-20_phase11_n50_matrix_validation.md
tags:
  - p0
  - wr-outlier
  - ruby-storm
  - diagnostic
  - storm
  - phase-12
summary: "Ruby Storm posts 24.9% flat / 21.9% weighted at N=50. Replay seed 60130 (Ruby Storm vs Boros) shows the divergence: on T4 with Ruby Medallion ×2 + Past in Flames resolved, Storm casts a 6-ritual chain flooring at ~12 floating mana and then PASSES TURN without a finisher. Mana empties between turns (CR 500.4) — the entire chain is wasted. The AI fires rituals greedily even when Grapeshot/Wish are not in hand or graveyard. Storm patience gate fires at storm=0, not at each ritual step."
---

# P0 WR outlier diagnostic — Ruby Storm 25% flat / 21.9% weighted

## Headline

Ruby Storm burns its ritual chain without a visible finisher path.
The `storm_patience` gate in `ai/strategy_profile.py` only considers
the *entry* into the combo (storm=0 check), not the per-ritual
step. Once one ritual resolves, the AI chains the rest — even when
Grapeshot / Wish / Past-in-Flames-flashback don't complete a lethal
sequence.

## Evidence (seed 60130 — Ruby Storm vs Boros, G1)

Replay: `replays/ruby_storm_vs_boros_energy_s60130.txt`
HTML: `replays/replay_ruby_storm_vs_boros_energy_s60130.html`

T4 Ruby Storm turn (lines 248-298):
```
T4 P1: Cast Desperate Ritual (1R)            ← storm = 1
T4 P1: Cast Ruby Medallion (2)               ← 2nd Medallion (1 already down)
T4 P1: Cast Past in Flames (3R)              ← enables flashback on GY instants
T4 P1: Cast Wrenn's Resolve (1R)             ← draws 2, net +1
T4 P1: Play Scalding Tarn
T4 P1: Cast Valakut Awakening (2R)           ← card selection
T4 P1: Cast Pyretic Ritual (1R)              ← +3R
T4 P1: Cast Manamorphose (2)                 ← +2 any
T4 P1: Cast Desperate Ritual (1R)            ← +3R
T4 P1: Cast Pyretic Ritual (1R)              ← +3R
T4 P1: Cast Desperate Ritual (1R)            ← +3R
  [Begin Combat]                             ← T4 ends, mana empties
```

At the end of T4, floating mana was approximately R×12 (after 6
rituals through Medallions). Storm count: ≥10. No finisher was
cast. The entire ritual chain is flushed at the phase transition
(CR 500.4).

The AI's hand at end of T4 still contains 5 cards. No Grapeshot or
Wish visible in the play log. The Past in Flames flashback also
didn't complete because Grapeshot never hit the graveyard.

T5 Ruby Storm turn (line 365-372):
```
T5 P1: Play Wooded Foothills → Mountain
  [Begin Combat]
  [Declare Attackers] P1 does not attack
  [End Combat]
  [Main 2]
  [End Step]
```

T5 is effectively a pass. Storm has burned its combo turn with no
result; Boros closes T5.

## Decklist context

`decks/modern_meta.py` Ruby Storm block:
- Main: 1× Grapeshot, 2× Wish, 3× Past in Flames
- SB:   1× Grapeshot, 1× Empty the Warrens, 1× Past in Flames

Total finisher sources in library: 1 Grapeshot + 2 Wish = 3 main-60
cards. Probability of drawing one by T4 with ~10 cards dug: roughly
50%. When the draw misses, the AI should recognise no-finisher-path
and hold rituals, not combust them.

## Diagnosis — AI layer

### (A) Storm patience gate has wrong scope

`ai/strategy_profile.py` STORM profile:
```
storm_patience:    hold rituals at storm=0 unless finisher access
storm_go_off_bonus: triggers the chain once conditions are met
```

The `storm=0` guard stops the AI from casting the *first* ritual
recklessly. Once the first ritual resolves, storm > 0 and the gate
disengages. The remaining rituals fire without re-checking whether a
finisher is still reachable this turn.

### (B) Finisher-access check is one-shot, not per-step

`ai/ev_player.py` Storm scoring treats "cast ritual" as a local EV
maximisation given Medallion reduction. It doesn't ask:
- Is Grapeshot / Wish / PiF-flashback-Grapeshot castable this turn
  with current mana + future ritual output?
- If no: abort the chain and pass with mana preserved for T5 dig.

The absence of this gate is the core problem. Without it, Storm
converts rituals → floating mana → wasted.

## Candidate fix locations

Not fix proposals — diagnostic only.

- `ai/ev_player.py` — before accepting a "cast ritual" play, project
  the post-chain state and require at least one reachable finisher
  (Grapeshot in hand, Wish in hand with SB Grapeshot available, or
  PiF-flashback on a GY Grapeshot). If no finisher is reachable,
  downgrade the ritual's EV below `pass_threshold`.
- `ai/combo_calc.py` — already has combo-chain EV infrastructure.
  Extend to expose a "can_kill_this_turn()" query that returns
  bool + confidence, callable from the ritual scorer.
- `ai/strategy_profile.py` — add `storm_per_ritual_gate` alongside
  `storm_patience`, reading the finisher-reachability signal at each
  ritual step.

## Relation to other outliers

This is isolated to combo-deck behaviour (Storm + Goryo's + possibly
Amulet). Goryo's 22.7% is similar magnitude; worth examining whether
Goryo's reanimator chain has the same "fire the chain even without
the target in hand" pathology.

## Non-negotiables

- Option C: failing test for "hold 3rd+ ritual when no finisher
  in hand / GY-flashback-reachable".
- Oracle-driven ritual detection (`'add {R}{R}{R}'` etc.), not
  hardcoded names.
- N=50 matrix validation before merge; expected direction is Ruby
  Storm +5-10pp.
