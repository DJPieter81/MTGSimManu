---
title: Dimir Midrange at 37% — its interaction is held, its blockers die with their pump unused, and its sweeper is never activated
status: active
priority: primary
session: 2026-10-14
supersedes: []
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
tags: [diagnostic, outlier, dimir-midrange, ai, deferral, combat, activation, sideboard, root-cause]
summary: >
  Dimir Midrange is at 37.4% of the field (drw-post; band 45-60, the
  field's second-largest miss). Its worst cells are Azorius Control (WST
  v2) 5%, Boros Energy 12.5% and Eldrazi Tron 15%. A Bo3 of Dimir vs Boros
  Energy (s50000, at db77f77; Boros wins 2-1) and one vs WST v2 (WST v2
  wins 2-0) name four AI subsystems. (1) The DEFERRAL GATE
  (`ai/ev_evaluator._is_immediate_interaction`) gives an edict no this-turn
  signal: Sheoldred's Edict scores -0.1 from T2 to T7 while Ajani's walker
  climbs, and is cast at last on T7 facing lethal. 59 of the pool's 71
  edict instants and sorceries are held this way. (2) BLOCK EVALUATION
  does not count a blocker's own instant-speed pump: Psychic Frog blocks
  three times and dies three times with cards in hand, where one discard
  ("put a +1/+1 counter on Psychic Frog") keeps it alive and kills the
  attacker. (3) Engineered Explosives is cast for X=3 into two charge
  counters and never activated in six turns against a board of
  mana-value-2 permanents. (4) The SIDEBOARD brings Mystical Dispute in
  against a deck with no blue spells; it fizzles on a paid tax. Units are
  built in that order, each red-first with its class.
---

# Dimir Midrange at 37% — its interaction is held, its blockers die with their pump unused, and its sweeper is never activated

## Why this document exists

Dimir Midrange is the field's second-largest band miss after Ruby Storm: 37.4 against [45, 60] (`drw-post`). CLAUDE.md asks for a Bo3 replay-based root cause before any unit targets an outlier. No unit has targeted Dimir yet. Its worst cells (`drw-post`, row and column averaged):

| Opponent | Dimir as row | Dimir as column | Mean |
|---|---|---|---|
| Azorius Control (WST v2) | 5 | 5 | 5.0 |
| Boros Energy | 15 | 10 | 12.5 |
| Eldrazi Tron | 25 | 5 | 15.0 |
| 4/5c Control, Azorius Control (WST), 4c Omnath | 10–25 | 10–25 | 17.5 |

## The replays

- `run_meta.py --bo3 "Dimir Midrange" "Boros Energy" -s 50000` at `db77f77`: Boros Energy wins 2-1 (G1 T8, G2 Dimir T9, G3 T10).
- `run_meta.py --bo3 "Dimir Midrange" "Azorius Control (WST v2)" -s 50000`: WST v2 wins 2-0. G1 is decided on T21 by decking.
- `run_meta.py --trace "Dimir Midrange" "Boros Energy" -s 50000` reproduces G1 with the AI's scores.

## Subsystem 1 — the deferral gate holds every edict

**Evidence (G1):**
- Dimir draws Sheoldred's Edict on T2.
- Ajani transforms on T3 and puts loyalty on every turn after.
- The trace scores the edict at −0.1 on every main phase from T3 to T6 (`[h=-0.1 la=-0.1]`), always the deferral path's exposure cost.
- On T6 Dimir passes with five untapped lands, the edict and Force of Negation in hand.
- On T7, at 6 life facing six creatures, it casts the edict and Ajani is sacrificed.

**Rule (AI, from CR 701.21a):** an edict removes an opposing permanent as it resolves. Each turn it waits is a turn of that permanent's value spent against its caster, so it is interaction this turn, exactly as destroying a target is.

**Subsystem:** `ai/ev_evaluator._enumerate_this_turn_signals` → `_is_immediate_interaction`. It reads removal tags and the typed damage, destroy, exile, counter and discard fields. It has no read of a SACRIFICE instruction an opponent performs, so an edict gets no signal and is deferred forever.

**Class:** 71 pool instants and sorceries whose typed spell or mode host makes an opponent, every player or a target player sacrifice; 59 carry no signal today. Field members are Sheoldred's Edict (Dimir MB ×1, Eldrazi Ramp SB ×2) and Lorehold Charm (Hollow One).

## Subsystem 2 — block evaluation ignores the blocker's own pump

**Evidence:** Psychic Frog ("Discard a card: Put a +1/+1 counter on Psychic Frog") blocks and dies three times, each time with cards in hand:
- G1 T3: it blocks the 2/1 Cat Warrior token (`lifespan_delta=+2.47`). The token's death transforms Ajani.
- G1 T5: it blocks Seasoned Pyromancer, a 2/2 (`+0.21`).
- G3 T6: it blocks Ragavan, a 2/1 (`BLOCK-EMERGENCY`).

