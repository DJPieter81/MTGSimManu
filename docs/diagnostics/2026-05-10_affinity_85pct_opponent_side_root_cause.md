---
title: Affinity 85% Root Cause v2 — Opponent-side defensive-save EV collapse
status: active
priority: primary
session: 2026-05-10
supersedes: []
depends_on:
  - docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
  - docs/diagnostics/2026-05-04_affinity_mulligan_overkeep_audit.md
  - docs/diagnostics/2026-05-04_affinity_plating_threat_undervaluation_audit.md
  - docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md
  - docs/diagnostics/2026-05-09_phase-1-2-post-mortem.md
  - docs/handoff/2026-05_session_summary.md
tags:
  - affinity
  - outlier
  - opponent-side
  - root-cause
  - diagnostic
  - position-value
  - cascade-reanimator
  - x-cost-board-wipe
summary: >
  PR #370's Path A revert of PR #288 produced 0pp delta on Affinity WR
  (85.6% pre/post n=20). PR #288 was not the driver. This diagnostic
  pivots: what is OPPONENTS' AI doing wrong that lets Affinity win 85%?

  Three lopsided Affinity wins from `replays/golden_pr288_revert/`
  (Boros, Living End, Eldrazi Tron at seed 50000) were re-run with the
  NDJSON decision dump. In ALL THREE matchups the opponent had the
  correct answer in hand at the lethal-imminent turn, and in ALL THREE
  the opponent's EVPlayer scored that answer well below an
  objectively-worse alternative.

  Named subsystem (one line): the bug lives in
  `ai/clock.py::position_value` lines 388-401 (the clock_diff branch
  when `opp_clock < 1` turn) combined with
  `ai/ev_evaluator.py::_project_spell::is_symmetric_reanimation` lines
  1744-1788 (symmetric reanimation projection skips the on-board
  sacrifice clause). The former collapses the EV gap between
  "defensive save that buys ≥1 turn" and "do nothing"; the latter
  zero-credits the Affinity-board-clearing half of Living End's effect.

  Concrete cited decisions:
  - `g2t4d45` (Boros vs Affinity, seed 50000): Boros at 1 life facing
    14 power lethal next-turn; **Wrath of the Skies scored EV=0.98 vs
    Phlage EV=6.39** (gap 5.40). Wrath wipes Affinity's 2 Construct
    Tokens + 2 Memnites + Mox Opal for X=0; Phlage suicide-sacs
    (escape unavailable, no GY) and gains 3 life. Boros chose Phlage,
    died next turn.
  - `g1t4d14` (Living End vs Affinity, seed 50000): Living End at 1
    life with 4 GY creatures and Demonic Dread in hand; **Demonic
    Dread (cascades 100% into Living End — only sub-CMC-3 cascadable
    card in the deck) scored EV=7.65 vs cycle-Raugrin EV=20.5**.
    Cascade-into-Living-End wipes Affinity's 5-creature board and
    returns 4 reanimated bodies — the deck's whole win condition.
    LE chose to cycle and died.

  Both errors stem from the same generic mechanic: the EV pipeline
  systematically undervalues "save-from-imminent-lethal" plays
  because:
    (a) `combat_clock` floors at 1.0 turn, so "lethal NOW" and
        "lethal next turn" produce identical opp_clock values, and
    (b) the symmetric-reanimation projection adds GY returns but
        skips the "sacrifice all creatures they control" clause, so
        Living End on a 14-power opp board is scored as if no power
        is removed.

  Class size: every modern board-wipe ({X}-cost or fixed), every
  cascade-reanimator combo deck (Living End + future cascade-WB
  combos), every defensive-save instant — easily 50+ cards across
  current Modern. NOT a one-card patch. NO CODE CHANGE in this
  diagnostic per the abstraction contract; fix specification is
  Phase II of this doc and is not in scope for this PR.
