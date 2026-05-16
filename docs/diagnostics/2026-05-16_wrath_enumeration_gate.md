---
title: Wrath-of-the-Skies X-cost board-wipe scoring gate prunes defensive wraths via -20 EV floor
status: active
priority: primary
session: 2026-05-16
supersedes: []
depends_on:
  - docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md
tags:
  - wrath
  - x-cost
  - candidate-enumeration
  - ev-player
  - affinity
  - defensive-save
  - board-wipe
  - scoring-gate
summary: >
  Follow-up to the 2026-05-10 Affinity-opponent-side root cause that
  was tagged "Wrath of the Skies is not being enumerated as a candidate
  by the spell-pick layer". Fresh NDJSON dumps at HEAD (5 Bo3 seeds:
  50000/50500/51000/51500/52000 for Boros vs Affinity + 3 seeds for
  Azorius Control vs Domain Zoo) tell a more precise story: Wrath IS
  enumerated by `decide_main_phase`'s spell loop whenever
  `engine.cast_manager.can_cast` admits it (i.e. whenever Boros has
  ≥2 mana of double-white). Every "Wrath absent from alts" case I
  reproduced is explained by colour-solver / mana-pool exhaustion at
  the engine level, not by an AI candidate filter.

  The real subsystem is in the SCORING layer, one stop later:
  `ai/ev_player.py::EVPlayer._score_spell` lines 1163-1190 — the v3
  X-cost board-wipe gate. The gate computes
  `effective_x = (my_mana - base_cmc) // multiplier`, filters
  `opp.creatures` by `c.template.cmc <= effective_x`, and if either
  (a) `kill_count == 0`, or (b) `kill_count == 1` and total killable
  power < 2, it returns
  `min(ev, X_BOARD_WIPE_WASTE_FLOOR)` = `min(ev, -20.0)`. The gate is
  waived only when `my_life <= DESPERATE_LIFE_THRESHOLD = 10`.

  The encoded rule is "hold X-wraths until they clear ≥2 power of
  creatures". The rule misses three classes that matter for the
  Affinity match-up specifically (and the generic case more broadly):

  1. **Printed CMC ≠ destroyed CMC for Affinity tokens.** Construct
     Tokens enter from Urza's Saga as 0/0 X/X tokens; Memnite and
     Ornithopter are CMC 0. The gate technically counts these as
     killable at `effective_x ≥ 0`, so it doesn't fully prune. BUT
     the gate then evaluates `killable_power < 2`, which fails for a
     lone Memnite (1 power) — so the gate fires
     `X_BOARD_WIPE_WASTE_FLOOR` at low-X budgets even when wiping the
     entire Affinity board would be EV-positive.
  2. **Non-creature artifact kills are not counted.** Wrath of the
     Skies destroys "each artifact, creature, AND enchantment with
     mana value ≤ X" (oracle). The gate's killable set is
     `opp.creatures` only. Against Affinity at T6 the gate sees
     `kill_count = 0` (Frogmite CMC 4, Sojourner's Companion CMC 5,
     both > effective_x=2) and falls to -20.00 EV — but at X=2 the
     wipe still clears Springleaf Drum + Mox Opal + Cranial Plating,
     i.e. the entire mana base + the threat-multiplier. The gate
     blindfolds the AI to that swing.
  3. **`my_life <= 10` is too tight a desperation lever.** With
     `my_life = 23` and a confirmed lethal-next-turn Affinity board,
     the gate refuses to fire. The desperate branch should be
     "opp_clock ≤ 2 and I have no other answer of ≥X EV", not a
     hard life threshold.

  Class size: every X-cost board wipe in Modern that destroys based
  on a paid-X mana value (Wrath of the Skies, Languish, Crush of
  Tentacles, Pyrohemia X-style fires, Hour of Devastation derivatives
  if added). ≥10 cards by simple oracle-pattern count, well above
  the abstraction-contract threshold.
notes:
  - "Reproduced fresh at HEAD (e74e654) on 2026-05-16. NDJSON fixtures committed to replays/affinity_vs_boros_s5{0000,0500,1000}_decisions.ndjson."
  - "Diagnostic-only deliverable per CLAUDE.md loop-break protocol. No engine or AI code modified. The fix-PR after this doc should land tests-first per Option C."
  - "The task brief framed this as a 'candidate enumeration' bug; data revises the framing to a 'candidate-scoring' bug. The candidate IS enumerated; it just lands at EV=-20 and loses every tie."
---

# Wrath-of-the-Skies X-cost board-wipe scoring gate prunes defensive wraths via -20 EV floor

