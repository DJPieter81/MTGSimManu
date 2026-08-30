---
title: Azorius Control 28.6% — 83% of planeswalker loyalty abilities resolve as no-ops
status: active
priority: primary
session: 2026-08-30
depends_on:
  - docs/diagnostics/2026-08-30_turn_cap_deflates_control.md
  - docs/diagnostics/2026-08-30_zoo_decklist_hypothesis_falsified.md
  - docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md
  - docs/diagnostics/2026-08-26_decider_loss_root_cause.md
supersedes: []
superseded_by: []
tags:
  - azorius-control
  - planeswalker
  - engine
  - loyalty-abilities
  - calibration
  - aggro-skew
  - wr-outlier
summary: >
  Root cause for Azorius Control's 28.6% field WR (band 45-60). Planeswalker
  loyalty abilities are dispatched by a hand-written substring table in
  `engine/planeswalker_manager.py::activate_planeswalker` keyed on invented
  phrases ("bounce", "brainstorm") rather than printed oracle text. 570 of
  690 parsed loyalty abilities in the card DB (83%) match no branch: the
  loyalty is paid and NOTHING resolves. Azorius Control's highest-priority
  card — 4x Teferi, Time Raveler, gameplan priority 24.0, `always_early` —
  has BOTH abilities in the dead set, and the AI activates them ~4x per game,
  killing the walker via SBA for zero effect. Routing just the -3 to the
  existing bounce branch moves Azorius 21.2% -> 32.5% (n=80 Bo1, same seeds,
  every one of four matchups improves) and 4/5c Control 39.3% -> 50.0%
  (n=28). The defect is archetype-asymmetric in exactly the direction of the
  field's calibration error: all 8 decks with mainboard planeswalker exposure
  are control/ramp/midrange; every over-performing aggro deck has zero.
  RULES OUT coloured-mana availability (perfect-mana A/B: 22.2% -> 22.2%,
  per-seed winners identical) and mulligan colour-soundness. CONTAINS A
  RETRACTION made before publication: "creature-land animation is inert" was a
  probe artefact.
---

# Azorius Control — the loyalty abilities do nothing

## Symptom and starting point

Azorius Control sits at **28.6% field WR** against a band of roughly
[45-60], the largest genuine calibration outlier left after the turn-cap fix
(`docs/diagnostics/2026-08-30_turn_cap_deflates_control.md` took it 21.0 ->
28.6 and explicitly recorded that the deck was still broken).

Game-1 baseline used throughout this document — Bo1, `MTG_LLM_DECISION_
SCORER_OFFLINE=1`, seeds 50000 step 500, quiet box:

| opponent | Azorius Bo1 WR (n=20) |
|---|---|
| Domain Zoo | 3/20 |
| Boros Energy | 3/20 |
| Affinity | 6/20 |
| Dimir Midrange | 5/20 |
| **total** | **17/80 = 21.2%** |

## The mechanism

`engine/planeswalker_manager.py::activate_planeswalker` pays the loyalty cost
first and then dispatches the effect through a chain of substring tests
against `effect_desc`, which is the **raw printed oracle text** of the
ability (`engine/player_state.py::_parse_planeswalker_abilities` returns
`(loyalty_change, desc)` straight from the `[+N]:` regex):

```python
loyalty_change, effect_desc = ability_info
pw_card.loyalty_counters = new_loyalty        # cost is paid HERE
...
elif "bounce" in effect_desc and "draw" in effect_desc:
    # Teferi -3: bounce target nonland permanent AND draw a card
```

No Magic card prints the word "bounce". Nor "brainstorm", nor "cast
sorceries as flash", nor "return land from graveyard", nor "exile opponent
library" — five of the fifteen branches are keyed on vocabulary that cannot
occur in oracle text. When no branch matches, the function simply falls off
the end: **the loyalty is spent and nothing happens.**

### Census (whole DB, and the registered decks)

Replicating the branch chain exactly over every card with a loyalty value:

```
DB-wide: 570/690 parsed loyalty abilities hit no dispatch branch (83%)
```

In the 25 registered decks, 14 of 20 distinct loyalty abilities are dead:

