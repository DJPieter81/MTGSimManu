---
title: The per-game deadline was wall-clock — load rewrote win rates; fixed as a CPU budget
status: active
priority: secondary
session: 2026-09-06
supersedes: []
superseded_by: []
depends_on: []
tags: [measurement, timeout, determinism, infrastructure, calibration, storm]
summary: >
  "Ruby Storm draws every game at turn 6 / field 0.0%" was not a Storm bug:
  the 8 s per-game deadline was WALL-CLOCK, so a contended box cut healthy
  games off and recorded them as draws — and every aggregator then handed
  each draw to the second-named deck as a win. Fixed in three units: the
  valve is a CPU budget owned by engine/game_budget.py (load-invariant,
  30 CPU-s, reported as "aborted"); draws/aborts are counted and announced,
  never credited; the process holds one CardDatabase instead of two (the
  17 s "first game" was a duplicate load). Record here so neither the Storm
  draws nor the 17 s game is ever re-diagnosed.
---

# The per-game deadline was wall-clock (2026-09-06)

## What was reported

At the start of the outlier-deck phase, a `--field "Ruby Storm"` run came
back **0.0% average** and a `--bo3 "Ruby Storm" "Domain Zoo"` had **all five
games drawn at turn 6**. It looked like a Storm-specific hang or loop and
was queued as the first thing to fix.

## What was measured (quiet box, `MTG_LLM_DECISION_SCORER_OFFLINE=1`)

1. **The draws do not reproduce.** Ruby Storm vs Domain Zoo on the 50000
   grid: 6 Bo1 games → 4-2, zero draws; the Bo3 at seed 50000 resolves
   normally (G1 Zoo T5, G2 Storm T5, G3 …). Nothing Storm-specific is
   broken. The earlier reading came from a container where the deadline
   was binding — a loaded box, and/or the live LLM scorer path with the
   offline flag unset (CLAUDE.md sequencing rule 0).

2. **The deadline was wall-clock, so load became draws.**
   `GAME_TIMEOUT_SECONDS = 8.0` was armed as `monotonic() + 8` and polled at
   six loop heads across two modules (`game_runner` ×5,
   `activation.can_activate`). Idle per-game cost:

   | matchup | wall = CPU (s), seeds 50000/50500/51000 |
   |---|---|
   | Ruby Storm vs Domain Zoo | 0.2 / 1.4 / 0.2 (3.9 max over the 24-game probe below) |
   | Eldrazi Tron mirror | 0.8 / 0.4 / 0.5 (2 of 3 reach the turn cap) |
   | Azorius Control (WST) mirror | 0.9 / 0.9 / 0.9 (3 of 3 reach the cap) |
   | Amulet Titan vs Jeskai Blink | 0.2 / 0.1 / 0.2 |
   | 4c Omnath vs 4/5c Control | 0.6 / 0.3 / 0.4 |

   The 08-25 probe (`e0ddc76`) already showed 8 s never binds idle; the
   anchor incident (`083393b`) showed it binds on the CI runner. Under
   three parallel workers plus anything else on the box, a 1.7 s game
   takes >8 s of wall time and is cut off. Only the WR anchor was protected
   (it rebinds the constant around its replay); production runs were not.

3. **An aborted game was mislabelled and silently credited.** A deadline
   abort at turn N < cap fell to the `else` branch of the result logic →
   `win_condition = "draw"`, indistinguishable from a CR 104.4 draw
   (`"timeout"` was only the turn cap). Every aggregator then tallied
   `wins[r.winner_deck]` and reported `wins[d1] / n`, and the matrix wrote
   the reverse cell as `100 - pct`: **every drawn or aborted game was a
   win for the second-named deck.** This is not a corner case — control
   mirrors reach the turn cap in 2 of 3 / 3 of 3 games — and no results
   file, calibration verdict, or dashboard recorded a draw or abort count.

4. **Every process loaded the card database twice.** The 17.5 s "first
   game" at seed 50000 was not game time. cProfile:
   `EVPlayer.__init__ → gameplan.create_goal_engine → _lookup_deck_and_db →
   sideboard_manager._get_card_db → CardDatabase()` — a second full
   22.5k-card load (16–29 s) although the runner already held one. Each
   `mp.Pool` worker paid it twice (2 × 16 s start-up, double memory — the
   pressure CLAUDE.md records as killing `test_parallel_matrix` on 2-core
   CI). It ran before the deadline was armed, so it did not truncate
   games; it was the largest single contributor to worker load.

## What changed (three commits, failing-test-first)

