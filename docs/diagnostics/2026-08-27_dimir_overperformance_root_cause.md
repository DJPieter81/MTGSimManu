---
title: Dimir Midrange 68.5% overperformance — victims' execution failures, not a Dimir buff; one new mechanic hole (land destruction unimplemented)
status: active
priority: primary
session: 2026-08-27
supersedes: []
superseded_by: []
depends_on:
  - docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md
  - docs/diagnostics/2026-08-20_ramp_deck_finisher_deployment_root_cause.md
  - docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md
  - docs/diagnostics/2026-08-26_decider_loss_root_cause.md
  - docs/diagnostics/2026-07-05_storm_dimir_canonical_gap.md
tags: [dimir, wr-outlier, overperformance, bo3, replay, response, blocking, mulligan, land-destruction, oracle-coverage]
summary: >
  Bo3 replay root cause (8 matches / 17 games, seeds 62000/62500) for Dimir
  Midrange's 69.0% flat field WR (band [45-60], n=20 Bo3 matrix at HEAD,
  2026-08-27). Dimir is NOT over-buffed: every converting Dimir card
  (Psychic Frog, Orcish Bowmasters, Murktide, Subtlety, Drown in the Loch)
  implements at or BELOW its oracle text (Bowmasters' opponent-draw trigger
  is missing entirely — Griselbrand drew 7 into a live Bowmasters with zero
  triggers). The excess wins sit on the opponents' side and decompose almost
  entirely into already-documented verdicts: setup/combo decks fail to
  execute their proactive plan (ramp-doc structural story; Storm engine
  turns with no payoff, jams into open UU) and defenders fail to block or
  deploy (Zoo-verdict defensive-execution story; reanimator blink-forfeit
  owned by the reanimator-pair doc). Dimir is the maximum structural
  beneficiary because its interaction is 100% cheap on-stack instants — the
  one reactive class the AI executes well — and its victims' win paths all
  pass through the stack. ONE new mechanism: land destruction has no play
  path anywhere (engine/oracle_parser.py parses destroy-artifact/
  enchantment/nonland-permanent but not destroy-target-land), so Boros
  Ponza's 11 mainboard LD slots are dead cards and its 10% row vs Dimir is
  structural. Two minor contributors flagged for follow-up at n=1: a
  counter-tax decline with 4 available mana, and the MAX_TURNS life-total
  timeout award.
---

# Dimir Midrange overperformance — root cause (2026-08-27)

## Symptom

`metagame_results.json` at HEAD (n=20 Bo3 matrix, 2026-08-27T04:45): Dimir
Midrange **69.0% flat / 58.5% weighted** against band [45-60]. Biggest
remaining undiagnosed above-band outlier. Trajectory check: this week's
merged fixes (reanimator blink arc, decider soft-counter gate) already moved
Dimir 71 → 68.5 — consistent with the verdict below that the excess lives in
its victims' misplays, which those fixes are progressively removing.

## Matchup anomaly ranking (vs field-strength expectation)

Naive expectation `50 + (dimir_flat − opp_flat)`; anomaly = actual − expected:

| Opponent | Dimir WR | opp field WR | anomaly |
|---|---|---|---|
| **Ruby Storm** | 85 | 55.2 | **+21.2** |
| **Boros Ponza** | 90 | 46.7 | **+17.7** |
| **Instant Reanimator** | 100 | 35.4 | **+16.4** |
| Hollow One | 95 | 36.5 | +12.5 |
| Living End | 80 | 50.0 | +11.0 |
| … | | | |
| **Eldrazi Tron (control case)** | 50 | 64.2 | −4.8 |
| Boros Energy | 30 | 73.3 | −15.7 |

Dimir's row is NOT uniformly inflated: it loses correctly to proactive
threat-density decks (Boros 30%, Zoo 30%, Prowess 40%) and is dead-even with
Eldrazi Tron. The excess is concentrated against **setup decks** (combo,
land-denial, blink-value) — exactly the class the 2026-08-20 ramp doc showed
"hands near-free wins to the whole field" (that doc already lists Dimir 73%
among the inflated beneficiaries).