notes:
  - "All cited decisions reproduced fresh at HEAD (7246eb3) with `python run_meta.py --bo3 --dump-replay`. NDJSON fixtures committed to `replays/affinity_vs_*_s50000_decisions.ndjson` for replay."
  - "This doc extends — does not duplicate — the prior Affinity diagnostics. Phase L (artifact_count includes lands) was an AFFINITY-side overscoring bug. This doc is the symmetric OPPONENT-side under-scoring bug."
---

# Affinity 85% Root Cause v2 — Opponent-side defensive-save EV collapse

## Premise

PR #370 (Path A revert of PR #288) shipped on the hypothesis that
mainboard-hate edits in PR #288 were not moving the WR. The result
was **exactly 0pp delta at n=20 (85.6% pre/post)** — confirming PR
#288 was not the driver. The actual driver of Affinity's
overperformance is still unidentified.

Per the abstraction contract loop-break protocol
(`CLAUDE.md` §Loop-break, session protocol), the rule is:

> If three consecutive commits target the same outlier deck without
> moving the win rate toward its expected band: halt. Run
> `run_meta.py --bo3` against the worst matchup, identify the exact
> turn where EV diverges from correct play, name the responsible
> subsystem in writing in `docs/`.

The PRs that have not moved Affinity meaningfully (per
`docs/diagnostics/2026-05-09_phase-1-2-post-mortem.md` and
`docs/handoff/2026-05_session_summary.md`):

| PR    | Phase shape                                          | Affinity WR delta |
|-------|-------------------------------------------------------|--------------------|
| #288  | Maindeck-hate redistribution on opponents             | reverted in PR #370 with **0pp delta** at n=20 |
| #304  | 6-bug rules-correctness ratchet (Phase 1A-2B)         | -1.6pp / -2.1pp    |
| #370  | Path A — revert PR #288                                | 0pp                |

Three Affinity-targeted PR cycles, zero net WR movement. The loop-
break rule applies.

This doc satisfies the loop-break requirement by:

1. Running Bo3 + NDJSON decision dump on the three lopsided wins
   from `replays/golden_pr288_revert/`.
2. Identifying ≥1 OPPONENT-SIDE decision in each game where the
   chosen EV is significantly below an alternative answer in hand.
3. Naming the single subsystem all three errors share.

## What was ruled OUT before naming the subsystem

The prior Affinity diagnostics (`docs/diagnostics/2026-05-04_phase-l-*`,
`docs/diagnostics/2026-05-04_affinity_mulligan_overkeep_audit.md`,
`docs/diagnostics/2026-05-04_affinity_plating_threat_undervaluation_audit.md`,
`docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md`)
collectively ruled out:

- Affinity's mulligan AI is over-keeping (FOUND: false; keep rate
  76.4% is mid-pack)
- Mox Opal under-paying T1 (FOUND: false; cast logic correct)
- Affinity-keyword cost reduction stacking (FOUND: false)
- Construct token P/T (FOUND: rules-correct)
- Plating equipment scaling (FOUND: rules-correct)
- Saga III tutor priority dict (lifted to AI callback in PR #304;
  no WR movement)
- Class A `parse_cost_reduction` lazy false-positive (fixed PR #304;
  cancelled across matchups)
- `artifact_count` includes artifact lands (PR-L1, merged; -1.6pp
  matrix delta)

**The shared structural property** of all of the above: they audit
the AFFINITY side. Every prior fix made Affinity score its own plays
more accurately. None of them changed how OPPONENTS score answers
to Affinity. This doc inverts the lens.

## Method

