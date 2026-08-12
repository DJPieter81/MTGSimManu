---
title: Recurring triggered-ability dispatch gap (CR 603) — cast-triggers and permanent-enters-triggers collapsed to one-time ETB
status: active
priority: primary
session: 2026-08-12
supersedes: []
superseded_by: []
depends_on: []
tags: [engine, triggers, cr603, affinity, tokens]
summary: >
  Two families of recurring CR 603 triggered abilities — "whenever you cast a
  [type] spell, <effect>" and "whenever a [type] permanent you control enters,
  <effect>" — are mis-modelled as one-time EffectTiming.ETB self-effects, so
  they fire once on the source's own ETB instead of per matching event. Plus a
  regressed ETB "draw N". Surfaces as Pinnacle Affinity underperformance
  (Emissary makes no per-artifact-cast Drones, Kappa gets no per-artifact-ETB
  counter, Thought Monitor draws 0) but is an engine-general dispatch gap.
---

# Recurring triggered-ability dispatch gap (CR 603)

Diagnosed 2026-08-12. Fixtures below are carriers only; the fix is engine-general
and must serve every card of each shape, artifact or not.

## The mechanism that already exists

- `engine/oracle_resolver.py:resolve_spell_cast_trigger(game, caster_idx, spell_cast)`
  — scans all controller permanents on each cast; fires "whenever you cast a
  spell" effects. Currently handles: energy, **noncreature**-spell token
  creation, draw, surveil, transform. Called from `cast_manager.py:1417`.
- `engine/triggers.py:TriggerManager.trigger_etb(game, card, controller)` —
  scans controller permanents on each ETB; fires "whenever another creature
  enters" (life/energy) and "whenever this creature or another [Subtype] you
  control enters" (Risen Reef). Called wherever a permanent enters
  (`game_runner`, `spell_resolution`, `card_effects` blink/exile-return).

Both are the correct home for recurring triggers. The bug is that three cards
are wired elsewhere (one-time ETB self-effects in `card_effects.py`) and their
shapes are not covered by the dispatchers.

## Root causes

### A. Cast-triggered token by SPELL TYPE — not just "noncreature"
`resolve_spell_cast_trigger`'s token branch (`oracle_resolver.py:934-943`) is
gated on `permanent.template.has_noncreature_spell_cast_trigger` AND
`not spell_cast.template.is_creature`. It covers "whenever you cast a
noncreature spell, create a token" (Young Pyromancer / Monastery Mentor class).
It does **not** cover "whenever you cast an **artifact** spell, create a token"
(**Pinnacle Emissary** → 1/1 flying Drone). Emissary is instead registered as a
one-time `EffectTiming.ETB` projection (`card_effects.py:2370-2391`) that counts
free artifacts in hand once — so no Drone is created per subsequent artifact
cast.

**Fix (generic):** parse the cast-trigger's spell-type condition into a typed
field at DB load — the SET of spell types that satisfy it (`{artifact}`,
`{noncreature}`, `{instant, sorcery}`, …) — and the token spec (count/P/T/
keywords). In `resolve_spell_cast_trigger`, fire the token when `spell_cast`
matches the condition, for ANY qualifying type. Remove Emissary's ETB
projection registration. One dispatch serves Emissary, Young Pyromancer,
Monastery Mentor, Talrand-class, etc.

### B. Permanent-enters counter by PERMANENT TYPE
`trigger_etb` handles "another creature enters" and subtype ("[Subtype] you
control enters") watchers, but has no "whenever this creature or another
**artifact** you control enters, put a +1/+1 counter on it" watcher (**Kappa
Cannoneer**). Kappa is registered as a one-time `EffectTiming.ETB` self-count
(`card_effects.py:2344-2366`) with a flagged "matrix-wide corruption / double-
credit" note — so it gets no counter on subsequent artifact ETBs and may
double-count its own.

**Fix (generic):** add a type-based enters-watcher to `trigger_etb` mirroring
the existing subtype scan — "whenever this creature or another [Type] you
control enters → +1/+1 counter (and set-unblockable-this-turn where stated)".
Fire once per qualifying ETB; the self-ETB counts exactly once (the subtype
scan's `watcher.instance_id == card.instance_id` handling is the template).
Remove Kappa's ETB self-count. Parse the type + effect into typed fields at DB
load; no card names, no new runtime oracle parse in a ratcheted file.

### C. ETB "draw N" regressed
**Thought Monitor** ("when this creature enters, draw two cards", plus Affinity)
draws 0 in audit (`ETB cards drawn: 0.0/game`); its handler was deleted
(`card_effects.py:2196`). Route it through the existing generic draw-N ETB path
(the Phase-3 draw-N ETB cluster) so the ETB draw fires. Confirm the Affinity
cost discount (`mana_payment.py:181-187`) still applies so it lands early.

## Constraints (CLAUDE.md abstraction contract)

- Failing test first, rule-phrased (CR 603 mechanic, not a card):
  `cast_triggered_token_fires_per_matching_spell_type`,
  `enters_triggered_counter_fires_per_qualifying_permanent_without_self_double_credit`,
  `etb_draw_n_adds_n_cards_to_hand`. Cards are fixtures only.
- Verify each family on a NON-artifact instance (Young Pyromancer / Monastery
  Mentor for A) so the fix is provably engine-general.
- Typed fields parsed once in `oracle_parser.py` + populated in
  `card_database.py` (both excluded from the oracle-runtime-parse ratchet);
  keep `resolve_spell_cast_trigger`/`trigger_etb` net-neutral or lower on the
  ratchet — do not add new `'x' in oracle` checks to ratcheted files.
- No card/deck-name gates in engine/ai; no magic numbers.

## Validation

- `python run_meta.py --audit "Pinnacle Affinity" -n 40`: Drone tokens created
  per artifact cast > 0, ETB draws/game > 0, Kappa grows per artifact ETB.
- `python run_meta.py --matchup "Pinnacle Affinity" "Boros Energy" -n 20` WR
  before/after. Confirm regular "Affinity" not regressed.
