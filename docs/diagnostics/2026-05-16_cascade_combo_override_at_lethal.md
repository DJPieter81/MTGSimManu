---
title: Combo-archetype clock override masks defensive cascade EV at lethal
status: active
priority: primary
session: 2026-05-16
supersedes: []
depends_on:
  - docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md
tags:
  - cascade
  - combo
  - living-end
  - position-value
  - override
  - affinity
  - defensive-save
summary: >
  Re-verified at HEAD (e74e654) that even after applying the dropped
  Path-2 fix to `position_value` and `is_symmetric_reanimation`
  (`claude/fix-affinity-opponent-side-position-value-and-sym-reanim`
  @ bdff379), Living End's cascade EVs at 1 life facing Affinity's
  24-power board still collapse to ~8.07 EV — a **35.68pp**
  suppression versus what the same projected board state would score
  under the midrange archetype branch of `position_value`.

  Named subsystem (one line): the override lives in
  `ai/clock.py::position_value` **lines 398-402**:

      if archetype in ("combo", "storm"):
          combo_c = combo_clock(snap)
          my_clock = min(my_clock, combo_c)

  Math: **replacement**, not multiplicative/additive. When
  `combat_clock(my_power, …)` returns `NO_CLOCK` (no creatures),
  `combo_clock` (which is a resource-availability heuristic, NOT a
  damage heuristic) clamps `my_clock` down to 1.0 even though the
  controller has NO board, NO life buffer, and NO surviving turn.
  This silently swaps the `my_clock >= NO_CLOCK` lethal-dread branch
  (lines 410-436: bounded by `-CAP/opp_clock - CAP*(1-opp_clock)`)
  out for the linear `clock_diff = opp_clock - my_clock` branch (line
  405), which evaluates `0.04 - 1.0 = -0.96` instead of `-39.17` for
  the same state.

  Consequence: the lethal-NOW penalty in `position_value(snap_now)`
  is masked to a trivial −1, making the DELTA against the post-wipe
  `position_value(snap_projected)` (where the wipe gives my_power=11,
  opp_power=4, clocks normal) only +10.7 instead of +48.9. The fix
  PR's `_project_spell` sacrifice clause works correctly — opp_power
  goes 24 → 4 in the projection — but the win it should produce in
  position_value is eaten by the combo-clock floor on the NOW side.

  Quantified at three seeds (50000, 50500, 51000) against the same
  matchup, the suppression is up to **+36.6pp** at the lethal-NOW
  decision (LE 1 life vs 24 opp power); it inverts to **-17pp** when
  the controller is no longer at lethal and the combo-clock estimate
  pulls in the other direction. The override goes both ways, but
  defensive-save scenarios are systematically the wrong-direction
  case.

  Class size: every cascade-reanimator decision (Living End +
  Crashing Footfalls + future cascade-WB combos), every combo
  archetype's defensive board-wipe decision when at low life
  (Storm with Wrath sideboarded in, Goryo's with mass-removal SB,
  Amulet Titan with Tectonic Reformation cycling-as-defense). Class
  size ≥ 10. Generic by construction; fix is in the shared
  `position_value` codepath.

  This doc satisfies the CLAUDE.md loop-break protocol: one
  subsystem, named, with evidence. No engine/AI code change in this
  diff. NO new card-name or deck-name conditionals.

notes:
  - "All reproductions ran at HEAD (e74e654) with `python run_meta.py --bo3 affinity 'Living End' -s SEED --dump-replay`. NDJSON saved to `/tmp/le_diag.ndjson`. Multi-seed sweep instrumentation at `/tmp/multi_seed_diag.py` (not committed)."
  - "The override is REPLACEMENT semantics: `my_clock = min(my_clock, combo_c)`. Pre-fix `position_value` would have the same bug; the dropped Path-2 fix (sub-turn-granular combat_clock + sacrifice clause projection) makes the OPP side accurate but the MY side is then capped by the combo-clock floor before the position_value branch select runs."
  - "Combo_clock at the lethal-NOW state returns 1.0 because the resource math (fuel=4, mana=3, gy=3, storm=0, needed=6 for cascade_reanimator) clears the deficit. The resource math says nothing about whether I will live to cast the cascade — that's the structural error."