| card | ability | status |
|---|---|---|
| **Teferi, Time Raveler** | `[+1]` Until your next turn, you may cast sorcery spells as though they had flash | **dead** |
| **Teferi, Time Raveler** | `[-3]` Return up to one target artifact, creature, or enchantment to its owner's hand. Draw a card | **dead** |
| **Karn, the Great Creator** | `[+1]`, `[-2]` | **both dead** |
| **Tyvar, Jubilant Brawler** | `[+1]`, `[-2]` | **both dead** |
| **Grist, the Hunger Tide** | `[+1]`, `[-2]`, `[-5]` | **all dead** |
| Wrenn and Six | `[+1]` Return up to one target land card from your graveyard to your hand | **dead** (`-1` damage works) |
| Ugin, Eye of the Storms | `[-11]`, `[0]` | dead (`+2` works) |
| Ashiok, Dream Render | `[-1]` | dead |
| Teferi, Hero of Dominaria | `[+1]`, `[-3]`, `[-8]` | all three work |

Teferi, Time Raveler's **static** ability (opponents cast only at sorcery
speed) is unaffected — it lives in the `Tag.SORCERY_SPEED_LOCKOUT` registry,
not here. Only the loyalty abilities are dead.

### Why Azorius Control is the deck this destroys

`decks/gameplans/azorius_control.json` gives **Teferi, Time Raveler the
highest card priority in the deck (24.0)** and lists it in `always_early`.
The deck plays 4 copies. Both of its loyalty abilities are in the dead set,
so the AI casts its most-prioritised card and then repeatedly activates a
no-op — the `-3` at 3 loyalty kills the walker outright.

Measured: with the no-op activations refused rather than executed, **321
refusals across 80 games — roughly 4 dead activations per game.**

### Replay evidence — `--verbose "Azorius Control" "Domain Zoo" -s 53500`

Azorius is *winning* this game at turn 9: 24 life to Zoo's 11, board swept,
Zoo's early Psychic Frogs answered. It then loses 24 -> 0 in four turns.

```
╔══ TURN 11 — Azorius Control (P1) ══
║ Life: Azorius Control 22  |  Domain Zoo 9
║ Hand: 0 cards   Lands: 8
║ Domain Zoo board: Psychic Frog (1/2) [tapped], Scion of Draco (4/4)

T11 P1: Cast Wrath of the Skies (2WW) (X=2)
T11: Isochron Scepter moved battlefield -> graveyard (destroyed)   <- its OWN artifact
T11: Psychic Frog dies
T11 P1: Teferi, Time Raveler [-3] -> Return up to one target artifact,
        creature, or enchantment to its owner's hand. Draw a card
T11: Teferi, Time Raveler moved battlefield -> graveyard (SBA 704.5p: zero loyalty)

╔══ TURN 11 — Domain Zoo (P2) ══
║ Domain Zoo board: Scion of Draco (4/4)          <- NOT returned to hand
T11 P2: Attack with Scion of Draco    -> P1 life: 22 -> 18
T12 P1: Cast Consult the Star Charts (1U)         <- only play at 18 life
T12 P2: Attack with Scion of Draco, Territorial Kavu -> 18 -> 9
T13 P2: Attack   -> 9 -> 0
```

The `-3` was aimed at a 4/4 Scion of Draco that costs `{12}` to recast —
returning it to hand is, for that card, permanent removal. Instead the walker
paid 3 loyalty, died to SBA, and the Scion killed Azorius three turns later.
**This is the exact turn where EV diverges from correct play.**

## A/B — routing one ability to a working branch

Arm B routes Teferi's `-3` to the existing "bounce + draw" code (player 0
only, so the arms differ in exactly one thing). Same seeds, same engine
otherwise:

| arm | Azorius total | Zoo | Boros | Affinity | Dimir | bounces |
|---|---|---|---|---|---|---|
| baseline | **17/80 (21.2%)** | 3/20 | 3/20 | 6/20 | 5/20 | 0 |
| Teferi -3 resolves | **26/80 (32.5%)** | 5/20 | 6/20 | 8/20 | 7/20 | 123 |

**+11.3pp from one ability of one card**, and it improves in **all four**
matchups — the consistency across four independent opponent buckets is what
carries this, not the headline number (n=80 alone is roughly +/-9pp).

