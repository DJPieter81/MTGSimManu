---
title: MAX_TURNS=25 decides a third of aggro-vs-control games on life total, deflating control
status: active
priority: primary
implemented: 2026-08-30
session: 2026-08-30
depends_on:
  - docs/diagnostics/2026-08-30_zoo_decklist_hypothesis_falsified.md
  - docs/diagnostics/2026-08-20_domain_zoo_overperformance_root_cause.md
supersedes: []
superseded_by: []
tags:
  - measurement-harness
  - turn-cap
  - calibration
  - domain-zoo
  - azorius-control
  - aggro-skew
summary: >
  `MAX_TURNS = 25` half-turns (~display turn 12-13) ends a game by comparing
  LIFE TOTALS, which an aggro deck wins almost by construction. Against
  control it fires in 32% of games (vs 6% across a mixed field). Raising the
  cap to 60 removes every timeout and drops Domain Zoo vs Azorius Control
  from 82% to 70%; across field sweeps both control decks GAIN (4/5c Control
  34.4 -> 50.0, into band; Azorius Control 24.0 -> 31.2) while both aggro
  decks are flat (Zoo -1.1, Boros -1.0). This is a measurement-harness
  artefact, not a Magic result, and it is a partial — not complete —
  explanation of the aggro-high/control-low skew. IMPLEMENTED the same
  session (MAX_TURNS 25->60, capped game = draw, plus a Bo3 draw-counting bug
  fixed first): 4/5c Control moved INTO band (38.1 -> 52.0), Azorius Control
  improved but is still broken (21.0 -> 28.6), Domain Zoo unchanged (83.9).
---

# The turn cap decides aggro-vs-control games on life total

## Mechanism

`engine/constants.py`: `MAX_TURNS = 25`. That counts half-turns, so it binds
around **display turn 12-13**. At the cap (`engine/game_runner.py:928`):

```python
elif game.turn_number >= game.max_turns:
    if game.players[0].life > game.players[1].life:
        winner = 0
    elif game.players[1].life > game.players[0].life:
        winner = 1
    else:
        winner = None
    win_condition = "timeout"
```

The winner is **whoever has more life**. An aggro deck has spent the game
reducing its opponent's life total and typically taking little back, so it
wins that comparison close to automatically. A control deck that has
stabilised at a low total but assembled an unbeatable board still loses.

This is not a Magic outcome. It is the harness adjudicating an unfinished
game with a metric that correlates almost perfectly with being the beatdown.

## How often it fires

Measured, Bo1, `MTG_LLM_DECISION_SCORER_OFFLINE=1`:

| sample | timeout share |
|---|---|
| Domain Zoo vs 8 mixed opponents, 32 games | 2/32 = **6%** |
| Domain Zoo vs **Azorius Control**, 40 games | 13/40 = **32%** |

The cap barely matters against decks that end games quickly. Against
control — the matchups that produce the worst calibration outliers — it
decides one game in three.

## Effect on the result

Domain Zoo vs Azorius Control, 40 Bo1 games per row, same seeds:

| MAX_TURNS | ~display turn | Zoo WR | timeouts | damage |
|---|---|---|---|---|
| **25 (current)** | T12 | **82%** | 13 | 27 |
| 40 | T20 | 78% | 5 | 35 |
| 60 | T30 | **70%** | **0** | 40 |

Monotonic, with timeouts going 13 -> 5 -> 0. At n=40 the 82-vs-70 endpoints
are near the edge of significance individually (95% CI ~ +/-12pp), but the
trend across three cap values with an identified mechanism is signal, not
noise.

## It is the control side that moves

Field sweeps, n=4 per opponent over the full 24-deck field (~96 games per
cell, so each average carries roughly +/-10pp at 95%):

| deck | cap=25 | cap=60 | delta |
|---|---|---|---|
| Azorius Control | 24.0% | 31.2% | **+7.2** |
| 4/5c Control | 34.4% | **50.0%** | **+15.6** |
| Domain Zoo | 84.4% | 83.3% | -1.1 |
| Boros Energy | 60.4% | 59.4% | -1.0 |

**Both control decks gain; both aggro decks are flat.** 4/5c Control moves
from below band into band. No single number here is individually decisive at
this n — the two-up/two-flat split along the archetype axis is what carries
the finding, because it is exactly what the mechanism predicts and is
unlikely to arise by chance in that pattern.

Note the asymmetry: Zoo's HEAD-TO-HEAD against Azorius drops 12pp, but its
FIELD average barely moves, because only a handful of its 24 opponents are
control decks. The cap does not inflate aggro averages much; it deflates
control averages a lot. That is the correct way round for explaining why the
bottom of the WR table is populated by control decks.

## What this does and does not explain

**Does:** a large part of why control decks sit far below band, and why the
Zoo-vs-control cells specifically read 83-100%.