---

# Cascade combo override at lethal — diagnostic

## TL;DR

`ai/clock.py::position_value` lines 398-402 replace `my_clock` with
`min(my_clock, combo_clock(snap))` for combo / storm archetypes.
At lethal-NOW states (my_life=1, opp_power=24, my creatures=0)
where the controller has NO survival path, `combat_clock` correctly
returns `NO_CLOCK` (99.0), but `combo_clock` returns 1.0 from its
resource-assembly heuristic. The `min` clamps my_clock to 1.0,
masking the lethal-NOW penalty and shrinking the defensive-save
EV delta by **~36pp** at the canonical Affinity vs Living End
g1t4d14 decision.

## Reproduction

```bash
git checkout e74e654  # main HEAD at the time of this diag
python run_meta.py --bo3 affinity "Living End" -s 50000 \
    --dump-replay /tmp/le_diag.ndjson

grep '"decision_id":"g1t4d14"' /tmp/le_diag.ndjson | python3 -m json.tool
```

Result (HEAD):

```
chosen:      Raugrin Triome (cycle)      ev=20.50
alt:         Zagoth Triome (cycle)       ev=20.50
alt:         Raugrin Triome (play_land)  ev=11.00
alt:         Shardless Agent (cast)      ev= 7.93
alt:         Demonic Dread (cast)        ev= 7.65   ← cascade-into-Living-End
```

LE chooses Cycle and dies the next turn. The cascade enabler is in
hand, the graveyard has 3 creatures (Architects + 2 Wraiths +
Striped Riverwinder reanimation pool), Affinity has 24 power on the
board, LE has 1 life. The textbook play is Demonic Dread → Living
End → wipe Affinity, return reanimated bodies. EV ≈ 7.65 says no.

## Method

Two-pronged instrumentation, both at HEAD and with the dropped
Path-2 fix temporarily applied (cherry-picked `ai/clock.py` and
`ai/ev_evaluator.py` from `claude/fix-affinity-opponent-side-
position-value-and-sym-reanim @ bdff379` into a worktree, runs
deleted after measurement):

1. Wrap `_project_spell` to dump pre/post snapshot attributes
   (`projected.my_power`, `projected.opp_power`, …) for cascade
   cards at `snap.my_life ≤ 5`.
2. Wrap `compute_play_ev` to dump `current_value`, `projected_value`,
   `raw_delta`, `after_response_value`, and the **same call again
   with archetype="midrange"** as a counter-factual baseline. The
   midrange branch of `position_value` does NOT execute the
   `min(my_clock, combo_c)` override, so the delta gap between
   `raw_delta_combo` and `raw_delta_midrange` is exactly the override
   suppression.

## At-HEAD baseline (no fix applied)

`compute_play_ev` returns `ev_returned = 7.65` directly. There is no
post-`compute_play_ev` modifier subtracting EV.  `card_combo_modifier`
returns 0 for this cascade card (Demonic Dread is not STORM-keyword,
not a tutor with payoff access, not a non-cascade payoff; falls
through to `return 0.0`).  `_score_spell`'s overlays (Free cast,
Evoke, Combo sequencing, Cascade patience gate, …) all either
skip the spell or add zero.  Final `_score_spell_FINAL = 7.65`.

