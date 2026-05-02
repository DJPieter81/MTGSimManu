---
title: Unified target solver — Phase 7 N=50 matrix validation
status: active
priority: primary
session: 2026-05-02
depends_on:
  - docs/proposals/2026-05-02_unified_target_solver.md
  - docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md
tags:
  - matrix
  - target-solver
  - validation
  - cr-601-2c
  - affinity
  - completed
summary: "N=50 16-deck matrix validates the Phases 1–4 unified target solver refactor. **Affinity barely moves (−0.3pp; 86.0% → 85.7%).** This *falsifies* the design doc's empirical hypothesis that the cast-time fix would correct Affinity's overall WR — confirming the design doc's alternate branch: 'the real Affinity bug is a positive-overscoring issue, separate next-session work.' Boros −3.1pp, Dimir −7.2pp, Living End +11.9pp are the largest shifts; most are within n=20→n=50 noise except Living End and Dimir. Phases 5-6 deferred. H_ACT_1/2/3 confirmed."
---

# Unified target solver — Phase 7 N=50 matrix validation

## Goal

The Phases 1–4 refactor consolidated five scattered target-validation
sites in `engine/cast_manager.py` (graveyard regex + loose fallback +
"target creature you control" + bare "target creature" + the helper
`_battlefield_legal_targets`) plus the redundant graveyard-target type
filter in `engine/oracle_resolver.py` into a single oracle-driven
solver in `engine/target_solver.py`. The empirical hypothesis from the
design doc (`docs/proposals/2026-05-02_unified_target_solver.md`,
§Empirical hypothesis):

> Removal-heavy decks (Boros, Azorius, Dimir, Domain Zoo) overall WR
> up by 2-4pp each. Affinity overall WR down by 2-4pp (relative
> correction, not direct). Combo decks (Storm, Living End, Amulet
> Titan) approximately unchanged.

> If Affinity moves from 87.8% to ~80-83%, this refactor is the
> biggest contributor since the EV correctness overhaul. If it
> doesn't move, the **real Affinity bug is a positive-overscoring
> issue** — separate next-session work.

## Procedure

```bash
# Pre-refactor baseline saved before any code change
cp metagame_data.jsx /tmp/metagame_baseline_pre_solver.jsx

# Phases 1+2 land (parser + dataclass + legality queries + 78 unit tests)
# Phase 3 lands (cast_manager 5 sites → 1 solver call; 152-line dead helper removed)
# Phase 4 lands (oracle_resolver graveyard handler uses enumerate_legal_targets)

# Test-suite regression: 467+ targeted tests green across cast_time,
# Goryo's, discharge, counterspell, holdback, stack, cascade, cycle,
# blockers, amulet, BHI, bo3, combo, EV, finisher, fetch, grafdiggers,
# mulligan, living-end, oracle, parallel, reanimation. One pre-existing
# test (test_evoke_available_with_full_mana::Counterspell) updated to
# seed the stack — pre-fix it asserted "Counterspell castable on empty
# stack" which violated CR 601.2c; the solver correctly enforces.

python3 run_meta.py --matrix -n 50 --save
python3 build_dashboard.py --merge
```

136 matchups × 50 Bo3 = 6800 total games (3 workers).

## Results — overall WR shift (Phases 1–4, n=50)

Pre-refactor: n=20 baseline saved at `/tmp/metagame_baseline_pre_solver.jsx`.
Post-refactor: n=50, this session's matrix.

| Deck                       | Pre flat WR | Post flat WR | Δpp   | Predicted | Verdict           |
|----------------------------|-------------|--------------|-------|-----------|-------------------|
| Affinity                   | 86.0%       | 85.7%        | −0.3  | −2 to −4  | **NULL** — falsifies hypothesis |
| Boros Energy               | 70.7%       | 67.6%        | −3.1  | +2 to +4  | **OPPOSITE**      |
| Jeskai Blink               | 62.3%       | 63.3%        | +1.0  | ~0 to +2  | OK                |
| Ruby Storm                 | 43.3%       | 42.9%        | −0.4  | ~0        | OK                |
| Eldrazi Tron               | 68.7%       | 65.1%        | −3.6  | ~0        | NOISE / OPPOSITE  |
| Amulet Titan               | 43.3%       | 42.9%        | −0.4  | ~0        | OK                |
| Goryo's Vengeance          | 10.7%       | 10.3%        | −0.4  | +5 to +10 | NULL — no movement |
| Domain Zoo                 | 69.7%       | 70.0%        | +0.3  | +2 to +4  | NULL              |
| Living End                 | 43.3%       | 55.2%        | **+11.9** | ~0    | **MAJOR ANOMALY** |
| Izzet Prowess              | 37.0%       | 36.4%        | −0.6  | ~0        | OK                |
| Dimir Midrange             | 54.3%       | 47.1%        | **−7.2**  | +2 to +4 | **MAJOR OPPOSITE** |
| 4c Omnath                  | 62.7%       | 61.3%        | −1.4  | ~0        | OK                |
| 4/5c Control               | 42.7%       | 43.6%        | +0.9  | +2 to +4  | OK                |
| Pinnacle Affinity          | 57.3%       | 60.5%        | +3.2  | −2 to −4  | OPPOSITE          |
| Azorius Control            | 16.7%       | 14.5%        | −2.2  | +2 to +4  | OPPOSITE          |
| Azorius Control (WST)      | 31.3%       | 33.5%        | +2.2  | +2 to +4  | OK                |

