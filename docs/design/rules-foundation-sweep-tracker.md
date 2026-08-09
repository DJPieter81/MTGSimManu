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

## Phase 1 / 3

Phase 1 (1a cost/counter framework, 1b combat legality, 1c damage funnel, 1d CDA generalization) was
done in this program's session on a separate branch (`claude/rules-foundation-phase1`, PR #490,
CI green, still draft/unmerged as of this Phase 2 branch's creation) — not reflected in this copy
of the tracker doc yet since that PR hasn't merged; see that PR's own commits for 1a-1d detail.
Phase 3 (this tracker doc item 1, now done) + EFFECT_REGISTRY mechanic-cluster consolidation
(burn-N-damage, destroy/exile-nonland-MV≤X, board-sweep, draw-N clusters — ~40-50 of 104
registrations) + remaining `ai/` patch retirement using 2a/2b's primitives + remaining raw
zone-mutation sites + CDA coverage extension (Death's Shadow, Mortivore/Bonehoard, Multani) — not
started. See the approved plan (`/root/.claude/plans/lets-create-plan-and-typed-flurry.md` at
authoring time — copy the plan content here if that path is not durable across sessions) for full
item-by-item design.

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

### 2c. Unify turn_planner/board_eval block-prediction models — NOT STARTED

Stretch goal, explicitly deferred per the task's own instruction ("if you don't have time for this,
leave it explicitly marked not started — do not half-implement it"). `ai/turn_planner.py`'s
opponent-block-prediction model and `ai/board_eval.py`'s CMC-weighted scorer were not investigated
in this pass; unifying them onto the same joint-assignment primitive `decide_blockers` now uses (2b)
is real, separate work for a future session. Verify first (per this program's own repeated finding
that claims about `ai/`'s block-related code have drifted from what's actually there) before
assuming either module's current shape.

## Verification convention (every item)

Failing test first, rule-phrased name (mechanic, not card — card names live only in fixture-carrier
constants/docstrings). `python -m pytest tests/ -q` full suite (now feasible in ~4-5 min per-session,
not the ~80 min CLAUDE.md describes from before the shared-DB-fixture consolidation) +
`python tools/check_abstraction.py` + `python tools/check_magic_numbers.py` +
`python tools/check_zone_mutation.py`, all at baseline or better. Targeted replay of the specific
audit seed for bugs directly fixed by an item (table above).