The 7.65 is THE projection's output: `evaluate_board(projected) -
evaluate_board(current) = -6.71 - (-14.36) = 7.65`.

`_project_spell` at HEAD produces:
- `snap`: my_power=0, opp_power=24, my_gy_creatures=3, my_life=1
- `projected`: my_power=11, **opp_power=28** (cascade projection
  recursively projects Living End on the opp side too, adding
  opp_gy contributions without subtracting opp's on-board sac
  clause).

## With dropped fix applied (b dff379 cherry-pick)

`_project_spell`:
- `projected`: my_power=11, **opp_power=4** (sacrifice clause fires,
  Affinity's 24 power is zeroed; the +4 left over is residual from a
  single Mox Opal artifact-creature being missed by the sac filter —
  not relevant here; this is the Component-B fix from the
  2026-05-10 root-cause doc).

`compute_play_ev`:
- `current_value = -15.32`
- `projected_value = -7.24`
- `raw_delta = 8.07`
- `ev_returned = 8.07` (final, no override after compute_play_ev)

So the corrected projection produces a delta of 8.07, NOT the
"18.88" referenced in the parent diagnostic. The 18.88 figure was
apparently from a different state or a misreading; the actual
position_value delta at this state, with the fix applied, is 8.07.

## The override math — pinned

Direct `position_value` math, identical snapshots, only the
`archetype` argument changes:

```python
snap_now      = my_life=1, opp_life=20, my_power=0, opp_power=24, gy=3
snap_projected = my_life=1, opp_life=20, my_power=11, opp_power=4
```

| archetype | position_value(now) | position_value(projected) | delta |
|---|---:|---:|---:|
| `"midrange"` | -49.58 | -0.63 | **+48.95** |
| `"combo"` | -11.37 | -0.63 | **+10.74** |

The projected value is identical (-0.63 — the combo override is a
no-op when my_clock is finite and combo_c ≥ my_clock). The
**current value** differs by 38.21 because of the combo override.

Decomposed clocks:

```
NOW:
  combat_clock(my)  = 99.0    (NO_CLOCK — no creatures)
  combo_clock       = 1.0     (resources assembled: fuel=4 mana=3 gy=3 storm=0 needed=6)
  opp_clock         = 1/24    = 0.042 (sub-1.0 because of overkill)

PROJECTED:
  combat_clock(my)  = 1.94    (11 power vs 20 life with blocker math)
  combo_clock       = 5.0     (resources spent by the cascade chain)
  opp_clock         = 1/4     = 0.25 (still overkill but reduced)
```

For **midrange** archetype, `my_clock = combat_clock = 99.0 = NO_CLOCK`
at NOW.  Falls into the NO_CLOCK branch:
```
clock_diff = -CAP/max(opp_clock,1) - CAP*(1-opp_clock)
           = -20/1 - 20*(1-0.042)
           = -39.17