## Replays (8 Bo3 matches, 17 games, seeds 62000/62500)

```
replays/dimir_midrange_vs_ruby_storm_s62000.txt          Dimir 2-0
replays/dimir_midrange_vs_ruby_storm_s62500.txt          Dimir 2-0
replays/dimir_midrange_vs_boros_ponza_s62000.txt         Dimir 2-0 (G2 via timeout — discarded from the table)
replays/dimir_midrange_vs_boros_ponza_s62500.txt         Dimir 2-0
replays/dimir_midrange_vs_instant_reanimator_s62000.txt  Dimir 2-0
replays/dimir_midrange_vs_instant_reanimator_s62500.txt  Dimir 2-1
replays/dimir_midrange_vs_eldrazi_tron_s62000.txt        Tron 2-0  (control)
replays/dimir_midrange_vs_eldrazi_tron_s62500.txt        Tron 2-0  (control)
```

Dimir won 12/13 games in the anomalous matchups; 1 was a timeout truncation
(discarded), leaving 11 classified below.

## Per-win mechanism table

Classes: **(a)** opponent misplay (named, with owning subsystem),
**(b)** Dimir card over-executing, **(c)** legitimate win.

| # | Game | Turn | Decisive mechanism | Class | Evidence (from the log) |
|---|---|---|---|---|---|
| 1 | Storm s62000 G1 | 12 | Storm launches its combo turn NON-lethal (Grapeshot for 10 at opp 19 after Wish is Counterspelled mid-chain), then jams Wrenn's Resolve twice and a second Grapeshot into open UU/UB — all countered | (a) combo sequencing + counter-blindness — `ai/combo_chain`/BHI jam decision | `T4: Wish is countered` → `T4: Grapeshot deals 1 damage (opponent life: 18…9)`; `T5/T10: Wrenn's Resolve is countered`; `T12: Grapeshot is countered` |
| 2 | Storm s62000 G2 | 8 | Storm keeps 2-land Past-in-Flames hand, stalls on 3 lands; T6 PiF resolves with ZERO flashback casts after; Bowmasters+Murktide close | (a) keep quality + chain follow-through — `ai/mulligan` + combo executor | `T6 P1: Past in Flames grants flashback` followed by no flashed-back spell all game |
| 3 | Storm s62500 G1 | 7 | T3 full enabler dump (3× Desperate Ritual, Pyretic, Glimpse, Manamorphose) with no payoff; T6 Wish fetches **Past in Flames instead of Grapeshot** at 5 life, PiF again no follow-through; dead on crackback | (a) payoff selection + enabler dump — combo executor / tutor-target choice | `T6 P2: Wish finds Past in Flames (from sideboard)`; Storm at 2 life passes with 3 Ruby Medallions and 6 lands |
| 4 | Storm s62500 G2 | 6 | Storm KEEPS a 1-land 7 (Grapeshot+engine); casts one spell all game (Pierced T1). Same game, Dimir's own 1-land 7 is MULLED by the generic rule | (a) mulligan combo-keep bypasses the land floor — `ai/mulligan.py` | `P1 KEEPS 7 — key card(s): Past in Flames…` with `(1 lands, 6 spells)` vs `P2 MULLIGANS (too few lands (1 < 2))` |
| 5 | Ponza s62000 G1 | 10 | Ponza sits on 8 lands / 4 cards with an EMPTY board T4-T10, casts zero land-destruction spells all match, Blood Moon T10 at 2 life; dies to 1/1s+Murktide | (a) structural: LD mechanic unimplemented (see below) + held resources | T9 header: `Boros Ponza board: Creatures: (empty)`, `Hand: 4` / `Lands: 8`; only casts: 2 Bolt, 2 Ragavan, Blood Moon |
| — | Ponza s62000 G2 | 13 | **timeout** — engine awards game to higher life total (Dimir 20, Ponza 7) | discarded | `>>> Dimir Midrange wins Game 2 on turn 13 via timeout` |
| 6 | Ponza s62500 G1 | 10 | Ponza never blocks in 7 consecutive attack steps (Solitude 3/2 on board from T5), zero LD casts, Blood Moon T9 at 5 life | (a) defensive execution (no-block) — `ai/ev_player.decide_blockers` inputs + LD absence | seven `[Declare Blockers] P2 does not block` lines; `T9 P2: Cast Blood Moon` at 5 life |
| 7 | Ponza s62500 G2 | 9 | Ponza takes 7 and 4 unblocked with Pyromancer/Phelia on board at 8→4→0; Fable is Spell-Pierced **with 4 mana remaining** (tax {2} payable, not paid) | (a) no-block + counter-tax decline (n=1, see below) | `(paying for Fable…, 4 mana remaining)` → `T8: Fable… is countered`; `[Declare Blockers] P1 does not block` at 4 life |
| 8 | Reanimator s62000 G1 | 11 | Goryo's countered T7 (open UU); T8 Goryo's resolves, Griselbrand hits once for 7, then Ephemerate REBOUND re-blinks it every upkeep → perpetually summoning-sick, never attacks T9-T11 and never blocks 1-power attackers at 6 life | (a) blink-timing — **owned by 2026-08-27_reanimator_pair_root_cause.md** (+ rebound-recast residual) | `T9/T10/T11: Cast Ephemerate ×3` at upkeep, `Blink Griselbrand`; `[Declare Attackers] P2 does not attack` / `attacks with: Solitude` only; loses at −7 with an untapped 7/7 |
| 9 | Reanimator s62000 G2 | 12 | Goryo's jammed into open UU T7, countered; deck passive thereafter; Bowmasters beats | (a)/(c) — jam into open counters; partially a legitimate discard+counter win | `T7 P1: Cast Goryo's Vengeance` → `T7 P2: Cast Counterspell` → countered |
| 10 | Reanimator s62500 G2 | 8 | Griselbrand reanimated T6, attacks once, then `does not attack` T7-8 and never blocks Murktide+Subtlety (9 unblocked twice with an untapped 7/7) | (a) blink-forfeit + no-block — reanimator-pair doc class | `T6: Reanimate Griselbrand` … `[Combat Damage] 9 damage dealt → P2 life: 11 → 2` unblocked |
| 11 | Reanimator s62500 G3 | 7 | Keeps an Atraxa hand with no reanimation enabler; burns 5 Ephemerates blinking a 1/2 Psychic Frog; never reanimates | (a) keep quality + blink resource waste | `P1 KEEPS 7 — key card(s): Atraxa, Grand Unifier, Psychic Frog`; `T3-T5: Cast Ephemerate ×5` with no payoff in play |

