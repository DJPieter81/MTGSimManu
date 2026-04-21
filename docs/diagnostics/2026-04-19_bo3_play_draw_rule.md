---
title: Bo3 play/draw rule not implemented
status: archived
priority: historical
session: 2026-04-19
resolved: 2026-04-21
tags:
  - bo3
  - rules
  - engine
  - quick-fix
summary: "RESOLVED 2026-04-21: engine/game_state.py:setup_game accepts forced_first_player; engine/game_runner.py:run_match forwards prev-game loser into G2/G3. Tests in tests/test_bo3_play_draw.py."
---

> **Status: resolved (2026-04-21).** `GameState.setup_game` now accepts
> `forced_first_player`, and `GameRunner.run_match` passes the prior
> game's loser into games 2 and 3 (CR 103.2, default loser-plays). See
> `tests/test_bo3_play_draw.py` and Phase 11a commit.

# Diagnostic: Bo3 play/draw rule not implemented

**Date:** 2026-04-19
**Priority:** Medium (affects Bo3 statistical fidelity, not individual game correctness)
**Observed:** `replays/boros_rarakkyo_vs_affinity_s63000_bo3.txt`
**Root cause:** `engine/game_state.py:410` — `self.active_player = self.rng.randint(0, 1)` on every `setup_game` call.

## The bug

In Magic: The Gathering's Bo3 match structure, after game 1:
- **Loser of the previous game chooses** whether to play or draw first in the next game.
- Near-universally, the loser chooses to play (on-play has ~54% win rate vs ~46% on the draw).

The sim instead:
- Rolls a fresh random die for every game.
- No communication between game outcomes and next-game play/draw assignment.

## Evidence

Seed 63000 Bo3 log, game-by-game:

| Game | Header label | Actually on play | Result |
|---|---|---|---|
| G1 | `Boros (P1) vs Affinity (P2)` | Affinity | Boros wins |
| G2 | `Affinity (P1) vs Boros (P2)` | **Boros** | Affinity wins |
| G3 | `Boros (P1) vs Affinity (P2)` | Affinity | Affinity wins |

G2 anomaly: Boros won G1, so Affinity (loser) should have chosen play for G2. Sim instead put Boros on the play.

## Impact on sim statistics

The bug produces *noise* rather than *systematic bias* in individual matchup WRs (symmetric random coin flips), but:

1. **Decks that compound play-advantages are under-valued.** A deck that wins G1 on the play will often also get the play in G2 (because the opp elects to go second post-sideboard) in real Modern. Sim forces re-randomization — decks lose their structural G2 edge.

2. **Heavy-sideboard matchups are mis-modeled.** Affinity loses G1, sides in artifact hate, and real-life gets the play in G2 to race. Sim gives the play back to Boros 50% of the time.

3. **Match-level WRs are noisier than they should be.** Individual games are fine; the Bo3 layer loses signal.

## Also: labeling confusion in the log

Separate from the mechanics bug, the log uses `P1`/`P2` ambiguously:
- Header label: deck-order-based (Boros always written first in G1/G3, Affinity first in G2 for some reason)
- Pre-game label: turn-order-based (`P1 (on play)`)

These use the same symbols (`P1`, `P2`) but mean different things. Fix: either (a) always assign P1 = on-play (and relabel per game), or (b) use distinct symbols like `active/defending` for turn order vs `D1/D2` for deck labels.

Low priority but worth aligning.

## Fix shape

```python
# engine/game_runner.py run_match():
# Pass previous game result into setup. Loser elects play/draw (use
# a deck-profile flag for aggro/combo decks that want draw due to
# card access; default = play).

on_play = None  # determined by die roll first game
for game_num in range(1, 4):
    if game_num == 1:
        # Random die roll (existing behavior)
        on_play = None
    else:
        # Loser of previous game chooses; default to play.
        prev_loser = 1 - games[-1].winner
        on_play = prev_loser
        # (Could extend: consult deck profile for "prefer draw" decks
        # like some combo or card-advantage-greedy archetypes)
    
    result = self.run_game(..., forced_first_player=on_play)
```

And in `GameState.setup_game`:

```python
def setup_game(self, deck1, deck2, forced_first_player=None):
    ...
    if forced_first_player is not None:
        self.active_player = forced_first_player
    else:
        self.active_player = self.rng.randint(0, 1)
```

## Test shape

```python
def test_bo3_loser_chooses_play():
    # Setup: forced G1 winner (e.g. mock so deck1 wins G1)
    # Run Bo3.
    # Assert: G2 opens with deck2 as active_player.
    # Assert: if G2 also won by deck1, G3 opens with deck2 again.
```

## Severity and priority

Rules-correctness bug, medium severity. The sim is internally self-consistent but doesn't match Modern tournament Bo3 mechanics. Fix is ~30 min of careful work and one test. Doesn't block any of the EV overhaul work in `docs/design/ev_correctness_overhaul.md` — can be done in parallel or after.

Recommend fixing after the EV overhaul lands so baseline re-calibration isn't muddied by two concurrent changes.
