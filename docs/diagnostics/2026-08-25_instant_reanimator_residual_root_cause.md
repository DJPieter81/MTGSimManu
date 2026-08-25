---
title: Instant Reanimator residual — AI launder-sequencing, not a missing mechanic
status: active
priority: secondary
session: 2026-08-25
supersedes: docs/diagnostics/2026-07-05_instant_reanimator_mechanism_gaps.md
superseded_by: []
depends_on: []
tags: [ai, reanimation, blink, ephemerate, goryos-vengeance, discard, sequencing]
summary: >
  Bo3/Bo1 replay re-diagnosis of Instant Reanimator (~36% field, band 45-60).
  The engine gap the 2026-07-05 doc named as the blocking dependency is FIXED
  and verified (blink clears the Goryo's end-of-turn exile rider via CR 400.7
  entry-seq identity; 8 tests green). The residual is bimodal performance: the
  deck beats control (~67% vs Azorius) and collapses versus aggro (0% vs Domain
  Zoo). Cause is AI-layer: the stabilizing line (keep a reanimated 7/7
  lifelinker by blinking it) only fires when the blink happens to be in hand
  with the right mana, and the forced-loot discard advisor pitches the deck's
  best castable blocker. Same launder machinery is shared with Goryo's
  Vengeance, so a fix lifts both.
---

# Instant Reanimator residual root cause (2026-08-25)

## What changed since 2026-07-05

The prior doc declared the engine's blink-vs-exile-rider behaviour the
"blocking dependency for this deck reaching its band." **That is now fixed and
independently verified in this session:**

- `engine/game_state.py:138-144` — `register_end_of_turn_exile` captures
  `card.battlefield_entry_seq` alongside the card.
- `engine/cards.py` — `enter_battlefield()` increments `battlefield_entry_seq`.
- `engine/zone_manager.py::_blink_zone_transition` re-calls
  `enter_battlefield()`, so a blinked permanent is a NEW object (CR 400.7).
- `engine/turn_manager.py:230-234` — the end step exiles only when
  `card.battlefield_entry_seq == entry_seq`; after a blink they differ and the
  rider is dropped.
- `tests/test_delayed_eot_exile_drops_on_zone_change.py` +
  `tests/test_blink_clears_pending_eot_detriment.py` — **8 passed**.

The 2026-07-05 doc is therefore marked `status: superseded`. Re-opening that
engine gap would be chasing a fixed bug.

## Symptom (replay-verified)

The deck is **bimodal**, not uniformly weak:

| Opponent | Bo1 n=6 WR | note |
|---|---|---|
| Azorius Control | ~67% | combo assembles; one Goryo's swing per turn is enough |
| Boros Energy | 33% | |
| Domain Zoo | **0%** | never lands a stabilizing body |

The aggro floor is what drags the field number to ~36%.

## Root cause — AI layer, two levers

**1. The launder line is a 3-card coincidence, not a plan (primary).**
The one line that stabilizes versus aggro is keeping a reanimated Atraxa (7/7
flying/vigilance/deathtouch/**lifelink**) by blinking it before the end-step
exile rider fires. The decision layer for this exists and is wired
(`ai/ev_player.py` RC-1 blink-clears-detriment credit), but it only fires when
the blink is *coincidentally* in hand with the right mana on the reanimate
turn. Nothing holds or digs for the blink before committing the reanimation, so
under pressure the anti-aggro line effectively never happens. Observed
(seed 50000 vs Azorius): reanimate → ETB fires correctly → one attack →
`Atraxa moved battlefield -> exile (delayed end-of-turn exile)`.

**2. The forced loot pitches the anti-aggro wall (secondary).**
In both aggro replays the deck's "discard 2" binned a castable 4/6 flyer — its
best natural blocker — because the discard advisor ranks it as low-value filler
(non-legendary, CMC below the gameplan's reanimation-fuel floor) with no term
for its *blocking* value against the live clock.

**3. Greedy 4-colour fetch/shock manabase (contributing, data).** The deck was
at 12-15 life by T4 from its own mana in both aggro games, shortening the
window it must survive.

## Fix sketch (generic, abstraction-contract-safe)

- **Not engine.** Do not re-open the rider gap.
- **Commit-gating on protection:** when the opponent's clock (from
  `ai/clock.py` turns-to-lethal) is short, treat a temporary-reanimation payoff
  as requiring *payoff ∧ protection* in combo-readiness/mulligan rather than
  payoff alone. Generic to any "temporary reanimation + blink-to-keep" shell.
- **Defensive discard term:** don't pitch a castable creature whose blocking
  value exceeds the loot's selection gain while under a lethal-in-N clock, with
  N derived from `ai/clock.py`. Rule-phrased test: *"forced loot keeps a
  castable wall over a spare land when under a lethal-in-≤N clock."*

## Cross-deck check

**Goryo's Vengeance** is the sibling deck and shares the entire launder
machinery — `docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md`
names "the AI never attempts the blink line" as its own top follow-up. A fix
here lifts both decks.

## Confidence / falsifier

High on the chain (engine verified fixed + tests green + replays show the deck
never keeps a body and craters vs aggro while beating control). Medium on lever
ranking — evidence is 3 detailed games at one seed family plus n=6 Bo1 spot
rates, not a Bo3 field sweep.

**Falsifier:** force the blink + mana on the reanimate turn versus aggro. If
the kept lifelinker flips those matchups, lever 1 is confirmed; if it still
loses, the manabase/tempo lever dominates. Bo1 numbers here are a diagnostic
reference only — Bo3 is canonical.
