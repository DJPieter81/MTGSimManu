---
title: Domain Zoo band loop — two loop-breaks; the residual is the play gate and the evaluator's card/mana currency
status: active
priority: primary
session: 2026-09-05
depends_on:
  - docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md
  - docs/diagnostics/2026-08-31_zoo_obvious_matchup_replay_audit.md
  - docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md
  - docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md
  - docs/design/rules-foundation-sweep-tracker.md
tags:
  - zoo
  - wr-outlier
  - loop-break
  - mulligan
  - goryos
  - hand-attack
  - play-gate
  - currency
summary: >
  The self-paced loop "until Zoo is within WR range" ran three code
  iterations on the 50000 matchup grid (n=20 Bo3, quiet box, offline
  scorer): ETB targeted-removal class + X-bound target legality (Azorius
  Blink cell 90 → 75), turn-scoped opponent restrictions firing on the
  opponent's turn (Azorius Control 80 → 80), and caster-chosen hand
  attacks carrying a this-turn signal and priced by the best card they
  take (Grixis Reanimator 85 → 65, Dimir 70 → 50). Zoo's flat field moved
  67.5 → 67.3 → 65.8: 1.7pp over the loop, under the 2.2pp one-SE
  movement rule, so the loop halts per protocol. The stop gate is not
  met: 65.8% sits just above the 50–65 band and six cells are ≥85%
  (Goryo's 100, Creatures Toolbox 95, Affinity 90, Amulet Titan 85,
  Boros Ponza 85, Hollow One 85). The Bo3 replay of the worst cell names
  the residual subsystem: the reanimator side keeps 4–5 cards every game
  because the combo-path keep rule in ai/mulligan.py demands enabler AND
  payoff in the opening hand; the exact divergence is the pre-turn-1
  mulligan of a 7-card hand holding two copies of the payoff, two hand
  attacks and two lands. RESTART (same day, after the keep/mull
  precondition shipped): three more class fixes (land-type lock as a
  continuous effect, cycling credit / body charge, burn damage from
  the parsed amount + turn lethal + flashback sacrifice pricing) moved
  the field 65.0 → 64.0; second loop-break. The field is inside the
  band but five cells stay ≥85%, each with a named cause; the subsystem
  every restart iteration met is decide_main_phase's PLAY_VALUE_FLOOR
  gate executing mildly negative real plays, with position_value's
  card / mana / land terms priced far below its board terms.
---

# Domain Zoo band loop — loop-break record

## What the loop was

Self-paced loop (`/loop until zoo is within wr range`), plan approved
2026-09-05. Stop gate: Domain Zoo flat field WR in [50, 65] at n=20 Bo3 on
the 50000 matchup grid AND no single cell ≥85% AND reproduced on the
40000 matrix grid, on pushed CI-green code. Loop-break: three consecutive
code iterations without ≥2.2pp field movement (one SE at n=20 field) →
halt and write this doc.

## What happened (all on the 50000 grid, n=20 Bo3, quiet box)

| Iteration | Head | Fix (class-sized, failing-test-first) | Target cell | Field |
|---|---|---|---|---|
| 0 | `0335e9e` | measurement | — | **67.5%** (baseline) |
| 1 | `3d3149e`, `b1e5ba4` | ETB targeted-removal class (141 cards) + X-bound target legality (19); sideboard solver prices permanent-type removal | Azorius Blink 90 → 75 (post field) | — |
| 2 | `114d094` | turn-scoped opponent restrictions fire in the opponent's upkeep (30 instants) | Azorius Control 80 → 80 | **67.3%** |
| 3 | `6487ab6` | caster-chosen hand attacks carry a this-turn signal and are valued by the best card they take (150 cards) | Goryo's 100 → 100 | **65.8%** |

Cells that moved over the loop (iteration-0 → iteration-3 rows): Azorius
Blink 90 → 75, Grixis Reanimator 85 → 65, Dimir Midrange 70 → 50, Instant
Reanimator 55 → 65, WST v2 50 → 55. Every other cell is identical,
including all six cells still ≥85%.

Each fix was verified in-game (the replay that diagnosed it shows the
mechanic firing afterwards), and each moved the cell of the deck that
carries the class — but the field is dominated by the six ≥85% cells,
none of which is held by the subsystems the loop repaired.

## The exact divergence in the worst cell

`python run_meta.py --bo3 "Goryo's Vengeance" "Domain Zoo" -s 50000`
(Zoo 2-1 before and after iteration 3). Game 1, before turn 1:

