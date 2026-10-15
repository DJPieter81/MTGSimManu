---
title: Ruby Storm's goldfish — the chain does not end on its finisher, and the deck mills itself
status: active
priority: primary
session: 2026-10-15
supersedes:
  - docs/diagnostics/2026-10-13_ruby_storm_ritual_hold_root_cause.md
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
tags: [diagnostic, outlier, ruby-storm, ai, combo-calc, finisher, goldfish, root-cause, loop-break]
summary: >
  The goldfish diagnostic the 2026-10-13 root cause asked for. Against an
  opponent that never casts, attacks or blocks (tools/goldfish.py, 160
  games at 3e71f87), Ruby Storm kills by turn 4 in 0 of 160 games and by
  turn 6 in 12. It wins 58 and loses 102 to an empty library (CR 704.5b).
  The chain is built but does not end on its finisher. 57% of the turns
  that cast ten or more spells cast no closer; a lethal sum of castable
  finisher casts went untaken in 61 games; 121 of 203 Grapeshots dealt 4
  or less. The responsible subsystem is the storm finisher timing in
  ai/combo_calc.card_combo_modifier. Lethal is one finisher (storm+1,
  storm+2), never the sum of the finisher casts the mana pays (CR
  702.40a). The hold counts fuel by its kind, not by whether casting it
  leaves the mana for the finisher. And once a spell is cast the closer
  fires at once. Unit FS is licensed. Even with every lethal sum taken the
  kills stay late (median turn 7–10), so the pace before the chain is the
  next measurement.
---

# Ruby Storm's goldfish — the chain does not end on its finisher

## Why this document exists

The loop-break rule (CLAUDE.md) applies to Ruby Storm. Four Storm-targeted units (SC, CT, RG0, KS) have not moved it from its 12.5% field win rate (band 40–55). The 2026-10-13 root cause closed with the next diagnostic to run before any further Storm code: measure the AI's goldfish speed against the deck's real-world kill turn (T3–T4), then read where the turns are lost. This document is that measurement.

## The measurement

**Harness:** `tools/goldfish.py` (committed with this document).
- The opponent's deck is 60 Wastes. It plays lands only: it never casts, attacks or blocks.
- 160 games: seeds 70000–70079 on the play and the same seeds on the draw, at `3e71f87`.
- Matrix seeding; deadline neutralised; LLM scorer offline.
- 0 rules violations in 10 games run with the auditor on.

| | Play | Draw |
|---|---|---|
| Kills | 30 / 80 | 28 / 80 |
| Lost to an empty library | 50 | 52 |
| Killed by T4 | 0 | 0 |
| Killed by T5 | 2 | 2 |
| Killed by T6 | 4 | 8 |
| Killed by T8 | 13 | 16 |
| Median kill turn | 9.5 | 8 |
| Mulligans to six | 24 | 24 |

Real-world Ruby Storm goldfishes on T3–T4. The AI kills by T6 in 12 of 160 games and mills itself in 102.

## Where the turns go

### 1. The chain does not end on its finisher

**Big turns end without a closer.**
- 87 turns cast ten or more spells. In 50 of them (57%) neither Grapeshot nor Empty the Warrens is cast.
- Counting Wish as a finisher too, 37 such turns cast no finisher at all:
  - 18 **strand** it: a finisher was castable at a decision earlier in the turn, and at the turn's last decision it no longer was;
  - 17 **mill** mid-chain: the turn drew from an empty library;
  - 2 had no finisher in reach.
- Over turns of six or more spells: 91 strand, 30 mill, 18 no finisher, 1 held.

**Lethal sums go untaken.** An observer recorded, at every main-phase decision, the damage the castable finishers reach this turn when cast in sequence. It used the engine's costs: printed coloured pips plus generic less `rules_query.cost_delta`.
- A storm closer cast with S spells before it deals S+1 (CR 702.40a).
- A tutor cast first adds one to the count and fetches the closer it casts next.
- 76 games had a decision whose reach was lethal. In 61 of them the AI did not fire at some such decision, and 34 of those games were later lost to an empty library.

**Finishers are spent small.** 121 of 203 Grapeshots fire at storm 0–3, dealing 4 or less; 15 fire at storm 18 or more.

### 2. The pace before the chain

The first lethal reach appears on T5–T8. Before it:
- **Mana at a turn's first decision:** T3 1.81, T4 2.52, T5 3.32.
- **T3's casts are mostly digs:** Reckless Impulse 50, Wrenn's Resolve 48, Ruby Medallion 46, Ral 36, rituals 35.
- **Ruby Medallion** is down by T2 in 37 of 160 games.

Even if every lethal reach were taken, the kills stay late:

| If every lethal reach were taken | Play | Draw |
|---|---|---|
| Kills | 45 / 80 | 48 / 80 |
| Killed by T4 | 0 | 1 |
| Killed by T6 | 5 | 16 |
| Median kill turn | 10 | 7 |

So the conversion is the first gap and a measurable one, but it is not the whole of Storm's slowness.

## The divergence, exact turns