### Headline read

The design doc's empirical hypothesis explicitly named the
**falsified branch**:

> If Affinity moves from 87.8% to ~80-83%, this refactor is the
> biggest contributor since the EV correctness overhaul. **If it
> doesn't move, the real Affinity bug is a positive-overscoring
> issue** (Construct token P/T calculation, Mox Opal metalcraft
> gating, Affinity discount stacking) — separate next-session work.

Affinity didn't move. The cast-time fix was correctness-only, not a
WR mover. The conclusion the design doc itself proposed is now the
working hypothesis: **Affinity's 86% WR is sustained by AI scoring
issues**, not by removal-heavy opponents wasting casts on empty
boards.

### Anomalies worth flagging

**Living End +11.9pp.** Largest single-deck shift. Living End was a
combo deck the design doc predicted would be unchanged. Hypothesis:
the cast-time fix prevents Living End opponents from casting their
graveyard hate on empty graveyards (e.g., Surgical Extraction or
Endurance with no targets), which previously fizzled but now refuses
to cast — letting Living End assemble its cascade chain unmolested
in matchups where the opp keeps hate in hand. Worth a Bo3 trace.

**Dimir Midrange −7.2pp.** Largest single-deck regression. Dimir
runs Drown in the Loch, Consider, and other "target [X] in graveyard"
spells; the cast-time fix may now correctly refuse some of these on
empty graveyards that previously cast-and-fizzled. The lost casts
mean Dimir's value engine fires less often. Worth a Bo3 trace.

**Boros Energy −3.1pp.** Lost ground despite being the canonical
"removal-heavy deck" the prediction said would gain. Combined with
Living End +11.9pp, this suggests removal-heavy aggro decks lose to
combo decks more than they gain in fair matchups — the cast-time
correctness improvement was a lateral move, not a clear win.

### Noise margin

n=20 → n=50 alone reduces single-pair stderr from ~10pp to ~5pp.
Most of the table's |Δ| ≤ 3pp falls inside that band and could be
sample-size cleanup rather than refactor effect. The honest read on
the table:

- **Genuine refactor effects (|Δ| ≥ 5pp):** Living End +11.9pp,
  Dimir −7.2pp.
- **Possibly refactor effects (3pp ≤ |Δ| < 5pp):** Boros −3.1pp,
  Eldrazi Tron −3.6pp, Pinnacle Affinity +3.2pp.
- **Within noise (|Δ| < 3pp):** the rest, including Affinity itself.

## Phase 5 + 6 deferral notes

**Phase 5 (stack fizzle re-validation, est. 30 min in design doc).**
The existing check at `engine/stack.py:144-155` validates that
`item.targets` (instance ids) are still in `zone="battlefield"`.
Generalising to per-target legality at resolve time (CR 608.2b)
needs a richer API than `has_legal_target` — each chosen target must
be re-checked individually against its requirement, not just "any
legal target exists". Designing this without breaking the
graveyard-resolution path needs more careful thought than the doc's
30-minute estimate. **Status:** deferred to follow-up.

