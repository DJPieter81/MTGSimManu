---
title: Domain Zoo lane loop-break — three opponent-side units without movement; the divergent turn is an engine piece attacking into a losing block
status: active
priority: primary
session: 2026-09-12
depends_on:
  - docs/diagnostics/2026-09-05_zoo_band_loop_break.md
  - docs/design/rules-foundation-sweep-tracker.md
tags:
  - zoo
  - wr-outlier
  - loop-break
  - combat
  - combo
summary: >
  Domain Zoo reads 70.7% at n=60 against a [50,65] band. Three consecutive
  units under the "/loop 50x with improving zoo" protocol (Amulet tutor
  payoff + delivery; one bounce land per fetch batch; mana creatures tapped
  last and no blocker tapped into lethal) each corrected real behaviour and
  moved no Zoo cell on the same seeds. Loop-break. The replay-based root
  cause for the worst cell (Creatures Toolbox, 100 on these seeds, 95 at
  n=60) is one turn: Toolbox assembles Devoted Druid + Vizier of Remedies
  on turn 3 (the engine credits the loop, 80 mana), then on turn 4 the
  CombatPlanner attack path sends Vizier alone into an untapped 4/4, it dies
  in the block, and the engine is gone. The lethal path and the
  send-everything fallback already keep home a creature whose non-combat
  worth exceeds its damage; the planner path returns its plan unfiltered.
  Subsystem: ai/ev_player.py::decide_attackers (planner branch). Zoo's own
  play in every replay was ordinary.
---

# Domain Zoo lane loop-break (2026-09-12)

## The rule that fired

CLAUDE.md loop-break: three consecutive commits on the same outlier deck
without moving its win rate toward the band → halt, replay the worst
matchup, name the exact turn where EV diverges from correct play and the
responsible subsystem, in writing, before any further code.

| Iteration | Unit (commit) | Same-seed pre → post (n=20 Bo3) |
|---|---|---|
| 1 | Land-sacrifice tutor: ability-land payoff; delivery by the land-drop valuation; engine asks the seam per pick (`3332b59`) | Amulet field 22.7 → 21.0; Zoo vs Amulet 80 → 100 |
| 2 | One bounce land per fetch batch (`79aabff`) | Amulet field 21.0 → 23.3; Zoo vs Amulet 100 → 100 |
| 3 | Mana creatures tapped last; no blocker tapped into lethal (`426b088`) | Toolbox field 20.2 → 18.8; Zoo vs Toolbox 100 → 100 |

Every unit is behaviourally correct and kept. None moved a Zoo cell.

## Why the cells do not move: Zoo's field is the tail's defects

Zoo's row (n=60) is 98 Hollow One, 95 Toolbox, 93 Amulet, 90 Goryo's, 83
Affinity, 82 Azorius Blink — every one an under-band deck. Zoo itself, in
all six replays read this session, played ordinarily: Psychic Frog (whose
"Discard a card: put a +1/+1 counter" growth is rules-correct — checked),
Leyline Binding on the opponent's best permanent, Stubborn Denial on the
opponent's payoff. The opponents lose to their own defects, and each defect
is one deck's engine, so a unit on one of them has the radius of one cell.
Iterations 1–2 fixed how Amulet fetches; Amulet still dies to a Frog
because its Titan is exiled and it has no second threat — a 20% deck by its
own field, not a Zoo artefact.

## The divergent turn (replay-based root cause)

`--bo3 "Domain Zoo" "Creatures Toolbox" -s 50000`, game 2, Toolbox on the
play:

- T2: Green Sun's Zenith → Dryad Arbor; Devoted Druid cast.
- T3: Fiend Artisan tutors **Vizier of Remedies** onto the battlefield.
  Druid + Vizier is the deck's declared engine (`DEPLOY_ENGINE` role:
  engines Devoted Druid, enablers Vizier of Remedies) — infinite green mana.
  The engine credits it: the T4 payment log reads "80 mana remaining".
- **T4 — the divergence.** With the engine assembled and an untapped
  Scion of Draco (4/4) across the table, `decide_attackers` sends
  **Vizier of Remedies alone** into it. Zoo blocks (lifespan delta +5.00),
  Vizier dies, the infinite mana is gone. Toolbox then spends its turn on
  Tyvar and a Leyline, tutors Craterhoof a turn later, and loses on T7.

Correct play keeps Vizier home: a 2/1 that attacks into a 4/4 for two
damage is worth nothing, and this 2/1 is half of an infinite-mana engine.

## The responsible subsystem

`ai/ev_player.py::decide_attackers`, the **CombatPlanner branch**
(`plan_attack` → `attack_plan`, accepted when `score_delta + trigger_bonus
> threshold`). Two of the three attack paths already charge a creature's
non-combat worth: the on-board-lethal path keeps home any creature whose
`noncombat_opportunity_cost` (mana production, unbounded-engine
membership, abilities — life-point units) exceeds the damage it adds, and
the send-everything fallback (racing / desperate / vs combo) applies the
same rule. The planner path returns `attack_plan` unfiltered: the
planner's `VirtualCreature.value` is clock-based (`creature_value`), so an
engine enabler with two power is just a 2/1 to it. That is the one place
the three paths disagree, and the replay went through it.

Class: every creature whose worth is not its combat — mana creatures,
unbounded-engine members, activated-ability bodies, equipment carriers —
in every deck; the primitive (`ai/clock.py::noncombat_opportunity_cost`)
already exists and is already the rule on the other two paths.

## Next unit (iteration 4, allowed once this document exists)

The planner path applies the same keep-home rule as its siblings: after
`plan_attack`, drop from the plan any creature whose non-combat worth
exceeds the damage it adds unless the remaining plan is lethal (the
lethal path's own exception). Failing test first: an engine piece whose
`noncombat_opportunity_cost` exceeds its power is not in the returned
attackers when the plan is not lethal; a vanilla body with the same P/T is.
Measure Zoo vs Toolbox and the Toolbox field on the same seeds; the
Broodscale lane (Blade of the Bloodchief carriers) is the second deck
that should benefit.

## What this does NOT change

- Zoo's band verdict stands at 70.7 (n=60) until the tail's engines run.
  Whether [50,65] is the right band for a flat field average over a tail
  this weak is a calibration question for the user, not an engine one:
  the weighted (meta-share) figure is the tournament-relevant one.
- The loop continues past this document only with units named here or
  in the tracker's replay register; the 3-of-3 counter resets on the
  first unit that moves a cell.