**Does NOT:** Zoo's overall ceiling. At an uncapped 60 turns Zoo still beats
Azorius 70%, and its field average is unchanged at 83.3%. The cap accounts
for roughly 12 of the ~30pp excess in that matchup and close to none of the
field average. Anyone reading this as "the outlier is solved" is
over-reading it, which is the specific failure mode this session repeated
several times.

## IMPLEMENTED (2026-08-30, same session)

The recommendation below was acted on. Both halves shipped together, because
the draw path was unusable on its own:

* **A capped game is now a DRAW** (`game_runner.adjudicate_capped_game`,
  CR 104.4). Life total no longer breaks it.
* **`MAX_TURNS` 25 -> 60.** At 60 the cap did not fire at all in the measured
  sample, and 80/120 bought nothing further.
* **A draw-counting bug was fixed first.** `run_meta`'s Bo3 loop read
  `if winner_deck == deck1: score[0] += 1 else: score[1] += 1`, and
  `winner_deck` is the literal string `"draw"`, so every drawn game was
  silently a game win for whichever deck was named second. Scoring capped
  games as draws would have made that strictly worse than the life tiebreak
  it replaces. Now routed through `score_game_result()`. The Bo3 loop is also
  bounded on GAMES PLAYED (`BO3_MAX_GAMES`), since gating only on
  `score < 2` cannot terminate once games can end without incrementing
  either side. Field/matchup accumulation was audited and was already
  correct — draws land in a `"draw"` key that the percentages do not read.

### Anchor evidence

6 of 29 entries drifted and **every one had `turns=13`**, the old cap — a
fifth of the drift-detection fixture was recording tiebreak verdicts rather
than game outcomes. Refreshed by hand (not via the whole-fixture refresher):

| entry | before | after |
|---|---|---|
| Azorius Control vs Azorius Control (WST) s50000 | WST wins T13 | **draw** T30 |
| Azorius Control (WST) vs (WST v2) s50500 | WST wins T13 | **draw** T30 |
| 4/5c Control vs Pinnacle Affinity s50500 | Pinnacle wins T13 | **4/5c Control wins** T17 |
| Eldrazi Tron vs Amulet Titan s50500 | T13 | T14 |
| Boros Ponza vs Boros Energy s51000 | T13 | T14 |
| Eldrazi Ramp vs Broodscale Bloodchief s52000 | T13 | T14 |

The two control mirrors now correctly draw — they genuinely cannot kill each
other — and 4/5c Control converts a tiebreak LOSS into a real win.

### Field result (n=6, implemented vs the cap=25 baseline)

| deck | before | after | band |
|---|---|---|---|
| Azorius Control | 21.0 | **28.6** | still far below |
| 4/5c Control | 38.1 | **52.0** | **moved INTO band** |
| Jeskai Blink | 48.6 | 47.9 | in band |
| Dimir Midrange | — | 57.6 | in band |
| Domain Zoo | 84.4 | 83.9 | still far above |
| Boros Energy | 60.4 | 65.3 | slightly above |

These track the pre-implementation A/B closely (predicted Azorius ~31.2,
4/5c ~50.0, Zoo ~83.3, Boros ~59.4), which is the check that matters: the
effect reproduces from the mechanism rather than from noise.

**Honest limits.** Only 4/5c Control actually changed band. Azorius Control
is better but still broken at 28.6, and Domain Zoo is untouched at 83.9 —
the cap was never its explanation. n=6 per opponent leaves roughly +/-10pp
on each field average, so the individual numbers are soft; the pattern
(control up, aggro flat) is the finding.

**Cost, stated plainly:** ~+52% wall-clock on control-heavy matchups. A full
matrix is correspondingly more expensive, and every cell in the existing
dashboard is now stale.

## Original recommendation — kept for the record

Raising `MAX_TURNS` is not a free correctness fix. It:

* changes **every number** in the matrix, the dashboard and the WR anchor;
* increases sim wall-clock (games run to completion rather than stopping at
  T12), which matters given a full matrix already costs hours; and
* trades one arbitrary constant for another — 60 is not principled either,
  it is simply high enough that the cap stopped binding in this sample.

A more principled variant, if this is pursued: keep a cap for wall-clock
safety but stop adjudicating on life total. Score a capped game as a DRAW
(the rules answer for an unfinished game) rather than awarding it to
whoever is ahead on life. That removes the systematic aggro bias without
requiring longer games, at the cost of more draws in the matrix.

Measurement to run before adopting either: a full Bo3 matrix at the new
setting, on a quiet box, since every cell moves.

## Reproducing

The cap is read at `GameState.__init__` into `self.max_turns`, so a sweep
patches both `engine.constants.MAX_TURNS` and that attribute:

```python
orig = gs.GameState.__init__
def patched(self, *a, **k):
    orig(self, *a, **k); self.max_turns = cap
gs.GameState.__init__ = patched
```

`GameResult.win_condition` is `"timeout"` for capped games, which is how the
32%/6% split above was counted.
