---
title: position_value sign inversion — the bug is real, the fix does NOT improve win rate (FALSIFIED)
status: falsified
priority: diagnostic
session: 2026-08-30
depends_on:
  - docs/diagnostics/2026-08-30_activated_effect_coverage_census.md
supersedes: []
superseded_by: []
tags:
  - clock
  - position-value
  - scoring-primitive
  - falsified
  - azorius-control
summary: >
  `ai/clock.py::position_value` contains a provable sign inversion: with no
  clock of your own, `clock_diff = -opp_clock`, so a FASTER opponent scores
  BETTER. Repairing it (one saturating formula replacing four branches) was
  predicted to rescue creature-light control, whose removal was being priced
  as a downgrade. An A/B field sweep FALSIFIES that prediction: Azorius
  Control 21.0 -> 23.0, 4/5c Control 38.1 -> 38.1 (zero), while Jeskai Blink
  FELL 8.3pp out of band and both above-band aggro decks rose further above.
  Do not re-run this experiment. The bug remains real and worth fixing on
  correctness grounds; it is not the cause of Azorius Control's 21%.
---

# The clock sign inversion is real; fixing it does not help (2026-08-30)

## The defect (confirmed, not in dispute)

`ai/clock.py::position_value`, baseline:

```python
clock_diff = opp_clock - my_clock
if my_clock >= NO_CLOCK and opp_clock >= NO_CLOCK:
    clock_diff = 0.0
elif my_clock >= NO_CLOCK:
    # I have no clock, opponent does -> I'm losing; worse as opp gets faster
    clock_diff = -opp_clock
elif opp_clock >= NO_CLOCK:
    clock_diff = CLOCK_LETHAL_ADVANTAGE_CAP / my_clock
```

Two independent defects, both reproduced directly from the live formula:

**Sign inversion.** `clock_diff = -opp_clock` is the exact inverse of its own
comment. Lower `opp_clock` means a FASTER opponent:

```
opp_clock=  1 (about to kill me) -> clock_diff =  -1.00
opp_clock= 50 (fifty turns away) -> clock_diff = -50.00
```

