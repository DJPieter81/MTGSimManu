---
title: Five-panel strategic audit (deep audit Phase 2) — below-band lanes
status: active
priority: primary
session: 2026-09-15
supersedes: []
depends_on:
  - docs/diagnostics/2026-09-15_deep_audit_backlog.md
tags: [deep-audit, strategic-audit, calibration, ai-decision-quality]
summary: >
  Phase 2 of the deep audit. With the rules auditor finding zero engine
  violations (Phase 1), three archetype panels (ramp/combo, blink/tempo,
  control) read fresh Bo3 traces of the below-band lanes and named the
  AI-decision mechanisms behind the win-rate outliers. Every finding is
  class-sized, card-name-free, backed by a quoted log line + owning
  file:function, and cross-referenced to the Phase-1 backlog. The fix order
  for the WR-resolution loop is set here.
---

# Five-panel strategic audit — 2026-09-15

Method: the 2026-05-16 5-panel precedent, run as parallel archetype panels on
fresh Bo3 verbose traces (seed 60500/60600/60700, offline scorer). The rules
auditor's zero-violation result (Phase 1) means these are all decision-quality,
not rules bugs. A finding is promoted only with a quoted decisive log line, an
owning `ai/` (or engine) file:function, a rule-phrased failing test, and a
class-size ≥10 pool cards or a genuine mechanic; no card/deck-name hardcodes.

## Convergence with Phase 1
The **planeswalker loyalty** mechanism is named by BOTH the Phase-1 backlog
(83% loyalty no-op / `606/loyalty_unexecutable_kind` census) AND control panel
Finding 1 — a 2-source consensus, the highest-confidence unit.

## Tooling regression (flagged by two panels, blocks tracing project-wide)
`run_trace_game::_make_traced_main` (`run_meta.py`) does not forward the
`excluded_activations` kwarg that `engine/game_runner.py:1490` now passes to
`decide_main_phase` (→ `TypeError`), and reads the deleted
`profile.pass_threshold` (→ `AttributeError`); `tools/bo3_trace.py` crashes on
every game. Both the ramp and blink panels hit it and fell back to `--bo3`
verbose. Cheap repair, unblocks reasoning-inlined tracing for future units.

## Ranked fix order (the WR-resolution loop draws from here)

### U0 — repair bo3_trace / run_trace_game (tooling; cheap, unblocks the loop)
Forward `excluded_activations` through the traced-main wrapper; drop the
`profile.pass_threshold` read. Test: a traced Bo3 game runs without error.

### U1 — an optional loyalty activation must be declinable (control F1, + Phase-1 convergence) — HIGHEST CONFIDENCE
A planeswalker whose only *executable* slot is a loyalty-minus is forced to
fire it every turn — Teferi, Time Raveler ticks 4→1 into an empty board on the
turn it resolves, forfeiting the bounce answer it needed two turns later
(`45c_tron.txt:228`, `az_zoo:1760`). `ai/pw_ability.py::choose_pw_ability`
always returns a slot (no decline path) and credits a bounce's draw rider even
with no legal target; `engine/game_runner.py::_activate_planeswalkers` treats
every up walker as must-activate (CR 606.3 makes it optional).
**Mechanism:** decline an optional loyalty activation when the only executable
slot(s) are loyalty-negative AND the targeted effect has no legal target (only
a value rider remains) AND the controller is not at PANIC/LETHAL.
**Tests:** `test_minus_loyalty_ability_declined_when_its_targeted_effect_has_no_legal_target`,
`test_walker_with_only_executable_minus_is_not_forced_to_tick_down`.
**Class:** 564 unclassified loyalty abilities; 8 MB-PW decks (control/ramp/
midrange — the calibration-loss axis). Decks: Azorius Control (42/48), 4/5c (55).

