---
title: A storm closer held below lethal once a chain has begun — fewer goldfish kills and more decking (FALSIFIED as designed)
status: falsified
priority: diagnostic
session: 2026-10-16
depends_on:
  - docs/diagnostics/2026-10-15_ruby_storm_goldfish_speed.md
supersedes: []
superseded_by: []
tags:
  - ruby-storm
  - storm
  - finisher-timing
  - combo-calc
  - goldfish
  - falsified
summary: >
  Mode A, from the cc-post replay: once a spell has been cast this turn, a
  storm closer (or a tutor that fetches one) with no fuel left in hand
  fires whatever its damage, because `card_combo_modifier`'s storm and
  tutor branches let the develop line count only at storm 0. Dropping that
  gate, so a closer below lethal is held while a later turn can still
  assemble lethal, fails both guards the plan set for it over the 160
  goldfish seeds. Kills fall 101 -> 74, and losses to an empty library
  rise 59 -> 86 (40 seeds turn a kill into a loss, 13 the other way). The
  kills it keeps come sooner (by turn 6: 17 -> 27), but the deck's damage
  accumulates across turns, and a held hand of closers waits for draws. Do
  not re-run this experiment.
---

# A closer held below lethal mid-chain: fewer kills, more decking (2026-10-16)

## The hypothesis

The `cc-post` replay (Ruby Storm vs 4c Omnath, seed 50500, game 1) showed Grapeshot cast after two spells, for 3 into 20.

In `ai/combo_calc.card_combo_modifier`, a closer with no fuel left in hand is scored by `ai.clock.scarce_payoff_commit_ev`: EV(fire now) − EV(develop a larger line). Both finisher branches let the develop line count only at storm 0:
- **Storm branch:** `develop_possible = (storm + 1) < opp_life and storm == 0 and not snap.am_dead_next`.
- **Tutor branch:** the same, with `(storm == 0 or not pays_whole)`.

So once one spell is cast, the closer fires.

**Hypothesis (unit MA in the plan):** the spells already cast are spent whether the closer fires or not, so firing below lethal forfeits only the closer. It should be held while a later turn can still assemble lethal.

## The experiment

- **Patch:** both `storm == 0` terms removed, nothing else. Survival (`am_dead_next`) and the library's chain fuel still gate the develop line.
- **Base:** `90af9a5` (unit RW's head).
- **Measurement:** `tools/goldfish.py "Ruby Storm" -n 80 -s 70000`, on the play and on the draw. These are the 160 seeds every Storm unit has used since the goldfish diagnostic.
- **The setting:** the opponent does nothing, which is the most favourable setting for holding. There is no clock, no disruption, and every later turn is guaranteed.
- **The guards set in advance:** kills must not fall, and decking must not rise.

## Result

| | Base (`90af9a5`) | Held below lethal |
|---|---|---|
| Kills, of 160 | 101 | 74 |
| Losses to an empty library | 59 | 86 |
| Kills by turn 6 | 17 | 27 |
| Median kill turn, play / draw | 10 / 8 | 8 / 7 |

- **Per seed:** 40 kills turn into losses and 13 losses into kills.
- **Seeds both arms kill (61):** the held arm kills 21 sooner and 3 later.

Both guards fail. The speed gain is real but smaller than the loss.

## Why: seed 70010, on the play

Both arms play the same first three turns. On turn 4 the hand is Wish, Grapeshot, Wish and Ral, Monsoon Mage, with no fuel. Ral is cast first in both.

**Base:**
- Turn 4: Ral, Wish, then Grapeshot for 3.
- Turn 6: Past in Flames, and Grapeshot again for 2.
- Turn 7: Empty the Warrens at storm 4, for ten Goblins.
- Turn 8: they attack, 11 → 1. Turn 9 kills.

**Held:**
- Turn 4, after Ral, at storm 1: Grapeshot scores −9.5 and both Wishes −85; the AI passes.
- Turns 5 to 7 pass the same way: one Medallion, one Manamorphose. The hand is three closers and no fuel, so holding them waits for draws, one a turn.
- Turn 8: a nine-spell chain (Past in Flames recasting the graveyard's Wrenn's Resolves) and Grapeshot for 9.
- Turn 10: Grapeshot for 5. Turn 11: the library is empty.

Damage persists from turn to turn, so a closer fired below lethal is progress, not waste:
- Grapeshot fired early goes to the graveyard, where Past in Flames can recast it.
- Empty the Warrens' tokens attack the next turn.

The held line needs one turn whose chain alone deals 20. In most goldfish games the deck chips its way there sooner than it assembles that turn, and the library runs out while it waits. The linear kill fraction in `scarce_payoff_commit_ev` (`min(1, damage / life)`) reads this deck correctly: chip damage counts.

## Do not re-run

Do not re-run "drop `storm == 0` from `develop_possible`" in either branch. What remains open:
- **The speed gain is a lead, not a fix.** The held arm's 21 earlier kills say a hold pays when the next turn's chain is reachable. A hold bounded by what the library and hand can assemble next turn (the projection `ai/combo_chain` makes for this turn) is a different hypothesis, with the same two guards.
- **Seed 50500:** whether the 3-damage Grapeshot cost that cell's game is not established. The goldfish says the general rule is not the defect.
- **Firing well below lethal against a deck that answers it** (a counter, discard, a life-gain swing) is a separate question. It needs BHI's disruption estimate threaded into `develop_reach`, the hook `scarce_payoff_commit_ev` already documents.
