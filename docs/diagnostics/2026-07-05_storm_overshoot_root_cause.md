---
title: Ruby Storm field overshoot (70.8% vs band 40-55) — defender response starvation, not chain over-credit
status: active
priority: primary
session: 2026-07-05
depends_on:
  - docs/diagnostics/2026-07-05_calibration_probe_findings.md
tags:
  - ruby-storm
  - calibration
  - response
  - combo-calc
  - chain-fuel-hold
  - p0
summary: >
  Storm's 70.8% field WR (band 40-55) is driven by 8 blowout rows (>=80%)
  against slow/reactive decks. Decision-level probes show the responsible
  subsystem is the defender response layer, not Storm's chain scoring:
  (1) the M2 chain-fuel hold in bottleneck_probability treats a combo
  ARCHETYPE opponent as "chain in flight" on every turn of the game, so
  defenders can never counter any fuel-tagged spell — cost reducers,
  draw engines, rituals all resolve unopposed all game; (2) silence-tagged
  instants are never enumerated as response candidates, so a control
  deck's dedicated anti-chain interaction is a dead card vs combo.
  Fix (this track): narrow the fuel-hold to turns where the chain is
  actually mid-cast. PR #454's finisher-lockout fix is correct-play
  credit (real goldfish improvement), not the over-credit.
---

# Ruby Storm overshoot — root cause

## Symptom

