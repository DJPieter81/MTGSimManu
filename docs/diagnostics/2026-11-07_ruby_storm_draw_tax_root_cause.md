---
title: "Ruby Storm vs draw-punish boards: the projected draw count was blind to bound counts"
status: active
priority: primary
session: 2026-11-07
depends_on: [docs/design/rules-foundation-sweep-tracker.md]
tags: [ai, storm, draw-tax, root-cause]
summary: "Bo3 replay root causes for Ruby Storm's outlier (12.8% vs band 40-55). (1) The draw-tax projection was blind to bound draw counts: fixed by unit DT. (2) Storm copies resolved inside the original with no state-based check, and the copy plan ignored the spell's own damage, so lethal Grapeshots were split: fixed by units SY and CD. (3) Open: a chain with no payoff in reach still starts, because payoff reachability is a boolean that any dig card in hand satisfies."
---

# Ruby Storm vs draw-punish boards

## The outlier
- In the 2026-11-07 n=60 matrix (26 decks), Ruby Storm is at 12.8% against its band of 40-55. The miss is 27.2 pp, the largest of the nine decks out of band.

## The replay (loop-break rule)
- **Match:** Bo3 Ruby Storm vs Dimir Midrange, s50000, game 1 (`run_meta.py --bo3`), turn 5.
- **The board:** Dimir has Orcish Bowmasters, which pings 1 per card Storm draws beyond the first in its draw step.
- **The play:** Storm chains rituals and cantrips from 5 life and never reaches a finisher. At 2 life, holding Hex Magic and two lands, it scores Hex Magic at 0.0 and casts it. "Exile all the cards from your hand, then draw that many" draws two, and Bowmasters deals the last two damage.
- **Where expected value diverged from correct play:** the cast projection (`ai/ev_evaluator._project_spell`) prices each projected draw at the opponent's draw tax, but the count (`_projected_real_draws`) came from a regex reading only "draw <number> cards". "Draw that many" projected 0 draws, so 0 tax, so the self-kill was invisible.
- **A second gap:** `compute_play_ev` returned the small deferral cost of a cast with no same-turn value before it checked the lethal-to-self floor.

## The subsystem and the fix
- **The subsystem:** the AI's cast projection (`ai/ev_evaluator.py`), specifically the draw count that feeds the draw tax.
- **The fix (unit DT, generic):**
  - `ai/draw_projection.py` reads the count from the typed spell: a printed count, or "that many" bound to the hand an earlier instruction moves.
  - The lethal-to-self floor now precedes the deferral check.
- **The class:** 57 pool cards print "draw that many"; 14 pool cards punish an opponent's draws.
- **Effect on the replay:** Storm no longer draws itself to death. It loses to combat on turn 6 instead of 5, and the anchor is refreshed.

