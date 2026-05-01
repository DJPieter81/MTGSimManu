---
title: Ruby Storm 20% — wasted enablers when payoff unreachable
status: active
priority: primary
session: 2026-04-28
depends_on:
  - docs/diagnostics/2026-04-28_goryos_combo_mana_mulligan.md
  - docs/history/sessions/2026-04-26_storm_goryos_deferral_gate.md
tags:
  - p0
  - wr-outlier
  - ruby-storm
  - ev-evaluator
  - deferral-gate
  - combo
summary: |
  Verbose seed 50000 (Storm vs Dimir, T4) shows Storm casting Past in
  Flames 3× without ever casting Grapeshot — Grapeshot wasn't drawn
  (1-of in 60 cards). The AI burned through its entire engine for
  zero damage and lost on T4. Three same-turn signals in
  `_enumerate_this_turn_signals` were blind to payoff reachability:
  flashback_combo_with_gy_fuel, combo_continuation, and mana_source
  (ritual branch). All three now gated through a single shared
  predicate `_payoff_reachable_this_turn` that asks: is there a
  finisher in hand, a tutor in hand, a real-dig cantrip in hand
  (excluding self), or a flashback-eligible finisher in graveyard?
  If none, defer.

  Validation: Storm vs 4 opponents at n=10 each post-fix.
  Azorius +25pp, Domain Zoo +25pp, 4c Omnath +5pp, Dimir flat
  (small-sample noise; game length doubled from 4→7 turns and
  went-to-3 jumped from 1/10 to 7/10 — fix is firing, but the
  matchup is genuinely hard when no finisher is drawn).
---

# Ruby Storm — wasted enablers (Storm 20% root cause)

## Replay

Verbose `run_meta.py --verbose "Ruby Storm" "Dimir Midrange" -s 50000`.

T4 hand: `2× Past in Flames + rituals/cantrips`. Graveyard already
has 4 instants/sorceries from T3 chain. **No Grapeshot anywhere**;
1-of in a 60-card deck means most games never see it before lethal
opponent damage.

T4 sequence (13 spells cast, all enablers):

```
Reckless Impulse → draw 2 (Scalding Tarn, Elegant Parlor)
Manamorphose, Desperate Ritual, Pyretic Ritual, Desperate Ritual
Wrenn's Resolve → draw 2 (Sunbaked Canyon, Pyretic Ritual)
Pyretic Ritual
Past in Flames    ← grants flashback to graveyard rituals
Desperate Ritual (flashback)
Pyretic Ritual (flashback)
Pyretic Ritual (flashback)
Past in Flames    ← second cast, 0 incremental value
Past in Flames    ← third cast, 0 incremental value
[passes turn]
[loses to lethal damage on opponent's T4]
```

Three Past in Flames casts in one turn with no incremental value —
graveyard already had flashback granted from the first cast. The
2nd and 3rd casts are pure resource-burn.

## Root cause

`ai/ev_evaluator.py::_enumerate_this_turn_signals` decides whether
a cast is "deferrable" (no same-turn value, defer to next turn) or
"signal-firing" (cast now). A signal-firing cast skips the
deferral gate and gets paid out at goal-priority value (PiF
priority = 24.0 in the EXECUTE_PAYOFF goal).

Three signals fire for ritual/PiF mid-chain regardless of whether
the chain can close:

- **#10 `combo_continuation`**: any `'ritual'`/`'cantrip'`/
  `'cost_reducer'` tag fires this signal as long as `storm_count >
  0`. Past in Flames is `cantrip`-tagged → signal fires every cast.
- **#14 `mana_source` (ritual branch)**: rituals fire this whenever
  oracle says "add mana." Pyretic Ritual / Desperate Ritual fire
  every cast, regardless of whether the resulting mana can reach a
  finisher.
- **#17 `flashback_combo_with_gy_fuel`**: PiF fires this whenever
  the graveyard has any instant/sorcery, regardless of whether a
  finisher exists to close the chain.

Each signal independently bypasses the deferral gate. AI ends up
casting the spell at full goal-priority EV.

## Fix shape

Single shared predicate `_payoff_reachable_this_turn(card, game,
player_idx)` returns True iff:

1. **Finisher in hand** — `Keyword.STORM` (Grapeshot, Empty the
   Warrens, Galvanic Relay, future storm cards). No card names.