Definitive 19-deck Bo3 matrix (`metagame_results.json`, n=20):
Ruby Storm field WR **70.8%** vs calibration band **[40-55]**.
Row profile (win % from Storm's side):

| vs | WR | vs | WR |
|---|---|---|---|
| Amulet Titan | 100 | Boros Ponza | 70 |
| Living End | 95 | Domain Zoo | 65 |
| Instant Reanimator | 95 | Izzet Prowess | 60 |
| Azorius Control | 95 | Eldrazi Tron | 55 |
| 4c Omnath | 95 | Boros Energy | 45 |
| Goryo's Vengeance | 85 | Azorius Control (WST/WST v2) | 45 |
| 4/5c Control | 85 | Affinity | 45 |
| Jeskai Blink | 80 | Dimir Midrange | 40 |

The overshoot is concentrated in decks that answer Storm **reactively**
(counters, silence, sweeper-control) or race slowly. Fast-clock decks
(Boros 45, Affinity 45, Prowess 60) already sit in plausible bands.
`Azorius Control vs Ruby Storm: 5% vs band [45-60]` is the flagged
matchup-level calibration failure — the classic storm predator loses
19 of 20 matches.

## Falsified-hypothesis check (protocol step 3)

- `docs/diagnostics/2026-05-09_storm_mulligan_audit.md` (falsified):
  Storm mulligan is already keep-tight — "mulligan protection
  over-keeping" is NOT re-tested here.
- `docs/diagnostics/2026-05-10_multi_card_tutor_projection_audit.md`
  (falsified): tutor projection under-crediting — not relevant to an
  overshoot.

## Candidate 1 — finisher-lockout gate too permissive (PR #454): NOT the over-credit

Replays `--bo3 "Ruby Storm" "Azorius Control" -s 50000` and
`-s 50001..53500` show Storm kills on T4-T8 via fuel-first sequencing
and full-storm Grapeshots. That is *correct Storm play* (real Ruby
Storm goldfishes T3-T4). The #454 fix removed a self-sabotage bug
(payoff fired at storm=1-3); its credit is legitimate. Reverting it
would restore wrong play, not calibration.

## Candidate 2 — counter-holds starving defenders: CONFIRMED (primary root cause)

Decision-level probe (`ResponseDecider.decide_response` on a synthetic
state, defender = counter deck with UU open + Counterspell in hand):

| Stack item | Storm's spells cast this turn | Result (main, pre-fix) |
|---|---|---|
| draw-engine cantrip | 0 (development turn) | **held** — "chain-fuel hold" |
| ritual | 0 (development turn) | **held** — "chain-fuel hold" |
| cantrip | 3 (mid-chain) | held (intended M2 behavior) |
| payoff / tutor | 3 (mid-chain) | fires (correct) |

The exact divergence: `ai/combo_calc.py::_opp_chain_in_flight` returns
True for a combo-archetype opponent **unconditionally** (archetype
lookup arm) and whenever any cost-reducer is on the battlefield — on
every turn of the game, not just combo turns. `bottleneck_probability`
therefore returns 0.0 for every fuel-tagged spell Storm ever casts,
and the bp==0.0 branch in `ai/response.py::decide_response`
early-returns None (hold everything).

Consequence chain: the defender can never counter a cost reducer,
draw engine, or ritual at any point in the game. Storm's entire
velocity/engine development resolves unopposed; the defender's
counters are reserved exclusively for payoff-access spells, which
Storm answers with redundancy (double Wish + Past in Flames — see
s50000 G1 T7: two Wishes cast back-to-back; one counter cannot stop
the turn). Match evidence: across 8 Bo3 matches vs Azorius Control
(s50000-53500), the defender cast 0-4 pieces of interaction per
*match* in Storm wins; in s52000's trace the defender held reactive
cards all game while Storm goldfished.

The M2 hold (PR #453) was designed for the mid-chain triage case
(don't burn the last counter on a 4-of cantrip during the combo turn).
Its in-flight predicate over-extends the hold from "mid-chain this
turn" to "opponent is a combo deck", which converts a triage rule
into a full-game interaction lockout.

**Fix (this track):** the fuel-hold (bp==0.0) arm requires the chain
to be *mid-cast this turn* — at least one spell cast by the opponent
before the one on the stack. Development-turn fuel drops to NaN
(legacy threat evaluation prices it; probes show Counterspell then
fires on a dev-turn draw-engine and on a cost reducer, and correctly
passes on a lone ritual). The payoff arm (bp==1.0) and the mid-chain
hold are unchanged.

## Candidate 2b — silence class inert end-to-end (secondary, same starvation family): CONFIRMED and FIXED (this track)

Three stacked defects made the turn-scoped cast-lock class (the
dedicated anti-chain interaction, 4 mainboard copies in Azorius
Control) completely inert:

1. **Engine**: `silenced_this_turn` was SET by the effect layer but
   READ by nothing — `CastManager.can_cast` never consulted it, so
   even a resolved cast-lock denied zero casts. Fixed: the cast gate
   now refuses casts for a locked player (all cast routes).
2. **Enumeration**: `ai/response_enumeration.py::_yield_hand_candidates`
   surfaced only counter / pitch / removal / instant-discard /
   channel candidates; a silence-tagged instant was never yielded —
   probe showed `decide_response` recording "No castable instants in
   hand" with a castable silence instant in hand and W open. Fixed:
   silence-tagged instants yield an `action="silence"` candidate.
3. **Decision**: the bp==0.0 early return held ALL responses,
   including the cast-lock — whose polarity is inverted vs a counter
   (it never trades with the payoff; it is at its best the earlier
   it lands in a running chain). Fixed: at bp==0.0 a held cast-lock
   FIRES (cheapest first), denying the rest of the combo turn while
   counters stay reserved for the payoff.

Post-fix replays confirm the intended play pattern: mid-chain Chant
responses ending combo turns, and the Isochron Scepter + cast-lock
soft-lock actually functioning (s52500 G2/G3).

## Candidate 3 — mulligan over-keeping: NOT re-tested

Falsified 2026-05-09 (see above). Replay mulligans (s50000-53500) show
Storm mulling no-reducer/no-backup hands per policy; no over-keep
pattern observed.

## Additional defender-side gaps observed (out of scope this track — named for follow-up)

1. **Mana-holdback blind to the silence class and to creatureless
   threats** (`ai/ev_player.py::_holdback_penalty` — owned by a
   sibling track's region set): held interaction is recognized only
   via `removal`/`counterspell` tags, so a defender holding two
   cast-lock instants gets the PROACTIVE TAP-OUT BONUS and enters
   Storm's combo turn with zero open mana (s50000 G2 T3: tapped out
   for a 2/2 while holding two cast-locks). Adding `silence` to the
   held-interaction tag set is a one-line follow-up.
2. **Sideboard selection**: vs Storm the control deck boards
   +1 Celestial Purge +2 Mystical Dispute but leaves 4x Consign to
   Memory (counters the storm trigger itself), High Noon, and
   Damping Sphere in the sideboard (s50000 log, game 2 swap line).
   Sideboard selection is a separate subsystem.

## Measured effect (this track, n=15 Bo3 field runs, same seeds)

- Baseline (pre-fix code): **65.2%** (matrix reference: 70.8% n=20).
- After fix 1 (mid-cast hold narrowing): **63.3%**.
- After fix 1+2 (cast-lock class armed end-to-end): **63.9%**.

Honest read: both fixes are decision-level verified (counters now
fire on development-turn engine pieces; cast-locks now end combo
turns) and defender interaction per match vs the counter-control
deck rose from 0-4 to 1-7 casts, but the aggregate field WR moved
only ~1-2pp at n=15 (noise band per matchup ±13pp). The remaining
distance to the [40-55] band is carried by the two named follow-up
gaps above plus the independently-broken slow decks (below), not by
Storm-side over-credit — Storm's own play is correct per Candidate 1.

## Amulet Titan probe (13.1% field — deck-side or field-side?)

`--bo3 "Amulet Titan" "Boros Energy" -s 50000`: Boros 2-0.

- G1: Amulet kept a 7 with "3 lands, 1 castable spell" and cast only
  a T1 Vexing Bauble and a T3 Amulet of Vigor across six turns — dead
  on board while Boros curved out. Keep policy accepted a
  near-functionless hand.
- G2: Amulet actually functioned — T5 Primeval Titan (fetching
  Simic Growth Chamber + Gruul Turf), T7 second Titan + attack — but
  Titans fetched *value* lands, never a haste-enabling line, attacked
  once for 6, and Amulet lost the race T8 while holding 5 cards.

**Verdict: primarily deck-side.** The engine executes (karoo bounce,
Spelunking untap, Titan fetches all work) but conversion is passive:
no haste-land fetch pattern, no attack prioritization, and a keep
policy that accepts 1-castable-spell hands. The 0-100 row vs Storm is
amplified by Storm's overshoot (any slow deck auto-loses to a T4-T6
unopposed goldfish), but Amulet also loses non-degenerate games where
its Titans resolve — a stronger field is not the primary cause. No
Amulet code in this track per ownership boundaries.

## Verification plan

1. Failing test first (mechanic-named): fuel-hold requires mid-cast
   chain; development-turn fuel defers to legacy evaluation.
2. `python -m pytest tests/ -q` full suite green.
3. Ratchets green (`check_abstraction`, `check_magic_numbers`,
   `check_doc_hygiene`).
4. Before/after `run_meta.py --field "Ruby Storm" -n 15` (Bo3);
   target: movement from 70.8% toward [40-55].