```
P1 (Goryo's Vengeance) opening hand (2 lands, 5 spells):
  Thoughtseize, Marsh Flats, Goryo's Vengeance, Unburial Rites,
  Flooded Strand, Goryo's Vengeance, Inquisition of Kozilek
→ P1 MULLIGANS (combo path under-covered in 7-card hand
   (best path: 1/2 role buckets; need enabler+payoff))
  New hand: 6 lands, 1 spell                → MULLIGANS (6 lands)
  New hand: Griselbrand, Leyline of Sanctity, Thoughtseize,
            Inquisition, Flooded Strand, Marsh Flats, Unburial Rites
→ P1 MULLIGANS (goal conjunction unreachable — no role-path
   coverage and no castable dig card)
  Keeps 4: Thoughtseize, Archon of Cruelty, Undying Evil + land
```

Games 2 and 3 repeat the shape ("combo path under-covered in 6-card hand
… need enabler"): Goryo's keeps 4, 5 and 5 cards. Zoo's 40 game wins in
the n=20 cell land on turns 5–7 (17 sweeps); a reanimator on 4–5 cards
does not reach a reanimation before that whatever its hand attacks do —
after iteration 3 the same seed shows Thoughtseize on turn 1 and the
match still 1-2.

**Responsible subsystem: the combo-path keep rule in `ai/mulligan.py`.**
The role-bucket conjunction (`enabler` AND `payoff` from the gameplan's
FILL_RESOURCE / EXECUTE_PAYOFF goals) treats a reanimator's discard outlet
as the only enabler, so a hand with two copies of the payoff, two hand
attacks (the deck's whole plan against a creature deck: strip the
counter, resolve the reanimation) and two lands is shipped, and the
6-card hand holding the fatty, the reanimation spell and two hand attacks
is shipped as "goal conjunction unreachable". This is the mirror image of
`docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md` (hands kept
that could not cast their spells): the tightening moved the rule past
the keepable hands. Class: every deck whose gameplan declares a
FILL_RESOURCE goal (Goryo's, Instant and Grixis Reanimator, Living End,
Storm) — the same rule decides all of their opening hands.

This is a judgment rule, so the fix must not be tuned toward the Zoo
number: the test that pins it is "a combo hand holding the payoff, its
protection/disruption and its land drops is keepable when the missing
role is reachable by the deck's own draw/dig density within the payoff's
turn", derived from the library composition (the `p_higher_threat`-style
pool density already in `ai/bhi.py`), not from a keep count.

## The other five ≥85% cells (recorded, not reopened here)

- **Creatures Toolbox 95** — Soul Cauldron / Vizier line, tracked in the
  sweep tracker ("Creatures Toolbox outlier", 2026-09-04); the engine
  assembles and the Craterhoof line fires; residual is the one-of
  toolbox reach.
- **Affinity 90** — audited clean as a race
  (`docs/diagnostics/2026-08-31_zoo_obvious_matchup_replay_audit.md`).
- **Amulet Titan 85** — `docs/diagnostics/2026-08-26_amulet_titan_rediagnosis.md`.
- **Boros Ponza 85, Hollow One 85** — registered mtgtop8 lists, no
  replay audit yet; both are next in the target-selection order (largest
  excess, no tracked root cause) when the loop is restarted.

## Standing findings from the loop (do not re-derive)

- The live sideboard path is the LEGACY string table
  (`engine/sideboard_manager.py::sideboard`, `SB_SOLVER` defaults to
  `old`); the oracle solver runs only opt-in by the held decision in
  `docs/proposals/sideboard_solver.md`. Sideboard-solver improvements do
  not reach a live game until that decision is revisited.
