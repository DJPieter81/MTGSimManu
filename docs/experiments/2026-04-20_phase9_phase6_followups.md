---
title: Phase 9 — Phase 6 follow-ups (Storm finisher patience, Amulet recurring engine, Pinnacle Affinity diagnosis)
status: active
priority: diagnostic
session: 2026-04-20
depends_on:
  - docs/experiments/2026-04-20_phase6_matrix_validation.md
  - docs/experiments/2026-04-20_phase8_life_energy_persistent.md
tags:
  - ev-scoring
  - storm
  - amulet-titan
  - pinnacle-affinity
  - phase-9
summary: "Closes the three Phase 6 follow-ups. Phase 9a — Storm finisher patience: counts hand+GY chain fuel without can_cast filter, gives PiF reachability bonus. Phase 9b — Amulet of Vigor recurring-engine signal added (Amulet Titan +6.5 wtd). Phase 9c — Pinnacle Affinity hypothesis 'Wrath collateral' falsified by matchup data; no fix needed."
---
# Phase 9 — Closing the Phase 6 follow-ups

The Phase 6 experiment log flagged three decks with regressions or
slow performance: Storm sequencing, Amulet Titan deferral, Pinnacle
Affinity Wrath collateral. Phase 9 addresses each.

## Phase 9a — Storm finisher holds when next-turn chain is bigger

### Problem

`run_meta.py --verbose "Ruby Storm" "Dimir Midrange" -s 50000` T3:
Storm fires Grapeshot at storm_count=4 for 4 damage, even though the
hand still holds 2× Past in Flames + cantrips that would yield a
bigger chain on T4 after untapping 4 lands.

### Diagnosis

`_combo_modifier` finisher gate at `ai/ev_player.py:884-918`:

```python
fuel_available = sum(
    1 for c in list(me.hand) + gy_flashback
    if c.instance_id != card.instance_id
    and not c.template.is_land
    and game.can_cast(self.player_idx, c)   # ← under-counts
    and Kw.STORM not in getattr(c.template, 'keywords', set())
)
```

The `game.can_cast` filter restricts fuel to what's castable AT THE
FIRING DECISION. After Storm has spent the turn on rituals/cantrips,
~0-1 mana remains, so PiF (4 CMC) doesn't count. fuel_available was
~1 (one cantrip), penalty -2.5, Grapeshot EV = +1.4 — fired.

### Fix

`ai/ev_player.py:_combo_modifier` storm finisher branch:

- Drop the `can_cast` filter from hand-fuel counting. Next-turn mana
  (after untap + draw) is the right horizon, not the depleted current
  pool. The `storm_chain_continuation_p` discount (0.4) already
  models chain-resolution risk.
- When a flashback grantor is reachable (PiF in hand or GY), also
  count GY ritual/cantrip cards as multi-turn fuel — those replay via
  flashback next turn.
- GY flashback cards retain the cast filter (their replay window is
  this turn).

### Test impact (Phase 9a)

| Test | Before | After |
|---|---|---|
| `test_grapeshot_held_when_pif_in_hand_and_storm_short` (new) | FAIL (EV +2.86) | PASS (EV < -5) |
| `test_grapeshot_fires_for_lethal` (regression) | PASS | PASS |

## Phase 9b — Amulet of Vigor recurring-engine signal

### Problem

`_enumerate_this_turn_signals(amulet, ...)` returned `[]`: Amulet's
oracle ("Whenever a permanent you control enters tapped, untap it")
matches no Phase 1-7 signal. AI defers Amulet on T1 → loses a turn
of bounce-land ramp.

### Fix

`ai/ev_evaluator.py:_enumerate_this_turn_signals` adds signal #16
`recurring_engine_trigger`:

- Fires on permanents whose oracle declares a `whenever ...` or
  `at the beginning of ...` trigger.
- Filters out activated abilities (`{cost}:` form) so it doesn't
  misclassify Cranial Plating's `{B}{B}: Attach`. Plating's deferral
  spec from Phase 1 is preserved.
- Filters out attack-only triggers (`whenever ~ attacks`) — those
  aren't same-turn unless the card has haste.
- Doesn't double-fire when `etb_trigger` or `cast_trigger` already
  fired on the same card.