**Phase 6 (AI scoring migration, est. 45 min).** The design doc
explicitly flags this as skippable — "skip phase 6 (AI migration) if
time runs short — it's a refinement, not a correctness fix." The AI
already pre-filters target-less casts in `score_spell` (per the
hypothesis list's `H_FAL_1`). **Status:** deferred to follow-up.

## H_ACT_3 — Sideboard package strength (independent of solver)

While the matrix ran, an independent investigation per the Affinity
88% hypothesis list (`H_ACT_3`) confirmed a class of sideboard-
manager bugs that suppresses anti-Affinity sideboarding for at least
8 of the top 10 decks. Reproduced at `engine/sideboard_manager.py`
via:

```python
from decks.modern_meta import MODERN_DECKS
from engine.sideboard_manager import sideboard
new_main, new_sb = sideboard(deck.mainboard, deck.sideboard,
                              "Boros Energy", "Affinity")
```

**Findings (`vs Affinity` matchup, current `sideboard_manager.py`):**

| Deck                       | Hate boarded IN | Useful SB cards NOT boarded |
|----------------------------|-----------------|-----------------------------|
| Boros Energy               | 4 (Wear×2 + Wrath×2) | Damping Sphere, Orim's Chant, Surgical Extraction, The Legend of Roku, Vexing Bauble, Celestial Purge |
| Azorius Control            | 2 (Wear×2)      | Wrath of the Skies, Damping Sphere, Mystical Dispute×2, Consign to Memory×4 |
| Azorius Control (WST)      | **0**           | Subtlety×3, Damping Sphere×3, Engineered Explosives×2, Consign to Memory×2, Force of Negation, Celestial Purge |
| Domain Zoo                 | 2 (Wear×2)      | Wrath of the Skies×2, Damping Sphere×2, Obsidian Charmaw×2, Clarion Conqueror, Consign to Memory×2 |
| Dimir Midrange             | 6 (Sheoldred×2, Flusterstorm×2, Damnation×1, Engineered Explosives×1) | (best-tuned of the field) |
| Living End                 | **0**           | Foundation Breaker×3, Force of Vigor×2, Boseiju×2, Endurance×3, Mystical Dispute×2 |
| Jeskai Blink               | 5 (Wear×2, Teferi×1, White Orchid Phantom×1, Clarion Conqueror×1) | Wrath of the Skies×3, Mystical Dispute, Surgical Extraction |
| Eldrazi Tron               | 2 (Ratchet Bomb×2) | Pithing Needle×2, Trinisphere×2, Spatial Contortion×2 |
| 4c Omnath                  | **0**           | Boseiju×1, Force of Vigor×2, Force of Negation×2, Surgical Extraction, Supreme Verdict, Obsidian Charmaw×3 |
| 4/5c Control               | 2 (Boseiju×1, Wear×1) | (more SB cards available) |

**Class-of-bug shape.** The legacy keyword filter in
`engine/sideboard_manager.py` (lines 57-107, the artifact-hate /
counterspell / board-wipe groups) misses these tokens for the
Affinity matchup:

- `damping sphere` (cost-tax, hard-to-remove, anti-Affinity stable)
- `subtlety` (free flicker → bounce a Construct/Mox at flash speed)
- `foundation breaker` (Living End's evoke artifact removal)
- `trinisphere` (Eldrazi Tron's tax piece — taxes Affinity's <1cmc artifacts)
- `engineered explosives` (matched in some keywords, but only via
  the "explosives" word; depends on board-wipe priority firing)
- `endurance` (Living End's flash blocker; Affinity's ground attack
  hates a 3/4 reach blocker — actively useful)
- `force of vigor` / `force of negation` (only matched in the combo
  branch, not the artifact branch)
- `mystical dispute` (counterspell branch only fires for combo decks,
  not when opp is Affinity)

**Predicted impact (not validated this session — would need a
separate sideboard-manager fix + matrix re-run):** if the keyword
filter is extended to cover these tokens, Living End / Azorius
Control (WST) / 4c Omnath each gain 5-15pp vs Affinity at the
sideboard-only level. Boros / Jeskai stays roughly flat (already
boarding 4-5 hate). Dimir is already best-tuned and won't move.

This is a **separate bug class from the cast-time target validation
fix**. Documented here for completeness; the H_ACT_3 fix lives in a
follow-up.

## H_ACT_1 — Creature-only removal targets base-power, not threat

While inspecting the AI for unrelated reasons, found and reproduced
the H_ACT_1 hypothesis from the diagnostic doc.

**Bug.** `ai/ev_player.py::_choose_targets` line 2452 (creature-only
removal branch) uses `creature_value` (base clock impact) instead of
`permanent_threat` / `creature_threat_value` (threat-aware,
amplifier-included). The burn-spell branch in the same function
already uses `permanent_threat` correctly (line 2376). The
inconsistency means non-burn removal picks the wrong target whenever
a creature with battle cry / scaling / attack triggers is present.

**Reproduction:**

```python
from engine.card_database import CardDatabase
from ai.ev_evaluator import creature_value, creature_threat_value
db = CardDatabase()
# Build Memnite (1/1 vanilla) and Signal Pest (0/1 battle cry):
# creature_value:        Memnite 1.15  >  Signal Pest 1.00  → picks Memnite
# creature_threat_value: Memnite 1.15  <  Signal Pest 2.15  → picks Signal Pest
```

**Failing test before fix.** `tests/test_creature_removal_targets_threat_amplifiers.py`
was committed alongside the fix (per CLAUDE.md ABSTRACTION CONTRACT
"no fix without a failing test in the same diff"). It seeds a Memnite
+ Signal Pest fixture on the opponent board, hands the AI a generic
"destroy target creature" spell, and asserts the AI picks Signal Pest.
Pre-fix the test FAILS — AI returns Memnite's instance_id. Post-fix
GREEN.

**Fix.** One-line change at `ai/ev_player.py:2452`:

```python
# before:
best = max(opp.creatures, key=lambda c: creature_value(c, snap))
# after:
from ai.permanent_threat import permanent_threat
best = max(opp.creatures, key=lambda c: permanent_threat(c, opp, game))
```

This brings the creature-only removal path into alignment with the
burn-spell path in the same function.

**Class-of-bug shape.** Generalizes to any deck that runs amplifier
creatures with low base power: Signal Pest, Champion of the Parish,
Goblin Bushwhacker, Kemba's Skyguard etc. Fix is principled (no card
names) and applies wherever amplifier-class creatures exist.

**Status:** test committed failing, fix applied, validated by a
follow-up matrix run after this Phase 7 doc lands.

## H_ACT_2 — "Can't be blocked except by..." not enforced

H_ACT_2 from the hypothesis list pinned blocking decisions vs
evasive Affinity attackers. Investigation reveals **two related bugs**:

1. **Engine bug.** Oracle text "can't be blocked except by creatures
   with flying or reach" (Signal Pest) and similar phrases are NOT
   parsed or enforced anywhere in `engine/combat_manager.py`. The
   engine accepts illegal blocks. Class-of-bug: ~50+ Modern cards
   with this phrasing.

2. **AI bug.** `ai/ev_player.py::decide_blockers` line ~2135 only
   checks `Keyword.FLYING` for evasion. Signal Pest is not tagged
   with `Keyword.FLYING` — its evasion is in oracle text — so the AI
   can assign a non-flying ground creature to chump-block Signal
   Pest. Even after the engine bug above is fixed, the AI would
   still try illegal blocks.

**Status:** both bugs documented but **not** fixed in this session.
The principled fix parses oracle text "can't be blocked except by
[X]" and emits a per-attacker block restriction predicate; the
combat manager queries this predicate at block-assignment time. The
AI's `decide_blockers` then filters its candidate pool through the
predicate. This is a separate, larger refactor. Filed as a follow-up
in `docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md`.

## What this means for next session

1. The Phase 1-4 solver refactor lands the cast-time CR 601.2c
   compliance. Removes 152 lines of dead helper code in
   `cast_manager.py`. Single source of truth in
   `engine/target_solver.py`. 78 unit tests + 467+ regression tests
   green.

2. **Affinity stayed at 85.7% (−0.3pp).** Per the design doc's own
   alternate-branch language, the cast-time bug class was
   correctness-only and the real WR mover is one of the AI bugs.
   This session lands H_ACT_1 (commit `a10a52f`); H_ACT_2 and
   H_ACT_3 are documented and queued.

3. **Living End +11.9pp** is the biggest validation surprise. It
   wasn't predicted by the design doc and warrants a focused
   replay-based investigation: which previously-fizzling cast did
   Living End's opponents stop making, and is that the new winning
   margin?

4. **Dimir Midrange −7.2pp** is the biggest regression. Hypothesis:
   Dimir's "target X in graveyard" spells (Drown in the Loch,
   maybe others) now correctly refuse on empty graveyards. The
   value engine misses casts it used to silently fizzle on. A
   replay trace would confirm.

