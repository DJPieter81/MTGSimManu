---
title: RETRACTED perf claim (live LLM scorer, not the Cage fix); reanimator WRs still provisional
status: superseded
priority: primary
session: 2026-08-30
supersedes: []
superseded_by: []
depends_on: [docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md]
tags: [performance, measurement, reanimator, graveyard, calibration]
summary: >
  The phantom-Cage fix (#567) unlocked graveyard casting for the controllers of
  446 permanents that were wrongly acting as symmetric Grafdigger's Cages. That
  is correct behaviour, but it made reanimator matches ~4x more expensive to
  simulate (measured 35s/Bo3 match vs ~9s the same day, same box, same method).
  As a result the reanimator field WRs recorded in the 2026-08-27 doc (+12.5 /
  +8.1 into band) are PROVISIONAL: they were measured against an engine that was
  suppressing those decks, and the corrected re-measurement could not be
  completed in this environment.
---

# Post-Cage performance cost + provisional reanimator WRs

## 1. Measured performance regression

| Measurement | Before (same day, pre-#567) | After (#568 HEAD) |
|---|---|---|
| Instant Reanimator vs Boros Energy, 3 Bo3 matches | ~28s (~9s/match) | **106s (~35s/match)** |

Same box, same command, same method as the earlier A/B that cleared the
mechanic classes of causing a slowdown — so this is a real cost, not host
noise. (Separately, this container has been ~4x slower than earlier in the
week since a restart; that factor is environmental and additive.)

**Hypothesis (evidence-based, NOT yet confirmed):** `can_cast` no longer
switches off flashback/escape for 446 permanents' controllers, so reanimator
decks now enumerate and score far more legal plays per decision. The cost is
concentrated in the main-phase enumeration + scoring loop. First probe should
be a profile of `decide_main_phase` on a reanimator board with a stocked
graveyard, comparing play-candidate counts before/after the fix.

**Why it matters beyond convenience:** a full 300-pair matrix at this cost is
roughly an order of magnitude more wall-clock than the 19-25 min it used to
take. Calibration runs are the project's primary feedback loop.

## 2. Reanimator WRs are provisional

`docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md` records Instant
Reanimator 35.4 -> 47.9 and Goryo's 28.8 -> 36.9 (both into band) after the
blink-timing / discard / mulligan fixes. Those runs predate #567. Because the
phantom Cages were SUPPRESSING these decks across most of the field, the
corrected numbers may move in either direction. Do not cite them as settled.

Partial post-#567 data (n=20, deadline-capped, checkpointed before the run was
reaped): Instant Reanimator vs Boros Energy 40%, vs Jeskai Blink 65%. Two
matchups is not a field average and is recorded only so the work is not lost.

## 3. Why the re-measurement did not complete

Two compounding environment facts, both verified:
- Background processes in this container are reaped within ~10-20 minutes
  (three consecutive attempts, including `setsid`/`nohup` with stdin detached).
- At 35s/match, even an n=8 field sweep exceeds a 10-minute foreground call.

Per-opponent checkpointing (`scratchpad/sweep_par.py` pattern: append each
matchup result the moment it lands, skip recorded pairs on resume) is the right
shape for this environment and did preserve partial work; it is worth
re-creating as a committed tool if these conditions persist.

## Next steps
1. Profile the main-phase enumeration for graveyard-castable plays (item 1).
2. Re-measure both reanimator decks' field WR in an environment that can run a
   sweep uninterrupted; update the 2026-08-27 doc with the corrected figures.


## RETRACTION (2026-08-30, same day) — the perf claim above is WRONG

Section 1 blamed the phantom-Cage fix for a 4x slowdown. **That is retracted.**
Profiling the actual game showed the two dominant costs were a redundant card-DB
load and `ai/llm_decision_scorer.weight`, NOT graveyard-play enumeration.

Root cause, and it was self-inflicted:

- `llm_decision_scorer.weight` makes a **live LLM call** (`agent.run_sync`)
  unless `MTG_LLM_DECISION_SCORER_OFFLINE=1` is set. CI sets it at the top of
  `.github/workflows/abstraction-contract.yml`; local runs do not.
- A container restart had wiped `pydantic_ai`, so `_get_agent()` returned None
  and the live path cost nothing. I then reinstalled `pydantic_ai` to "repair
  the environment" — which silently switched the live LLM path ON for every
  subsequent local sim.

Measured, same box, same command, same 3 Bo3 matches:

| Condition | Time |
|---|---|
| Without the flag (live LLM calls) | **106s** |
| With `MTG_LLM_DECISION_SCORER_OFFLINE=1` | **29s** |

29s ≈ 9.7s/match, matching the pre-#567 baseline. **There is no performance
regression from the Cage fix.** Do not spend a session profiling one.

**The more serious consequence — measurement validity.** A live LLM weight is
non-deterministic, so any local sim run after that reinstall, without the flag,
was neither reproducible nor comparable to CI. In the 3-match probe the same
matchup read 0% without the flag and 67% with it. Treat any local WR measured
in that window as unusable, not merely noisy.

**Standing rule (now in CLAUDE.md):** every local sim run sets
`MTG_LLM_DECISION_SCORER_OFFLINE=1`, matching CI.

Section 2 (reanimator WRs provisional) still stands, for its original reason:
those numbers predate the Cage fix, which was suppressing the decks.
