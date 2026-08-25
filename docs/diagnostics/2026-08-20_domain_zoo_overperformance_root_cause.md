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

**RETRACTED Bo1 claim (kept for the record):** an earlier pass reported
`Bo1 n=4` field numbers showing Domain Zoo 86→64.6%, Boros 71→60%, i.e. "the
field converges from both ends." **The authoritative Bo3 matrix (n=6, 25 decks,
2026-08-20 22:05) does not support this** — see the correction below. The Bo1
n=4 field run was too noisy (4 games/opponent) and the holdback benefit is
seed-dependent; do not cite those numbers.

### AUTHORITATIVE Bo3 result (n=6, post all fixes) — the ceiling held

| Deck | pre-fix Bo3 field | post-fix Bo3 field |
|---|---|---|
| Domain Zoo | 89.5% | **89.5%** (flat) |
| Eldrazi Ramp | 12.5% | **32.7%** (+20, mana fix) |
| Jeskai Blink | 28.7% | **37.5%** |
| Instant Reanimator | 25% | **32%** |

The mechanic fixes lifted the decks whose payoffs were broken (the FLOOR rose),
but they did **not** tame the aggro overperformers. Live Bo3 Domain Zoo vs 4/5c
Control is still **100%**. So the `is_held_interaction` flash-removal fix is a
correct, unit-verified improvement that helps in isolated game-one seeds, but
it is **not sufficient** to close the aggro-defense gap in a sideboarded match.
The systemic aggro-overvaluation is still the #1 open problem. Re-diagnosis
needed on the sideboarded Bo3 path specifically (post-board configs, blocking,
racing), not just the game-one holdback.

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

## Bo3 RE-DIAGNOSIS (2026-08-25) — a distinct mana-planner root contributor

Re-ran the sideboarded Bo3 path the "ceiling held" note above called for.
Seed 50000, `--verbose "Domain Zoo" "4/5c Control"`: the control deck holds
**Wrath of the Skies** (`{X}{W}{W}`, its board sweeper) in hand from the opening
and never casts it, dying to a 2/1 Ragavan by T6.

**Ruled out (verified by a controlled `_score_spell` harness):** the scorer and
the X-cost board-wipe gate are correct. With two white sources present, Wrath
scores **+10.8** and `can_cast` is `True`; `_gate_x_cost_board_wipe` returns
`None` (does not floor). The wipe is *wanted*.

**Actual blocker — double-pipped colour requirement invisible to the mana
planner.** In-game the control deck had only **one** white source (a single
Hallowed Fountain), because its fetches grabbed non-white duals (Stomping
Ground RG, Breeding Pool GU). `ai/mana_planner.py::analyze_mana_needs` computed
`missing_colors = needed_colors.keys() - all_land_colors` — a colour left the
"missing" set the instant ONE source of it existed. A `{W}{W}` sweeper with one
white source therefore read as "white covered", so the fetch/land scorer
(`score_land` block A) gave a *new* colour a strong missing-colour bonus while a
*second* white source — the one that actually unlocks `{W}{W}` — got only the
weak redundant-colour weight and lost the fetch. **Set-of-colours representation
collapses pip depth**; every double/triple-pipped cost in the field
(`{W}{W}` wraths, `{U}{U}` counters, heavy-black spells) is under-served the
same way.

**Fix (generic, `ai/mana_planner.py`):** a colour is a deficit when its deepest
single-spell pip requirement exceeds the number of sources producing it — not
merely when zero sources exist. `max_pip_by_color` (deepest single-spell pip,
per colour) vs `source_counts` (lands producing that colour, tapped or
untapped). Reduces to identical behaviour for every mono-pip case (`get(c, 1)`
default preserves cycling-only colours); changes only when a held spell's pip
depth outruns its sources. Failing-test-first:
`tests/test_double_pip_color_deficit.py` (double-pip-with-one-source flagged
missing; single-pip and two-source cases unchanged).

**Measured impact (honest):** the control deck now assembles the second white
and **casts Wrath of the Skies on T4** (sweeps 4 permanents); the seed-50000
game extends T6 → T9 and Zoo-vs-4/5c Bo3 matches now go to game three 4/10
times (was 0). **But Zoo still wins the matchup ~100%** — it rebuilds after the
sweep faster than the control deck answers-and-closes. So this is a real,
field-wide mana-correctness fix (helps every multi-pip deck reach its colours),
but it does **not** by itself tame the aggro overperformer. The post-sweep
reactive-execution + own-clock gap named above remains the #1 open problem;
the colour-deficit fix is a prerequisite that makes the sweeper reachable, not
the whole cure.

## CORRECTION (2026-08-25, canonical Bo3) — the empty-board wipe fix is game-level, not match-level

The board-wipe fix (`3cab1f4` — the X-cost waste gate no longer disables itself
when the opposing board is empty) was first reported at **0% → 30%** for
control vs Domain Zoo. **That number was Bo1 and does not survive canonical
Bo3.** Per this project's standing directive Bo1 is systematically biased and
is never the basis for a WR claim; it should not have been reported as the
result.

Measured, n=10 each:

| Matchup | Bo1 | Bo3 (canonical) |
|---|---|---|
| 4/5c Control vs Domain Zoo | 30% | **0%** |
| Azorius Control vs Domain Zoo | 30% | **0%** |
| 4/5c Control vs Boros Energy | 30% | **30%** |

The fix is real but its effect is at the GAME level, not the MATCH level.
Against Zoo, 4/5c now wins individual games it previously never won (game wins
on turns 9, 10, 11, 11, 12) and **5 of 10 matches went to three**. It converts
game losses into game wins without converting them into match wins — i.e. it
wins a game and then loses the decider.

Against Boros Energy the 30% holds in both formats, so the fix is not
Zoo-specific.

**Open lead, NOT yet verified as causal:** in every post-board game the log
shows `+2 Mystical Dispute, -2 Orim's Chant`. Mystical Dispute is discounted
only against blue spells and Domain Zoo runs **4 blue cards in 60**, so it
boards in as an expensive soft counter rather than efficient interaction.
Cutting Orim's Chant is correct (it was observed being cast uselessly at 5
life). Whether this swap explains the lost deciders is unproven — a sideboard
audit against aggro is the natural next probe, and it must be measured in Bo3.