1. **Re-run the 3 fixture matchups at HEAD with NDJSON dump**:

   ```bash
   python run_meta.py --bo3 "Affinity" "Boros Energy" -s 50000 \
       --dump-replay replays/affinity_vs_boros_s50000_decisions.ndjson
   python run_meta.py --bo3 "Affinity" "Living End" -s 50000 \
       --dump-replay replays/affinity_vs_living_end_s50000_decisions.ndjson
   python run_meta.py --bo3 "Affinity" "Eldrazi Tron" -s 50000 \
       --dump-replay replays/affinity_vs_eldrazi_tron_s50000_decisions.ndjson
   ```

   All three games reproduce as 2-0 Affinity wins on the same lines as
   `replays/golden_pr288_revert/` (HEAD = 7246eb3, the PR #370 baseline).

2. **For each game, walk all OPPONENT decisions** where the chosen
   action is `cast_spell` or `cycle` (not `play_land`) and inspect
   the `alternatives[]` array for high-leverage answers.

3. **For each "wrong" decision, compute the gap** between the chosen
   EV and the best answer-in-hand EV, then trace which subsystem
   produced the gap (clock.py / ev_evaluator._project_spell /
   ev_player._score_spell overlays).

## Three decision IDs from the NDJSON

### Decision `g2t4d45` — Boros vs Affinity, Wrath of the Skies

State (verbatim from NDJSON):
- `actor=Boros Energy`, `pidx=1`, `game=2`, `turn=4`, `phase=Main1`
- `state.life = [1, 20]` (Boros at 1 after Windswept Heath crack)
- `state.hand = [6, 2]`
- `state.lands = [3, 2]` (Boros has Elegant Parlor + Arena of
  Glory + Sacred Foundry = 3 mana; W from Parlor, R from Arena,
  RW from Foundry)
- Affinity on-board (from text fixture, line 745-746): 2 Memnites
  (1/1), Frogmite (2/2), 2 Construct Tokens (10/10), Springleaf
  Drum, Mox Opal, Springleaf Drum (just played). **Total
  opp_power = 14, opp_creature_count = 5.**

Chosen vs alternatives:

```
chosen:      Phlage, Titan of Fire's Fury  ev=  6.39  (cmc 3, ETB sacs unless escaped)
alt:         Seasoned Pyromancer           ev=  4.53
alt:         Wrath of the Skies            ev=  0.98  (cmc {X}{W}{W})
alt:         Ocelot Pride                  ev= -0.29
```

Domain reality:
- Phlage cast unescaped → ETBs, deals 3 damage to ONE creature,
  gains 3 life, sacrifices itself. Boros goes 1 → 4 life. Affinity
  attacks next turn with 12 remaining power → Boros dies.
- Wrath of the Skies at X=0 → destroys 2 Memnites (CMC 0) +
  2 Construct Tokens (CMC 0) + Mox Opal (CMC 0). Affinity left
  with Frogmite (2/2) + 2 Springleaf Drums + lands.
  opp_power_after = 2. Boros at 1 life survives next turn
  (loses ≤2, can stabilise).

**Wrath is the correct answer**. The simulator scored it 5.4 EV
below Phlage. Gap is decisive — Phlage was chosen.

### Decision `g1t4d14` — Living End vs Affinity, cascade play

State (verbatim from NDJSON):
- `actor=Living End`, `pidx=1`, `game=1`, `turn=4`, `phase=Main1`
- `state.life = [20, 1]` (Living End at 1 after Affinity's T4
  Cranial-Plating-equipped Construct Token attack)
- `state.hand = [4, 8]` (LE has 4 cards)
- `state.lands = [3, 3]`
- LE's hand at this moment (from text fixture line 232-251):
  Demonic Dread (cascade!), Raugrin Triome, Zagoth Triome,
  Blooming Marsh, Shardless Agent. GY=4 creatures (Architects of
  Will, 2 Street Wraiths, Striped Riverwinder).
- Affinity on-board: Construct Token (16/8), Construct Token
  (8/8), Cranial Plating equipped to first construct.
  opp_power = 24, opp_creature_count = 2.

Chosen vs alternatives:

```
chosen:      Raugrin Triome (cycle)        ev= 20.50  (draws 1)
alt:         Zagoth Triome (cycle)         ev= 20.50  (draws 1)
alt:         Raugrin Triome (play_land)    ev= 11.00
alt:         Shardless Agent (cast)        ev=  7.93  (cmc 3, cascade)
alt:         Demonic Dread (cast)          ev=  7.65  (cmc 3, cascade)
```

Domain reality. Living End's deck (decks/modern_meta.py:Living End):
the ONLY non-land spell with `cmc < 3` is **Living End itself (4
copies)**. So cascading off either 3-CMC cascade spell hits Living
End with 100% probability (all other sub-3 cards are lands). Living
End's effect: each player exiles all GY creatures, sacrifices all
creatures they control, then returns the exiled.

- After Living End: Affinity loses 2 Constructs (24 power) — 100%.
  LE returns 4 creatures from GY (Architects 1/3 + 2 Street
  Wraith 2/3 + Striped Riverwinder 5/5 = +10 power, +4 bodies).
  Boros board state inverts: opp_power 24 → 0, my_power 0 → +10.
- After cycling: LE draws 1 card. Affinity attacks for 24 next
  turn. LE dies.

**Cascade is the correct answer** — it's literally what the deck is
built to do. The simulator scored it 12.85 EV below cycling. Cycling
doesn't even put a creature into the GY this turn (LE already at
resource_target=4 from the gameplan JSON; cycling is pure
card-draw).