### It is the missing EFFECT, not the wasted loyalty

A control arm that merely REFUSES the unexecutable ability (mirroring
`ActivationManager.can_activate` rule 9b, "an effect kind the resolver cannot
execute must be refused BEFORE any cost is charged" — an invariant the
planeswalker path violates) moves almost nothing:

| arm | Azorius total | refusals |
|---|---|---|
| baseline | 17/80 (21.2%) | 0 |
| refuse the no-op | 18/80 (22.5%) | 321 |

So the deck is not losing because it throws walkers away; it is losing
because **the answers those walkers represent never happen.** Any fix that
only stops the waste is worth ~1pp and misses the point.

### Generalisation (CLAUDE.md rule 4 — name another deck)

`4/5c Control` runs 3x Teferi, Time Raveler and is currently IN band at
52.0%. Same patch, same seeds:

| arm | 4/5c Control | Zoo | Boros |
|---|---|---|---|
| baseline | 11/28 (39.3%) | 6/14 | 5/14 |
| Teferi -3 resolves | **14/28 (50.0%)** | 6/14 | 8/14 |

Same direction, same magnitude (+10.7pp). This is a field-wide mechanic, not
an Azorius patch.

## Archetype asymmetry — this is the Zoo ceiling seen from the other end

`docs/diagnostics/2026-08-30_zoo_decklist_hypothesis_falsified.md` closed by
saying Zoo's 100% column against every interactive deck and Azorius's floor
"are almost certainly one phenomenon seen from both ends." Mainboard
exposure to this defect:

| dead-ability copies (mainboard) | deck |
|---|---|
| 8 | Eldrazi Tron (4x Karn — both abilities; 4x Ugin — 2 of 3) |
| 6 | 4/5c Control (3x Teferi TR; 3x Wrenn and Six) |
| 5 | 4c Omnath |
| 4 | Creatures Toolbox (4x Tyvar — both abilities) |
| 4 | **Azorius Control** (4x Teferi TR — both abilities) |
| 3 | Azorius Control (WST) |
| 3 | Azorius Control (WST v2) |
| 2 | Eldrazi Ramp |

**Zero mainboard exposure:** Affinity, Amulet Titan, Azorius Blink, Boros
Energy, Boros Ponza, Broodscale Bloodchief, Dimir Midrange, Domain Zoo,
Goryo's Vengeance, Grixis Reanimator, Hollow One, Instant Reanimator, Izzet
Prowess, Jeskai Blink, Living End, Pinnacle Affinity, Ruby Storm.

Every deck that pays for this defect is control, ramp or midrange. Every
over-performing aggro deck pays nothing. That is the aggro-high/control-low
skew, sourced.

**Stated honestly:** exposure count alone does not predict the WR gap — 4/5c
Control carries 6 dead copies and is in band, because it also has a real
board (4x Quantum Riddler, 3x Omnath, 2x Eternal Witness) and a clock.
Azorius is hit hardest because the dead card is its top-priority card AND it
has no other answer of that quality.

## Responsible subsystem

**`engine/planeswalker_manager.py::activate_planeswalker`** — single module,
single function. The effect dispatch is a bespoke substring table; every
other effect surface in this engine (ETB, spell resolution, attack triggers,
dies triggers) is oracle-driven through `engine/oracle_resolver.py`.

## What was RULED OUT, and how

### 1. Coloured-mana availability — FALSIFIED

This was the standing lane: the 2026-08-20 CORRECTION retracted a
"held-removal window" theory and replaced it with "control decks cannot
actually cast their answers." It does not hold here.

Counterfactual: every land the Azorius player controls produces **any**
colour (unit COUNT unchanged, so quantity is still enforced). Same seeds:

| arm | Azorius WR | affordable-but-uncastable hand cards |
|---|---|---|
| baseline | **8/36 (22.2%)** | 870/2752 |
| perfect colour fixing | **8/36 (22.2%)** | 453/2518 |

The patch demonstrably bit (uncastable count nearly halved; individual games
diverged — s52000 ended T20 vs T9, s52500 T11 vs T18) and **the winner of
every single seed was unchanged.** Coloured mana is not why this deck loses.

### 2. Mulligan colour-soundness — ruled out