### U2 — unbounded-mana-engine credit gated on sink reachability (ramp F1) — HIGHEST RAW LEVERAGE
Completing an unbounded mana loop is credited `LOOP_SHORTCUT_MANA` (80) of value
with no check that any mana sink exists, so Toolbox's single tutor fetches the
*enabler* (Vizier) to complete the Druid loop (`toolbox_zoo.txt:525` +99.7),
then passes with 81 mana and no payoff on board (`:537-555`), and is swept.
`ai/ev_player.py::_gate_x_tutor_payoff` (`:1077-1082` engine_bonus branch) +
the shared `would_complete_unbounded_engine` credit in the tutor-delivery
ranking. Mirror the already-solved land-sacrifice sibling gate
(`_overlay_land_sacrifice_fizzle` `:896`); a sink-reachability primitive exists
(`ai/combo_calc.py:761 _tutor_has_payoff_access`, storm-scoped, unwired here).
**Mechanism:** crediting engine completion requires a mana sink reachable (in
hand, on board, or fetchable by a still-available tutor at the engine's mana);
no sink → credit ~0.
**Test:** `test_unbounded_mana_engine_credit_requires_reachable_sink`.
**Class:** every unbounded-mana loop (Druid+Vizier, Kinnan+Monolith, Pili-Pala,
Freed-from-the-Real, …). Deck: Creatures Toolbox (21).

### U3 — flicker/blink floored unless a controlled target yields realizable value (blink F1)
Both blink decks spend Ephemerate blinking a creature with no live ETB value
(Phelia, an attack-trigger creature, into an empty board — `azb_omnath.txt:155-193`).
`ai/ev_player.py::_score_spell` blink floor (`:1666`) only catches a total
(0-creature) fizzle; the retrigger credit (`:1679`) keys off the static
`etb_value` tag, not a live positive-value resolution now.
**Mechanism:** floor a flicker/blink EV to ~0 unless a controlled target yields
realizable value now (live-target ETB, active protection need, or an EOT-exile
rider to shed).
**Test:** `test_flicker_floored_when_no_controlled_target_yields_blink_value`.
**Class:** every flicker/blink spell. Decks: Jeskai Blink (29), Azorius Blink (28).

### U4 — holdback ignores held interaction that is uncastable in the current mana base (blink F2) — MOST GENERIC
`ai/ev_player.py::_holdback_penalty` reserves mana for a counter whose colors
the current sources can't produce (Consign {U} with 0 U sources), suppressing a
tempo deck's turn-1 clock (`jeskai_omnath.txt:50-54`). Filter uncastable answers
in the collection loop (`:1999-2031`), the same `continue` given to dead tax
counters. Test: `test_holdback_ignores_held_interaction_lacking_color_source`.
Class: any 2+-color deck holding a colored instant before that color is online.

### U5 — combo enabler outranks a vanilla body under DEPLOY_ENGINE (ramp F2)
A declared `engines`-role creature (0/2 dork) scores −2.8 under deploy_engine,
below a vanilla 2/2 at +2.1 (`toolbox_zoo.txt:223-227`), delaying assembly.
`ai/ev_player.py::_score_spell` goal/role term should read `card_roles` (data)
for a deploy-priority credit. Test:
`test_combo_enabler_outranks_vanilla_body_under_deploy_engine_goal`.

### U6 — X-wipe own-collateral prices utility permanents by combat threat (control F3)
`engine/cast_manager.py::pick_wipe_x_value` subtracts own collateral but scores
it via `ai/permanent_threat.py::permanent_threat`, which values a non-attacking
lock piece (Chalice) ~0, so the picker sacrifices its own Chalice for a marginal
opposing kill (`wst_zoo.txt` T22, X=2 kills own Chalice; Scion survives).
Test: `test_x_wipe_picker_prices_own_utility_permanents_as_collateral`.
Confirms "defect C" from `2026-08-30_azorius_planeswalker_loyalty_noop_root_cause.md`.

### U7 — Dash chosen without a combat projection (blink F3) — narrower
`ai/board_eval.py::_eval_dash` grants a Dash bonus because the opponent has
blockers, the exact state where a small attacker won't attack; the deck bounces
Ragavan having done nothing (`jeskai_omnath.txt:474-482`). Consult the attack
projection (parallels `_eval_evoke`). Test:
`test_dash_mode_requires_profitable_attack_projection`.

### Lower confidence / needs its own A/B
- **Control can't close a won position** (control F2): a stabilized controller
  at 13-vs-8 never deploys a clock and cedes to topdeck variance
  (`wst_zoo.txt` T15). ADJACENT to the FALSIFIED clock-sign work — a new
  measurement target (close-out tempo bonus gated on `opp_creature_count==0 AND
  off-clock AND finisher in hand`; control decks up, aggro flat) is required
  before any code.

## Decks NOT given an engine unit (reported, not patched)
- **Hollow One (37)**: G1 win → G2/G3 losses = post-board graveyard-hate swing
  (Orcish Bowmasters punishes every loot); expected fragile-graveyard-aggro
  behavior, not a decision defect. Lever, if any, is SB/mulligan vs hate.
- **Amulet Titan (21)**: losses were flood/screw + "one answerable win-con vs an
  8-power turn-6 board" resilience, no isolable EV-divergent decision;
  construction, not a scoring bug. The `_overlay_land_sacrifice_fizzle` and
  ramp-finisher-deployment prior hypotheses were DISCARDED against live traces
  (both already correct on current code; Eldrazi Ramp is now ~in-band at 56 and
  won its historically-17% 4/5c matchup 2-1).

## Discarded prior hypotheses (against live traces)
Counter-triage passivity and C3 tap-out (control counters fired correctly; every
discard-to-hand-size was the opponent flooding); Jeskai UNDER-valuing ETB
creatures (it OVER-casts blink on no-value targets); Amulet Scapeshift
payoff-blindness and ramp over-ramp (both correct on current code). Clock
sign-inversion stays falsified, not re-proposed.