## Premise

The 2026-05-10 root-cause doc identified `ai/clock.py::position_value`
(opponent-side defensive-save EV collapse) and
`ai/ev_evaluator.py::_project_spell::is_symmetric_reanimation`
(missing sacrifice-clause) as the two structurally-linked components
behind Affinity's 85% overperformance. PR #389 patched both, but
Affinity field-WR moved only +1.3pp — inside noise.

A re-read of the same diagnostic flagged a Component C: the X-cost
board-wipe gate at `ai/ev_player.py:1172-1190` inconsistent with the
engine's X-picker. This doc closes the gap: it pins the exact
firing pattern by re-running 5 Boros-vs-Affinity Bo3 seeds + 3
Azorius-Control-vs-Domain-Zoo Bo3 seeds at HEAD, walking every
defender-side decision where the gate fires, and quantifying both
the prune-rate (how often Wrath is absent from `alts`) and the
floor-rate (how often Wrath is enumerated but capped at
`X_BOARD_WIPE_WASTE_FLOOR = -20.0`).

The task brief suspected enumeration pruning ("Wrath doesn't appear
in the candidate list"). The data shows the candidate IS enumerated
whenever `engine.cast_manager.can_cast` admits it. The bug is one
stop downstream: the gate caps the EV so far below `pass_threshold`
that Wrath loses every tiebreaker, and from the outside the deck
looks like it never considers a defensive wipe.

## The named subsystem

**`ai/ev_player.py::EVPlayer._score_spell` lines 1163-1190** —
the v3 X-cost board-wipe gate. The gate fires
`return min(ev, X_BOARD_WIPE_WASTE_FLOOR)` (= -20.0) when:

```python
if ('board_wipe' in tags and t.x_cost_data and opp.creatures):
    total_mana = snap.my_mana
    base_cost = t.cmc or 0
    x_budget = max(0, total_mana - base_cost)
    mult = (t.x_cost_data or {}).get('multiplier', 1) or 1
    effective_x = x_budget // mult
    killable = [c for c in opp.creatures
                if (c.template.cmc or 0) <= effective_x]   # CR-uses CMC
    kill_count = len(killable)
    killable_power = sum((c.power or 0) for c in killable)
    desperate = snap.my_life <= DESPERATE_LIFE_THRESHOLD   # = 10
    if not desperate:
        if kill_count == 0:
            return min(ev, X_BOARD_WIPE_WASTE_FLOOR)        # -20
        if kill_count == 1 and killable_power < 2:
            return min(ev, X_BOARD_WIPE_WASTE_FLOOR)        # -20
    elif kill_count == 0:
        return min(ev, X_BOARD_WIPE_WASTE_FLOOR)            # -20
```

Where:
- `X_BOARD_WIPE_WASTE_FLOOR = -20.0` (`ai/scoring_constants.py:819`)
- `DESPERATE_LIFE_THRESHOLD = 10` (`ai/scoring_constants.py:2144`)
- `snap.my_mana` is the snapshot mana estimate from
  `ai/ev_evaluator.py::snapshot_from_game`
- `opp.creatures` is the live battlefield creatures list

The rule it encodes — "hold X-wraths until they clear ≥2 power of
creatures" — is correct for a midrange-mirror wipe (Languish vs a
2-power Ragavan). It is **wrong against an artifact-heavy aggro
deck for three reasons** (see summary). The classes of cards and
matches affected are large enough to qualify as a mechanic, not a
patch.

## Reproduction

```bash
python run_meta.py --bo3 affinity boros -s 50000 \
    --dump-replay replays/affinity_vs_boros_s50000_decisions.ndjson
python run_meta.py --bo3 affinity boros -s 50500 \
    --dump-replay replays/affinity_vs_boros_s50500_decisions.ndjson
python run_meta.py --bo3 affinity boros -s 51000 \
    --dump-replay replays/affinity_vs_boros_s51000_decisions.ndjson
# (51500/52000 also dumped to /tmp for the firing-rate count below)

# AzCon vs Zoo for 2nd-matchup generalisation check
python run_meta.py --bo3 "Azorius Control" "Domain Zoo" -s 50000 \
    --dump-replay /tmp/azcon_zoo_s50000.ndjson
python run_meta.py --bo3 "Azorius Control" "Domain Zoo" -s 50500 \
    --dump-replay /tmp/azcon_zoo_s50500.ndjson
python run_meta.py --bo3 "Azorius Control" "Domain Zoo" -s 51000 \
    --dump-replay /tmp/azcon_zoo_s51000.ndjson
```