- The archetype-gated holdback hypothesis for Azorius Blink is falsified
  by the log (Blink deploys Solitude at Zoo's begin-combat); do not
  reopen (tracker, iteration 1).
- Beneficial targeted instants (pump / protection / keyword grants) can
  be cast on an OPPONENT's creature to dodge the cleanup discard
  (Goryo's Undying Evil, s50000 G1 T6 and the s50500 anchor game). AI
  target choice for beneficial effects + the cast-anything-rather-than-
  discard branch; class-sized, unbuilt.
- Two WR-anchor winner flips were accepted in iteration 3 after
  anchor-exact before/after replays (hand attack fires T1/T2 taking
  Ephemerate / Tyvar); their losses trace to the decks not casting their
  dig spell after the hand attack on the same turn — sequencing, not the
  fix.

## Restart condition

Restart the loop only after the keep/mull rule above ships with its
rule-phrased test and a Goryo's-side n=20 measurement on the 50000 grid;
then re-run iteration 0 (full field) as the new baseline before picking
the next cell. Until then the Zoo headline (65.8% on the matchup grid)
is an artefact of six defending-side failures, five of them already
named elsewhere.

## Restart (same day) — second loop-break

The user chose to fix the keep/mull residual and restart. The
precondition shipped (`94475d0`: a "target player" discard can target
its caster — engine resolution, AI self-target, mulligan enabler
derivation; Goryo's cell 100 → 85) and the loop restarted from a
**65.0%** baseline (`e37d28b`).

| Iteration | Head | Fix (class-sized, failing-test-first) | Target cell | Field |
|---|---|---|---|---|
| 0 | `94475d0` | measurement | — | **65.0%** |
| 1 | `77f7527` | forced basic land type is a layer-4 continuous effect (symmetric, later lands, no fetch); a lock is worth what it makes uncastable (no coefficient / cap / turn table); overlay not silenced by held instants; a lock carries a this-turn signal | Boros Ponza 85 → 60 | 63.8% |
| 2 | `303e7de` | cycling credits a graveyard body only if the deck can return it (`deck_can_return`, typed `mass_graveyard_return`); cycling charges the body it spends; reminder text never supplies a target zone | Hollow One 85 → 85 | — |
| 3 | `d8cbd0f` | burn damage from the parsed amount (typed `direct_damage_data`, eight readers rewired); lethal is a property of the turn; damage removes only what it kills; a Flashback sacrifice is priced | Izzet Prowess 80 → 75 | **64.0%** |

Three code iterations, 1.0pp of field movement in total (each under the
2.2pp rule): loop-break. The stop gate is not met — the field is inside
the band but five cells are ≥85%, and every one of them now carries a
named cause: Creatures Toolbox 95 (Soul Cauldron line, tracked), Affinity
90 (audited race), Amulet Titan 85 (rediagnosis doc), Goryo's Vengeance 85
(a race the registered list loses to a turn-5 kill after the outlet fix),
Hollow One 85 (the list's speed against a turn-5 kill when its discard
outlets are not drawn).

### The exact divergence, and the subsystem this loop keeps meeting

Every iteration of the restart found the same shape underneath its
cell: **a play with small or negative real EV is executed because the
best candidate runs unless it is below `PLAY_VALUE_FLOOR` (−5.0)**, and
the card / mana / land terms of `position_value` are one to two orders
of magnitude smaller than the board terms, so those plays never reach
the floor. Exact turns:

- Izzet Prowess vs Dimir Midrange s50500, **turn 2**: after the burn
  branch fix, a one-damage burn spell is flashed back by sacrificing the
  only untapped land for one face damage at 17 life — projection +0.03,
  land −0.37 (own-side land-denial term), net **−0.34**, executed.
- Hollow One vs Domain Zoo s50000, **turn 3 / turn 5**: after the credit
  and body-charge fixes, the 4/4 payoff is still cycled for one card:
  draw ≈ +1.7 against a body charge of ≈ −1.0 at one mana; the same
  currency, the same margin.
- Boros Ponza vs Domain Zoo s50000, **turn 6 / turn 9**: a second and
  third copy of the land-type lock cast while one is in play (fixed at
  the family level, zero value while one is out) — the general form is
  the same: a zero-value play is not passed.

**Responsible subsystem: `ai/ev_player.py::decide_main_phase`'s play
gate (`PLAY_VALUE_FLOOR`, the M3 sentinel that tolerates the holdback
penalty's scale) together with the currency of `ai/clock.py::position_value`
(cards, mana and lands priced at `1/opp_life`-order values against board
terms at unit order).** The M3 decision
(`docs/history/audits/2026-05-16_5panel_bo3_audit.md`) is a documented
design choice and was not reopened inside the loop. The principled
reformulation, for the next lane: apply the floor to the holdback
component only — a candidate executes when its real EV (before the
signed holdback term) is non-negative and its holdback-adjusted value is
above the floor — and re-derive `card_clock_impact` / `mana_clock_impact`
so a card and a land are priced in the same units as the body they
become (the hand-denial and stax valuations already do this for the
cards they touch). Both are global, both need the anchor and a full
field to measure, neither is a Zoo-only lever.

### Standing findings added by the restart

- `decks/card_knowledge_loader.get_burn_damage` is a card-name table
  that the decision layer read for damage; it remains only as the
  fallback behind `ai/card_classes.burn_damage`. Its other tables
  (`get_threat_value`, `requires_target`) are the same shape and are
  still read directly.
- A Flashback rider that pays life or sacrifices a creature (Deep
  Analysis, Cabal Therapy shapes) is neither paid by the engine nor
  priced; only the sacrifice-a-land rider is (typed, narrow field).
- `_project_spell`'s removal branch still removes the highest-threat
  creature for non-damage removal without reading the spell's typed
  targeting ceiling (`targeted_removal_data['mv']`); damage removal now
  respects toughness.

### Restart condition (second)

Reopen the Zoo lane only after the play-gate / currency reformulation
above ships with its own anchor-exact verification and a full-field
baseline; the five ≥85% cells will not move on Zoo-specific work.