Only one game (s62500 Reanimator G1) went to the opponent — Goryo's resolved
and Griselbrand was allowed to keep attacking; the deck won immediately.
That is the counterfactual that proves the point: when the victim executes,
the matchup is competitive.

## Control-matchup contrast (Eldrazi Tron, 4-0 Tron, all T9 via damage)

Tron never holds resources, never needs to block, and its win path barely
touches the stack in a counterable way: Thought-Knot T5, a second Devourer
T7 after the first is countered, Ugin T9. Dimir counters exactly one threat
per turn cycle and loses to the next. All four games ended turn 9. The
matrix's 50% is honest — a deck that executes proactively at mana advantage
is immune to the failure classes Dimir's victims exhibit.

## Oracle cross-check — Dimir over-execution ruled out (class b)

Checked every converting Dimir card against `ModernAtomic.json`:

- **Psychic Frog** {U}{B} 1/2: log's `deals combat damage — draw a card` and
  2/3 (discard counter) growth match oracle exactly.
- **Murktide Regent**: entered 4/4-5/5 (delve counters) — matches.
- **Subtlety**, **Drown in the Loch**, **Creeping Tar Pit**: all uses in the
  logs are within oracle text (Drown countered CMC≤graveyard targets against
  stocked graveyards).
- **Orcish Bowmasters**: engine implements the ETB damage+amass but NOT the
  opponent-draw clause — s62000 Reanimator G1 T8, `Griselbrand: pay 7 life,
  draw 7` with a live Bowmasters produced **zero** triggers (should be 7
  damage + amass 7). Dimir is *under*-implemented here, the opposite
  direction of an over-execution story.