Corollary, and the reason this looked promising: anything that LENGTHENS the
opponent's clock is priced as a downgrade. A creature-less deck casting
removal makes its own score worse. `tests/test_panic_gearshift_reaches_play_selection.py`
had that frozen as a fixture invariant ("the removal's raw projection EV must
be negative here") — the bug was load-bearing in a test.

**Sentinel cliff.** The finite branch is unbounded below while the sentinel
branch is bounded, so acquiring a real-but-slow clock scores far worse than
having none:

```
my_clock=99.0 (sentinel), opp_clock=10 -> clock_diff = -10.00
my_clock=60.0,            opp_clock=10 -> clock_diff = -50.00
```

The crossing point moves with `opp_toughness`, which is why an earlier
single-board measurement made it look board-dependent and produced an
over-general claim that had to be retracted the same day.

## The fix that was built

`clock_pressure(clock) = CLOCK_LETHAL_ADVANTAGE_CAP / min(clock, NO_CLOCK)`;
`clock_diff = clock_pressure(my) - clock_pressure(opp)`. All four branches
deleted, no new constants. Sign-preserving by construction
(`1/a > 1/b <=> b > a`), saturating rather than clipped, and it reuses the
constant the old code already applied to the winning side of the race.

It is on `worktree-agent-aa717e273f93e7f59` (`4cab2dc`). It is NOT merged.

## The prediction, and its falsification

**Predicted:** creature-light control is structurally punished for casting
removal, so repairing the inversion should lift Azorius Control and 4/5c
Control substantially. The baseline supported the prior — the two decks
furthest below band were the two most creature-light.

**Measured.** A/B field sweep, n=6 Bo3 over 24 opponents each (~144 matches
per cell), arms run SEQUENTIALLY on a quiet box so contention could not bias
one against the other. Both arms verified to differ (`-opp_clock` present in
baseline, absent in fix; `clock_pressure` present only in fix).

| deck | baseline | with fix | delta | band effect |
|---|---|---|---|---|
| Azorius Control | 21.0 | 23.0 | +2.0 | still far below |
| 4/5c Control | 38.1 | 38.1 | **0.0** | unchanged |
| Jeskai Blink | 48.6 | 40.3 | **-8.3** | IN band -> below |
| 4c Omnath | 66.0 | 68.8 | +2.8 | above -> further above |
| Domain Zoo | 79.2 | 78.4 | -0.8 | noise |
| Boros Energy | 66.0 | 69.4 | +3.4 | above -> further above |

Sum of deltas: **-0.9**. At this n a delta under ~4pp is inside noise, so
every cell except Jeskai Blink's is uninformative on its own.

**The prediction fails on its own terms.** The two decks it was supposed to
rescue moved +2.0 and exactly 0.0. The largest single move in the experiment
is a control deck getting 8.3pp WORSE and leaving its band. The two decks
that gained most were aggro decks already above band, so what movement there
is runs *against* calibration.

## Verdict

**Do not merge the fix as-is, and do not re-run this experiment.**

Keep two claims separate, because conflating them is what made this look
like a sure thing:

* The **bug is real**. The formula is inverted; that is provable from source
  and was verified independently of the agent that found it. It is worth
  fixing on correctness grounds alone, and a future fix should carry these
  measurements so nobody expects a win rate from it.
* The **causal hypothesis is falsified**. The inversion is not what costs
  creature-light control its win rate. Azorius Control at 21% needs its own
  root cause; this was not it.

Also note the fix moved 12 of 29 WR-anchor entries (41%), including 5 winner
flips, while delivering no measured benefit — a large behavioural change
buying nothing.

## What this does NOT rule out

* That the fix helps in combination with something else (e.g. whatever
  actually explains Azorius Control's 21%). It was measured alone.
* That a larger n would resolve the sub-noise cells. Only Jeskai Blink's
  -8.3 is above the noise floor here.
* That the *sentinel cliff* half, separated from the sign-inversion half,
  behaves differently. They were fixed together and measured together.

## Next lead for Azorius Control (21%)

Untested. The panic-gearshift fixture named above encodes the belief that
removal SHOULD project negative for this deck; with the inversion confirmed
as the source of that belief, the fixture's expectation is itself suspect
and is the obvious next thread — but the measurement above says pulling it
will not, by itself, move the win rate.


## Addendum (2026-09-04): the halves, measured separately

The sign half alone shipped (the losing branch as the mirror of the winning
one, `-CAP / opp_clock`), because it was the direct blocker for deploying a
zero-power mana creature (Creatures Toolbox engine work, PR #569). The
sentinel-cliff half was then built on top (one saturating form
`CAP/min(clock, NO_CLOCK)` per side) and A/B-measured at n=5 field, same
seeds, on a quiet box:

| deck | sign half only | sign + cliff |
|---|---|---|
| Creatures Toolbox | 17.5 | 11.7 |
| Jeskai Blink | 30.8 | 27.5 |

The cliff half repeats the combined fix's finding — a win-rate cost and no
benefit — so it is reverted; the sign half stays. Do not re-run the cliff
half as a standalone lever either.

## Addendum (2026-09-09): the narrow clamp, measured on a different target

Reopened against a different measurement target, as the 2026-09-08 plan
required: not "does it lift creature-light control" but "does it let an
aggro deck deploy into a single early blocker" (Izzet Prowess never
deployed a creature in WST v2 s50000 G2; a 1-power creature into a lone
2/2 flier scored -50.0 against -2.0 for no creature; the mirror scored the
opponent's 1/1 at +51 for me). The formula built was the NARROW rule, not
the reverted reformulation: both finite, `clock_diff` clamped to the two
sentinel values (`max(-CAP/opp_clock, min(CAP/my_clock, opp - my))`),
untouched inside the bounds. Measured jointly with the blend-rounding fix
(A6), n=20 Bo3, matchup grid s50000, against the post-combat-tricks tree:

| cell | before | with clamp + A6 | delta |
|---|---|---|---|
| Izzet Prowess vs Azorius Control (WST) | 45 | 30 | -15 |
| Izzet Prowess vs Azorius Control (WST v2) | 10 | 10 | 0 |
| Izzet Prowess vs Eldrazi Tron | 35 | 30 | -5 |
| Izzet Prowess vs Domain Zoo | 30 | 40 | +10 |
| Creatures Toolbox vs Boros Energy (guard) | 35 | 15 | **-20** |
| Jeskai Blink vs Boros Energy (guard) | 25 | 30 | +5 |

The target cells net -10 and the Creatures Toolbox guard pays 20pp — the
same deck, the same direction, the same size as the 09-04 measurement. The
clamp is reverted. Three formulations of "a real clock is never worse than
none" have now cost win rate without lifting any target (combined
saturating form, standalone saturating form, standalone clamp): whatever
the decks that deploy small creatures into blockers are losing to, it is
not this term's monotonicity, and the term's current shape is
load-bearing for Creatures Toolbox in a way no diagnosis has yet named.
Do not build a fourth form without first naming, from a Bo3 replay, the
decision the clamp changes in a Toolbox game and why it loses.