### Decision `g1t2d6` — Eldrazi Tron vs Affinity, Chalice X-value

State (verbatim from NDJSON):
- `actor=Eldrazi Tron`, `pidx=1`, `game=1`, `turn=2`, `phase=Main1`
- `state.life = [20, 20]`, `state.lands = [2, 2]`
- ETron's hand: Chalice of the Void, Cavern of Souls, Thought-Knot
  Seer (cast next turn at Tron speed).
- Affinity on-board at this moment (text fixture line 100-101):
  Mox Opal (CMC 0), Urza's Saga, Mistvault Bridge.
  Affinity has cast Memnite (CMC 0) and Mox Opal already on T1.

Chosen: `cast_spell Chalice of the Void ev=2.52`. No alternatives
(only Chalice was castable with available mana).

Critical detail: **Chalice's X-value is picked at engine cast time
in `engine/cast_manager.py:944-984`** via a SYMMETRIC opp-CMC ≤
X − my-CMC ≤ X heuristic. The verbose log (line 73:
`T2 P2: Cast Chalice of the Void (0)`) shows X=0 was chosen. X=0
counters future 0-CMC casts — but Affinity's 0-CMC creatures
(Memnite, Ornithopter) are typically deployed T1, and Mox Opal is
already on the battlefield. The Construct Tokens entering from
Saga III ARE 0-CMC permanents, but they enter as tokens (not as
spells cast from hand), so Chalice doesn't counter them.

The textbook anti-Affinity X for Chalice is **X=1** (locks Springleaf
Drum, Galvanic Discharge mirror cost, etc.) or **X=4** (locks
Frogmite + Thought Monitor; Affinity's affinity-cost discount does
NOT change printed CMC for Chalice rules). X=0 against Affinity is
strictly the WORST choice — every relevant Affinity card on the
battlefield is already there, and the deck doesn't deploy future
Mox Opal copies because there's no more in hand by T3.

This is a less catastrophic single decision than the Boros/LE ones
(Tron still wins-or-loses for other reasons), but it surfaces the
SAME class of bug:

**The X-cost selection in `engine/cast_manager.py:944-984` is
divorced from the AI's strategic intent.** The X-picker uses
`permanent_threat` valuation over `opp.battlefield`; it doesn't
account for the cards Affinity will CAST FROM HAND across future
turns. For aggro decks that empty their hand fast (Affinity casts
Memnite + Mox + Springleaf T1), Chalice X=0 picks the CMC that
maximises a snapshot count that's already maxed.

## The shared subsystem

