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

## Phase 2

Not started. 2a `opportunity_cost` primitive, 2b joint block-assignment, 2c unify 3 combat
models. See the approved plan for full item-by-item design.


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

## Verification convention (every item)
Failing test first, rule-phrased name (mechanic, not card — card names live only in fixture-carrier
constants/docstrings). `python -m pytest tests/ -q` full suite (now feasible in ~4-5 min per-session,
not the ~80 min CLAUDE.md describes from before the shared-DB-fixture consolidation) +
`python tools/check_abstraction.py` + `python tools/check_magic_numbers.py` +
`python tools/check_zone_mutation.py`, all at baseline or better. Targeted replay of the specific
audit seed for bugs directly fixed by an item (table above).
