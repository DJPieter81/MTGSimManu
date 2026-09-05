---
title: Rules-foundation remediation — sweep tracker
status: active
priority: primary
session: rules-foundation-phase0
depends_on:
  - docs/proposals/resolver_sba_unification.md
  - docs/history/plans/TRANSFORM_FIX_PLAN.md
  - tools/check_abstraction.py
  - tools/check_magic_numbers.py
  - tools/check_zone_mutation.py
tags:
  - engine
  - ai
  - dead-code
  - rules-foundation
  - patch-shaped-rules
  - sba
  - zone-transfer
  - cda
  - combat
summary: >
  A parallel Bo3 audit across 6 tier-1 decks found 6 concrete bugs, one of
  which (`ai/ev_player.py:3196-3198`, a 0-power creature banned from chump
  blocking) was diagnosed as a specimen of a class, not an isolated bug.
  Six research passes (3 architecture maps, 2 patch censuses, 1 dead-code
  verdict, 1 ratchet-blind-spot sweep) confirm the class: the engine has no
  single owner for "what is true about this object right now" and the AI has
  no single owner for "what does spending this cost me" — every reader that
  needed an answer improvised one. This doc tracks the fix program (approved
  plan: Phase 0 foundational primitives → Phase 1 missing rules mechanics →
  Phase 2 AI decision kernel → Phase 3 tracked sweep of the long tail) so the
  ~230-item finding set is auditable and resumable across sessions, per
  CLAUDE.md's own "read docs with status: active, priority: primary at
  session start" convention.
---

# Rules-foundation remediation — sweep tracker

## The 6 audited bugs (source of truth for what "done" means)

| # | Bug | Deck / matchup | Seed | Root cause | Phase item |
|---|---|---|---|---|---|
| 1 | Cultivator Colossus is 0/0, dies on ETB | Eldrazi Tron vs Amulet Titan | `--bo3 "Eldrazi Tron" "Amulet Titan" -s 55505` | `detect_power_scaling` has no "lands you control" CDA bucket | 1d |
| 2 | Metallic Rebuke never counters | Pinnacle Affinity vs Izzet Prowess | `--bo3 "Pinnacle Affinity" "Izzet Prowess" -s 55506` | No "unless controller pays" mechanism exists anywhere | 1a |
| 3 | Fable → Kiki-Jiki self-destructs on transform | Jeskai Blink vs Eldrazi Tron | `--bo3 "Jeskai Blink" "Eldrazi Tron" -s 55502` | `player_state.creatures/planeswalkers` hardcode "transformed ⇒ became a PW"; back-face types never captured at DB load | 0c |
| 4 | Two lethal attackers, only one gets blocked | Eldrazi Tron vs Amulet Titan | `--bo3 "Eldrazi Tron" "Amulet Titan" -s 55505` | `decide_blockers` is a greedy per-attacker loop, no joint optimization | 2b |
| 5 | Won't chump-block a Dashed Ragavan | Boros Energy vs Dimir Midrange | `--bo3 "Boros Energy" "Dimir Midrange" -s 55501` | Categorical veto (`if b_pow == 0: continue`) instead of scoring | 2a/2b |
| 6 | Won't chump-block with a dead Ornithopter | Affinity vs 4c Omnath | `--bo3 "Affinity" "4c Omnath" -s 55504` | Same class as #5 | 2a/2b |

Bug #5's line — `ai/ev_player.py:3196-3198`, `if b_pow == 0: continue  #
0-power non-kill = pure waste` — is the specimen that triggered this whole
program: `ai/clock.py:271-272` already prices a 0-power blocker correctly
(`toughness * PURE_BLOCKER_TOUGHNESS_VALUE`); the veto just never lets that
call happen. Proof the gap is plumbing, not modelling.

## Dead-infrastructure verdicts (no dead modules among anything built to solve this)

| Module | Verdict | Status |
|---|---|---|
| `engine/continuous_effects.py` (`ContinuousEffectsManager`) | **Activate** | Done (0b) |
| `engine/sba_manager.py` (`SBAManager.check_and_perform_loop`) | **Finish migration** (live proposal, 3/9 rules done: poison, deathtouch, token-cleanup) | Done (0d) |
| `engine/event_system.py` (`EventBus`) | **Delete** — registered-closure API mismatches `oracle_resolver.py`'s imperative-function style everywhere | Done — deleted commit `dc3bf4d`; zone_manager, game_state, triggers.py all cleaned up |
| `engine/priority_system.py` | **Delete** — `resolve_priority_round` (sole caller of `pass_priority`/`both_passed`) confirmed to have zero callers anywhere | Done — deleted commit `dc3bf4d`; game_state and game_runner cleaned up |
| `engine/oracle_parser.py:260 is_living_end_cascader` | Delete, 0 callers | Done — deleted commit `dc3bf4d` |
| `engine/oracle_parser.py:350 parse_planeswalker_abilities` | Delete, superseded by `player_state.py:272` | Done — deleted commit `dc3bf4d` |
| `engine/stack.py` legacy resolver/priority methods | Already deleted (pre-existing work, `docs/proposals/resolver_sba_unification.md`) | Done, verified |
| `engine/log_export.py` | Already deleted (pre-existing work) | Done, verified |

## Phase 0 — foundational primitives

### 0a. Zone-transfer funnel enforcement — IN PROGRESS

Real architecture found (not what the plan originally assumed): `zone_manager.move_card`
already fires events via `EventBus.fire_event` (dead registry — always `[]`), but the
actually-live trigger dispatch is `oracle_resolver.py`'s imperative functions
(`resolve_dies_trigger`, `resolve_attack_trigger`, `resolve_spell_cast_trigger`,
`_handle_permanent_etb`), called via direct explicit imports from a handful of sites —
NOT through `move_card` at all for creature death specifically (`_creature_dies` raw-mutates
then calls `resolve_dies_trigger` directly). `move_card` also cannot handle the "stack" zone
today (`_get_zone_list` only maps library/hand/battlefield/graveyard/exile) — a real
architectural gap noted for future work, not solved in this pass.

Completed:
- [x] **`EFFECT_REGISTRY` handler-presence gates scoped to timing, not name** (commit `3b9110f`).
  Found the exact same bug shape at 3 sites: `PermanentEffects._creature_dies` (gated dies-trigger
  fallback on "any handler for this name", so the repo's one real DIES registration — Haywire Mite,
  gain 2 life — was dead code since nothing ever called `EFFECT_REGISTRY.execute(..., DIES, ...)`),
  `ResolutionManager._handle_permanent_etb` (same shape for the main ETB dispatch), `resolve_stack`'s
  X-cost charge-counter gate. `zone_transfer.py::_fire_etb_triggers` already had the correct pattern
  (`h.timing == EffectTiming.ETB`) — all three fixes converge on `EFFECT_REGISTRY.has_handler(name, timing)`.
  Tests: `tests/test_dies_trigger_registry_gate.py`, `tests/test_etb_handler_gate_scoped_by_timing.py`.
- [x] **Countered spells respect flashback/rebound/spell-copy zone replacement** (commit `5a9d41c`).
  The generic counterspell branch raw-mutated `countered_card.zone = "graveyard"` regardless of how the
  spell was cast — normal resolution already handled flashback (CR 702.33a → exile)/rebound (CR 702.86)/
  spell-copy (CR 707.10a) correctly. Extracted `ResolutionManager._move_resolved_spell_off_stack` as the
  single owner, called from both normal resolution and the new `_move_countered_stack_item`.
  Tests: `tests/test_countered_spell_zone_replacement_effects.py`.
- [x] **Zone-mutation ratchet** (commit `c3a4060`). `tools/check_zone_mutation.py` +
  `tools/zone_mutation_baseline.json` (seeded at 103 raw `.zone =` sites outside the funnel) +
  `tests/test_zone_mutation_ratchet.py`, wired into CI. Backstop against new bypasses while the
  migration below proceeds incrementally — does not require the full migration to land first.

Remaining (tracked here, not yet started):
- [x] **Migrate `_creature_dies` through zone_manager** — `PermanentEffects._creature_dies` now calls
  `game.zone_mgr.move_card(game, creature, "battlefield", "graveyard")` for the normal death path
  instead of raw `.zone= "graveyard"` + `graveyard.append()`. The pre-removal from battlefield (line
  226-228 of the old code) is also removed since `move_card` owns the list mutation. Undying/persist
  replacement effects retain their direct-mutation paths (checked BEFORE any `move_card` call so
  counter values are still readable; they redirect back to battlefield, making them structurally
  different from a graveyard transition). Equipment tags are read BEFORE `move_card` clears
  `instance_tags` via `_cleanup_leaving_battlefield`. Zone-mutation baseline updated 102 → 101 in
  the same commit (`python tools/check_zone_mutation.py --update`). Tests:
  `tests/test_creature_death_zone_funnel.py` (3 tests — funnel-routing mock assertion RED pre-fix,
  graveyard-landing regression, died-this-turn counter).
- [x] **Investigated, deprioritized**: discard-path migration (madness/Containment Construct-class
  triggers). Finding: `_force_discard` (`game_state.py:537`) *already* routes through
  `zone_mgr.move_card` — this tranche is further along than the plan assumed. What's actually missing
  is a `resolve_discard_trigger` imperative function (analogous to `resolve_dies_trigger`) for
  discard-watcher effects, since `move_card`'s own event firing (the dead `EventBus`) doesn't fire
  anything for hand→graveyard transitions at all. **Verified zero blast radius**: 0 madness cards and
  0 discard-watcher-shaped cards (Archfiend of Ifnir/Containment Construct/Liliana's Caress class) in
  any of the 16 registered decks' 75s (checked programmatically against `decks/modern_meta.py` +
  `ModernAtomic_part*.json`). Per CLAUDE.md's class-size rule ("how many cards could legitimately hit
  this path? If fewer than 10, you are patching"), building this now would be speculative work with
  no observable game impact. Re-check if/when a madness or discard-watcher card enters the pool.
- [x] **Investigated, deprioritized**: `triggers.py`'s Annihilator-sacrifice raw mutation (fires no
  dies/sacrifice/LTB triggers, picks the opponent's sacrifice targets by lowest CMC instead of asking
  them). Real bug, confirmed. **Verified zero blast radius**: 0 cards with `Keyword.ANNIHILATOR` in
  any of the 16 registered decks' 75s. Same class-size reasoning as above — deprioritized, not fixed.
- [ ] Extend `zone_manager._get_zone_list` / `move_card` to understand the "stack" zone, so future
  stack→zone transitions (the ~11 remaining `spell_resolution.py` sites) can route through the real
  funnel instead of file-local helpers. Architecturally larger than the counter-fizzle fix already
  landed; scope this as its own slice.
- [x] Delete `engine/event_system.py` — done commit `dc3bf4d`. Zero registrations confirmed by
  grep; fire_event always returned (event, []) — a confirmed no-op at all 4 call sites in
  zone_manager.py. Cleaned up zone_manager, game_state, and the queue_event_trigger bridge in
  triggers.py in the same commit.
- [ ] Per-file baseline reduction in `tools/zone_mutation_baseline.json` as each tranche above lands
  (`python tools/check_zone_mutation.py --update` after migrating, in the same commit as the migration).

### 0b. Activate continuous_effects.py — DONE (manager correctness + first real pilot registration)

Found before writing any migration code that `recalculate()`'s own docstring ("1. Clears all
calculated modifications on all permanents") didn't match its body — it only removed effects
whose SOURCE had left, then applied every remaining effect's `apply` closure via `+=` with no
reset. Since the manager's documented usage is "called at key points: after ETB, after spells
resolve, before combat" (i.e. repeatedly), this would have double/triple/N-counted every
registered effect the moment it was wired in.

Fixed by:
- Adding dedicated `cem_power_mod`/`cem_toughness_mod`/`cem_keywords` accumulator fields on
  `CardInstance` (engine/cards.py), kept **separate** from `temp_power_mod`/`temp_toughness_mod`/
  `temp_keywords` — those are a shared dumping ground for one-shot pump spells, Dash, etc.
  throughout `card_effects.py`, cleared unconditionally at end-of-turn `cleanup_damage()`.
  Reusing them for continuous effects would either wipe a lord's bonus early (end of turn) or
  double-count it (`recalculate()` re-applying on top of a value cleanup never reset). `power`/
  `toughness`/`keywords` properties now sum both accumulator families.
- `recalculate()` clears the `cem_*` fields at the start of every call — genuinely idempotent now,
  verified by `tests/test_continuous_effects_manager_recalculate.py` (register once, recalculate
  twice → same value not double; source leaves battlefield, recalculate → bonus retracts).
- **Found and fixed a third pre-existing, never-exercised bug** while wiring the factories:
  `create_lord_effect`/`create_pump_spell_effect`'s `description=f"...{_kw.name}"` referenced `_kw`
  (a closure-local parameter name of the nested `apply_keyword` function) from the OUTER scope
  where only `kw` (the loop variable) is defined — a `NameError` waiting to fire the moment any
  caller registered a keyword grant. Never caught because the module had zero callers until now.
  Fixed to `kw.name`. Verified: temporarily reverted the clear-step fix and confirmed all 3 manager
  tests go genuinely red (not just fail-to-import) before restoring it — real red→green, not just
  green-by-construction.

**Pilot activation — Scion of Draco** (chosen because it's a documented audit-census finding: the
`creature.keywords.add(...)` no-op at `card_effects.py:2908-2912`). Investigating the REAL oracle
text before fixing revealed the existing handler modeled the wrong mechanic entirely: "Each
creature you control **has** vigilance if it's white, hexproof if it's blue, lifelink if it's
black, first strike if it's red, and trample if it's green" is a continuous, per-creature-color
static ability (present tense) — not a one-time ETB event gated on an unrelated Leyline-of-the-
Guildpact flag that (even if the mutation had worked) would have granted all five keywords to
every creature regardless of color. A `temp_keywords.add()` one-line fix would have been WRONG,
not just incomplete.

Also found: `CardTemplate` only had `color_identity` (MTGJSON `colorIdentity` — format-legality
scope, can differ from a card's own printed color), no field for a permanent's actual color at
all. Added `CardTemplate.colors` (MTGJSON `colors`), populated at DB load mirroring the existing
`color_identity` pattern — a small, generically-useful primitive (any future "if it's [color]"
card needs this, not just Scion).

Implementation: `scion_of_draco_etb` now registers 5 `create_lord_effect`-shaped continuous
effects (one per color→keyword mapping), each `affected_fn` checking the specific creature's own
`template.colors` and `controller`. Retraction is automatic via the manager's existing stale-
source cleanup — no separate unregister call needed. `game.continuous_effects.recalculate(game)`
called once inline in the handler (proves the wiring end-to-end for this card; broader "call
recalculate() at every game-loop checkpoint" wiring is Phase 3 scope, not needed for existing
registered effects to work correctly when re-registered on each relevant ETB).

Tests: `tests/test_continuous_effects_manager_recalculate.py` (3 — manager correctness, generic
fixtures), `tests/test_scion_of_draco_color_conditional_keywords.py` (2 — color-specific grant,
not-all-five; retraction on leaving battlefield).

**Verified live**: `python run_meta.py --bo3 "Domain Zoo" "4c Omnath" -s 60001` — confirms the
correct log message fires on cast, and that removal (Leyline Binding, Solitude exiling Scion)
interacts cleanly with the registered effects with no crash and correct retraction.

Deferred to Phase 3 (explicitly out of scope for this pilot): migrating the OTHER lord/pump/
equipment mutations in `card_effects.py` to this system (the plan's original broader scope) — the
manager is now proven correct and has one real registration; converting the remaining dozens of
`temp_power_mod +=`-style call sites is real, separate migration work per card/mechanic, tracked
here rather than rushed.

### 0c. Effective-characteristics accessor + DFC back-face capture — DONE (fixes audited bug #3, both halves)

Root cause confirmed via `docs/history/plans/TRANSFORM_FIX_PLAN.md`: `_transform_permanent` was
built specifically (and only) for planeswalker-backed transforms (Ajani, Ral — that plan's
original scope), archived as done, then reused for Fable/Kiki-Jiki (a creature-backed transform)
without generalizing. Confirmed the fix is adding the missing generalization, not re-deriving
already-attempted work.

Verified via real DB inspection (Fable of the Mirror-Breaker // Reflection of Kiki-Jiki) that
`card_entries[1]` is genuinely the back FACE (not a printing variant) — MTGJSON's Atomic format
uses `side: 'a'`/`'b'` per-face entries. Kiki-Jiki's back face: `types: ['Enchantment','Creature']`,
subtypes `['Goblin','Shaman']`, P/T 2/2 — confirming it's an Enchantment Creature, not a bare
Creature (relevant: it later legitimately dies to an artifact/enchantment-destruction removal
spell in the replay below, which is correct now that its type is captured at all).

Implementation:
- `CardTemplate` gained `back_face_types`/`back_face_subtypes`/`back_face_power`/
  `back_face_toughness`/`back_face_keywords`, populated at DB load for **every** multi-face card
  (not just planeswalker-backed — `card_database.py`'s previous gate `if 'Planeswalker' in
  card_entries[1].get('types', [])` removed).
- `CardInstance` gained `effective_card_types`/`effective_subtypes`/`effective_is_creature`/
  `effective_is_planeswalker` (front vs back face selected once, by `is_transformed`) and private
  `_effective_printed_power`/`_effective_printed_toughness`/`_effective_oracle_text` consumed by
  `_dynamic_base_power`/`_dynamic_base_toughness` (previously read `self.template.power`/
  `oracle_text` unconditionally — the front face — even when transformed).
- `is_transformed` promoted from an undeclared `setattr`-only attribute to a real dataclass field.
- `PlayerState.creatures`/`.planeswalkers` now consult `effective_is_creature`/
  `effective_is_planeswalker` instead of hardcoding "transformed ⇒ planeswalker".
- **Second bug found and fixed in the same pass** (not in the original plan item, found via a live
  test-failure trace): `ResolutionManager._handle_permanent_etb`'s `EFFECT_REGISTRY.execute(name,
  ETB, ...)` and the oracle-fallback both key on the front face's literal name/oracle text, which
  is identical for both DFC faces — so Fable's own Chapter I ETB handler ("create a Goblin token")
  unconditionally re-fired every time Fable's Chapter III exile-and-return-transformed triggered,
  since that return is a genuine re-entry per this engine's implementation. Fixed by skipping the
  whole front-face-keyed ETB dispatch block when `card.is_transformed` — correct for the current
  pool (no card has a back-face-specific ETB effect that would need to fire there); documented as
  needing its own dispatch mechanism if that ever changes. Also fixed `is_creature`/`is_artifact`
  (Torpor Orb / Doorkeeper Thrull suppression checks) in the same function to use
  `effective_card_types` — same class, found by inspection while fixing the ETB-refire bug.

Tests: `tests/test_transform_creature_back_face.py` (3 tests — creature-back-face classification,
no-ETB-refire, planeswalker-back-face regression guard).

**Replayed the exact audited seed**: `python run_meta.py --bo3 "Jeskai Blink" "Eldrazi Tron" -s
55502`. Confirmed: T9 Fable transforms into Kiki-Jiki, no spurious Goblin token, no 704.5p
zero-loyalty death. Kiki-Jiki survives on the battlefield as a creature until later legitimately
destroyed by Eldrazi Tron's Kozilek's Command (a real artifact/enchantment-destruction mode X=3) —
correct, since Kiki-Jiki really is an Enchantment Creature. Bug #3 confirmed fixed end-to-end.

### 0d. Finish SBA unification — fixpoint loop DONE; static consolidation deferred to Phase 3

`docs/proposals/resolver_sba_unification.md` (status: active) already documents the fuller task as
its own named follow-up (§6): every 704.5x rule becomes a `SBAManager.perform_*` static (3/9 done:
poison, deathtouch, token-cleanup), `check_state_based_actions` collapses into the CR 704.3 fixpoint
loop. **Landed the fixpoint loop** — the highest-value, cleanly-testable piece — and investigated
704.5b before implementing it (found zero blast radius, same pattern as the discard/annihilator
findings in 0a).

**Fixpoint loop**: `GameState.check_state_based_actions` ran its full rule sequence exactly ONCE
per call — no CR 704.3 repeat-until-stable. This became a REAL (not just latent) gap the moment 0b
activated `ContinuousEffectsManager`: a legend-rule sacrifice (checked LAST in the sequence) can
retract a continuous toughness/keyword grant a DIFFERENT creature depended on — but the toughness/
lethal-damage checks (rules g/h) already ran EARLIER in that same pass, so the cascade was only
caught on the NEXT external call (13 call sites throughout the engine — not guaranteed to fire
before other same-step logic reads the now-stale state). Fixed by extracting the existing single-
pass body into `_check_sba_once()` and wrapping `check_state_based_actions()` around it in a loop
bounded by `SBA_MAX_ITERATIONS` (already imported, previously unused — the exact constant/pattern
the dead `SBAManager.check_and_perform_loop` already used). Also added
`self.continuous_effects.recalculate(self)` at the top of each pass, so a prior pass's retraction
is visible before the P/T-dependent checks run.

Tests: `tests/test_sba_fixpoint_loop.py` — a REAL cascading-SBA integration test (two same-named
Legendary creatures, one granting a continuous +0/+2 toughness bonus to a third creature via
`ContinuousEffectsManager`; sacrificing the older one via legend rule must retract the bonus and
kill the third creature in the SAME `check_state_based_actions()` call), plus a mocked loop-bound
test mirroring `tests/test_sba_uses_max_iterations_constant.py`'s existing style for the dead loop.
Full suite + all pre-existing SBA-pinning tests (indestructible, poison, deathtouch, the dead-loop
iteration-bound test) green; the dead loop itself untouched (still pinned, still dead, unaffected).

**704.5b (draw from empty library) — investigated, deprioritized.** Currently an inline instant-
loss in `draw_cards` rather than deferred to the next SBA window (the technically-correct CR 704.5b
behavior, which matters for Thassa's Oracle/Laboratory Maniac-class "win the game" interactions).
Checked the registered 16-deck pool programmatically for any "win the game" or library-empty-
interaction card: **zero hits**. Without such a card, the instant-vs-deferred distinction is
outcome-neutral — the player who'd draw from empty still loses either way, just via a different
CR-technical path. Per the same class-size reasoning as 0a's discard/annihilator findings,
deprioritized rather than implemented now; re-open if a mill/self-deck win-condition card enters
the pool. `_drew_from_empty` (the dead flag in `sba_manager.py`, never written anywhere) is left
as-is — it's part of the untouched dead loop, not a live-path concern.

**Deferred to Phase 3**: consolidating the remaining inline rules (704.5a life, 704.5g/h toughness/
lethal-damage, 704.5j legend rule, 704.5p planeswalker-loyalty) into shared `SBAManager` statics
matching the poison/deathtouch/token-cleanup pattern, and — only once every rule is shared —
retiring `SBAManager.check_and_perform_loop`/`_check_and_perform_once` (blocked today by
`tests/test_sba_uses_max_iterations_constant.py` pinning the dead loop via `inspect.getsource`;
retiring requires moving that pinned rule to the now-live `check_state_based_actions` fixpoint
loop in the same change). Real, separate migration work — the live path is already correct and
now has its own fixpoint; consolidating for code-sharing's sake alone isn't urgent.

### 0e. Make snapshot mandatory in ai/ — DONE

`creature_value`/`creature_threat_value` (`ai/ev_evaluator.py`) took `snap: EVSnapshot` behind an
optional-parameter default (`_DEFAULT_SNAP`, life=20/power=3 fiction). Every omitted call silently
scored against that fiction instead of the live board — confirmed live at
`ai/ev_player.py`'s emergency-block portfolio cap (`sacrificed_value += creature_value(best_chump)`,
called with `me.life`/`my_power_total`/`opp_power_total` all live in scope three lines above and
ignored).

Fix: `snap` is now a required positional parameter on `creature_value`/`creature_threat_value`;
`_DEFAULT_SNAP` is deleted (renamed callers that used it intentionally — `ai/sideboard_solver.py`,
`ai/permanent_threat.py`'s scale-consistency baseline — to the public `BASELINE_SNAPSHOT`, so the
context-free comparison points are named and visible, not a silent fallback). Every call site the
resulting `TypeError` sweep surfaced was fixed by threading the snapshot already in scope (the
`ev_player.py:3095` chump-block fix — the flagship bug — plus ~15 other production sites across
`ai/ev_player.py`, `ai/board_eval.py`, `ai/response.py`, `ai/discard_advisor.py`, `engine/card_effects.py`)
or, for `ai/bhi.py`'s `p_higher_threat_in_n_turns` (deliberately test-ergonomic — callable from unit
tests without a live game), given an optional `snap=None` parameter that falls back to
`BASELINE_SNAPSHOT` explicitly, with its one production caller (`ev_player.py:1414`) threading the
real snapshot already in scope. `choose_card_to_strip`/`score_card_for_opponent_strip`
(`ai/ev_evaluator.py`) also gained a required `snap` — both callers (`ai/discard_advisor.py`'s
opponent-forced-discard path, `_choose_for_caster`'s panic-mode picker) now build a caster-perspective
snapshot (`snapshot_from_game(game, 1 - victim_idx)`) instead of scoring blind.

**Cascading test-debt fixed in the same pass** (surfaced by the mandatory-snapshot `TypeError` sweep,
not new regressions): `tests/test_construct_no_double_credit.py`,
`tests/test_equipment_ceiling_threat.py`, `tests/test_thoughtseize_at_low_life_picks_imminent_attacker.py`
had bare `creature_threat_value(card)`/`score_card_for_opponent_strip(c)` calls on synthetic/context-free
fixtures — threaded `BASELINE_SNAPSHOT` (synthetic-fixture tests) or a real `snapshot_from_game(...)`
(the Thoughtseize test, matching the real discard path's caster-perspective construction) into each.

**Separate pre-existing CI debt fixed alongside** (PR #488, unrelated to 0e itself but blocking its
green suite): 13 test files referenced `Phlage, Titan of Fire's Fury`, which a fresh MTGJSON refresh
(commit `d02c543`) removed from the DB entirely following its Modern ban. Representative/incidental
uses (a red/white card filling a hand slot, a library-density filler) were swapped for
`Lightning Helix`; uses that depended on Phlage's *specific* mechanics were swapped for a real card
with the same mechanic shape rather than deleted: `Kroxa, Titan of Death's Hunger` for the
"Escape—" oracle-text protected-piece tests (`test_decide_blockers_protects_engines.py`) and the
threat-ranking test (`test_phelia_blink_picks_highest_threat_etb.py` — Kroxa's
`creature_threat_value` of 8.45 lands almost exactly where Phlage's ~8.4 did, preserving the
Solitude > mid > Omnath ordering the test pins), `Kroxa` again for the "unless...cost" substring-
collision trap in `test_parse_cost_reduction_strict.py` (same "unless it escaped" / "escape cost"
text shape as Phlage), and `Ranger-Captain of Eos` (a real CMC-3 creature threat still in Boros
Energy's current mainboard) for the mulligan signal-evaluation hand in
`test_mull_keeps_anti_matchup_hand.py`. `tests/invariants/test_target_fidelity.py` and
`tests/test_affinity_cost_mechanics.py::test_phlage_does_not_reduce_other_spells` needed a synthetic
`CardTemplate` fixture replicating Phlage's exact real oracle text (preserved from pre-ban data)
because they pin specific historical bugs (ETB target re-picking, cost-reducer false-positive) that
no other real card reproduces. `tests/invariants/test_sb_value.py` swapped Phlage for
`Lightning Helix` in a sideboard-value decklist stub.

`tests/test_waker_of_waves_oracle.py` was a **separate, unrelated root cause**, not a Phlage/DB-refresh
casualty: the test asserted Waker of Waves has a "Cycling {X}{1}{U}" keyword and an ETB graveyard-size
power buff, attributing their absence to DB corruption. Web verification (Scryfall/Gatherer listings)
confirms neither ability exists on any real printing — Waker of Waves is the Core Set 2021 7/7 Whale
with a static -1/-0 debuff and a discard-cost "look at the top two cards" activated ability; it has
never had Cycling and was never reprinted in MH3. The local DB's oracle text was correct; the test
was pinning a fabricated card ability, the same class of bug CLAUDE.md documents for the "unprovenanced
ninth part shipped 30 fabricated card texts" incident. Rewrote the test to pin the real oracle text
instead of hand-patching the DB with fictional text (which would have reintroduced that exact
anti-pattern). **Follow-up, not fixed here**: `decks/gameplans/living_end.json` sets
`prefer_cycling: true` and lists Waker of Waves among Living End's cyclers — since Waker's real
ability isn't the Cycling keyword, `engine/cycling.py:CyclingManager.can_cycle` correctly returns
False for it, so the AI cannot use Waker's discard ability via the cycling code path today. Doesn't
crash anything (Waker is simply unused by the AI in its current plan), but the deck-plan/engine
mismatch is real and worth a dedicated look — out of scope for this CI-green pass.

Full suite: 2359 passed, 22 skipped, 4 deselected (the pre-existing `test_etb_graveyard_return.py`
gap), 2 xfailed, 0 failed. All ratchets clean. `tools/refresh_wr_baseline.py` re-run to absorb 2
expected WR-anchor drifts from Phase 0's real engine/AI behavior changes (`Living End vs Jeskai Blink`
seed 50500, `Azorius Control (WST) vs Azorius Control (WST v2)` seed 50500) — both are legitimate
decision changes from 0a-0e landing, not regressions.