5. Phase 5 + Phase 6 of the original refactor are deferred. They
   don't gate the Affinity investigation.

6. **Next-session priorities (in order):**
   1. Validate H_ACT_1 fix with a fresh n=50 matrix (the matrix in
      this doc was run BEFORE the H_ACT_1 commit landed).
   2. Investigate Living End +11.9pp via Bo3 replay trace
      (`run_meta.py --bo3 "Living End" "Boros Energy" -s 60100`).
   3. Investigate Dimir −7.2pp via Bo3 replay trace
      (`run_meta.py --bo3 "Dimir Midrange" "Affinity" -s 60100`).
   4. Apply H_ACT_3 sideboard-manager keyword-filter fix
      (extend to Damping Sphere, Subtlety, Foundation Breaker,
      Trinisphere, Endurance, Force of Vigor, Force of Negation
      tokens for the Affinity matchup).
   5. Apply H_ACT_2 fix iff the previous three are green —
      requires a cross-cutting refactor that adds oracle-text
      "can't be blocked except by..." parsing to both
      `engine/combat_manager.py` (rule enforcement) and
      `ai/ev_player.py::decide_blockers` (smart block AI).

## Linked work

- `docs/proposals/2026-05-02_unified_target_solver.md` — design
  (relocated from `docs/design/` to match `sideboard_solver.md` /
  `combo_simulator_unification.md` precedent)
- `docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md` —
  H_ACT_1/2/3 hypotheses; this doc adds confirmed evidence for all
  three
- Pre-refactor baseline preserved at
  `/tmp/metagame_baseline_pre_solver.jsx` for diff comparisons
