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

## Phase 2 / 3

Not started. Phase 2: 2a `opportunity_cost` primitive, 2b joint block-assignment, 2c
unify 3 combat models. Phase 3: this tracker doc (item 1, now done) + EFFECT_REGISTRY mechanic-
cluster consolidation (burn-N-damage, destroy/exile-nonland-MV≤X, board-sweep, draw-N clusters —
~40-50 of 104 registrations) + remaining `ai/` patch retirement using 2a/2b's primitives + remaining
raw zone-mutation sites + CDA coverage extension (Death's Shadow, Mortivore/Bonehoard, Multani).

## Verification convention (every item)

Failing test first, rule-phrased name (mechanic, not card — card names live only in fixture-carrier
constants/docstrings). `python -m pytest tests/ -q` full suite (now feasible in ~4-5 min per-session,
not the ~80 min CLAUDE.md describes from before the shared-DB-fixture consolidation) +
`python tools/check_abstraction.py` + `python tools/check_magic_numbers.py` +
`python tools/check_zone_mutation.py`, all at baseline or better. Targeted replay of the specific
audit seed for bugs directly fixed by an item (table above).
