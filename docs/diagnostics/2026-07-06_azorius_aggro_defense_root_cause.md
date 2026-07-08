---
title: Azorius fast-aggro defense — sweeper spent at single-kill while opponent develops
status: active
priority: primary
session: 2026-07-06
depends_on: docs/diagnostics/2026-07-05_calibration_probe_findings.md
tags: [azorius, board-wipe, x-cost, defense, aggro]
summary: >
  Azorius loses the fast-aggro matchups (10% vs Boros band [45-55]) in
  large part because the X-cost sweeper gate lets the AI spend its
  sweeper on a single 2-power token on T3 while the aggro opponent
  holds a full grip; the swarm that arrives T5-6 then goes unanswered.
  Fix: the gate must hold a one-kill sweep while the opponent is still
  developing. Solitude target selection is a named follow-up.
---

# Root cause — Bo3 replay `--bo3 "Azorius Control" "Boros Energy" -s 50000`

Game 1 timeline (P1 Azorius, on the play):

- T3: Azorius casts **Wrath of the Skies at X=0** with three lands,
  killing only a 2/2 Cat token. Ajani (the token generator, cmc 2)
  survives — the base cost WW leaves x_budget=1, cap=1.
- T4-T5: Boros deploys Voice of Victory, Ragavan, Ocelot Pride; Azorius
  durdles (Teferi, card draw). T5 combat: 7 damage.
- T6: Solitude exiles **Ocelot Pride** (a 1/1) instead of the largest
  threat; T6 combat: 10 damage, dead from 20 with the sweeper long gone.

## Instrumented decision (spy on `_gate_x_cost_board_wipe` + `pick_wipe_x_value`)

```
GATE: Wrath of the Skies ev_in=10.0 my_mana=3 -> None   # no clamp
      opp=[(Ajani cmc2 pow1), (Cat Token cmc0 pow2)]
PICKER: budget=1 -> (X=0, score 4.97, kill 1)
```

Both subsystems behaved as *coded*:
- `pick_wipe_x_value` correctly maximises value within budget=1
  (only the cmc-0 token is reachable).
- The AI-side gate (`ai/ev_player.py::_gate_x_cost_board_wipe`)
  clamps a one-kill sweep only when `killable_power < 2`. The Cat
  token is a 2/2, so the gate said "meaningful" and let the cast
  through at full EV.

## The named defect (single subsystem: X-wipe gate, `ai/ev_player.py`)

The gate prices "does X clear something now" but not the sweeper's
**opportunity cost against a developing board**: a wipe's value curve
rises with the opponent's board; the opponent held a full grip
(5 cards) of cheap wide threats. Spending the deck's sweeper as
one-shot spot removal on T3 forfeits the T5-6 sweep that the matchup
is decided by. Class size: every X-cost sweeper in Modern x every
go-wide opponent — mechanic, not card.

Rule-phrased fix: **a one-kill sweep is spot removal; hold the
sweeper while the opponent is still developing** (hand size at or
above the existing `OPP_HAND_FULL_HOLDBACK_THRESHOLD` development
signal), unless desperate (existing PANIC/LETHAL escape hatch,
unchanged). Kill counts >= 2 are untouched.

## Follow-ups named, not fixed here

- **Solitude target selection** exiled a 1/1 lifelink value creature
  over the flip-walker token engine — the evoke/removal targeter may
  under-weight token *generators* (threat of the engine vs the body).
  Needs its own trace before code.
- Azorius still has no proactive pressure line when stabilised; the
  Hall of Storm Giants activation (#472) only fires when the race
  math is already won.