No Dimir card exceeds its printed text. Class (b) is empty.

## Verdict — primary attribution

**The excess decomposes into already-documented opponent-side classes. No
new Dimir-side mechanism is invented.** Ownership:

1. **Setup decks fail to execute their proactive plan** — owned by
   `docs/diagnostics/2026-08-20_ramp_deck_finisher_deployment_root_cause.md`
   (which already lists Dimir among the inflated beneficiaries). Observed
   here as: Storm's engine-without-payoff turns and non-lethal all-ins
   (wins 1, 2, 3), Ponza's held-hand/empty-board games (5, 6).
2. **Defenders don't deploy answers/blocks against a resolved clock** —
   owned by `docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md`
   (defensive-execution story). Observed as pervasive no-block lines while
   dying to 1-2 power creatures (wins 6, 7, 8, 10).
3. **Reanimator blink-forfeit** — owned by
   `docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md`
   (`ai/ev_player.py` blink-timing). Observed residual beyond that doc's
   Main-1 finding: the **Ephemerate rebound recast at each upkeep re-blinks
   the reanimated body into perpetual summoning sickness** (rebound is a
   *may* cast; the AI always takes it), so the 7/7 attacks exactly once per
   game (wins 8, 10).

**Why Dimir specifically is the maximum beneficiary (the unifying frame):**
Dimir is the only top-table deck whose entire reactive suite is cheap
on-stack instants (Thoughtseize/Counterspell/Drown/Push/Pierce). The
response path (`ai/response.py`) answers stack objects reliably — that is
the one reactive class the AI executes well, per the Zoo doc's own analysis
— and Dimir's anomalous victims (Storm, Goryo's/Instant Reanimator, Living
End, Hollow One) all have win paths that MUST pass through the stack as a
single counterable object. Its in-band opponents (Tron, Boros, Zoo,
Prowess) present threat-per-turn density instead. So Dimir harvests both
sides of the documented skew: its offense collects the setup decks'
misfires, and its defense is the only style the AI plays near-optimally.

## New finding (not owned by any existing doc)

**Land destruction has no play path anywhere in the simulator.**
`engine/oracle_parser.py` parses `destroy target artifact`,
`destroy target enchantment`, and `destroy target nonland permanent`; there
is no destroy-target-land clause, and `grep` for any LD handling in
`engine/` + `ai/` returns nothing. Consequence: Boros Ponza's 11 mainboard
slots (4 Cleansing Wildfire, 3 Pillage, plus SB Molten Rain) are drawn,
held, and never cast in any sampled game — its archetype cannot execute,
and its 10% row vs Dimir (90% anomaly +17.7) is structural, not strategic.
Class size: every LD spell in Modern (well over 10 printed cards; one
registered deck plus common sideboard cards), so per the abstraction
contract this is a *mechanic* gap: oracle clause + typed field + targeting
+ AI enumeration. Responsible subsystem: `engine/oracle_parser.py` (clause
coverage) feeding `ai/ev_player.py` play enumeration.

## Minor contributors flagged at n=1 (NOT root-caused — do not act without more replays)

- **Counter-tax declined while payable** (Ponza s62500 G2 T8): Fable of the
  Mirror-Breaker cast with `4 mana remaining`, Dimir's Spell Pierce fired
  and the {2} tax went unpaid. `engine/optional_costs.offer_counter_tax`
  gates on `available_mana_estimate` then asks `decide_optional_cost`; one
  of the two got this wrong, and `ai/response.py`'s dead-counter gate
  (2026-08-26 decider doc) should also have refused to fire Pierce at a
  4-mana payer. One occurrence in 13 games; needs its own replay set.