## What remains (next replays)
- Storm still loses this matchup: it chains without a finisher in hand or library reach.
- The next replay should name why no finisher is reached. Candidates to check against the trace, not yet tested:
  - the finisher route (Wish, or Grapeshot's reach from the sideboard);
  - the chain's stop condition.

## Second replay: storm copies and the copy plan (units SY, CD)
- **Match:** Bo3 Ruby Storm vs Boros Energy, s50000.
- **What went wrong:**
  - Game 2, turn 5 (before unit SY): 8 of 9 Grapeshot damage went into a 3-loyalty planeswalker. The copies ran inside the original's resolution, with no state-based check between them, so a 0-loyalty walker stayed a target.
  - After unit SY, the same game showed a second defect. Grapeshot plus 8 copies was 9 damage into 9 life, but the copy plan counted only the copies, so it split them and Boros survived at 3.
- **The subsystems:** the engine's storm copies (`engine/cast_manager.py`) and the AI's copy plan (`ai/damage_targets.plan_copy_damage`).
- **Effect:** with units SY and CD the match is a 2-0 Storm win (it was a 1-2 loss).
- **Found on the way:** a performance defect (`CardTemplate.effects` re-deriving its parse key on every access) that aborted a 60-token game at the wall-clock budget. Fixed in the same series.

## Third root cause (open): a chain with no payoff in reach still starts
- **Match:** Bo3 Ruby Storm vs Dimir Midrange, s50000, game 1, Storm's turn 4 (trace global turn 7).
- **The position:**
  - Storm has 3 lands and a Ruby Medallion, and is holding Desperate Ritual x2, Twinned Vision and Hex Magic.
  - Past in Flames was discarded by Thoughtseize.
  - Storm has no finisher in hand. Its only route to one is Wish, 2 copies in a 46-card library.
- **The play:**
  - Desperate Ritual scores -0.1 and is cast, then a second one at -0.0.
  - The Past in Flames flashback and a Reckless Impulse flashback follow.
  - Storm ends at storm 4 with 0 mana and its rituals and Past in Flames spent, and dies on turn 6.
- **Where expected value diverged from correct play:**
  - `ai/ev_evaluator._payoff_reachable_this_turn` clause (d) returns True whenever any dig card is in hand (`_is_real_dig`). The ritual therefore gets the `combo_continuation` same-turn signal instead of the no-signal deferral.
  - Its near-zero score clears the pass floor (`PLAY_VALUE_FLOOR`, -5).
  - Reachability is a boolean. It ignores how many cards the digs actually see (`ai.draw_projection` counts them), how many payoffs the library holds, and whether the mana left after the dig can still pay for the payoff.
- **The subsystem:** `_payoff_reachable_this_turn` in `ai/ev_evaluator.py`, the chain-continuation gate.
- **Next step:** give the gate the chain's real reach.
  - The digs' projected draws against the library's payoff density, the existing hypergeometric primitives (`ai/deck_knowledge.py`, `ai/outcome_ev.py`), compared in one currency with holding the resources.
  - The mana the chain has left for the payoff.
  - No new threshold literal; test red first, with a Storm case and one other chain deck.


## Not the lever: the pass floor (`PLAY_VALUE_FLOOR`, -5)
- **Seen twice:**
  - In the Storm replay above, a ritual at -0.1 is cast.
  - In Eldrazi Tron vs Azorius Control (WST) s50000, game 1, turn 3, Azorius casts Chalice of the Void at -0.1 for X=0. That locks nothing: the engine's Chalice X picker found no X with positive net disruption, and the cast was still made.
- **Why both fire:** the cast clears the floor, which is -5, not the value of passing (0).
- **Why raising the floor to 0 is not the fix:** the score scale is not anchored at "pass = 0". Correct plays score negative too; cantrip bait into counter density sat at -15, and Storm then passed every turn and lost (`tests/test_storm_baits_counters_with_cantrips_at_high_counter_density.py`). The orchestration audit (`docs/diagnostics/2026-09-16_ev_orchestration_audit.md`) already lists the floor as a stand-in for a valued pass candidate.
- **The fix belongs in the valuations that read too high:**
  - the reachability gate here;
  - a Chalice X whose net disruption is not positive should not be a cast at all. That X is picked in `engine/cast_manager.py`, a strategic choice in the engine (7 pool cards; below class size on its own).

## Third root cause, built as unit RR (AI)
- **The gate now weighs reach:** `combo_calc._digs_reach_today_beats_holding` compares the digs' chance to find a route to the closer today against the chance after holding a turn, discounted by the opponent's discard.
- **Measured** on Storm's row (`rr-post`): 15.2 → 16.0, within noise.
- **The replay turn behind the third root cause was in fact a live route.** A Wish exiled by Reckless Impulse was playable that turn only. The misplay there is sequencing: the chain's mana went to Past in Flames and a dig, and the Wish expired uncast.
- **Next:** that sequencing, in the chain planner (`ai/combo_chain.py` / `_estimate_combo_chain`). The next replay should name why a castable finisher route ranks below a flashback granter with no closer of its own.


## Fourth root cause (named, not built): the chain planner cannot see a tutor's kill
Traced on Storm's turn 4 of the same replay (trace global turn 7), with the candidate list at each step.
- **The position:** a Wish exiled by Reckless Impulse is playable this turn only.
  - Wish scores -12.4 at every step: the tutor-as-finisher branch holds a tutor while chain fuel remains.
  - Storm casts Desperate Ritual twice (-0.13, -0.03), Past in Flames from the graveyard (-0.04) and a flashback Reckless Impulse (+0.01). It ends at 0 mana with the Wish uncast.
- **The chain could not kill that turn.** With 5 mana at storm 3, Wish plus Grapeshot deals about 5 damage into 17 life.
- **Why the rituals were cast:** the ritual gate skips its hold whenever a finisher route is in reach (`_has_storm_finisher`: the exiled Wish), even when the chain cannot kill.
- **Why it was not fixed by gating on `ComboAssessment.is_ready`:** `combo_chain.find_all_chains` never models a tutor. A Wish → Grapeshot kill would never read as ready, so that gate would hold real lethal Wish turns.
- **The subsystem:** the chain planner (`ai/combo_chain.py`: `classify_card`, `find_all_chains`).
- **Next unit:**
  1. A tutor whose pool holds a payoff is that payoff in the chain, at the tutor's cost plus the payoff's, with one more storm.
  2. Then the ritual gate holds a reachable but non-lethal finisher route when a later turn remains.
- **Tests first:** a Storm case, plus another tutor-to-payoff chain.

## Fifth lead (named, not built): Storm never sideboards
- **The evidence:**
  - A game-1 batch of 8 seeds each against four decks: Storm wins 11 of 32, and 0 of 8 against Dimir.
  - The decision-gate arms (`rr-post`, `tc-post`, `tc2-post`, `pr-post`) moved Storm's Bo3 row by no more than noise.
  - In Bo3 Ruby Storm vs Boros Energy s51500, Storm wins game 1 on turn 4 and loses games 2 and 3.
- **The finding:**
  - The default sideboarding path (`engine/sideboard_manager.sideboard`, legacy deck-name and card-name string matching) boards in nothing for Ruby Storm against any opponent. Four Thoughtseize, the standard plan against Dimir and Azorius control, never come in.
  - The oracle-driven solver (`SB_SOLVER=new`, `ai/sideboard_solver.plan_sideboard`) swaps one Fatal Push for one Hex Magic against every opponent alike.
- **The subsystem:** sideboarding. It is a strategic choice that lives in `engine/`, and its legacy path matches names, which the abstraction contract forbids.
- **Next unit:** make the oracle-driven solver the owner. Test-first: hand disruption comes in against counter-dense and discard-dense opponents (BHI priors), and a Storm case is paired with another deck whose sideboard answers a matchup. Measure on Bo3 (the canonical format), not game 1.

## Fifth lead, measured (unit SBH)
- **The solver now values hand attacks.** Under `SB_SOLVER=new` (the `sbh-post` arm), Storm's row falls 15.8 → 8.4.
- **Why:** Storm cuts its own digs (Hex Magic x4, Twinned Vision) for Thoughtseize against every opponent, and the field boards in real hate against it (Consign to Memory, Mystical Dispute, Obsidian Charmaw).
- **Default unchanged.** The solver needs plan-aware values for a deck's own cards before it can own sideboarding.
- **Re-derive the band.** Storm's band of 40-55 was set against a field that never sideboarded against it; it should be re-derived once the field does.


## The list is current (checked 2026-11-08)
- **Source:** a fresh mtgtop8 fetch (`data/tier1_decklists/2026-11-08/`, run by the `weekly` workflow in `decklists_only` mode, since this session cannot reach mtgtop8).
- **The newest Ruby Storm list** (event 93409) matches the registered mainboard card for card. Its sideboard differs by one card: 1 Brotherhood's End where we play 1 Fatal Push.
- **The other new list** (event 93268) runs the same 37 spells with a slightly different mana base and sideboard (Veil of Summer, Nature's Claim, no Thoughtseize).
- **So the list is not the cause of Storm's gap.**
- **Storm's metagame share is 4%.** The registry's seven "gaps" in `DIFF_REPORT.md` are name differences already mapped on 2026-11-01: UW Control → Azorius Control, UrzaTron → Eldrazi Tron, Dimir Control → Dimir Midrange, and so on.

## Real-world baseline and the next mechanism (2026-11-08)
- **Real-world win rates (web search):** Ruby Storm 49.2% at the 2026 Fall Regional Championships, and 46% on MTGO (n=260). Storm's band [40, 55] stands (`debe4bb`), and the sim (13-16) is about 33 pp low.
- **The calibration report's worst pairs are Storm against low-interaction decks,** where the real-world priors favour Storm: Domain Zoo 8.0 (prior 50-65), Izzet Prowess 15.0 (50-65), Living End 16.5 (50-65). So the gap is not mainly opponent interaction.
- **Against Living End, game 1 only** (16 seeds; `storm_stats.py`): Storm wins 6 of 16 (37.5%). Its kills land on turns 5-11. The legacy sideboard table changes nothing in this pairing, so the Bo3 cell (16.5) is the per-game weakness compounded, not sideboarding.
- **The mechanism seen** (s56500, turn 4): Storm goes off for a chain of more than 20 spells (Hex Magic, Past in Flames, flashbacks, three digs) and never finds a finisher.
  - The main deck has none: its only routes are 2 Wish, and the chain dug through a large part of the library without finding either.
  - In 6 of the 16 games Storm never cast a finisher.
- **Next question:** how real Ruby Storm closes a turn like this. Candidates, not yet checked:
  - its other kill routes (Ral, Leyline Prodigy's damage; a Past in Flames that rebuys Wish);
  - whether the sim's Wish reaches every sideboard finisher;
  - the mulligan's Wish-access keep rule.