Instrumented every kept opening hand. Azorius keeps **0%** hands where a
spell's coloured pips are unsupported by the hand's lands (Domain Zoo 28.3%,
Boros Energy 6.8%). The colour check exists (`MulliganDecider.
_combo_set_color_gap`) and is combo-deck-only, which is a real gap for other
decks — but not this one.

### 3. "Creature-land animation is inert" — RETRACTED BEFORE PUBLICATION

An earlier pass here concluded that Hall of Storm Giants (the win condition
Azorius's own gameplan names) could never be activated, because its animate
ability parses as `ActivationEffectKind.UNCLASSIFIED` and
`ActivationManager.can_activate` rule 9b refuses unclassified effects — 85 of
109 animate-self abilities land there, including Mutavault, Inkmoth Nexus and
Celestial Colonnade.

**That is a probe artefact and the claim is false.** `ANIMATE_SELF_UEOT` is
deliberately excluded from the activation subsystem — it is owned by
`parse_land_animation` / `PermanentEffects.animate_land`, reached from
`ai/activation_ev.py::land_animation_candidates`. That parser handles every
manland tested (Hall of Storm Giants, Mutavault, Creeping Tar Pit, Celestial
Colonnade, Inkmoth Nexus, Faceless Haven, Restless Anchorage). Instrumented
live: **19 animations executed in 20 games**, and the 21-32 damage Azorius
deals in the games it wins comes from exactly this.

Recorded rather than deleted because it is the same failure mode the Zoo doc
recorded on 2026-08-30: a census over the wrong entry point produces a
confident false positive. The `ActivationEffectKind` census is not evidence
about land animation.

### 4. The turn cap — already fixed, and not the residue

With `MAX_TURNS = 60` the games do run long (wins at T18/T19/T20 in the
sample) and Azorius genuinely wins the long ones. It is not "losing slower";
it is dying around T11 in the median loss.

## Real defects found on the way that are NOT the cause

Recorded so they are not re-discovered, each with its class size. None of
them is offered as the explanation.

### A. The two land-colour representations disagree (126 lands)

`CardTemplate.produces_mana` (a colour SET, read by `ai/mana_planner.py`,
`ai/board_eval.py`, `ai/ev_evaluator.py`, `ai/gameplan.py`, mulligan
colour-soundness and land scoring) and `CardTemplate.mana_units` (colour
options per UNIT, the only thing `CastManager._can_pay_colored_pips` /
`ManaPayment.land_mana_units` will spend) are populated independently, and
`mana_units` wins when non-empty. **126 lands have a colour in
`produces_mana` that `mana_units` cannot pay**, so the AI plans on a colour
the engine then refuses. Verified end-to-end: `{W}` is uncastable off Mystic
Gate + Island; `{B}` off Sunken Ruins + Island.

Sub-classes:

| n | cause | registered-deck copies |
|---|---|---|
| 43 | a plain coloured tap line LOST a length tie to a `{C}` line in `detect_land_mana_units` (`if len(units) > len(best)`) — the painland / Verge cycle | Grove of the Burnwillows 7, Shivan Reef 2, Underground River 2 |
| 30 | coloured line carries an additional MANA cost (filter lands) — correctly skipped by the "additional costs are not always-available" rule, but then nothing narrows `produces_mana` | Mystic Gate 1, The Mycosynth Gardens 2 |
| 26 | spend-restricted coloured line (Cavern of Souls) — correct to drop from units, still wrong in `produces_mana` | Cavern of Souls 1 |
| 27 | other | Crumbling Vestige 4, Spire of Industry 3, Gemstone Caverns 2 |

Class A is an unambiguous engine bug with a one-line cause. The invariant to
pin: *a land's `mana_units` must be able to pay every colour its
`produces_mana` advertises, or `produces_mana` must be narrowed to match.*
Ruled out as Azorius's cause by the perfect-mana A/B above, but it is real
and it hits Affinity, Dimir Midrange, Eldrazi Ramp and Broodscale.

### B. Opponent-turn-only effects are fired on the caster's own turn

Orim's Chant ("Target player can't cast spells this turn"; kicked, "creatures
can't attack this turn") is the **most-cast card in the Azorius deck against
Domain Zoo at 2.50 casts/game** — every one of them in Azorius's own Main 1,
where the effect expires before the opponent ever gets priority. Seed 50000
shows the Isochron Scepter lock being assembled and then fired on the
caster's own main phase on T9, T10, T11 and T12:

```
T10 P1: [Mana] Tap Plains->W, Hall of Storm Giants->U (paying for Isochron Scepter)
T10 P1: Cast Orim's Chant (W)
T10 P1: Isochron Scepter copies Orim's Chant
T10 P1: Orim's Chant silences P2 this turn        <- during P1's OWN turn
```

The reactive-only gate in `ai/ev_player.py` DOES list Orim's Chant
(`gameplans/azorius_control.json: reactive_only`), but the survival override
(`is_dying = snap.am_dead_next or snap.opp_clock_discrete <= dying_opp_clock`)
lets it through — correct for removal, which still kills the attacker when
cast in your own main phase, and worthless for an effect whose entire window
is the opponent's turn.

Class: 31 fogs + 5 silence effects + 2 "creatures can't attack this turn" =
**~36 cards** whose benefit lands only on the opponent's turn. Note the
19-card "creatures can't block this turn" family is the MIRROR and is
correctly cast on your own turn, so the rule has to be phrased on *which turn
the effect's benefit lands*, not on "reactive".

Not measured for WR impact — offered as a lead, not a finding.

### C. An X-cost sweeper prices its own board at zero

Seed 53500 T11: `Wrath of the Skies` at X=2 destroyed the caster's own
Isochron Scepter (mv 2) to kill one 1/2 Psychic Frog, while the 4/4 Scion of
Draco that actually killed Azorius survived (mv 12, unreachable at any X the
deck can pay). Related to but distinct from the 2026-07-06 X-wipe gate
finding, which is about spending the sweeper too early rather than about
counting your own losses in the X choice.

## Proposed fix

**Rule, phrased on the mechanic:** *a planeswalker loyalty ability's effect
resolves from its printed oracle text through the same oracle-driven effect
resolver every other effect surface uses; an effect the resolver cannot
execute is refused before the loyalty is paid (`can_activate` rule 9b).*

**Class size:** 690 parsed loyalty abilities in the DB, 570 currently dead;
20 abilities across 8 registered decks. Far above the 10-card floor. Not a
card fix and not a deck fix.

**Subsystem:** `engine/planeswalker_manager.py` only. The destination
(`engine/oracle_resolver.py::resolve_spell_from_oracle` and the
`engine/target_solver.py` targeting seam) already exists and is what ETB,
spell resolution and triggers use.

**Suggested slicing**, largest effect family first (the same
evidence-ordered discipline as the activated-effect census):

| dead abilities | family | note |
|---|---|---|
| 223 | (long tail, one-off shapes) | refuse, do not approximate |
| 80 | create token(s) | `PermanentEffects.create_token` already exists — wiring |
| 38 | you get an emblem | needs an emblem home |
| 37 | put +1/+1 counters | `put_counter` primitive exists |
| 36 | exile target | target solver exists |
| 35 | draw N | trivial |
| 27 | destroy target | target solver exists |
| 24 | target creature gets/gains | continuous-effects home |
| 22 | search your library | tutor primitives exist |
| 16 | return target ... to hand | `_bounce_permanent` exists — **this is the Teferi slice measured above** |

The 16-card "return target … to its owner's hand" slice is the cheapest
first cut: the primitive exists, the A/B above is already measured, and it
covers Teferi, Time Raveler and Wrenn and Six's `+1`, i.e. 19 mainboard
copies across 6 registered decks.

**Failing tests to write first** (rule-phrased, no card names):

* `test_loyalty_ability_returning_a_permanent_moves_it_to_owners_hand`
* `test_loyalty_ability_the_resolver_cannot_execute_costs_no_loyalty`
* `test_loyalty_effect_dispatch_reads_printed_oracle_text_not_a_phrase_table`
  — a census-style guard pinning the number of loyalty abilities that reach
  no branch, so the count can only shrink (the same ratchet shape as
  `tools/check_card_name_registry.py`).

**Not implemented in the session that wrote this document.** The session's
standing instruction forbade running the full test suite (two agents working
concurrently), and `activate_planeswalker` is a hot engine path that must not
land unvalidated. The diagnosis, the A/B and the slicing were that session's
deliverable.

### IMPLEMENTED — the return-to-hand slice

The rule and the first slice landed as specified. What changed:

* `oracle_parser.parse_loyalty_abilities` classifies every printed `[±N]:`
  line ONCE at DB load into `CardTemplate.loyalty_abilities`
  (`{slot: LoyaltyAbility}`), with a closed `LoyaltyEffectKind` set and an
  explicit `UNCLASSIFIED` escape hatch — the same shape and discipline as
  `ActivationEffectKind`.
* `PlaneswalkerManager.activate_planeswalker` dispatches off the typed
  `effect_kind` and **refuses `UNCLASSIFIED` before charging loyalty**
  (rule-9b parity), returning `bool`.  `resolvable_ability_slots` narrows
  the AI's menu in `game_runner._activate_planeswalkers`, so a refused line
  is never *offered* — refusing only at resolution time would still burn
  the walker's one activation per turn.
* `RETURN_TO_HAND` resolves through `target_solver.enumerate_legal_targets`
  on the printed `TargetRequirement` and the `zone_mgr` funnel.  Riders
  outside `{draw N}` refuse the whole ability rather than half-execute it.
* Ten of the fifteen original dispatch branches were keyed on vocabulary
  that occurs on ZERO cards; the census confirmed 0 hits each and they were
  deleted.  Five live branches kept their exact predicates, moved to load
  time.  A differential census over all 22,470 cards shows the ONLY
  classification change is the 10 newly-live return-to-hand abilities.
* Ratchet: `tools/check_loyalty_dispatch.py` + `loyalty_dispatch_baseline.json`
  pin the unclassified count (**576 → 564** measured on this branch, over
  698 parsed abilities); it fails on growth AND on a stale baseline.
* Incidental: `target_solver._BATTLEFIELD_COMPOUND` gained the three-type
  "target artifact, creature, or enchantment" phrase (13 cards in the pool,
  including March of Otherworldly Light).  Without it Teferi's `-3` parsed
  as artifact-only and could not have bounced the creature the A/B measured.

Newly live (10): Teferi Time Raveler `-3` (+ draw rider), Wrenn and Six `+1`,
Jace TMS `-1`, Jace Unraveler `-3`, Jace the Living Guildpact `-3`, Liliana
Death Mage `+1`, Liliana the Necromancer, Nissa Vital Force, Tamiyo Collector
of Tales, Tezzeret Master of the Bridge.

Still refused in this family, each for a named reason: Ashiok Nightmare Muse
(trailing exile clause), Mu Yanling Celestial Wind (plural targets), Liliana
the Last Hope (mill rider, untargeted), Wrenn and Seven (emblem rider),
Nahiri, Kaya, Tamiyo Moon Sage, Teferi Temporal Pilgrim.

## Reproducing

All measurements: `MTG_LLM_DECISION_SCORER_OFFLINE=1`, seeds 50000 step 500,
Bo1 via `GameRunner.run_game` with both sideboards passed, `/proc/loadavg`
below 1.5 throughout.

* Census — replicate the branch chain in `activate_planeswalker` over every
  card with a `loyalty` value, feeding it
  `engine.player_state._parse_planeswalker_abilities(t.oracle_text, t.loyalty)`.
* Teferi A/B — monkeypatch `PlaneswalkerManager.activate_planeswalker` for
  `controller == 0` only; when the ability description contains "Return up to
  one target artifact, creature, or enchantment", pay the loyalty, bounce the
  opponent's highest-mana-value nonland permanent and draw. (The patch
  approximates the printed target restriction: it can pick a planeswalker,
  which the real card cannot. It never picked one in these games.)
* Perfect-mana A/B — wrap `ManaPayment.land_mana_units` and, for
  `player_idx == 0`, return the same number of units with every colour
  option. Wrapping `land_mana_units` (not `_can_pay_colored_pips`) keeps
  `can_cast` and the actual payment consistent.
* Land-animation check — `engine.oracle_parser.parse_land_animation` on the
  manland, and a counter on `PermanentEffects.animate_land`. Do NOT use the
  `ActivationEffectKind` census; it is the wrong entry point for this
  mechanic.