- **Timeout life-total award** (`engine/game_runner.py:926`, MAX_TURNS=25
  player-turns ≈ display turn 13): the higher life total takes the game. A
  life-preserving control deck banks these; observed 1/13 games (Ponza
  s62000 G2, Ponza alive at 7). Small but systematically Dimir-positive.

## Expected effect of the existing tracks

Because the excess is victim-side, no Dimir-targeted change is warranted.
Closing the ramp/setup-deck execution class, the reanimator rebound
residual, and the LD mechanic hole should each pull Dimir's anomalous rows
(85-100%) toward their real-world analogs (e.g. the Storm-Dimir matchup's
55-65% Dimir per `2026-07-05_storm_dimir_canonical_gap.md`) without
touching a line of Dimir-relevant scoring — the same direction the 71→68.5
trajectory already shows.

## FOLLOW-UP RESOLUTION (2026-08-28) — three flagged defects re-tested

Each of the three engine/AI items this doc raised was reproduced (or not)
in a test before any code changed.

1. **Rebound re-blink loop — REPRODUCED, FIXED.** The upkeep pass took the
   "you may cast it" recast unconditionally, re-blinking the controller's
   highest-threat body into summoning sickness every upkeep. The choice now
   goes through `EVPlayer.decide_optional_recast`, which declines a recast
   that resets an attack-capable creature unless the recast's own EV covers
   the forfeited combat step (priced by the shared clock primitive
   `ai/clock.forfeited_attack_clock_impact`). Two rules bugs fell out with
   it: a declined/targetless rebound card used to be dropped out of every
   zone, and the free recast re-earned rebound forever (CR 702.88a scopes
   the replacement to a spell cast FROM HAND — `_cast_from_zone` now
   records the origin). Pin:
   `tests/test_optional_recast_respects_pending_attack.py`.

2. **Bowmasters "zero triggers" — DID NOT REPRODUCE.** An instrumented
   re-run of s62000 (patching the on-draw damage applier to log) fires all
   SEVEN triggers on the T8 draw-7; per-card firing and the
   first-draw-of-draw-step exemption are both correct. The claim came from
   the absence of a log line the DRAW fan-out never emits. What the same
   evidence DOES show is a state-based-action hole: those triggers took the
   drawing player from 2 to −5 life, nothing checked SBAs, and a lifelink
   attack later in the turn restored them to 2 — they kept playing a game
   they had already lost. Fixed in the DRAW fan-out (one SBA pass when a
   handler fires; draws stop once the game ends). Pin:
   `tests/test_per_draw_triggers_fire_per_card.py`.

3. **Spell Pierce unpaid tax — DID NOT REPRODUCE.** The offer path, the
   payment, and the real `AICallbacks.decide_optional_cost` verdict are all
   correct in a reconstruction of the replayed shape (the production seam
   answers "pay" with 4 untapped lands). The replayed instance was an
   *unpayable* tax: Ponza had 4 lands, tapped 3 for the 3-mana spell and
   held 1 — below the {2}. The misleading part was the log line, which
   measured "N mana remaining" BEFORE the cost was paid and so counted the
   mana earmarked for that very spell ("4 mana remaining" with one untapped
   land). The line is now emitted post-payment. Pins:
   `tests/test_counter_tax_is_offered_and_payable.py` (production wiring —
   previously covered only through stub callbacks) and its mana-log case.

## LD MECHANIC MEASURED (2026-08-28, post-#563)

The LD hole this doc flagged is closed and confirmed: **Boros Ponza
44.4 -> 56.9 (+12.5pp, n=20 Bo3 field)** — firmly in band. Its row was
structural exactly as stated; with 11 mainboard slots alive the deck plays
real land denial. Dimir's row should soften further at the next matrix
(one more victim class repaired).