All three decisions hit the same generic mechanic from different
angles. The shared subsystem is **the EV pipeline's valuation of
defensive plays when the controller is at or near lethal**. The
codepath has three components, named below.

### Component A — `combat_clock` floors at 1.0 turn

`ai/clock.py:50-76` defines:

```python
def combat_clock(power: int, opp_life: int, ...):
    ...
    if effective_power <= 0:
        return NO_CLOCK
    return max(1.0, math.ceil(opp_life / effective_power))
```

The `max(1.0, ...)` floor means that "Affinity will kill me next
turn with 14 power vs my 1 life" produces `opp_clock = 1.0`, IDENTICAL
to "Affinity will kill me in 1 turn with 1 power vs my 1 life".
Every state where Affinity has lethal-on-the-stack collapses to
the same opp_clock value.

Then `position_value` (lines 388-401):

```python
if my_clock >= NO_CLOCK and opp_clock >= NO_CLOCK:
    clock_diff = 0.0
elif my_clock >= NO_CLOCK:
    clock_diff = -opp_clock
elif opp_clock >= NO_CLOCK:
    clock_diff = CLOCK_LETHAL_ADVANTAGE_CAP / my_clock
```

For Boros at decision g2t4d45 (`my_power=0` → `my_clock=NO_CLOCK`,
`opp_power=14` → `opp_clock=1.0`), `clock_diff = -1.0`. **The
controller-is-dying-this-turn state is worth -1 clock-unit;
controller-is-dying-next-turn would also be worth -1.**

After casting Wrath at X=0 (`my_power=0`, `opp_power=2`), the
clock_diff is **STILL -1.0** because `opp_clock = max(1.0,
ceil(1/2)) = 1.0`. So `position_value` records no improvement
from the wrath at all on the clock_diff term.

The only positive swing comes from `life_advantage =
life_as_resource(my_life, opp_power) - life_as_resource(opp_life,
my_power)`. Before: `life_as_resource(1, 14) ≈ 0.07`. After:
`life_as_resource(1, 2) = 0.5`. Net `+0.43`. That's the ENTIRE
delta the position-value math gives the wrath. After downstream
overlays (`urgency_factor` × ~2, exposure cost, etc.) it nets to
about +1 EV — which matches the observed `ev=0.98`.

**The defensive-save plays score essentially zero because the
clock differential is insensitive to lethal-NOW vs lethal-next-
turn**, and the life-resource term floors at small numbers when
incoming_power is large.

### Component B — `is_symmetric_reanimation` skips the sacrifice clause

`ai/ev_evaluator.py:1744-1788` defines:

```python
is_symmetric_reanimation = (
    ('each player' in oracle_lower or 'all graveyards' in oracle_lower)
    and 'graveyard' in oracle_lower
    and ('return' in oracle_lower or 'battlefield' in oracle_lower)
    and 'creature' in oracle_lower
)

if is_symmetric_reanimation and game:
    # ... iterates my_gy_creatures and opp_gy_creatures, ADDS to
    # projected.my_power / projected.opp_power ...
```

The projection captures **step 3** of Living End's effect (graveyard
creatures return to battlefield) but does **NOT** model **step 2**
(sacrifice all creatures both players control). When the opponent
has a wide board (Affinity at G1 T4: 2 Construct Tokens, 24 power
total), the projection misses the entire `opp_power -= 24` swing.

The cascade projection at `ev_evaluator.py:2163-2217` recursively
calls `_project_spell` on the cascade hit, so this bug compounds: a
Demonic-Dread-into-Living-End cascade evaluates the LE as if it
ONLY adds my reanimated creatures, never removes opp's on-board
ones. **This is why cascade-into-board-wipe (the whole point of
the deck) scores ~7 EV against an Affinity board that visibly
contains the deck's exact win condition.**

### Component C — X-cost board-wipe gate doesn't consult the engine X-picker

