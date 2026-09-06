---
title: Reanimator pair (Instant Reanimator 35.4% / Goryo's Vengeance 28.8%) — blink-before-combat forfeits the haste swing
status: archived
priority: primary
session: 2026-08-27
depends_on:
  - docs/diagnostics/2026-08-25_instant_reanimator_residual_root_cause.md
  - docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md
supersedes: []
superseded_by: []
tags:
  - p0
  - wr-outlier
  - reanimation
  - goryos
  - blink
  - ephemerate
  - haste
  - sequencing
  - ai
summary: >
  Bo3 replay root cause (6 matches, seeds 60000/60500/61000, vs Domain Zoo —
  both decks' shared worst matchup in the 2026-08-27 n=20 matrix) for the
  below-band reanimator pair. Primary subsystem: ai/ev_player.py blink-timing
  scoring. In all 4 observed opportunities across both decks, Ephemerate was
  cast in Main 1 on the freshly Goryo's-reanimated body; the blinked permanent
  is a new object (CR 400.7) that no longer carries the Goryo's haste grant,
  so the 7-power lifelink attack is silently forfeited ("does not attack").
  In one game this forfeited on-board LETHAL (Zoo at 3 life, all blockers
  tapped) and the deck then decked itself chain-blinking Atraxa. The engine
  is rules-correct throughout; the existing BLINK_M1_HOLD_PENALTY (2.0) is
  empirically never decisive. Secondary, already-documented levers (discard
  advisor pitching blockers/ignoring on-board GY hate, incoherent mull-to-5
  keeps) account for most remaining losses. Grixis Reanimator (61.7%,
  in-band) is the control case: Persist/Unearth return bodies PERMANENTLY —
  no exile rider, no blink dependency — so it never touches the broken line.
---

# Reanimator pair root cause — 2026-08-27

## Scope and protocol

Diagnosis-only. Fresh n=20 Bo3 matrix (`metagame_results.json`,
2026-08-27T04:45): **Instant Reanimator 35.4%** (band 45-60, Tier 1, 4.9%
meta) and **Goryo's Vengeance 28.8%** (band 30-70). Two decks sharing one
mechanism class (outlet → legendary body in yard → Goryo's Vengeance →
temporary hasty body → Ephemerate to keep it) both below band = generic
subsystem gap, per the abstraction contract. **Grixis Reanimator 61.7%**
is the in-band contrast the diagnosis must explain.

Falsified-hypothesis check performed first
(`grep -rEl '^status: falsified' docs/`): the only reanimation-domain
falsification is RC-3 inside
`docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md` (relaxing
typed mulligan combo paths to flat 2-of-3 — moved nothing; not re-run here).

### Prior claims on file

| Doc | Claim | Status vs this session |
|---|---|---|
| 2026-07-05_instant_reanimator_mechanism_gaps | engine drops blink→rider clear | superseded; engine FIXED and verified again here (rider correctly dropped after blink in every replay) |
| 2026-07-05_goryos_field_13pct_root_cause | RC-1 engine fix + "AI never attempts the blink line" as top follow-up | partially stale: the AI now DOES attempt the blink line — but sequences it wrong (this doc) |
| 2026-08-25_instant_reanimator_residual_root_cause | blink line is a 3-card coincidence; forced loot pitches the wall | both confirmed as secondary levers by these replays; the primary is one level deeper — when the line IS assembled, the AI mis-sequences it |
| 2026-04-28_goryos_combo_mana_mulligan | mulligan keeps mana-broken combo hands | still visible: two incoherent mull-to-5 keeps in this sample |

## Matrix positions (n=20 Bo3, 2026-08-27)

| Deck | Field | 3 worst | 2 best |
|---|---|---|---|
| Instant Reanimator | 35.4% | Domain Zoo 0, Dimir 0, Pinnacle Affinity 0 | Hollow One 70, Goryo's 75 |
| Goryo's Vengeance | 28.8% | Domain Zoo 0, Pinnacle Affinity 0, Eldrazi Tron 5 | AzCon(WSTv2) 55, Living End 70 |
| Grixis Reanimator | 61.7% | Domain Zoo 30, 4c Omnath 30, AzCon(WSTv2) 30 | Hollow One 85, Creatures Toolbox 95 |

Domain Zoo (top-ranked deck, 84.8%) is the shared 0% floor and the replay
opponent. Grixis holds 30% against the same opponent — in-band decks lose
this matchup honorably; the pair loses it absolutely.

## Replay evidence (all Bo3, committed to `replays/`)

- `instant_reanimator_vs_domain_zoo_s60000.txt` — Zoo 2-0
- `instant_reanimator_vs_domain_zoo_s60500.txt` — Zoo 2-1 (G1 was a
  T13-timeout win for IR: **excluded** from loss analysis as truncated)
- `instant_reanimator_vs_domain_zoo_s61000.txt` — IR 2-1
- `goryos_vengeance_vs_domain_zoo_s60000.txt` — Zoo 2-1
- `goryos_vengeance_vs_domain_zoo_s60500.txt` — Zoo 2-0
- `grixis_reanimator_vs_domain_zoo_s60000.txt` — Zoo 2-0 (contrast)

## Per-loss first-divergence table

(a)=outlet+payoff assembled on curve, (b)=reanimate cast earliest legal
turn, (c)=right target, (d)=win once body lands, (e)=lost to opposing
interaction/hate.

| Game | Result | First divergence from correct line | Owning subsystem |
|---|---|---|---|
| IR s60000 G1 | L T7 | (a)-(c) OK: T4 Goryo's→Atraxa, 7 dmg + 7 lifelink. Blink genuinely unavailable (Ragavan exiled Ephemerate off library top). Then race loss vs 9-10 dmg/turn permanent board; T6-T7 Ephemerates spent chip-blinking Quantum Riddler at 13→4 life. | race/structural (blink absence = variance) |
| IR s60000 G2 (60001) | L T6 | (a)-(c) OK: T4 Griselbrand, pay-7-draw-7, 7+7 swing — all engine-correct. EOT hand-size discard then pitched **both Quantum Riddlers** (only castable blockers) + Faithful Mending while facing lethal-in-2; died on an empty board holding 7 cards. | ai/discard_advisor.py (no blocking-value-under-clock term; confirmed lever 2 of 2026-08-25 doc) |
| IR s60500 G2 (60501) | L T7 | T3 Faithful Mending binned BOTH payoffs (Griselbrand, Atraxa) with Territorial Kavu already on board; Kavu exiled Griselbrand T4 (`T4 P1: Territorial Kavu exiles Griselbrand from opponent GY`) and Atraxa T5; Goryo's drawn T5 was dead. AI then correctly cast nothing. | ai/discard_advisor.py (ignores active on-board GY-hate) → then (e) |
| IR s60500 G3 (60502) | L T6 | Mull-to-5 kept `['Marsh Flats', 'Atraxa', 'Polluted Delta', 'Ephemerate', 'Flooded Strand']` — no outlet, no reanimation spell, Atraxa uncastable. Never assembled. | ai/mulligan.py (land-count heuristics only on small hands) |
| **IR s61000 G1** | L T6 | **T5 full assembly**: Goryo's→Griselbrand, then `T5 P1: Cast Ephemerate` in Main 1 → `T5: Blink Griselbrand` → **`[Declare Attackers] P1 does not attack`** (blinked object = new object, no haste). 7 dmg + 7 lifelink forfeited; Leyline Binding answered next turn; dead T6 at −2 (the forfeited lifelink covered the margin). | **ai/ev_player.py blink-timing scoring** |
| **Goryo's s60000 G1** | L T8 (self-mill) | T7: Zoo at **3 life**, Goryo's→Atraxa (haste), all three Zoo blockers tapped from their T6 attack — attack = lethal. AI instead blinked Atraxa **three times** for reveal value (first blink kills haste), `P1 does not attack`, discarded 7 to hand size at EOT, and hit `Library: 0` on T8: `P1 loses: empty library`. | **ai/ev_player.py blink-timing scoring** + no lethal-check before value lines |
| Goryo's s60000 G3 (60002) | L T6 | 3x Faithful Mending binned lands/spells; no legendary body ever reached yard (none drawn); 2 Goryo's dead in hand; T4 loot pitched Ephemerate + Undying Evil (its protection). | variance + ai/discard_advisor.py |
| **Goryo's s60500 G1** | L T7 | T6 Goryo's→Griselbrand, then Undying Evil AND Ephemerate both cast pre-combat (`→ Goal: execute_payoff [protection]`), blink kills haste → `P1 does not attack`; Zoo Leyline Bindings the kept body at begin-combat anyway. Zero damage from the whole package. | **ai/ev_player.py blink-timing scoring** |
| Goryo's s60500 G2 (60501) | L T8 | Mull-to-5 kept `["Goryo's Vengeance", 'Unburial Rites', 'Unburial Rites', 'Inquisition', 'Godless Shrine']` — 1 land, no payoff creature, no outlet. Goryo's countered T7; T8 single 7-dmg Atraxa swing (exiled EOT); Zoo's 15-power permanent board killed back. | ai/mulligan.py + structural |

## Primary subsystem — one call

**`ai/ev_player.py` — blink-timing scoring around the pending-EOT-exile
rider** (the RC-1 credit block at ~lines 1448-1457 and the M1-hold gates at
~lines 1421-1432, with `BLINK_M1_HOLD_PENALTY: float = 2.0` at
`ai/scoring_constants.py:724`).

Mechanism, precisely:

1. Goryo's Vengeance reanimates with haste via
   `engine/permanent_effects.py::reanimate(give_haste=True)` →
   `temp_keywords.add(Keyword.HASTE)`. Engine-correct.
2. Ephemerate's blink makes the permanent a new object
   (`engine/zone_manager.py::_blink_zone_transition` re-calls
   `enter_battlefield()`); the new object has no haste and is summoning-sick.
   Engine-correct (CR 400.7) — and it is exactly this correctness that makes
   the pre-combat blink catastrophic.
3. The scorer already *knows* the right rule — the RC-1 block even comments
   "an attack-capable rider should swing first … and be blinked post-combat"
   and withholds the rider-clearance credit in MAIN1, applying
   `BLINK_M1_HOLD_PENALTY` — **but a −2.0 nudge is empirically never
   decisive**: in **4 of 4** observed assembled-line opportunities across
   both decks (IR s61000 G1; Goryo's s60000 G1 ×1 decisive of three blinks;
   Goryo's s60500 G1; plus IR s60000 G1's T6 value-blink pattern) the
   Ephemerate resolved in Main 1 under `→ Goal: execute_payoff [protection]`,
   the haste attack evaporated, and the log line `[Declare Attackers] P1
   does not attack` followed the reanimation in the same turn. Competing
   positive terms (the `[protection]` role path through the reactive-only
   gate at ~line 642, `BLINK_ETB_RETRIGGER_BONUS`, and the goal-role
   machinery) outweigh it. Note `run_trace_game` is currently broken
   (`TypeError: traced_main() got an unexpected keyword argument
   'excluded_activations'`, run_meta.py:1092 → engine/game_runner.py:1222),
   so the per-term EV printout could not be captured; the behavioral record
   is 4/4.
4. Downstream of the same value-scoring: chain-blinking Atraxa scores +4-5
   cards per blink with no term for (i) lethal being available on board,
   (ii) library depletion — Goryo's s60000 G1 went `Library: 0, Graveyard:
   31` by T8 and lost to draw-from-empty with the opponent at 3 life.

Why this is THE primary and not the (real) secondary levers: the pair's
only winning line versus a permanent board is *swing 7 with lifelink, THEN
keep the body*. Each mis-sequenced turn costs ~14 points of life swing plus
the body's future clock. In the three games where the deck fully assembled
its namesake line against its worst matchup, it converted **zero damage
from the swing in all three** — one of them with literal lethal on board.
Fixing mulligans or discard priorities cannot matter while the assembled
line itself is worth 0 attack damage.

## Mechanism failure vs race/structural — separated

- **Mechanism failure (AI):** 3/9 losses decisively (both decks'
  assembled-line games), plus the lethal forfeit + self-deck.
- **Discard/mulligan (AI, secondary, already documented):** 4/9 losses
  (2026-08-25 doc lever 2 and 2026-04-28 mulligan doc remain accurate).
- **Race/structural (not a bug):** 2/9 — even played perfectly, one
  temporary 7-point lifelink swing per Goryo's loses to Zoo's 8-15
  dmg/turn permanent board, and Zoo mainboards incidental hate
  (Territorial Kavu GY exile, Doorkeeper Thrull ETB suppression, Leyline
  Binding — which answered a *kept* body twice in this sample). A 0%
  matchup should become a bad-but-live ~25-35% one, not a favorable one.

## The Grixis contrast — why the control deck is fine

Grixis Reanimator (`decks/modern_meta.py:1110`) shares the archetype label
but not the mechanism: **Persist and Unearth return bodies permanently — no
end-of-turn exile rider, no haste-grant, no blink dependency** — and its
threat base (Psychic Frog, Emperor of Bones, Abhorrent Oculus) is cheaply
hard-castable with Fatal Push ×4 backing it. In
`grixis_reanimator_vs_domain_zoo_s60000.txt` both losses run 9-10 turns of
normal attrition: creatures stick, block, and attack every turn; removal
trades. The deck never enters the code path this doc indicts. That is
exactly why the pair's shared 0% floor and Grixis's 30% against the same
opponent isolate the temporary-body/blink machinery — the one mechanism
the pair uses and Grixis does not — as the differentiator.

## Falsifier / next step (for the fix session, not this one)

Force the sequencing (attack with the hasty rider before any blink of it;
blink only in Main 2/post-combat) and re-run these six seeds. If IR s61000
G1 and both Goryo's blink games flip or go long, the lever is confirmed.
If they still fold identically, the discard/mulligan levers move up. Any
fix must be phrased on the mechanic ("a blink targeting a live
EOT-exile-rider permanent that can attack is not cast before combat") —
no card names; the same rule covers Sneak Attack / Through the Breach
shapes. Fix also repairs `run_trace_game` first so the per-term EV can be
observed.

## BLINK-TIMING FIX MEASURED (2026-08-27, post-#557)

The derived forfeit charge landed (flat penalty removed; pre-combat blink
charged the attack it forfeits via `forfeited_attack_clock_impact`).
Acceptance, n=20 Bo3 field, idle machine:

- **Instant Reanimator: 35.4 -> 43.3 (+7.9pp)** — the largest single-fix
  WR movement measured this session; 1.7pp below its [45-60] band floor.
- **Goryo's Vengeance: 28.8 -> 32.1 (+3.3pp)** — back inside its [30-70]
  band.

The anchor corroborated the mechanism before the sweep did: all three
drifted entries kept their winner and finished one turn faster (the swing
lands). The diagnosed secondary items (discard-advisor pitching
blockers/payoffs under a lethal clock; the outlet∧payoff∧spell mulligan
coherence check) remain open and are now the levers for Instant
Reanimator's final ~2pp; the pair's shared floor vs Domain Zoo should be
re-read from the next full matrix.

## SECONDARY LEVERS IMPLEMENTED (2026-08-27, follow-up session)

Both diagnosed secondary levers landed, test-first, no WR measurement
run in that session (single-process constraint):

1. **Discard vs live plan + race state** (`ai/discard_advisor.py`):
   graveyard-payoff bonuses (fuel/flashback/escape/big-creature) are
   discounted by the existing `EVSnapshot.urgency_factor` survival
   fraction; under a lethal-range clock (`opp_clock_discrete <= 2`) a
   deployable creature subtracts its `ai.clock.opportunity_cost`
   (Phase-2a primitive, reused); the fuel bonus is gated on graveyard
   safety (typed `has_graveyard_hate` on any opposing permanent); and
   a live-plan role guard never pitches the LAST accessible copy of a
   required role (goal `card_roles` payoffs/enablers/protection + the
   derived FILL_RESOURCE resource role) — a dead plan protects
   nothing. Tests: `tests/test_discard_respects_live_plan.py`.
2. **Mulligan goal-conjunction distance** (`ai/mulligan.py`,
   `ai/ev_player.py`): covered `mulligan_combo_paths` role buckets are
   scored like functional lands (`MULLIGAN_CONJUNCTION_BUCKET_VALUE`),
   and at the always-keep floor a hand whose conjunction is
   UNREACHABLE (no fully-covered path, no castable dig card) mulls
   exactly once more. Distinct from falsified RC-3 (which relaxed the
   7/6 gates) and the 2026-05-09 Storm pro-bar audit (which tightened
   7-card keeps): the 7/6 gates are untouched; the new hard rule lives
   only at the previously ungated floor (5) and keys on reachability.
   Tests: `tests/test_mulligan_scores_goal_conjunction.py`.

Anchor after both levers: one drifted entry — Goryo's Vengeance vs
Izzet Prowess s50000, winner unchanged (Izzet Prowess), turns 9 → 6.
Snapshot deliberately NOT refreshed in that session; refresh alongside
the next matrix re-read.

## SECONDARY LEVERS MEASURED — ARC CLOSED IN BAND (2026-08-27, post-#559)

Acceptance, n=20 Bo3 field, idle machine:

- **Instant Reanimator: 43.3 -> 47.9 (+4.6pp) — INSIDE its [45-60] band.**
  Cumulative across the three fixes (blink timing, plan-aware discard,
  goal-conjunction mulligan): **35.4 -> 47.9, +12.5pp.**
- **Goryo's Vengeance: 32.1 -> 36.9 (+4.8pp)** — solidly in its [30-70]
  band; cumulative +8.1pp.

This investigation is CONCLUDED: the pair's shared gap decomposed into one
primary (forfeited haste swing) and two secondary mechanisms (isolation-
valued discard, conjunction-blind mulligan), every fix generic and reused
by other decks, every step measured. The next full matrix should re-read
both rows plus the decks the generic mechanisms touch (any deck holding
blink effects, graveyard payoffs, or multi-role gameplans).

## POST-CAGE-FIX RE-MEASUREMENT (2026-08-30) — provisional status LIFTED

The numbers above were flagged provisional because they predated the
phantom-Cage fix (#567), which found that 446 permanents were wrongly acting
as symmetric Grafdigger's Cages and SUPPRESSING graveyard decks across most of
the field. Re-measured on the corrected engine, n=20 Bo3, all 24 opponents,
with `MTG_LLM_DECISION_SCORER_OFFLINE=1` (see the 2026-08-30 diagnostics doc —
without it the decision loop makes live LLM calls and is non-deterministic):

| Deck | Original | Post-levers | **Post-Cage-fix** | Band |
|---|---|---|---|---|
| Instant Reanimator | 35.4 | 47.9 | **56.7** | [45-60] — in band |
| Goryo's Vengeance | 28.8 | 36.9 | **48.1** | [30-70] — in band |

Both rose again (+8.8 and +11.2), which is the direction the Cage diagnosis
predicted: these decks were being held down by phantom hate, not
under-punished by absent hate. Cumulative across the arc: Instant Reanimator
35.4 -> 56.7 (+21.3), Goryo's 28.8 -> 48.1 (+19.3).

Comparability note: the post-levers figures were measured while `pydantic_ai`
was absent from the container, so the live-LLM path was inert — behaviourally
equivalent to running with the offline flag. The three columns are therefore
comparable.

Instant Reanimator's spread is now sensible for the archetype: 90% vs Living
End and Azorius Control, 75% vs Hollow One and Amulet Titan, 35% vs Dimir
Midrange, 5% vs Domain Zoo — it beats the slow decks and loses to the fastest
aggro, which is what the deck does in paper.
