---
title: "Card-interaction graph and a generic combo line planner"
status: active
priority: secondary
session: 2026-11-08
depends_on: [docs/design/rules-foundation-sweep-tracker.md]
tags: [ai, combo, graph, planner, proposal]
summary: "Every combo deck in the field kills about three turns late in the goldfish (median turn 6.5-7.5, Goryo's 11) against real kill turns of 2-4. Each replay names a different local mistake, and the common cause is that the AI scores one card at a time and never sees a multi-step line (bin the target, then reanimate; chain, then Wish for the closer, then flash it back). Proposal: a typed card-interaction graph derived at load from the parsed EffectSpecs (no names), and one deterministic line planner over it that scores a play by how far it advances the shortest line to a kill or a lock. It would replace per-deck JSON roles and scattered relation predicates."
---

# Card-interaction graph and a generic combo line planner

## Why

- **The combo-speed census** (tracker, 2026-11-08): Amulet, Broodscale, Creatures Toolbox, Grixis Reanimator, Hollow One, Instant Reanimator, Living End and Ruby Storm all goldfish to a median kill turn of 6.5–7.5. Goryo's Vengeance is at 11. Their real kill turns are 2–4.
- **Each replay names a local mistake:**
  - Past in Flames cast with no mana left to flash anything back;
  - Thoughtseize aimed at an empty plan;
  - Abhorrent Oculus exiling the binned Archon;
  - Ephemerate before combat on a hasty reanimated creature;
  - Primeval Titan fetching no haste land.
- **The common cause:** the AI scores one card at a time.
  - Relations between cards are recomputed ad hoc by about a dozen predicates (`deck_can_return`, `_tutor_has_payoff_access`, `flashback_chain_viable`, `self_discard_outlet_targets`, `_choose_land_delivery`, …).
  - Part of each deck's plan is hand-declared in gameplan JSON. The Grixis Reanimator gameplan was a stub until unit HAS.

## Census of relation types in the pool (typed tags at load, 22,738 cards)

| Relation (edge) | Pool cards | Example edge |
|---|---|---|
| tutors-for (library → hand) | 393 | Summoner's Pact → Primeval Titan |
| fetches-from-outside-the-game | 42 | Wish → Grapeshot |
| returns-from-graveyard (→ battlefield) | 422 | Persist → Archon of Cruelty |
| grants-graveyard-casting | 178 | Past in Flames → Desperate Ritual |
| reduces-cost-of | 448 | Ruby Medallion → red spells |
| produces-mana-for (ritual) | 165 | Desperate Ritual → the chain |
| puts-into-graveyard (filler) | 626 | Faithless Looting → Archon |
| storm payoff | 17 | Grapeshot |
| cascade-into | 18 | Shardless Agent → Living End |

Every type is mechanic-sized.

## Design

1. **Graph (`ai/card_graph.py`, built once per deck at match start, deterministic):**
   - Nodes are the cards of the deck and its sideboard.
   - Edges are typed relations derived from the parsed `EffectSpec`s and typed `CardTemplate` fields: the filter a tutor searches with, a returner's graveyard target requirement, a reducer's spell filter, a filler's zone move.
   - No names, no regex at runtime.
   - Each edge records the cost to traverse it (mana, cards, life) and the zone change it performs.
2. **Line planner (`ai/line_planner.py`):**
   - From the current state (hand, graveyard, exile permissions, mana, storm count), it runs a bounded search over the graph for the cheapest sequence that reaches a win condition: lethal damage, a lock, or a deck-declared payoff state.
   - It reuses `finisher_reach`, `combo_chain` and the cost estimate (`effective_cmc.chain_cost`) for arithmetic.
   - Output: the next play on the best line and the line's expected turn.
3. **Scoring hook:**
   - `card_combo_modifier` and the main-phase scorer read "this play advances the best line by k steps" in place of the per-card predicates.
   - Each predicate is deleted as the planner covers its case: the single-owner rule.
4. **Deck knowledge:** gameplan JSON keeps only what the graph cannot derive, such as mulligan preferences. Payoffs and enablers become derived.
5. **Models:** none in the sim; the planner is deterministic. A model (Opus tier) is used offline to review the graph's edges against oracle text, as an eval with a golden set.

## Measurement plan

- **Goldfish per combo deck** (n=30, both sides). Success: the median kill turn falls toward the real one, and no deck regresses.
- **Full 26-row arm:** combo rows toward their bands, and fair-deck rows down toward theirs.
- **Pins:** the anchor and digest, replayed winner flips, the rules auditor at 0 violations.

## Steps

1. Graph construction plus a census test: edge counts per type, with a sample of edges checked against oracle text.
2. A read-only planner that logs its best line next to the AI's actual choice in goldfish traces, to measure disagreement before it decides anything.
3. Hook the planner into one decision family (reanimation: fill, then return), measure, and delete the predicates it replaces.
4. Extend it to tutors and chains (Storm, Amulet), then to cascade.

## Progress (2026-11-08)
- **Step 1 is built** (`7520cba`), and **step 2 is built, read-only** (`d37390a`, `22bd0c5`, `41a800d`).
- **The step-2 audit does not justify step 3 for reanimation.** The main phase agrees with the planner on 83–96% of decisions where a castable line exists (tracker, "Graph step 2").
- **Next:** extend the planner to tutor and chain lines, and read colours, before hooking it anywhere.