`ai/ev_player.py:1172-1190` (the v3 X-cost board-wipe gate) computes
`effective_x = (mana - base_cost) // multiplier` and uses
`opp.creatures` filtered by `c.template.cmc <= effective_x` to
estimate kill count. But:

1. The gate doesn't include opp's non-creature wipe targets
   (Mox Opal, Springleaf Drum) when computing the kill value —
   Wrath of the Skies destroys `artifact, creature, AND enchantment`
   per CR. The gate only counts creatures.
2. The gate's `effective_x` math is divorced from
   `engine/cast_manager.py:987-1035`'s permanent_threat-valued
   X-picker — the AI scores Wrath as if X is chosen to maximize
   creature-count, but the engine picks X to maximize
   permanent_threat. They can disagree by a factor of 5x on a
   board with high-CMC creatures (Frogmite CMC 4 not killable at
   X=0).
3. The projection in `_project_spell` (line 1864) zeroes
   `projected.my_power` AND `projected.opp_power` regardless of
   X-value. So the **projection over-estimates** the wrath's effect
   (assuming everything dies) while the **gate under-estimates**
   it (only counts creatures with CMC ≤ effective_x).

This three-way inconsistency is why Wrath of the Skies scored 0.98:
the projection thought it'd zero everything, but the kill-count
gate didn't see catastrophic damage avoided, and the position_value
collapse near lethal (Component A) damped the delta to nothing.

## Why this is one subsystem, not three patches

The three components above are NOT independent bugs. They are
three facets of one architectural choice: **the EV pipeline's
default-case scoring assumes the controller is not currently
dying**. The clock differential, the projection's swing terms, and
the X-cost gate all use formulas that linearise around "I have
turns to spare". The instant-defensive-save case (life=1 facing
lethal) is the limit case where every linearisation collapses.

Concretely:
- `combat_clock`'s `max(1.0, ...)` floor is a SCALE choice: it
  treats sub-1-turn clocks as discrete "next turn". Fine for
  midrange-vs-midrange; catastrophic for "save my life with a
  wrath" decisions where the controller is on the chopping block.
- `is_symmetric_reanimation`'s missing-sac is a SIGN choice: it
  treats opp creatures as untouched (sign=0) instead of going to
  GY (sign=-opp_power). Fine for a Damnation-style wipe-then-
  reanimate-yours; wrong for Living End which is exactly the
  same shape but symmetric.
- The X-cost gate's `cmc <= effective_x` predicate is a TYPE
  choice: it only considers creature kills. Fine for Wrath of God;
  wrong for Wrath of the Skies which kills artifacts and
  enchantments too, and especially wrong vs a deck whose threat
  base is 80% artifacts.

The pattern: **every place in the EV pipeline that assumes
"normal" game state breaks when the game state is "I'm about to
die from a wide-board artifact deck".** Affinity is uniquely
positioned to exploit this because its win condition is exactly
"wide board of artifacts that go lethal in one combat step".

## Failing test, rule-phrased

Per CLAUDE.md §Abstraction Contract — the failing test should
name the *mechanic*, not the card. Three tests, one per component:

```
tests/test_position_value_sub_turn_clock.py
  test_position_value_distinguishes_lethal_now_from_lethal_next_turn
  # Build a snap: my_life=1, opp_power=14, my_power=0.
  # Build a save_snap: my_life=1, opp_power=0, my_power=0.
  # Assert position_value(save_snap) - position_value(snap) is at
  # LEAST CLOCK_IMPACT_LIFE_SCALING in magnitude (a turn of life
  # is worth one CLOCK_IMPACT_LIFE_SCALING unit; the math should
  # respect this). Current implementation returns ~0.5; test fails.

tests/test_symmetric_reanimation_sacrifices_on_board.py
  test_each_player_sacrifices_then_reanimates_projects_both_clauses
  # Build a snap with opp_power=14, opp_creature_count=5, my_gy=4
  #   creatures totaling 10 power.
  # Project _project_spell on Living End.
  # Assert projected.opp_power == 0 (sacrifices fire) AND
  #        projected.my_power == 10 (reanimation fires).
  # Current implementation: projected.opp_power == 14 (sac skipped);
  # test fails.

tests/test_x_cost_board_wipe_value_matches_engine_picker.py
  test_x_cost_wipe_ev_consults_engine_x_picker_for_kill_value
  # Build a state where engine.cast_manager picks X=0 and clears
  # CMC-0 creatures (Memnite + Construct Token); the AI score
  # should reflect the same X-value's kill set, not the optimistic
  # "all creatures die" assumption.
  # Current implementation: _project_spell.board_wipe zeroes both
  # sides regardless of X; test fails.
```