**Phase 0 is now fully merged (0a-0e complete).** Per the approved plan's sequencing, Phase 1 can
start.

## Phase 1

See the approved plan (`/root/.claude/plans/lets-create-plan-and-typed-flurry.md` at authoring
time — copy the plan content here if that path is not durable across sessions) for full
item-by-item design. 1c damage funnel, 1d CDA generalization: not started.

### 1a. "Unless controller pays {N}" counter/tax framework — DONE

**Problem confirmed exactly as scoped:** `spell_resolution.py`'s counter dispatch synthesized a
"Counter target X" ability description from the parsed `OracleEffect`, discarding the
"unless...pays {N}" clause entirely — Metallic Rebuke, Mana Leak, and Countersquall counter
every spell unconditionally, identical to Counterspell. Confirmed via the DB: `Metallic Rebuke`
oracle is `"...Counter target spell unless its controller pays {3}."`.

**Two additional bugs found and fixed in the same class** (both surfaced by tracing why the
fix didn't reach real cards):

1. **Dispatch fragility** — the `elif "counter" in desc:` gate was a raw substring match.
   Empirically zero false positives exist today (no synthesized ability description happens to
   contain "counter" outside real counter effects), but the plan's concern generalizes to a real
   structural bug: gating on a **template-level** `is_counterspell` flag with no per-ability
   scoping would mis-fire on every OTHER ability of a **multi-ability counterspell** — 58
   Modern-legal counterspells (Cryptic Command, Absorb, Censor, Bone to Ash, Confirm Suspicions,
   Exclude, ...) carry a second synthesized ability (usually "Draw 1 card(s)") alongside the
   counter effect. Fixed by gating on **both** `card.template.is_counterspell` (new structured
   field, populated at load time from the same `OracleEffect` the ability description is
   synthesized from — never re-derived from raw oracle text at resolution time) **and** the
   specific ability's own synthesized description starting with `"counter target"` — this binds
   the branch to the ONE ability entry that's actually the counter effect, not every ability on
   a multi-ability card. `counter_target_kind` (structured: `"spell"`/`"creature_spell"`/
   `"noncreature_spell"`/`"instant_or_sorcery_spell"`) replaces the `'noncreature' in
   counter_oracle` / `'instant or sorcery' in counter_oracle` substring checks for the same
   reason.
2. **`resolve_spell_from_oracle` intercepts multi-ability counterspells before they ever reach
   the counter dispatch at all** — its "draw N cards" pattern (`engine/oracle_resolver.py`)
   matches the WHOLE oracle string, not a single clause (the function's own docstring claims
   clause-scoping via `split_abilities`, but this specific branch uses the raw `oracle` string).
   On a real "draw + counter" counterspell it fires the draw, returns `True`, and the caller
   (`_execute_spell_effects`) skips the per-ability loop entirely — **the counter effect never
   runs at all**. Confirmed empirically: `Cryptic Command`, `Censor`, `Confirm Suspicions`, and
   `Exclude` are intercepted this way (`Absorb`/`Countersquall`/`Counterspell`/`Mana Leak` are
   not, since they have no separate draw-pattern-matching clause). This is a worse bug than the
   tax issue — these 4 cards countered NOTHING before this fix, tax or no tax. Fixed by skipping
   `resolve_spell_from_oracle` entirely for any `is_counterspell` card, always routing through
   the per-ability loop (which correctly fires both the draw ability AND the counter ability as
   independent entries, since it iterates the synthesized abilities list rather than pattern-
   matching the whole oracle string once).

**Design, following the plan:** `engine/oracle_parser.py::parse_counter_tax` (scoped to the
single ability paragraph containing "counter target" via `split_abilities`, so an unrelated
"unless...pays" clause elsewhere on a multi-ability card — e.g. a land's optional-untapped-entry
cost — can't leak in) → `CardTemplate.counter_tax_amount` (populated at load time, `0` = hard
counter). At resolution, a nonzero tax routes through `engine/optional_costs.py`'s NEW
`offer_counter_tax`/`parse_counter_tax_cost` — reusing the EXISTING `OptionalCost` typed schema
and the EXISTING `decide_optional_cost` AI callback (both already existed, unused for this shape,
per `ai/schemas.py`'s `CostKind = Literal["life", "mana", ...]` / `EffectKind = Literal[...,
"counter_target", ...]` already anticipating exactly this), not a new mechanic-named callback —
just a new discovery+offer pair for the case where the decision-maker (targeted spell's
controller) differs from the source card's own controller (the counter's caster). Affordability
(`player.available_mana_estimate < amount`) is an engine-side rules gate checked BEFORE offering
the decision at all — an unpayable tax never becomes a choice, matching a real game where an
unaffordable "unless" clause auto-resolves to the default (countered). The strategic "would I
want to" question is decided by `ai.ev_evaluator.project_counter_tax_payment` (new function,
reuses the existing `_project_spell` oracle-driven resolution projection rather than a second
bespoke value model — pre-compensates for `_project_spell`'s "still in hand" assumption since the
targeted spell already left the hand and already paid its own cost) feeding `best_choice` via the
same `[pay, skip]` `Choice` list `decide_optional_cost` already builds for every other optional
cost — no new AI decision-kernel code.

**Tests (failing-first):** `tests/test_counter_tax_framework.py` — `parse_counter_tax` unit tests
(hard vs soft, ability-paragraph scoping), structured-field tests on real DB cards (Metallic
Rebuke tax=3, Counterspell tax=0, Essence Scatter kind=creature_spell, DB-wide zero-false-positive
regression on the old substring-collision concern), resolution-engine integration
(`TestSoftCounterResolution`: pay→not countered, don't-pay→countered, unaffordable→auto-countered
with the AI callback never even asked, hard-counter regression), `TestMultiAbilityCounterDoesNotDoubleFire`
(synthetic Cryptic-Command-shape fixture — counters exactly once, doesn't re-fire on the second
ability), `TestRealMultiAbilityCounterspellsActuallyCounter::test_censor_counters_its_target` (real
DB card, pins the `resolve_spell_from_oracle` interception fix specifically). 15 tests, all
failing red before the fix (verified via the substring/template-flag-only intermediate states
during development), green after.

**Replay:** `python run_meta.py --bo3 "Pinnacle Affinity" "Izzet Prowess" -s 55506` does NOT
demonstrate the fix — traced via `sys.settrace` to a genuinely separate, pre-existing bug: the AI
decides to cast Metallic Rebuke based on a color-blind mana-availability estimate, then the
engine's real (correctly color-aware) mana-payment step rejects the cast because no blue source
is actually untapped at that moment (`CastManager.cast_spell` returns `False` at its final
`tap_lands_for_mana` call — confirmed identical on `origin/main` before this branch's changes via
a `git stash` A/B comparison, so this is not a regression from this work). Deprioritized per
class-size reasoning — this is a general "AI overestimates castability of colored spells" gap,
not specific to counterspells, and out of scope for 1a. **Seed `55507` does demonstrate the fix
working end-to-end in a real game**: `T4: Resolve Metallic Rebuke` → `T4: Dragon's Rage Channeler
is countered`. Unit tests are the authoritative verification for the pay/don't-pay/unaffordable
branches specifically, since live-game RNG doesn't reliably hit the tax-paid branch on demand.

**Follow-up filed, not fixed here:** the color-blind mana-availability estimate in the AI
response-cast decision path (`ai/response.py` / whatever feeds `game_runner.py`'s
`opponent_ai.decide_response`) — worth its own investigation given the class size (affects any
colored spell's response-cast decision, not just counterspells).

### 1b. Combat legality enforcement — DONE (evasion, menace, protection-blocking, hexproof-targeting)

**Problem confirmed exactly as scoped:** `CombatManager.declare_blockers`'s docstring said
"validates and records" blocking assignments; the body only recorded them. Flying/reach/menace/
protection were entirely unenforced at the engine layer — `ai/ev_player.py::decide_blockers`
already filters candidates by these same rules before proposing a block, but that only means the
AI is polite to itself; nothing stopped an illegal assignment from being recorded and acted on as
legal if it reached this layer any other way. Confirmed `engine/target_solver.py` (the unified
targeting module Phase 1-3 of a prior migration already routes cast-legality checks through) had
matching zero coverage for hexproof/protection as illegal-target filters.

**Design:** New oracle-derived `CardTemplate.protection_from_colors: frozenset` (populated at
load time via new `engine/oracle_parser.py::parse_protection_from`, which handles the compound
"protection from X and from Y" form — e.g. Sanctifier en-Vec's "Protection from black and from
red" — by matching the whole clause span first, then extracting every color word from within it,
since only the FIRST color in the compound form is preceded by the word "protection"). Scope:
color-based protection only (matches the precedent already set by `ai/sideboard_solver.py`'s
`_clause_protection_color`); type-based ("protection from artifacts") and "protection from
everything" are 0-card gaps in the registered 16-deck pool today — extend when one enters.

`CombatManager.declare_blockers` now filters `blocker_ids` per attacker: CR 702.9b (flying needs
flying/reach to block), CR 702.16d (protection blocks a same-quality blocker), both per-blocker;
CR 702.111b (menace needs ≥2 blockers) is a whole-assignment rule checked after the per-blocker
filter — a single surviving blocker against a menace attacker drops the ENTIRE block (menace
isn't "one fewer legal blocker", it's "this block never happened"). Every drop is logged so AI
behavior stays debuggable (a silently-dropped illegal block is indistinguishable from a
deliberate no-block decision in the log).

`engine/target_solver.py` gains `_blocked_by_hexproof(card, controller)` (CR 702.11d — hexproof
blocks OPPONENT spells/abilities only, not the controller's own), wired into both
`has_legal_target` and `enumerate_legal_targets`'s per-candidate filtering loop, scoped to
`req.zone == "battlefield"` (hexproof has no meaning for cards in other zones — a reanimation
spell targeting a hexproof creature CARD in a graveyard is unaffected).

**Deferred, not fixed here:** Shadow (CR 702.27) — zero Modern-legal cards have this keyword
(Nemesis-block mechanic, never reprinted into Modern legality), so building enforcement for it
would be speculative machinery for a 0-card class; skip until proven otherwise. Ward (CR 702.21)
— structurally a "cost imposed on the caster when targeting" mechanic, much closer in shape to
1a's counter-tax framework (`OptionalCost`/`decide_optional_cost`) than to a simple illegal-target
filter; the plan's original framing grouped it with hexproof/protection, but that's not
rules-accurate (ward doesn't make a target illegal, it imposes a consequence for targeting
anyway). 4 cards with "ward" in the registered 16-deck pool. Worth its own properly-scoped
test-first pass reusing 1a's cost/decision primitives rather than rushing it into this commit.
Protection as a TARGETING restriction (not just a blocking restriction — CR 702.16e, a removal
spell of the protected quality can't target the protected permanent either) is also deferred:
needs the source spell's own color threaded through `TargetRequirement`/`has_legal_target`, which
neither function currently receives — a real but separable extension.

**Tests (failing-first):** `tests/test_combat_block_legality.py` (11 tests — flying/reach
evasion, menace whole-block illegality, protection-from-color blocking, real-card structured-field
integration on Sanctifier en-Vec, illegal-block logging) and 4 new tests appended to the existing
`tests/test_target_solver_legality.py` (opponent's hexproof creature excluded from both
`has_legal_target` and `enumerate_legal_targets`; own hexproof creature still targetable by own
spells; a second non-hexproof candidate keeps the requirement satisfiable). 15 new tests total.

Full suite: 2389 passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed. All ratchets clean.

### Ward mechanic (deferred from 1b) — DONE

**Placement note:** filed here (immediately after 1b) rather than as a new Phase-3 entry because
this item IS 1b's own explicitly-deferred paragraph ("Ward ... worth its own properly-scoped
test-first pass reusing 1a's cost/decision primitives") being picked up and finished, not a new
finding from a fresh sweep — keeping it adjacent to the deferral makes the tracker's "what got
deferred, what happened to it" trail readable in one place.

**Problem confirmed exactly as scoped:** a codebase-wide `grep -i ward` across `engine/` and `ai/`
prior to this change returned zero hits outside comments/docstrings — no Ward (CR 702.21a)
enforcement existed anywhere. A spell or ability could legally target a Ward permanent and simply
resolve; the "counter unless its controller pays [cost]" trigger was never offered to anyone.
Confirmed via the DB: 4 registered-16-deck-pool cards carry "ward" in their oracle text (Kappa
Cannoneer, Lavaspur Boots, Sire of Seven Deaths, Hall of Storm Giants), and all 4 previously
functioned as if unwarded.

**Confirmed 1b's framing was rules-accurate, not just plausible.** Ward is a triggered ability that
lives on the TARGETED PERMANENT ("Whenever this permanent becomes the target of a spell or ability
an opponent controls, counter that spell or ability unless its controller pays [cost]") — the
mirror image of 1a's counter-tax framework in EVERY structural respect except one: 1a's counter-tax
is a property of a SPECIFIC SPELL (a counterspell), discovered once, at that spell's own resolution.
Ward is a property of the TARGET, and can be triggered by ANY spell or ability that targets it —
so, unlike 1a, the check can't live inside a single mechanic's resolution branch; it needs a hook
that runs for every targeted stack item, spell or ability alike.

**Class-size discipline applied to cost-shape scoping.** DB-wide census of every card whose oracle
text has a clause literally STARTING with "ward" (`engine.oracle_clauses.split_clauses`, matching
how the keyword always prints as its own standalone ability line): 76 are mana-cost-shaped
("Ward {N}"), 15 are life-shaped ("Ward—Pay N life"), 26 are other shapes (discard a card,
sacrifice a permanent, collect evidence N, exile-cost, some with no fixed numeric amount at all —
"Ward—Pay life equal to this creature's power" has no static {N} to encode). Mana-shape clears the
≥10-card class-size bar most clearly (5x the next bucket) and is the only shape implemented in this
pass. Life/discard/sacrifice-shaped Ward — real cards, e.g. Sire of Seven Deaths's "Ward—Pay 7
life" — are a documented, deliberate gap: `parse_ward_cost` returns 0 for them (same "0 = no tax to
enforce" contract `parse_counter_tax` uses for hard counters), so they behave as unwarded until a
follow-up extends `CostDescriptor`'s existing `"life"`/`"discard"`/`"sacrifice"` `CostKind` variants
(all already defined in `ai/schemas.py`, unused for this purpose today) to the Ward discovery site.

**Second gap found and deliberately excluded, not missed.** Ward CONFERRED to another object —
Lavaspur Boots' "Equipped creature ... has ward {1}" (an Equipment granting ward to whatever it's
attached to), Hall of Storm Giants' "{5}{U}: ... becomes a 7/7 ... creature with ward {3}" (an
activated ability that temporarily grants ward to its own source) — is a DYNAMIC keyword-grant
mechanism, the same class as 0b's `ContinuousEffectsManager` migration (a static field on the
granting card's own `CardTemplate` cannot represent "ward on whatever this is currently attached
to" or "ward only while animated"). `parse_ward_cost` is scoped via clause-START matching
specifically so these don't leak in as false positives on the wrong card (Lavaspur Boots' own
`ward_cost` must read 0 — the Boots are never themselves targeted with intent to remove a
creature). Confirmed via `tests/test_ward_framework.py::TestWardTemplateField` that both cards
correctly report `ward_cost == 0`. Extending Ward enforcement to dynamically-granted instances is
future work, tracked here rather than silently mis-scoped into this pass.

**Design, mirroring 1a exactly where the shapes coincide and diverging where they don't:**
`engine/oracle_parser.py::parse_ward_cost` (clause-scoped via `split_clauses`, not
`split_abilities` — Ward's own clause never needs pairing with an unrelated trigger phrase the way
1a's counter-tax needed `"counter target"` co-occurrence, since the clause literally starting with
"ward" IS the whole signal) → `CardTemplate.ward_cost` (populated at load time, mirroring
`counter_tax_amount`'s pattern; 0 = no mana-shaped ward). At resolution, `engine/optional_costs.py`
gains a NEW `offer_ward_tax`/`parse_ward_tax_cost` pair — same typed `OptionalCost` schema, same
`decide_optional_cost` AI callback as every other optional-cost mechanic, no new callback — with
the (target-permanent, targeting-item) role assignment reversed from 1a's (source-spell,
targeted-spell): the WARDED PERMANENT plays 1a's "source_card" role (it carries the tax-imposing
ability), and the CASTER'S OWN spell/ability plays 1a's "targeted_card" role (it's the stack item
at risk of being countered). Affordability is checked engine-side before offering the decision, an
unpayable tax auto-counters with no AI call at all — identical rule to 1a's, for the identical CR
reason (an unpayable "unless" clause never becomes a real choice in a real game).

**Hook point — the one piece that genuinely can't reuse 1a's shape.** 1a dispatches from inside the
counterspell's own resolution branch (`_execute_spell_effects`'s per-ability loop), because the
mechanic only exists on counterspells. Ward has no equivalent "only exists on X" anchor — it must
be checked for every stack item, spell or ability, the instant before it would resolve. Implemented
at the TOP of `engine/spell_resolution.py::resolve_stack`, immediately after popping the item and
before the existing CR 608.2b fizzle check: for each target in `item.targets`, if the live card is
still on the battlefield, carries `ward_cost > 0`, and is controlled by an OPPONENT of the item's
own controller (CR 702.21a's "an opponent controls" gate — a player targeting their own warded
permanent never triggers it), `offer_ward_tax` is called. An unpaid tax reuses 1a's own
`ResolutionManager._move_countered_stack_item` (already branches SPELL vs ABILITY correctly) and
returns immediately — nothing else about the item resolves. Multiple simultaneously-warded targets
each get their own sequential offer; the first one left unpaid counters the WHOLE spell/ability (CR
702.21a counters the spell, not just that target) and short-circuits any remaining checks.

**AI projection — same math, reversed roles, not a re-derivation.** `ai/ev_evaluator.py::
project_ward_tax_payment` projects "does my own spell/ability survive, minus the tax" for the
pay branch. Investigated whether this needed new logic (1a's docstring explicitly frames the two
mechanics as opposite-polarity: "there, the TARGETED spell's controller decides; here, the SOURCE
spell's caster decides") — but tracing the actual math shows both mechanics reduce to the identical
computation once the decision-maker is identified: in 1a, the decision-maker (targeted spell's
controller) is ALSO that spell's own caster from ITS perspective; in Ward, the decision-maker
(source spell's caster) is the SAME role relative to their own spell. Both projections are "does
the stack item I control survive this resolution, minus an additional mana tax" over the identical
`snap`-at-resolution-time convention `decide_optional_cost` already provides. `project_ward_tax_
payment` therefore delegates to `project_counter_tax_payment` rather than re-deriving the
pre-compensation math, kept as a separately-named entry point (not a bare alias) purely for
call-site clarity — each is discovered from a different template field via a different engine hook,
and the shared math is a real coincidence of shape, not evidence the two mechanics are one thing
wearing two names.

**Tests (failing-first):** `tests/test_ward_framework.py` — 23 tests. `TestParseWardCost` (8):
no-ward, standalone clause, clause-with-reminder-text, ward-among-other-abilities, life-shaped
returns 0, discard-shaped returns 0, ward-conferred-via-equipment not captured, ward-conferred-via-
activated-ability not captured. `TestWardTemplateField` (6): real-DB structured-field checks on all
4 pool cards (Kappa Cannoneer ward_cost=4; Sire of Seven Deaths, Lavaspur Boots, Hall of Storm
Giants all 0, each for a documented reason), a DB-wide ≥10-card class-size floor (positive control),
a DB-wide false-positive guard (every `ward_cost>0` card has a real clause starting with "ward").
`TestWardResolution` (6): pay→spell resolves and kills its target, don't-pay→spell countered and
target survives, unaffordable→auto-countered with the AI callback never invoked, own-permanent
targeting never triggers the offer at all, a non-warded target is completely unaffected by the new
code path, and a two-target spell with only one warded target gets fully countered (not partially)
when that one tax goes unpaid. `TestWardAppliesToAbilitiesNotJustSpells` (2): an
`ACTIVATED_ABILITY`-type stack item targeting a warded permanent is gated identically to a spell
(effect callable never invoked when unpaid, invoked exactly once when paid) — the one behavior 1a's
own test suite never needed to cover, since counter-tax is spell-only by construction.
`TestProjectWardTaxPayment` (1): `project_ward_tax_payment` and `project_counter_tax_payment`
produce the identical mana-deducted result on the same inputs, pinning the delegation intentionally
rather than leaving it to drift.

Verified genuinely red pre-fix via `git stash` on the 6 modified engine/AI files (test file and an
unrelated in-flight sibling-agent file left untouched by the stash): `ImportError: cannot import
name 'parse_ward_cost'` — the mechanic didn't exist at all, matching 1b's own "likely: none at all"
prediction for what the grep would find.

Full suite: 2413 passed pre-existing baseline (from 1d) + this pass's 23 new tests. All 4 ratchets
(`check_abstraction.py`, `check_magic_numbers.py`, `check_zone_mutation.py`,
`check_doc_hygiene.py`) clean — zone-mutation count unchanged at 102 (the new unpaid-tax path
reuses 1a's existing `_move_countered_stack_item`, adding no new raw `.zone =` site).

### 1c. Combat damage funnel unification — DONE

**Problem confirmed exactly as scoped:** `CombatManager._deal_combat_damage` never routed through
`engine/damage.py:deal_damage` — it mutated `damage_marked`/`life` directly at 5 sites,
reimplementing a parallel, incomplete copy of what `deal_damage` already does correctly for every
OTHER damage source (burn spells, oracle-resolver damage triggers). Two concrete bugs:

1. **Lifelink was only ever checked for the attacker.** `has_lifelink = Keyword.LIFELINK in
   attacker.keywords`, applied once at the end via `game.players[active_player].life +=
   total_damage_dealt` — a BLOCKER with lifelink dealing damage back to the attacker gained its
   controller nothing. Verified failing-first: `test_lifelink_blocker_gains_controller_life`
   failed on pre-fix code (`life=20`, expected `23`) via a `git stash` A/B check.
2. **Deathtouch was faked**, not modeled. The assignment logic correctly computes a 1-point
   "lethal" hit for deathtouch (CR 702.2c), but then force-set `damage_marked = toughness`
   afterward to guarantee SBA destruction, rather than using the real `_deathtouch_damage` marker
   `deal_damage` already writes and `SBAManager.perform_deathtouch_check` (migrated live in 0d)
   already consumes. Functionally equivalent outcome pre- and post-fix (both destroy the
   creature), but via a bespoke combat-only mechanism instead of the shared one — the exact
   "no single owner" pattern this whole program targets.

**Design:** Damage *assignment* (how many points each blocker/the player receives — CR 510.1c
ordering, deathtouch's 1-point threshold, trample overflow) stays in `combat_manager.py`; that
part is genuinely combat-specific. Damage *application* now calls `deal_damage(source, target,
amount, is_combat=True)` for every one of the 5 sites (blocker takes damage, attacker takes
damage back, trample overflow to player, unblocked attacker to player). This required completing
the "lifelink hook" `engine/damage.py`'s own docstring had documented as reserved-but-unimplemented
since the W0-D commit: added `CardInstance.has_lifelink` (mirroring the existing `has_deathtouch`
property) and real CR 702.15 life-gain logic inside `deal_damage` itself, so lifelink is now
correct for EVERY damage source in the engine (burn spells included), not just combat, and not
just attackers. The old combat-only lifelink block and both deathtouch-faking loops were deleted
entirely — no longer needed once the real markers are written by the shared primitive. Also fixed
a dead `total_player_damage` accumulator (`_deal_combat_damage` always returned 0 due to a no-op
`sum()` expression computing `life - life`; harmless because the one caller discards the return
value, but directly in the code being rewritten, so fixed alongside).

**Tests (failing-first):** `tests/test_combat_damage_funnel.py` (6 tests) —
`TestLifelinkBlocker` (blocker-side lifelink, attacker-side regression, both-sides-independent),
`TestDeathtouchRealMarker` (attacker/blocker deathtouch destruction via the real marker at
mismatched toughness, non-deathtouch regression pinning `damage_marked` reflects actual damage
dealt, not a fake). Verified the 2 lifelink tests genuinely fail pre-fix via `git stash`; the
deathtouch tests pass both pre- and post-fix (same observable outcome, different mechanism —
legitimate behavior-preservation coverage for the internal-mechanism change).

Full suite: 2395 passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed. All ratchets clean.
Replay: `python run_meta.py --bo3 "Boros Energy" "Dimir Midrange" -s 55501` — combat resolves
correctly end-to-end (blocks, trades, trample, lethal damage) with no regressions.

### 1d. Generalize `detect_power_scaling` (CDA) — DONE

**Problem confirmed exactly as scoped:** `detect_power_scaling` had 4 buckets (domain, tarmogoyf,
delirium, graveyard) but no bucket for "power and toughness are each equal to the number of X you
control" — the single most common CDA shape in Magic (47 cards share this exact phrasing in this
DB: Cultivator Colossus, Crusader of Odric, Darksteel Juggernaut, Master of Etherium, Katilda,
...). Cultivator Colossus fell through to `template.power or 0` = 0/0 and died to state-based
actions the instant it resolved — the audited bug (replay seed 55505/55515).

Independently confirmed the plan's second claim: the `"graveyard"` branch had **no P/T anchor at
all** — bare co-occurrence of `'exile'` + `'instant'/'sorcery'` + `'graveyard'` matched
**293/21795 cards** in the DB, almost all Embalm/Eternalize reminder text ("Exile this card from
your graveyard: Create a token..."). This included live-pool card **Murktide Regent**, whose real
scaling mechanism is delve-triggered +1/+1 counters (`plus_counters`), not a continuous
graveyard-count CDA at all — it was being *actively mismodeled*, not just harmlessly mistagged.

**Design:** New `permanent_count:<word>` bucket in `detect_power_scaling`, matched via one regex
capturing the noun after "you control" — no enumeration of which nouns are valid at parse time.
Resolution is generic: new `CardInstance._get_permanent_type_count(type_word)` on `cards.py`
(mirroring the existing `_get_domain_count`/`_get_artifact_count` shape per the plan's explicit
design) handles card types (land/creature/artifact/enchantment/planeswalker), the literal word
"permanent(s)", and land/tribal SUBTYPES via naive regular-plural singularization ("Islands" →
"Island", "Soldiers" → "Soldier") — irregular plurals (Elves, Wolves) are a documented 0-card gap
in the registered pool today, extend if one enters. `_dynamic_base_power`/`_dynamic_base_toughness`
both dispatch to the same count for power AND toughness (the oracle text says "each equal to" —
no ceiling/multiplier, unlike domain's `min(count, 4)` or tarmogoyf's `+1` toughness offset).

The `"graveyard"` branch's false-positive fix went through two iterations — the first attempt
(require `'power'`/`'toughness'` + `'graveyard'` + `'instant'/'sorcery'` + `'equal'` co-occurring
in the same `split_abilities` paragraph) cut 293 → 24 false positives but still matched Scavenge
reminder text ("Exile this card from your graveyard: Put a number of +1/+1 counters equal to this
card's power ... Scavenge only as a **sorcery**.") — all four words are present, just in the wrong
relationship (the CDA phrase requires "power ... equal to ... number of ... instant/sorcery ...
graveyard" IN THAT ORDER; Scavenge text has "graveyard" before "equal to this card's **power**",
with "sorcery" describing the activation timing restriction, not a P/T definition). Final fix: an
ordered, sentence-scoped regex (`[^.]*?` between each anchor, never crossing a period) requiring
that exact phrase sequence. Cuts 293 → **8** real graveyard-count CDAs (Enigma Drake, Haughty
Djinn, Crackling Drake, Kinetic Augur, Magnivore, Melek, Spellheart Chimera, a Seize the Storm
token) — all genuine, none currently in the registered 16-deck pool, but Murktide Regent (which
IS in the pool) is correctly excluded.

**Tests (failing-first):** `tests/test_permanent_count_cda.py` (18 tests) — parser unit tests for
the new bucket (card types, "permanents", subtypes), the graveyard-anchor fix (Eternalize
reminder-text regression, Murktide-shaped delve-counter regression, a positive-control real CDA
to guard against overcorrecting into a false negative), real-DB structured-field integration
(Cultivator Colossus, Crusader of Odric, Murktide Regent, a DB-wide false-positive-count bound),
and live P/T computation (survives-own-ETB with lands present — the audited bug itself — zero-count
edge case, creature-count excludes noncreatures, subtype count, "permanents" counts everything
including itself).

Full suite: 2413 passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed. All ratchets clean.
Replay: `python run_meta.py --bo3 "Eldrazi Tron" "Amulet Titan" -s 55515` — Cultivator Colossus
cast T6, resolves, and lives on the board as a correct **9/9** (9 lands controlled) instead of
dying to its own 0/0 on cast.

**Phase 1 is now fully done (1a-1d complete).**

## Phase 2 — AI decision kernel (opportunity cost + joint block assignment)

> **Concurrent-edit note**: sibling agents may be editing this same tracker doc file on separate
> Phase 3 branches at the same time as this Phase 2 section was written. If a PR merge conflicts
> here, it is a low-risk prose-only conflict — resolve by keeping both sections.

### 2a. `ai.clock.opportunity_cost` primitive — DONE

**The specimen bug, re-examined.** `ai/ev_player.py`'s non-emergency block loop had
`if b_pow == 0: continue  # 0-power non-kill = pure waste` — a categorical veto standing in for a
computation `ai/clock.py` already had all the pieces for. Empirical check before writing any fix
(per this program's own "verify, don't assume" rule): the emergency path's chump-selection loop
was NOT gated by this veto at all — it already scored every candidate via
`_score_block_lifespan_delta` regardless of power. The veto lived only in the non-emergency
("normal") path. Bugs #5/#6 (won't chump-block a 0-power creature at healthy life) are therefore
non-emergency-path bugs; bug #4 (joint block assignment) is an emergency-path bug — different
mechanisms, same root class (a proxy standing in for a real computation), which is why the plan
groups them under "2a/2b" together rather than either phase alone.

**The primitive.** `opportunity_cost(card, board, snap)` (`ai/clock.py`) answers "what do I lose by
spending this permanent right now" as a single computation, in the same "value" units as
`ai.ev_evaluator.creature_value` (clock-impact × `CREATURE_VALUE_OUTER_SCALE`). Three additive
terms, each reusing an existing primitive rather than reimplementing it:

1. **Ongoing combat/keyword clock impact** — `creature_clock_impact_from_card` (already existed,
   already prices a 0-power creature's blocking value via `PURE_BLOCKER_TOUGHNESS_VALUE` when it
   has real toughness, and prices keyword-bearing 0-power creatures like Walking Ballista's damage
   ability separately — see below).
2. **Un-exhausted activated ability** — one card's worth of future clock impact
   (`card_clock_impact`, the SAME conversion `creature_clock_impact_from_card` already uses for its
   "card_advantage" tag bonus) when the oracle text exposes a CR 602.1a-shaped activated ability
   (`"[Cost]: [Effect]"`). **Real gap found while implementing this term**: the obvious existing
   candidate, `ai.response_enumeration._battlefield_has_activatable`, checks
   `AbilityType.ACTIVATED` on `CardTemplate.abilities` — verified empirically that this is
   populated for **zero of 12972 creatures** in the live DB (`card_database.py` only extracts
   `CAST`/`ETB`/`ATTACK`/`DIES` ability shapes at load time; `ACTIVATED`/`MANA_ABILITY` are never
   written). That helper is unreachable dead code for every real card today. `opportunity_cost`
   instead scans oracle text directly with a small CR 602.1a cost-separator regex
   (`_ACTIVATED_ABILITY_RE`, deliberately not excluding mana abilities — losing a mana source to a
   needless chump block is a real cost, unlike `_battlefield_has_activatable`'s instant-speed-
   response framing where mana abilities are irrelevant noise).
3. **Equipment ceiling** — `_equipment_ceiling_for_creature` (`ai.permanent_threat`, PR-L3), already
   expressed in the same "value" units (previously summed directly into `creature_threat_value`).

`creature_value`/`creature_threat_value` (`ai/ev_evaluator.py`) are now CALLERS of `opportunity_cost`
rather than parallel implementations — `creature_value` is now a one-line delegation (via a small
shared `_controller_board` helper resolving the controller `PlayerState` from `card._game_state`);
`creature_threat_value` calls `opportunity_cost` for its base+ceiling terms and keeps only its own
virtual-power (battle-cry/scaling) premium as an additive term on top.

Tests (`tests/test_opportunity_cost_primitive.py`): a genuinely-dead 0-power creature (real
Ornithopter — the literal "stripped Ornithopter" shape from bug #6, Affinity vs 4c Omnath) prices
near zero; the SAME Ornithopter with an unattached Cranial Plating nearby (Affinity-board equipment-
ceiling shape) prices strictly above that baseline; a 0/0 Walking Ballista (real card, isolates the
activated-ability term specifically since its raw clock impact is exactly 0) prices above zero via
its un-exhausted ability alone; a non-creature permanent returns 0.0; `creature_value` is proven to
literally equal `opportunity_cost` on the same fixture. Verified genuinely red pre-fix via
`git stash` (the primitive didn't exist — `ImportError`, not a soft assertion failure) before
implementing.

### 2b. Joint block-assignment — DONE (fixes audited bug #4)

**Root cause, confirmed by reproduction, not by re-reading the old writeup.** The emergency path's
per-attacker `my_life_now` was `me.life - max(0, total_incoming - already_absorbed -
attacker.power)` — for the FIRST attacker processed, this subtracts every OTHER still-undecided
attacker's power on top of the current one, which can drive `my_life_now` deeply negative. Built a
minimal empirical reproduction (`life=8`, two 8/8 attackers, two 4/4 blockers — see the probe script
history in this branch's session, encoded permanently as
`tests/test_decide_blockers_joint_assignment.py`): `my_life_now` comes out to `8 - max(0, 16-0-8) =
-8` for the first attacker. Both the hypothetical "block" and "no-block" post-states then read as
already-dead (`ai.clock.life_as_resource` floors at -100 for `life <= 0`), so the delta is exactly
`-100 - (-100) = 0` — which fails the strict `delta > 0` selection threshold for EVERY candidate.
`emergency_blocks` ends up completely empty, and the function falls through — ungated — into the
non-emergency path below, which scores against a static `me.life` and happily spends BOTH available
blockers double-blocking the FIRST attacker for a "clean kill" (4+4=8 meets the 8 toughness) while
the second, equally dangerous attacker gets zero blockers. Defending player takes 8 unblocked
damage at 8 life and dies — to damage that was trivially preventable by single-blocking each
attacker once. Confirmed via `git stash` that this reproduction genuinely fails pre-fix (not
assumed from the audit's prose).

**Fix: two-pass joint assignment**, replacing the single greedy per-attacker loop:

- **Pass 1 (coverage, emergency turns only)**: force-block attackers (biggest power first) with the
  CHEAPEST available blocker — ranked by `opportunity_cost` (2a) ascending — until the actual JOINT
  remaining damage from attackers NOT yet covered is survivable. "Joint" is the fix:
  `unblocked_damage` is recomputed each iteration directly from the current coverage set (`sum of
  power for every attacker whose instance_id is not yet a key in blocks`), never from a per-attacker
  guess about what the others will do. This is also where bugs #5/#6's veto retirement actually
  lands for the emergency path: a 0-power creature with near-zero `opportunity_cost` is now the
  FIRST choice for coverage, not a banned candidate.
- **Pass 2 (optimization / swap-upgrade)**: once survival is secured for a covered attacker, check
  whether a different UNUSED blocker scores a strictly better `_score_block_lifespan_delta` (a clean
  kill / favorable trade the cheap coverage pick couldn't achieve) and swap it in. Deliberately
  bounded to attackers pass 1 already committed to — NOT extended to attackers pass 1 left
  deliberately unblocked (verified against `tests/test_decide_blockers_emergency_gate.py`'s
  portfolio-cap test: extending pass 2 to uncovered attackers made that test fail, since every
  "avoid a little more face damage" block looks locally positive under
  `_score_block_lifespan_delta` even when the danger has already passed — the old portfolio cap's
  *intent*, just implemented on the correct joint quantity instead of a mismatched one).
- **Non-emergency turns**: pass 1 is skipped entirely (unchanged trigger — `emergency`'s three
  conditions are untouched) and pass 2 runs over every attacker, replacing the old "normal path"
  loop exactly, minus the two categorical vetoes (0-power blocker, hard battle-cry exclusion —
  the latter subsumed by `_is_protected_piece`, which already covers the same "whenever this/<name>
  creature attacks" oracle shape plus escape/planeswalker, so the separate battle-cry check was
  redundant). The positive-`_score_block_lifespan_delta` threshold is the only remaining gate;
  `_is_protected_piece` keeps a SOFT preference (unprotected-candidates-first, fall back to
  protected only if no alternative exists) rather than a hard veto, via the same `_candidate_pool`
  helper pass 1 and pass 2 both use.

**Unit-mismatch found and removed, not patched.** The old emergency loop's "portfolio cap"
(`sacrificed_value > max(remaining, 1.0)`) compared `creature_value` output (clock-impact ×
`CREATURE_VALUE_OUTER_SCALE`, a "value" unit) directly against `remaining` (raw damage points) — a
real unit mismatch flagged by the Phase 2 plan. The two-pass redesign doesn't need this comparison
at all: pass 1's survivability check (`unblocked_damage` vs `me.life`) is dimensionally consistent
on its own (both sides are damage/life points), so the mismatched term is deleted rather than
threaded through `life_as_resource`. No new constant was needed for this half of the fix.

**Emergency/normal collapse — partially warranted, not fully forced.** Verified (per the task's
"don't force a refactor that isn't warranted") that the two paths share one `_candidate_pool` helper,
one `_log_block_assignments` helper, and pass 2's optimization logic — but the emergency/non-
emergency SPLIT itself is preserved (pass 1 only runs `if emergency:`), because collapsing it
further (running pass 1's forced-coverage logic even on comfortable non-emergency turns) is
unnecessary: pass 1's own `unblocked_damage < me.life` stabilize check already no-ops on comfortable
boards, so the two code paths already share everything they safely can without changing behavior on
non-emergency turns.

Tests (`tests/test_decide_blockers_joint_assignment.py`):
`test_two_jointly_lethal_attackers_both_get_blocked` — the exact bug-#4 reproduction above, replayed
as a deterministic unit test (two 8/8 attackers, two 4/4 blockers, life=8; both attackers must be
blocked, one blocker each, defender survives). Verified genuinely red pre-fix via `git stash` (old
code: `blocks={atk_a: [blk1, blk2]}`, `atk_b` completely unblocked, `final_life=0`).
`test_favorable_clean_kill_trade_still_happens_when_survival_not_compromised` — regression guard:
a strictly-dominant trader (survives AND kills) must still be selected over a merely-adequate cheap
chump once survival is secured — this is the "swap in favorable trades" half of the two-pass design,
and it was ALREADY passing pre-fix (pinned by the pre-existing
`tests/test_decide_blockers_protects_engines.py`-adjacent `TestDefenderTakesFavorableTradeOverChump`
shape), so this test's job is to prove the refactor didn't regress it, not to prove a new bug fixed.
Full existing block-decision test suite (`test_block_scoring_is_lifespan_delta_formula.py`,
`test_blocking_equipment_aware.py`, `test_chump_block_plating_when_lethal_range.py`,
`test_decide_blockers_emergency_gate.py`, `test_decide_blockers_plating_aware.py`,
`test_decide_blockers_protects_engines.py`, `test_decide_blockers_race_when_winning.py`,
`test_defender_chumps_when_no_block_means_lethal_next_turn.py`,
`test_virtualboard_respects_summoning_sickness.py`) re-run green — 37 tests, 0 regressions.

**WR-baseline-anchor drift (expected, refreshed — same class as 0e's two drifts).** Because
`opportunity_cost` changes `creature_value`/`creature_threat_value` for every caller across `ai/`
(targeting, discard, mulligan hand evaluation — not just `decide_blockers`), the full suite surfaced
2 `tests/test_wr_baseline_anchor.py` entries that drifted from real decision-quality changes: `Amulet
Titan vs Living End` (seed 50000) flips winner Amulet Titan → Living End; `Pinnacle Affinity vs 4/5c
Control` (seed 50000) keeps its winner but shifts turns 9 → 10. **Verified these are caused by this
Phase's changes, not pre-existing debt**, via `git stash` on `ai/clock.py`/`ai/ev_evaluator.py`/
`ai/ev_player.py` before refreshing: all 19 currently-tested anchor entries pass on unmodified code.
Refreshed via `python tools/refresh_wr_baseline.py` (same tool 0e used) — the script recomputes the
full 27-entry fixture deterministically from seeds, so it also updated 2 entries outside the
currently-parametrized `range(17)` (`Instant Reanimator vs Boros Ponza` turns 11→13, `Grixis
Reanimator vs Creatures Toolbox` turns 8→9 — both winner-unchanged, consistent with the same
broad creature-valuation ripple). All 19 tested entries green post-refresh.

**Replay verification** (all three audited seeds, `python run_meta.py --bo3 ...`):

- `"Eldrazi Tron" "Amulet Titan" -s 55505` — match completes cleanly (Eldrazi Tron wins 2-0). Direct
  live evidence of the 0-power-veto retirement: T4 `[BLOCK] Arboreal Grazer (0/3) blocks Glaring
  Fleshraker (2/2) — lifespan_delta=+1.00` — a 0-power creature chump-blocking, which the old
  categorical veto would have refused outright regardless of the delta formula's (correctly
  positive) answer.
- `"Boros Energy" "Dimir Midrange" -s 55501` (Dash-Ragavan-class chump-blocker shape) — match
  completes cleanly (Boros wins 2-1), multiple `[BLOCK]`/`[BLOCK-EMERGENCY]` lines across both
  players, no crashes, no anomalous double-block-starves-a-sibling patterns.
- `"Affinity" "4c Omnath" -s 55504` (stripped-Ornithopter shape) — match completes cleanly (4c
  Omnath wins 2-0). This particular seed's games didn't reach a beneficial-block decision point
  (races/mulligans didn't produce a blocking scenario) — no `[BLOCK]` lines at all, which is a
  legitimate outcome, not a gap; the mechanic itself is covered directly by
  `test_opportunity_cost_primitive.py`'s dedicated Ornithopter fixtures (2a) rather than depending
  on one seed's RNG to exercise it.

#### 2b-follow-up: two coverage-math gaps in `coverage_pass` (menace, trample overflow) — DONE

The two-pass joint assignment shipped correct in aggregate, but `coverage_pass`'s survival
accounting had two mechanic-level gaps that only surface at lethal life — found by the parallel
Bo3 combat-defense scan, each fixed as its own rule-phrased class, not as a card patch.

- **Menace (CR 702.111b / 509.1c) — `coverage_pass` would single-block a menace attacker.** Pass 1
  assigned exactly one cheapest blocker per attacker with no menace awareness, so a defender holding
  ≥2 legal blockers would commit ONE to a menace attacker; the engine then drops that illegal
  one-blocker declaration in its entirety and the attacker connects for full damage — a preventable
  lethal. Fix: an injected `min_blockers_fn(attacker) -> int` (menace ⇒ 2, else 1); if fewer than
  `need` candidates exist, the attacker is left uncovered rather than partially (and illegally)
  blocked. Wired from `decide_blockers` via `_min_blockers`. Test:
  `tests/test_menace_block_requires_two_blockers.py` (`Boggart Brute` 3/2 menace as fixture; two
  `Wall of Omens` — assigns 2 or 0, never 1). Commit `fe1ff76`.
- **Trample overflow (CR 702.19) — a blocked trampler dropped out of the survival sum entirely.**
  `unblocked_damage` summed the power of only attackers NOT in the block map, so a *blocked*
  trampler counted as zero incoming — its through-damage (power beyond its blockers' combined
  toughness) was invisible, and the pass stabilized one attacker too early, leaving a sibling
  unblocked and the defender dead to overflow it never accounted for. Fix: an injected
  `overflow_fn(attacker, chosen_blockers) -> float` that adds each blocked trampler's through-damage
  back into the survival total; non-trample blocked attackers return 0 (unchanged). Wired from
  `decide_blockers` via `_trample_overflow`, reusing the same `max(0, power - soaked)` model already
  in `_score_block_lifespan_delta`. Test:
  `tests/test_coverage_survival_counts_trample_overflow.py` (pure `coverage_pass` unit — 6/6
  trample + 4/4 vanilla vs two 0/3 walls at 5 life must field BOTH walls). Commit `7d2c794`.

Both are `coverage_pass`-local (the pure function in `ai/block_assignment.py`); `optimize_pass`
already modeled trample per-block via `_score_block_lifespan_delta` and needs no change. No new
constants; no card names; ratchets stay at baseline (magic=13, abstraction OK). Full
block/combat suite (`test_menace_block_requires_two_blockers`, `test_coverage_survival_counts_
trample_overflow`, plus the 26-test joint-assignment/emergency/legality set) green.

### 2c. Unify turn_planner/board_eval block-prediction models — DONE

**Problem confirmed, and one assumption from the stretch-goal write-up corrected by investigation.**
Two of the three "how will/should blocks be assigned" implementations named in the original problem
statement were real:

- `ai.turn_planner.CombatPlanner._predict_blocks` — a LIVE, reachable five-phase heuristic (must-
  block / trade-up / trade-even / double-block / chump), consumed by `plan_attack`'s per-attack-
  config EV comparison via `_simulate_combat`. This predicts how the OPPONENT will block MY
  attackers during turn planning — genuinely independent of `ai.ev_player.EVPlayer.decide_blockers`
  (Phase 2b's real two-pass joint-assignment DECISION), using a totally different valuation
  (`ai.evaluator._permanent_value`'s CMC-weighted `.value`, not `ai.clock.opportunity_cost`) and
  fixed trade-ratio constants (`BLOCK_TRADE_UP_VALUE_RATIO`, `BLOCK_EVEN_TRADE_VALUE_RATIO`) instead
  of `ai.clock.score_block_assignment`'s lifespan-delta formula.
- `ai.board_eval.py`'s `ActionType.BLOCK` / `_eval_block` — a THIRD, CMC-weighted block-scoring
  algorithm, matching the stretch-goal write-up's description exactly. **Investigation (grepping
  `engine/`, `ai/`, and `tests/` for every `Action(ActionType.BLOCK, ...)` construction and every
  `_eval_block` reference) found ZERO callers anywhere** — unlike `ActionType.EVOKE`/`DASH` (real
  production callers: `engine.game_runner.DefaultCallbacks.should_evoke`/`should_dash`) and
  `COMBO_NOW` (dispatched from `evaluate_action`'s own body, reachable), `BLOCK` was never
  constructed by any caller and had zero test coverage. Per this program's own repeated finding that
  claims about `ai/`'s block-related code drift from what's actually there (the original stretch-
  goal write-up assumed this was a live third algorithm — it was dead), and per CLAUDE.md's dead-
  code discipline (0a's `engine/priority_system.py`/`oracle_parser.py` deletions, the burn-damage
  cluster's Phlage handler deletion — "same class-size/dead-code reasoning"): building unification
  machinery for code nothing can ever call would be speculative work with no observable effect.
  **Deleted** `ActionType.BLOCK` and `_eval_block` instead — this still directly serves the "single
  owner" goal (a duplicate algorithm sitting in source, even if unreachable, is exactly the anti-
  pattern this program targets), and is lower-risk than leaving it to silently bit-rot alongside two
  newly-unified real implementations.

**Design.** Read `ai.ev_player.EVPlayer.decide_blockers`'s real Phase 2b body in full before
touching anything (per this item's own instruction) and extracted its two-pass shape into
`ai.block_assignment` (`coverage_pass`, `optimize_pass`) — pure, side-effect-free functions with no
dependency on `self`, a real `GameState`, or `CardInstance` specifically. Every mechanic-specific
judgment is injected by the caller as a callable:

- `can_block_fn(attacker, blocker) -> bool` — legality (flying/reach only, matching `decide_blockers`'s
  existing narrower `_flying_ok`; the engine's full CR 702.9b/702.111b enforcement already runs at
  the `CombatManager.declare_blockers` layer per Phase 1b — this AI-side check was never meant to be
  the sole legality gate).
- `cost_fn(blocker) -> float` — ranks candidates ascending for PASS 1 coverage (cheapest-to-lose
  first). `decide_blockers` supplies `ai.clock.opportunity_cost`; `_predict_blocks` supplies the
  pre-computed `.value` already attached to every lightweight `VirtualCreature` (turn-planning's
  fast simulation has no `CardInstance.template` to run `opportunity_cost` against).
- `score_fn(attacker, blocker) -> float` — pairwise lifespan-delta for PASS 2 optimization.
  `decide_blockers` supplies (unchanged) `_score_block_lifespan_delta`; `_predict_blocks` supplies a
  new `_predict_block_score`, which composes the SAME `ai.clock.score_block_assignment` formula
  adapted to `VirtualCreature`'s shape (string keywords, no oracle access) — replacing the old fixed
  `BLOCK_TRADE_UP_VALUE_RATIO`/`BLOCK_EVEN_TRADE_VALUE_RATIO` thresholds entirely, not just relocating
  them.
- `skip_fn(attacker, unblocked_damage) -> bool` (coverage only) and `on_assigned(attacker,
  blocker_ids, used)` (optimize only, non-bounded assignments only) — extension hooks so
  `decide_blockers` keeps its two COMMIT-specific behaviours (RC-2's plating-futile skip; the non-
  emergency double-block-if-needed extension) fully interleaved with the shared loop's own live
  bookkeeping, rather than as a separate post-process that would see a different (already-fully-
  resolved) `used` set and change which blockers are available to a later attacker. `_predict_blocks`
  doesn't use either hook — it has no plating-equivalent concept (no oracle access), and its own
  Phase-4 double-block search (a pair-search extension, not part of the "joint SINGLE assignment"
  either shared caller drives) stays exactly where it was, as a final loop over whatever the shared
  passes above left uncovered.
- `protected_fn(blocker) -> bool` (optional soft preference) — `decide_blockers` supplies
  `_is_protected_piece`; `_predict_blocks` omits it (same "no oracle access" reason as above,
  documented as a deliberate simplification, not an oversight).

`_predict_blocks`'s own emergency-equivalent trigger stays `total_incoming >= board.opp_life` (its
pre-existing bare-lethal-only check) rather than reproducing `decide_blockers`'s three-way OR
(literal lethal / low-life-with-incoming-floor / two-turn-lethal) — the lightweight `VirtualCreature`
projection has no path to `_two_turn_lethal`'s oracle-driven lookahead, and widening the trigger
without also widening the underlying model would be exactly the kind of "half-implement it" the
original stretch-goal note warned against. This is the one place prediction and decision can still
legitimately diverge (a defender at low-but-not-lethal life the real AI would chump but the
prediction won't foresee) — documented, not hidden, and covered by the structural test below, which
deliberately targets this exact shape to make the remaining gap visible rather than assuming it away.

**Files:**
- `ai/block_assignment.py` (new) — `coverage_pass`, `optimize_pass`. No `GameState`/`CardInstance`
  dependency; duck-typed on `.instance_id`/`.power`.
- `ai/ev_player.py` — `decide_blockers` is now a thin wrapper: builds `_cost_fn`/`_score_fn`/
  `_skip_fn`/`_double_block_if_needed` closures over its real board state, calls `coverage_pass`/
  `optimize_pass`, keeps the RC-2 plating-skip check, the emergency-vs-non-emergency gate, and
  `_log_block_assignments` logging exactly as before. Zero change to `_score_block_lifespan_delta`,
  `_is_protected_piece`, `_attacker_equipment_bonus`, `_equipment_breakable`, `_two_turn_lethal`,
  `_racing_to_win`, or `_log_block_assignments` — only the coverage/optimize loop bodies moved.
- `ai/turn_planner.py` — `CombatPlanner._predict_blocks` now calls `coverage_pass`/`optimize_pass`
  instead of its own Phases 1–3; Phase 4 (double-block) is unchanged. New `_predict_block_score`
  (the `VirtualCreature`-shaped `score_block_assignment` composition) and `_OppLifeSnap` (a minimal
  duck-typed `opp_life`-only stand-in for `EVSnapshot`, since `score_block_assignment` only reads
  that one field and `VirtualBoard` doesn't carry a real snapshot). `BLOCK_TRADE_UP_VALUE_RATIO`/
  `BLOCK_EVEN_TRADE_VALUE_RATIO` imports removed (no longer referenced anywhere in the file); the
  constants themselves are left defined in `ai/scoring_constants.py` (no test or other caller
  depends on their removal, and CLAUDE.md's ratchets don't penalize an unused named constant — only
  bare literals).
- `ai/board_eval.py` — `ActionType.BLOCK` and `_eval_block` deleted; `CREATURE_VALUE_CMC_MULT`/
  `CREATURE_VALUE_TOUGH_WEIGHT` imports removed (both were `_eval_block`-only).
- `tools/magic_numbers_baseline.json` — `ai/block_assignment.py` added at `0` (new file, zero bare
  literals — every numeric value it touches arrives via a caller-supplied constant/primitive).

**Tests (failing-first).** `tests/test_block_prediction_matches_decision.py` — the structural test
this item's task explicitly asked for: same board state (one low-toughness-immune attacker, one
lone chump blocker, defender at low-but-not-literally-lethal life — chosen specifically because it
is the one fixture class that reliably distinguished the two OLD independent algorithms; a
symmetric fully-forced-coverage fixture like bug #4's turned out to produce identical output on
both old algorithms by coincidence and would not have been a genuine red-before-green test),
resolved through BOTH `ai.ev_player.EVPlayer.decide_blockers` (the real decision) and
`ai.turn_planner.CombatPlanner._predict_blocks` (fed via `extract_virtual_board` exactly as the real
`plan_attack` call site does), asserting the two dicts are identical. **Verified genuinely red
pre-fix**: old `_predict_blocks`'s bare-lethal-only Phase 1 doesn't fire on this fixture and its
trade-up/trade-even phases require a KILL the lone 1-power chump can never land on a 10-toughness
attacker, so it predicted `{}` (no block) while the real `decide_blockers` (Phase 2b's low-life-
emergency branch, unchanged by this item) chumps anyway — `decided={2: [1]}` vs `predicted={}`.
Green after the fix (both resolve through the identical shared algorithm on the identical inputs).
`tests/test_board_eval_block_dead_code_removed.py` — 3 tests pinning the `ActionType.BLOCK`/
`_eval_block` deletion (enum member absent, function absent, dispatch body no longer mentions
`BLOCK`), all verified genuinely red before the deletion (the enum member and function still
existed).

Full existing block-decision suite re-run green with zero behaviour change for `decide_blockers`
(`tests/test_decide_blockers_joint_assignment.py`, `test_block_scoring_is_lifespan_delta_formula.py`,
`test_chump_block_plating_when_lethal_range.py`, `test_decide_blockers_emergency_gate.py`,
`test_decide_blockers_plating_aware.py`, `test_decide_blockers_protects_engines.py`,
`test_decide_blockers_race_when_winning.py`,
`test_defender_chumps_when_no_block_means_lethal_next_turn.py`) and for `_predict_blocks`
(`test_blocking_equipment_aware.py`, `test_virtualboard_respects_summoning_sickness.py`,
`test_turn_planner_constants_linkage.py`) — the extraction is behaviour-preserving for every
pre-existing pinned case; only the NEW structural test's low-life-chump fixture changes observable
prediction output (by design — that was the bug).

All 4 ratchets clean: `check_abstraction.py` (0 hits, this item touches no card-name/deck-gate
conditionals), `check_magic_numbers.py` (13/13, `ai/block_assignment.py` added to the baseline at 0
— every numeric value flows through a caller-injected callable or an existing named constant),
`check_zone_mutation.py` (102/102, unaffected — pure `ai/` decision-kernel work, no zone mutation
anywhere in this slice), `check_doc_hygiene.py` clean.

**Replay.** `python run_meta.py --bo3 "Boros Energy" "Dimir Midrange" -s 55501` — match completes
cleanly (Boros wins 2-1, matching Phase 2b's own replay note for this exact seed), `[BLOCK-EMERGENCY]`
log lines still fire correctly (`Psychic Frog (1/2) blocks Cat Token (2/1) — lifespan_delta=+0.32`),
confirming the internal-mechanism refactor changed no observable in-game behaviour for the real
decision path. No dedicated replay demonstrates `_predict_blocks`'s corrected prediction specifically
— it is consumed only by `plan_attack`'s internal EV comparison between attack configurations, never
logged directly, so the unit-level structural test is the authoritative verification (same precedent
as 1a's Metallic Rebuke note and the burn-damage/draw-N cluster slices' internal-refactor-only
verification approach).

## Phase 3

Tracked sweep of the long tail. This section accumulates independent Phase 3 slices as they land;
each slice gets its own `###` subsection. Multiple slices may run concurrently on separate
`claude/rules-foundation-phase3-*` branches — see each subsection's own scope note for what it
does and does not cover.

### EFFECT_REGISTRY burn-damage cluster consolidation — DONE

**Problem confirmed exactly as scoped:** `engine/card_effects.py` registered four independent
SPELL_RESOLVE handlers for the "deal N damage to any target" mechanic shape — Lightning Bolt, Lava
Dart, Unholy Heat, Grapeshot — plus one ETB handler (Phlage, Titan of Fire's Fury) with the same
shape bundled into a larger multi-effect trigger. `EFFECT_REGISTRY.register("Card Name", ...)`
calls are literal per-card-name registrations, invisible to `tools/check_abstraction.py`'s regex
(confirmed: `python tools/check_abstraction.py --list` reports 0 hits before AND after this
change — the ratchet does not see registry keys at all), but bespoke per-card logic living
*inside* the handler bodies is the same anti-pattern the ratchet targets for `card.name ==`
checks. This item holds registry-handler bodies to that same bar.

**Research pass (read every candidate in full before designing anything):**

- **Lightning Bolt** (`deals 3 damage to any target`) and **Lava Dart** (`deals 1 damage to any
  target`, plus a Flashback cost handled entirely elsewhere) were already near-identical: walk
  `targets`, apply to the first battlefield creature-or-planeswalker hit via `deal_damage`, else
  face. No per-card quirk beyond the fixed amount.
- **Unholy Heat** has one real quirk: the printed amount is conditional on delirium (2 damage, or
  6 if 4+ card types are in the caster's graveyard) — a card-specific AMOUNT computation,
  orthogonal to target resolution. Its real oracle text is "deals 2 damage to **target creature or
  planeswalker**", not "any target" — confirmed this does not change target-eligibility handling
  (creature-or-planeswalker is exactly what the shared filter already checks; the wording
  difference only matters for the DB's `OracleTextParser.DAMAGE_PATTERNS`, which is not part of
  this migration's dispatch path).
- **Grapeshot** had a real, pre-existing bug: its handler ignored `targets` entirely and always
  mutated `game.players[opponent].life` directly, bypassing `deal_damage` altogether — the spell's
  actual oracle ("deals 1 damage to **any target**") supports targeting a creature or
  planeswalker, but the engine could not do so. Traced the live blast radius before treating this
  as in-scope: `ai/ev_player.py`'s storm-finisher target policy always casts Grapeshot with
  `targets=[-1]` ("Grapeshot always goes face (storm copies auto-target)"), so the bug never fired
  in any current sim — but it is still a real correctness gap (the exact "no single owner" pattern
  this program targets), and fixing it via the shared resolver costs nothing extra once the
  resolver exists. `_handle_storm`'s re-invocation of `_execute_spell_effects` per copy was
  confirmed to re-declare the same `item.targets` on every copy (CR 706.10c: a Storm copy keeps
  the original targets unless the caster is offered new ones, which this engine does not yet
  model) — unaffected by this migration either way.
- **Phlage, Titan of Fire's Fury**'s ETB handler ("deal 3 damage to any target" bundled with a
  sacrifice-unless-escaped clause and a life-gain rider) turned out to be **dead code**: the card
  was removed from `ModernAtomic` entirely by the MTGJSON refresh that followed its Modern ban
  (`d02c543`, already on `main` before this branch). Grepped `tests/*.py`, `engine/*.py`,
  `ai/*.py` for the exact literal `"Phlage, Titan of Fire's Fury"` — the only hit was the
  registration itself. No `CardInstance` can ever carry that name, so the handler was unreachable.
  Deleted rather than migrated (same class-size/dead-code reasoning as 0a's discard-path and
  Annihilator-sacrifice findings) — a 30-line raw-mutation handler removed for zero behavioural
  cost, and one fewer raw `card.zone = "graveyard"` site outside the zone-transfer funnel
  (`tools/zone_mutation_baseline.json`'s `engine/card_effects.py` entry ratcheted 34 → 33).
- **Excluded, with reasoning** (found while grepping the file for every `damage`/`life -=`
  mutation, to make sure the cluster was fully enumerated, not just the four named cards):
  - **Thraben Charm** — a 3-mode charm (damage / destroy enchantment / exile graveyard) whose mode
    selection is entirely EV-scored inline in the handler via `ai.ev_evaluator.creature_threat_value`
    (a separate, pre-existing "engine layer scores" violation, out of scope for this item). Not the
    "deal N damage to any target" shape — it is "destroy target creature" scaled by controlled
    creature count, one mode among three, never "any target".
  - **Kolaghan's Command** and **Pick Your Poison** — modal "choose 2 of N" charms where damage is
    one mode among several, auto-selected by board-state heuristics, not "deal N damage to a
    player-declared any-target".
  - **Wrath of the Skies** — despite the "Skies" name, its oracle is an energy-fueled board wipe
    (destroy each permanent with MV ≤ energy paid); no damage effect at all.
  - **Orcish Bowmasters** and **Walking Ballista** — both deal damage as one part of a richer ETB
    (token creation; X-counter-based repeated removal), and both use bespoke auto-target selection
    (no player-declared `targets` list reaches them the way a cast spell's does) rather than the
    "declared target, else face" shape this cluster's resolver models. Real drift from the shared
    funnel (`damage_marked +=` / `life -=` instead of `deal_damage`) still exists in both — flagged
    here, not fixed, since fixing them means designing (or extending) a second resolver for the
    "auto-picked ETB damage" shape, which is a different mechanic boundary and its own slice.
  - **Archon of Cruelty** and **Tribal Flames** — "loses N life" (not damage; no lifelink/
    prevention interaction, CR 119 does not apply) and a domain-count-derived amount that also
    ignores `targets` (real bug, same class as Grapeshot's), respectively. Tribal Flames'
    amount-derivation is a CDA-adjacent mechanic (`_get_domain_count`-shaped), not the
    fixed/conditional-amount shape this slice's resolver covers — a natural companion to 1d's CDA
    work rather than this burn cluster; flagged for a future slice.
  - **`resolve_attack_trigger`'s own "whenever this creature attacks, deal N damage" branch**
    (`engine/oracle_resolver.py`) has the identical bug class (raw `damage_marked +=` / `life -=`,
    bypassing `deal_damage`) but is not an `EFFECT_REGISTRY` handler at all — it is already a
    generic oracle-driven dispatcher. Checked class size before touching it: 0 cards in any of the
    16 registered decks' 75s have an attack-trigger damage clause (grepped `decks/modern_meta.py`'s
    full card pool against every card's oracle text for `attacks` + `damage` co-occurrence).
    Zero blast radius — left as a documented follow-up rather than fixed speculatively, per this
    program's class-size discipline (0a's identical reasoning for the discard-path and
    Annihilator-sacrifice findings).

**Design:** `engine.oracle_resolver.resolve_damage_to_chosen_target(game, source, controller,
amount, targets)` is the new single owner for "given an already-resolved amount and a declared
target list, apply the damage" — the one piece of this mechanic shape that generalizes across
every card in the cluster. It walks `targets` for the first battlefield creature-or-planeswalker
hit (mirroring CR 601.2c's "any target" = creature/player/planeswalker), applies damage via
`engine.damage.deal_damage` (this program's shared damage primitive; already exists, not
reimplemented — see its own docstring for what it does and does not yet implement, notably a
lifelink hook it reserves but does not populate until the separate damage-funnel activation work
lands), and falls through to the opponent's face on no declared/no eligible target. The AMOUNT
computation stays per-card (fixed constants for Bolt/Dart/Grapeshot, the delirium check for
Unholy Heat) — that half of the mechanic is genuinely card-specific and does not belong in a
shared resolver.

**Files:**
- `engine/oracle_resolver.py` — new `resolve_damage_to_chosen_target`, placed next to the existing
  `_pick_damage_target` (the oracle-driven auto-target picker used by ETB/attack-trigger callers
  that can legitimately re-fire without a declared target list; this new function does not
  duplicate that picker — callers needing the auto-pick fallback resolve a target via
  `_pick_damage_target` themselves and pass it in as an explicit id).
- `engine/card_effects.py` — Lightning Bolt and Lava Dart shrink to one-line calls into the shared
  resolver with a fixed amount; Unholy Heat shrinks to its delirium-amount computation followed by
  one call; Grapeshot shrinks to one call plus its own log line (its only remaining bespoke code:
  which log message to print, not how damage is applied); Phlage's handler and registration are
  deleted (dead code, see above).
- `tools/zone_mutation_baseline.json` — `engine/card_effects.py` ratcheted 34 → 33 (Phlage's raw
  `card.zone = "graveyard"` mutation deleted along with the handler).

**Registrations were not deleted for Lightning Bolt/Lava Dart** despite the generic per-ability-
loop fallback in `spell_resolution.py::_execute_spell_effects` already implementing the same
target-filter+`deal_damage` logic independently (confirmed by inspection: for a single-target
spell the two implementations are behaviourally identical). Kept because
`tests/test_burn_spell_damage_resolves_on_creature.py` (pre-existing, not authored by this item)
pins `EFFECT_REGISTRY.execute("Lightning Bolt", ...)` returning `True` via direct registry
dispatch — deleting the registration would silently reroute through a more fragile fallback path
(the ability-loop parses the damage amount by scanning a synthesized description string for the
first int-parseable word) for no reduction in duplicated logic, since the one-line shrink already
eliminates 100% of the bespoke target-filter code that mattered. The ability-loop's own damage
branch was deliberately left untouched (not also pointed at the new shared resolver): several
Modern "divided damage" spells (Arc Lightning, Electrolyze, Boulderfall, ...) reach that fallback
today and rely on its loop-over-every-declared-target semantics, which the new resolver's
single-target CR 601.2c-shaped return contract does not preserve — unifying that path is a
separate, differently-scoped mechanic ("divided damage among any number of targets") and was
explicitly not pulled into this slice's blast radius.

**Tests (failing-first):** `tests/test_burn_damage_shared_resolver.py` — resolver-unit tests
(declared-creature-target hit, face sentinel, no-declared-targets defaults to face, a non-creature/
non-planeswalker declared target falls through to face, zero-amount no-op, and two "routes through
`deal_damage` rather than a raw mutation" pins via monkeypatching the primitive), plus real-DB
integration tests for the migrated handlers: Lava Dart (new coverage — no prior test exercised its
resolve handler directly), Unholy Heat's two delirium branches (new coverage), and
`TestGrapeshotRespectsDeclaredTarget` (the one genuinely red-before-green case — a declared
creature target was ignored pre-fix, confirmed via `git diff`/manual revert during development;
the face-sentinel and no-declared-targets cases are regression anchors pinning the AI's actual
current call shape, both passing before and after since the bug only affected the untaken
creature-target branch). A `TestDeadCardRegistrationRemoved` case pins the Phlage deletion (asserts
the card is absent from the DB and the handler is no longer registered — regression guard in case
Phlage or a functional reprint re-enters the pool, at which point the registration should come
back pointing at the shared resolver, not the old raw-mutation version).

23 tests, all green after the fix; 2 (`TestGrapeshotRespectsDeclaredTarget::test_grapeshot_damages_declared_creature_target`
and `TestDeadCardRegistrationRemoved::test_phlage_etb_handler_no_longer_registered`) confirmed
genuinely red before the corresponding fix landed.

Full suite: see the session's final run below. All 4 ratchets clean (`check_abstraction.py`:
0 hits before and after — registry keys are outside its regex scope, confirmed by inspection, not
assumption; `check_magic_numbers.py`: unaffected, this slice is `engine/`-only;
`check_zone_mutation.py`: 103 → 102 total, baseline ratcheted down in the same commit;
`check_doc_hygiene.py`: clean). No replay demonstrates this fix specifically — the one real bug
found (Grapeshot's ignored-target case) never fires in any current sim (the AI always casts
Grapeshot face-only, confirmed above), so the unit/integration tests are the authoritative
verification, matching this program's precedent for fixes whose live-game trigger conditions
don't reliably occur under the deck's own AI policy (see 1a's Metallic Rebuke note for the same
pattern).

### EFFECT_REGISTRY draw-N ETB cluster consolidation — DONE

**Problem confirmed exactly as scoped:** `engine/card_effects.py` registered three independent
`EffectTiming.ETB` handlers — Omnath, Locus of Creation; Quantum Riddler; Thought Monitor — whose
bodies were each a single `game.draw_cards(controller, N)` call plus a log line, differing only in
the fixed N. Same duplicated-mechanic smell the burn-damage and nonland-permanent-removal slices
already targeted, even though `EFFECT_REGISTRY.register(...)` calls stay invisible to
`tools/check_abstraction.py`'s regex either way (confirmed again here, 0 hits before and after).

**Research pass (read every registered ETB handler in full before designing anything; also
enumerated the class size from the raw DB, not from memory):**

- Grepped every `game.draw_cards(...)` call site in `engine/card_effects.py` (13 total) and read
  each surrounding handler. Three were pure fixed-amount ETB draws with no rider: Omnath (`draw a
  card`), Quantum Riddler (`draw a card`), Thought Monitor (`draw two cards`) — all `re.fullmatch`
  against the template `"when [^,]+ enters,\s*draw\s+(\w+)\s+cards?\.?"` on the card's own single
  ability paragraph.
- The rest were excluded, with reasoning, none forced into the cluster:
  - **Seasoned Pyromancer** (ETB: discard 2, draw 2, create tokens for nonland discards) — a rider
    (discard-then-draw with conditional token creation) genuinely more complex than "draw N";
    its own raw hand→graveyard mutation (`worst.zone = "graveyard"`) is a separate, pre-existing
    zone-transfer-funnel gap, out of this slice's scope.
  - **Wan Shi Tong, Librarian** (ETB: `+1/+1` counters = opponent's library-search count, then draw
    `X // 2`) — an oracle-derived amount tied to a stateful game counter, not a fixed constant;
    class size 1 (no other card in the DB shares this exact "counters-then-half-draw" shape),
    doesn't meet the "≥10 legitimate cards" bar for its own generalization.
  - **Galvanic Relay**, **Faithful Mending**, **Valakut Awakening // Valakut Stoneforge**,
    **Explore** — all `EffectTiming.SPELL_RESOLVE` (instant/sorcery), not ETB; out of this slice's
    named scope (the task is specifically the ETB cluster). `Explore`'s and `Valakut Awakening`'s
    draw call is bundled with an unrelated rider (extra land drop; hand-selection heuristic)
    anyway, so they would not have qualified even if timing matched.
  - **Griselbrand** — its draw-7 is an *activated* ability (pay life, tap), not a cast or ETB
    trigger; the `SPELL_RESOLVE` handler registered for it is an explicit no-op with a comment
    explaining why. Unrelated mechanic.
- **Class-size check on the generalization itself** (not just the 3 registered cards): grepped
  `ModernAtomic.json`'s full 22k+ card pool for ability paragraphs matching the exact fullmatch
  template used below. **103 real Modern-legal cards** match cleanly (Elvish Visionary, Wall of
  Omens, Mulldrifter, Silvergill Adept, Skyscanner, Gadwick the Wizened, ...) — comfortably above
  CLAUDE.md's "fewer than 10 ⇒ you are patching" threshold. A broader co-occurrence search (`enters`
  + `draw`+`card` anywhere in the same ability paragraph, no fullmatch) hits 142 cards; the
  remaining 39 all carry a rider (life-gain, conditional amount, damage-on-draw, oracle-derived
  count) that the strict template deliberately excludes — see the "no swallowing" reasoning below.
- **Checked whether an existing generic path already covers this, per the task's explicit
  instruction.** `engine.oracle_resolver.resolve_spell_from_oracle`'s own "draw N cards" branch
  (lines ~563-582) already handles the identical CR 121.1 phrasing correctly, but ONLY for
  `EffectTiming.SPELL_RESOLVE` (called from `spell_resolution.py`'s cast-resolution path) — it is
  never invoked for ETB. The actual ETB-context generic fallback,
  `engine.oracle_resolver.resolve_etb_from_oracle` (dispatched identically by both
  `zone_transfer._fire_etb_triggers` and `spell_resolution.ResolutionManager._handle_permanent_etb`
  whenever no card-specific `EffectTiming.ETB` handler is registered), already existed as a module
  with two classifier-tag-gated branches (surveil-N, return-from-graveyard-to-hand) but had **no**
  draw-N branch at all — the gap this item fills.

**Design:** New branch in `resolve_etb_from_oracle` (`engine/oracle_resolver.py`), NOT a new
top-level function — the correct single owner for "an ETB ability that is nothing but a fixed-N
card draw" already exists as this resolver's job; the fix extends it rather than adding a fourth
oracle-parsing entry point next to `resolve_damage_to_chosen_target` /
`_resolve_nonland_permanent_removal` / the SPELL_RESOLVE draw-N branch. Deliberately **stricter**
than the SPELL_RESOLVE sibling: `re.fullmatch` against the WHOLE ability paragraph
(`"when [^,]+ enters,\s*draw\s+(\w+)\s+cards?\.?"`), not a co-occurrence search — an ETB ability
with ANY extra clause (a discard rider, a life-gain rider, a conditional or oracle-derived amount,
a damage rider) must not be silently reduced to "just draw N" and drop the rider; those stay on
their own `EFFECT_REGISTRY` handler or a future dedicated resolver for that rider's own mechanic
shape. `ability.startswith('when ')` (not `'whenever '`) additionally excludes repeatable "whenever
[something else] enters, draw a card" watcher triggers (the Risen Reef class) — those describe a
different permanent's entry, not this one's own one-shot ETB, and firing this branch on them would
misfire every time *any* permanent enters, not just this one. `_WORD_TO_NUM` (the `'a'/'two'/...→
int` table) is now a single module-level constant in `oracle_resolver.py`, shared by both the new
ETB branch and the pre-existing SPELL_RESOLVE branch (previously a function-local dict duplicated
inline) — same "no single owner" fix pattern this whole program targets, applied to the amount
parser itself.

**Files:**
- `engine/oracle_resolver.py` — new `_WORD_TO_NUM` module constant; new draw-N branch appended to
  `resolve_etb_from_oracle` (before its final `return False`); `resolve_spell_from_oracle`'s local
  `word_to_num` now aliases the shared constant instead of redefining it.
- `engine/card_effects.py` — Omnath's, Quantum Riddler's, and Thought Monitor's `EffectTiming.ETB`
  registrations deleted, each replaced by a comment pointing at the generic resolver and the test
  that proved the deletion safe. No zone-mutation-baseline change (none of the three deleted
  handlers had a raw `.zone =` mutation — all three were `draw_cards` + log only).

**Tests (failing-first, verify-before-delete):** `tests/test_etb_draw_n_shared_resolver.py` — 12
tests. `TestResolveEtbDrawNUnit` (7): fixed word-amount draws (one/two) on synthetic fixtures,
`whenever`-watcher-trigger rejection, 5 parametrized "rider must not be swallowed" cases (discard,
life-gain, conditional/kicked, oracle-derived-count, damage rider), and the `draw x cards`
unresolved-amount no-op. `TestGenericResolverCoversRegisteredHandlers` (3, real DB cards, parametrized):
Omnath/Quantum Riddler/Thought Monitor each resolved via the real `_handle_permanent_etb` dispatch
path with **zero** dedicated `EffectTiming.ETB` handler present, asserting both `has_handler(...)
is False` (pins the deletion) and the correct draw count purely through the generic fallback. Per
CLAUDE.md's "verify before deleting" instruction, this redundancy was additionally confirmed with a
throwaway script BEFORE the registrations were removed (temporarily popping each entry from
`EFFECT_REGISTRY._handlers`, firing `_handle_permanent_etb`, confirming the correct draw count, then
restoring the entry) — all three drew correctly through the fallback pre-deletion, so the deletion
in this commit changes zero observable behavior for any of the three cards.

Full suite: 2440 passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed (`--deselect
tests/test_etb_graveyard_return.py`, 431s). All 4 ratchets clean: `check_abstraction.py` 0 hits before and
after (unaffected — registry keys stay outside its regex scope in both directions);
`check_magic_numbers.py` unaffected (`engine/`-only slice, `ai/` untouched); `check_zone_mutation.py`
102 → 102 (no change — none of the three deleted handlers had a raw zone mutation to remove from the
baseline); `check_doc_hygiene.py` clean.

No WR-anchor drift: the three migrated cards' draw amount and log-line semantics are unchanged
(same `game.draw_cards` call, same count, same net effect); the only observable difference is which
code path performs the call, which is not something `ai/`'s decision kernel or `tests/fixtures/
wr_baseline_anchor.json` can see. No dedicated replay needed for the same reason — this is a
pure internal-mechanism consolidation (three duplicate implementations → one shared owner) with a
verified-identical observable outcome, not a behavior fix; the unit/integration tests above are the
authoritative verification, matching this program's precedent for internal-refactor-only slices
(see 1c's deathtouch-marker rewrite, which is the same "same outcome, different mechanism" shape).

### EFFECT_REGISTRY board-sweep cluster consolidation — DONE

**Scope:** the EFFECT_REGISTRY "destroy/sacrifice all [matching] permanents" cluster — a symmetric
mass effect hitting every eligible permanent on every player's battlefield, as opposed to the
target-picking "destroy/exile ONE chosen permanent" shape the removal-spell slice (immediately
above this subsection) already generalizes. This branch was cut from `origin/main` at the Phase 1
tip, before the removal-spell slice merged, and was developed independently of it; the two clusters
share no code (removal's `_resolve_nonland_permanent_removal` picks one legal target and honors
`targets`, board-sweep never receives a meaningful `targets` list at all — it is not a targeted
effect) and merged cleanly with no code-level conflict, only the expected prose-only conflict in
this doc (both slices append a `###` subsection to the same `## Phase 3` section — resolved by
keeping both, this one placed after removal's per this doc's edit-time ordering).

**Research pass (read every candidate in full before designing anything):** grepped
`engine/card_effects.py` for every handler shaped like "destroy/exile all creatures" or
"destroy/exile all permanents [filtered]". Four real candidates, verified against real oracle text
via `CardDatabase` (not assumed from memory):

- **Damnation** — `"Destroy all creatures. They can't be regenerated."` Unconditional, no filter.
  Real bug found: the pre-migration handler had **no indestructible check at all** — it called
  `game._permanent_destroyed` on every creature unconditionally, which itself does not check
  indestructible (neither `_creature_dies` nor `_permanent_destroyed` do — the removal-spell
  slice's commit already documented this exact "indestructible is the caller's job" finding for
  target-picking removal; this slice re-confirms it applies identically to mass removal). Verified
  by extracting the pre-fix handler body into a standalone script and running it against a
  synthetic indestructible creature — it died. Supreme Verdict and Wrath of the Skies already
  checked indestructible correctly pre-migration; only Damnation had the gap.
- **Supreme Verdict** — `"This spell can't be countered. Destroy all creatures."` Same
  unconditional creature-destroy as Damnation, plus an uncounterable-casting property. Confirmed by
  grepping the whole `engine/` tree for `is_uncounterable`/`"can't be countered"` enforcement: none
  exists anywhere. Supreme Verdict is castable-and-counterable like any other spell in the current
  implementation today — a real, separate gap belonging with 1a's counter-tax/uncounterable
  framework, not this sweep-resolver slice. Left as a documented follow-up, not fixed here (out of
  this cluster's mechanic boundary).
- **All Is Dust** — `"Each player sacrifices all permanents they control that are one or more
  colors."` Two real differences from Damnation/Supreme Verdict: (1) **sacrifice, not destroy**
  (CR 701.20a) — indestructible does NOT protect against it, the opposite rule from the other two
  cards in the cluster; (2) filtered by color, not creature-type — and covers ALL nonland
  permanents (artifacts, enchantments, planeswalkers, creatures), not just creatures. Real bug
  found: the pre-migration handler checked `color_identity` (MTGJSON `colorIdentity`, a
  format-legality superset that can include colors a permanent doesn't actually have — e.g. from a
  colored activated-ability cost) instead of `colors` (MTGJSON `colors`, the permanent's actual
  printed color, CR 105.2a) — `CardTemplate.colors` was added in Phase 0b specifically to fix this
  exact class of "is this permanent actually white/blue/etc." check, and All Is Dust's handler
  predates that field and was never updated to use it. Programmatic DB sweep: 1077 nonland cards
  have `colors != color_identity`, confirming this is not a hypothetical edge case.
- **Wrath of the Skies** — `"You get X {E}..., then you may pay any amount of {E}. Destroy each
  artifact, creature, and enchantment with mana value ≤ the amount of {E} paid."` The burn-damage
  cluster's own commit already investigated and excluded this card ("despite the 'Skies' name, its
  oracle is an energy-fueled board wipe... no damage effect at all") — confirming it belongs HERE,
  not there. Two real differences: (1) type-filtered to artifact/creature/enchantment (lands and
  planeswalkers survive even at high X); (2) a resolution-time mana-value ceiling derived from the
  energy actually paid (a genuine per-card quirk — the energy get/spend bookkeeping stays in the
  card's own wrapper, exactly like Unholy Heat's delirium-amount computation stayed in its wrapper
  in the burn-damage slice). Indestructible applies (it's a destroy effect) — already checked
  correctly pre-migration.

No card was excluded from this cluster after verification — all four candidates found by the
grep matched the mechanic shape once real oracle text was checked.

**Design:** `engine.card_effects._resolve_board_sweep(game, card, controller, targets, item, *,
action, types, filter_fn, log_verb, log_noun)` is the new single owner. `action="destroy"` (CR
702.12b — indestructible blocks it) vs. `action="sacrifice"` (CR 701.20a — indestructible does not
apply) is the one genuine rules fork the whole cluster shares; every other difference is captured
by `types` (a token set consumed by `target_solver._matches_type` — the removal-spell slice's own
type-matching primitive, reused directly rather than re-derived) and an optional `filter_fn(game,
controller, permanent) -> bool` for a per-card resolution-time condition (Wrath's mana-value
ceiling via `_wrath_of_the_skies_mv_filter`, All Is Dust's color check via
`_all_is_dust_has_color`). `_board_sweep_pool(game, types)` walks BOTH players' battlefields — a
sweep is symmetric by definition, unlike the removal cluster's `owner_scope="opponent"` default.
Deliberately does NOT route through `target_solver.enumerate_legal_targets` (which layers hexproof
filtering on top of type filtering): a board sweep is not a targeted effect at all (CR 701.6a/
701.20a name no "target"), so CR 702.11d hexproof — which protects only against being the target of
a spell or ability — is correctly irrelevant here. Zone dispatch (`game._creature_dies`/
`game._permanent_destroyed`) is identical for both `action` values; only ELIGIBILITY (which
permanents make it into the sweep) differs by action, never HOW an eligible permanent leaves the
battlefield.

**Files:**
- `engine/card_effects.py` — new `_board_sweep_pool`, `_resolve_board_sweep`,
  `_wrath_of_the_skies_mv_filter`, `_all_is_dust_has_color`. Damnation and Supreme Verdict shrink to
  one-line calls (`action="destroy", types={"creature"}`). All Is Dust shrinks to one call
  (`action="sacrifice", types={"permanent_nonland"}, filter_fn=_all_is_dust_has_color`). Wrath of
  the Skies keeps its energy get/spend bookkeeping (genuine per-card quirk, same pattern as Unholy
  Heat) followed by one call (`action="destroy", types={"artifact","creature","enchantment"},
  filter_fn=_wrath_of_the_skies_mv_filter(x_val)`).
- No `tools/zone_mutation_baseline.json` change — none of the four handlers had a raw `.zone =`
  mutation outside the funnel to begin with (all four already routed through `_creature_dies`/
  `_permanent_destroyed` pre-migration; the bugs found were in ELIGIBILITY checks, not zone-mutation
  bypass), so there was nothing to ratchet down. Per this program's precedent ("don't force a
  reduction that isn't there"), left unchanged.

**Tests (failing-first):** `tests/test_board_sweep_shared_resolver.py` (16 tests) —
`TestDestroySweepRespectsIndestructible` (Damnation spares an indestructible creature — the real
bug, confirmed genuinely red pre-fix by extracting the old handler body into a standalone repro
script and running it against a synthetic indestructible creature; Supreme Verdict regression
anchor), `TestSacrificeSweepIgnoresIndestructible` (All Is Dust still sacrifices an indestructible
COLORED creature — CR 701.20a), `TestAllIsDustUsesRealColorNotColorIdentity` (the `colors` vs.
`color_identity` bug, both a live-game integration case and a direct unit test of
`_all_is_dust_has_color`), `TestBoardSweepHitsBothPlayers` (symmetry), `TestBoardSweepTypeFilter`
(Wrath spares lands), `TestBoardSweepManaValueThreshold` (Wrath's X=0 spares above-threshold
permanents, plus a direct unit test of `_wrath_of_the_skies_mv_filter`),
`TestBoardSweepRoutesThroughDeathFunnel` (Undying still returns a creature Damnation sweeps — CR
702.92d, proving the shared resolver funnels through the same replacement-effect-aware primitive as
every other destroy effect in the engine), `TestRealCardsMatchClusterShape` (oracle-text structured
assertions guarding the migration's assumptions against a future DB refresh), and
`TestBoardSweepPoolTypeMatching` (direct `_board_sweep_pool` unit coverage). The import itself
(`from engine.card_effects import _all_is_dust_has_color, ...`) fails against the pre-migration
module (those names didn't exist), confirming red-before-green at the collection level; the
indestructible-Damnation bug was additionally verified red via the standalone extraction method
above (the pre-fix handler body, run in isolation against the same fixture the new test uses,
destroys the indestructible creature — the post-fix resolver spares it).

Existing tests referencing these four cards (`tests/test_card_features.py`,
`tests/test_gameplan_loader_derives_card_lists.py`, `tests/test_llm_sideboard_advisor.py`,
`tests/test_march_x_from_item_not_lands.py`, `tests/test_panic_gearshift_reaches_play_selection.py`,
`tests/test_removal_tag_generic_derivation.py`,
`tests/test_removal_tag_no_self_bounce_false_positives.py`,
`tests/test_sideboard_manager_slm_dispatch.py`, `tests/test_stax_ev.py`,
`tests/test_sweeper_held_when_single_kill_and_opponent_developing.py`,
`tests/test_target_solver_legality.py`, `tests/test_wrath_x_optimizes_sweep.py`,
`tests/test_x_cost_board_wipe_gate.py`) were checked in full — none exercises the resolve
handlers' actual sweep behavior (they pin tag derivation, cast-time X-selection, or AI
scoring-gate logic upstream of resolution) — all still green, no observable-behavior drift.

All 4 ratchets clean.

**WR-anchor drift (expected, absorbed):** `Pinnacle Affinity vs 4/5c Control` seed 50000 — winner
unchanged (`4/5c Control`), turn count shifted 9 → 10. 4/5c Control's list carries board-sweep
cluster cards; a real correctness change to sweep eligibility (Damnation's indestructible-check fix
being the most likely direct cause, given Affinity's artifact-heavy indestructible-adjacent
permanents) legitimately changes which permanents survive a wipe and therefore how many turns the
follow-up clock takes to close out — exactly the kind of "real AI-visible outcome from a real engine
fix" this program's precedent (0e, the removal-spell slice) already establishes as expected and
absorbable, not a regression. Verified via `git stash` A/B on `engine/card_effects.py` alone: the
anchor test passes against pre-fix code and fails against post-fix code, isolating this branch's
change (not some other pre-existing issue) as the cause. Refreshed via
`python tools/refresh_wr_baseline.py`; snapshot committed alongside this fix.

Full suite: 2444 passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed (after the WR-baseline
refresh above — the pre-refresh run reported exactly 1 failure, the anchor entry described here).
### EFFECT_REGISTRY removal-spell cluster consolidation — DONE

**Problem confirmed, and extended past the original scope.** The prior patch census claimed a
shared `_nonland_permanent_threat` HELPER existed for threat-scoring but no shared RESOLUTION
handler — confirmed accurate (it was purely a `max(..., key=...)` comparator, called
independently from 4 separate handler bodies). But reading every handler in full surfaced two
real bugs the census didn't catch, both instances of this whole program's diagnosis ("no single
owner for what is true right now"):

1. **4 of the 6 handlers (Abrupt Decay, Prismatic Ending, Leyline Binding, March of Otherworldly
   Light) never read the `targets` parameter at all**, even though `spell_resolution.py` already
   threads `item.targets` into every `EFFECT_REGISTRY.execute(SPELL_RESOLVE/ETB, ...)` call, and
   `ai/ev_player.py::_choose_targets` already computes a real target for these removal-tagged
   spells (via `permanent_threat` + `engine_disruption_value`) before casting. The already-made
   choice was silently discarded and a DIFFERENT permanent was picked at resolution using a
   second, less-informed heuristic (`_nonland_permanent_threat`, which has no combo-engine-
   disruption premium). Only Assassin's Trophy and Fatal Push consulted `targets`.
2. **Fatal Push's explicit-target branch, when the chosen target failed its mana-value
   condition, fell through to auto-picking a DIFFERENT creature** instead of doing nothing.
   CR 608.2c: a resolution-time condition ("if it has mana value 2 or less") that a legally-
   targeted permanent fails means the spell/ability has no effect — it does not re-target.
   Verified via an A/B harness reproducing the exact pre-fix function body: targeting a
   mana-value-6 creature with a mana-value-1 bystander also on board destroyed the BYSTANDER,
   not the (correctly-surviving) targeted creature — see the test-writing session's harness in
   this branch's history; the same shape is what `test_destroy_removal_condition_failure_*`
   pins.

**Per-card restriction table** (verified against real oracle text via `CardDatabase`, not
assumed from memory — one entry corrected a wrong assumption going in):

| Card | Zone dest | Type filter | Owner scope | MV condition |
|---|---|---|---|---|
| Abrupt Decay | destroy | nonland permanent | opponent | ≤ 3 (fixed) |
| Assassin's Trophy | destroy | **any permanent, including lands** | opponent | none |
| Fatal Push | destroy | **creature only** | opponent | ≤ 2, or ≤ 4 with revolt |
| Leyline Binding | exile | nonland permanent | opponent | **none** — the domain-scaled cost reduction is a *casting* cost, not a targeting restriction (verified against the real oracle text: "exile target nonland permanent an opponent controls" has no mana-value clause at all) |
| Prismatic Ending | exile | nonland permanent | opponent | ≤ colors of mana spent (Converge), floor 1, cap 5 |
| March of Otherworldly Light | exile | **artifact/creature/enchantment only** (not all nonland — no planeswalkers/battles) | opponent | ≤ X paid |

March of Otherworldly Light was folded into the migration even though the task's illustrative
list named five cards — it is the same mechanic shape (exile, MV-gated, opponent-only) and
Assassin's Trophy/Fatal Push's own per-card type-filter differences already required the
resolver to be type-parameterized, so including it costs nothing and is a second real card
proving the generalization (per CLAUDE.md's "name at least one other card/deck that benefits").

**Design.** `engine/card_effects.py::_resolve_nonland_permanent_removal(game, card, controller,
targets, item, *, zone_dest, types, owner_scope, mv_max_fn, log_verb)` — one function shared by
all 6 cards. Target-legality candidate enumeration (zone/type/owner-scope) is delegated to
`engine.target_solver.enumerate_legal_targets` (a new `_removal_legal_pool` helper builds an
`instance_id → CardInstance` map from it) — never re-derived per handler. **Note**: on this
branch, `target_solver.py` does not yet have hexproof-aware filtering (that lands in Phase 1b,
a separate not-yet-merged branch) — this resolver calls the shared enumeration function rather
than re-implementing zone/type/owner filtering locally specifically so it inherits hexproof
(and any future protection-as-targeting) filtering automatically the moment Phase 1b merges,
with zero changes needed here.

Per-card differences are captured entirely as keyword arguments a thin registration wrapper
supplies — `mv_max_fn: (game, card, controller, item) -> int | None` computes the resolution-time
condition (`None` = no condition at all, correctly modeling Assassin's Trophy/Leyline Binding).
Zone dispatch: `zone_dest="graveyard"` routes through the existing `game._permanent_destroyed`
funnel (`engine/permanent_effects.py`, Phase 0a discipline) — which itself dispatches to
`game._creature_dies` for creature targets, so Undying/Persist replacement still applies — and a
new explicit indestructible check (CR 702.12b) added generically for the whole cluster, since
neither `_permanent_destroyed` nor `_creature_dies` checked it themselves and 2 of the 6 handlers
(Abrupt Decay, Assassin's Trophy) previously had **no indestructible check at all** (a real,
previously-silent rules bug — only Fatal Push had one, and it was per-card duplicated logic, not
shared). `zone_dest="exile"` routes through `game._exile_permanent` — no indestructible check, no
death-replacement funnel, matching CR 700.4/CR 702.92d (exile bypasses Undying/Persist).

**Migration shape per card**: all 6 registrations shrink to a one-line call into the shared
resolver with parsed parameters; none were deleted entirely (each still needs its own
`EFFECT_REGISTRY.register("Card Name", ...)` decorator per the established per-card-registration
pattern this file uses — CLAUDE.md's Phase 3 scoping explicitly keeps those calls, targeting only
the bespoke LOGIC inside handler bodies). Assassin's Trophy keeps a one-line comment noting the
still-unmodeled "opponent may search for a basic land" downside (unchanged, out of scope — no
basic-land-search simulation exists anywhere in the engine yet). `_nonland_permanent_threat`
(the shared threat-scoring comparator) is unchanged and now has exactly one call site (inside the
new resolver's auto-pick fallback) instead of four duplicated ones.

**Abstraction ratchet note**: `tools/check_abstraction.py --list` reports 0 card-name hits and 0
deck-gate hits both before and after this change — its regex only matches `card.name ==` /
`name in {...}` literal-comparison shapes, not `EFFECT_REGISTRY.register("Card Name", ...)`
decorator calls (confirmed by inspection, matching this task's own framing that the ratchet is
blind to this pattern). No baseline change was possible or needed; the real improvement here is
LOGIC consolidation (6 duplicated candidate-filter/target-legality/zone-dispatch bodies → 1
shared function), which the current ratchet cannot measure. `check_magic_numbers.py` (engine/
only touched, `ai/` untouched — unaffected, still 13/13), `check_zone_mutation.py` (all zone
changes route through the pre-existing `_permanent_destroyed`/`_exile_permanent` funnel, no new
raw `.zone =` sites — unaffected, still 103/103), and `check_doc_hygiene.py` all pass clean.

**Tests (failing-first, rule-phrased):** `tests/test_nonland_permanent_removal_mv_threshold.py`
— 17 tests: CR 608.2c condition-failure-does-not-retarget (the Fatal Push bug, plus a positive
control), Fatal Push's revolt-threshold formula, honors-pre-chosen-target-over-auto-pick (the
4-handler bug, plus the auto-pick-fallback regression anchor for when no target was chosen),
destroy-vs-exile zone dispatch (indestructible blocks destroy only, Undying-return-on-destroy,
no-Undying-replacement-on-exile), and one test per card pinning its specific restriction shape
(Assassin's Trophy's no-MV-condition + land-targetability, Leyline Binding's no-MV-condition,
Prismatic Ending's Converge formula + colorless floor, March's X-paid formula + its
artifact/creature/enchantment-only type filter excluding lands). Verified genuinely red pre-fix
two ways: (1) the whole file fails to import against the pre-fix module (the new private
functions don't exist yet — `git stash` A/B on `engine/card_effects.py`), and (2) a standalone
harness reproducing the exact pre-fix `fatal_push_resolve` body against the condition-failure
fixture confirms it destroys the wrong creature (the bystander), which is the specific behavior
the new test's assertion rejects.

**WR-anchor drift (expected, absorbed):** `tests/test_wr_baseline_anchor.py::test_match_outcome_matches_baseline[baseline[4]]`
(Jeskai Blink vs 4c Omnath, seed 50000) flipped winner (`4c Omnath` → `Jeskai Blink`, same
turn count, T13) after this change — verified via `git stash` A/B on `engine/card_effects.py`
alone that it is caused by this fix, not a pre-existing drift. Both decks in that matchup run
cards from this cluster (Jeskai Blink: 3× Prismatic Ending; 4c Omnath: 2× Prismatic Ending,
4× Leyline Binding), so a real AI-decision change here (both handlers now honor a pre-chosen
target instead of silently re-picking) is exactly the kind of legitimate behavior change Phase
0e already established a precedent for absorbing via `tools/refresh_wr_baseline.py`. Traced with
`--verbose` at the same seed: no fizzles, no crashes, both removal spells resolve against legal
in-game permanents. Ran `python tools/refresh_wr_baseline.py`; `git diff
tests/fixtures/wr_baseline_anchor.json` confirms exactly the one expected entry changed (winner
only, turns unchanged) — committed alongside this fix.

Full suite: 2376 passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed. All ratchets clean
(`check_abstraction.py`, `check_magic_numbers.py`, `check_zone_mutation.py`,
`check_doc_hygiene.py`) — no baseline changes to any of the 4 (the abstraction ratchet's regex
does not match `EFFECT_REGISTRY.register(...)` calls at all, so this migration is invisible to
it in both directions).

### CDA coverage extension — PARTIAL (1 of 3 named shapes clears the class-size bar; the
### mechanic it generalizes into covers the named shape plus 2 pre-existing bugs)

**Scope, as assigned:** extend Phase 1d's `detect_power_scaling`/`_dynamic_base_power`/
`_dynamic_base_toughness` with three named CDA shapes — Death's Shadow-class negative
life-scaling, Mortivore/Bonehoard-class "creature cards in all graveyards", Multani-class
land+graveyard compound. Per this program's own discipline ("verify each against real oracle
text... don't force a fix that isn't warranted"), all three were researched against the real DB
before any regex was written.

**Shape 1 — Death's Shadow-class negative life scaling: EXCLUDED, class size too small.**
Verified real oracle text via `CardDatabase`: Death's Shadow is genuinely printed **13/13** (not
1/2, as memory would suggest) with `"This creature gets -X/-X, where X is your life total."` —
confirmed by inspecting the raw MTGJSON part file, not assumed. DB-wide census of the literal
phrase (`-X/-X, where X is your life total`): **2 cards** (Death's Shadow, The Last Ride).
Broadened the search to the whole "life total defines a creature's stats" family regardless of
exact formula (any card where `power`/`toughness`/`+X/+X`/`-X/-X` co-occurs with `life total`):
**18 hits**, but the family splits into genuinely different formulas, each too narrow on its own
to be "the mechanic" and not a coincidental grouping — `-X/-X where X = life total` (2: Death's
Shadow, The Last Ride), `power/toughness = life total` (1: Serra Avatar), `power/toughness = half
the highest opponent life total` (1: Malignus), `power/toughness = your life total minus an
opponent's` (1: Roiling Horror), `power/toughness = 20 minus the highest life total among
players` (1: Scourge of the Skyclaves) — the rest of the 18 hits are unrelated mechanics
(life-total-based activated abilities, life-total-conditional removal, life-total exchange
effects) that don't touch a creature's own continuous P/T at all. Even the most generous possible
grouping (every card whose OWN P/T is ever derived from ANY life total, any formula) totals **6
real cards** — well under CLAUDE.md's "fewer than 10 ⇒ you are patching" bar. No bucket built.
Regression guard: `TestExcludedShapesUncaught::test_negative_life_scaling_not_detected`
(`tests/test_graveyard_count_cda.py`) pins that Death's Shadow stays uncaught (`power_scales_with
== ""`) so a future parser change doesn't silently sweep it into an unrelated bucket.

**Shape 3 — Multani-class land+graveyard compound: EXCLUDED, class size too small.** Verified
real oracle text: `"Multani gets +1/+1 for each land you control and each land card in your
graveyard."` DB-wide census of the exact compound shape (`<type> you control ... and each <same
type> card in your graveyard`): **1 card** (Multani, Yavimaya's Avatar) — no other Modern-legal
card shares the land-specific version. Broadened to ANY type word sharing the same compound-count
structure (battlefield count of type X + graveyard count of cards of type X, contributing to the
SAME creature's own stats): 3 real cards (Multani/land, Moon-Vigil Adherents/creature, Desmond
Miles/Assassin subtype) — a related shape (Cid, Timeless Artificer/Artificer) was excluded from
even that count because it pumps OTHER permanents, not itself (an anthem effect, not a CDA).
3 cards is still well under the bar. No bucket built. Regression guard:
`TestExcludedShapesUncaught::test_land_plus_graveyard_compound_not_detected` pins Multani staying
uncaught.

**Shape 2 — Mortivore/Bonehoard-class "creature cards in all graveyards": the literal shape is
ALSO too narrow standalone (6 cards: Bonehoard, Cruel Somnophage, Lhurgoyf, Mortivore, Necrogoyf,
Nighthowler) — but it is one member of a family the EXISTING "graveyard" bucket (1d) had already
partially built, just hardcoded to one type (instant/sorcery) and one scope (controller's
graveyard only).** Per this task's explicit instruction to check whether the existing bucket "can
be extended with a sub-parameter", generalized its TYPE and SCOPE into parameters the same way 1d
generalized `_get_artifact_count` into `_get_permanent_type_count`'s noun parameter. DB-wide
census of the generalized shape (`power[/toughness] ... equal to ... number of <TYPE?> card(s) in
<SCOPE> graveyard(s)`, TYPE and SCOPE both variable): **26 real cards** — comfortably clears the
bar, and Mortivore/Necrogoyf/Lhurgoyf (the creature-count/all-graveyards slice this task named)
are a proper subset of it, not a separate mechanism. Bonehoard and Nighthowler were investigated
and confirmed OUT of scope for this bucket specifically — both grant `+X/+X` to a DIFFERENT
creature (equipped/enchanted) using this count, which is the equipment/aura-pump mechanism
`_dynamic_base_power`'s `equipped_` tag scan already owns (a different code path from
`power_scales_with` CDA buckets), not a card's own characteristic-defining ability; extending that
scan to recognize this count shape is real, separable follow-up work, not done here.

Generalizing the bucket also surfaced and fixed two real, previously-shipped mismodelings in 1d's
narrow "graveyard" bucket (found via full-DB oracle-text inspection while building the TYPE/SCOPE
extraction, not assumed):

1. **Enigma Drake, Haughty Djinn, Kinetic Augur, and Spellheart Chimera are POWER-ONLY CDAs**
   (`"power is equal to the number of instant and sorcery cards in your graveyard"` — no toughness
   clause at all; each is printed with a real fixed toughness, e.g. Enigma Drake 0/**4**). The old
   bucket applied the SAME graveyard count to BOTH power and toughness unconditionally (`if scaling
   == "graveyard": return effective_printed_toughness() + gy_count()`), inflating these four cards'
   toughness by their graveyard count on top of the printed value. The new bucket's `formula`
   parameter (`sym`/`goyf`/`power_only`, detected from whether the clause has a toughness sub-clause
   at all) fixes this: these four now correctly classify as `power_only`, leaving toughness at its
   printed value.
2. **Magnivore's real oracle is `"power and toughness are each equal to the number of SORCERY
   cards in ALL graveyards"`** — sorcery-only, both-players. The old bucket's single hardcoded
   `_get_gy_instants_sorceries()` resolver counted instant-OR-sorcery in the CONTROLLER'S graveyard
   only — wrong on BOTH axes for this one real member. The new bucket correctly resolves Magnivore
   to `graveyard_count:sym:sorcery:all`.

None of the 8 pre-existing "graveyard"-bucket cards (Crackling Drake, Enigma Drake, Haughty Djinn,
Kinetic Augur, Magnivore, Melek, a Seize the Storm token, Spellheart Chimera) nor any of the newly-
covered 26 are in the registered 16-deck pool today (checked programmatically against
`decks/modern_meta.py`), so this fix has no live matchup blast radius this session — same
"no WR-anchor drift expected" situation 1d itself was in for the graveyard bucket.

**Design:** New helper functions in `engine/oracle_parser.py` — `_normalize_gy_type` (canonical
token from a captured type phrase; "" → `"any"`, spaces → underscores), `_normalize_gy_scope`
(`"your opponents'"` → `"opponents"`; `"your"`/`"all"` pass through), `_detect_gy_formula`
(`sym`/`goyf`/`power_only`, from whether the clause states a symmetric "power and toughness are
each equal to", an asymmetric goyf-style "toughness is equal to that number plus 1", or neither).
New `_GY_COUNT_RE` regex captures TYPE (0-3 words, non-greedy, directly before `card(s)` — same
"parse generically, no enumeration at parse time" discipline as 1d's `permanent_count` word
capture) and SCOPE (`your`/`all`/`your opponents'`). `detect_power_scaling` tries the new
structured regex FIRST inside the existing per-clause loop; if it matches, returns
`graveyard_count:<formula>:<type>:<scope>`. If it doesn't (a compound clause the structured shape
can't parse — Crackling Drake's `"...you own in exile and in your graveyard"`, which breaks the
required `cards? in <scope> graveyard(s)` adjacency), falls through to the ORIGINAL `gy_pattern`
anchor check unchanged, returning the legacy bare `"graveyard"` string — zero behavior change for
the one card (Crackling Drake) that doesn't fit the structured shape. Seize the Storm and Melek DO
match the new structured regex (their "twice"/"plus flashback cards" extra clauses sit outside the
`cards? in <scope> graveyard(s)` span the regex anchors on) but the extra clause's contribution
isn't modeled by either the old or the new bucket — numerically identical output before and after,
just under a more precisely-parameterized bucket name; documented here as a known, unchanged gap
rather than silently left ambiguous.

`CardInstance._get_graveyard_type_count(type_word, scope)` (`engine/cards.py`) is the resolution
side, generalizing `_get_gy_instants_sorceries` (hardcoded instant-or-sorcery, controller-only) the
same way 1d's `_get_permanent_type_count` generalized `_get_artifact_count`. Scope dispatch:
`your` → controller's graveyard only; `opponents` → every OTHER player's graveyard; `all` → every
player's graveyard. Type dispatch recognizes `any`/`card` (no filter), `creature`, `artifact`,
`land`, `nonbasic_land` (land minus `Supertype.BASIC`), `sorcery`, `instant`,
`instant_and_sorcery`/`instant_sorcery`, `enchantment`, `planeswalker`, `permanent` (any card type
except instant/sorcery), and `noncreature_nonland` (Dragonfly Swarm's real shape, found during the
DB census); an unrecognized token falls back to counting every card, matching
`_get_permanent_type_count`'s "don't return a silent 0 for a word this dispatch doesn't know"
discipline. `_dynamic_base_power`/`_dynamic_base_toughness` both dispatch on a
`scaling.startswith("graveyard_count:")` check placed immediately after the (unchanged) legacy
`scaling == "graveyard"` check — power always returns the raw count (`sym` and `goyf` agree on
power; only toughness differs), toughness returns the count for `sym`, `count + 1` for `goyf`
(mirroring `tarmogoyf`'s own `+1` offset), and the untouched `_effective_printed_toughness()` for
`power_only` — the fix for bug 1 above.

**No double-credit risk** (per `tests/test_construct_no_double_credit.py`'s precedent, which this
task was explicitly told to be aware of): the graveyard-census family and the Construct-token
`for each artifact you control` pattern are mutually exclusive regex branches inside
`_dynamic_base_power`/`_dynamic_base_toughness` — a card can only take ONE of the `if
scaling == ...` branches per call, so a graveyard-count creature can never ALSO pick up the
artifact-scaling fallback's bonus. `ai/ev_evaluator.py::creature_threat_value`'s
`THREAT_SCALING_FUTURE_VP` virtual-power bonus (the mechanism that test file guards) only fires on
oracle text matching `for each (artifact|creature|land|card)` — none of the 26 graveyard-count
cards' oracle text contains the literal words "for each" adjacent to a type word in that shape
(verified: the phrase these cards use is "the number of ... in ... graveyard", never "for each"),
so no double-credit path exists between this fix and that AI-side bonus either.

**Tests (failing-first):** `tests/test_graveyard_count_cda.py` (26 tests) —
`TestDetectGraveyardCountScaling` (6, parser unit tests: symmetric/power-only/goyf formula
detection, artifact type, all/your/opponents scope, no-scaling negative control),
`TestGraveyardCountFallbackPreservesLegacyBehavior` (2, Crackling Drake's compound-zone shape
still resolves to the legacy `"graveyard"` string, both a synthetic fixture and the real DB card),
`TestExcludedShapesUncaught` (2, Death's Shadow and Multani regression guards for shapes 1 and 3),
`TestRealCardsGetGraveyardCountBucket` (7, real-DB structured-field integration: Mortivore,
Necrogoyf, Lhurgoyf, Consuming Aberration, Magnivore's fixed-bug assertion, Enigma Drake's
fixed-bug assertion, a DB-wide class-size floor of 15), `TestLiveGraveyardCountPT` (9, live P/T
computation: symmetric both-stats-equal-count, goyf toughness-plus-one, power-only toughness-stays-
printed — the bug-1 fix, made concrete — your/opponents/all scope filtering, any-type counts
everything, nonbasic-land excludes basics, zero-graveyard-cards edge case). Also extended
`tests/test_permanent_count_cda.py`'s pre-existing positive-control test (now asserts the new
parameterized bucket name instead of the old bare `"graveyard"` string, since that specific
fixture — "power is equal to the number of instant and sorcery cards in your graveyard" — is
exactly the power-only shape bug 1 above fixes) and its Murktide Regent regression guard (now also
checks the card doesn't fall into the new `graveyard_count:` family, not just the old literal
string).

Full suite (`python -m pytest tests/ -q --deselect tests/test_etb_graveyard_return.py`): 2506
passed, 22 skipped, 4 deselected, 2 xfailed, 0 failed (332.64s), including the 26 new tests in
this item. All 4 ratchets clean (`check_abstraction.py`: 0 hits, unaffected —
this item touches no card names; `check_magic_numbers.py`: 13/13, unaffected — `engine/`-only;
`check_zone_mutation.py`: 102/102, unaffected — no zone mutation in this item;
`check_doc_hygiene.py`: clean). No WR-anchor drift: none of the affected cards (8 pre-existing +
26 newly-covered) are in the registered 16-deck pool, so `tools/refresh_wr_baseline.py` was not
needed — verified by grepping `decks/modern_meta.py`'s full card pool against every name this item
touches, zero hits (344 unique card names checked across all registered decks' main+sideboards).

**Flaky-test note, investigated and ruled out:** one full-suite run surfaced a single
`tests/test_wr_baseline_anchor.py` failure (`Boros Energy vs Ruby Storm`, seed 50500 — baseline
`Ruby Storm` win, actual `draw`) that did NOT reproduce on any of 5 subsequent runs (4× isolated,
1× via `git stash` A/B against clean HEAD — both pre- and post-fix code pass this exact case every
time it was re-run). Neither deck carries a card this item touches (confirmed by the same
zero-hits sweep above), so there is no causal path from this fix to that matchup's outcome; the
single failure is attributed to transient load on a machine running several concurrent
rules-foundation sessions in parallel (visible via `git worktree list` — 5+ sibling worktrees
active), not a regression. No baseline refresh performed for this entry.

No replay demonstrates this fix for the same reason 1d's own graveyard-bucket
false-positive fix had none: the affected cards aren't in any currently-simulated deck; the
unit/integration tests above (plus the real-DB structured-field assertions) are the authoritative
verification, matching this program's established precedent for fixes whose trigger conditions
don't occur under the current 16-deck pool's own AI policy (see 1a's Metallic Rebuke note, the
burn-damage cluster's Grapeshot note, and 1d's own graveyard-bucket precedent for the same
pattern).

## Verification convention (every item)
Failing test first, rule-phrased name (mechanic, not card — card names live only in fixture-carrier
constants/docstrings). `python -m pytest tests/ -q` full suite (now feasible in ~4-5 min per-session,
not the ~80 min CLAUDE.md describes from before the shared-DB-fixture consolidation) +
`python tools/check_abstraction.py` + `python tools/check_magic_numbers.py` +
`python tools/check_zone_mutation.py`, all at baseline or better. Targeted replay of the specific
audit seed for bugs directly fixed by an item (table above).

## Audit round 2026-09-01 — cascade / reanimation / control / big-mana (4 fresh Bo3 audits)

Four parallel Bo3 audits on matchups the earlier arcs had not stressed. Two came back
clean at the class level (Goryo's vs Amulet s55611 — reanimation timing, Amulet untap,
bounce-lands, Cultivator Colossus live `*/*`; Azorius vs Boros s55612 — counters, Wrath
of the Skies energy-MV threshold, planeswalker loyalty, mobilize end-step sacrifice). Two
surfaced four genuine class-level bugs:

| # | Bug | Matchup | Seed | Root cause | Status |
|---|---|---|---|---|---|
| A | ETB "reveal hand, exile a nonland card" never fires | Eldrazi Tron vs Izzet Prowess | 55613 | reveal-hand chooser lived only on the spell/cast path; unreachable from an ETB trigger | **FIXED** (`ad2077f`) |
| C | Revolt/morbid "this turn" counter leaks into opponent's turn | Living End vs Dimir | 55610 | `untap_step` reset only the active player's per-turn tracking | **FIXED** (`e401e68`) |
| D | Cascade **permanent** source enters before its cascade trigger resolves | Living End vs Dimir | 55610 | `resolve_stack` entered the permanent, then fired cascade → source swept by its own cascaded spell | **FIXED** (`cad4487`) |
| B | Modal "Choose two —" resolves only one mode + drops the mode's target filter | Eldrazi Tron vs Izzet Prowess | 55613 | see below | **TRACKED FOLLOW-UP** (not a one-line fix) |

Below-bar (recorded, not fixed): Living End's own "sacrifice all creatures" moves them to
exile rather than graveyard — a real rules deviation but a ~2-card class (Living End,
Living Death), under the 10-card abstraction bar.

### Bug B — modal "Choose two —" (root cause, why it is a build not a patch)

Kozilek's Command parses to `is_modal=True`, `modal_choose_count=2`, and a correct verbatim
`modes` list (4 entries), but its synthesized `abilities` list collapses to a SINGLE
`CAST "Exile creature"` ability with `target_filter=None`. The modal dispatch in
`engine/spell_resolution.py` (`_execute_spell_effects`) gates interception on
`_n_abilities > modal_choose_count` (1 > 2 → False), so the card falls to the single-ability
path: only the exile mode resolves (should be two modes) and, with `target_filter=None`, it
exiled an unattached Equipment under a "target creature" mode (illegal target type).

The obvious one-line fix — broaden the gate to `len(modes) > modal_choose_count` — was
**empirically rejected**: `resolve_spell_from_oracle(oracle_override=<mode clause>)` returns
`fired=False` for ALL FOUR Kozilek's Command modes (X-scaled Eldrazi-Spawn tokens, "scry X
then draw", "exile target creature with mana value X or less", "exile up to X cards from
graveyards"). Broadening the gate would route the card to a per-mode resolver that no-ops
every chosen mode — strictly WORSE than the status quo (which at least exiles one creature).
The existing gate comment already documented this hazard.

A correct fix is therefore a real build, not a gate flip:
1. Extend the generic per-mode resolver (`resolve_spell_from_oracle`'s `oracle_override`
   path) to cover the missing Command-cycle mode shapes: X-scaled token creation, "scry X
   then draw a card", "exile target creature with mana value N or less" (with the creature
   filter + MV cap enforced), "exile up to X target cards from graveyards", "tap all
   creatures your opponents control". Several other modes (draw, bounce, destroy artifact,
   deal N to any target, target-player-discards) already resolve.
2. Extend `ai/modal._mode_value` beyond its current two shapes (mass-damage, mass-destroy)
   so mode selection is value-driven across the new shapes rather than defaulting to the
   first `k`.
3. Thread `X` and per-mode targeting through the mode resolution.
4. Only then broaden the `spell_resolution.py` gate to `len(modes) > modal_choose_count`,
   with a per-mode-resolvable guard so no card is ever made worse, and regression-test the
   whole Command/charm cycle (Kozilek's, Cryptic — counter-mode excluded, Kolaghan's, the
   charms) plus the existing `test_modal_spell_resolves_one_chosen_mode` (Brotherhood's End).

Class ≈ 15 Command-cycle spells. Real-world sim blast radius is currently narrow (Kozilek's
Command in Eldrazi Tron is the main in-pool carrier), which is why this is scheduled as a
tracked build rather than rushed into this audit round's lean-fix batch.

## Audit round 2 2026-09-01 — artifact/blink, storm, energy/evoke, prowess/reanimation

Four more parallel Bo3 audits. Storm mechanics and prowess/reanimation both verified
clean (Ruby Storm vs Amulet s55621 — storm count, splice, cost-reducer pip floor, ritual
mana, flashback all correct; Izzet Prowess vs Goryo's s55623 — prowess per-spell/wears-off,
Cori-Steel Cutter Flurry, Goryo's haste + end-step exile + Ephemerate all correct). Five
class bugs found and fixed, plus follow-ups.

| Bug | Matchup | Seed | Root cause | Status |
|---|---|---|---|---|
| Ward on a trigger-bound target counters the whole permanent spell | Affinity vs Jeskai Blink | 55620 | ward branch lacked the CR 603.3 permanent-spell exemption its sibling fizzle branch has | **FIXED** |
| Token entry fires no ETB / creature-enters watchers | Boros vs 4c Omnath | 55622 | create_token never called _handle_permanent_etb (undying/persist paths do) | **FIXED** |
| Multi-blocked attacker only takes damage from blockers reached before its power ran out | Boros vs 4c Omnath | 55622 | deal-back lived inside the attacker damage-assignment loop that breaks at remaining<=0 | **FIXED** |
| Flat "Equipped creature gets +N/+M" grants never applied | Izzet Prowess vs Goryo's | 55623 | P/T accessors only handled per-artifact SCALING equipment; no flat-grant branch | **FIXED (P/T)** |
| Practiced Offense silent no-op (mass +1/+1-counter class) | (CI-surfaced) | — | no handler for "put a +1/+1 counter on each creature target player controls" | **ALLOWLISTED** |

### Follow-ups (tracked, not yet built)
- **Equipment keyword rider** — "Equipped creature ... and has trample/haste" (Cori-Steel
  Cutter, Kaldra Compleat, the Sword cycle's protection/triggers). The flat P/T grant is
  now applied; the granted KEYWORDS are still dropped. Same parse-once pattern
  (equip_keyword_grants field) + union in CardInstance.keywords, but it modifies a hot
  accessor and moves combat/haste math — deserves its own test + WR-anchor verification.
- **Mass +1/+1-counter distribution** — "put a +1/+1 counter on each creature you
  control / target player controls" (Practiced Offense, Inspiring Call, ...). Allowlisted
  for now; a real one-shot mass-counter resolver would model it.
- **Coin-flip "deals 1 damage to you" lose-branch** — routes damage to the source
  permanent, not the controller. A single-card variant today (Ral, Monsoon Mage); the
  shared handler is reached by ~29 coin-flip cards but only this one reads "to you", so it
  is below the abstraction bar — recorded, not fixed.

## Audit round 3 2026-09-01 — control mirror, ramp/aggro, artifact/equipment, tempo/energy

Four more parallel Bo3 audits. Two clean (Pinnacle Affinity vs Eldrazi Tron s55632 —
Cranial Plating live-recount, Kappa CDA, improvise, Chalice MV-match, Warp, ward on
Kozilek's Command all correct; Amulet vs Domain Zoo s55631 — Scion of Draco per-creature-
by-own-color keyword grant, Territorial Kavu domain CDA, Amulet ramp all correct). The
token-ETB fix was confirmed clean in-game (Guide of Souls fires once per token, correct
controller, no double-fire). Three class bugs found and fixed:

| Bug | Matchup | Seed | Root cause | Status |
|---|---|---|---|---|
| Owner!=controller permanent never removed on leaving battlefield (near-infinite SBA loop) | Izzet Prowess vs Boros | 55633 | move_card/_find_card_zone resolved source from card.owner; a stolen/opponent-cast permanent sits on the controller's battlefield | **FIXED** (`cfb6730`) |
| amass mints a new Army token instead of growing the existing one | Dimir vs Azorius | 55630 | handler always created a fresh 1/1 (its own comment admitted "simplified") | **FIXED** (`e2a567d`) |
| Planeswalker ETB duplicates its -3 loyalty bounce (double-bounce + token persists in hand) | Dimir vs Azorius | 55630 | re-added Teferi ETB handler re-implemented the -3; a documented fix had regressed | **FIXED** (`6ec9265`) |

### Deferred (AI heuristic / below bar, recorded not fixed)
- **AI under-sizes X on an X-cost "destroy MV <= energy paid" board wipe** (Wrath of the
  Skies cast for X=0 at lethal life). Borderline sub-10 class; there is already
  tests/test_x_cost_board_wipe_gate.py. An AI-scoring lead.
- **AI sweeper sequencing** — a control deck spent its white pips on low-value spells
  (Orim's Chant via Isochron Scepter, Prismatic Ending) before a game-saving Supreme
  Verdict, leaving WW unpayable; compounded by over-valuing "can't cast spells this turn"
  cast on one's own main phase. turn_planner ordering + play-scorer valuation. Lower
  confidence; a --trace would confirm enumerated-but-outscored vs sequenced-out.

## Session tally (2026-09-01): 11 engine fixes across 3 audit rounds
revolt-reset, cascade-permanent ordering, ETB reveal-hand exile, ward-on-trigger-target,
token-entry ETB watchers, every-blocker-deals-damage-back, flat-equipment P/T grant,
owner!=controller zone removal, amass-grows-Army, planeswalker-ETB-no-duplicate-bounce
(+ Practiced Offense allowlist). Every fix failing-test-first, all 7 ratchets at baseline,
WR anchor re-verified after each drift (all drifts confirmed correct). Tracked follow-ups:
modal per-mode resolver, equipment keyword rider, mass +1/+1-counter distribution, coin-flip
"to you", plus the two round-3 AI-heuristic leads above.

## Audit round 4 2026-09-01 — blink/cascade, reanimation/aggro, control/tempo, prison/storm

Four more parallel Bo3 audits. Regression checks all PASSED in-game: cascade-permanent
survives its own Living End (Jeskai/LivingEnd), Thought-Knot Seer ETB strips a nonland card
never a land (Eldrazi/Storm), Teferi phantom-ETB double-bounce absent (4-5c/Prowess),
prowess/Chalice/storm-count all correct. Four class bugs found and fixed:

| Bug | Matchup | Seed | Root cause | Status |
|---|---|---|---|---|
| Non-trample attacker discards damage over a blocker's lethal (lifelink under-gains) | Goryo's vs Zoo | 55641 | assignment capped at lethal; overflow consumed only by the trample branch | **FIXED** (`c4a247b`) |
| Creature-only exile ("exile target creature") illegally targets a planeswalker | Eldrazi Tron vs Storm | 55643 | coarse can_exile_permanent flag; AI gate enumerated all nonland permanents | **FIXED** (`bc67892`) |
| Resolve handler re-charges a Phyrexian-mana cast cost (double life loss) | 4/5c Control vs Prowess | 55642 | Mutagenic Growth resolve handler had a stray life -= 2 (cost is the cast path's) | **FIXED** (`ad368aa`) |
| Mass return fires returned ETBs mid-resolution (before both boards settle) | Jeskai Blink vs Living End | 55640 | _resolve_living_end processed each player fully before the next, firing ETBs inline | **FIXED** (`91842e2`) |

## Session tally (2026-09-01): 15 engine/AI fixes across 4 audit rounds
Round 1: revolt-reset, cascade-permanent ordering, ETB reveal-hand exile.
Round 2: ward-on-trigger-target, token-entry ETB watchers, every-blocker-deals-damage-back,
flat-equipment P/T grant (+ Practiced Offense allowlist).
Round 3: owner!=controller zone removal, amass-grows-Army, planeswalker-ETB-no-duplicate-bounce.
Round 4: non-trample damage overflow, creature-only-exile target restriction, resolve-handler-
no-recharge-cast-cost, mass-return-defers-ETBs.
Every fix failing-test-first, all 7 ratchets at baseline (zone-mutation improved 75->73), WR
anchor re-verified after each drift (all confirmed correct). Tracked follow-ups: modal per-mode
resolver, equipment keyword rider, mass +1/+1-counter distribution, coin-flip "to you", plus the
round-3 AI-heuristic leads (Wrath X-sizing, sweeper sequencing).

## Disguised single-card patches — the narrow-typed-field ratchet (2026-09-02)

The `name == "X"` ratchet is at 0, but card-specific knowledge can still be
laundered past every source-grep guard: parse ONE card's exact oracle wording
at load time into a typed `CardTemplate` mechanic field, and a
`card.name == "Omnath"` conditional becomes a `template.landfall_third_damage`
boolean that only Omnath populates — functionally identical, invisible to the
regex ratchets, and reads as "abstracted" in review. This is the most dangerous
abstraction leak precisely because it *looks* generic (handled via a variable,
in a typed field) while being as narrow as a card name.

**Guardrail:** `tools/check_narrow_typed_fields.py` measures, empirically over
the whole 22.5k-card DB, how many cards actually populate each typed mechanic
field, and flags any whose real class is `<= THRESHOLD` (2) cards — a
single/near-single-card carrier. The logic holds NO card, field, or mechanic
names; the grandfathered set lives in `tools/narrow_typed_fields_baseline.json`
(data, like every ratchet's baseline). A NEW narrow field fails CI until the
author either (a) generalises the parser so the field covers its real Modern
class (>THRESHOLD), or (b) declares it in the baseline with a `reason`, turning
a silent leak into a counted, deliberate exception. The baseline may only
shrink — a field leaves it by ceasing to be narrow.

Wired into `.github/workflows/abstraction-contract.yml` as a standalone step
(after the DB is assembled) plus `tests/test_narrow_typed_fields.py` in the
abstraction-contract pytest set. Grandfathered set at introduction — 17 fields,
`reason` empty (the baseline entry is itself the declaration):

| Field | Cards | Kind |
|---|---|---|
| `landfall_second_mana_colors`, `landfall_third_damage` | Omnath | genuinely unique CDA |
| `has_cc_tap_draw` | Endbringer | genuinely unique |
| `enters_type_counter` | Kappa Cannoneer | candidate for a counters-on-ETB class |
| `has_energy_damage_target` | Galvanic Discharge | candidate for an energy-payoff class |
| `has_mana_value_wipe` | Wrath of the Skies | candidate for the MV-sweep class |
| `is_land_sacrifice_tutor` | Scapeshift | candidate for a land-tutor class |
| `landfall_first_life_gain` | Whispering Snitch | candidate for a landfall class |
| `cycling_watch_trigger_damage` | Drannith Stinger | candidate for a cycling-payoff class |
| `library_search_trigger_draws_card`, `has_library_search_opponent_trigger` | Wan Shi Tong (+Ob Nixilis) | candidate for a search-watcher class |
| `aura_mana_color_chosen` | Utopia Sprawl, Shimmerwilds Growth | 2-card mana-aura class |
| `counters_colorless_only` | Ceremonious Rejection, Consign to Memory | 2-card soft-counter class |
| `limits_opponent_spell_timing` | Teferi ×2 | 2-card static |
| `prevents_graveyard_etb` | Grafdigger's Cage, Kunoros | 2-card static |
| `self_cost_reduction_amount`, `self_cost_reduction_unit` | Emrakul, Hollow One | 2-card cost-reduction class |

Per the standing directive these are **grandfathered, not hand-fixed** — the
ratchet's job is to stop the *next* disguised patch and make the existing ones
counted and visible. The "candidate for a class" rows are the natural Phase-3
generalisation backlog: when a second card of that shape enters Modern, the
parser generalises and the field drops out of the baseline automatically.

## EFFECT_REGISTRY fixed-N burn consolidation — typed field (2026-09-02)

Registry-consolidation arc (post max-effort abstraction audit, which graded
the codebase C+ / not-maxed and flagged ~45-50 nominally-consolidatable
EFFECT_REGISTRY handlers). A handler census corrected that estimate: the
pure-redundancy deletions were already done by the earlier Phase-3 clusters
(Omnath/Quantum Riddler/Thought Monitor draw-N, Preordain, etc.), and the
generic spell-oracle fallback did NOT independently cover damage/removal — so
the remaining thin wrappers were **load-bearing**, not redundant. Real
consolidation therefore means building a guarded generic branch first, then
deleting the handler.

First cluster, done as the verified proof: **fixed-amount face-legal burn**.
`parse_direct_damage_spell` classifies the whole-effect "deals N damage to
<any target / target creature or player / target player / target creature,
player, or planeswalker>" shape (N a printed LITERAL; rider verbs and
keyword-ability cost lines handled — a burn+lifegain/draw or a delirium/
domain/storm-scaled amount is refused, a flashback cost line is not) into the
typed field `CardTemplate.direct_damage_data`. 79 DB cards populate it — a
genuine class, well clear of the narrow-typed-field ratchet's threshold.
`resolve_spell_from_oracle` dispatches off the typed field (no oracle
inspection at resolve time) into the shared owner
`resolve_damage_to_chosen_target`. `lightning_bolt_resolve` (N=3) and
`lava_dart_resolve` (N=1) — the two registered pure fixed-N handlers — are
DELETED, verified redundant with the typed path first
(`tests/test_direct_damage_shared_resolver.py::TestRegisteredBurnHandlersRetired`).
Card-name-registry baseline 95 → 93. Unholy Heat (delirium) and Grapeshot
(storm) keep their handlers — a derived/conditional amount is a different
mechanic the typed field deliberately does not carry. WR anchor unchanged (29
pins hold — the path change is behaviour-identical), all 7 ratchets at
baseline. A bonus: ~30 unregistered fixed-N burn spells that previously
resolved to nothing now resolve through the typed path.

**Remaining clean-deletion candidates** (thin wrappers around an existing
shared resolver, each needs a guarded generic branch built the same way, not
just a delete): nonland-permanent removal (~4 of 6 — Abrupt Decay/Assassin's
Trophy/Leyline Binding/March; Prismatic Ending's colors-spent and Fatal Push's
revolt cap stay bespoke), board-sweep (Damnation/Supreme Verdict/Wrath of the
Skies/All Is Dust). Realistic remaining ceiling ~6-8, not the audit's 45-50.

## EFFECT_REGISTRY board-sweep consolidation — typed field (2026-09-02)

Second registry-consolidation cluster, same pattern as the fixed-N burn one.
`parse_board_sweep` classifies the symmetric "destroy all creatures"
instant/sorcery shape (optional "they can't be regenerated" no-op rider; a
scope/condition — opponents-only, power-gated, nonland, or an extra
resolution rider — is refused) into the typed field
`CardTemplate.board_sweep_data`. 6 DB cards populate it (Damnation, Supreme
Verdict, Wrath of God, Day of Judgment, Doomskar, Vanquish the Horde) — a
genuine class. `resolve_spell_from_oracle` dispatches off the typed field (no
oracle inspection at resolve time) into the shared owner
`card_effects._resolve_board_sweep`. `damnation_resolve` and
`supreme_verdict_resolve` — byte-identical `_resolve_board_sweep(destroy,
{creature})` handlers — are DELETED, verified redundant first
(`tests/test_board_sweep_shared_resolver.py`). Card-name-registry baseline
93 → 91. All Is Dust (color filter), Wrath of the Skies (MV-gated energy
wipe) keep their handlers — a resolution-time parameter the plain typed field
does not carry. WR anchor unchanged (29 pins), all ratchets at baseline.

**Registry surface after two clusters: 95 → 91.** The realistic remaining
clean ceiling is small (~2-4): targeted MV-gated removal (Abrupt Decay,
March of Otherworldly Light are cleanly generic; Assassin's Trophy has an
unmodelled search rider, Leyline Binding is an ETB linked-exile, Prismatic
Ending / Fatal Push are bespoke colors-spent / revolt conditionals). The bulk
of the remaining 91 entries are genuinely card-specific effects, not
duplicated shared-resolver delegations — the audit's "45-50 consolidatable"
estimate did not survive a handler-body census.

## EFFECT_REGISTRY targeted-removal consolidation — typed field (2026-09-02)

Third registry cluster, and the highest-value one: it is a large correctness
fix as well as a consolidation. `parse_targeted_removal` classifies the whole
"destroy/exile target <permanent type> [with mana value N/X or less]" shape
into `CardTemplate.targeted_removal_data` ({action, types, mv}); 102 DB cards
populate it. `resolve_spell_from_oracle` dispatches off the typed field (no
oracle inspection at resolve time) into the shared
`card_effects._resolve_nonland_permanent_removal`, opponent-scoped.

Before this, only ~6 removal spells of this shape had EFFECT_REGISTRY handlers;
the other ~96 (Murder, Dreadbore, Hero's Downfall, Terminal Agony, …) resolved
to NOTHING — a confirmed no-op (a synthetic cast left the target alive). They
now destroy/exile correctly. Abrupt Decay (destroy nonland, MV<=3 literal) and
March of Otherworldly Light (exile a/c/e, MV<=X) — the two cleanly-generic
registered handlers — are DELETED, verified redundant with the typed path
first (`tests/test_targeted_removal_shared_resolver.py`). Card-name-registry
baseline 91 → 89. Non-generic conditions keep their handlers: Assassin's Trophy
(basic-land search rider), Fatal Push ("if it has" revolt), Prismatic Ending
(Converge colors-spent), Leyline Binding (ETB linked "until leaves" exile).

Meta exposure is contained: of the ~96 newly-functional removal spells, only
Shattering Spree (Affinity sideboard) is in a registered deck. WR baseline
anchor unchanged (29 pins hold), all 7 ratchets at baseline. The parser refuses
riders (search/draw/sacrifice/second effect), non-literal conditions, sweeps,
bounce, and land destruction (its own typed path).

**Registry surface across the three clusters: 95 → 89 (−6).** The realistic
clean-consolidation ceiling is now essentially reached — the remaining 89
entries are genuinely card-specific effects or non-generic conditionals, not
duplicated shared-resolver delegations. The audit's "45-50 consolidatable"
estimate did not survive a handler-body census; the real, durable win here was
the ~96-card removal no-op correctness fix that the consolidation surfaced.

## Card-name substring gates hidden from the ratchet (2026-09-02)

`check_abstraction.py` matches `name == "X"` but not substring `"X" in …name`
forms, so two real card-name gates were invisible. Audited both:

- **FIXED** `card_effects.py` nettlecyst_etb: `"Germ" in c.name` searched the
  battlefield for the token it had just created. `game.create_token` already
  returns the created `CardInstance`s — use the return value. Behaviour-
  identical (WR anchor's 29 pins hold), and the gate no longer exists.
- **DEFERRED** `card_database.py:972` `"Living End" in name → tags.add("combo")`.
  No clean typed predicate matches exactly Living End: `has_symmetric_reanimation`
  is True for 32 cards (Liliana Vess, Rise of the Dark Realms, …), so tagging
  "combo" off it would relabel 31 unrelated cards and shift AI scoring. The
  distinguishing feature (a cascade/suspend-cast mass-reanimation payoff) needs
  a purpose-built predicate + WR validation — a tracked follow-up, not a safe
  drop-in. Left as-is rather than risk a reanimation-archetype WR regression.

## Oracle-runtime-parse ratchet: false-negative confirmed, but 14× bigger than estimated (2026-09-02)

The max-effort audit flagged `tools/check_oracle_runtime_parse.py` as a false
negative: it reports total=0, but its detector is a regex keyed to 6 whitelisted
variable-name prefixes, so any oracle-text variable under another name (e.g.
`o = (card.template.oracle_text or '').lower()` in ai/ev_evaluator.py) evades it.
The audit estimated ~17 evading sites (the ones duplicating typed fields).

An AST data-flow detector (taint any local bound from `.oracle_text`/`.oracle`,
then count membership / `.count/.find/index` / `re.*` on it, per function scope)
was written and validated — it correctly finds the evading sites and skips the
parse-once modules. It reveals the true count is **238** (180 excluding the
sanctioned resolve-time oracle-fallback layer: oracle_resolver.py / triggers.py /
spell_resolution.py). Concentrated in ai/ev_player.py (49), ai/ev_evaluator.py
(33), engine/game_runner.py (21). The audit's "~17" was a 10-14× under-count.

SHIPPED (2026-09-03): the AST detector replaced the regex one, baseline
0 -> 180. Scope decision made: the RESOLUTION-fallback layer
(oracle_resolver.py / triggers.py / spell_resolution.py) is the sanctioned
resolve-time oracle-reading counterpart to the parse-once layer (oracle_parser /
card_database / target_solver / ai/card_features), so those three are EXCLUDED
alongside the parse-once modules. The ratchet now polices oracle inspection
LEAKING into scoring / decision / bookkeeping code — the audit's real concern.
The 180 remaining sites are concentrated in ai/ev_player.py (49) and
ai/ev_evaluator.py (33). It is shrink-only: a new inspection fails CI (build a
typed CardTemplate field), and a typed-field migration lowers the baseline via
`--update` in the same commit — so the number tracks migration progress. Unit
tests pin the new detection power (a non-whitelisted variable name and `not in`,
which the old regex missed). The false-negative 0 is retired.

Follow-up (tracked, not urgent): the 180 is a backlog to migrate to typed
fields incrementally — each `parse_*` + typed-field cluster removes some. No
single sweep; opportunistic as scoring code is touched.

## Plot mechanic — deferred-cast-from-exile (CR 702.170) (2026-09-03)

A silently-no-op mainboard mechanic (Slickshot Show-Off in Izzet Prowess; 31 DB
cards) built as a GENERIC class, not a one-card patch — the "what really
abstracts / makes the simulator more robust" direction: it expands the fraction
of Modern the engine correctly models. Plot generalizes the warp/suspend
deferred-cast family: pay the plot cost and exile from hand at sorcery speed,
then cast for FREE as a sorcery on a LATER turn.

- `oracle_parser.parse_plot_cost` → `CardTemplate.plot_cost` (ManaCost), parsed
  once at load, mirroring parse_warp_cost. 31 cards — a genuine class (clears the
  narrow-field threshold), no card names.
- `CastManager.can_plot` / `plot_card` / `can_cast_plotted` / `cast_plotted`
  (+ game_state wrappers, game_runner dispatch for the `plot` / `cast_plotted`
  special actions). Zone moves go through the funnel (`zone_mgr.move_card`); the
  later cast reuses the proven suspend/rebound free-cast path
  (`cast_spell(free_cast=True)`), so no change to cast-cost logic and no new raw
  zone mutations.
- AI (ev_player): enumerates `cast_plotted` (free, scored as the card's cast EV)
  and `plot` — the latter gated to cards NOT castable at full cost this turn, so
  plotting never displaces simply casting now. No new magic numbers (reuses
  `_score_spell`).

Failing-test-first: `tests/test_plot_mechanic.py` (5) pins parse, exile+pay,
not-castable-same-turn, and free-cast-on-a-later-turn. WR anchor 29/29 unchanged
— correctly, since the one registered plot card (Slickshot Show-Off) has plot
cost = mana cost, so casting now is strictly better and the AI never plots it.
The value is the generalized engine mechanic + correct AI gating: any plot card,
in any deck, now resolves correctly. All 7 ratchets at baseline.

Deferred-cast family follow-ups (same primitive): hideaway, emerge, and bestow's
graveyard cast; a full unification of warp/suspend/plot into one
deferred-cast-from-exile primitive is the tracked next step.

## Equipment keyword-grant rider (CR 301.5c / 613) (2026-09-03)

Equipment could grant P/T but not KEYWORDS — "Equipped creature has trample and
haste" was a silent no-op for ~172 DB Equipment (Shadowspear, Cori-Steel Cutter,
Lavaspur Boots, Skateboard, the Sword cycle, …), so an equipped creature never
gained trample/haste/lifelink/etc. Haste changes attack legality; lifelink/
trample change combat maths — a real robustness gap in every equipment deck.

Built as a generic class, mirroring the existing equip P/T-grant machinery:
`oracle_parser.parse_equip_keyword_grant` → typed field
`CardTemplate.equip_keyword_grant` (frozenset of Keyword.value strings; 172
cards, no card names), applied in `CardInstance.keywords` via the same
`equipped_{iid}` instance-tag scan the P/T grant uses (no oracle re-parse). Only
unconditional grants match — a conditional ("as long as", "if") is skipped so the
sim never grants a keyword it cannot gate. DamageSource hooks (has_lifelink,
has_deathtouch) follow automatically since they read off `keywords`.

Failing-test-first: `tests/test_equipment_keyword_grant.py` (8) — parse
(multi-keyword, alongside-P/T, conditional-skip, no-clause), grant applied,
haste/trample visible, pure-P/T grants nothing, grant removed on unequip. WR
anchor drifted 2 pins, both traced by game log solely to keyword-granting
equipment (opponents run none): Izzet Prowess (Cori-Steel Cutter trample+haste)
closes a turn faster; Affinity vs Domain Zoo flips to Affinity as its
Skateboard (haste) + Shadowspear (trample+lifelink), tutored via Urza's Saga,
finally function. Anchor refreshed to the corrected outcomes. All 7 ratchets at
baseline.

## Static team keyword anthem (CR 611) (2026-09-03)

"Creatures you control have trample/vigilance/…" statically granted no keyword —
only Scion of Draco's colour-conditional grant had a bespoke handler; the
unconditional class (~121 Modern permanents: Archetype of Aggression, Archetype
of Imagination, the anthem cycle, …) was a silent no-op, so a creature next to
such an anthem never gained the keyword.

Built generically on the existing continuous-effects machinery (which already
supports keyword lord effects — Scion proves it): `parse_team_keyword_grant` ->
typed field `CardTemplate.team_keyword_grant` ({keywords, others_only} | None;
121 cards, no card names), registered as a `create_lord_effect` on ETB in the
generic `spell_resolution._handle_permanent_etb` hook (alongside modular /
aura-attach). The manager re-derives it each recalculate() (later-entering
creatures are covered) and retracts it when the source leaves. Unconditional
grants only — a colour/type condition ("if it's green", "that's a Cat") or the
one-shot "gain <kw> until end of turn" pump (Craterhoof, already handled via
team_pump_data) is deliberately NOT matched here.

Scope note: an initial keyword-word scan suggested 5 registered-deck hits, but
they were false positives — Kaheera's grant is type-restricted, Momo's is a
trigger, Shang-Chi/Tyvar's is "activate abilities as though they had haste"
(not combat haste). The stricter parser correctly excludes all four, so this
class has NO current registered-deck impact — it is pool-level correctness (a
whole class the engine now models), like the equipment keyword grant's DB tail.
WR anchor unchanged (29/29). Failing-test-first (tests/test_team_keyword_anthem.py,
8), all 7 ratchets at baseline.

## Combo hold applied to a resource nobody declared (2026-09-04)

Root cause of the Creatures Toolbox 10.4% outlier (worst deck on the
2026-09-04 matrix), found via `--bo3 "Creatures Toolbox" "Boros Energy" -s 50000`
+ `--trace`: the deck never cast any of its 8 tutors (Green Sun's Zenith,
Nature's Rhythm) or Fiend Artisan — every one scored ≈ −46 while the
projection said −0.1 … +3.9. `card_combo_modifier` was charging
`−COMBO_NON_READY_POTENTIAL_FALLBACK × combo_value` (≈ −50) to each of them.

Two generic defects, both in `ai/combo_calc.py`:

1. `_find_resource_zone` fabricated `("graveyard", 0, 0)` for any gameplan with
   no positive `resource_target`. The graveyard assessor then reported
   `is_ready=False` whenever the payoff was not in hand — a permanent,
   unsatisfiable "not ready" for a resource nobody declared — so every
   payoff-role card in hand ate the full non-ready hold penalty. Three
   registered combo-archetype decks have no FILL_RESOURCE goal and were all
   in the trap: Creatures Toolbox (10.4%), Hollow One (25.4%; its 5-cmc
   payoffs never "affordable" against printed cmc), Grixis Reanimator.
   Fix: return zone `None` → `assess_combo` yields the null assessment →
   modifier is 0 and the projection scores the cards.
2. `card_combo_role`'s tag fallback mapped every `tutor`-tagged card to
   `'payoff'`. A tutor is finisher ACCESS, not the finisher; the
   TUTOR-AS-FINISHER-ACCESS branch already scores tutors with a real target
   before any role check, and the module's own trailing comment says tutors
   are projection-scored. Fallback is now `'dig'` (card selection). Decks
   whose gameplans declare tutors in roles (Storm's Wish, Amulet's GSZ/Pact)
   are unaffected — the role cache wins.

Failing-test-first (`tests/test_combo_calc.py`): no-declared-resource → no
zone; end-to-end null assessment with 0 modifier for a payoff-role card;
tutor-tag fallback is not `'payoff'`. The old `test_default_when_no_target`
pinned the fabricated default and was replaced. Replay after: 6 tutor casts
in the match (0 before), Craterhoof line closes G2 on T6. WR anchor: one
turn-only drift (Broodscale Bloodchief vs Creatures Toolbox s52500, 10 → 9,
winner unchanged) refreshed. All 7 ratchets at baseline.

## Storm-chain mulligan backup rule applied to every combo deck (2026-09-04)

Second layer of the Creatures Toolbox outlier (after the combo-hold fix
above, the field only moved 10.4% → 18.3%), found via
`--bo3 "Creatures Toolbox" "Dimir Midrange" -s 50000`: G2 mulliganed a 3-land
7 with Devoted Druid, Fiend Artisan, Green Sun's Zenith and Leyline down to 5
with reason "no cost reducer and no ritual+cantrip+finisher backup".

That keep rule in `ai/mulligan.py` fires whenever a combo-archetype gameplan
declares `always_early` and none of those cards is in hand, and then demands
the STORM chain shape (ritual + cantrip + finisher) as backup. The shape is
storm's: rituals make the mana, cantrips find the next spell, a storm finisher
closes. A graveyard / mana / undeclared-zone combo deck has no such chain, so
the rule shipped good hands back. Blast radius — every non-storm combo
gameplan with an `always_early` list: Amulet Titan (22.7%), Creatures Toolbox,
Goryo's Vengeance, Hollow One (25.4%), Instant Reanimator.

Fix: the gate now also requires the gameplan to declare a "storm" combo
resource zone (`_find_resource_zone` — the same primitive `assess_combo`
reads). `requires_combo_backup` (the per-deck policy flag) is unchanged;
it still gates the rule, but the rule only has a shape to test against a
storm chain. Failing-test-first
(`tests/test_mulligan_chain_backup_shape_is_zone_derived.py`): the replay
hand is kept under a non-storm combo gameplan; a Ruby Storm 7 with draw +
two finishers and no ritual is still rejected with the same reason.
Replay after: G2 keeps the 7 and survives to T11 (was T7). Field n=5:
Creatures Toolbox 18.3% → 23.3%. WR anchor: three turn-only drifts (winners
unchanged; Creatures Toolbox ×2, Hollow One) refreshed. All 7 ratchets at
baseline.

### Residual after both layers (tracked, not patched)

Post-fix replay `--bo3 "Creatures Toolbox" "Dimir Midrange" -s 50000`: G1 is
lost from 20 life on T6 with no decision error left to point at — the deck
spends T3–T5 on Eternal Witness (only fetchlands in the yard), a second
Leyline and Tyvar while Frog + Bowmasters + Subtlety race, and its only
untapped blocker dies to Drown in the Loch. The deck's actual kill (Devoted
Druid + Vizier of Remedies → unbounded mana → Walking Ballista) is not
modelled: the engine has no counter-placement replacement class ("if a
-1/-1 counter would be put on…, instead …"). That is a mechanic build
(Vizier, Solemnity, Melira, Hex Parasite shells), not a Toolbox patch — it
is the next root cause for this deck and is left here rather than opened
as a third diagnostic. Amulet Titan (22.7% → 19.2% at n=5, within noise)
shares the mulligan fix but not the residual; its own replay root cause is
still owed.

## Counter-placement replacements + unbounded untap-engine shortcut (2026-09-04)

The Creatures Toolbox residual above, built as two mechanic classes.

**Counter-placement replacement (CR 614.1c).** "If one or more <kind>
counters would be put on <a creature/artifact/permanent you control | this>,
<that many plus one | that many minus one | twice that many> are put on it
instead." 14 Modern cards (Hardened Scales, Conclave Mentor, Winding
Constrictor, Ozolith the Shattered Spire, Kami of Whispered Hopes, Mauhúr,
Branching Evolution, Corpsejack Menace, The Earth Crystal, Loading Zone,
Vizier of Remedies, Mowu, Caradora, Michelangelo) — none was modelled; every
counter-doubling shell was under-counted and the "-1/-1 minus one" shape
that frees a "put a -1/-1 counter: untap" mana engine never fired. Parsed
once (`parse_counter_placement_replacement` → typed
`CardTemplate.counter_placement_replacement`) and applied inside the ONE
counter funnel (`CardInstance.add_plus_counters` / `adjust_counters` →
`replaced_counter_amount`), so activation costs, put-counter effects,
persist (now routed through the funnel too), modular and enters-with-N all
see it. CR 614.5 each applies once; CR 616.1 additive before multiplicative
(controller-optimal for the printed shapes). All 14 DB parses verified.

**Unbounded untap-engine shortcut (CR 726.4).** Rule 9 refuses to stack a
free repeatable ability (nothing depletes → nothing terminates the loop).
That is a sim safety valve, not a rule of Magic: with the counter cost fully
replaced away the loop is legal and a paper player shortcuts it. The engine
now reads the REPLACED cost amount in rule 9 (`_cost_depletes_a_resource`),
keeps the free untap off the stack (no spin), and exposes the loop as mana:
`ActivationManager.unbounded_mana_engines` names free self-untapping mana
sources; `untapped_mana_capacity` credits `LOOP_SHORTCUT_MANA`
(= 4 × starting life, the finite iteration count the shortcut proposes) per
engine; `ManaPayment._run_loop_shortcut` executes exactly the iterations a
cost falls short by (untap paying the replaced-to-zero cost through the
funnel, tap into the pool), logging one "CR 726 shortcut" line. Colours the
engine cannot make still fail payment.

**Toolbox awareness.** `would_complete_unbounded_engine(game, idx, template)`
is a pure rules query (phantom permanent, engines before/after, both
directions: replacement source freeing an existing untapper, or untapper
freed by an existing replacement). `default_tutor_rank` — the engine's
delivery ranking every tutor picker and resolver shares — leads with it, and
both tutor valuations (`ai/activation_ev.py` activated X-tutor,
`EVPlayer._gate_x_tutor_payoff` cast X-tutor) credit an engine-completing
delivery at `LOOP_SHORTCUT_MANA` mana units instead of its printed mana
value: what the piece actually delivers.

**What it took for the AI to actually use it** (each step replay-diagnosed,
`--bo3 "Creatures Toolbox" "Boros Energy" -s 50000` + `--trace`, and each a
generic gap, not a Toolbox rule):

1. *Zero-power creatures never cast.* `position_value`'s "I have no clock,
   opponent does" branch scored `-opp_clock` — the inverse of its own
   comment — so deploying a 0/2 that stretched the opposing clock from 17 to
   51 turns projected at -34. docs/diagnostics/2026-08-30_clock_sign_inversion_fix_falsified.md
   confirms the defect and falsifies only the prediction that repairing it
   (with the sentinel cliff, together) lifts creature-light control. This
   ships the SIGN HALF alone, as the mirror of the winning branch
   (`-CLOCK_LETHAL_ADVANTAGE_CAP / opp_clock`), with no WR claim for control
   attached. The panic-gearshift fixture that had frozen the inverted sign
   as an invariant (named in that doc) drops the invariant; the rule it
   tests (the multiplier never demotes) is unchanged.
2. *Tutor delivery blind to the engine.* The engine's `default_tutor_rank`
   leads with `would_complete_unbounded_engine`, but the AI callbacks that
   actually decide (`choose_tutor_delivery`, `choose_sacrifice_victim` in
   ai/activation_ev.py) ranked by `creature_threat_value` alone — so a
   two-mana Vizier lost to any 2/2 and the Druid was the first creature fed
   to Fiend Artisan. Delivery now credits an engine-completing candidate at
   `LOOP_SHORTCUT_MANA`; sacrifice routes through `opportunity_cost`.
3. *`opportunity_cost` ignored mana and engines.* The one primitive that
   prices "what the board gives up" (chump, sacrifice, discard) gained a
   mana-production term (`tap_mana_units × mana_clock_impact`) and an
   engine term — `engines_lost_if_removed` × allowance, so the replacement
   source that frees three untappers costs three engines and one of three
   untappers costs one.
4. *Blocks priced a dead blocker at its power.* The lifespan-delta block
   scorer charged a killed blocker only through `my_power_after`, so a
   0-power engine half chump-blocked a 2/1 at 16 life one turn after being
   tutored. Its non-combat worth (`noncombat_opportunity_cost`) is now
   charged to the block post-state as virtual life — the scale
   `life_as_resource` already converts to survival turns — and the
   double-block helper picks the cheapest adequate second blocker instead of
   the first in list order.
5. *Attacks spent the engine half for two points.* Both on-board-lethal
   shortcuts (`decide_attackers`' early return and the combat planner's)
   sent every creature with combat value; Vizier attacked into a 3/3 with
   the opponent at 1 and the engine died with it. Each now sends what is
   NEEDED: a creature whose non-combat worth exceeds its power stays home
   when the rest still reach lethal, and the planner's per-creature
   `value` carries `noncombat_opportunity_cost` so its trade-down and
   board-presence terms see an engine half as the loss it is. The
   racing/desperate "send everything" branch applies the same gate.
6. *An untapped-land life payment must buy the spell it enables.* Once
   life was priced honestly the bare pay/skip delta always read as a loss
   (a turn-two sweeper went uncast behind a tapped shock — anchor pin
   Pinnacle Affinity vs 4/5c Control s50000). `decide_optional_cost`
   now offers a pay-variant per MARGINAL spell (cheapest-first packing
   over `spells_enabled_by_one_more`, colours checked per scenario),
   projected through the cast projection. The sentinel-CLIFF half of the
   clock fix was also built, A/B-measured (Creatures Toolbox 17.5% ->
   11.7%, Jeskai Blink 30.8% -> 27.5%, n=5 same seeds) and reverted;
   recorded as an addendum on the 2026-08-30 falsified diagnostic.
7. *Cast feasibility saw the engine's units but not its colours.* With the
   loop live (`mana=85` in the trace) Craterhoof {5}{G}{G}{G} was not even
   a candidate: `_mana_source_units` (the colour-pip feasibility builder)
   lacked the engine. It now appends the engine's printed units per
   iteration, so feasibility, estimate and payment agree on quantity AND
   colour.

Failing-test-first: `tests/test_counter_placement_replacement.py` (12),
`tests/test_unbounded_untap_mana_engine_shortcut.py` (15),
`tests/test_tutor_credit_for_engine_completing_piece.py` (4),
`tests/test_position_value_no_clock_sign.py` (3),
`tests/test_opportunity_cost_prices_mana_and_engines.py` (5),
`tests/test_block_prices_blocker_noncombat_value.py` (5). All 7 ratchets at
baseline (the new typed field populates 14 cards, well above the narrow
threshold). Replay after: the CR 726 shortcut fires from T5 ("loops Devoted
Druid ×8 — cast Craterhoof Behemoth"), Vizier is tutored on sight of a
Druid, and the engine halves are neither chumped nor sacrificed while a
fresh body is available; the seed-50000 match vs Boros goes 2-0 (was 0-2).

WR anchor: 12 drifts, 5 winner flips, each replayed through the anchor's
exact harness (`run_meta._run_game`, deadline neutralised, verbose) in the
pre-change worktree and the final tree and diffed at the first divergence.
All five stem from pricing life honestly (the sign half): 4/5c Control at 11
life facing five power plays its shock tapped and survives at 1 instead of
dying (s50500); Hollow One pays two life to deploy a two-drop and attacks
(s53500); Boros Ponza keeps two life, dashes instead of hard-casting, and
wins (s51000); Azorius bounces with Teferi instead of a four-mana Wrath in
the mirror (s50000); 4/5c takes a five-permanent Wrath on T3 over a
three-permanent one on T2 and loses that single game (s50000) — a
defensible choice, not a defect. Refreshed.

Field at n=5 (same seeds, quiet box): Creatures Toolbox 17.5% (matrix
baseline 10.4%), Jeskai Blink 27.5% at the final code (30.8% before the
untapped-payment change, 30.2% matrix baseline — inside n=5 noise, no
regression signal). The deck is still
out of band: with Vizier a one-of, the engine assembles in a minority of
games; the next lever is a fair one (a second Druid untapper / the Soul
Cauldron line), tracked, not patched.

## Domain Zoo band loop (2026-09-05)

Self-paced loop until Domain Zoo's flat field WR is inside [50, 65]. Stop
gate (noise-aware): n=20 Bo3 on the 50000 matchup grid inside the band AND no
single cell ≥85% AND reproduced on the 40000 matrix grid, on pushed CI-green
code. Lanes closed before this loop and not reopened: the decklist swap
(falsified), the clock sentinel-cliff (falsified twice), the "defensive fixes
lift control" prediction (falsified), Zoo-vs-4/5c (structural for that list).

### Iteration 0 — baseline on `0335e9e` (measurement only)

`run_meta.py --field "Domain Zoo" -n 20 --parallel`, quiet box, offline
scorer. **Flat 67.5%** (this is the loop's baseline; the 65.6% matrix
headline is on the other seed grid and is not comparable). Cells ≥85%:
Goryo's Vengeance 100, Creatures Toolbox 95, Affinity 90, Azorius Blink 90,
Amulet Titan 85, Boros Ponza 85, Grixis Reanimator 85, Hollow One 85. At
band or below: Ruby Storm 25, Eldrazi Tron 25, 4c Omnath 45, WST v2 /
Broodscale 50, Boros Energy / Instant Reanimator 55, Jeskai Blink / Living
End / 4/5c / WST 60, Pinnacle Affinity / Eldrazi Ramp 65. Stop gate: not
met. Target for iteration 1: Azorius Blink (90%; registered list whose
defensive plan exists but is switched off — `archetype: aggro` in its
gameplan makes `holdback_applies` False).

### Iteration 1 — Azorius Blink (90%): the holdback lead is falsified by the log

`--bo3 "Azorius Blink" "Domain Zoo" -s 50000` (Zoo 2-0, T9/T8). Blink DOES
deploy its instant-speed interaction on Zoo's turns — T3 both games it
evokes Solitude on Scion of Draco at Zoo's begin-combat (G2 with Ephemerate
in response, the intended line). The archetype-gated holdback hypothesis is
not what this matchup shows; not pursued. What the log shows instead, both
defending-side and class-sized:

1. **March of Otherworldly Light cast into nothing.** G1 T3 (Zoo's turn):
   "Cast March (1W) (X=1) → Resolve → graveyard", no exile line — Zoo's board
   held Leyline Binding (MV 6) and Scion (MV 12), nothing of MV ≤ 1. G2 T6
   and T7: "Cast March (W)" (X=0) the same way. An X-bound targeted removal
   spell ("… with mana value X or less") is cast with an X that reaches no
   target, burning the card and the mana. Class: every X-bound targeted
   removal spell; the fix is the delivery-conditioned discipline the X
   creature tutor already has (`pick_creature_tutor_x_value`): choose X to
   reach the best legal target, and no candidate when none is reachable.
2. **Witch Enchanter's enters-the-battlefield destroy never fires.** G1 T4:
   "Cast Witch Enchanter → Resolve", no destroy line, while Leyline Binding
   (an enchantment) held Ocelot Pride in exile. `CardTemplate` carries no
   typed field for the "when this enters, destroy/exile target
   <artifact/enchantment/…> an opponent controls" ETB shape
   (`targeted_removal_data` is the SPELL class from the 2026-09-02
   consolidation). A blink deck's whole plan is re-triggering exactly this
   class with Ephemerate.

Built (both failing-test-first, rule-phrased):

- **ETB targeted removal class** (`tests/test_etb_targeted_removal_class.py`,
  10): `parse_etb_targeted_removal` → typed
  `CardTemplate.etb_targeted_removal_data` (same dict shape as the spell
  class plus owner_scope/optional; 141 pool cards carry the shape, the
  naturalize subclass ~24), dispatched from `resolve_etb_from_oracle`
  through the shared `_resolve_nonland_permanent_removal` — so target
  legality (CR 601.2c), indestructible (702.12b), the printed mana-value
  ceiling (608.2c) and the zone funnels are one code path. The linked
  "exile … until this leaves" shape stays on its own mechanic.
- **X-bound target legality + X payment** (`tests/test_x_bound_target_legality.py`,
  6; 19 pool cards bound a target by X): `target_solver` now attaches a
  trailing "with mana value N/X or less" clause to the battlefield
  requirement it follows (`max_mana_value_is_x`), `enumerate_legal_targets`
  / `has_legal_target(_for_spell)` take an `x_ceiling`, `can_cast` supplies
  it from the new `CastManager.affordable_x` (one formula: (capacity −
  fixed cost) // X pips), the cast path pays X = the chosen target's mana
  value (CR 601.2b), and both AI target branches filter candidates by the
  same ceiling. A spell whose affordable X reaches no target is no longer
  castable, and the AI no longer picks the highest-MV permanent regardless
  of X.

Neighbouring suites 362 green; all 7 ratchets at baseline (the new typed
field populates 141 cards); WR anchor 29/29 unchanged.

Replay after (`--bo3 "Azorius Blink" "Domain Zoo" -s 50000`): both fixes fire
in-game — "T4 Witch Enchanter destroys Leyline Binding", "T5 Cast March
(X=2) → exiles Territorial Kavu" (X paid for the target's mana value, not
the whole pool). The match is still 0-2 (G1 lasts T11, was T9): one seed,
not the measurement. Targeted n=20 follows.

**Measurement (n=20, 50000 grid, quiet box):** Azorius Blink vs Domain Zoo
5% / 95% (baseline cell 10 / 90) — no movement; code iteration 1 of the
loop-break count. The deterministic sideboard plan for this pairing is
"+2 Spell Pierce, −2 Witch Enchanter": the naturalize body that now works
is boarded OUT against a deck running 4 Leyline Binding + 4 Leyline of the
Guildpact, so games 2 and 3 never see the fix. That swap is the next
defending-side lead (the solver's card valuation vs the opponent's
permanent density), subject to the class-size check.
Regression guard: Azorius Control vs Domain Zoo 20 / 80 at n=20 — identical
to the baseline cell; no regression from iteration 1.

Follow-up built (`tests/test_sideboard_values_permanent_type_removal.py`,
2): the sideboard solver's removal clause was artifact-only and keyed to
spell tags, so an ETB naturalize body was valued as a bare 2/2. It now
prices removal by the opponent's density of the permanent TYPES it can hit
(artifact / enchantment / planeswalker), read from the typed fields
(`targeted_removal_data`, `etb_targeted_removal_data`), within the printed
mana-value ceiling; creature removal keeps its threat-based clause (now
also ceiling-aware). Whether Witch Enchanter should stay in against Zoo is
a judgment the solver still makes on the numbers — not tuned. Ratchets at
baseline; WR anchor 29/29 (Bo1 pins carry no sideboarding).

### Iteration 2 — Azorius Control (80%): the lock is cast on the wrong turn

`--bo3 "Azorius Control" "Domain Zoo" -s 50000` (Zoo 2-0, T11/T7). G1:
control stabilises — two Solitudes (T3, T5), Teferi bounce (T4), Wrath X=2
(T7), Supreme Verdict (T8) — and sits at 17 life on T9 with Zoo's board
swept. It then casts Orim's Chant through Isochron Scepter on T9, T10 and
T11 **on its own turn** ("silences P2 this turn" during P1's turn), which
restricts nothing — Zoo casts Ragavan, Riddler and Frog on its own turns
and attacks for 4/6/10 — while control holds 5–6 cards and never
presents a clock (Hall of Storm Giants never animated). The Scepter-Chant
lock, that list's actual win plan, only works cast in the opponent's
upkeep. G2 is a mulligan-to-tempo loss to two Scions (T7), no decision
error beyond a desperate-attack at 3 life facing 8 power.

Built (failing-test-first, `tests/test_turn_scoped_opponent_restriction_timing.py`, 6):
a typed `CardTemplate.turn_scoped_restriction` ('no_spells' | 'no_attacks' |
'fog'; 30 instants, `parse_turn_scoped_restriction`). The AI's this-turn
signal enumerator returns no signal for such a card on the caster's own turn
(deferrable) and a signal on the opponent's turn; the runner's imprint hook
(`_process_imprint_copy_activations`, an engine-side auto-fire that predates
the AI activation seam) is timing-aware — a restriction copy fires in the
OPPONENT's upkeep (new call for the non-active player) and never in its
controller's main phase, every other copy keeps the sorcery-speed firing.
Related suites 29 green; ratchets at baseline; anchor: two turn-only
drifts (4/5c vs Pinnacle 21→18, Azorius vs WST 17→16, winners unchanged —
both control decks, consistent with the lock now landing on the right
turn) refreshed.

**Measurement (n=20, 50000 grid, quiet box):** Azorius Control vs Domain Zoo
25 / 75 (baseline cell 20 / 80) — +5pp toward band on the cell (cell noise
~±10pp at n=20; the field re-read decides movement). Same log: the
sideboard plan for this pairing is "+2 Mystical Dispute, −2 Orim's Chant" —
the lock piece is boarded OUT against Zoo, so games 2 and 3 never see the
fix. The solver has no clause for the turn-scoped-restriction class at all
(it values such a card at its body, i.e. zero), so it is swapped out
against every opponent: next lead, class-sized.

**Field re-read (n=20, 50000 grid, quiet box, head `114d094`): flat
67.3%** (baseline 67.5%). Cell-by-cell against iteration 0: Azorius Blink
90 → 75 (the only cell that moved by more than one match; direction of the
iteration-1 fix), Azorius Control 80 → 80, Instant Reanimator 55 → 60, WST
v2 50 → 55, every other cell identical. Cells ≥85% now: Goryo's Vengeance
100, Creatures Toolbox 95, Affinity 90, Amulet Titan 85, Boros Ponza 85,
Grixis Reanimator 85, Hollow One 85. No field movement (<2.2pp): **code
iteration 2 of the loop-break count (2 of 3)**. Stop gate: not met.

Sideboard finding resolved: the "+2 Mystical Dispute, −2 Orim's Chant"
plan is produced by the LEGACY path. `engine/sideboard_manager.py::sideboard`
defaults to `SB_SOLVER=old`, a deck-name × card-name string table (Chant
boards out against any opponent whose name contains "domain"/"control"/
"tron"/"titan"…; Dispute boards in against "zoo"/"energy"/"prowess"/
"affinity"). The oracle solver — the path both sideboard fixes in this
loop improved — only runs opt-in (`SB_SOLVER=new`), held there by the
recorded Phase 2/2.5/2.6 decisions in `docs/proposals/sideboard_solver.md`
(Goryo's −4pp regression, ±25–35pp pairing volatility at n=20). So neither
sideboard-solver change in this loop reached a live game. Flipping the
default reopens a held decision with a matrix-wide blast radius and is not
taken inside this loop; it is recorded here as the standing reason the
defending side's post-board games do not see the loop's fixes.

### Iteration 3 — Goryo's Vengeance (100%): the hand attack is never cast

`--bo3 "Goryo's Vengeance" "Domain Zoo" -s 50000` (Zoo 2-1; `--trace`
reproduces G1). G1: Goryo's keeps Thoughtseize and holds it through turns
1–5 with black open while Zoo deploys Ragavan and Scion, casting it on
turn 6 at five life ("−0.1 cast_spell: Thoughtseize" every main phase);
G3 casts Inquisition + two Thoughtseize on turn 11. Two defects, both
class-sized, both on the hand-attack class (150 pool cards carry the new
typed field: 51 caster-chosen, 99 victim-chosen, 6 random):

1. **The class carried no this-turn signal.** `_is_immediate_interaction`
   gates forced discard on the typed `has_discard_effect`, whose parser
   read "discards a card" but not "discards THAT card" — the caster-chosen
   shape ("You choose a nonland card from it. That player discards that
   card"). Every Thoughtseize / Inquisition / Duress was therefore scored
   as deferrable (−exposure) and skipped by the pass-preference filter no
   matter what its EV said. The RC-2 fix of 2026-07-05 (`docs/diagnostics/
   2026-07-05_goryos_field_13pct_root_cause.md`) had corrected the
   *phrasing* gate; the later typed-field migration re-broke it, and its
   pinning test used a stub template with `has_discard_effect=True`.
2. **A caster-chosen strip was priced as a card-neutral trade.** The
   projection charges the caster's card, credits one average opponent
   card and bills the life cost — net −0.1 — although the caster takes
   the BEST eligible card of the hand.

Built (failing-test-first, `tests/test_hand_attack_values_the_best_card_of_a_hidden_hand.py`,
12): typed `CardTemplate.hand_attack_data` (chooser caster / victim /
random, target, verbatim choose clause, count; `parse_hand_attack`);
`_is_immediate_interaction` reads it (and `parse_has_discard_effect` now
covers "discards that card"); `ai/hand_denial.py::hand_denial_value`, the
mirror of `ai/land_denial.py`: eligible pool = opponent hand ∪ library
(the BHI public-decklist premise) filtered through the engine's own
revealed-hand restriction filter, ranked by the resolution's own strip
scorer, exact hypergeometric order statistic for "rank-k card is the
strip", denied value = `creature_threat_value` for a creature and
`card_clock_impact` otherwise, minus the projection's average-card
credit; zero into an empty hand. Overlay hooked in `_score_spell` beside
the land-denial overlay, typed-field gated. No new literals; all 7
ratchets at baseline.

Replay after: G1 Thoughtseize on turn 1 (takes a Stubborn Denial), G3
Thoughtseize turn 2 + Inquisition turn 6; the match is still 1-2 (one
seed, not the measurement). Second finding recorded as a lead, not
built: G1 T6 (and the anchor game below) Goryo's casts Undying Evil on an
OPPONENT's creature to dodge the cleanup discard — a beneficial targeted
effect aimed at the wrong side; `can_cast` correctly refuses it on an
empty board (verified), so the defect is the AI's target choice for
beneficial effects plus the cast-anything-rather-than-discard branch.
Class: pump / protection / keyword-grant instants.

WR anchor: two winner flips + one turn-only drift, both flips replayed
through the anchor-exact harness before/after and accepted as the class
firing on its intended turn: 4c Omnath vs Goryo's s50500 (Goryo's T9 →
Omnath T6; first divergence T1 "Cast Thoughtseize" taking Ephemerate,
then T4 Inquisition taking Solitude; the loss follows from Goryo's not
casting Faithful Mending with WU open after the turn-4 Inquisition and
the Undying Evil misfire on turn 5 — pre-existing sequencing, recorded
above) and Creatures Toolbox vs Grixis Reanimator s52000 (Grixis T9 →
Toolbox T9; first divergence T2 "Cast Thoughtseize" taking Tyvar, the
deck's combo piece, then a second on T4; Grixis pays 4 life and is raced
by the creature curve). Instant Reanimator vs Boros Ponza s51500 11 → 10
turn-only. Fixture refreshed via `tools/refresh_wr_baseline.py`, 29/29.
Both CI chunks green locally (a-g 2199 + the seeded magic-number baseline
for the new file, h-z 2216); pushed as `6487ab6`.

**Measurement (n=20, 50000 grid, quiet box):** Goryo's Vengeance vs Domain
Zoo 0 / 100 (baseline cell 0 / 100) — no movement; Zoo's wins land on
turns 5–7 (17 sweeps). Regression guard Azorius Control vs Domain Zoo
25 / 75, identical to the iteration-2 cell. In the same replay Goryo's
mulligans to 4, 5 and 5 in the three games ("combo path under-covered …
need enabler+payoff" on 7 and 6, "goal conjunction unreachable" on 6):
a reanimator keeping 4–5 cards every game cannot race a turn-5 kill
whatever its hand attacks do. The keep/mull rule for combo paths
(`ai/mulligan.py`) is the subsystem this cell now points at; not built
inside this loop (third code iteration — the field re-read below decides
the loop-break).

**Field re-read (n=20, 50000 grid, quiet box, head `6487ab6`): flat
65.8%** (baseline 67.5%, previous 67.3%). Cells that moved against
iteration 2: Grixis Reanimator 85 → 65 and Dimir Midrange 70 → 50 (both
hand-attack decks — the class fix landing where the class lives),
Instant Reanimator 60 → 65; every other cell identical, including the six
still ≥85% (Goryo's 100, Creatures Toolbox 95, Affinity 90, Amulet Titan
85, Boros Ponza 85, Hollow One 85). 1.7pp over the loop is under the
2.2pp movement rule: **third consecutive code iteration without field
movement → loop-break.** Stop gate not met (65.8 is above the band edge
and six cells ≥85%). The required primary doc is
`docs/diagnostics/2026-09-05_zoo_band_loop_break.md`: it names the
pre-turn-1 mulligan of the reanimator side (combo-path keep rule in
`ai/mulligan.py`, class = every FILL_RESOURCE deck) as the residual
subsystem, records the other five cells' owners, and states the restart
condition. Loop ended.

### Restart precondition — a "target player" discard can target its caster (2026-09-05)

User decision after the loop-break: fix the keep/mull residual first,
then restart. The residual is not a looser rule (RC-3's flat sets stay
falsified and untouched); it is a line the sim could not play at all:
**cast a "target player … discards" spell on yourself** to bin the
reanimation target. Three coupled gaps, one class (75 pool cards can
target their caster: 11 caster-chosen, 62 victim-chosen, 2 random):

1. **Engine.** The Thoughtseize registry handler hardcoded the opponent
   as victim and ignored the target list; the generic reveal-choose
   resolver did the same and picked the discard by mana value inside the
   engine. Built: named player-target sentinels in `engine/constants.py`
   (`PLAYER_TARGET_OPPONENT = -1`, the historical face value;
   `PLAYER_TARGET_SELF = -2`) resolved by
   `target_solver.player_index_for_target` / `targeted_player` (the face-
   burn path reads the same helper); the reveal-choose branch is gated on
   the typed `hand_attack_data`, resolves the victim from the chosen
   target (opponent when none is chosen — every existing caller
   unchanged; "target opponent" wording can only hit the opponent),
   applies the choose clause through the engine's restriction filter,
   and delegates WHICH card goes through the one discard funnel
   (`GameState._force_discard(..., candidates=)`: strip advisor against an
   opponent, the self-discard advisor — the one that already targets
   reanimation fuel — for one's own hand). The Thoughtseize registry
   entry is retired (card-name registry 89 → 88, baseline lowered).
2. **AI.** `ai/card_classes.py::self_discard_outlet_targets(template,
   hand, gameplan)`: the cards in hand a self-targetable hand attack
   could bin for the deck's own graveyard plan — the gameplan declares a
   graveyard FILL_RESOURCE goal, the choose clause admits the card
   (engine filter), and a payoff in hand can return it (the payoff's
   parsed graveyard target requirement: legendary / any / mana-value
   ceiling). `_choose_targets` aims the spell at the caster when the
   self-fill value exceeds `hand_denial_value`; the self-fill value is
   the reanimation readiness boost (`_reanimation_readiness_boost`, the
   GV-2 quantity factored into one owner) when the bin moves the
   graveyard from below the declared resource target to at or above it,
   zero otherwise. `_score_spell` credits the larger of the two lines.
3. **Mulligan.** The 7/6-card typed-path gate and `conjunction_unreachable`
   count a self-targetable outlet that can bin a returnable creature in
   the same hand as enabler coverage; a hand attack with nothing to bin
   covers nothing (the tightness that keeps RC-3 closed).

Fidelity fix found on the way: `engine/card_database.py` carried a
card-name table stamping LEGENDARY on Archon of Cruelty (real type line
"Creature — Archon") so a legendary-only reanimation could hit it — wrong
reanimation legality and a wrong legend rule for that card, plus a test
(`tests/test_archon_of_cruelty_is_legendary.py`, GV2-7) pinning the
fabricated value on a false premise. Table and test removed; the rule
"supertypes come from the printed type line" is pinned in the new file.
The gameplan's `mulligan_combo_sets` still list [Mending, Goryo's,
Archon] (rules-invalid; used only by the color check) — left as data,
recorded here.

Tests (failing-test-first, rule-phrased,
`tests/test_target_player_discard_can_target_its_caster.py`, 15): the
victim is the targeted player including the caster (life cost still
paid); default opponent with no target; choose clause honoured on the
caster; the choice is delegated to the callback (cheapest-card stub
obeyed; self-discard flag set for one's own hand); no card-name handler;
supertypes from the printed line; the graveyard-fill deck targets itself
with a binnable target and a payoff, the opponent once the target is
already in the graveyard, never without a graveyard plan; the self line
scores positive; mulligan: outlet + returnable creature covers the
enabler bucket, a capped outlet (IoK vs an 8-drop) does not, the payoff
must be able to return the binned card (legendary-only vs any), no
binnable target leaves the rule unchanged, and the outlet makes the
conjunction reachable at the keep floor. One existing fixture
(`test_goryos_mulls_payoff_only_at_6`) held Thoughtseize + a creature
Unburial Rites could return — no longer "payoff-only" — and now carries a
blink spell instead. Ratchets: registry 88 (lowered), others at baseline.

Replay after (`--bo3 "Goryo's Vengeance" "Domain Zoo" -s 50000`, still
1-2): the line exists now — G1 "[Target] own hand — bin the reanimation
target … Thoughtseize discards Griselbrand (own hand)" on turn 2, G3 the
same on turn 2 with Goryo's Vengeance turn 3 (countered by Stubborn
Denial) and turn 4 (resolves). Kept hand sizes 4/5/5 → 5/5/5: the
opening 7 with two Goryo's and no fatty is still shipped (nothing to
bin), the 5 with Griselbrand + Thoughtseize + Rites is now reachable.

WR anchor: one turn-only drift (Goryo's vs Izzet Prowess s50000, 6 → 7)
and one winner flip, replayed through the anchor-exact harness: Instant
Reanimator vs Boros Ponza s51500 (IR T10 → Ponza T6). First divergence
is the mulligan: IR now KEEPS its 6 (Marsh Flats, Goryo's Vengeance,
Polluted Delta, Atraxa, Thoughtseize, Ephemerate) — the exact self-
outlet shape — where it previously mulled to 5; it self-Thoughtseizes
Atraxa on turn 2 and reanimates it on turn 3 for 7, then loses a Bolt +
Ragavan race at 3 life. The deck's real line, one seed's race — accepted;
fixture refreshed, 29/29. Both CI chunks green (a-g 2196, h-z 2231);
pushed as `94475d0`.

**Measurement (n=20, 50000 grid, quiet box):** Goryo's Vengeance vs
Domain Zoo 5 / 95 (was 0 / 100) — one match, direction only; Goryo's
game wins now land on turns 5–8 (were 5–6), Zoo's on 4–8. Guards:
Grixis Reanimator vs Domain Zoo 40 / 60 (the self-outlet class fires
there too — Thoughtseize + Archon; no regression against the 35 / 65
field cell), Dimir Midrange vs Domain Zoo 55 / 45 (no graveyard plan,
still targets the opponent; field cell 50 / 50). The restart
precondition is met on the mechanism (the line exists and is kept);
the Goryo's cell itself is now a race the registered list mostly loses
to a turn-5 kill. Full field re-read follows as the restarted loop's
iteration-0 baseline.

## Domain Zoo band loop — restart (2026-09-05)

### Iteration 0 — baseline on `94475d0` (measurement only)

`run_meta.py --field "Domain Zoo" -n 20 --parallel`, 50000 grid, quiet
box, offline scorer. **Flat 65.0%** (pre-restart 65.8%; the original
loop's baseline was 67.5%). Against the last row: Goryo's Vengeance
100 → 85 (the self-discard-outlet line), Instant Reanimator 65 → 60,
every other cell identical. Cells ≥85%: Creatures Toolbox 95, Affinity
90, Amulet Titan 85, Goryo's Vengeance 85, Boros Ponza 85, Hollow One
85. At band or below: Ruby Storm / Eldrazi Tron 25, 4c Omnath 45, Dimir /
Broodscale 50, Boros Energy / WST v2 55, Jeskai Blink / Living End /
4/5c / WST / Instant Reanimator 60, Pinnacle / Eldrazi Ramp / Grixis 65.
Stop gate: the field average sits at the band edge but six cells are
≥85% — not met. Loop-break counter reset (new loop).

Target selection (rule: largest excess, no tracked root cause, no clean
audit): Creatures Toolbox (Soul Cauldron line, tracked), Affinity (audited
race), Amulet Titan (rediagnosis doc) and Goryo's (measured above as a
race after the outlet fix) are skipped; **Boros Ponza 85 and Hollow One
85** are the two registered lists with no replay audit at all. Iteration
1 opens both with `--bo3 … -s 50000` replays and follows whichever shows a
class-sized defending-side defect.

### Iteration 1 — Boros Ponza (85%): the land-type lock is neither a continuous effect nor valued past turn 4

`--bo3 "Boros Ponza" "Domain Zoo" -s 50000` (Ponza 2-1 on this seed). G1:
Ponza casts Blood Moon on turn 10 — after the game — and G2 the same;
`--trace` shows it drawn on turn 7 against a five-colour deck on duals and
fetches and scored **−0.2** every turn until turn 11 (+1.3). Two class
defects, engine and AI:

1. **Engine.** "Nonbasic lands are Mountains" ran as a one-shot ETB handler
   on the OPPONENT's lands then on the battlefield, swapping in a per-
   instance template copy: lands played afterwards kept their colours,
   the caster's own nonbasics were untouched (the real card is symmetric —
   Ponza runs basics for that reason), and a fetchland under the effect
   could still be cracked. Built: a **layer-4 land-type SET continuous
   effect** derived every `recalculate()` from any permanent with the
   typed `stax_forced_basic` (`create_forced_land_type_effect`; CR 613.1d,
   305.7, 611.2a) — every nonbasic land on both battlefields, entering
   later included, produces only that basic type's colour
   (`ManaPayment.effective_produces_mana`, `available_mana_colors`) and
   has no fetch ability (`crack_fetchland` refuses, reading the live layer
   state and the source for a land cracked as it enters); the effect ends
   with its source. `BASIC_LAND_TYPE_COLORS` (CR 305.6) in
   `engine/constants.py` is the one type→colour map. The Blood Moon ETB
   registry entry is retired (87, baseline lowered); Magus of the Moon and
   Harbinger of the Seas get the effect for free.
2. **AI.** `ai/stax_ev._blood_moon_lock_ev` was a coefficient × nonbasic
   count with a cap, multiplied by a turn-decay table that reaches ZERO on
   turn 5, and the whole stax overlay was silenced by `holdback >= 0` —
   any instant in hand hid the lock's value. Built: the lock is worth the
   cards it makes uncastable, for both players (symmetric): a card is dead
   when its cost needs a colour the player can no longer make (basics of
   that type on the battlefield, in hand or in the library keep it alive;
   fetching is gone), each dead card is valued by `creature_clock_impact`
   (creatures) or `card_clock_impact` in the family's life units, weighted
   by the share of the pool the player will see before the game ends
   (`_lock_horizon_draws` from the two combat clocks; the whole library
   when neither side has a clock). No coefficient, cap or turn table; the
   family bypasses `_turn_decay`. The overlay gate is removed — the tap-out
   is priced once, by the signed holdback penalty already in the score
   (the inverted "silenced" pin retired; the rule re-pinned).

Tests (failing-test-first, rule-phrased,
`tests/test_forced_land_type_is_a_continuous_effect.py`, 9): symmetric and
retracting on both battlefields; a land entering later is affected (and
the untapped colour census agrees); a fetchland has no fetch ability; no
card-name handler and a second family member works; the value counts
the opponent's dead cards and is zero for a one-colour opponent on
basics; it does not expire on a turn number; a colour still reachable
through basics is not dead; the caster pays for its own dead cards; the
overlay is priced by holdback, not silenced. Existing pins
(`tests/test_stax_ev.py` Blood Moon > 2.0 vs a UW nonbasic base, 0 vs
mono-red on basics) hold on the derived formula. Ratchets: registry 87
(lowered), others at baseline.

Replay after the valuation alone: Blood Moon scored **+8.7** on turn 7,
was the best candidate — and was still passed: a lock permanent carried
no "this-turn signal", so the pass-preference filter deferred it forever
whatever its EV (the same gate that hid the hand-attack class). Built:
a typed-`stax_class` permanent carries the signal
`lock_before_opponent_turn` (it restricts the opponent's NEXT turn, so
cast-now and cast-later are different states); pinned. A second copy
while the effect is in play (either side) is worth zero; pinned. Replay
after both: Blood Moon on **turn 4** in G1 (Ponza wins T9) and turn 4 in
G2; Ponza's own fetchlands lose their ability under it as the rules say
("Marsh Flats has no fetch ability"); match 1-2 on this seed (one seed,
not the measurement). Anchor: one turn-only drift (Boros Ponza vs Boros
Energy s51000, 13 → 17) refreshed. Both CI chunks green on the commit
state (a-g 2207, h-z 2230); pushed as `77f7527`.

**Measurement (n=20, 50000 grid, quiet box):** Boros Ponza vs Domain Zoo
**45 / 55** (baseline cell 15 / 85) — a 30pp cell move; Ponza's wins land
on turns 7–13, Zoo's on 5–16 (11 of 20 matches to game 3). The cell-move
rule (≥15pp) triggers the full field re-read below. Regression guard —
Azorius Control (WST) vs Domain Zoo, the pairing the removed overlay gate
was introduced for (T2 Chalice over a held Counterspell): 20 / 75 (one
match unresolved) against a 40 / 60 field cell — a 15pp move the wrong
way at n=20 (cell noise ±10pp); the field re-read decides whether the
gate's removal re-opened that regression, and if it did the Chalice
family's coefficient valuation is the next subsystem, not the gate.

**Field re-read (n=20, 50000 grid, quiet box, head `77f7527`): flat
63.8%** (restart baseline 65.0%). Cells: Boros Ponza 85 → 60, Boros
Energy 55 → 50, Azorius Control (WST) 60 → 60 (the guard's 20 / 75 was
noise in the other seat), every other cell identical. 1.2pp is under the
2.2pp movement rule: **code iteration 1 of the loop-break count (1 of
3)**, although the targeted cell moved 25pp. Stop gate: field inside the
band but five cells ≥85% (Creatures Toolbox 95, Affinity 90, Amulet Titan
85, Goryo's Vengeance 85, Hollow One 85) — not met. Iteration 2 target:
Hollow One (recorded lead above).

**Hollow One (85%) — recorded lead, next iteration.** `--bo3 "Hollow One"
"Domain Zoo" -s 50000` (Zoo 2-0): the trace scores "cycle: Street Wraith"
at **+8.3** and "cycle: Hollow One" at **+7.8** against +0.9 for a
one-drop creature, so the deck cycles its own payoff on turn 5 instead of
sequencing cycles into the turn it casts it, and Hollow One is never cast
in either game. The cycling action's valuation (a flat draw bonus that
dwarfs board plays) and the per-turn "costs {1} less for each card
cycled or discarded this turn" discount not being planned as a same-turn
sequence are the subsystems; class = every cycling card and every
this-turn cost reducer.