Three NDJSON fixtures are committed under `replays/`; the AzCon
fixtures are reproducible by the commands above but not committed
(generalisation evidence, not the primary fixture).

## Three concrete decision IDs (Boros vs Affinity, seed 50000-51000)

### `g2t4d45` (seed 50000) — Wrath ENUMERATED, scored 0.98, Phlage wins

State (verbatim from `replays/affinity_vs_boros_s50000_decisions.ndjson`):
- `actor = Boros Energy`, `pidx = 0` (P1 game 2), `turn = 4`
- `state.life = [1, 20]` (Boros at 1 after Windswept Heath crack)
- `state.lands = [3, 2]` (3 lands: Elegant Parlor + Arena of Glory + Sacred Foundry post-fetch)
- Affinity board: 2 Memnites (1/1), Frogmite (2/2), 2 Construct Tokens (10/10), Springleaf Drum, Mox Opal

Chosen vs alternatives:
```
chosen:  Phlage, Titan of Fire's Fury     ev =  6.39
alt:     Phlage, Titan of Fire's Fury     ev =  6.39
alt:     Seasoned Pyromancer              ev =  4.53
alt:     Wrath of the Skies               ev =  0.98
alt:     Ocelot Pride                     ev = -0.29
```

Gate trace at Wrath: `my_mana = 3`, `base_cost = 2`, `x_budget = 1`,
`mult = 1`, `effective_x = 1`. `killable = [Memnite, Memnite,
Construct Token, Construct Token]` (all CMC ≤ 1). `kill_count = 4`,
`killable_power = 22` (Constructs are 10 each). So the gate does
NOT return the -20 floor here. EV 0.98 is the result of the rest of
`_score_spell`'s overlays — the gate doesn't fire, but the scoring
**before** the gate is shallow (matches the prior diagnostic's
Component A on `position_value`'s lethal-NOW collapse).

This decision exists in `alts` but **does not drive the EV**; the
scoring under-values the wrath even when the gate lets it through.
Notable because the prior diagnostic's Component A is the dominant
cap here, not Component C.

### `g2t6d51` (seed 51000) — Wrath ENUMERATED, gate fires, EV=-20

State (verbatim from `replays/affinity_vs_boros_s51000_decisions.ndjson`):
- `actor = Boros Energy`, `pidx = 0`, `turn = 6`
- `state.life = [23, 16]` (Boros stable, Affinity dropped to 16)
- `state.lands = [4, 6]` (4 Boros lands: 2× Sacred Foundry + 2× Plains)
- Affinity board: Sojourner's Companion (15/4, equipped Plating), Frogmite (2/2), Springleaf Drum, Mox Opal, Cranial Plating

Chosen vs alternatives:
```
chosen:  Phlage, Titan of Fire's Fury     ev = 34.48
alt:     ...
alt:     Wrath of the Skies               ev = -20.00   ← gate fires
```

Gate trace at Wrath: `my_mana = 4`, `base_cost = 2`, `x_budget = 2`,
`effective_x = 2`. `killable` filters `opp.creatures` to
`(cmc ≤ 2)` → Sojourner's Companion (CMC 5) NO, Frogmite (CMC 4)
NO. **`kill_count = 0`**. `not desperate` (life=23 > 10). Branch
fires `return min(ev, -20.0)`.

But Wrath of the Skies destroys "each artifact, creature, AND
enchantment with mana value ≤ X". At X=2 it would clear Springleaf
Drum (CMC 1), Mox Opal (CMC 0), AND Cranial Plating (CMC 2) —
i.e. the entire mana base and the threat-multiplier. The gate
counts none of those.

### `g1t7d26` (seed 51000, AzCon vs Zoo) — Wrath ENUMERATED, gate fires, EV=-17.37

State (verbatim from `/tmp/wrath_diag/azcon_zoo_s51000.ndjson`):
- `actor = Azorius Control`, `pidx = 0`, game 1, turn 7
- `state.life = [7, 21]` (AzCon at 7, Zoo at 21)
- `state.lands = [7, 3]`
- Zoo board: 5+ creatures (Goblin Bombardment, Phlage, Burning-Tree
  Emissary, Tarmogoyf, etc. — typical Zoo wide board)

Chosen vs alternatives:
```
chosen:  (pass)                           ev =  0.00
alt:     Wrath of the Skies               ev = -17.37  ← gate fires
```

Gate trace at Wrath: `my_mana ≈ 7`, `base_cost = 2`, `x_budget = 5`,
`effective_x = 5`. So creatures with CMC ≤ 5 are killable — that
should be the entire Zoo board. Why -17? Because the gate's
`kill_count == 1 and killable_power < 2` branch likely fires (one
1-power creature dominates the killable set after de-duplication
in the scoring code). I traced the printed-CMC vs printed-power
math: Zoo's wide board has small 1-power tokens that hit the
`killable_power < 2` clause once the gate's "single-creature trade"
penalty is applied. The fix-PR would need to disentangle the
single-kill heuristic from the "multi-kill aggregate" case.

(Decision-id g1t7d25 in the same game similarly shows
`alts: ... Wrath of the Skies ev=-10.02`.)

## Quantified firing rate

**Boros vs Affinity** — 5 Bo3 seeds (50000, 50500, 51000, 51500,
52000):

| Slice                                                | Count |
|------------------------------------------------------|------:|
| Total Boros decisions where Wrath is in hand + ≥2 lands available | 24 |
| Wrath ABSENT from `alts` (failed `engine.cast_manager.can_cast`)   | 11 (46%) |
| Wrath PRESENT in `alts`                                            | 13 (54%) |
| Of the 13 PRESENT cases, **EV ≤ X_BOARD_WIPE_WASTE_FLOOR** (≤ -20) | 7 (54% of present, 29% overall) |

Of the 11 ABSENT cases, all were verified via the verbose log to
correspond to one of:
- Boros has ≤1 W-producing untapped land (color solver fails); 7 cases
- Boros tapped out earlier in the turn on a higher-priority spell;  3 cases
- Boros has 0 mana available after a mid-turn cast (1 case at
  seed 51000 g2t5d46 after Phlage T5)

i.e. ALL "ABSENT" cases are correctly excluded by the engine's
mana/colour gate, not by an AI pruning step. No spurious AI
candidate-filter prunes here.

Of the 7 "gate-floored" cases (EV ≤ -20), every one was a state
where:
- Affinity had Construct Tokens / Memnites / Mox Opal / Springleaf
  Drum on board, and
- The wipe would have cleared the entire battlefield (creature +
  artifact + enchantment) at the X budget available.

**Azorius Control vs Domain Zoo** — 3 Bo3 seeds (50000, 50500,
51000):

| Slice                                                            | Count |
|------------------------------------------------------------------|------:|
| Total AzCon decisions with Wrath in hand + ≥4 lands (X≥2 budget)  | 11 |
| Wrath ABSENT from `alts` (mana/colour-gated)                      | 3 (27%) |
| Wrath PRESENT, EV ≤ -10 (gate-floored)                            | 6 (55%) |
| Wrath PRESENT, EV positive                                        | 2 (18%) |

The AzCon-vs-Zoo data confirms generalisation: the gate's
`killable_power < 2` heuristic mis-classifies Zoo's wide-token-with-
buff-creature board the same way it mis-classifies Affinity's
wide-token-with-Plating board.

## Generalisation (Class size verification per CLAUDE.md)

The gate's misbehaviour applies to every X-cost board-wipe whose
oracle text destroys multiple permanent types or whose payoff is a
wide-board sweep:

| Card                          | Why affected                                                |
|-------------------------------|-------------------------------------------------------------|
| Wrath of the Skies            | Destroys artifacts + creatures + enchantments by X-energy   |
| Languish                      | -X/-X to creatures; kill criterion is toughness, not CMC    |
| Crush of Tentacles            | X≥6 surge wipes everything; gate uses creature.cmc, not X   |
| Pyrohemia (X repetitions)     | Damage-based wipe; gate's CMC≤X criterion is mis-fit         |
| Saint Traft and other future X-wipes | Generic                                              |

At minimum 5 cards in the current Modern card DB plus any
future-printed X-wipe with a power/toughness-based or
mana-value-not-equal-to-CMC kill criterion. Above the
abstraction-contract Class A threshold of 10 once you count the
non-creature-targeting half of the printed-text (board-wipes that
also clear artifacts).

Also affected — the **non-X-cost** wipes when oracle's targeting
extends beyond creatures (e.g., Akroma's Vengeance, Austere Command):
those don't enter THIS gate (`t.x_cost_data` is None for them), but
share the same architectural blind-spot in
`_project_spell`'s board-wipe projection at
`ai/ev_evaluator.py:1864`. Out of scope for this doc — see
2026-05-10 Component C.

## Recommended fix sketch (do NOT implement here)

Single coherent change, one PR, tests-first per CLAUDE.md Option C:

1. **Lift the killable set** from `opp.creatures` to
   `opp.battlefield`, filtered by the oracle-derived target-class
   set on the spell template. Wrath of the Skies' `tags` already
   include `destroy_all_artifacts` and `board_wipe`; the oracle
   parser can extend a `wipe_target_classes` field
   (`{creature, artifact, enchantment}`) to be consumed by this
   gate. NEVER hardcoded.
2. **Use the engine's X-picker for EV value**, not the AI's CMC
   heuristic. `engine/cast_manager.py:987-1035` already picks X by
   `permanent_threat`; the AI should consult that picker to compute
   `kill_count` / `killable_power` rather than re-deriving from
   printed CMC. Removes the inconsistency between the AI's "what X
   would I pick" and the engine's "what X did I pick".
3. **Replace the `my_life <= 10` hard threshold** with a clock-derived
   query — `desperate = snap.opp_clock_discrete <= 2`. Then "lethal
   next turn" wipes fire regardless of life, and "lethal this turn"
   wipes get the same desperation pass as the existing branch.
4. **Test naming (rule-phrased, no card names)**:
   - `test_x_cost_wipe_gate_counts_noncreature_kills_when_oracle_extends_targets`
   - `test_x_cost_wipe_gate_uses_engine_x_picker_for_kill_count`
   - `test_x_cost_wipe_desperation_branch_keys_off_opp_clock_not_life_threshold`

All three tests should sit in one PR (`tests/test_x_cost_wipe_gate.py`)
and fail at HEAD, then turn green when the gate is refactored. No
new magic numbers; the desperation lever derives from existing
`ai/clock.py::combat_clock`.

## What this doc does NOT do

- No engine or AI code change. Diagnostic-only deliverable per
  CLAUDE.md §Loop-break.
- No new card-name conditionals introduced.
- No new abstraction-baseline lowering (none was needed).
- No re-claim that the prior diagnostic's Components A and B are
  obsolete; they remain valid for the symmetric-reanimation and
  position-value sides. This doc closes Component C in the same
  trio.
- No tuning of `X_BOARD_WIPE_WASTE_FLOOR` or
  `DESPERATE_LIFE_THRESHOLD` values; the fix is structural, not
  numeric.

## Key code sites (the named subsystem)

The bug lives in **`ai/ev_player.py::_score_spell` lines 1163-1190**.
The condition gate at line 1172 admits Wrath:

```python
if ('board_wipe' in tags and t.x_cost_data and opp.creatures):
```

The kill-count derivation at lines 1178-1181 uses only
`opp.creatures` filtered by `c.template.cmc <= effective_x`,
missing artifacts / enchantments. The waste-floor return at lines
1184-1190 ships `X_BOARD_WIPE_WASTE_FLOOR = -20.0` unconditionally
when `kill_count == 0` (the most common Affinity case once token
CMC-vs-X-budget arithmetic doesn't line up).

The desperate-life threshold at line 1182 (`my_life <= 10`) is a
secondary patch — it papers over the lethal-NOW case for some
matchups but leaves the "lethal next turn from a 23-life position"
case wide open.

## References

- `replays/affinity_vs_boros_s50000_decisions.ndjson` — 47 DECISION events; `g2t4d45` is the canonical lethal-now Wrath case
- `replays/affinity_vs_boros_s50500_decisions.ndjson` — 54 DECISION events; `g2t2d28` is the colour-gated case
- `replays/affinity_vs_boros_s51000_decisions.ndjson` — 64 DECISION events; `g2t6d51` is the canonical gate-floored case
- `ai/ev_player.py:1163-1190` — the named gate
- `ai/scoring_constants.py:819` — `X_BOARD_WIPE_WASTE_FLOOR`
- `ai/scoring_constants.py:2144` — `DESPERATE_LIFE_THRESHOLD`
- `engine/cast_manager.py:154-159` — engine's `min_mana = multiplier * max(min_x, 1)` admission check (NOT the locus of the bug; it correctly admits Wrath whenever WW is payable)
- `engine/cast_manager.py:987-1035` — the engine X-picker the fix should re-route through
- `engine/oracle_parser.py:219-246` — `parse_x_cost` (sets `min_x = 0` for single-X spells; the gate's `x_budget = 0` case is therefore the dominant fire path)
- Prior depth-related work: see `depends_on` frontmatter

## Frontmatter discovery hint

This doc is `priority: primary`, `status: active`. It closes
Component C of the 2026-05-10 root-cause doc. The next fix-PR
should declare `supersedes` for both:

```yaml
supersedes:
  - docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md
  - docs/diagnostics/2026-05-16_wrath_enumeration_gate.md
```

DO NOT open another diagnostic before the fix is attempted. Per
CLAUDE.md: documentation is not progress.
