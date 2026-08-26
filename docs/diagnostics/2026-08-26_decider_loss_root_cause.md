---
title: Decider losses vs Domain Zoo — soft counters fired into payable taxes + no close-out transition
status: active
priority: primary
session: 2026-08-26
supersedes: []
superseded_by: []
depends_on: [docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md]
tags: [ai, response, counterspell, counter-tax, control, decider, goal-engine]
summary: >
  Bo3 replay root cause for control losing game-3 deciders vs Domain Zoo.
  Primary (verified, mechanical): the response scorer fires "unless controller
  pays {N}" counters with zero awareness of counter_tax_amount — the opponent
  pays from idle mana and the counter achieves nothing (seed 54500 g3: two
  soft counters, both taxes paid, 6 mana + 2 cards + 2 shock life burned for
  zero effect). Secondary (correlated): reactive decks never transition from
  answering to closing — 0 attacks in lost deciders vs 3 in the won one; a
  5-CMC finisher held from the opening hand to death at T9 with 6+ lands.
---

# Decider losses vs Domain Zoo — replay root cause

## Symptom

Zoo vs 4/5c Control, 12 seeds (50000+500k, `_run_pair` path): Zoo 12-0 in
matches; 5 matches reached a deciding game 3 and control lost **all 5**.
Control converts game wins (T8-T13, damage) but never match wins. Same
pattern across the Azorius builds (n=20 field sweep 2026-08-25: Zoo 80-100%
vs every control deck).

## Method

`--bo3` replays at 51000 / 54500 / 55500 (note: the `--bo3` CLI seeds
per-game as seed+game_num while `_run_pair` seeds per-match, so CLI matches
are independent samples, not replays of the scan's matches). Quantified per
decider: control self-damage, creature damage taken, control attacks, casts
while at ≤5 life.

| seed | g3 winner | ctrl self-dmg | creature dmg | ctrl attacks | casts at ≤5 life |
|---|---|---|---|---|---|
| 51000 | **Control T10** | 11 | 4 | **3** | — |
| 54500 | Zoo T9 | 12 | 12 | 0 | Stock Up ×2, Mystical Dispute ×2 |
| 55500 | Zoo T8 | 4 | 19 | 0 | — |

The intra-match contrast at 54500 is the cleanest evidence: **game 1 control
wins T11 by casting its 5-CMC finisher twice on T9 and attacking**; game 3 it
holds the same finisher from the OPENING HAND to death at T9 with 6 lands.

## PRIMARY root cause (verified in the log, mechanical)

Seed 54500 game 3, turn 8, control at 3 life:

```
T8 P2: Cast Wild Nacatl (G)                       [Zoo, 4 untapped lands remain]
T8 P1: Cast Mystical Dispute (2U)                 [control taps 3 of its lands]
T8: Wild Nacatl's controller pays 3 — not countered by Mystical Dispute
```

Same again T9 (Dispute on Ragavan, tax paid). Control burned 6 mana, 2
cards, and 2 life (Sacred Foundry brought in untapped at 5 life to fund the
second Dispute) for exactly nothing — the opponent paid both taxes from mana
it wasn't otherwise using.

**Responsible subsystem: `ai/response.py`, the counterspell candidate loop
(~:479-506).** It filters on `counter_target_kind` and effective cost but
never reads `template.counter_tax_amount`. A soft counter is therefore
scored identically to a hard counter. The engine side of the 1a framework is
correct (the tax is parsed, offered, and paid rationally by the opponent);
the AI side never learned the symmetric EV: **when the targeted spell's
controller can afford the tax, the expected neutralization is ~0** — a
rational opponent who already sank the spell's cost pays the tax whenever
able. The payer-side projection already exists
(`ai/ev_evaluator.project_counter_tax_payment`); the caster-side discount
does not.

Class size: every "unless its controller pays {N}" counter in Modern (Mana
Leak, Mystical Dispute, Metallic Rebuke, Spell Pierce, Condescend, ...) — a
full mechanic class, not a card.

Fix shape (mechanic-phrased, no literals): in the counter-candidate loop,
skip a candidate with `counter_tax_amount > 0` when the stack item's
controller's current untapped mana capacity (plus floating mana) covers the
tax. The tempo-tax cast ("make them pay 3") is a distinct strategic line the
scorer does not model anywhere; refusing the dead counter is strictly better
than current behaviour and leaves that as future work.

## SECONDARY root cause (correlated across replays, own fix required)

Reactive decks never transition from ANSWER mode to CLOSE mode. In lost
deciders control attacks 0 times; in its won decider it attacks 3 times. At
54500 g3 it spent T8+T9 (6 mana/turn) on draw spells + dead soft counters
while holding a castable 5-CMC finisher and 6 untapped-capable lands, then
died to 1-power chip attacks. Zoo deployed 7 threats across the game;
control deployed 5 finite answers and zero clock, making every long game an
attrition loss by construction. Subsystem: `ai/gameplan.py` goal transitions
for reactive archetypes (stabilized → close). Tracked as the follow-up, not
fixed in this diff.

## Minor contributor

Untapped-shock life payment at low life (T8: 5→3 life for a land whose
untapped colours weren't needed that turn) — fed the dead Dispute. The
post-T3 EV delegation in `decide_optional_cost` deserves a clock-pressure
term, but at 2-4 life of impact it is not the decider.

## Verification plan

1. Failing test first: a tax counter is not fired when the targeted spell's
   controller has untapped mana ≥ the tax; still fired when they cannot pay.
2. Replay seed 54500: the T8/T9 Disputes must not be cast (or the game must
   materially change).
3. Zoo vs 4/5c and Zoo vs Azorius Control matchups, n=20 Bo3, before/after.

## MEASUREMENT FOLLOW-UP (2026-08-26, post-fix, n=20 Bo3, pre-fix worktree vs fixed tree, same seeds)

| Matchup | before | after | note |
|---|---|---|---|
| Zoo vs 4/5c Control | 95 / 5 | 100 / 0 | one match flipped the other way — within n=20 noise |
| Zoo vs Azorius Control | 90 / 10 | 90 / 10 | identical breakdown and avg turns |
| Pinnacle Affinity (Rebuke caster) vs Izzet Prowess | 35 / 65 | 40 / 60 | +5pp for the soft-counter holder — one match, directionally right, within noise |

**Honest verdict: the tax fix is decision-level correct but WR-neutral in the
Zoo-control matchups.** The post-fix replay of seed 54500 confirms both dead
Disputes are no longer cast — and control then spends the same turns on draw
spells while still holding its castable finisher, and dies on the same turn.
The card-and-mana waste is gone; the loss is not, because the binding
constraint is the SECONDARY root cause above: no answer→close transition.
The fix stands on its own correctness (a whole mechanic class stops burning
resources on provably-dead responses, with a mild positive signal on the
biggest soft-counter caster) — but bringing Zoo's control matchups toward
band requires the goal-transition work, which is the next diagnosis target.
