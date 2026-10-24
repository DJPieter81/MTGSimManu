---
title: "Temporal state model — one turn clock, one effect ledger"
status: active
priority: primary
session: 2026-09-28
depends_on: [docs/design/rules-foundation-sweep-tracker.md]
tags: [engine, timing, duration, continuous-effects, architecture]
summary: >
  Temporary game state is spread over four mechanisms with separate expiry
  hooks (player "this turn" flags, card temp_* fields, the layer system's
  durations, delayed triggers). They drift: a "this turn" effect flag set
  during a player's own turn survives the opponent's whole next turn
  (reproduced). Proposal: one TurnClock that emits every step boundary, one
  EffectLedger that owns every temporary effect with a typed Duration
  expiring on clock events and object identity, and event tallies kept
  apart from effects. Migrated in behaviour-identical stages.
---

# Temporal state model — one turn clock, one effect ledger

## Why

Rules text is built from three orthogonal parts:

- **timing** — *when* something happens: "at the beginning of your upkeep",
  "whenever a creature attacks";
- **duration** — *how long* an effect lasts (CR 611.2): "until end of turn",
  "this turn", "until your next turn", "for as long as …";
- **state** — *what it is attached to*: a player, a permanent (a specific
  object — CR 400.7), the game, or a stack item.

The engine has no single owner for any of these. The inventory
(2026-09-28, read from the code):

| Mechanism | Holds | Expires at |
|---|---|---|
| `PlayerState` "this turn" fields (22) | Event tallies (cards drawn, life lost, spells cast) **and** effect flags (silenced, can't attack, combat damage prevented, cost rules, flash permission, spell-type prohibitions) | That player's own untap (`reset_turn_tracking`) |
| `CardInstance` temp fields | `temp_power_mod`, `temp_toughness_mod`, `temp_keywords`, `cannot_be_blocked_this_turn`, `attacked_this_turn`, … | `cleanup_damage` / `new_turn` |
| Layer system (`ContinuousEffect.duration`) | `until_next_turn` effects (S3a) only | Three `cleanup_*` calls at three hooks |
| Delayed triggers, turn-end request | "next upkeep", `end_turn_requested` | Their own hooks |

**The drift is a live defect.** An effect flag and an event tally share one
reset: the player's own untap. So a "this turn" effect set on the active
player lives through the opponent's entire next turn. Reproduced:

- A combat-damage prevention resolved on P1's turn is still active on
  P2's turn (`combat_manager.py:283` reads any player's flag).
- The same leak applies to every effect flag in that list.

## Model

### 1. `TurnClock` — the single owner of timing (`engine/turn_clock.py`)

`turn_manager` advances the clock at every step boundary. The clock emits
typed events:

- `TURN_BEGINS(p)`, `UNTAP(p)`, `UPKEEP(p)`, `DRAW(p)`, `MAIN(p, n)`;
- `BEGIN_COMBAT`, `DECLARE_ATTACKERS`, `DECLARE_BLOCKERS`, `COMBAT_DAMAGE`,
  `END_COMBAT`;
- `END_STEP(p)`, `CLEANUP(p)`, `TURN_ENDS(p)`.

Subscribers — never ad-hoc calls — handle:

- expiry (below);
- the reset of event tallies at `TURN_BEGINS` of *any* player (the shared
  game-turn clock, CR "this turn");
- delayed and "at the beginning of …" triggers.

### 2. `Duration` — typed, expiring on clock events and object identity

Each duration is one value with an expiry predicate:

| Duration | Expires |
|---|---|
| `END_OF_TURN` / `THIS_TURN` | `CLEANUP` of the turn it was created in |
| `END_OF_COMBAT` | `END_COMBAT` |
| `UNTIL_YOUR_NEXT_TURN(c)` | `TURN_BEGINS(c)` |
| `UNTIL_NEXT_UPKEEP(p)` / `NEXT_END_STEP(p)` | That event |
| `WHILE_ON_BATTLEFIELD(obj)` / `UNTIL_LEAVES(obj)` | That object's zone change (identity = `instance_id` + `battlefield_entry_seq`, so a blinked permanent is a new object — CR 400.7) |
| `PERMANENT` | Never |

### 3. `EffectLedger` — the single owner of temporary effects (`engine/effect_ledger.py`)

A record is `{scope, payload, duration, source, controller, timestamp}`:

- **scope:** `Game` | `Player(i)` | `Object(instance_id, entry_seq)`.
- **payload:** a typed effect the engine already has, for example:
  - `PTMod`, `KeywordGrant`, `TypeChange` (read by the layer system at
    layers 4/6/7);
  - `Restriction(attack|block|cast[filter]|draw[cap])`;
  - `Permission(flash[types])`, `CostRule`, `CombatPrevention`;
  - `AttackObserver`, `DelayedTrigger`.

Queries are typed:

- `ledger.active(scope, kind)` is the only read path for gates
  (`can_cast`, `can_attack`, `can_block`, the cost matcher, draw limits,
  combat damage);
- the layer system reads the P/T and ability payloads.

Expiry is driven only by the clock and by zone changes. So "how long" is
answered once, and a new rules effect is a new payload kind, not a new field
plus a new reset line.

### 4. Tallies stay counters

"Cards drawn this turn", "spells cast this turn" and "life lost this turn"
are facts about the game turn, not effects. They stay as counters, reset
by the clock at `TURN_BEGINS` of any player. This separates them from the
effect flags they are mixed with today.

### 5. Stack and board state

- Stack items already carry their own per-object state (X, kick count,
  targets, mode).
- The ledger's object scope gives effects and triggers last-known-information
  identity: an effect on a creature that left and returned does not follow
  it.
- Delayed triggers become ledger entries of kind `DelayedTrigger`, fired
  by the clock — one owner for "at the beginning of the next …".

## Migration — behaviour-identical stages (the S1 precedent)

Each stage is verified by byte-identical seeded verbose logs (as the S1
clause-resolver refactor was), except where a stage fixes a named defect.
That defect then lands with a failing test and an auditor invariant.

- **M1 — TurnClock.** Emit events from `turn_manager`; move the existing
  resets and cleanups to subscribers. Identical behaviour.
- **M2 — Ledger for player-scoped effects.** Silenced, spell-type
  prohibitions, can't attack / can't be attacked, combat damage prevention,
  cost rules and flash permission become ledger records with typed
  durations.
  - Fixes the "this turn" leak.
  - Test: an effect created this turn is gone at the next player's turn.
  - Auditor: `611.2a/this_turn_effect_expired`.
- **M3 — Ledger for object-scoped effects.** End-of-turn pumps and keyword
  grants (`temp_*`), `cannot_be_blocked_this_turn`, and the S3a
  `until_next_turn` layer effects move to object-scoped records read by the
  layer system. A blinked object sheds them (CR 400.7).
- **M4 — Delayed triggers on the clock.**
- **Then S3b-2/3 on the ledger:** per-creature restrictions and attack
  observers are just new payload kinds.

## Enforcement

A ratchet, `tools/check_temporal_state.py`, pins and may only lower:

- `*_this_turn` / `temp_*` effect fields outside the ledger;
- `cleanup_*` / reset hooks not subscribed to the clock.

A new temporary effect must be a ledger payload.

## Open decisions

- Order: M1–M2 before S3b-2 (recommended — S3b-2 would otherwise add a
  fifth mechanism) or after.
- Whether M3 also absorbs vehicles/manland animation ("becomes a creature
  until end of turn"), the deferred S3b-4.
