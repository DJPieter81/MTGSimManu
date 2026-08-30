---
title: Activated-effect coverage census — what the activation subsystem still refuses, and why
status: active
priority: secondary
session: 2026-08-30
depends_on:
  - docs/diagnostics/2026-08-28_creatures_toolbox_replay_diagnosis.md
supersedes: []
superseded_by: []
tags:
  - activation
  - mechanic-class
  - census
  - abstraction-contract
summary: >
  Full census of the 6457 parsed activated abilities by effect kind. 88% are
  UNCLASSIFIED, but 1411 of those are mana abilities that are correctly owned
  by the mana subsystem, so the real executable share of NON-mana activated
  abilities is 773/5046 (15%). Ranks the remaining classes by size and by
  presence in the 25 registered decks, and records two blockers found while
  building the put-counter class that are NOT that class's own fault.
---

# Activated-effect coverage census (2026-08-30)

## Why this exists

Successive tranches made activation COSTS payable (mana/tap → life/sacrifice-
self → sacrifice-another/discard → counter costs). The EFFECT side grew far
more slowly, and `can_activate`'s rule 9b refuses any kind the resolver
cannot execute — so an unclassified effect makes the whole ability inert
*before any cost is charged*. This census measures what is actually left,
so the next class is chosen by evidence instead of by impression.

## Headline numbers

| | abilities |
|---|---|
| parsed activated abilities | 6457 |
| UNCLASSIFIED | 5684 (88%) |
| …of which are mana abilities | 1411 |
| **non-mana abilities** | **5046** |
| **non-mana and executable** | **773 (15%)** |

**The 88% figure is misleading and should not be quoted.** Every one of the
1411 `Add …` abilities carries `is_mana_ability=True` and is reachable
through `ManaPayment` / `mana_units` / `sacrifice_mana_units` — verified
card-by-card, 0 genuinely dead. UNCLASSIFIED is the *correct* verdict for
them: the activation resolver deliberately does not own mana abilities.
This is the same false-positive shape that produced the earlier "Talisman of
Impulse is inert" claim, and it is why this census checks reachability
through other subsystems before calling anything a gap.

## Remaining classes, by size and deck presence

Deck presence counts the 25 registered decks (mainboard + sideboard).

| abilities | cards | in decks | class |
|---|---|---|---|
| 291 | 285 | 2 | CREATE_TOKEN |
| 195 | 179 | 2 | GRANT_KEYWORD |
| 173 | 172 | 1 | GRAVEYARD_RETURN |
| 156 | 155 | 0 | TAP / COMBAT_CONTROL |
| ~148 | ~146 | 2 | **PUT_COUNTER — built 2026-08-30** |
| 120 | 120 | 3 | DESTROY_TARGET |
| 119 | 119 | 1 | REGENERATE |
| 118 | 118 | 1 | TRANSFORM |
| 112 | 112 | 11 | TUTOR (non-hand/battlefield destinations) |
| 93 | 90 | 2 | GAIN_LIFE (fixed) |
| 42 | 42 | 0 | SCRY |
| 34 | 33 | 0 | COUNTER_SPELL |
| 25 | 25 | 1 | MILL |

### The fetchland entry is not the opportunity it looks like

TUTOR's 11 deck cards are almost all fetchlands, and they are **already
live** — `LandManager.crack_fetchland`, reached from `play_land`, not from
the activation subsystem. UNCLASSIFIED is correct for them.

There is a real but small gap underneath: the oracle-derived flag
`has_sacrifice_search_land` is true for 51 lands, while execution is gated
behind `FETCH_LAND_COLORS`, a **38-entry card-name-keyed table** read from
`ai/ev_player.py` and `ai/mana_planner.py`. 13 lands carry the flag with no
table entry; only 2 are in registered decks (Urza's Cave — Amulet Titan;
Sanctum of Ugin — Eldrazi Ramp), and several of the 13 are not fetchlands at
all (Ghost Quarter, Field of Ruin are land destruction). The name table is
an abstraction-contract smell worth retiring on its own merits — the
fetchable types are printed on each card — but the incremental correctness
win is ~2 deck cards, so it is not the highest-value next class.

## Two blockers found while building PUT_COUNTER (not that class's fault)

Recorded here because both will bite the next class too.

### 1. Rule 6 makes one unpayable sibling ability sterilise a whole permanent

`can_activate` rule 6 refuses a permanent outright if **any** of its
abilities has an unpayable cost — deliberately, so the AI cannot pay for
the cheap half of a combo it can never finish.

The consequence is that a fully-built effect class can still be dead on a
real card. **Psychic Frog** (Dimir Midrange, Grixis Reanimator, Instant
Reanimator) has "Discard a card: Put a +1/+1 counter on this creature",
which is now fully parsed, legal-shaped and resolvable — but its second
ability, "Exile three cards from your graveyard: … gains flying", carries
an unpayable `exile` cost, so rule 6 refuses the permanent entirely.

Unblocking it needs an **exile-cards-from-your-graveyard COST** tranche, not
more effect classes. That is a cost-side item and is currently the single
highest-leverage one, because rule 6 makes each unpayable cost sterilise
every other ability on the same card.

### 2. `position_value` is not monotonic in the projected board terms

Measured on one board (Walking Ballista + 6 Islands, opposing Grizzly
Bears), varying one snapshot field at a time:

```
my_power  0 -> 1   position_value  -9.45 -> -49.45     (clock 99 -> 20)
my_power  1 -> 5   rises monotonically      -49.45 -> 11.55
my_toughness 5 -> 6                          46.55 -> 1.88
```

The 0→1 step is the `NO_CLOCK = 99` sentinel scoring an empty board better
than a real, slow clock. Any branch that projects `my_power` up from zero
can therefore read a genuine improvement as a loss. `PUMP_SELF_UEOT` dodges
it with an explicit clock gate; the new PUT_COUNTER branch does not, and on
that specific board a 0/0 Ballista's growth is suppressed.

**Scope note, stated because an earlier draft of this doc over-claimed it:**
the effect is board-dependent, not universal. On the test fixture board a
0-power Ornithopter's growth *is* offered
(`test_growth_is_offered_on_a_body_that_contributes_no_power_yet` passes).
So this is a real discontinuity that suppresses some activations, not a
blanket "growth off zero never fires" rule. Fixing the sentinel touches the
core scoring primitive used by every deck and every decision, so it needs
its own failing test and its own field measurement — deliberately not
smuggled into a mechanic-class commit.

## Recommended order for the next classes

1. **Exile-from-graveyard COST tranche** — cost-side, unblocks whole
   permanents via rule 6 rather than one ability at a time.
2. **CREATE_TOKEN** — the largest remaining effect class (291/285); token
   creation already has a primitive (`PermanentEffects.create_token`), so
   this is wiring rather than new machinery.
3. **GRANT_KEYWORD** (195/179) — needs a continuous-effect home so the grant
   expires correctly; interacts with `continuous_effects.py`.
4. **REGENERATE** (119) and **GRAVEYARD_RETURN** (173) — both are genuinely
   new rules machinery, not wiring.

## Reproducing

The census is a plain scan over `CardDatabase().cards`, bucketing
`ActivatedAbility.effect_kind` and, for UNCLASSIFIED, the leading verb
phrase of `effect_text`. Deck presence joins against
`decks.modern_meta.MODERN_DECKS` (a dict of
`{deck: {'mainboard': {...}, 'sideboard': {...}}}`; DFC decklist names are
`"A // B"` and must be split to match DB keys).