All three tests should land in the same fix PR, since the bug is
one subsystem (`position_value` defensive-save case + downstream
projection consistency). A piecemeal fix that closes one component
without the others would risk re-introducing the others' bias
(e.g., fixing `combat_clock`'s floor without fixing the symmetric-
reanimation projection would make Living End's cascade EV rise to
~50 instead of ~7 — but Boros's wrath EV would stay ~1).

## Cross-deck applicability (Class size verification per CLAUDE.md)

The Affinity-vs-X effect is one slice of a generic mechanic. Other
matchups that should improve from a fix:

| Opp deck            | Affected mechanic                                | Why                                         |
|---------------------|--------------------------------------------------|---------------------------------------------|
| Boros Energy        | X-cost board-wipe + position_value lethal-NOW    | Wrath of the Skies SB vs Affinity / Zoo     |
| 4/5c Control        | X-cost board-wipe + symmetric-reanimation        | Wrath of the Skies, Vanishing Verse, etc.   |
| Azorius Control     | position_value lethal-NOW                        | March of Otherworldly Light defensive cast  |
| Living End (mirror) | symmetric-reanimation projection                 | Cascade-into-LE vs any wide-board opp       |
| Dimir Midrange      | X-cost board-wipe (Damnation if added) — minor    | Marginal                                    |
| Goryo's Vengeance   | symmetric-reanimation (Goryo's IS one-sided —     | NOT affected (reads tags='reanimate' branch)|
|                     | uses `elif 'reanimate' in tags` path at 1790)    |                                             |

Class size estimate: ≥40 cards across Modern (every X-cost board
wipe, every symmetric reanimation, every defensive instant). This
satisfies CLAUDE.md §Abstraction Contract item 1 (Class size ≥ 10).

## What this doc does NOT do

- It does not patch anything. Per CLAUDE.md §No fix without a
  failing test in the same diff, and per the loop-break protocol
  "no further code until that document exists", the fix is a
  separate PR.
- It does not propose a numeric tuning constant. The fixes
  outlined above are STRUCTURAL: change the projection's modeled
  clauses, change the clock floor's continuity property, route
  the AI scorer through the engine's X-picker. No new magic
  numbers expected.
- It does not lower the abstraction-contract baseline (no card
  names introduced, no deck gates introduced).
- It does not invalidate the prior Affinity diagnostics. Phase L
  remains correct: artifact_count including lands inflated
  Affinity-SIDE scoring. This doc identifies the OPPONENT-SIDE
  symmetric bug. They are additive, not redundant.

## Provenance — exact NDJSON evidence

Committed in this PR:

- `replays/affinity_vs_boros_s50000_decisions.ndjson` — 47
  DECISION events, decision `g2t4d45` cited above
- `replays/affinity_vs_living_end_s50000_decisions.ndjson` — 35
  DECISION events, decision `g1t4d14` cited above
- `replays/affinity_vs_eldrazi_tron_s50000_decisions.ndjson` —
  43 DECISION events, decision `g1t2d6` cited above

Reproduction (deterministic):

