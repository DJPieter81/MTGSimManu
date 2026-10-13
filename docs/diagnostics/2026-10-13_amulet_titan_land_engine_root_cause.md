---
title: Amulet Titan at 25% — its land-from-hand engine resolved as nothing
status: active
priority: primary
session: 2026-10-13
supersedes: []
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
tags: [diagnostic, outlier, amulet-titan, rules-gap, card-flow, root-cause]
summary: >
  Amulet Titan sat at 25.5% of the field (band 45-60), 0% vs Boros Energy and
  Pinnacle Affinity. A Bo3 trace (vs Boros Energy, s50000) names the
  subsystem: the "put a land card from your hand onto the battlefield"
  instruction had no executor. Spelunking's enter trigger (x4) neither drew
  nor put a land, Sakura-Tribe Scout's activation (x2) did nothing, and
  Cultivator Colossus's enter trigger (x1) did nothing; only Arboreal Grazer
  worked, through a card-name registry handler. Fixed by unit SP (the
  card-flow MOVE executor for a land from hand, through the land-entry owner).
---

# Amulet Titan at 25% — its land-from-hand engine resolved as nothing

## The outlier

`po-post` (same-seed n=20 Bo3): Amulet Titan 25.0% of the field, band 45–60.
Its worst cells were 0% vs Boros Energy and Pinnacle Affinity, and 5% vs
Affinity and Domain Zoo.

## The replay

`tools/bo3_trace.py "Amulet Titan" "Boros Energy" 50000`, game 1:

- **T1:** Urza's Saga, then Amulet of Vigor.
- **T2:** Gruul Turf (untapped by Amulet), bouncing the Saga. Green Sun's
  Zenith for X=1 finds Arboreal Grazer, which puts the Saga back. Then a
  second Amulet.
- **T3:** a second Urza's Saga, then **Spelunking**. Hand before: Scout,
  Spelunking, Scapeshift. Hand after: Scout, Scapeshift. Spelunking's
  "When this enchantment enters, draw a card, then you may put a land card
  from your hand onto the battlefield" did nothing: no draw, no land.
- **T4–T9:** with neither the draw nor the extra land, the deck never
  assembles Titan mana. Primeval Titan arrives only on a turn-10 Green Sun's
  Zenith for X=6, and Boros Energy wins on turn 10.

Isolated on a bare board, Spelunking resolves as a cast with no effect.
Sakura-Tribe Scout's "{T}: You may put a land card from your hand onto the
battlefield" is an UNCLASSIFIED activation that returns False. Cultivator
Colossus's enter trigger does nothing.

## The subsystem

The effect grammar types each of these instructions correctly: a MOVE to the
battlefield of "a land card from your hand", optional, sometimes tapped. But
no executor bound that shape, and no legacy handler read it except
Arboreal Grazer's card-name registry entry. Spelunking's "If you put a Cave
onto the battlefield this way, you gain 4 life" was also mistyped: the
performed test dropped its object, so on the dispatcher any land would
have gained the life.

The "lands you control enter untapped" static already worked, through
`LandManager.apply_lands_enter_untapped`.

## The fix (unit SP)

- `effect_executors.land_from_hand` / `_put_land_from_hand`: the controller
  picks a land from hand (A35). It enters through
  `LandManager.put_land_onto_battlefield`, the land-entry owner generalized
  from the search put, so Amulet of Vigor's untap, a karoo's return and
  "lands enter untapped" all see it. Landfall triggers. Auditor
  `305.4/land_put`: a land put is not a land played.
- The grammar types the performed test's object ("if you put a Cave ... this
  way") as an OBJECT condition on the named action's result. The OBJECT
  evaluator now reads subtypes too.
- AI: the land comes from `activation_ev._choose_land_delivery`, the AI's one
  land valuation.
- Arboreal Grazer's registry handler is retired (registry 78 → 77).

## Not fixed by SP (leads)

- **Sakura-Tribe Scout is resolvable but never activated.** The AI's
  activation scorer (`ai/activation_ev.activation_candidates`) values an
  UNCLASSIFIED card-flow activation only as an impulse draw, so the
  land-put activation (8 pool activations) yields no candidate. This is
  unit SP.2.
- **Scapeshift cast with no payoff line.** In the anchor game (Amulet Titan vs
  Living End, s50000), Spelunking's draw now gives Amulet a fourth land on
  turn 4. The AI then casts Scapeshift for four lands, sacrificing its only
  Valakut for four Crumbling Vestiges, with nothing to spend the mana on.
  This is an AI valuation gap, not a rules defect. The winner is unchanged.
- **Cultivator Colossus** (x1): "If you do, draw a card and repeat this process."
  The loop is refused by the grammar.
- **Untimely Malfunction, Echoing Deeps, Urza's Saga constructs:** other Amulet
  cards were not audited here.

## Measurement

`sp-post` (unit SP alone, same seeds as `po-post`/`gx-post`): **Amulet Titan
24.7 → 35.0 (+10.3)**, every other deck within ±1.0. A cell-exact replay of
Amulet vs Boros Ponza (30 → 60) reproduces both arms; its flips turn on
Spelunking's draw and land. Amulet is still 10 pp under its band. Units SP.2
(the AI activates Scout) and LE (a put land enters by its own rules and
triggers) follow, measured by `le-post`. Details are in the SP, SP.2 and LE
entries of `docs/design/rules-foundation-sweep-tracker.md`.

## Next subsystem (from the SP / LE replays)

A **bounce land is a land only beside another land**. In Living End vs Amulet
Titan s57500 game 3, Amulet kept two Gruul Turfs as its only lands and played
each into a self-return (9 times in the match). The mulligan and the land
drop count a bounce land as a land when no other land is on the battlefield.
Amulet's list runs 7 bounce lands.
