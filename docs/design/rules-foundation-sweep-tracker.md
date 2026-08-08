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

### 0b. Activate continuous_effects.py — NOT STARTED

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

### 0d. Finish SBA unification — NOT STARTED (but exact scope already authored)

`docs/proposals/resolver_sba_unification.md` (status: active) already documents this exact task as
its own named follow-up (§6): every 704.5x rule becomes a `SBAManager.perform_*` static (3/9 done:
poison, deathtouch, token-cleanup), `check_state_based_actions` collapses into the CR 704.3 fixpoint
loop over those statics. Remaining 6: zero-toughness (704.5g), lethal-damage (704.5h — the
INDESTRUCTIBLE exemption part is already fixed at the single owning site, `CardInstance.is_dead`;
what remains is consolidating into a shared static), legend rule (704.5j), planeswalker-loyalty
(704.5p), player-life (704.5a), empty-library-decking (704.5b — currently an inline instant-loss in
`draw_cards`, not deferred to the next SBA window; `_drew_from_empty` is read at `sba_manager.py:101`
and written nowhere in the repo).

### 0e. Make snapshot mandatory in ai/ — NOT STARTED

## Phase 1 / 2 / 3

Not started. See the approved plan (`/root/.claude/plans/lets-create-plan-and-typed-flurry.md` at
authoring time — copy the plan content here if that path is not durable across sessions) for full
item-by-item design. Phase 1: 1a cost/counter framework, 1b combat legality, 1c damage funnel,
1d CDA generalization. Phase 2: 2a `opportunity_cost` primitive, 2b joint block-assignment, 2c
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
