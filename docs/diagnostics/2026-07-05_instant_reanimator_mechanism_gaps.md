---
title: Instant Reanimator — engine mechanism gaps (blink vs Goryo's exile rider)
status: active
priority: secondary
session: 2026-07-05
tags: [reanimation, blink, ephemerate, goryos-vengeance, engine, delayed-triggers]
summary: >
  Ephemerate-blinking a Goryo's-reanimated creature does not clear the
  end-of-turn exile rider — the engine tracks the delayed trigger by
  CardInstance identity across the blink, so the deck's namesake line
  ("reanimate, blink to keep it permanently") is impossible in-sim.
  Gameplan tuning cannot fix this; it caps the archetype's ceiling.
---

# Instant Reanimator — mechanism gaps (2026-07-05)

Found while tuning `decks/gameplans/instant_reanimator.json` (Track A,
field WR 24.4% → target band 30-70). These are engine-layer gaps; per the
abstraction contract they are documented here rather than patched from a
deck-tuning session.

## Gap 1 (primary): blink does not clear the Goryo's end-of-turn exile rider

**Subsystem:** `engine/turn_manager.py` end-step handling of
`game._end_of_turn_exiles` (filled by
`engine/permanent_effects.py:PermanentEffects.reanimate(exile_at_eot=True)`).

**Rule:** Goryo's Vengeance's delayed trigger ("exile it at the beginning
of the next end step") tracks a specific *object*. When Ephemerate exiles
and returns the reanimated creature, it re-enters as a **new object**; the
delayed trigger no longer applies and the creature stays permanently.
This is the entire point of the "Instant Reanimator" archetype: Goryo's
Vengeance an Atraxa/Griselbrand, Ephemerate it in response/post-combat,
keep a hard-cast-quality body from turn 3.

**Engine behaviour:** `_end_of_turn_exiles` stores `(CardInstance,
controller)` tuples and, at end of turn, exiles any stored card whose
`zone == "battlefield"` (`engine/turn_manager.py:212-220`). A blink
returns the *same* `CardInstance` to the battlefield, so the rider still
fires.

**Replay cite:** `run_meta.py --bo3 "Instant Reanimator" boros -s 50000`
(pre-tuning gameplan), Game 2 turn 4:

```
T4 P2: Cast Goryo's Vengeance (1B)
T4 P2: Reanimate Atraxa, Grand Unifier
T4 P2: Cast Ephemerate (W)
T4: Blink Atraxa, Grand Unifier
...
T4: Atraxa, Grand Unifier moved battlefield -> exile (Goryo's end-of-turn exile)
T4: Atraxa, Grand Unifier exiled (end of turn)
```

The blink resolved (Atraxa re-ETB'd, drew cards) and the engine exiled it
anyway. In real Magic that Atraxa stays forever.

**Class size:** every "reanimate with a downside rider + flicker to
launder the rider" line — Goryo's Vengeance + Ephemerate/Phelia/Touch the
Spirit Realm, Unearth-style riders, Feign Death/Undying Evil interactions.
Mechanic, not a card patch.

**Suggested fix shape:** clear a card's pending `_end_of_turn_exiles`
entry whenever it changes zone (the rider is object-bound); the blink
handler already moves the card battlefield → exile → battlefield, so a
zone-change hook that drops stale riders fixes the whole class. Needs a
rule-phrased failing test: "end-of-turn exile rider does not survive a
zone change of the tracked object".

**Impact:** with the rider un-launderable, a resolved Goryo's is worth
one attack + one ETB instead of a permanent 7/7 lifelink board win. Both
diagnostic seeds (`boros -s 50000`, `dimir -s 50500`) show games where a
kept reanimated body wins outright. This is the dominant residual after
gameplan tuning.

## Gap 2 (minor): Goryo's Vengeance is only cast at sorcery speed

The sim casts Goryo's Vengeance exclusively in the controller's main
phase. The real card is an instant — the archetype name comes from
casting it at the opponent's end step (dodging sorcery-speed
interaction) or mid-combat. Observed consequence: vs Dimir
(`-s 50500`, G1 turns 5-6) Goryo's was jammed into open counter-mana
twice and countered both times; an instant-speed AI would have baited or
waited. Subsystem: `ai/ev_player.py` / `ai/response.py` proactive
instant-speed casting — the engine appears to have no "cast own-turn
spells at instant speed on opponent's turn" pathway for non-reactive
spells. Lower priority than Gap 1.

## What data-only tuning did fix (same session)

Rebuilt `decks/gameplans/instant_reanimator.json` to the mature schema
(FILL_RESOURCE graveyard goal with `resource_min_cmc: 7`, Goryo's as
payoff, looting as engines/enablers, typed `mulligan_combo_paths`,
`critical_pieces`, `fallback_goals`). This activates the existing generic
hooks (GV-1 discard-fuel boost in `ai/discard_advisor.py`, GV-2
reanimation-readiness boost and GV-4 payoff gates in `ai/ev_player.py`)
that previously never fired because the auto-generated gameplan lacked
the FILL_RESOURCE shape. `resource_min_cmc` is 7 (not 5) because the
CMC-only fuel class otherwise captures Solitude (CMC 5, non-legendary,
not a Goryo's target) — observed T2 loot-discarding the deck's own
removal vs Boros (`-s 50000`, attempt-2 replay).

**Field WR (n=15 Bo3):** 25.6% before → 25.5% after (flat overall,
loop-break invoked after 3 attempts). Composition shifted hard:
slow/interactive matchups way up (Azorius Control 53→80, Amulet Titan
27→47, Boros Ponza 7→33, 4/5c Control 7→27), fast aggro collapsed to
~0 (Boros Energy 7→0, Domain Zoo 7→0, Affinity 0→7). Diagnosis: a
reanimated fatty that survives (Gap 1 fixed) is exactly the
stabilization tool the aggro matchups need — one attack + one ETB per
Goryo's cannot outrace a T4-T6 aggro clock, and no mulligan/priority
policy changes that arithmetic. Gap 1 is the blocking dependency for
this deck reaching its 30-70 band.