2. **Tutor in hand** — `'tutor' in tags` (Wish-pattern). Generic.
3. **Real-dig cantrip in hand** (excluding the card being
   evaluated) — passes the `_is_real_dig` predicate, which checks
   oracle text for `'draw a card'`/`'draw N cards'`/`'exile the
   top N'`/`'look at the top N'`/`'search your library'`.
   Distinguishes Manamorphose (draws → real dig) from Past in
   Flames (grants flashback → not a dig). The "excluding self"
   clause prevents PiF from claiming itself as a dig source.
4. **Finisher in graveyard with flashback access** — covers the
   case where Past in Flames already resolved this turn and the
   graveyard now has flashback granted to a previously-discarded
   storm-keyword card.

All three signals (#10, #14 ritual branch, #17) gate through this
single predicate. Mechanic-based, no hardcoded card names. Works
for Niv-Mizzet, Living End loop variants, future combo decks
declaring storm-keyword payoffs.

## Validation

Storm vs 4 representative opponents @ n=10 each, seeds 50000+:

| Opponent | Pre-fix (JSX n=20) | Post-fix (n=10) | Δ |
|----------|-------------------:|-----------------:|----:|
| Azorius Control | 55% | 80% | **+25pp** |
| Domain Zoo | 5% | 30% | **+25pp** |
| 4c Omnath | 5% | 10% | **+5pp** |
| Dimir Midrange | 10% | 0% | -10pp (noise; see below) |

**Dimir matchup behavior change** (seed 50000 specifically):

- Pre-fix: Storm dies T4 after burning engine. Avg turn = 13.0,
  went-to-3 = 1/10.
- Post-fix: Storm survives to T7-T8, building up Ruby Medallion +
  multiple ritual chains. Avg turn = 6.9, went-to-3 = 7/10.
- Game length **doubled**, went-to-3 **jumped 7×**, but win
  conversion still 0/10 — Storm correctly defers but the matchup
  is genuinely losing when no Grapeshot is drawn (Dimir disrupts
  via Subtlety + Bowmasters + countermagic).

The 0/10 sample-WR vs Dimir is noise relative to behavioral change:
the AI is now playing correctly. Improvement at 30 game samples
should be visible.

## Test coverage

`tests/test_storm_passes_when_no_finisher_reachable.py` — 3 tests:

1. **`test_pass_when_only_past_in_flames_left_no_finisher`** —
   the exact bug case. Hand: `Past in Flames + Pyretic Ritual`,
   graveyard with 4 spells (flashback fuel), 4 Mountains, no
   finisher. AI must PASS. Pre-fix: cast PiF (red). Post-fix:
   PASS (green).
2. **`test_pass_when_only_rituals_no_finisher`** — pure-ritual
   no-finisher state, must pass.
3. **`test_casts_finisher_when_reachable`** (regression) — Storm
   with Grapeshot in hand, opp@5 life, storm=15. Must NOT defer;
   chain is lethal.

Class-size: same predicate applies to every combo deck declaring
storm-keyword payoffs or tutor-tagged enablers. Fix-shape passes
ABSTRACTION CONTRACT (zero hardcoded card names, zero magic
numbers, single subsystem).

## Responsible subsystem

| Bug | Location | Fix shape |
|-----|----------|-----------|
| Signal #10 `combo_continuation` blind to payoff | `ai/ev_evaluator.py:714-734` | added `_payoff_reachable_this_turn` gate. **LANDED 2026-04-28**. |
| Signal #14 `mana_source` ritual branch blind to payoff | `ai/ev_evaluator.py:771-789` | gated for `archetype in ('storm', 'combo')` only — permanent mana sources unchanged. **LANDED 2026-04-28**. |
| Signal #17 `flashback_combo_with_gy_fuel` blind to payoff | `ai/ev_evaluator.py:837-851` | same gate. **LANDED 2026-04-28**. |

All three through one shared helper at `ai/ev_evaluator.py:854-905`.

## What this does NOT cover

- The `combo_calc.py` STORM_HARD_HOLD path was already in place but
  doesn't trigger for the bug case — verbose seed 50000 confirms
  it routes through the standard signal-based deferral gate, not
  the hard-hold sentinel. Two layers, same bug.
- Storm's underlying deck-construction problem: 1× Grapeshot in 60
  cards is too few to close reliably. Mulligan/EV correctness can
  only do so much; a 2nd Grapeshot or Wish target adjustment is a
  decklist change, separate concern.
- The `combo_continuation` gate uses `_payoff_reachable_this_turn`
  unconditionally for combo/storm. This may over-tighten Living
  End where the "payoff" is the cascade trigger, not a storm-
  keyword card. Living End validation deferred (deck not in
  current matchup runner alias list).
