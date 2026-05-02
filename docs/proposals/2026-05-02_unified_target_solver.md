---
title: Unified Target Solver — Architectural Refactor
status: active
priority: primary
session: 2026-05-02
depends_on:
  - docs/history/sessions/2026-05-02_mulligan_typed_combo_paths_and_cast_target.md
tags:
  - architecture
  - cast-time-validation
  - cr-601-2c
  - target-resolution
  - oracle-driven
  - refactor
summary: "Consolidate scattered cast/trigger/resolve target validation into a single oracle-driven solver. Closes 250+ sister-bug cards (target-permanent, target-artifact, target-enchantment removal castable on empty boards). Prerequisite for next-session work on Affinity threat-overscoring diagnosis."
---

# Unified Target Solver — Architectural Refactor

## Problem statement

After ~100 iterations of bug-fix patches, `engine/cast_manager.py`
contains five independent target-validation code paths, each
implementing a partial, hand-rolled subset of CR 601.2c. Coverage is
inconsistent. Adding a new target pattern (e.g., a recent set's
"target nonland permanent an opponent controls") requires hunting
down each branch and replicating the logic.

Concrete symptoms observed in the 2026-05-02 session:
- Goryo's Vengeance fizzled silently when cast with empty graveyard
  (fixed in PR #220, branch `fix/cast-time-graveyard-target-validation`)
- 119 target-permanent spells (Vindicate, Abrupt Decay, Beast Within,
  Anguished Unmaking, Assassin's Trophy) castable on empty boards
- 127 target-artifact/enchantment spells (Wear // Tear, Disenchant,
  Nature's Claim, Ancient Grudge) castable with no target on board
- Total ~250+ Modern-pool cards affected by the same anti-pattern

## Current architecture (the problem)

`engine/cast_manager.py` (1326 lines) has five validation sites:

| Line | Code path | Coverage |
|------|-----------|----------|
| 130  | `'target creature you control' in oracle_l` | 1 pattern |
| 136  | `'target creature' in oracle_l` with hand-coded exceptions | 1 pattern |
| 200  | Graveyard-target dispatcher (PR #220 fix) | ~9 type filters, 2 supertype filters, 2 zone scopes |
| 351  | Evoke target validation (different code path) | 1 pattern |
| 811  | Force-of-Will / pitch-cast target check (third path) | 1 pattern |

Each path independently parses oracle text and queries game state.
Adding a sixth pattern means six near-duplicate edits.

`engine/oracle_resolver.py` (line 392) implements a *separate*
graveyard-target resolution at spell-resolve time — also redundant
with the cast-time check.

`engine/stack.py` (line 146) has a *third* fizzle check at trigger
resolve.

Three places parse "is there a legal target?" using three different
substring heuristics. They do not agree on edge cases (split cards,
modal spells, "up to N" optional targets).

## Proposed architecture

A single `engine/target_solver.py` module exposing:

```python
@dataclass(frozen=True)
class TargetRequirement:
    """One target requirement parsed from oracle text.

    A spell with multiple required targets has multiple
    TargetRequirements. Modal spells have one per mode (resolved
    later when the mode is chosen).
    """
    zone: Literal["battlefield", "graveyard", "hand", "library", "exile", "stack", "any"]
    type_filter: Optional[Literal["creature", "permanent", "artifact",
                                  "enchantment", "planeswalker", "land",
                                  "instant", "sorcery", "instant_or_sorcery",
                                  "card", "spell", "any"]]
    supertype_filter: Optional[Literal["legendary", "nonlegendary",
                                       "basic", "snow"]]
    owner_scope: Literal["you", "opponent", "any", "controller"]
    is_optional: bool   # "up to one target", "you may"
    count_min: int      # "target X" → 1; "two target Xs" → 2
    count_max: int      # equal to count_min unless "up to N"
    raw_phrase: str     # original oracle substring for debugging


def parse(oracle_text: str) -> List[TargetRequirement]:
    """Parse all target requirements from an oracle text.

    Returns empty list if no targets required (draw, mill, lifegain).
    Modal spells return one list per mode — caller picks based on
    chosen mode at cast time.
    """


def has_legal_target(game: GameState, controller: int,
                     req: TargetRequirement,
                     exclude: Optional[CardInstance] = None) -> bool:
    """CR 601.2c — does at least one legal target exist for this
    requirement at this moment in time?

    `exclude`: the spell being cast (cannot target itself in source
    zone — relevant for graveyard-cast spells like Persist).
    """


def enumerate_legal_targets(game: GameState, controller: int,
                            req: TargetRequirement) -> List[CardInstance]:
    """Same as has_legal_target but returns the candidate list.
    Used by AI scoring (best target choice) and by stack resolution
    (re-validate that chosen target is still legal at resolve)."""
```

### Call sites the refactor touches

1. `engine/cast_manager.py::can_cast` — replace 5 scattered checks with one loop:
   ```python
   for req in target_solver.parse(template.oracle_text):
       if not req.is_optional:
           if not target_solver.has_legal_target(game, player_idx, req, exclude=card):
               return False
   ```
2. `engine/cast_manager.py::can_evoke` (line 230, 351, 811) — same
   loop on the spell's primary target requirement, since evoke cost
   doesn't change targeting.
3. `engine/oracle_resolver.py::_resolve_*` — graveyard-target spells
   call `enumerate_legal_targets` instead of re-implementing the type
   filter (Goryo's Vengeance, Unburial Rites, Persist).
4. `engine/stack.py::resolve` — fizzle-on-illegal-target uses
   `has_legal_target` to re-validate at resolution (CR 608.2b).
5. `ai/ev_evaluator.py::score_spell` — use `enumerate_legal_targets`
   for "is there a high-value target available" scoring without
   substring parsing in AI code.

### What the solver does NOT do

- **Mode selection.** Modal spells (Wear // Tear, Charms, Pillar of
  the Paruns) — caller picks the mode and asks the solver about that
  mode's requirements. Modal-target dispatch belongs in the AI's
  decision layer, not the solver.
- **Splice / cascade / cycling-trigger targeting.** These are
  separate cast events; each calls `parse(other_spell.oracle)` for
  the spell being spliced/cascaded into.
- **Cost reduction or alternative cost selection.** Target legality
  is independent of cost; `can_cast` still owns mana validation.

## Sister bugs this fix closes

Spot-checked in 2026-05-02 audit, all confirmed castable on empty
boards today (sample, not exhaustive):

- Wear // Tear, Wear (`target artifact`)
- Disenchant, Nature's Claim, Altar's Light (`target artifact or enchantment`)
- Smelt, Shatter, Ancient Grudge (`target artifact`)
- Vindicate, Beast Within, Assassin's Trophy (`target permanent`)
- Maelstrom Pulse, Anguished Unmaking (`target nonland permanent`)
- Galvanic Discharge with no battlefield target chosen (`target creature or planeswalker`)
- Pithing Needle (technically activated, not cast — separate path)

DB regex sweep totals (instant + sorcery only):
- 119 target-permanent spells
- 127 target-artifact/enchantment spells
- 35 target-instant-or-sorcery (counterspells — stack-target, validated by `stack.is_empty` check, currently OK)
- ~250 cards in the union, before counting activated abilities and triggered abilities

## Class-of-bug verification

The fix is oracle-driven. Adding a new card to a deck list with any
new target phrasing automatically gets validated — no engine
changes required. This satisfies the abstraction contract: the
new module contains zero hardcoded card names; all behavior derives
from `CardTemplate.oracle_text` and `CardTemplate.card_types` /
`supertypes`.

## Implementation plan

| Phase | Tests | Code | Estimated effort |
|-------|-------|------|------------------|
| 1. Solver module + parser | `tests/test_target_solver_parser.py` (~30 cases covering each pattern) | `engine/target_solver.py` parse() + dataclass | 60-90 min |
| 2. Solver legal-target queries | `tests/test_target_solver_legality.py` (~40 cases against fixture games) | `engine/target_solver.py` has_legal_target / enumerate | 60-90 min |
| 3. Migrate cast_manager (5 sites → 1) | `tests/test_cast_*.py` (existing 200+ pass unchanged) | `engine/cast_manager.py` | 45-60 min |
| 4. Migrate oracle_resolver graveyard handlers | `tests/test_grafdiggers_cage.py` etc. (existing pass) | `engine/oracle_resolver.py` | 30 min |
| 5. Migrate stack.py fizzle check | new test `tests/test_stack_fizzle_via_solver.py` | `engine/stack.py` | 30 min |
| 6. Migrate ai/ev_evaluator scoring helpers | `tests/test_ev_*` (existing pass) | `ai/ev_evaluator.py` | 45 min |
| 7. Empirical validation | `--matrix -n 50 --save` + dashboard refresh | n/a | 30 min sim, ~15 min audit |
| **Total** | **~70 new + 250+ regression** | **+400 net lines** | **5-6 hours of focused work** |

## Empirical hypothesis

The current matrix has Affinity at 87.8% overall WR (verified
2026-05-02 against current main). My read: the wasted-cast bug
suppresses removal-deck WR globally — Boros, Azorius, Dimir all
cast removal into empty boards in *non-Affinity* matchups, hurting
their overall WR. This indirectly inflates Affinity's relative WR
because the field has a globally weakened threat-answering capacity.

Predicted impact of this refactor:
- Removal-heavy decks (Boros, Azorius, Dimir, Domain Zoo) overall WR
  up by 2-4pp each
- Affinity overall WR down by 2-4pp (relative correction, not direct)
- Combo decks (Storm, Living End, Amulet Titan) approximately unchanged
- Goryo's WR up modestly (cast-time fix already merged; this just
  consolidates the code)

If Affinity moves from 87.8% to ~80-83%, this refactor is the
biggest contributor since the EV correctness overhaul. If it
doesn't move, the **real Affinity bug is a positive-overscoring
issue** (Construct token P/T calculation, Mox Opal metalcraft
gating, Affinity discount stacking) — separate next-session work.

The refactor is justified independent of Affinity's specific
movement, because:
1. CR 601.2c compliance is correctness, not optimization
2. The 5 scattered code paths are a maintenance burden any future
   targeting fix has to navigate
3. Class-applicable: closes 250+ cards in one shape

## Anti-goal

Do NOT build the solver as a "smarter version of `'target X' in
oracle_l`". The current scattered approach is fragile precisely
because substring parsing is treated as the API. The solver's
contract is: "given this oracle and this game state, here is the
list of legal targets at this moment, by zone." The substring
parsing is an implementation detail, not the interface.

## Anti-goal

Do NOT migrate every target-checking site at once. Phases 3-6
above are ordered so each phase compiles, tests pass, and the
existing scattered logic is removed only after the solver-driven
replacement is verified. Skip phase 6 (AI migration) if time runs
short — it's a refinement, not a correctness fix.

## Open questions for Pieter

1. **Branch naming convention.** I'd suggest
   `refactor/unified-target-solver` since it's a cross-cutting
   refactor, not a single fix. Confirm or override.

2. **Modal-target handling.** Should the solver expose a
   `parse_modal(oracle) -> Dict[mode_index, List[TargetRequirement]]`
   API, or is that out of scope for this refactor (defer to AI layer)?

3. **Activated/triggered abilities.** Same target patterns appear in
   activated abilities (Liliana of the Veil "discard target card")
   and triggered abilities (Ravenous Chupacabra ETB). Should the
   solver be wired into `engine/permanent_effects.py` as well, or is
   that a follow-up?

4. **Replay format.** Should fizzles caused by the new
   no-legal-target check appear in `replays/*.html` as a distinct
   row (analogous to "BLOCK-EMERGENCY")? Useful for debugging.

## Linked work

- 2026-05-02 cast-time graveyard-target fix: PR #220 (merged)
  — basis for the graveyard zone branch of the solver
- 2026-05-02 mulligan typed combo paths fix: PR #218 (merged)
  — orthogonal but same architectural philosophy (typed-roles
    over flat-set predicates)
