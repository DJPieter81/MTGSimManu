---
title: Ruby Storm at 13% — the storm-0 ritual gate holds the rituals that would dig for the payoff
status: active
priority: primary
session: 2026-10-13
supersedes: []
depends_on:
  - docs/design/rules-foundation-sweep-tracker.md
tags: [diagnostic, outlier, ruby-storm, ai, combo-calc, ritual-gate, root-cause, loop-break]
summary: >
  Ruby Storm is at 13.0% of the field (ct-post; band 40-55). Its recent
  units moved it the wrong way (FB, rules-correct: 29.4 -> 14.2) or not at
  all (CT: -1.1), so the loop-break rule applies. A Bo3 trace of its
  least-interactive matchup (vs Living End, s50000, at da93a86) names the
  turn and the subsystem. On T5 Storm has five lands, Ruby Medallion and
  Artist's Talent in play, two rituals and Valakut Awakening in hand, no
  payoff, and 15 power facing it at 16 life. Both rituals score -990 (the
  hard hold); Valakut Awakening scores -0.16; Storm passes. It dies on T6
  after casting the same rituals at 1 life. The responsible subsystem is the
  storm-0 RITUAL CHAIN GATE in `ai/combo_calc.card_combo_modifier`. It
  hard-holds a ritual whenever no finisher is in hand, and it does not ask
  whether the ritual's mana reaches a dig; the mid-chain gate beside it
  does ask. Game 2 names a second subsystem: the keep. It sent back a
  two-land seven because it counts no creature as an immediate cost
  reducer (Ral's reduction is static, CR 302.6), then kept a one-land six.
---

# Ruby Storm at 13% — the storm-0 ritual gate holds the rituals that would dig

## Why this document exists

The loop-break rule (CLAUDE.md, "Loop-break") applies. Three consecutive units touched Ruby Storm's play without moving its win rate toward its band [40, 55]:

| Unit | Arm | Ruby Storm field WR |
|---|---|---|
| SC (storm copies choose new targets) | `sc-post` | 28.1 → 29.4 |
| FB / FB.2 (stale flashback removed; rules-correct) | `fb-post` | 29.4 → 14.2 |
| CT (Artist's Talent's rummage performed) | `ct-post` | 14.2 → 13.0 |

FB removed play the rules do not allow, and the cell-exact replays recorded in the tracker confirm it. What remains is what Storm's AI earns legitimately: a 27 pp miss. No further Storm code lands until this document names the subsystem.

## The replay

- **Cell:** Ruby Storm vs Living End, seed 50000, matrix-exact (deadline neutralised), at `da93a86`. Storm wins 5% of this cell at `ct-post`. Living End barely interacts with Storm's chain, so it measures Storm's goldfish speed.
- **Commands:**
  - `cell_match_dump_audit.py "Ruby Storm" "Living End" 50000`
  - `cell_match_trace.py ... "Ruby Storm"`

**Game 1** (Living End wins on T6):
- **T2:** Artist's Talent.
- **T3:** Ral, Monsoon Mage.
- **T4:** Wrenn's Resolve, then Ruby Medallion from exile.
  - Both rituals in hand score −990 at every decision.
- **T4, Living End:** casts Shardless Agent and cascades into Living End. Generous Ent, Endurance and Striped Riverwinder return, with 15 power.
- **T5, the divergence turn.** The board:
  - Storm has 16 life, five lands, Ruby Medallion and Artist's Talent in play.
  - Its hand is Pyretic Ritual, Desperate Ritual and Valakut Awakening. There is no payoff in hand.

  The AI's candidates:

  ```
  +23.25 play_land: Scalding Tarn [exile]
   -0.16 cast_spell: Valakut Awakening // Valakut Stoneforge [hand]
 -990.03 cast_spell: Pyretic Ritual [hand]
 -990.03 cast_spell: Desperate Ritual [hand]
  >>> play_land, then PASS (main 1, main 2)
  ```

  Storm passes with five mana open and is attacked to 1.
- **T6:** at 1 life the holds lift (−0.03). Storm casts both rituals, Reckless Impulse, a second Talent and Wrenn's Resolve, and finds no payoff in time.

**Game 2** (Living End wins on T9) is lost to the keep, not to the hold.
- **The mulligan.** Storm sends back a two-land seven: Arid Mesa, Sacred Foundry, Ral, Desperate Ritual, Valakut Awakening, Hex Magic ×2. The stated reason is "no cost reducer and no ritual+cantrip+finisher backup".
- **The keep.** It keeps a one-land six (Scalding Tarn) and sits on one land until T7.
- **The keep rule** (`ai/mulligan.py`, the storm-chain backup gate) counts only non-creature `cost_reducer` cards as reducers. Its comment says a creature reducer "needs to be cast AND flipped/attacked" and that "summoning-sick creatures don't contribute". Neither holds for Ral, Monsoon Mage: "Instant and sorcery spells you cast cost {1} less" is a static ability. It applies from the moment Ral is on the battlefield, and summoning sickness limits only attacking and {T} abilities (CR 302.6).
- So the rule misreads a typed fact. This is the same class unit MK fixed for lands and digs: the keep reads a hand from its typed facts.

### What correct play is on T5

Each ritual costs one less with Ruby Medallion in play (Pyretic Ritual and Desperate Ritual cost 1 and add 3). Each is a noncreature spell, so with Artist's Talent in play it also triggers a rummage (unit CT). So every ritual is mana-positive and digs one card.

Valakut Awakening is an instant: "put any number of cards from your hand on the bottom of your library, then draw that many plus one." Cast after the rituals, it digs two to three more cards.

Two lines are correct. One chains the rituals into Valakut Awakening on T5 to dig for Grapeshot, Wish or Past in Flames. The other casts Valakut Awakening at Living End's end step. Holding all three on an empty-payoff hand facing lethal next turn is the divergence.

## The responsible subsystem

The fault is the **storm-0 RITUAL CHAIN GATE** in `ai/combo_calc.card_combo_modifier` (`role == 'fuel' and storm == 0 and 'ritual' in tags`). It returns `STORM_HARD_HOLD` when all of these hold:

```
not a.has_payoff
and not _has_storm_finisher(card, me)
and flashback_chain_viable(...) == 0.0
and not snap.am_dead_next
```

The gate never asks whether the ritual's mana reaches a dig. The MID-CHAIN gate a few lines below (storm ≥ 1) does: it hard-holds only when `_digs_the_ai_will_make(card, ...)` is empty, and otherwise applies a soft penalty. The two gates encode one rule ("ritual mana empties at the end of the phase, CR 500.4, 106.4, so a ritual is wasted unless it reaches a finisher or a dig") in two different ways. The storm-0 copy is missing the dig clause.

There is a second, smaller gap in the dig count. `_digs_the_ai_will_make` counts the ritual as its own dig only when its own text draws (`_is_real_dig(card)`). A ritual cast under a typed cast trigger that digs (the cast carrier's rummage, unit CT) also digs. Today that dig is not counted.

There is also a contributing valuation: Valakut Awakening scores −0.16. Its draw is net one card. The snapshot has no card-quality term (`ai/activation_ev` documents the same limit), so it cannot see that swapping two held rituals for fresh cards is the dig the hand needs. This is a lead, not the root cause: with the gate fixed, the rituals and Talent's rummages dig before the hand-filter matters.

## The unit this licenses (not built here)

**Rule (AI, from CR 500.4 and 106.4):** a ritual is held at storm 0 only when its mana reaches neither a finisher nor a dig the AI will make this turn. This is the mid-chain gate's rule, with one owner for both gates.

A dig the AI will make includes the ritual itself when a cast trigger in play digs on its cast (a typed `TriggerHead.cast` whose spec draws or rummages), not only when its own text draws.

Then:
- **Class:** every ritual-tagged card (the "ritual" tag over typed `ritual_mana`). It covers every deck that plays rituals beside digs.
- **Tests:** written for the rule.
- **Storm-side test:** the T5 board above.
- **Measurement:** a same-seed arm. The test of the diagnosis is Storm's goldfish cells (Living End, Domain Zoo, Izzet Prowess) moving toward their bands.

## The second unit (the keep; not built here)

**Rule (AI, from CR 302.6 and 601.2f):** a permanent whose static ability reduces the cost of the hand's spells reduces it from the moment it is on the battlefield, creature or not. The storm-chain backup gate reads it from the typed cost-reduction field (unit CR), not from the creature bit.

Changes:
- The comment's premise is replaced by the rule.
- Tests are written for the rule: a creature with a static spell-cost reduction counts as an immediate reducer, and one whose reduction is a triggered or activated ability does not.

## Not the cause (checked)

- **Rules:** 0 violations in this match with the auditor on. The rummage, the flashback grant and the storm copies resolve as printed.
- **The mulligan in game 1:** the kept hand has three lands, a ritual, Ral, Talent and Wrenn's Resolve. In game 2 the keep is the cause; see the second unit.
- **Living End's speed:** it cascaded on T4, a normal speed for it. Storm's goldfish should still win the race on T4 or T5 with this board.

## Outcome (2026-10-14)

Both units this document licensed were built:
- RG0: one rule for both ritual gates, plus the cast-trigger dig;
- KS: the keep counts a static cost reducer.

They are in `5d1e12c` and measured by `rg-post`: **Ruby Storm 12.9 → 12.7**. The T5 decision in this replay now differs: the rituals are no longer hard-held when a dig is in reach. The game is still lost, and so is the field.

So the named subsystems were real defects but not the cause of Storm's miss. Four Storm-targeted changes have now not moved it: SC, CT, RG0 and KS.

**Next diagnostic** (before any further Storm code): measure Storm's goldfish speed, the turn its AI kills an opponent that does nothing, against the deck's real-world kill turn (T3–T4). Then read where the turns are lost: mana sequencing, holding the chain for a payoff it never assembles, or the payoff count. A goldfish removes the opponent's interaction from the measurement, so the gap it shows is the AI's own.
