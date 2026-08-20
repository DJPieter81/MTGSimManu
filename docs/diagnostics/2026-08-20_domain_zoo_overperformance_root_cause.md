---
title: Domain Zoo 86% is a defensive-piloting failure, not a Zoo over-buff
status: active
priority: primary
session: 2026-08-20
supersedes: []
superseded_by: []
depends_on: []
tags: [ai, response, removal, control, win-rate, calibration, aggro-skew]
summary: >
  Bo3 replay root cause for Domain Zoo's 86% flat WR (band 50-65), the single
  largest WR distortion in the field. Zoo is NOT over-buffed — it wins turn 6
  with a lone Ragavan (2/1) + Doorkeeper Thrull (1/2). The cause is on the
  defending side: control/midrange decks hold premium flash removal (Solitude)
  and never deploy it against an already-resolved attacker applying a lethal
  clock, because the reactive-removal path only answers creatures ON THE STACK,
  not resolved attackers. This is the systemic aggro-overvaluation skew viewed
  from the winning side, and it unifies the top and bottom of the WR table.
---

# Domain Zoo overperformance — a defensive-play root cause

## Symptom

Bo3 baseline (n=6, 2026-08-20): Domain Zoo = **86.1% flat WR / 89.5% field**,
band [50-65]. Largest single outlier in the table. Matchup spread:

| Zoo crushes (fair decks) | | Zoo loses (interaction+speed) | |
|---|---|---|---|
| 4/5c Control | 100% | Dimir Midrange | 30% |
| Affinity / Amulet | 95% | Ruby Storm | 40% |
| Azorius ×3 / Tron / Goryo's / Reanimator | 85% | 4c Omnath | 60% |

## Not a Zoo over-buff (ruled out)

Replay `--verbose "Domain Zoo" "4/5c Control" -s 50000`: **Zoo wins turn 6 with
a lone Ragavan (2/1) and a Doorkeeper Thrull (1/2)** — no Scion of Draco, no
Territorial Kavu, no Leyline-of-the-Guildpact keyword grants involved. A
control deck losing to a 2/1 by T6 is a catastrophic defensive failure, not an
attacker being too strong. (The stale `deck_cards` MVP data citing Phlage —
banned 2026-05-19, not in the current list — is leftover from an old build and
is unrelated.)

## Root cause (replay-verified, defending side)

Trace `--trace "4/5c Control" "Domain Zoo" -s 50000`: the control deck holds
**Solitude** (flash evoke removal — exile any creature for free by pitching a
white card) in hand from T1 through the late game and **never deploys it** on
the Ragavan that is killing it. It also sits on Prismatic Ending until too
late, and casts **Orim's Chant** (a Silence effect — useless against creatures
already in play) on T5.

Mechanism:
- Solitude is a *creature*, so it bypasses the main-phase reactive-only
  suppression gate (`ai/ev_player.py:579` only gates non-creature reactive
  spells). It is therefore left to the **response path**.
- The response path's removal check (`ai/response.py:~940-985`) only answers a
  creature that is **on the stack (being cast)**. An attacker that resolved on
  T2 and swings every turn is never on the stack, so the held flash removal is
  never offered against it.
- Net: **no path deploys held flash/evoke removal against an already-resolved
  attacker applying a lethal clock.** Control decks hold their answers and lose
  to persistent small-creature aggression.

Corroboration from the matchup spread: decks with **cheap proactive instant
removal** (Dimir Midrange — Fatal Push etc.) beat Zoo (30% for Zoo), while
decks relying on **held flash removal / sweepers** (4/5c, the three Azorius
builds) lose 85-100%. The dividing line is exactly "does your removal get
deployed against a resolved attacker."

## CORRECTION (2026-08-20, instrumented) — it's mana availability, not a missing path

The "held flash removal only answers stack objects" mechanism above is
**wrong** and is retracted. Env-gated instrumentation of `_cast_instant_removal`
(engine/game_runner.py) on live Zoo-vs-4/5c games shows:

- The defender's instant window **IS** called every turn and correctly sees the
  threats (`max_threat` 6.8 → 20.5, far above the 2.0 threshold).
- But the defender has removal it **cannot cast**: `Lightning Bolt` (1 mana!),
  `Leyline Binding`, `Wrath` all show `can_cast=False`, because on the critical
  early turns the control deck has **0 untapped lands** during the opponent's
  combat — it tapped out on its own turn.

So the real defect is **mana availability**: control decks tap out on their own
turn (deploying Wrenn and Six / sorceries) and hold up no mana for instant-speed
interaction, so they cannot answer an aggressive board. The removal-deployment
window is fine.

### Precise, generic contributing bug (FIXED)

`ai/card_classes.py::is_held_interaction` — which drives `_holdback_penalty`'s
decision to reserve mana instead of tapping out — gated on `is_instant` **only**.
Flash removal that is not a plain instant was therefore invisible to holdback:

| Card | is_instant | has_flash | recognized before | after |
|---|---|---|---|---|
| Lightning Bolt | ✓ | – | yes | yes |
| **Solitude** (evoke flash creature) | ✗ | ✓ | **no** | yes |
| **Leyline Binding** (flash enchantment) | ✗ | ✓ | **no** | yes |
| Prismatic Ending (sorcery) | ✗ | ✗ | no | no |

Fix: "instant-speed" = `is_instant OR has_flash`. A control deck holding
Solitude/Leyline Binding now prices tapping-out as a holdback penalty and keeps
the mana to cast them on the opponent's turn. Failing-test-first in
`tests/test_held_interaction_includes_flash_removal.py`.

**Measured impact (Bo1 n=10):** Zoo vs 4/5c Control 100% → **80%** (games
extended T6 → T11, control now interacts); Zoo vs Azorius WST v2 85% → **70%**.
Zoo vs Azorius Control still 100% — a separate gap (its removal suite / holdback
differs), tracked for follow-up. This is one correct piece of the systemic
defensive-play rebalance, not the whole of it.

**Field-level impact (Bo1 n=4, this fix + the two mana fixes combined):**

| Deck | Before | After | Δ |
|---|---|---|---|
| Domain Zoo | 86–89% | **64.6%** | −24pp (→ nearly in band 50–65) |
| Boros Energy | 71–74% | **60.4%** | −12pp |
| Eldrazi Tron | 52.8% | **61.5%** | +9pp |
| Eldrazi Ramp | ~13% | **30.2%** | +17pp |

The top of the table falls toward its bands and the bottom rises — the field
converges from both ends off three generic fixes (flash-removal holdback +
non-land mana + Eldrazi-Temple restricted double-mana), no card-name gates.

## Responsible subsystem

`ai/response.py` (reactive removal deployment) + `ai/ev_player.py` (main-phase
deployment of flash removal). The gap: a resolved attacking permanent that
represents a fast/lethal clock does not trigger deployment of held flash/evoke
removal. Fix must be generic (any flash/evoke/instant removal vs any resolved
attacker whose clock is fast/lethal), phrased on the mechanic, failing-test
first, no card names.

## Significance — this unifies the whole WR skew

This is the **same** systemic issue as the bottom-of-table decks
(docs/diagnostics/2026-08-20_ramp_deck_finisher_deployment_root_cause.md),
seen from the other side:

- Bottom: setup decks under-execute their proactive plan.
- Top: control/midrange decks under-execute their *reactive* plan (holding
  removal instead of deploying it to survive).

Both are "the AI overvalues proactive aggro and underplays defense." Fixing the
removal-deployment gap is the highest-leverage single change: it should lower
every aggro deck's WR (Zoo, Boros, Dimir, Izzet) and raise every control/
midrange deck's WR (the Azorius trio, 4/5c, Jeskai) toward their bands
simultaneously. It is field-wide and must be validated on the full matrix, not
a single matchup.