**Unit 1 — `engine/game_budget.py` owns the valve** (`0fa190c`). The budget
is CPU seconds (`time.process_time`): a starved process accrues none, so
contention cannot exhaust it; a spinning loop still does, so a hang is still
caught. The six polls call `game_budget.expired(game)`. The value is read
from `ai.constants` at arm time (the anchor's neutralisation contract).
`GAME_TIMEOUT_SECONDS` 8 → **30 CPU-s**: >7× the slowest legitimate game
ever recorded (3.9 CPU-s over the 24-game probe below, ~4 s in the CI anchor
incident), so it fires only on a runaway game. An exhausted budget is `win_condition == "aborted"`.
Tests: `tests/test_game_budget_is_load_invariant.py`.

**Unit 2 — nobody is credited** (`def9f88`). `_worker_matchup` returns
`WorkerResult(d1, d2, pct, errors, pct_reverse, draws, aborted)`; the
reverse matrix cell is the opponent's own wins; per-cell `cell_draws` /
`cell_aborted` are stamped into `metagame_results.json`; the symmetry check
becomes the real invariant `wr1 + wr2 + nobody == 100`; every print path
reports draws and flags aborts; `tools/check_calibration.py` prints
`NOT CALIBRATION-GRADE (aborted=N)` before the band table. Tests:
`tests/test_drawn_games_are_credited_to_nobody.py`.

**Unit 3 — one `CardDatabase` per process** (`b19e90d`).
`CardDatabase.shared()` / `register_shared()`; `GameRunner` registers its
pool; the sideboard accessor and the test cache resolve it. First game on a
fresh runner 17.5 s → 0.25 s. Test:
`tests/test_card_database_loads_once_per_process.py`.

## Load-invariance measurement (the deliverable)

The `e0ddc76` probe shape — 4 matchups × 6 seeds (50000 grid), Bo1 — run
three ways on the fixed code. The three per-seed (winner, turns,
win_condition) tables must be identical and contain no abort.

| run | code | box | CPU max / game | wall max / game | wall total | aborted | draws (rules) | vs idle table |
|---|---|---|---|---|---|---|---|---|
| idle | fixed (`b19e90d`) | quiet (load 0.1) | 3.87 s | 3.87 s | 21.5 s | 0 | 8 | — |
| saturated | fixed | 6 busy loops on 4 cores (load ≈5) | 3.70 s | 4.94 s | 36.0 s | 0 | 8 | **identical** |
| unlimited | fixed, budget = 1e9 | quiet | 3.65 s | 3.65 s | 20.6 s | 0 | 8 | **identical** |
| heavy | fixed | 16 busy loops (load ≈17) | 3.84 s | 16.65 s | 98.3 s | 0 | 8 | **identical** |
| heavy | **pre-fix** (`b0989cc`, wall-clock 8 s) | 16 busy loops (load ≈17) | 25.0 s¹ | 106 s¹ | 190 s | — | 11 | **3 games differ** |

¹ the pre-fix first game includes its duplicate database load (finding 4).

The three pre-fix divergences are exactly the reported symptom — Ruby Storm's
own winning turns, cut off and recorded as draws:

| pair | seed | idle / fixed-heavy | pre-fix heavy |
|---|---|---|---|
| Ruby Storm vs Domain Zoo | 51500 | Ruby Storm, T5, damage | draw, T5, "draw" |
| Ruby Storm vs Domain Zoo | 52000 | Ruby Storm, T6, damage | draw, T6, "draw" |
| Ruby Storm vs Domain Zoo | 52500 | Ruby Storm, T5, damage | draw, T5, "draw" |

Storm's kill turn is the most CPU-expensive turn in the field (the chain
enumeration), so it is the first thing a wall-clock deadline truncates under
load — and each truncation was then scored as a Zoo win by the `100 - pct`
reverse cell. That is the whole "Storm draws every game at turn 6 / 0.0%"
report. Rules draws (control mirrors at the 30-turn cap) are the same 8
games in every fixed run.

Per-seed idle table (winner, turn, condition), reproduced byte-for-byte by
the saturated, unlimited and heavy runs:

| pair | seed | winner | T | cond |
|---|---|---|---|---|
| Ruby Storm vs Domain Zoo | 50000 | Domain Zoo | 5 | damage |
| Ruby Storm vs Domain Zoo | 50500 | Ruby Storm | 6 | damage |
| Ruby Storm vs Domain Zoo | 51000 | Domain Zoo | 5 | damage |
| Ruby Storm vs Domain Zoo | 51500 | Ruby Storm | 5 | damage |
| Ruby Storm vs Domain Zoo | 52000 | Ruby Storm | 6 | damage |
| Ruby Storm vs Domain Zoo | 52500 | Ruby Storm | 5 | damage |
| Eldrazi Tron mirror | 50000 | draw | 30 | timeout |
| Eldrazi Tron mirror | 50500 | Eldrazi Tron | 20 | damage |
| Eldrazi Tron mirror | 51000 | draw | 30 | timeout |
| Eldrazi Tron mirror | 51500 | Eldrazi Tron | 10 | damage |
| Eldrazi Tron mirror | 52000 | Eldrazi Tron | 8 | damage |
| Eldrazi Tron mirror | 52500 | Eldrazi Tron | 7 | damage |
| Azorius Control (WST) mirror | 50000–52500 | draw ×6 | 30 | timeout |
| Amulet Titan vs Jeskai Blink | 50000 | Amulet Titan | 8 | damage |
| Amulet Titan vs Jeskai Blink | 50500 | Jeskai Blink | 7 | damage |
| Amulet Titan vs Jeskai Blink | 51000 | Jeskai Blink | 10 | damage |
| Amulet Titan vs Jeskai Blink | 51500 | Jeskai Blink | 10 | damage |
| Amulet Titan vs Jeskai Blink | 52000 | Amulet Titan | 11 | damage |
| Amulet Titan vs Jeskai Blink | 52500 | Amulet Titan | 10 | damage |

## Consequences for the outlier phase

- Ruby Storm's "turn-6 draw" item is closed; its field must be re-measured
  on the fixed code before any Storm work (`--field "Ruby Storm" -n 20
  --parallel`, with `aborted=0` in the output).
- Every published matrix before this date credited draws to the
  second-named deck. The 2026-09-06 refresh (n=20 Bo3) is affected in
  proportion to its draw count per cell; the next `--matrix --save` records
  `cell_draws` so the size of the effect becomes visible.
- Sequencing rule 2 in CLAUDE.md ("is the machine quiet?") still applies for
  wall-clock reasons, but a busy box now costs time, not correctness.