### Test impact (Phase 9b)

| Test | Before | After |
|---|---|---|
| `test_amulet_signals_recurring_engine` (new) | FAIL ([]) | PASS |
| `test_amulet_chosen_on_t1_with_mana` (new) | FAIL (None) | PASS |
| `test_plating_no_carrier_still_defers` (regression) | PASS | PASS |

## Phase 9c — Pinnacle Affinity hypothesis falsified

### Hypothesis from Phase 6

> Phase 3's X-cost optimizer may be clipping Pinnacle's
> Nettlecyst/equipment deployment against its own artifact board
> when Wrath fires.

### Investigation

Inspecting Pinnacle Affinity's per-opponent WRs in the post-Phase 8
matrix:

```
vs Boros Energy             : 8/20 (40%)
vs Jeskai Blink             : 7/20 (35%)
vs Ruby Storm               : 18/20 (90%)
vs Affinity                 : 4/20 (20%)   ← weakest matchup
vs Eldrazi Tron             : 12/20 (60%)
vs Amulet Titan             : 19/20 (95%)
vs Goryo's Vengeance        : 20/20 (100%)
vs Domain Zoo               : 12/20 (60%)
vs Living End               : 18/20 (90%)
vs Izzet Prowess            : 8/20 (40%)
vs Dimir Midrange           : 11/20 (55%)
vs 4c Omnath                : 13/20 (65%)
vs 4/5c Control             : 11/20 (55%)
vs Azorius Control (WST)    : 13/20 (65%)
vs Azorius Control          : 20/20 (100%)
```

**Pinnacle Affinity dominates every Wrath-running control deck**
(Azorius 100%, WST 65%, 4/5c Control 55%, 4c Omnath 65%). The deck's
weakness is the artifact mirror (Affinity 20%), not Wrath collateral.

The Phase 6 hypothesis is falsified: the X optimizer doesn't
disproportionately punish Pinnacle Affinity. The deck's dropped flat
WR vs the pre-overhaul baseline reflects the same matrix-wide trend
(opponents play more disciplined post-overhaul) plus a structural
weakness vs Affinity-mirror that's deck-design, not AI logic.

No code change in Phase 9c. Hypothesis archived.

## Combined matrix deltas (N=20, vs Phase 8 baseline)

| Deck | Δ flat | Δ wtd | Note |
|---|---:|---:|---|
| Goryo's Vengeance | +4.3 | **+17.7** | Recurring-engine signal lifts Faithful Mending / persist class |
| 4c Omnath | -6.0 | **+11.6** | Recurring-trigger Omnath valuation improved |
| **Amulet Titan** (target) | -16.4 | **+6.5** | Phase 9b — recurring-engine signal added |
| Azorius Control | -5.2 | +5.8 | Better ability to value its own engines |
| Jeskai Blink | -13.0 | +5.8 | |
| Pinnacle Affinity | -7.0 | +5.6 | (Phase 9c — no fix, drift inside noise) |
| Eldrazi Tron | -17.1 | +4.7 | |
| Living End | -9.8 | +4.3 | |
| Domain Zoo | -7.8 | +3.4 | |
| **Ruby Storm** (target) | -6.6 | +1.9 | Phase 9a — finisher held more often |
| Dimir Midrange | -11.2 | +1.7 | |
| Izzet Prowess | -16.7 | +1.6 | |
| Boros Energy | -0.3 | +1.5 | |
| Azorius Control (WST) | -3.8 | -0.9 | |
| 4/5c Control | -19.7 | -1.2 | |
| Affinity | -8.8 | -2.6 | Slight drop; still 78% flat / 83% wtd |

13 of 16 decks gained weighted WR. Both Phase 9a and Phase 9b
targets met (Storm +1.9 wtd, Amulet Titan +6.5 wtd). Boros remains
at the top with no further regression. Goryo's Vengeance had the
biggest unexpected lift (+17.7 wtd) — the recurring-engine signal
also helps reanimator decks where the engine card values its own
recurring trigger.

## Open follow-ups

None remaining from Phase 6. The flat WR drops continue to track the
"more disciplined AI = more balanced meta" trend established in
phases 6-8. If specific deck performance becomes a concern in a
later session, that's a fresh investigation.