**Draw 70048, T7.** Opponent at 20; Storm has two Ruby Medallions in play. Its castable finishers are Grapeshot (exiled by Reckless Impulse) and two Wishes; the sideboard holds Grapeshot and Empty the Warrens.
- **Storm 8, 3 mana:** Wish ({R} under two Medallions), then the sideboard Grapeshot ({R}), then the exiled Grapeshot ({R}): 10 + 11 damage, 21. Lethal.
- **Storm 15, 4 mana:** four net-positive rituals are castable (each {R} for {R}{R}{R}) and score −0.12; the finishers score −30 and −35.
- **The AI's line from storm 15:** Wrenn's Resolve (+0.12), a third Artist's Talent (+14.9), then a third Ruby Medallion (+9.5) at storm 17, which spends the last 2 mana.
- It passes at storm 18 with Grapeshot and both Wishes uncast, and loses on T9 to an empty library.

**Draw 70003, T8.**
- At storm 15, with Grapeshot and Wish castable and the opponent at 19, Storm flashes back Hex Magic with one card left in its library.
- Artist's Talent's rummage draws that card; Hex Magic then draws from an empty library, and Storm loses on its own turn.

**Play 70001, T5.**
- At storm 8 with 3 mana, the exiled Grapeshot is castable for {R}.
- The AI casts Past in Flames with that mana; its flashback grant then has nothing left to pay for it.
- Grapeshot's permission expires uncast. Two Wishes stay in hand for five turns, and the deck mills itself on T10.

## The responsible subsystem

The storm finisher timing in `ai/combo_calc.card_combo_modifier`: the STORM FINISHER and TUTOR-AS-FINISHER-ACCESS branches. It has three faults.

**(a) Lethal is one finisher.**
- The closer fires at `storm + 1 >= opp_life`; the tutor fires at `storm + 2 >= opp_life`.
- The damage a turn's finishers deal together is never counted. That damage is the sum over the finisher casts the mana pays, each copying itself once for every spell cast before it (CR 702.40a).
- At 20 life, one closer needs storm 19, but two need storm 9.

**(b) The hold counts fuel by its kind.**
- A finisher is held, at a penalty, while chain fuel (or a tutor with access) is in hand.
- It does not ask whether casting that fuel leaves the mana to cast the finisher. Mana empties between steps (CR 106.4, 500.4), so a fuel play that spends the finisher's mana ends the chain without it.
- The stranded turns above are this fault.

**(c) Once a spell is cast, the closer fires at once.**
- With no fuel in hand, the build-or-fire comparison treats developing a larger line as unreachable whenever storm > 0.
- So the closer fires for storm+1, however small: 121 Grapeshots at storm 3 or less.
- This spends the scarce finisher the later lethal turn needs.

## Secondary findings (separate units; not built here)

**AI:**
- `ai/effective_cmc` lets on-board reducers reduce coloured pips ("Storm rituals become free under enough medallions"). A generic reduction reduces only the generic component (CR 601.2f), so a ritual reads as free under two Medallions.
- No draw in a chain is valued against the library's end. 17 of the 37 big turns without a finisher ended in a draw from an empty library (CR 704.5b, 121.4).
- The rummage choice (`ai/discard_advisor.draw_improves_on_discard`) ignores the library's end and gives up chain pieces (Ral, rituals).

**Runner:** loyalty abilities are activated after the main phase's spells (`_activate_planeswalkers` follows `_execute_main_phase`). Ral, Leyline Prodigy's +1 ("instant and sorcery spells you cast cost {1} less") therefore never serves the turn's chain. It expires as Storm's next turn begins.

**Engine:**
- Ral, Monsoon Mage's lost flip deals 1 damage to Ral, not to its controller; the card says "Ral deals 1 damage to you". Ral dies after three lost flips in a turn.
- A won flip transforms Ral in place rather than exiling it and returning a new object (CR 400.7, 712).
- Spliced text is performed only when the host spell is itself a ritual. Hex Magic with a spliced Desperate Ritual adds no mana (CR 702.47b).
- The engine, not the AI, decides to splice.
- A cost reduction is applied to the splice cost apart from the spell's total cost (CR 601.2f).

## The unit this licenses

**Unit FS (AI, from CR 702.40a, 106.4, 500.4):**
- A storm finisher fires when the finisher casts its mana pays sum to lethal.
- Until then it is held only while a castable play grows the chain and still leaves the mana to cast it.

**Owner:** one AI primitive, the reach of this turn's finisher casts (a closer, or a tutor and the closer it fetches, in sequence, within the mana). Both branches read it, the lethal test and the hold alike.

**Class:** every storm finisher (17 pool cards with the keyword) and every tutor whose pool holds one. The hold rule is the same for both.

**Storm-side test:** the draw 70048 T7 board.

**Measurement:** the goldfish, plus a same-seed arm. Its bound from the observer is at most 35 more goldfish kills in 160, mostly on T6 or later.

## Not the cause (checked)

- **Rules:** 0 violations in 10 audited goldfish games.
- **The opponent:** it does nothing, so every turn measured is Storm's own play.
- **The mulligan:** 30% mulligan to six on either side. The decking losses come from kept hands and long games.

## Next measurement

The pace before the chain. Even with every lethal reach taken, 1 of 160 games kills by T4.

Measure the earliest turn a full search over the same seeded draws could kill. That splits the remaining gap between the list as the engine implements it and the AI's sequencing of turns 2–4: digs cast where a Medallion and rituals would build the chain.
