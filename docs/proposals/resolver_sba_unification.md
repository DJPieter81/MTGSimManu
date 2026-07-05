---
title: Resolver/SBA unification — port rules trapped in dead code, delete the dead machinery
status: active
priority: secondary
session: 2026-07-05
supersedes: []
superseded_by: []
depends_on: [docs/diagnostics/2026-07-05_calibration_probe_findings.md (PR #441), PR #443]
tags: [engine, dead-code, sba, stack, resolution, comprehensive-rules, cr-608, cr-704]
summary: |
  Two engine subsystems — Stack's resolver/priority machinery
  (engine/stack.py) and SBAManager's check_and_perform loop
  (engine/sba_manager.py) — are full parallel legacy implementations
  with zero live callers, and some Comprehensive Rules exist ONLY in
  the dead copy. This proposal inventories the dead code, ports the
  trapped rules to the live path as single shared implementations
  (CR 608.2b target-fizzle → ResolutionManager.resolve_stack;
  CR 704.5i deathtouch, CR 704.5c poison, CR 704.5h indestructible →
  SBAManager statics called from game_state.check_state_based_actions),
  then deletes the dead machinery (Stack resolver/priority methods,
  engine/log_export.py). Named end-state: one SBA fixpoint
  implementation shared by both call sites; Stack as pure container.
---

# Resolver/SBA unification — port rules trapped in dead code, delete the dead machinery

Implements the "named proposal" ask from
`docs/diagnostics/2026-07-05_calibration_probe_findings.md` (PR #441).
All line numbers cite `main@714070e` (pre-change) unless marked
*post-change*.

## 0. Anchors

- Diagnostic source: `docs/diagnostics/2026-07-05_calibration_probe_findings.md`
  (lives on PR #441's branch; not edited here).
- Dead resolver: `engine/stack.py:118` (`Stack.resolve_top`),
  `engine/stack.py:139` (`Stack._resolve_spell`),
  `engine/stack.py:197` (`Stack._resolve_ability`). Zero callers
  anywhere including tests (the `_resolve_spell` hits in
  `tests/test_spell_reanimate.py:45` and `tests/test_spell_bounce.py:39`
  are local helper functions, not callers).
- Dead priority machinery: `engine/stack.py:84-116, 204-213` —
  `peek`, `size`, `pass_priority`, `both_passed`, `reset_priority`,
  `priority_player` property+setter, `switch_priority`, `__len__`,
  `__str__`, internal state `_priority_player` / `_passed_priority`.
  The live priority system is `engine/priority_system.py` (its
  `pass_priority` / `both_passed` at `priority_system.py:60/90` are a
  separate, live implementation).
- Live resolver: `engine/spell_resolution.py:42-129`
  (`ResolutionManager.resolve_stack`) — before this PR it NEVER
  re-checked targets at resolution.
- CR 608.2b trapped ONLY in dead code: `engine/stack.py:144-156`
  (re-check `item.targets` against battlefield; all-invalid → fizzle
  to graveyard with no effect).
- Dead SBA loop: `engine/sba_manager.py:37-54`
  (`SBAManager.check_and_perform_loop`). The instance is constructed at
  `engine/game_state.py:111` (`self.sba_mgr = SBAManager(self.zone_mgr)`)
  and referenced nowhere else. Sole test caller:
  `tests/test_sba_uses_max_iterations_constant.py` (pins the loop via
  `inspect.getsource` — the loop and `SBA_MAX_ITERATIONS` stay).
- Live SBA path: inline in
  `engine/game_state.py:555-627` (`check_state_based_actions`), whose
  docstring FALSELY claimed "Delegates to SBAManager" (fixed here).
- CR 704.5i trapped ONLY in dead code: `engine/sba_manager.py:140-153`.
  `engine/damage.py:180-195` marks `_deathtouch_damage` with a comment
  naming `engine/sba_manager.py:143` as the consumer — a dead consumer.
  Worse, the marker itself could never be written: `CardInstance` had
  neither a `has_deathtouch` attribute (read at `engine/damage.py:180`)
  nor a `_deathtouch_damage` field (gated by `hasattr` at
  `engine/damage.py:181`).
- CR 704.5c trapped ONLY in dead code: `engine/sba_manager.py:91-100`
  (poison >= 10 loses). `PlayerState.poison_counters` exists
  (`engine/player_state.py:41`) but was never checked live.
- CR 704.5h discrete check ONLY in dead code:
  `engine/sba_manager.py:125-138` (lethal damage + INDESTRUCTIBLE
  exemption). The live path folded lethality into
  `CardInstance.is_dead` (`engine/cards.py:519-522`) with NO
  indestructible check — probe test confirmed indestructible creatures
  wrongly died to marked lethal damage (RED before fix).
- Rule ONLY in the live path: creature death routes through
  `game_state._creature_dies` → `PermanentEffects._creature_dies`
  (`engine/permanent_effects.py:178`), preserving Undying/Persist
  (`engine/game_state.py:582-593`). SBAManager's dead copies bypassed
  it via `zone_mgr.move_card`. Every shared implementation in this PR
  routes through `_creature_dies`.
- Third dead-code data point: `engine/log_export.py` (182 lines, zero
  importers anywhere including tests). Deleted.
- PR #443 overlap (`origin/claude/e4-tokens-cease`): introduces the
  shared-static pattern (`SBAManager.perform_token_cleanup(game)`
  called from the live path). This PR follows the same pattern with
  disjoint hunks: statics appended at the END of SBAManager; live-path
  insertions after the life-total block (poison) and after the
  0-toughness block (deathtouch) — never between the
  planeswalker-loyalty and legend-rule blocks where #443 inserts.

## 1. Dead subsystem #1 — Stack resolver + priority machinery

`engine/stack.py` (213 lines pre-change): live code uses `Stack` only
as a container — `items`, `is_empty`, `top`, `push`, `pop` are live;
`StackItem` / `StackItemType` are widely imported (~20 test files).
Everything else (resolver, priority passing, dunder sugar) had zero
callers. Resolution has lived in `ResolutionManager.resolve_stack`
since Commit 5a; priority lives in `engine/priority_system.py`. The
dead copy silently diverged from the live one — and was the only place
CR 608.2b existed.

## 2. Dead subsystem #2 — SBAManager loop

`engine/sba_manager.py` (191 lines pre-change): constructed at
`game_state.py:111`, then never called by any live code. The live SBA
pass is a hand-rolled inline sequence in
`game_state.check_state_based_actions` that implements 704.5a
(life), 704.5g/h (via `is_dead` / zero toughness), 704.5p (loyalty)
and 704.5j (legend rule) — but NOT 704.5c (poison), NOT 704.5i
(deathtouch), and 704.5h without the indestructible exemption.

## 3. Dead subsystem #3 — log_export

`engine/log_export.py`: 182 lines, zero importers anywhere including
tests. No rules trapped inside; pure deletion.

## 4. Rules-only-in-dead-copy inventory

| CR | Rule | Dead location | Live effect of the gap |
|----|------|--------------|------------------------|
| 608.2b | Spell fizzles when ALL targets illegal at resolution | `stack.py:144-156` | Spells with dead targets still resolved; oracle-resolver handlers (e.g. the nonland-bounce branch at `engine/oracle_resolver.py:279-295`) re-picked a NEW target, resolving a spell that should fizzle |
| 704.5i | Deathtouch damage destroys | `sba_manager.py:140-153` | A deathtouch source dealing non-lethal non-combat damage NEVER destroyed the creature (combat has a separate inline approximation at `engine/combat_manager.py:226-281`) |
| 704.5c | Poison >= 10 loses | `sba_manager.py:91-100` | Poison counters never checked live |
| 704.5h | Indestructible exempts lethal-damage destruction | `sba_manager.py:125-138` | `is_dead` ignored INDESTRUCTIBLE; indestructible creatures died to marked lethal damage |

## 5. What THIS PR ports / deletes

Ported (failing test first, fix in the same commit, per rule):

1. **CR 608.2b** → top of the spell branch of
   `ResolutionManager.resolve_stack`. `StackItem` gains a
   `target_zones` cast-time zone snapshot (populated at the single
   spell-item creation site, `engine/cast_manager.py:1138` area) so
   validity is judged against the zone the target was in when chosen —
   battlefield for removal, stack for counterspells, graveyard for
   reanimation. Player-target markers (negative ids) are always valid;
   entries without a snapshot cannot be proven illegal and count as
   valid. Fizzle only when ALL targets are invalid.
   Test: `tests/test_spell_fizzles_when_all_targets_invalid.py`.
2. **CR 704.5i** → `SBAManager.perform_deathtouch_check(game)`
   @staticmethod; exactly one implementation, called from BOTH
   `game_state.check_state_based_actions` (right after the
   0-toughness block) and `SBAManager._check_and_perform_once`
   (replacing the inline copy). Routes death through
   `game._creature_dies` (Undying/Persist preserved), respects
   INDESTRUCTIBLE. Enabling plumbing: `CardInstance` gains the
   `_deathtouch_damage` field and `has_deathtouch` property
   (keyword-derived) that `engine/damage.py` always assumed;
   `cleanup_damage()` clears the marker with marked damage.
   Test: `tests/test_deathtouch_nonlethal_damage_destroys.py`.
3. **CR 704.5h** → INDESTRUCTIBLE gate at the single owning site,
   `CardInstance.is_dead` (`engine/cards.py`): lethal marked damage no
   longer kills an indestructible creature; toughness <= 0 (704.5g)
   still does. Probe test was RED (real bug, fixed here).
   Test: `tests/test_indestructible_survives_lethal_damage.py`.
4. **CR 704.5c** → `SBAManager.perform_poison_check(game)`
   @staticmethod, same two-caller pattern; threshold is the existing
   `POISON_COUNTER_LETHAL` rules constant (`engine/constants.py:24`).
   Test: `tests/test_poison_ten_counters_loses.py`.

Deleted:

- `engine/stack.py`: `resolve_top`, `_resolve_spell`,
  `_resolve_ability`, `peek`, `size`, `pass_priority`, `both_passed`,
  `reset_priority`, `priority_player` property/setter,
  `switch_priority`, `__len__`, `__str__`, `_priority_player`,
  `_passed_priority`. Kept: `items`, `is_empty`, `top`, `push`, `pop`,
  `StackItem`, `StackItemType`. Class docstring now states Stack is a
  pure container and resolution (incl. CR 608.2b) lives in
  `ResolutionManager`.
- `engine/log_export.py` (entire file).
- The lying "Delegates to SBAManager" docstring on
  `game_state.check_state_based_actions`.

## 6. Named end-state (follow-up, NOT this PR)

**Single SBA fixpoint implementation shared by both call sites; Stack
as pure container.**

- Every 704.5x rule becomes a `SBAManager.perform_*` static (this PR:
  deathtouch + poison; #443: token cleanup; follow-up: life, draw-from-
  empty, zero-toughness, lethal-damage, loyalty, legend rule), and
  `check_state_based_actions` collapses into the CR 704.3 fixpoint loop
  over those statics — one implementation, two entry points gone.
- Removal of `check_and_perform_loop` / `_check_and_perform_once` is
  **deferred** because PR #443 revives parts of
  `_check_and_perform_once` (the 704.5f block) and
  `tests/test_sba_uses_max_iterations_constant.py` pins the loop source
  via `inspect.getsource`. Retiring them is only safe after #443 lands
  and the fixpoint loop above exists.
- Ability-item fizzle (CR 608.2b also covers abilities) is a follow-up:
  trigger items are created at `engine/triggers.py:212` without a
  cast-time zone snapshot; extending `target_zones` there is mechanical
  once the spell-side pattern has soaked.
