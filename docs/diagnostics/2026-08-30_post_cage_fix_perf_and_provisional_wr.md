---
title: Post-phantom-Cage performance cost, and why the reanimator WRs are provisional
status: active
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