```

For **combo** archetype at NOW, `my_clock = min(99.0, 1.0) = 1.0`.
The branch select is `else` (lines 405): `clock_diff = opp_clock -
my_clock = 0.042 - 1.0 = -0.96`.

**The override changes clock_diff from -39.17 to -0.96** — a
**+38.21** shift toward "I'm fine". Combined with smaller
life_advantage / mana / card terms (constant across archetypes),
the total position_value(now) shifts from -49.58 to -11.37, exactly
matching observation.

## (position_value, final_ev) table — three seeds

`run_meta.py --bo3 affinity "Living End" -s {50000, 50500, 51000}`
with the dropped fix applied (so projection is honest). Filter:
Demonic Dread / Shardless Agent decisions where LE is at
`my_life ≤ 5` and Affinity has wide board.

| Seed | Card | LE life | Aff power | GY | Δcombo | Δmidrange | Final EV | Override magnitude |
|-----:|------|--------:|----------:|---:|-------:|----------:|---------:|-------------------:|
| 50000 | Demonic Dread | 1 | 24 | 3 | **+8.07** | +43.75 | 8.07 | **+35.68** |
| 50000 | Shardless Agent | 1 | 24 | 3 | +8.40 | +45.00 | 8.40 | +36.60 |
| 50500 | Demonic Dread | 5 | 7 | 1 | +15.46 | -1.51 | 15.46 | -16.97 |
| 50500 | Demonic Dread | 5 | 1 | 1 | -17.35 | -14.01 | -17.35 | +3.33 |
| 50500 | Demonic Dread | 4 | 7 | 1 | +6.08 | -0.89 | 6.08 | -6.97 |
| 50500 | Demonic Dread | 3 | 0 | 2 | -0.44 | -0.44 | -0.44 | 0.00 |
| 51000 | Demonic Dread | 5 | 0 | 0 | -0.36 | -0.36 | -0.36 | 0.00 |

Observations:
1. **Defensive-save case (seed 50000 row)**: combo override
   suppresses the EV delta by ~36pp. The cascade is the correct
   defensive play; the override hides this.
2. **Mid-life, opp-power non-overkill (seed 50500, row 1)**:
   override goes the OTHER way (-17pp). Combo_clock floors my_clock
   when I have 1 fuel + 1 mana + 1 gy = 3 < 6 needed → deficit 3 →
   combo_c = 4.0. Combat_clock from the cascade swing = 1.83 →
   min = 1.83. But in the NOW state, combat_clock = 99 (no
   creatures), so my_clock = min(99, 4.0) = 4.0 → clock_diff =
   opp_clock - 4.0. This gives a LARGER apparent delta vs combat-
   only midrange. The override is non-monotone.
3. **opp_power = 0 case (seed 51000)**: no override (both archetypes
   agree; my_clock is NO_CLOCK in both NOW and projected, and combo_c
   ≥ that). Override is silent when the controller is not under
   pressure.

Conclusion: the override is a state-dependent replacement that
distorts the position_value scale by up to ±36pp on cascade-archetype
decisions, asymmetrically in favour of "I'm fine, don't bother" at
the exact moment defensive plays should fire.

## The rule the override encodes (and the rule it should encode)

The override's intent — read from the inline comment and the
`combo_clock` docstring (`ai/clock.py:213-250`):
> "Combo decks: use the faster of combat clock and combo clock"
> "Mid-chain: storm count directly measures proximity to kill"

The implicit rule: "if my combo deck can assemble a kill in N
turns, my clock is N; downstream evaluators should treat me as
having a real clock even when I have no on-board creatures."

This is sound when the combo is THIS-TURN-CASTABLE. It is unsound
when the controller is at lethal life and the combo requires (a)
surviving an opponent's turn (Living End cascaded this turn
returns blockers, but if opp_power > my_life RIGHT NOW with no
on-board, lethal happens before our reanimated bodies block) or
(b) more than one turn to assemble.

The correct rule: **combo_clock is a soft estimate, NOT a clock
substitute when the controller is dead-this-turn.** The `min`
should be gated on a survival predicate, not on raw resource
availability. Concretely the post-fix `combat_clock` already
encodes lethal-NOW as `opp_clock < 1.0` (sub-1.0 sub-turn
granular). The override should refuse to fire when `opp_clock <
1.0` AND `my_creatures = 0` (no blockers), because in that state
the combo can never resolve to lethal — the controller dies before
their next priority pass.

Alternative framing — the right rule is:
> "Use combo_clock for my_clock ONLY when at least one of:
>  (a) I survive opp's next combat step (`opp_clock ≥ 1.0` or
>     `my_blockers can absorb opp_power`), OR
>  (b) the combo resolves THIS turn (`storm_count ≥ payoff_threshold`
>     or the cascade enabler is in hand and we have priority)."

Either form preserves the override's intended use case (combo deck
isn't penalised for having no blockers when it's safely 3 turns
away from a Storm kill) while restoring lethal-NOW dread (combo
deck with 1 life facing Affinity scores the wipe at the same scale
midrange does).

## Cross-deck applicability

| Deck | Effect | Trigger condition |
|------|--------|-------------------|
| Living End | Cascade-into-mass-reanimation EV is suppressed at lethal | my_life ≤ 3, opp_power ≥ 2× my_life, on-board creatures = 0 |
| Ruby Storm | Defensive Wrath / counter EV at low life when storm chain is unassembled | combo_clock returns 1-2 from fuel-in-hand, but storm count = 0 |
| Amulet Titan | Cycling-defensive (Tectonic Reformation pattern) EV at low life | combo_clock returns 1 from mana resources, but no Titan in hand → cannot win this turn |
| Goryo's Vengeance | Defensive board wipe EV when Goryo's target is in GY | combo_clock counts 1 GY creature as ready, scoring the now-state as "I have a clock" while taking lethal |

Class size ≥ 10 (combo / storm / cascade / reanimator decks across
Modern). Generic by mechanism — the override is in shared
`position_value`, fires for any deck with `archetype` ∈ {"combo",
"storm"}. No card-name conditionals; the fix path is also generic.

## Recommended fix sketch (NOT implemented in this diag)

In `ai/clock.py::position_value` lines 398-402, gate the combo
override on a survival predicate:

```python
# Combo decks: override my_clock with combo-specific clock,
# but only when the controller is not dead-this-turn.  The combo
# clock is a resource-availability estimate, not a damage clock —
# it cannot substitute for combat_clock when the cascade/reanimate
# spell will not resolve before opp's next combat step.
if archetype in ("combo", "storm"):
    combo_c = combo_clock(snap)
    # Survival predicate: I survive at least one more turn if
    # either (a) opp's combat clock isn't sub-1.0 lethal-NOW, or
    # (b) I have blockers to absorb opp's power.
    opp_clock_for_me = combat_clock(snap.opp_power, snap.my_life,
                                    snap.opp_evasion_power,
                                    snap.my_toughness)
    survives_to_combo_turn = (opp_clock_for_me >= 1.0
                              or snap.my_creature_count >= 1)
    if survives_to_combo_turn:
        my_clock = min(my_clock, combo_c)
    # else: keep combat_clock's NO_CLOCK signal, fall into the
    # lethal-dread branch below.