One discard makes the Frog a 2/3, which survives and kills each of those attackers.

**Rule (AI, from CR 602.2, 510.1):** a blocker's controller may activate its abilities after blockers are declared, before damage. A block is worth what it is worth after the pumps its controller can pay for.

**Subsystem:** the blocker choice in the AI's combat module (`lifespan_delta`). It values a block from printed power and toughness, plus counters already placed.

**Class (to size before building):** creatures with an instant-speed self-pump, whether a +1/+1 counter or +N/+N until end of turn, paid with mana, a discard or life.

## Subsystem 3 — Engineered Explosives: sunburst X and the activation

**Evidence (G3):** Dimir casts Engineered Explosives on T4 with X=3, paying three mana of two colours. It enters with two charge counters (sunburst counts colours, CR 702.44a). It sits on the battlefield T4–T10 and is never activated. Boros held Ajani, Voice of Victory and Goblin Bombardment, all mana value 2, across those turns.

**Subsystems (to confirm in code):**
- the X choice for a sunburst permanent: a third mana of a colour already paid adds no counter;
- whether the board-wipe activation ("{2}, {T}, Sacrifice: destroy each nonland permanent with mana value equal to the number of charge counters") is offered and valued at all.

## Subsystem 4 — the sideboard brings a blue-discount counter against a deck with no blue spells

**Evidence:** the plan vs Boros Energy is "+2 Engineered Explosives, +2 Mystical Dispute, −2 Consider, −2 Drown in the Loch". Mystical Dispute costs {2}{U} against a non-blue spell. Boros has no blue spells, so the discount never applies. In G3 T9 the Dispute targets Seasoned Pyromancer, Boros pays the 3, and the Dispute is spent. The mulligan to six bottomed Counterspell and kept the Dispute.

**Subsystem:** the sideboard plan's selection. A card whose cost reduction needs an opposing blue spell, or whose only targets are blue, is worth its full cost against a deck without blue spells.

## The WST v2 games

G1 is decided on T21 by decking:
- Dimir passes most turns and deploys one threat at a time into Solitude, Prismatic Ending and March of Otherworldly Light.
- Murktide Regent first attacks on T15.

Before naming a subsystem here, the control mirror needs its own trace. Subsystem 1 bears on it as well: Dimir holds interaction. That is recorded, not built.

## Order of units

1. **Edict deferral:** smallest, one predicate, class 59.
2. **Self-pump in blocks:** needs the class census and the combat module.
3. **Engineered Explosives:** sunburst X and the activation, sized as a class first: sweeping activations with counters, plus sunburst.
4. **Sideboard selection** of conditional-discount counters.

Each unit lands with its red tests, ratchets, digest and anchor, and a same-seed arm. Dimir's row is replayed cell-exact when it moves more than 5 pp.

## Outcome

- **Subsystem 1, unit EI** (`c2479ec`, tracker): an edict is interaction this turn, worth what its victim gives up.
  - **Measured (`ei-post`):** Dimir Midrange 37.6 → 36.5. That is inside the arm's noise, with no movement toward the band.
  - In the replay seed's G1, the edict is cast on T3 where it had waited to T7.
- **Subsystem 2, unit CP** (`ba2d33e`, tracker): a creature's own pump is used in combat when it changes the fight, and the block counts it.
  - After blockers, both players now hold priority (CR 509.4).
  - Of the digest's 27 games, one changes: Domain Zoo's Frog survives its block and kills the attacker.
  - **Measured (`cp-post`):** Dimir Midrange 36.5 → 39.7 (+3.2), toward the band. The other Frog decks rise as well (Instant Reanimator +7.3, Domain Zoo +4.1, Grixis Reanimator +3.2).
  - **Loop-break count:** reset; CP moved Dimir toward its band.
- **Subsystem 3 (Engineered Explosives) is wider than the replay showed:**
  - Its entry handler counts the colours of the controller's lands, not the colours of mana spent (CR 702.44a).
  - The runner adds a charge counter to it every turn, though it has no ability that does.
  - The runner pops it without paying its cost, against the opponent's permanents only.
  - Affinity plays it main deck ×4, so the unit measures Affinity as well.
- **Subsystem 3, unit EE** (`c82832a`, tracker): a sweep destroys each permanent its description admits, read off what its source last had; sunburst counts the colours spent; the runner's shortcut is retired.
  - At the replay seed's G3, Dimir casts the Explosives for X=1 and pops it on T5 for three creatures, and wins the match 2-1.
  - **Measured (`ee-post`):** Dimir Midrange 39.7 → 40.4, inside the arm's noise. The loop-break count is one since CP.
- **Subsystem 4, unit TD:** the engine never read a spell's discount for its target. Mystical Dispute cost {2}{U} against a blue spell, and that is now fixed. The sideboard solver still counts no counter's cost (lead).
- **Loop-break count:** one Dimir-targeted unit without movement. If CP is the second and a third follows without movement, Dimir code halts and a deeper Bo3 root cause comes first.
