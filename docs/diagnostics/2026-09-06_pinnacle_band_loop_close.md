---
title: Pinnacle Affinity band loop — band met at 54.8%; the three cells still ≥85% are held by the victims' own tracked defects
status: active
priority: primary
session: 2026-09-06
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
  - docs/diagnostics/2026-08-26_amulet_titan_rediagnosis.md
  - docs/diagnostics/2026-09-05_zoo_band_loop_break.md
tags: [pinnacle-affinity, band-loop, saga, equipment, mana-planner, amulet, goryos, hollow-one, loop-close]
summary: >
  Three class-sized fidelity fixes (Saga chapter timing + "with mana
  cost" excludes lands + response window before the final-chapter
  sacrifice; lands in hand count as mana sources for a search; killing
  an equipped creature removes only its own power) took Pinnacle
  Affinity's flat field from 65.2% to 54.8% on the 50000 grid (n=20
  Bo3) — inside the 50–65 band. The stricter gate (no cell ≥85%) is
  not met: Amulet Titan 90, Goryo's 85, Hollow One 85 remain, and each
  is held by the victim deck's own tracked outlier, not by anything on
  Pinnacle's side. The loop halts here, one code iteration short of the
  formal loop-break, because a fourth iteration would have to reopen
  another deck's primary lane or build below class size. The next lane
  is named: the Amulet primary doc (with a new engine-fidelity lead —
  the Pact family's "pay at your next upkeep or lose" is not enforced).
---

# Pinnacle Affinity band loop — close (2026-09-06)

## Result

| Reading (n=20 Bo3, 50000 matchup grid, offline scorer) | Field |
|---|---|
| Iteration 0 — baseline on `ff8176c` | 65.2% |
| After iteration 1 (`c4f8ca5`, Saga rules) | 55.0% |
| After iterations 2–3 (`25ca7cb` hand-lands, `593901d` equipment split) | 54.8% |

The band ([50, 65]) is met. Weighted WR is not gated. The three cells
still ≥85% after iteration 3: **Amulet Titan 90, Goryo's Vengeance 85,
Hollow One 85**. Every other cell sits at 80 or below (Creatures
Toolbox 80, Boros Ponza 70, the rest 65 and under).

## What moved the field (all fidelity, all class-sized, all pushed with a failing test first)

1. **Saga chapters follow the printed timing** (CR 714.2a/b): first
   lore counter + chapter I on entry, later counters as the precombat
   main phase begins. **"With mana cost {0} or {1}" excludes lands**
   (CR 202.2, typed `has_mana_cost`). **Response window** before the
   final-chapter sacrifice (CR 603.3 / 714.4) so the controller can
   make the Construct in response. Boros Ponza cell 95 → 70; field
   −10.2pp.
2. **A land decision counts the lands in hand as sources** the player
   will have (`ai/mana_planner.py`, `pending_colors`). Diagnosed on
   Goryo's turn-1 fetch; both anchor flips were strictly better
   fetches (4/5c, Living End). Goryo's cell 95 → 85.
3. **Killing an equipped creature removes only its own power**
   (CR 301.5c, `CardInstance.equipment_power_bonus()` in both block
   scorers). Diagnosed on Griselbrand trading with a Plating carrier.
   Anchor unchanged; Goryo's cell held at 85.

## Why the loop halts here

The stop gate's second clause (no cell ≥85%) cannot be met from this
lane:

- **Amulet Titan 90.** Both replayed seeds (s50000, s50500) show
  Amulet's own engine: a Grazer-only keep, a turn-2 Zenith for Dryad
  Arbor with Amulet already in play, Titan on turn 6 into a turn-6
  kill. That is the domain of the active primary doc
  `2026-08-26_amulet_titan_rediagnosis.md`. New engine-fidelity lead
  found here, recorded below, that would make Amulet WEAKER.
- **Goryo's Vengeance 85.** With the fetch fixed the deck now plays
  its printed line (turn-2 Mending, turn-3 Griselbrand, Ephemerate);
  the cell is decided by the keep / assembly residual named in
  `2026-09-05_zoo_band_loop_break.md` (game 2 of every replayed match
  is a mulligan to five; wins arrive on turn 10 on average for a
  turn-3 deck). The RC-3 lane (flat 2-of-3 sets) is falsified.
- **Hollow One 85.** Its gameplan's `mulligan_max_lands: 3` mulligans
  a four-land seven with a one-drop, a two-drop and Vengevine, and the
  deck cycles its own payoff instead of sequencing two cycles into the
  turn it casts it (the same-turn cost-reduction lead from the Zoo
  restart). The first is deck configuration (not touched inside a
  loop); the second is a one-card class.

A fourth code iteration would have to build one of those, i.e. reopen
another deck's lane or build below the ten-card class floor. The rule
says halt and write; this is the write.

## Leads recorded, not built

- **Pact family "pay at your next upkeep or lose" is not enforced.**
  Summoner's Pact resolves as a free tutor (`engine/card_effects.py`
  registry handler); Amulet cast it on turn 1 with one land and never
  paid. Pool: five cards (Intervention Pact, Pact of Negation, Pact
  of the Titan, Slaughter Pact, Summoner's Pact); one registered deck
  runs two copies. Mechanic: a delayed upkeep cost with a lose-the-game
  rider — the engine's delayed-trigger queue and unless-pay framework
  both exist, so the build is a typed `delayed_upkeep_cost` parsed
  once, a delayed trigger registered at resolution, and an AI cast
  gate on next-upkeep mana. Belongs to the Amulet lane; it lowers
  Amulet's number.
- **Granted-ability activation is runner-driven** ("activate whenever
  payable", `GameRunner._activate_granted_token_ability`). The AI
  layer should price it like any other activation.
- **Mulligan land cap is per-deck configuration** (`mulligan_max_lands`
  3 / 4 / 5 across the gameplans) rather than a derivation from the
  deck's curve. Nine decks sit at 3.
- **Kozilek's Command resolves as nothing** (modal "choose two", task
  #40) and kicker is unsupported — Broodscale's side of its cell.

## Standing findings (unchanged from the Zoo loop)

The play gate (`PLAY_VALUE_FLOOR`) and the evaluator's card / mana /
land currency remain the cross-cutting residual; the live sideboard
path is the legacy string table; the card-knowledge tables remain
name-keyed readers.

## Restart condition

Re-open a Pinnacle loop only after the Amulet primary lane and the
Goryo's keep residual have moved; until then the Pinnacle row is at
band and its remaining excess is other decks' deficits.