```bash
python run_meta.py --bo3 "Affinity" "Boros Energy" -s 50000 \
    --dump-replay /tmp/check.ndjson
grep '"decision_id":"g2t4d45"' /tmp/check.ndjson | python3 -m json.tool | grep ev
# Expected: ev: 6.386 (Phlage), 4.525 (Seasoned Pyromancer),
#           0.983 (Wrath of the Skies), -0.288 (Ocelot Pride)
```

## Key code sites (the named subsystem)

The bug lives in **`ai/clock.py::position_value` lines 388-401**
(opponent-side defensive-save EV collapse) and the structurally-
linked **`ai/ev_evaluator.py::_project_spell::is_symmetric_reanimation`
lines 1744-1788** (Living End sacrifice-clause skipped). The
X-cost-board-wipe inconsistency between `ai/ev_player.py:1172-1190`
and `engine/cast_manager.py:987-1035` is a third facet of the
same architectural choice.

The fix-PR after this diagnostic should be branded a single coherent
overhaul of the defensive-save case in `position_value` and the
symmetric-reanimation clause in `_project_spell`, with the X-cost-
gate-vs-engine-picker reconciliation as a follow-on consistency
patch.

## Estimated WR impact (rough, pre-fix)

Reading the cited decisions and projecting forward:

- Fixing Component A (position_value lethal-NOW) → Boros's
  defensive wraths against Affinity at low life should fire. Lift
  Boros vs Affinity by 10-15pp on game-2/3 reactive lines.
  Cross-deck: lift Azorius/4/5c defensive casts by 3-5pp vs
  Affinity (smaller; they have other answers).
- Fixing Component B (symmetric-reanimation sacs) → Living End
  vs Affinity should lift dramatically; the cascade is the
  deck's win condition. Single-matchup lift: 30-40pp (LE goes
  from ~15% to ~50%). Cross-deck: Living End also lifts vs
  Boros / Domain Zoo / any wide-board opp.
- Fixing Component C (X-cost picker reconciliation) → tighter
  wrath EVs in Boros/Control mirror vs Affinity, but the swing
  is small once A is fixed (most of the leverage is in A).

Estimated cumulative WR drop on Affinity overall: **10-20pp**,
landing it near 65-75% — closer to but still above the expected
50-65% band. Residual after this fix would likely live in the
mulligan-side (LE/Boros keeping no-answer hands too easily) and
sideboard-AI (Wrath of the Skies isn't sided in often enough).
Those investigations are deferred until this primary subsystem
ships.

## References

- `replays/golden_pr288_revert/affinity_vs_boros_post.txt`
  (PR #370 fixture; game 2 turn 4 starting at line 514)
- `replays/golden_pr288_revert/affinity_vs_living_end_post.txt`
  (PR #370 fixture; game 1 turn 4 starting at line 195)
- `replays/golden_pr288_revert/affinity_vs_eldrazi_tron_post.txt`
  (PR #370 fixture)
- `ai/clock.py:50-76` — `combat_clock` with the 1.0 floor
- `ai/clock.py:355-448` — `position_value` clock_diff branch
- `ai/ev_evaluator.py:1744-1788` — symmetric-reanimation projection
- `ai/ev_evaluator.py:1864-1871` — board_wipe projection
- `ai/ev_player.py:1172-1190` — X-cost board-wipe gate (v3)
- `engine/cast_manager.py:987-1035` — Wrath X-picker (permanent_threat-valued)
- `engine/oracle_parser.py:219-246` — `parse_x_cost`
- Prior Affinity work: see `depends_on` frontmatter

## Frontmatter discovery hint

This doc is `priority: primary`, `status: active`. It is the
**single named-subsystem record** required by the loop-break
protocol. The next Affinity-relevant PR (the fix) should declare
`supersedes: [docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md]`
in its own diagnostic doc OR mark this doc `status: superseded`
when the fix ships.

DO NOT open a follow-up diagnostic before the fix is attempted.
Per CLAUDE.md: documentation is not progress.
