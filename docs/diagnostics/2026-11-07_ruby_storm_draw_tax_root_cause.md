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
  - The digs' projected draws against the library's payoff density, the same hypergeometric primitive the mulligan model uses, compared in one currency with holding the resources.
  - The mana the chain has left for the payoff.
  - No new threshold literal; test red first, with a Storm case and one other chain deck.

