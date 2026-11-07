---
title: "Ruby Storm vs draw-punish boards: the projected draw count was blind to bound counts"
status: active
priority: primary
session: 2026-11-07
depends_on: [docs/design/rules-foundation-sweep-tracker.md]
tags: [ai, storm, draw-tax, root-cause]
summary: "Bo3 replay root cause for Ruby Storm's outlier (12.8% vs band 40-55): with a draw-punish permanent across the table the AI chained cantrips into its own death, because the projected draw count came from an oracle regex that read only printed counts. Fixed generically by unit DT; Storm remains far below band, so further root causes are needed."
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