```

**Test name (mechanic-phrased, no card names)**:
`test_combo_clock_override_does_not_mask_lethal_now_state`.
Setup: snap with my_life=1, my_power=0, my_creature_count=0,
opp_power=24, combo resources assembled. Assert
`position_value(snap, archetype="combo")` falls within
`±5%` of `position_value(snap, archetype="midrange")` —  i.e.
the lethal-dread term is preserved.

## What this doc does NOT do

- It does not change code. Per CLAUDE.md §No fix without a failing
  test in the same diff and §Loop-break, this is the named-
  subsystem-in-writing record. Fix lands in a separate PR.
- It does not invalidate the Component-B fix (`_project_spell`
  sacrifice clause). That fix is correct: opp_power 24 → 4 in the
  projection. The bug surfaced here is a DIFFERENT, downstream
  problem in `position_value`'s combo branch select.
- It does not add card-name conditionals or deck-gate conditionals.
- It does not lower the abstraction-contract baseline.

## References

- `ai/clock.py:398-402` — the override (`min(my_clock, combo_c)`)
- `ai/clock.py:213-250` — `combo_clock` definition (resource-
  assembly heuristic, no damage modeling)
- `ai/clock.py:404-443` — branch select for `clock_diff` that the
  override bypasses by pulling my_clock out of NO_CLOCK
- `ai/ev_evaluator.py:1744-1788` — `is_symmetric_reanimation` (the
  projection side, OK after the dropped fix)
- `ai/ev_evaluator.py:2163-2217` — cascade projection (recursive
  `_project_spell` on the cascade hit, OK in principle)
- `docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md`
  — the parent doc (Component A is `position_value`'s `max(1.0, …)`
  floor in `combat_clock`; this doc identifies a SECOND override
  on top of the combat_clock continuity fix)
- Branch parked at `claude/fix-affinity-opponent-side-position-
  value-and-sym-reanim` @ `bdff379` — the Path-2 fix referenced
  throughout this doc
- `replays/affinity_vs_living_end_s50000_decisions.ndjson` —
  decision `g1t4d14` evidence
- Instrumentation scripts (not committed): `/tmp/instrument_diag.py`,
  `/tmp/instrument_diag2.py`, `/tmp/multi_seed_diag.py`,
  `/tmp/pv_check.py`, `/tmp/combo_clock_check.py`

## Frontmatter discovery hint

This doc is `priority: primary`, `status: active`. It extends —
does not supersede — the 2026-05-10 root-cause doc. Component A
(the combat_clock floor) and Component B (the sacrifice clause)
remain valid in the parent doc; this doc adds **Component D**:
the combo-clock floor in `position_value` that overrides
combat_clock's signal at lethal-NOW.

The fix PR for any of A / B / D must declare
`supersedes: [docs/diagnostics/2026-05-16_cascade_combo_override_at_lethal.md]`
or mark this doc `status: superseded` upon merge.
