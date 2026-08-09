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
| `engine/continuous_effects.py` (`ContinuousEffectsManager`) | **Activate** | Pending (0b) |
| `engine/sba_manager.py` (`SBAManager.check_and_perform_loop`) | **Finish migration** (live proposal, 3/9 rules done: poison, deathtouch, token-cleanup) | Pending (0d) |
| `engine/event_system.py` (`EventBus`) | **Delete** — registered-closure API mismatches `oracle_resolver.py`'s imperative-function style everywhere | Pending, deferred until 0a's remaining trigger-dispatch consolidation makes it provably unused |
| `engine/priority_system.py` | **Delete** — `resolve_priority_round` (sole caller of `pass_priority`/`both_passed`) confirmed to have zero callers anywhere, including via the doc that once claimed otherwise | Pending |
| `engine/oracle_parser.py:260 is_living_end_cascader` | Delete, 0 callers | Pending |
| `engine/oracle_parser.py:350 parse_planeswalker_abilities` | Delete, superseded by `player_state.py:272` | Pending |
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
- [ ] Migrate `_creature_dies` to route the battlefield→graveyard zone-list mutation through
  `zone_manager.move_card` (currently raw-mutates then separately calls the trigger dispatch, which
  is now correct but still bypasses the funnel's own bookkeeping/logging).
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
- [ ] Delete `engine/event_system.py` once the remaining trigger-dispatch consolidation proves
  `zone_manager.move_card`'s `EventBus.fire_event` calls are provably redundant with the direct
  imperative-function calls (currently both exist; the EventBus path never accomplishes anything
  since its registries are empty, but removing it needs the direct-call path to cover every case
  it currently silently no-ops for).
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

## Phase 1 / 2 / 3

Not started. See the approved plan (`/root/.claude/plans/lets-create-plan-and-typed-flurry.md` at
authoring time — copy the plan content here if that path is not durable across sessions) for full
item-by-item design. Phase 1: 1a cost/counter framework, 1b combat legality, 1c damage funnel,
1d CDA generalization. Phase 2: 2a `opportunity_cost` primitive, 2b joint block-assignment, 2c
unify 3 combat models. Phase 3: this tracker doc (item 1, now done) + EFFECT_REGISTRY mechanic-
cluster consolidation (burn-N-damage, destroy/exile-nonland-MV≤X, board-sweep, draw-N clusters —
~40-50 of 104 registrations) + remaining `ai/` patch retirement using 2a/2b's primitives + remaining
raw zone-mutation sites + CDA coverage extension (Death's Shadow, Mortivore/Bonehoard, Multani).

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

## Verification convention (every item)

Failing test first, rule-phrased name (mechanic, not card — card names live only in fixture-carrier
constants/docstrings). `python -m pytest tests/ -q` full suite (now feasible in ~4-5 min per-session,
not the ~80 min CLAUDE.md describes from before the shared-DB-fixture consolidation) +
`python tools/check_abstraction.py` + `python tools/check_magic_numbers.py` +
`python tools/check_zone_mutation.py`, all at baseline or better. Targeted replay of the specific
audit seed for bugs directly fixed by an item (table above).
