---
title: EV orchestration audit — currencies, context and duplicate valuation across the AI scoring paths
status: active
priority: primary
session: 2026-09-16
supersedes: []
depends_on:
  - docs/design/2026-09-16_agentic_decision_architecture.md
tags: [diagnostic, ai-decision-quality, ev, valuation, architecture]
summary: >
  The AI prices plays in 17 distinct valuation currencies. Ten of them are summed into the one
  Play.ev argmax with no conversion contract. The worst mismatches: LOOP_SHORTCUT_MANA=80
  converted at 20/opp_life, which is 1600/opp_life and was the source of the +99.7 Toolbox
  tutor; one card in hand priced at 0.125 inside position_value but 2.5 in every caller; and
  five non-terminal "reject" sentinels between −10 and −990. Verification confirmed 33
  duplicate valuations, including tutor worth three ways, mana spent five ways, one card in
  hand six ways, the clock three ways, evoke three ways and win_swing credited twice.
  Recommendation: one single-owner node, "Valuate Candidate Play Under Current Goal". It
  prices every candidate in win points (WP, where 100 WP is one game, the existing
  WIN_POSITION) from an explicit ValuationContext. Every existing path becomes a leaf that
  returns typed WP terms through one converter, and hard clamps become menu vetoes or
  probability-weighted trade-offs.
---

# EV orchestration audit

**Scope.** This audit asks how the AI's individual EV calculations are scaled, contextualized
and weighed against each other. It does not ask whether any single formula is correct in
isolation. It covers the scoring paths in:

- `ai/ev_player.py`
- `ai/ev_evaluator.py`
- `ai/clock.py`
- `ai/activation_ev.py`
- `ai/board_eval.py`
- `ai/pw_ability.py`
- `ai/response.py`
- `ai/turn_planner.py`
- `ai/combo_calc.py`
- `ai/combo_chain.py`
- `ai/outcome_ev.py`
- `ai/mana_planner.py`
- `ai/scoring_constants.py`
- `ai/strategy_profile.py`
- `ai/llm_decision_scorer.py` and `ai/llm_decision_weights.json`

**Method.** Six read-only mappers each produced a currency map. Three refuters then checked
every claim against the code at HEAD `92a749c`, one for each lens: currency, duplicates and
context. Only claims that survived are stated here, and the refuters' corrections have been
applied. Appendix A lists what was dropped.

**Line anchors.** `ai/ev_player.py` has drifted about +12 lines after line ~1260 relative to
the first mapper's references. Wherever a refuter re-anchored a line, the current line is
used. Lines marked `≈` are mapper-cited and may be off by up to ±15.

**Constants referenced throughout:**

| Constant | Value |
|---|---|
| `CLOCK_IMPACT_LIFE_SCALING` = `CREATURE_VALUE_OUTER_SCALE` = `CLOCK_LETHAL_ADVANTAGE_CAP` | 20.0 |
| `LOOP_SHORTCUT_MANA` (4 × STARTING_LIFE, `engine/constants.py:50`) | 80 |
| `WIN_POSITION` = `LETHAL_THREAT` = `LETHAL_BONUS` | 100 |
| `NO_CLOCK` | 99 |
| `PLAY_VALUE_FLOOR` | −5.0 |

---

## 1. Headline verdict

**The EV calculations are not well orchestrated.** Most of them are individually reasonable
and several are well derived, but four problems break how they combine:

- **They do not share a unit.** The AI prices plays in **17 distinct currencies** (§2.1).
  **Ten** of them are summed or compared directly in the single main-phase argmax over
  `Play.ev` (`ai/ev_player.py` ≈416–810, floor at :807). No conversion contract links them.
- **Context is applied at the wrong layer.** Goal and life-phase weighting are applied once,
  inside `compute_play_ev` (`ai/ev_evaluator.py:3398-3401`). That happens *before* the
  31–35 additive overlays that `_score_spell` then stacks on top, and those overlays never see
  the goal or the phase.
- **The same value is derived many times.** Verification confirmed **33 duplicate
  valuations**. In each, two or more paths re-derive the same quantity with different
  formulas.
- **Some context never arrives.** Several inputs never reach the paths that need them (§5):
  BHI never reaches combat or activations, the matchup is read once in the whole of
  `ev_player`, and colours are ignored by the chain simulators.

**The numbers that make this concrete:**

| Measure | Value |
|---|---|
| Distinct valuation currencies | 17 |
| Currencies summed into the one `Play.ev` argmax | 10 |
| Largest linear term | 1600/opp_life. It enters through `clock.opportunity_cost` (not sink-gated) and through the tutor paths (sink-gated). It is 80 at 20 opponent life, 320 at 5, and larger than a whole win (100) once opponent life < 16 |
| One card in hand, priced inside `position_value` vs in callers | 0.125 vs 2.5 (20×) |
| Magnitudes of "do not cast" sentinels | −10, −20, −50, −100, −990. A sixth, −3, sits *above* the −5 floor, so it is not a reject |
| Reject clamps that are terminal | 0. Every one is a `min()` that later additive terms can lift back above the floor |
| Additive terms in `_score_spell` | 31–35 |
| Where `life_phase` is read directly in `ev_player` | 1 place (`_gate_x_cost_board_wipe`, ≈1185) |
| Matchup reads in `ev_player` | 1 (`decide_attackers`, ≈3234-3262) |
| `ResponseDecider.opp_archetype` | Never assigned |
| Confirmed duplicate valuations | 33 |
| Mapper claims refuted or corrected in verification | ≈40 (Appendix A) |

**What this costs in play.** Each defect found by the 2026-09-15 five-panel audit
(`docs/history/audits/2026-09-15_5panel_deep_audit.md`, units U1–U7) sits on one of these
seams: a term in the wrong currency, a clamp where a trade-off belongs, or a missing input.
The most damaging was U2: the tutor fetched a loop enabler at +99.7 with no sink and then
passed holding 81 mana.

---

## 2. Valuation-currency map

### 2.1 The 17 currencies

| Code | Currency | Unit and scale | Typical owner |
|---|---|---|---|
| C1 | Δposition_value | Mixed turns + resources, terminal ±100. Internally inconsistent (§3.2) | `clock.position_value` via `compute_play_ev`, activation deltas, `decide_combat_trick` |
| C2 | Life-scaled "value units" | clock fraction × 20. 1 mana ≈ 1.0 and 1 card ≈ 2.5 at 20 opponent life. Scales as 1/opp_life | `opportunity_cost`, `creature_threat_value`, most `ev_player` overlays, `per_mana` |
| C3 | Unscaled fraction-of-kill | `card_clock_impact` 2.5/opp_life, `mana_clock_impact` 1/opp_life, with no ×20 | inside `position_value`; `land_denial_value`, deferral exposure, suspend waste, EXILE_GY |
| C4 | Turns-to-lethal | Turns. Sentinel `NO_CLOCK`=99 (also 999 elsewhere) | `combat_clock`, EVSnapshot clocks, `assess_board`; `STORM_HARD_HOLD` = −99×10 |
| C5 | Survival turns | life / incoming power, cap 10, dead sentinel −100 | `life_as_resource`, `score_block_assignment` |
| C6 | VirtualBoard board points | life×5/3 per point, 2.5 per card, 0.3 per mana, `LETHAL_BONUS` 100, dead life −500 | `VirtualBoard.score`, `CombatPlanner.plan_attack` |
| C7 | Flat "EV-unit" preference constants | Unitless literals, not life-scaled | land table (10/12/−12…), `REANIMATE_OVERRIDE_BONUS` 40, `FREE_CAST_TEMPO_BONUS` 1.5, blink 3.0, dash ±2 |
| C8 | Held-response per-CMC units | 4.0–6.0 per held CMC, calibrated against the deleted `pass_threshold` | `_holdback_penalty`, `evoke_budget_penalty` (4.0 per *card*) |
| C9 | Raw mana units | CMC, tap units, 80-mana loop, before any conversion | `choose_tutor_delivery` (non-creature), `choose_sacrifice_victim`, `choose_pw_ability` (+cmc), `_effective_*_cost` |
| C10 | Damage/power points × preference multiplier | damage × `burn_face_mult`; power × `creature_value_mult` 1.5 | `_enumerate_burn_targets` (face), `_consider_equip` |
| C11 | Integer ordinal preference tables | 0–100 ranks, tuples | `choose_pw_ability` `PW_SCORE_*`, `DISCARD_*`, `mana_planner.score_land`, `put_counter_beneficiary` |
| C12 | Pressure sigmoid | 0–1 on a no-evasion clock | `board_eval.assess_board` → evoke/dash/kick |
| C13 | Undeclared LLM weight | Schema says "multiplier, 1.0 neutral". In use it is a multiplier (Tron, Amulet), a flat addend (cycling, cascade) or a storm-count threshold | `llm_decision_scorer.weight` |
| C14 | Probability / ΔP_win | [0,1]; tri-valued {1, 0, NaN}; ΔP_win in [−1, 1] (dead path) | BHI, `bottleneck_probability`, `outcome_ev` (flag off) |
| C15 | Mulligan keep-scores | Two families: `KEEP_SCORE_*` and `MULL_KEEP_*` (both LAND_NEEDED = 10) | `ai/mulligan.py`, `ai/gameplan.py` |
| C16 | Power-equivalent | Activations × 2.5; life 0.25, energy 0.5 per power | `loyalty_pool_value`, `_project_token_bonus` → `persistent_power` |
| C17 | JSON `threat_value` | Undefined units | `get_threat_value` in `evaluate_stack_threat` |

The ten currencies that reach `Play.ev`, and how:

- **C1** from `compute_play_ev`
- **C2** from the overlays and activation credits
- **C3** from `land_denial` and `hand_denial`
- **C4** from `STORM_HARD_HOLD`
- **C5** from inside `position_value`
- **C7** from the land table and flat bonuses
- **C8** from holdback
- **C10** from equip
- **C13** from the cycling addends
- **C16** from the persistent credit

### 2.2 Every verified path

The "Terms" column is the count of additive terms.

#### `ai/ev_player.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `decide_main_phase` fold, ≈416–810 | argmax over cast/land/cycle/suspend/plot/equip/activate/animate | `Play.ev` = sum of C1, C2, C3, C7, C8, C10, C13 | goal (EXECUTE_PAYOFF gates), `_estimate_combo_chain` vs LLM storm threshold, `am_dead_next`, MAIN1 (animation only), graveyard power ≥ 5, lock filter (main-list only, 552-553), `no_signal` | 3 | `REANIMATE_OVERRIDE_BONUS` +40 (735-736); activation dropped if ev + holdback ≤ 0; `PLAY_VALUE_FLOOR` −5 (807); `no_signal` drop (≈778-783); `control_patience`: both branches `continue` (725-729) | +40 flat |
| `_score_spell`, 1206 | cast; reused by flash deploy, optional recast, plot and cast_plotted | C1 plus overlays in C2/C3/C4/C7/C8 | goal and role tags into `compute_play_ev`; BHI (inside `compute_play_ev`); `am_dead_next`; opponent creature count; MAIN1 (blink only); `holdback_applies`; `life_phase` and goal only via `compute_play_ev` | 31–35 | free cast `max(ev,0)`+1.5; PATIENCE −10; X_WIPE −20; BLINK_FIZZLE −50; dead counter −3 (above the floor); evoke −20/+10; −990 via `card_combo_modifier` | engine credit ≤ 1600/opp_life; win_swing ≤ 100 |
| `_overlay_land_sacrifice_fizzle`, ≈908–973 | land-sacrifice tutor | pass-through, or −10 | land count ≥ 4; untap-watcher substring (copy 1 of 3); minimum payoff cmc vs ceil(N/2) | 0 | `min(ev,−10)` | −10 |
| `_gate_blockers_into_lethal`, 987–1003 | any cast while `am_dead_next` | pass-through, or −10 | `am_dead_next`; non-creature mana. Does not check whether the spell answers the lethal attacker | 0 | `min(ev,−10)` | −10 |
| `_overlay_cascade_patience`, ≈1008–1022 | cascade enabler | pass-through, or −10 | FILL_RESOURCE target, graveyard creatures, library reanimation payoff. Ignores the clock and the goal | 0 | `min(ev,−10)` | −10 |
| `_gate_x_tutor_payoff`, 1021 (credit at 1094-1095) | X creature tutor | C9 × per_mana → C2 | X budget, engine picker, `engine_completion_credit` (sink-gated), other tutor access, acceleration tags, `opp_clock` | 1 | `min(ev,−10)` on fizzle or hold | (x_net + engine credit) × **mult** × per_mana |
| `_gate_x_cost_board_wipe`, ≈1125–1204 | X-cost wipe | −20, or None | kill count (counted twice); killable power < 2; `opp_hand_size` ≥ 4 standing in for BHI; `life_phase` PANIC/LETHAL (the only direct read). Never reads own collateral | 0 | `min(ev,−20)` ×5 | −20 |
| `_holdback_penalty` + `_proactive_tap_out_bonus` + `_estimate_opp_threat_prob`, 1942–≈2358 | cast, cycle, equip, activation | C8 | `holdback_applies`; opponent power and hand; held interaction; tax liveness; post-cast colour capacity; raw BHI p_removal/p_counter/p_burn (reactive and not mana-gated) | 2 | 0 when colour capacity preserved; probability floor 0.1; 4.0–6.0 per CMC | about −24 stranded; bonus cost × 4 × (1−p), up to 3.6 per mana |
| `_score_land`, 2360 | land drop | C7 plus one C2 Tron term | untapped capacity; printed cmc; untap watcher (copy 2); `enters_tapped`; hand pips; landfall substring; Urza subtype and library card **names** (2587-2588); clocks; `land_priorities` × 0.25 | 12–14 | horizon [2, 10]; 8 when NO_CLOCK | Tron up to +40 at 20 life; RAMP 12, BASE 10, tapped −10, landfall −12 |
| `_score_cycling`, 2772 | cycle | C2 draw (2784) + C7/C13 flat + C8 | `my_mana`; `_deck_can_return_card`; cycling cost; cascade in hand (castability unchecked); graveyard < 3; `goal.prefer_cycling` | 8–9 | `castable_fraction`; holdback added unconditionally (2859-2864) | flat LLM 10 (combo stack up to 26) vs a draw of about 2.5 |
| `_score_suspend`, ≈2866–3030 | suspend | C7-scaled payoff minus C3 waste | faster castable route; suspend clause; graveyard creatures and hand cyclers; `opp_clock`; urgency. No return-path check | 2 | 0 when a faster route exists or the clause is unparsed | 5.25 per creature vs 0.15 waste |
| `decide_attackers`, 3035 | attack | C6 `score_delta` vs an EV-tuned `attack_threshold`; lethal fold in damage points | pump reach; opponent blockers; **opponent archetype (the only matchup read)**; racing (2 × power); desperation ≤ 6; `burn_low_life_threshold` (a real field, default 10); PUSH_DAMAGE; discard-pump side effect | 5 | own lethal shortcut returns before the planner (3149-3165); reductions −2/−2/−2/−3 | planner simulated lethal ≈ +600 |
| `_score_block_lifespan_delta` / `decide_blockers`, ≈3598–3637 | block | C5, with C2 subtracted as virtual life | P/T, deathtouch, trample, equipment, emergency, protected piece | 2 | `{}` when racing or holding lethal back; −100 dead floor | `noncombat_opportunity_cost` as virtual life |
| `decide_combat_trick`, ≈3714–3771 | pump in the CR 509.4 window | C1 | blocks, first/double strike, deathtouch, trample, prowess. No BHI, no holdback | 1 | must be > 0 | ±100 terminal |
| `decide_flash_deploy`, ≈3663–3685 | end-step flash creature | `_score_spell` | ev > 0 only. **No lock filter** | 0 | ev > 0 | inherits; the lock-branch level is live here |
| plot / cast_plotted, ≈585–594 | plot | `_score_spell` resolve-now EV | no plot cost, delay or flexibility loss | — | — | inherits |
| `_enumerate_burn_targets` / `_choose_targets` / `_pick_best_removal_target`, 4172… | targets | face C10; creature C1 (`permanent_threat`) + carrier C2; planeswalker C7 (4 + 0.5 × loyalty); exile by printed cmc; reanimation by P+T | opponent life vs threshold; own creatures; hexproof; `damage_marked`; burn reach; storm | 2 | face on lethal; face ×0.1 only with no creatures AND opponent life > threshold (4225) | aggro face at low life 2.5 × damage |
| blink terms + `decide_optional_recast`, ≈1680–1780, 4764–4840 | blink / recast | 3.0 flat C7; forfeited attack C2 (≤ 20); rider C2; reservation C2 | MAIN1; end-of-turn exile registry; static `etb_value` tag; haste | 4 | BLINK_FIZZLE −50; rider withheld in MAIN1 | −50 |
| `_consider_equip`, 5032 | equip | C10 (power × 1.5) + C8 | equipment tag; cost; `permanent_threat` for the carrier; oracle +X/+Y. No phase, no attack plan | 2 | fallback 2.0 | 9.0 (6-artifact Plating) |
| `decide_mulligan`, ≈345–410 | mulligan | C15 via profile integers, then MulliganDecider | land count; conjunction veto. No play/draw, no matchup | 0 | 0 lands; keep at ≤ 5; ≥ 6 lands mulligans | n/a |
| Amulet+Titan overlay, 1531–1539 | cast | C13 w × per_mana (w = 4.0 for combo/ramp, 0 otherwise) | oracle shape; land count; archetype label | 2 | `max(1, 6−lands)` | 16 at 5 life |
| Storm force-advance, 623–637 | goal shift | storm count ≥ `weight()` (5.0), a count | `can_kill`, `storm_count`. No BHI | 0 | irreversible goal advance | threshold 5 |

#### `ai/activation_ev.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `land_animation_candidates`, 49–109 | animate a land | C2 (`creature_clock_impact` × 20) | combat clocks; count of other untapped lands (no colour check). **No holdback** (ev_player 476-481) | 1 | skip unless the clock gets faster and stays ≤ `opp_clock` | about 3 for a 3/3 |
| `choose_sacrifice_victim`, 112–140 | which permanent pays a sacrifice cost | min over C2 (creatures) vs C9 + C2 (non-creatures) | `opportunity_cost` inputs. Ignores combat state and recursion (undying is priced *higher*) | 2 | — | engine term 1600/opp_life (not sink-gated) |
| `choose_tutor_delivery`, ≈143–200 | which card a tutor delivers | lexicographic (lethal, completes, value); value = engine credit C2 + `creature_threat_value` C2, or raw cmc C9 | engine credit (sink-gated); "completes" tier (sink-blind, 188-190) | 2 | — | engine tier |
| `_choose_land_delivery`, ≈203–236 | which land a land tutor delivers | (bounce bool, `_score_land`) on a **throwaway EVPlayer** | untap watcher; tutor batch | 1 | exceptions → 0 | bounce tier |
| `untap_beneficiary`, 349–382 | untap target | C9 tap units | whether the permanent is tapped. Ignores whether freed mana has a use | 0 | — | 1 |
| `graveyard_hate_plan`, 385–442 | exile-from-graveyard plan | integer fuel count, then × unscaled C3 | `graveyard_fuel`. Ignores card quality | 2 | `[:count]` | about 0.125 per card |
| `activation_candidates`, 445–≈958 | generic activated abilities | C1 delta + UNTAP C2 + EXILE_GY C3 + TUTOR_BF C9 × per_mana; GRANT_HASTE turns × 20; TUTOR_TO_HAND +1 hand (≈0.125) | legality; MAIN1 for PUMP/HASTE only; engine veto; engine credit; X budget. **No BHI, no goal** | 2+ | ev ≤ 0 dropped; PUMP gates on snapshot clock but haste/animation on `combat_clock`; PUT_COUNTER `continue` (786) makes 787-831 dead; ±100 leaks through the delta | engine credit / lethal-line swing ≤ 100; haste up to 20 |

#### `ai/board_eval.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `assess_board`, 90–148 | context for evoke/dash/kick | own clock (opp_life/power); pressure C12 | `DOMAIN_POWER_CREATURES` **name set** (533-534); computes three hand fields no caller reads | 0 | 99; 0.8; 0.2 | pressure 0.8 |
| `_eval_kick`, 225–251 | kick count | integer | ignores `a`; kicks Fog if any opponent creature can attack; oracle substring (241) | 0 | ≤ 1 | 1 |
| `_eval_evoke`, 254–404 | evoke. **Runs inside engine legality** (`cast_manager.py:587` in `can_cast`, and again at 1499) | C12 − threshold + C8 per card + ±10 + bare −2.0 | stack; targets; lifegain; colour from ALL lands, tapped included; archetype thresholds; prior evokes | 3 | ±10; −2.0 (341); +1.0 default; −4 per prior evoke | −4 × prior |
| `_eval_dash`, 407–445 | dash vs hard-cast | C7 small flat | turn ≤ 3; blockers; pressure. Reads no card attribute | 4 | ±10 | +2.0. With blockers the net is +0.7, so dash always wins |
| `_eval_combo`, `_life_value` | dead (no caller; `COMBO_NOW` is never constructed) | — | — | — | — | — |

#### `ai/clock.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `position_value`, 773–886 | the universal board evaluator | C1 = C4 `clock_diff` + C3 card/mana + C5 life + C2 persistent/artifact | life; power; evasion; toughness; hand; mana; lifelink; `persistent_power` × urgency; artifacts. Ignores goal, archetype, phase, sickness and colours | 7 | ±100 terminal; ±20 in the one-sided branches; finite branch uncapped (deliberately: falsified 2026-08-30 and 2026-09-04); mana `max(0,·)` | `clock_diff` |
| `combat_clock`, 73–99 | turns to kill | C4 | power; evasion; toughness/3 | 2 | 99 | 99 |
| `life_as_resource`, 102–122 | survival | C5 | life; incoming power | 1 | −100; cap 10 | −100 |
| `card_clock_impact` / `mana_clock_impact`, 129–157 | value of one card / one mana | C3 | `opp_life`; `my_mana` | 1 | floor at 1 life; literal 3.0 and 0.2 | 2.5 at 1 life |
| `opportunity_cost` family, 593–708 | chump, sacrifice, discard, creature value | C2 | snapshot; keywords; tags; equipment ceiling; engines lost | 5 | — | engine term 1600/opp_life, not sink-gated |
| `creature_clock_impact`, 378–534 | per-creature clock | C3 (callers apply ×20); some terms over `my_life` | P/T; keywords; tags; **live LLM `weight('*')` for cascade** (458-465). Ignores controller perspective | 12–14 | 0-power path; compounding multipliers ≈ 2.8× | ETB/annihilator 2/opp_life |
| `life_phase`, 928–967 | gear-shift | enum | `am_dead_next` (raw power); continuous clocks | 0 | early threshold 4.0 | n/a |
| `scarce_payoff_commit_ev`, 279–371 | fire a finisher now vs develop | kill fraction × combo_value (C1) | damage; `line_can_grow`; survival. No BHI | 2 | `min(1,·)`; binary `develop_reach` | combo_value |
| `forfeited_attack_clock_impact`, 475–510 | cost of a lost attack | C3 (callers apply ×20) | power; double strike; lifelink. No blockers | 2 | `min(1,·)` | 20 after scaling |
| `score_block_assignment`, 975–1024 | block | C5 | post-state life/power | 2 | inherits −100 | −100 |
| `combo_clock`, 220–257 | opponent combo urgency (`engine_disruption` only) | C4 | storm; hand; mana; graveyard | 4 | caps | 8 |

#### `ai/ev_evaluator.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `compute_play_ev`, 3137–3437 | base cast EV | C1 delta. **Exceptions:** the lock branch returns a *level* (3299); deferral returns −exposure (C3) | signals; `_project_spell`; BHI (3233-3238); lock; worthiness; urgency; combo chain; assembly; `life_phase`; goal; archetype weight tables | 4 | −100 self-kill; literal `max(ev,−2.0)` for cmc ≤ 2 (3317-3319); urgency multiply (3308); phase×goal weight also rescales win_swing (3398-3401) | win_swing ≤ 100, credited twice (3346 + 3371) |
| `estimate_opponent_response`, 2791–2973 | response discount | C14 blended into a snapshot | BHI probability; `opp_mana`. The blend **drops** persistent, artifact and enchantment fields and truncates with `int()` | 2 | unchanged when p ≤ 0 or opp_mana < 1 | loses the artifact term (×20) when p > 0 |
| `_project_spell`, 1989–2694 | projection | snapshot field deltas | tags; oracle; game; the burn gate counts creatures, not power | ≈20 | burn factor; literal +2, `max(p,2)` | mass reanimation / full burn |
| `_enumerate_this_turn_signals` + `_compute_exposure_cost`, 1057–1531 | cast now vs defer | list + C3 | active player; stack; storm; hand; `opp_clock_discrete` | 2 | `[]` means defer | ≈0.5 |
| `_estimate_combo_chain`, 3004–3134 | can the chain kill | bool/int | untapped lands + pool; finisher dispatch by **name** (3123, 3128) | 0 | draw adds +1 mana (literal) | `can_kill` |
| `estimate_pass_ev`, 3440–3477 | pass; only live consumer is the lock branch | C1 **level** + penalties | mana; opponent power; hand; `has_combo_chain` | 4 | 0.5 per mana; literal 1.0 per mana (combo); −2 on full hand | the level |
| `estimate_future_value`, 3484–3558 | look-ahead | discounted level | deck land density; `combat_clock` | 1 | decay [0.5, 0.95] | level |
| `estimate_spell_ev`, 761–772 | engine Scepter-imprint choice (`engine/card_effects.py:811-817`) | C1 raw delta | projection only | 1 | — | delta |
| `creature_threat_value`, 651–731 | removal targets; engine target picks | C2 | **the caller's** snapshot; oracle "for each"; attack triggers | 2 | unbounded | inherited engine term |
| `expected_future_value` / `loyalty_pool_value`, 1912–1986 / clock 717–752 | planeswalker persistent credit | C16 → C2 via `position_value` | loyalty; `opp_clock` (blocker-blind) | 1 | min(loyalty, survival) | loyalty × 2.5 |
| `_project_token_bonus`, 1534–1787 | token/life/energy credit | C16 | oracle; trigger rates | 3 | life 0.25, energy 0.5 | token power × rate × 2 |
| `snapshot_from_game`, 408–521 | state every valuation reads | fields | **never sets `persistent_power`**; template keywords only; no phase, no sickness or tapped state | 0 | — | n/a |
| `score_card_for_opponent_strip`, 3599–3684 | discard target | C2 for creatures vs C11 (0–100) table for non-creatures | victim roles; tags | 12 | cmc tiebreak | 100 |

#### `ai/pw_ability.py` and `ai/response.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `choose_pw_ability`, pw_ability 219–421 | loyalty slot | C11 + target cmc (C9) + damage points | loyalty; opponent creatures by **cmc**; opponent-only wipe count; `life_phase` | 5 | −100 initial; **always ult when affordable** (247-248); suicide −59; decline sentinel | 59 |
| `ultimate_win_line_value`, pw_ability 169–206 | planeswalker cast credit | C2 (20/horizon) | own loyalty-line parser; combat clock | 1 | 0 unless a win-lock line | 20 |
| `decide_response`, response 118–703 | respond | threat vs floors (C1/C2) | `bottleneck_probability`; tags; oracle "can't be countered"; tax (legacy path only) | 0 | bp == 1 → 100; NaN hold < 50; floor ×1.5/×0.5 | 100 |
| `evaluate_stack_threat`, response 1073–1293 | threat value | C1 base (opponent perspective) max'd with C17 JSON and C2 `creature_threat_value`, then + C2 terms | `opp_archetype` (**never assigned**, always "midrange", then ignored); ignores the stack item's targets | 9 | `LETHAL_THREAT` 100; `max(0,·)` | 100; equipment 60 × pb/our_life |
| `TurnPlanner.evaluate_response`, turn_planner 852–929 (runs first, response 437) | respond | threat − cmc × 0.5/0.3 vs absolute 5 / 2 / 4 / 3.5 | responses built with `damage=0`, `keywords=set()` (response 392, 430); **no** tax, kind or SPELL gates | 3 | net ≤ 0 | threat |
| `_held_counter_floor_ev`, response 827–850 | counter floor | C2 | our snapshot only. No counter count, no BHI | 1 | max 1.0 | about 10 |
| `_effective_counter_cost` / `_effective_paid_cost`, 935–948 / 852–912 | cheap-trade gates | C9 mixed with "1 exiled card" | engine cost re-derived | 5 | `max(0,·)` | cmc |

#### `ai/turn_planner.py`, `ai/combo_*.py`, `ai/outcome_ev.py`, `ai/mana_planner.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `VirtualBoard.score` / `_life_score`, 183 / 201 | board points | C6 | fixed `LIFE_SCORE_AVG_INCOMING` 3 | 5 | −500 dead; +50 cap | −500 |
| `CombatPlanner.plan_attack`, 242 (live via `decide_attackers`) | attack configuration | **three currencies:** flat 100 (quick lethal), raw power (no blockers), C6 delta | opponent life bands; literal `opp_mana` ≥ 2; `damage_to_me` = 0 always (470, 593) | 9 | −1 initial; 32 configs | ≈ +600 simulated lethal |
| `_predict_blocks` / `_predict_block_score`, 601 / 722 | forecast of opponent blocks | C6 (coverage) vs C5 (optimize) | omits the `noncombat_opportunity_cost` charge | 2 | double-block ≥ 4.0 | — |
| `TurnPlanner.plan_turn` + 5 orderings, 815–1191 | **dead** (ISMCTS fallback only) | C6 + mixed bonuses | — | — | removal as a 99-damage sentinel, etc. | — |
| `assess_combo` + zones, combo_calc 77–420 | combo readiness | kill fraction; combo_value C1; `risk_discount` = 1.0 live and never read | hand, **lands included**, as fuel; colourless mana | 3 | literal ×2 and 0.5 | combo_value > 200 possible |
| `card_combo_modifier`, combo_calc 919 | combo-deck cast overlay | C1 via combo_value, **except** the mid-chain ritual penalty (unscaled, 1297-1317) and `STORM_HARD_HOLD` (C4 × 10) | storm; hand; library; reducers; `am_dead_next`. No BHI, no colours | 1 | −990; literal `opp_clock > 2` | −990 |
| `bottleneck_probability`, combo_calc 661 | fire or hold a counter vs a chain | {1, 0, NaN} | an opponent archetype of "combo" counts as mid-chain on every turn; the decklist-wide payoff scan ignores opponent mana | 0 | NaN defers | — |
| `flashback_chain_viable`, combo_calc 818 | ritual gate | graded fraction, used as `== 0.0` | granters; graveyard fuel | 1 | 0.0 | — |
| `find_all_chains`, combo_chain 234 | chain facts | storm counts | **no `is_land` filter**; only RED reducers | 0 | fuel budget 7 | storm |
| `build_combo_distribution`, outcome_ev 220 | **dead** (flag off) | C14 ΔP_win | fresh goal engine | 5 | fizzle = 0 | — |
| `mana_planner.score_land`, 369 (fetch target) | fetch target | C11 land-preference points | mana needs; gameplan priority (unbounded). **No life input** | 11 | −999 init | 8 per pip demand |
| `should_stagger_shock`, 547 | pay life for a second shock | bool | defers whenever the hand is non-empty | 0 | 12 life | — |

#### `ai/strategy_profile.py` and `ai/llm_decision_scorer.py`

| Scoring function (file:line) | Decision | Currency | Context read | Terms | Clamps / sentinels | Largest term |
|---|---|---|---|---|---|---|
| `llm_decision_scorer.weight`, 335 / `_lookup_default` 214 | 8 contexts | C13 | (archetype label, context) only; "cascade" archetype rows never match | 0 | NEUTRAL 1.0 | 10 |
| `phase_weight_multiplier` / `goal_weight_multiplier` / `apply_preference_weight`, 328 / 497 / 379 | multiplicative re-rank of `compute_play_ev` | dimensionless 0.7–1.5 | only control/midrange/aggro; **no LETHAL row**; only `close_game`; ev < 0 → ev/w | 0 | identity on a miss | 1.5 (compounds to 2.25) |

---

## 3. Incommensurabilities, ranked

Each entry gives the two sides with their units, where they meet, and the decision it corrupts.

1. **Engine completion: 80 mana converted at 20/opp_life, added to clock-scaled EV.**
   - *Sides:* `LOOP_SHORTCUT_MANA` = 80 (C9) × per_mana = 20/opp_life, i.e. **1600/opp_life**
     (80 at 20 life, 320 at 5), against Δposition_value (C1, whose terminal is ±100).
   - *Where they meet:* summed linearly into `Play.ev` at `ai/ev_player.py:1094-1095` (cast
     gate, multiplied by X-multiplier `mult`) and at `ai/activation_ev.py:936-950` (activation,
     no `mult`). It is re-inlined in `clock.opportunity_cost` (`ai/clock.py:701-706`), which
     is **not** sink-gated and feeds sacrifice choice and chump-block virtual life.
   - *Corrupts:* the X tutor and tutor delivery. This is the 5-panel U2 defect: Toolbox's
     single tutor fetched the loop enabler at **+99.7** (`toolbox_zoo.txt:525`) and then
     passed holding 81 mana.
   - *Current state:* the three tutor paths now share `assembly_state.engine_completion_credit`,
     which returns 0 without a reachable sink. The incommensurability remains:
     - with a sink, the credit still exceeds a whole win once opponent life < 16;
     - `choose_tutor_delivery`'s "completes" tier (≈188-190) is still sink-blind;
     - `opportunity_cost` still prices every engine member at 1600/opp_life.

2. **`position_value` sums terms on different scales, and callers re-scale them.**
   - *Sides:* `card_value` and `mana_value` (C3: 0.125 per card, 0.05 per mana at 20 life;
     `ai/clock.py:842-846`) against `persistent_value` / `artifact_value` (C2: ≈1.0 per power
     or per artifact; 864-883) and `clock_diff` (C4: ±20 capped, uncapped in the finite
     branch).
   - *Consequences:*
     - An artifact on the board is worth about 8 cards in hand. The comment at 858-860 claims
       they share one scale; they do not.
     - Callers price the same card at `card_clock_impact` × 20 ≈ 2.5 (`ev_player.py:1564`,
       2784; `response.py:361`, 849; `clock.py:674-675`; `CARD_IN_HAND_VALUE` 2.5), which is
       20× the internal rate.
   - *Corrupts:* every trade between cards and board. Any `win_swing` = 100 − position_value
     inherits the inconsistency. Examples:
     - `hand_denial_value` sums `creature_threat_value` (C2) for creature cards with
       `card_clock_impact` (C3) for non-creatures (`ai/hand_denial.py:121-150`).
     - `land_denial_value` credits C3 (`ai/land_denial.py:204-221`) while the waste penalties
       it competes with charge C2 (`ev_player.py:1589`, 1631, 1643, 1671). The gap is 20×.
     - The suspend waste term (C3, `ev_player.py:3029`) against its payoff (5.25 per
       creature) is decorative.

3. **"Reject" is five magnitudes of a non-terminal `min()`.**
   - *The clamps:*
     - PATIENCE −10 (land-sacrifice, cascade, X-tutor and blocker gates);
     - X_BOARD_WIPE −20;
     - BLINK_FIZZLE −50;
     - `compute_play_ev` −`LETHAL_THREAT` −100;
     - `STORM_HARD_HOLD` −990 (−`NO_CLOCK` × 10, a turn sentinel used as value;
       `combo_calc.py:525-530`).
   - *Also:* `NONCREATURE_COUNTER_DEAD_FLOOR` −3 sits **above** `PLAY_VALUE_FLOOR` −5, so a
     "dead" counter is still castable.
   - *Why it is not terminal:* all are `min()` clamps, and `_score_spell` keeps adding terms
     after them: removal premium, holdback (1916-1920; the relevance-gate branch at 1994-2000
     can return a positive bonus even with `holdback_applies=True`), `stax_lock_ev`
     (1932-1933) and the phyrexian penalty. A patience-rejected cast can therefore end above
     the floor, although the sentinel's docstring (`scoring_constants.py` ≈2196) claims it is
     "unconditionally below".
   - *Corrupts:* every hold decision.

4. **The locked-cast branch returns a level, not a delta.**
   - *Sides:* `ev = estimate_pass_ev(snap) − card value` (`ai/ev_evaluator.py:3299`), where
     `estimate_pass_ev` is `position_value` + penalties (an absolute C1 level), against the
     `after − current` deltas every other branch returns.
   - *Consequences:*
     - A locked cast on a good board scores the board's value.
     - Urgency (3308-3309) and the cmc ≤ 2 floor (3317-3319) then distort it further.
     - `after_value = current + ev` (3300) double-counts the level in the detailed output.
   - *Where it is live:* `decide_main_phase` filters locked spells first (544-553), so the
     main phase is safe. The branch is live in `decide_flash_deploy` (accepts any ev > 0),
     `cast_plotted` (585), `decide_optional_recast` (4837) and the ISMCTS proxy
     (`ai/search/cardinstance_proxy.py:403`).
   - *Corrupts:* a flash creature can be deployed straight into a counter-on-cast lock.

5. **The opponent-response blend drops terms at p > 0.**
   - *Where:* `estimate_opponent_response` rebuilds its blended snapshot without the artifact,
     enchantment and scaling flags, `persistent_power`, `my_mana_by_color`,
     `opp_gy_creatures` or `archetype_subtype`, and truncates every field with `int()`
     (`ai/ev_evaluator.py:2950-2973`). It returns the full snapshot only when p ≤ 0 or
     `opp_mana` < 1 (2833, 2872).
   - *Consequences:*
     - `after_value` (without these terms) is subtracted from `current_value` (with them),
       giving a step change at p = 0+.
     - An artifact-scaling deck is charged its whole current `artifact_value` whenever the
       opponent has one open mana.
     - For planeswalkers and recurring tokens, the projection's persistent credit (2115,
       2147) vanishes. `current_value`'s own persistent term is always 0 anyway, because
       `snapshot_from_game` never sets it.
   - *Corrupts:* casting into open mana, for artifact and planeswalker decks.

6. **The attack scale vs the attack threshold.**
   - *Sides:* `plan_attack` returns flat `LETHAL_BONUS` 100, raw summed power, or a C6 delta
     (≈ +600 on simulated lethal, from the −500 dead `_life_score`; `ai/turn_planner.py:183-206`,
     253-292). `decide_attackers` compares it with `attack_threshold` (−0.5 / −1.0) minus
     reductions of 2/2/2/3 that were tuned in EV units (≈3269-3288), plus trigger bonus 1.5.
   - *Magnitude:* the anti-combo −3 alone exceeds a full trade-down penalty.
   - *Live inconsistency:* `decide_attackers`' own lethal shortcut returns before the planner
     (3149-3165), so the live problem is the keep-home worth measure (`noncombat_opportunity_cost`
     > power vs `VirtualCreature.value`), not 100 vs 600.
   - *Corrupts:* attack or hold.

7. **The land table vs spell deltas in one argmax.**
   - *Sides:* `_score_land` is an integer preference table (`LAND_BASE_EV` 10, +12/+8/+5,
     −10/−12; C7) with one C2 Tron term, 4.0 × turns (≤ 10) × 20/opp_life ≤ 40 at 20 life
     (≈2565-2593). It competes on `Play.ev` with Δposition casts (≈−5..+15).
   - *Consequences:*
     - A land essentially never falls under the −5 floor. `LANDFALL_DEFERRAL_PENALTY` −12 is
       the only way a spell outranks a land.
     - The Tron and Amulet terms scale with 1/opp_life while `LAND_BASE_EV` and RAMP do not,
       so their ranking flips as the opponent's life falls.
   - *Corrupts:* land drop vs spell ordering.

8. **The LLM weight has no declared currency.**
   - `ai/llm_schemas.py:427-434` documents a multiplier with 1.0 neutral. In use it is:
     - a multiplier for Tron and Amulet (`ev_player.py:2571-2573`, 1535, 1539);
     - a raw flat addend for cycling (2832-2855) and cascade (`clock.py:465`, /opp_life);
     - a storm-**count** threshold (`ev_player.py:631-635`).
   - The same "neutral" 1.0 is a no-op, +1 EV, or "force payoff after one spell", depending on
     the site.
   - *Corrupts:* cycling and cascade priority, and the combo goal advance.

9. **Burn target choice over three scales.**
   - *Sides:* face = damage × `burn_face_mult` (C10: 0/0.5/1.5, or 2.5 at low life);
     creature = `permanent_threat` (C1 marginal) + carrier bonus (C2); planeswalker =
     4.0 + 0.5 × loyalty (C7) (`ai/ev_player.py` ≈4221-4278).
   - *Consequence:* a 3-damage aggro burn at ≤ 10 life scores 7.5 face, above a 6-loyalty
     walker (7.0).
   - *Corrupts:* face vs removal.

10. **Tutor delivery and sacrifice choice mix C2 with raw mana (C9).**
    - `choose_tutor_delivery` value is engine credit × per_mana + `creature_threat_value`,
      **or** + raw cmc (`ai/activation_ev.py:176-197`). A 3-cmc non-creature is 3.0 at any
      life total.
    - `choose_sacrifice_victim` takes the min of `opportunity_cost` (C2) and
      cmc + tap units × per_mana (132-138). The two agree only near opponent life 20.
    - *Corrupts:* which card is fetched, and which permanent is sacrificed.

11. **Holdback is priced in a coefficient anchored to a deleted quantity.**
    - `held_response_value_per_cmc` 4.0–6.0 is justified against the CONTROL `pass_threshold`
      −5.0 (`scoring_constants.py:52-55`). That threshold was deleted in M3
      (`strategy_profile.py:135-143`).
    - The bonus branch, cost × 4.0 × (1−p), gives up to +3.6 per mana
      (`ev_player.py` ≈2295-2297), in the same `Play.ev` as projection deltas of a few points.
    - It is applied **unconditionally** to cycling (2859), equip (5092) and activations (505),
      but gated on `holdback_applies` for spells (1916). For `holdback_applies=False`
      profiles it returns the positive `_proactive_tap_out_bonus` (1991-1993). The same mana
      is therefore priced differently by action type.
    - *Corrupts:* tap-out decisions.

12. **Equip in raw power points.**
    - ev = power bonus × `creature_value_mult` 1.5 (`ev_player.py:5084-5085`; per-artifact
      counts at 5120-5133; fallback 2.0). A 6-artifact Plating scores 9.0 + holdback.
    - This coincides with Δposition_value only at 20 opponent life with no opposing clock
      (where `clock_diff` ≈ +1 per power). It diverges everywhere else.
    - *Corrupts:* equip vs cast.

13. **Evoke on the pressure axis.**
    - `_eval_evoke` sums pressure (0–1 sigmoid) − threshold with `evoke_budget_penalty`
      (a per-CMC 4.0 coefficient applied per **card**, −4 × prior evokes), a bare −2.0 (341),
      a +1.0 default and ±10 (`ai/board_eval.py:315-404`; `scoring_constants.py:174-224`).
    - Its waiver threshold is `creature_threat_value` ≥ 8.0 (C2).
    - *Corrupts:* evoke legality and mode choice (it runs inside `can_cast`).

14. **Combo hold terms that combo_value does not scale.**
    - `card_combo_modifier`'s hold and fire terms are correctly in C1 via combo_value.
    - The **mid-chain ritual penalty** (storm+2)/opp_life × 5 × escalation + a miss-risk term
      is **not** multiplied by combo_value (`ai/combo_calc.py:1297-1317`).
    - `STORM_HARD_HOLD` is −990.
    - All are summed at `ev_player.py:1349`.
    - *Corrupts:* ritual sequencing inside a chain.

15. **Response threat vs the counter floor.**
    - `evaluate_stack_threat`'s base is a Δposition_value from the opponent's perspective,
      whose card and mana terms are C3. It is max'd with the JSON `threat_value` (C17) and with
      `creature_threat_value` (C2), then summed with C2 terms such as equipment
      60 × pb/our_life (`ai/response.py:1090-1291`).
    - `TurnPlanner.evaluate_response` subtracts cmc × 0.5 or 0.3 (C9) and compares against the
      absolute constants 5 / 2 / 4 / 3.5 (`ai/turn_planner.py:874-918`). Its removal kill test
      is always true because responses carry `damage=0` (`response.py:430`).
    - *Corrupts:* whether to counter or respond.

16. **Engine value subtracted as life when blocking.**
    - `_score_block_lifespan_delta` subtracts `noncombat_opportunity_cost` (C2) from life as
      "virtual life" (C5; `ev_player.py:3602-3622`).
    - An engine piece worth 80 drives virtual life to ≤ 0 and hits the −100 sentinel, so it
      can never be spent in a block.
    - *Corrupts:* chump-block decisions.

17. **Planeswalker slot choice.**
    - `choose_pw_ability` adds target cmc (C9) and damage points to integer ranks (C11)
      (`ai/pw_ability.py:296-320`).
    - A 7-cmc bounce (22) beats killing a 1-cmc creature (21). Killing a 6-cmc creature (26)
      beats a big wipe (25).
    - The same walker's cast is credited in C2 (`ultimate_win_line_value`, 20/horizon).
    - *Corrupts:* the loyalty slot (with U1).

18. **Activation branch internals.**
    - GRANT_HASTE credits turns-saved × 20 (≤ 20) while animating the same body is worth ≈ 3
      (`ai/activation_ev.py:102-103`, 694-696).
    - TUTOR_TO_HAND is +1 hand (≈ 0.125) while TUTOR_TO_BATTLEFIELD is delivered cmc ×
      per_mana (≈ 4), about 30× apart for one effect class.
    - DRAW_N with a life cost charges life through `life_as_resource` but credits cards at
      0.125 each.
    - `position_value`'s ±100 leaks through the delta, so a lethal ping scores ≈ +100 − base.
    - *Corrupts:* activation vs cast.

19. **Flat nudges justified only against each other.**
    - `FREE_CAST_TEMPO_BONUS` 1.5; removal premium ×0.5 + `CHEAP_REMOVAL_ACTION_BONUS` 1.0;
      `BLINK_ETB_RETRIGGER` 3.0; `REMOVAL_DEFERRAL_TARGET_GAP` 4.0 × p.
    - The phyrexian penalty is life_cost/my_life × 10 (`ev_player.py` ≈1935-1938), unrelated
      to `life_as_resource`.
    - `_project_token_bonus` converts life at 0.25 power-equivalent, while direct ETB life goes
      through `life_as_resource`. The same life point has two prices.

---

## 4. Duplicate valuations, ranked

Each entry names the paths that re-derive one value, and the single owner it should have.
Owners use the node names from §7.

1. **Tutor: worth of the delivered card.**
   - *Formulas:* three.
     - Cast gate: (`creature_tutor_x_net_value` + engine) × `mult` × per_mana
       (`ev_player.py:1094-1095`).
     - Activation: delivered_cmc + engine credit, + x_net only when `mv_bound_is_x`, no `mult`
       (`activation_ev.py:936-950`).
     - Delivery ranking: `creature_threat_value` or raw cmc (194-198).
   - *Mismatch:* the activation EV prices the target picked by `pick_activated_tutor_x` /
     `default_tutor_rank` (completes, cmc, P+T; `engine/activated_effects.py:159-175`, 215,
     221). The card actually delivered is chosen by `choose_tutor_delivery` through the
     `choose_tutor_target` callback (`engine/game_runner.py:286-288`). **The priced target and
     the delivered target can differ.**
   - *Separate model:* TUTOR_TO_HAND is +1 hand, blind to what is fetched (908-914).
   - *Owner:* one leaf, **Value Delivered Tutor Target**. It chooses the card and returns its
     WP, so pricing and delivery are the same call.

2. **Mana spent.**
   - *Models:* five or more.
     - Deferral exposure, cmc × `mana_clock_impact` (C3).
     - `position_value`'s `max(0, diff)` (so spending contributes 0).
     - ×20 re-inlined at `ev_player.py:1589`, 1631, 1643, 1671.
     - `_holdback_penalty` per held CMC (1917).
     - `estimate_pass_ev` 0.5 per mana + 1.0 per mana for combo (3453, 3473).
     - `_blink_reservation_penalty` as a second colour-aware reservation (1777, 4839).
   - Activations add holdback on top of `activation_ev`'s own cost term.
   - *Owner:* **Price Mana Committed By Play**, one charge per play through the converter.

3. **One card in hand.**
   - *Prices:* six.
     - 0.125 inside `position_value`;
     - `card_clock_impact` × 20 in the overlays;
     - × 15 for evoke (`EVOKE_CARD_LOSS_MULTIPLIER`);
     - `CARD_IN_HAND_VALUE` 2.5;
     - `CARD_DRAW_BASE_VALUE` 2.0;
     - `ORACLE_DRAW_VALUE_PER_CARD` 1.0.
   - *Owner:* **Convert Resource Quantity To Win Points** (`kind=card`).

4. **The clock.**
   - *Formulas:* three.
     - `combat_clock` (evasion + toughness/3, `clock.py:73-99`);
     - EVSnapshot continuous life/power (`ev_evaluator.py:349-378`);
     - `assess_board` re-inlines the EVSnapshot formula (`board_eval.py:115-118`).
   - Inside `activation_ev`, PUMP gates on the snapshot clock (675-676) while haste and
     animation gate on `combat_clock`.
   - *Owner:* **Assemble Valuation Context** computes one blocker-aware clock pair.

5. **Evoke.**
   - *Evaluators:* three.
     - `board_eval._eval_evoke`, **inside engine `can_cast`** (`cast_manager.py:587`) and
       again at cast (1499);
     - the `ev_player` overlay, −`card_clock_impact` × 15 / +10 / −20 (1315-1323);
     - the engine no-target veto (`cast_manager.py:576-585`, 1502-1511), also repeated in
       `board_eval` (319-326) and `ev_player` (1322-1323).
   - *Owner:* evoke is one cost-mode candidate valued by **Price Mana Committed By Play**
     (the pitched card). The no-target case becomes a menu veto (§6). Strategy leaves engine
     legality.

6. **Win swing.**
   - A chain starter gets p × win_swing at `ev_evaluator.py:3346`, and again as
     `assembly.best_line.first_step` (3371).
   - A storm payoff at lethal range additionally receives `card_combo_modifier`'s combo_value
     (`combo_calc.py:960-972`), summed at `ev_player.py:1349`.
   - The partial-damage fire decision is owned twice: the literal gate dmg ≥ opp_life//2 or
     `opp_clock_discrete` ≤ 2 (3347-3359), and `scarce_payoff_commit_ev` (`combo_calc.py:1041`).
   - combo_value re-derives win_swing with its own ceiling and floor (`combo_calc.py:146-157`
     vs `clock.py:766-770`).
   - *Owner:* **Weigh Terminal Game Outcomes**. Win or loss is credited once per candidate.

7. **Reanimation.**
   - `REANIMATE_OVERRIDE_BONUS` +40 (graveyard power ≥ 5; `ev_player.py:735-736`) and
     `_reanimation_readiness_boost` opp_life/2 (resource_target; 1396-1404) are both added to
     one cast.
   - The target is then picked by P+T (4544-4550).
   - *Owner:* the reanimated body's projection in **Project Immediate Board Change**, with the
     target chosen by the same call.

8. **Blink target presumption.**
   - *Models:* three for the main phase, one for response.
     - `_score_spell`: max over riders + `etb_value` creatures (1723-1737);
     - `_presumed_reset_target`: max `creature_threat_value` over all creatures (4780-4790);
     - `_choose_targets`: riders by `creature_threat_value`, else `creature_value` (4524-4540);
     - response blink: `VirtualCreature.value` × 0.7 + 3.0 (`turn_planner.py:911-923`).
   - *Owner:* one blink leaf that picks the target and values the realized ETB.

9. **Attack: lethal and keep-home.**
   - `decide_attackers` uses `noncombat_opportunity_cost` > power plus pump reach
     (3145-3165). `plan_attack` uses `VirtualCreature.value` with no pump reach
     (`turn_planner.py:253-267`, 1235-1237), despite the comment "so both paths agree".
   - The rule is inlined again at 3336-3343 and 3385-3393.
   - Racing has two definitions: `is_racing` (opponent life ≤ 2 × power, 3251-3256) and
     `_racing_to_win` (a clock ratio, 3516-3530).
   - *Owner:* **Project Immediate Board Change** (combat projection) + **Weigh Terminal Game
     Outcomes**.

10. **Creature worth.**
    - `permanent_threat` (Δposition marginal) and `_permanent_value` (a power/keyword table,
      `evaluator.py:748-790`) are independent and both feed "which creature matters".
    - Removal virtual power is derived twice: typed +2/+3 in `creature_threat_value` (681-709)
      and an oracle substring +2 / `max(p,2)` in `_project_spell` (2270-2281).
    - It is also credited twice: the removal premium `creature_threat_value` − `creature_value`
      (`ev_player.py:1812-1820`) is added on top of a projection that already removed the
      virtual power.
    - *Owner:* the projection alone.

11. **Response: counter gate.**
    - `TurnPlanner.evaluate_response` runs first and returns (`response.py:377-464`). The tax,
      kind, colorless-only and SPELL checks exist only on the legacy path (484-541).
    - The held-counter floor is computed three ways: unfloored (361), floored (846-850), and
      absolute 5 / 2 in the planner.
    - Tax liveness has two models: binary (532-540) vs fractional
      `_held_tax_counter_liveness` (`ev_player.py:2226-2254`).
    - The removal-worth threshold has four variants: `creature_threat_value` ≥ 4.0 (4922-4946);
      ≥ 3.0 in triage (`response.py:1058-1061`); the same 3.0 applied to
      `estimate_removal_value` (663-671); and 0.8 × threat − 0.3 × cmc ≥ 4.0 (planner).
    - *Owner:* the response seam calls the same valuation node, with the held-response leaf.

12. **Planeswalkers.**
    - `loyalty_pool_value` counts every activation, the ult included (`clock.py:717-752`), and
      `ultimate_win_line_value` adds the ult again (`ev_player.py:1574-1577`).
    - `choose_pw_ability` ults whenever it can afford to.
    - Loyalty lines are parsed three times (`pw_ability.py:120`, `ev_evaluator.py:1843`,
      `engine/player_state.py:395`).
    - *Owner:* **Price Deferred Resource Change** (`kind=loyalty`).

13. **Land choice.**
    - `_score_land`, used for drops and, via `_choose_land_delivery`, for tutor delivery on a
      throwaway EVPlayer (`activation_ev.py:209-236`), vs `mana_planner.score_land` for
      fetchland cracks (`land_manager.py:219`). The constants differ: 3/4 vs 8/4/5.
    - *Owner:* one land leaf.

14. **Chain projection and payoff reachability.**
    - Two chain simulators: `combo_chain.find_all_chains` and `_estimate_combo_chain` (with
      name dispatch).
    - At least five reachability predicates: `_has_storm_finisher`, `_tutor_has_payoff_access`
      (which does not check what the tutor can fetch), `_opp_payoff_reachable`,
      `_payoff_reachable_this_turn`, `_count_finishers_*`.
    - *Owner:* the landed `ai/assembly_state.py` spine (per the architecture doc, 1.3.3).

15. **Dash.**
    - `_eval_dash` uses a flat table (`board_eval.py:407-445`, wired via
      `game_runner.py:263-267` → `cast_manager.py:1471`). `compute_play_ev` flags `dash_cost`
      as a this-turn signal (1124). `decide_attackers` never reads dash state.
    - *Owner:* the combat projection.

16. **The Amulet untap-watcher predicate.**
    - Inlined three times with disagreeing branches (`ev_player.py:938-941`, 1500-1511,
      2397-2405) instead of `LandManager.player_has_untap_on_enter_watcher`.
    - *Owner:* the engine predicate, read in **Assemble Valuation Context**.

17. **Urgency applied twice.**
    - `position_value` multiplies `persistent_power` by urgency (`clock.py:863-864`), and
      `compute_play_ev` multiplies the whole EV by urgency (3308-3309).
    - *Owner:* the converter (`kind=power_per_turn`).

18. **Block prediction vs block decision.**
    - `_predict_block_score` copies `_score_block_lifespan_delta`'s formula but omits the
      virtual-life charge (`turn_planner.py:740-782` vs `ev_player.py:3598-3637`).
    - *Owner:* the combat projection.

19. **X-wipe kill count.**
    - `pick_wipe_x_value` best_kill_count, then a re-scan with its own clause regex
      (`ev_player.py:1150-1183`).
    - *Owner:* the engine picker's result.

20. **Sacrifice.**
    - Chosen by `opportunity_cost`, but charged only as count deltas, with a separate engine
      veto (`activation_ev.py:535-576`).
    - *Owner:* **Price Mana Committed By Play** (sacrifice cost kind).

21. **Two cast-EV paths.**
    - `estimate_spell_ev` (raw projection; engine Scepter imprint, `card_effects.py:811-817`)
      vs `compute_play_ev`.
    - *Owner:* the valuation node, called from an AI callback, not from the engine.

22. **Threat's paid cost.**
    - `_effective_paid_cost` re-derives the engine's affinity, delve, domain and reducer
      computation (`response.py:852-912`).
    - *Owner:* the engine payment record.

23. **Constants and sentinels.**
    - The 20.0 scaling constant has two names (`CLOCK_IMPACT_LIFE_SCALING`,
      `CREATURE_VALUE_OUTER_SCALE`).
    - `ABILITY_BONUS_ETB_VALUE` / `TOKEN_MAKER` duplicate `ETB_VALUE_BONUS` / `TOKEN_MAKER_BONUS`.
    - There are two live mulligan families.
    - The no-clock sentinel is defined six times (99.0 ×3, int 99 ×3, and 999).
    - Lethal is 100 in three currencies (`LETHAL_THREAT`, `LETHAL_BONUS`, `WIN_POSITION`).
    - `NO_CLOCK` is defined in both `clock.py:45` and `scoring_constants.py:4810`.
    - *Owner:* one constant each, with `None` in place of numeric sentinels.

Items 1–23 group the 33 confirmed duplicates. Items 6, 9, 10, 11 and 23 each bundle several.

---

## 5. Context blind spots, ranked

Each entry names the missing input and the observed defect it caused. The 5-panel units are
from `docs/history/audits/2026-09-15_5panel_deep_audit.md`.

1. **Mana colours at the valuation layer.**
   - *Where the input is missing:*
     - `_holdback_penalty` reserves mana for held interaction whose colours the current sources
       cannot produce. It checks only post-cast capacity.
     - `land_animation_candidates` checks land count only (`activation_ev.py:86-87`).
     - `_eval_evoke` reads colours from ALL lands, tapped included (`board_eval.py:345-347`).
     - The chain simulators and `card_combo_modifier` treat mana as one colourless integer.
     - `card_clock_impact` and `mana_clock_impact` ignore `my_mana_by_color`, which is populated
       but never read by them.
   - *Observed:* **U4.** The deck reserved mana for a {U} counter with 0 U sources and gave up
     a tempo deck's turn-1 clock (`jeskai_omnath.txt:50-54`).

2. **Live target value, as opposed to a static tag.**
   - *Where the input is missing:*
     - The blink retrigger +3 is keyed on the static `etb_value` tag.
     - `choose_pw_ability` credits a value rider with no legal target, and always ults.
     - TUTOR_TO_HAND is blind to what it fetches.
     - UNTAP credits mana with no sink in hand.
     - DAMAGE_ANY_TARGET projects only the opponent's face (`activation_ev.py:604-607`).
   - *Observed:* **U3.** A blink hit a creature with no live ETB value (`azb_omnath.txt:155-193`).
     **U1.** A walker ticked 4→1 into an empty board (`45c_tron.txt:228`).

3. **Reachability of a mana sink.**
   - *Where the input is missing:* the engine-completion credit was ungated (§3.1).
   - *Residue:* `opportunity_cost`'s engine term and `choose_tutor_delivery`'s "completes" tier
     are still sink-blind.
   - *Observed:* **U2.** +99.7 for the enabler, then a pass holding 81 mana
     (`toolbox_zoo.txt:525-555`).

4. **Own collateral and utility value.**
   - *Where the input is missing:* `_gate_x_cost_board_wipe` reads only kill count, never own
     collateral (≈1180-1184). `choose_pw_ability`'s wipe branch counts only opponent permanents
     (`pw_ability.py:357-368`). `permanent_threat` prices a non-attacking lock piece at ≈ 0.
   - *Observed:* **U6.** X = 2 killed its own Chalice for a marginal opposing kill
     (`wst_zoo.txt` T22).

5. **A combat projection for mode choices.**
   - *Where the input is missing:* `_eval_dash` reads no card attribute, no opponent removal
     belief and no phase, and grants its bonus in the exact state where a small attacker will
     not attack.
   - *Observed:* **U7.** A Ragavan was bounced having done nothing (`jeskai_omnath.txt:474-482`).

6. **Goal and life phase at the overlay layer.**
   - *Where the input is missing:*
     - Goal and phase weights are applied in `compute_play_ev` only (3396-3401), before the
       roughly 30 overlays (removal premium, holdback, stax, evoke, gates 896–1204).
     - The weight tables cover control, midrange and aggro only, with **no LETHAL row**
       (`strategy_profile.py:288-321`, 487-491), although `life_phase` returns LETHAL before
       PANIC (`clock.py:955-956`). So in the most urgent phase the defensive up-weight
       vanishes.
     - The multiplier also scales a terminal `win_swing` down (×0.7–0.8).
   - *Observed:* **U5.** A declared engine-role creature scored −2.8 under `deploy_engine`,
     below a vanilla 2/2 at +2.1 (`toolbox_zoo.txt:223-227`).

7. **BHI in combat and in activations.**
   - *Where the input is missing:*
     - Pump reach and `decide_combat_trick` price a pump as certain.
     - `CombatPlanner` uses the literal `opp_mana` ≥ 2 (`turn_planner.py:315`) and never
       computes `damage_to_me` (470, 593).
     - There is no BHI reference in `ev_player` between ≈3030 and 4200.
     - `activation_candidates` applies no P(countered) or P(removed).
     - `card_combo_modifier`'s assessment is built without BHI, so `risk_discount` is 1.0 and
       never read.

8. **Matchup.**
   - *Where the input is missing:*
     - The only read in `ev_player` is `decide_attackers` (≈3234-3262).
     - `_estimate_opp_threat_prob` promises an "archetype aggression hint" (2305-2307) but
       never uses one. It uses the reactive posteriors p_removal/p_counter/p_burn as a
       *next-turn threat* probability, so a removal-heavy opponent suppresses the proactive
       bonus.
     - `ResponseDecider.opp_archetype` is never assigned.
     - `llm_decision_scorer.weight` is keyed by archetype label, not by mechanic. For example,
       Tron completion is worth 0.0 to a "midrange" Eldrazi Ramp, and the "cascade" rows
       never match.

9. **Perspective of the valued card.**
   - *Where the input is missing:* `creature_threat_value` is called on opponent creatures with
     the caster's snapshot and has no perspective parameter (`ev_evaluator.py:651`, 2265;
     `ev_player.py:4490`, 4532).
   - *Where it matters:* in argmax picks the error is common to every candidate, so ranking
     mostly survives. The damage is in absolute comparisons: the removal premium (1811-1812),
     `target_value` (1850), the `_has_high_threat_target` floor (4945) and
     `PROACTIVE_REMOVAL_MIN_VALUE` 3.0.

10. **Current-state completeness.**
    - `snapshot_from_game` never sets `persistent_power`, so walkers and engines already on
      board are worth 0 in the current position.
    - It reads template keywords only, so evasion and lifelink granted by equipment are
      invisible.
    - It reads no phase, active player, summoning sickness or tapped state.

11. **Phase of the turn.**
    - MAIN1 is read only by blink and land animation.
    - `activation_candidates` fires instant-speed DRAW, DAMAGE, UNTAP, TUTOR and EXILE_GY at
      sorcery speed, with no hold alternative.
    - `_score_land` and `_consider_equip` read no phase. This is milder than first claimed: the
      tapped penalty needs castable spells, and equipment stays attached.

12. **A lock filter on every seam.**
    - `lock_that_counters` is applied only to the main-phase `spells` list (552-553).
    - Flash deploy, `cast_plotted` and optional recast reach the level-returning lock branch.

13. **Banked vs cast.**
    - Plot is scored as a resolve-now cast (≈588-594), with no delay or plot cost.

14. **Action-type consistency.**
    - The holdback gate differs by action type (§3.11).
    - `land_animation_candidates` gets no holdback at all.

15. **Chain fuel.**
    - Lands in hand are simulated as 0-cost storm fuel. `classify_card` has no `is_land`
      filter (`combo_chain.py:80-134`), and `_assess_storm_zone` passes `me.hand`
      (`combo_calc.py:228`).
    - `bottleneck_probability` treats every "combo" opponent as mid-chain on every turn
      (591-598).

16. **Response inputs.**
    - Planner responses carry `damage=0` and `keywords=set()` (`response.py:392-393`, 430-432).
    - The held-counter floor has no count of counters held and no BHI threat density.
    - `evaluate_stack_threat` ignores the stack item's targets.

17. **Return path and graveyard hate.**
    - `_score_suspend` pays graveyard equity with no `_deck_can_return_card` check (unlike
      cycling, 2821).
    - Neither suspend nor cycling reads opponent graveyard-hate beliefs.
    - `_gate_blockers_into_lethal` never asks whether the spell answers the lethal attacker.

18. **Inputs that should not be there at all.**
    - `creature_clock_impact` makes a live LLM call unless `MTG_LLM_DECISION_SCORER_OFFLINE=1`
      (`clock.py:458-465`).
    - The engine calls AI scoring with a fictional `BASELINE_SNAPSHOT`
      (`engine/card_effects.py:185-186`, 765-767, 811-817).
    - Card names are read in `ai/`: `ev_player.py:2587-2588`, `ev_evaluator.py:3123` and 3128,
      `board_eval.py:533-534`.

19. **Life cost in land valuation.**
    - `mana_planner.score_land` has no life input, so a shock land gets the untapped bonus with
      no life charge (381-382).
    - `should_stagger_shock` never asks whether the extra mana enables a spell.

---

## 6. Cliffs: every hard clamp or sentinel, and the smooth trade-off it replaces

| Cliff (file:line) | Value | Smooth trade-off it stands in for |
|---|---|---|
| `PATIENCE_GATE_REJECT_SENTINEL` (land-sac ≈908–973, blockers 987–1003, cascade ≈1008–1022, X-tutor 1021+) | `min(ev,−10)` | p(the held line completes a better outcome next turn) × Δvalue − p(dying or being disrupted first) × loss, using opp clock, life_phase and BHI |
| `X_BOARD_WIPE_WASTE_FLOOR` (≈1125–1204) | `min(ev,−20)` | expected kills now vs expected kills after the opponent's next deploys (BHI threat density), minus own collateral valued as utility (U6) |
| `BLINK_FIZZLE_FLOOR` (≈1687) | `min(ev,−50)` | **menu veto** when no controlled target exists; otherwise the realized value of the blink (live ETB target, protection need, EOT rider) (U3) |
| `NONCREATURE_COUNTER_DEAD_FLOOR` (≈1790) | `min(ev,−3)`, above the floor | value of holding the counter (held-response leaf) vs casting it for no effect |
| `STORM_HARD_HOLD` (combo_calc 525-530) | −990 | p(chain completes next turn) × win_swing(next) vs value now − p(disruption) |
| `compute_play_ev` self-kill (3199-3203) | −100 | **kept** as a real terminal, emitted once by the terminal leaf |
| `max(ev, −2.0)` for cmc ≤ 2 creatures (3317-3319) | literal floor | the ETB, removal or draw value itself, priced by the projection |
| Lock branch (3299) | a level | **menu veto** at every seam (lock filter lifted to the menu) |
| Free cast `max(ev,0)` + 1.5 (≈1263) | floor + flat | mana saved, priced through the converter |
| `REANIMATE_OVERRIDE_BONUS` (735-736) | +40 | the reanimated body's projection (already priced), with the target chosen by the same call |
| `PLAY_VALUE_FLOOR` (807) | −5 | "pass / hold" becomes a valued candidate: held interaction plus mana, via the converter |
| Activation dropped when ev + holdback ≤ 0 (505, activation_ev 767–909) | 0 | comparison against the valued pass/hold candidate |
| Evoke ±10 / −20 / bare −2.0 / +1.0 default / −4 per prior (board_eval 315-404; ev_player 1315-1323) | vetoes and literals | evoke is a cost mode: effect WP − pitched card WP − forgone hard-cast body WP; no target becomes a menu veto |
| Dash ±10, +2 / +1 / +0.5 / −0.3 (board_eval 416-445) | table | attack projection with the bounce cost and recast tax (U7) |
| `NO_CLOCK` 99 / 999 and every "99" sentinel | numeric sentinel | `Optional[None]`, "no clock", handled explicitly |
| `life_as_resource` −100 inside sums (clock 113; blocks; `_life_score` −500) | dead sentinel in a sum | terminal loss handled once by the terminal leaf; non-terminal life is priced by the converter |
| `LETHAL_BONUS` 100 / quick-lethal return (turn_planner 267, 292) | flat | the terminal leaf (win credited once), with p(the attack succeeds) under BHI tricks |
| Attack threshold reductions −2/−2/−2/−3 (≈3269-3288) | step reductions | combat projection including crackback, `damage_to_me` and opponent clock |
| Emergency block (life − incoming ≤ 5 …) (≈4001-4004) | threshold | survival-turn projection with lifegain and blockers |
| `HOLDBACK_PROBABILITY_FLOOR` 0.1; per-CMC 4.0–6.0 | floor and coefficient | calibrated BHI next-turn threat probability × the value of the held answer (converter) |
| `bottleneck_probability` {1, 0, NaN} (combo_calc 661) | tri-valued | a graded p(chain in flight) from opponent mana, storm count and BHI |
| `flashback_chain_viable == 0.0` (combo_calc 1195, 1283) | graded quantity used as bool | the graded coverage used as a probability of completion |
| Mid-chain ritual gate `opp_clock > 2` (combo_calc 1285) | literal | a survival probability |
| `choose_pw_ability` always-ult (247-248); suicide −59; decline sentinel | rule / literal | each slot valued by the same node (projection + loyalty kind) |
| `REANIMATE_TARGET_MIN_POWER` 5, `DESPERATION_LIFE_FLOOR` 6, `OPP_HAND_FULL_HOLDBACK_THRESHOLD` 4, `LAND_SACRIFICE_MIN_LANDS` 4, `CYCLING_GY_URGENCY_FLOOR` 3, `_eval_dash` turn ≤ 3, `_eval_kick` any-attacker | integer gates | inputs to the relevant leaf's projection, not gates |
| `position_value` finite-branch uncapped `clock_diff` | **not a target** | Leave as is. The saturating form was A/B-falsified twice (`docs/diagnostics/2026-08-30_clock_sign_inversion_fix_falsified.md`; the 2026-09-04 note at `ai/clock.py:826-833`). Do not re-run it |

---

## 7. Recommendation for the agentic architecture

### 7.1 The node and where it sits

Add one lifted auxiliary to the FSD in `docs/design/2026-09-16_agentic_decision_architecture.md`
§3.3, placed by the §3.6 rationalisation pattern that already lifts 1.2.2 *Enumerate Legal
Action Menu*. Its FSD number is **V** until the architecture doc assigns one.

**V — Valuate Candidate Play Under Current Goal — SUPERVISORY** (deterministic orchestration;
zero model calls in the sim, consistent with the architecture's §2).

- *Result:* one `PlayValuation` per candidate. It holds `total_wp`, a `terms` tuple of
  `ValueTerm`, and an optional `veto`. It is produced for every candidate that any seam
  compares.
- *Callers:* 1.3.5 *Rank Candidate Lines* (which already requires "both are compared in one
  unit"), 1.4.2 *Select Main-Phase Action*, 1.4.3 *Declare Line Attackers*, 1.4.4 *Select
  Engine-Offered Choice Member* (tutor delivery, sacrifice, evoke, dash, kick, fetch target,
  loyalty slot) and 1.4.5 *Choose Reactive Seam Decisions* (counter, combat trick, flash
  deploy).
- *Single-owner rule:* no seam compares two candidates by any number except `total_wp` from V.

### 7.2 The one currency

**WP (win points).** WP is expected game equity from the acting player's perspective, measured
as a delta from the current state.

- **+100 WP** means a certain win from here; **−100 WP** means a certain loss.
- `WIN_POSITION`, `LETHAL_THREAT` and `LETHAL_BONUS` collapse into this one constant.
- WP keeps the scale that `compute_play_ev`, `win_swing` and combo_value already use, so no
  existing terminal credit changes size.
- **Invariant:** the non-terminal terms of any candidate satisfy |Σ| < 100. Only the terminal
  leaf (V.5) may emit ±100.

### 7.3 The context schema V takes

`ValuationContext` is built once per decision by V.1 and passed read-only to every leaf.

```python
class ValuationContext(BaseModel):       # ai/schemas.py (Pattern A schema commit)
    perspective: PlayerIdx                # whose equity; fixes creature_threat_value's side
    goal: GoalType                        # current_goal + on_fallback_plan
    goal_roles: frozenset[Role]           # gameplan card_roles for held/board cards (U5)
    life_phase: LifePhase                 # DEVELOP/GRIND/PANIC/LETHAL — LETHAL rows required
    phase: TurnPhase                      # MAIN1/COMBAT/MAIN2/END, active_player, combat_done
    matchup: MatchupView                  # opp archetype, opp gameplan roles, opp graveyard reliance
    mana: ManaView                        # untapped sources BY COLOUR, pool, lands in hand,
                                          #   land drop available, castable_now per held card
    held: HeldCardsView                   # held interaction with castable_now (U4), counters count,
                                          #   pitch options, flash threats
    bhi: BeliefView                       # MANA-GATED p_counter/p_removal/p_trick/p_burn,
                                          #   next-turn threat density (not reactive posteriors)
    clocks: ClockPair                     # ONE blocker/evasion-aware pair; None = no clock
    assembly: AssemblyState               # best_line, sink reachability (U2), engine membership
    lock: LockView                        # lock_that_counters result, applied as a veto
```

Every field listed as ignored in §2.2 and §5 is now an explicit input. A leaf that needs a
field reads it. No leaf builds its own snapshot: no throwaway `EVPlayer`, no
`BASELINE_SNAPSHOT`, no private clock.

### 7.4 The function tree (Verb + Qualified Object; 6 children under V)

Children are ordered by result dependency.

- **V.1 Assemble Valuation Context — TOOL.**
  - Builds `ValuationContext` from `snapshot_from_game` + BHI + the goal engine + assembly.
  - It is the single owner of the clock pair (duplicate 4), the untap-watcher predicate
    (duplicate 16) and on-board `persistent_power` (blind spot 10).
  - FSD V.1 | Result: `ValuationContext` | SL ≤ 20 µs (reuses `self._assembly`) | P = 1.0 per
    decision.

- **V.2 Project Immediate Board Change — TOOL.** Leaves: the existing projectors, which now
  return `ValueTerm`s.
  - **V.2.1 Project Cast Resolution Outcome** — `_project_spell` + `compute_play_ev`'s delta.
    No lock branch; no `max(ev,−2)`; phase/goal weighting removed (moved to V.5/V.6 inputs).
  - **V.2.2 Project Combat Exchange Outcome** — one combat projector replacing `plan_attack`,
    `_predict_blocks`, `_score_block_lifespan_delta`, pump reach, `decide_combat_trick`,
    `_eval_dash` and the equip value. It includes `damage_to_me` and crackback, and discounts
    tricks by `bhi.p_trick`.
  - **V.2.3 Project Activated Ability Outcome** — `activation_candidates` and
    `land_animation_candidates`. Every branch returns a projection delta, with no GRANT_HASTE
    turns×20 or TUTOR_TO_HAND +1.
  - **V.2.4 Project Land Drop Outcome** — `_score_land`, `_choose_land_delivery` and
    `mana_planner.score_land` merged. Mana-now and mana-per-turn are priced through V.6, the
    life cost is charged, and the Tron assembly comes from V.1's assembly.
  - Target and mode choice happen **inside** the projector that values them. Examples: the
    blink target, the reanimation target, the tutor delivery target (**V.2.5 Value Delivered
    Tutor Target**), the X-wipe X value with own collateral, and the burn target.

- **V.3 Price Deferred Resource Change — TOOL.** This covers what a play changes beyond the
  immediate board: cards drawn or discarded, graveyard equity, loyalty, persistent engines, and
  mana committed.
  - **V.3.1 Price Mana Committed By Play** — the one mana-spent charge (duplicate 2) plus
    alternative costs: evoke pitch, sacrifice, life, phyrexian.
  - **V.3.2 Price Held Response Retention** — `_holdback_penalty` + `_blink_reservation_penalty`
    + the held-counter floor. It uses `held.castable_now` (U4) and the next-turn threat
    probability from `bhi`. It applies identically to every action type (§3.11).
  - **V.3.3 Price Graveyard Equity Change** — the cycling, suspend, self-fill and reanimation
    readiness credits, gated on a real return path.
  - **V.3.4 Price Planeswalker Loyalty Change** — `loyalty_pool_value` + ultimate line, counted
    once.

- **V.4 Discount Play By Opponent Response — TOOL.**
  - p(resolves), p(removed) and p(trick) from the mana-gated `bhi`, applied to the V.2/V.3
    terms by `estimate_opponent_response`.
  - The blend must preserve every snapshot field (§3.5). Activations and combat are
    discounted too (blind spot 7).

- **V.5 Weigh Terminal Game Outcomes — TOOL.**
  - The only leaf that may emit ±100 WP. It credits win_swing once per candidate (duplicate 6)
    using the assembly line's p × win_swing, emits the self-kill −100, and owns the
    partial-damage fire decision (`scarce_payoff_commit_ev`).
  - Menu **vetoes** are also decided here: lock, no legal target for a targeted mode, blink
    with no controlled target, evoke with no target. A vetoed candidate is removed with a
    reason. It is never clamped.

- **V.6 Convert Resource Quantity To Win Points — TOOL.**
  - The single converter. It is the only function allowed to multiply by
    `CLOCK_IMPACT_LIFE_SCALING` (and the alias `CREATURE_VALUE_OUTER_SCALE` is deleted).
  - Input: `ResourceQuantity(kind ∈ {card, mana_now, mana_per_turn, life, power_per_turn,
    damage, loyalty, graveyard_fuel}, amount, perspective)` + context. Output: WP.
  - Each kind has one derivation from `ai/clock.py` primitives (`card_clock_impact`,
    `mana_clock_impact`, `life_as_resource`, `combat_clock`) and the context's `life_phase`
    and `goal`. The goal/phase weighting now reaches every term (blind spot 6), not only
    `compute_play_ev`.
  - The engine-completion credit is expressed here as p(sink reachable) × the assembly line's
    win_swing, so it is ≤ 100 by construction (§3.1).

**Classification.**

- Every leaf is a **TOOL**: deterministic and name-free.
- **No AGENT or SUPERVISORY leaf** runs at sim time.
- The one place a model may contribute is compile time (architecture 1.1.5 *Author Seam Policy
  Sections*), as closed-Literal policy only. There are no float weights, per the
  architecture's compile vocabulary.
- `llm_decision_weights.json` and the in-loop `weight()` call are retired. Each of their eight
  contexts becomes a typed mechanic predicate read by the owning leaf: Tron and Amulet by
  oracle shape, cycling and cascade by tag, and the storm threshold by the assembly
  projection.

### 7.5 How the existing paths become leaves

| Existing path | Becomes | Change of currency |
|---|---|---|
| `compute_play_ev` / `_project_spell` / `estimate_spell_ev` | V.2.1 | C1 delta only; lock → V.5 veto; deferral → hold candidate |
| ~31–35 `_score_spell` overlays | V.2/V.3 terms, or deleted | each re-expressed as a `ResourceQuantity` through V.6; flat literals (C7) deleted |
| `_gate_*` / `_overlay_*` patience gates | V.5 veto or a V.3 hold trade-off | clamp → probability-weighted term |
| `card_combo_modifier`, `_estimate_combo_chain`, `find_all_chains` | V.2.1 + V.5 via `ai/assembly_state.py` | combo_value = win_swing (one owner); ritual penalty scaled; −990 deleted |
| `_score_land`, `mana_planner.score_land`, `_choose_land_delivery` | V.2.4 | C7/C11 → mana kinds via V.6 |
| `_score_cycling`, `_score_suspend`, `_self_fill_value`, reanimation boosts | V.3.3 | flat and LLM addends → `graveyard_fuel` / card kinds |
| `_holdback_penalty`, `_blink_reservation_penalty`, held-counter floor | V.3.2 | C8 → card and mana kinds via V.6 |
| `plan_attack`, `decide_attackers` fold, blocks, trick, dash, equip | V.2.2 | C6/C10 → a C1 combat delta |
| `activation_candidates`, `land_animation_candidates` | V.2.3 | branch credits → projection deltas |
| `choose_tutor_delivery`, `pick_activated_tutor_x`, the X-tutor gate | V.2.5 | one choice = one price |
| `choose_sacrifice_victim` | V.3.1 (sacrifice kind) | C2/C9 mix → WP |
| `_enumerate_burn_targets` and the target pickers | inside V.2.1 | C10/C7 → projection delta per target |
| `choose_pw_ability`, `ultimate_win_line_value`, `loyalty_pool_value` | V.2.1 per slot + V.3.4 | C11 → WP |
| `evaluate_stack_threat`, `TurnPlanner.evaluate_response`, legacy gates | V called at the response seam | C1/C17/C2 max → WP; tax, kind and SPELL checks → menu legality (1.2.2) |
| `_eval_evoke`, `_eval_kick`, `_eval_dash` (engine callbacks) | V at seam 1.4.4 | C12 → WP; **removed from engine `can_cast`** |
| `score_card_for_opponent_strip` | V.2.1 (discard projection) | C11 table → WP |
| `phase_weight_multiplier` / `goal_weight_multiplier` | inputs to V.6 | applied to every term, with LETHAL rows and all archetypes |

### 7.6 How magnitudes get normalized

1. **One converter.** `tools/check_single_owner.py` gains a pin: no multiply by
   `CLOCK_IMPACT_LIFE_SCALING` and no bare additive float in a valuation leaf outside V.6.
   This follows the existing ratchet pattern: the count may only fall.
2. **Typed terms.** Every leaf returns `ValueTerm(wp, term_id, kind, derivation)`. A test
   sums them and asserts the non-terminal |Σ| < 100 on a board corpus.
3. **Fix the 0.125 vs 2.5 card-price split at its owner.** One card is priced once, in V.6.
   *Which* rate is kept is a measured decision: this changes behaviour globally, so it must
   pass the architecture's 1.1.6.4 *Measure Artifact Regression* (`measure_lane`) same-seed
   gate. Do **not** couple it to the finite-branch `clock_diff` change, which is falsified
   twice (§6, last row).
4. **Sentinels become types.** "No clock" is `None`. The dead value comes only from V.5.
   Reject is `veto`, not a number.
5. **Migration by Pattern A** (CLAUDE.md). One schema commit adds `ValuationContext`,
   `ValueTerm`, V.6 and the ratchet pins. Then leaf migrations touch disjoint consumer files,
   each with its rule-phrased failing test first and a `measure_lane` guard. The Loop-break
   rule applies per outlier deck.

### 7.7 Which clamps become weighed trade-offs

- **Become menu vetoes** (decided by V.5):
  - the lock branch;
  - blink with no controlled target;
  - evoke or targeted modes with no legal target;
  - a dead soft counter the payer can pay.
- **Become probability-weighted trade-offs** (in V.3.2 / V.5):
  - PATIENCE −10;
  - X_WIPE −20;
  - `STORM_HARD_HOLD` −990;
  - `NONCREATURE_COUNTER_DEAD` −3;
  - `PLAY_VALUE_FLOOR` −5 (pass becomes a valued candidate);
  - the holdback probability floor;
  - `bottleneck_probability`;
  - `flashback_chain_viable`;
  - the attack-threshold reductions;
  - the emergency-block thresholds;
  - the integer gates in §6.
- **Are deleted** because the projection already prices them: `REANIMATE_OVERRIDE_BONUS` +40,
  `FREE_CAST_TEMPO_BONUS`, `max(ev,−2.0)`, the blink +3 flat and the dash/evoke tables.
- **Kept:** the self-kill −100 and the ±100 terminals, emitted by V.5 alone.

---

## 8. What to test per node

Each test name describes the mechanic and names no card. Names already fixed by the 5-panel
audit are reused so that its units land as leaf tests of V.

| Node | Rule-phrased test |
|---|---|
| V (root) | `test_every_seam_compares_candidates_only_by_one_valuation_currency` |
| V | `test_nonterminal_value_terms_sum_below_one_game_win` |
| V.1 Assemble Valuation Context | `test_valuation_context_uses_one_blocker_aware_clock_pair` |
| V.1 | `test_on_board_persistent_engines_carry_value_in_current_position` |
| V.1 | `test_granted_keywords_count_toward_evasion_and_lifelink_power` |
| V.1 | `test_valuation_makes_no_live_model_call` |
| V.2.1 Project Cast Resolution Outcome | `test_locked_cast_is_vetoed_not_valued_as_a_position_level` |
| V.2.1 | `test_opposing_creature_threat_valued_from_its_controllers_perspective` |
| V.2.1 | `test_removal_virtual_power_credited_once_per_target` |
| V.2.1 | `test_minus_loyalty_ability_declined_when_its_targeted_effect_has_no_legal_target` (U1) |
| V.2.1 | `test_walker_with_only_executable_minus_is_not_forced_to_tick_down` (U1) |
| V.2.1 | `test_ultimate_activated_only_when_it_outvalues_other_loyalty_slots` |
| V.2.1 | `test_flicker_floored_when_no_controlled_target_yields_blink_value` (U3) |
| V.2.1 | `test_x_wipe_picker_prices_own_utility_permanents_as_collateral` (U6) |
| V.2.2 Project Combat Exchange Outcome | `test_attack_valuation_charges_crackback_exposure` |
| V.2.2 | `test_combat_trick_discounted_by_opponent_instant_interaction_belief` |
| V.2.2 | `test_attack_and_keep_home_use_one_creature_worth_measure` |
| V.2.2 | `test_block_forecast_and_block_decision_share_one_pair_formula` |
| V.2.2 | `test_dash_mode_requires_profitable_attack_projection` (U7) |
| V.2.2 | `test_equipment_value_derived_from_combat_projection_not_raw_power` |
| V.2.3 Project Activated Ability Outcome | `test_activation_discounted_by_opponent_response_like_a_cast` |
| V.2.3 | `test_untap_credit_requires_a_castable_use_for_freed_mana` |
| V.2.3 | `test_land_animation_affordability_checks_colour_requirements` |
| V.2.4 Project Land Drop Outcome | `test_land_from_hand_and_fetched_land_share_one_land_valuation` |
| V.2.4 | `test_land_valuation_charges_life_paid_to_enter_untapped` |
| V.2.4 | `test_tron_piece_count_uses_subtype_search_oracle_not_names` |
| V.2.5 Value Delivered Tutor Target | `test_tutor_priced_target_equals_delivered_target` |
| V.2.5 | `test_unbounded_mana_engine_credit_requires_reachable_sink` (U2) |
| V.2.5 | `test_engine_completion_tier_requires_reachable_sink` |
| V.3.1 Price Mana Committed By Play | `test_mana_spent_is_charged_once_per_play` |
| V.3.1 | `test_evoke_values_pitched_card_and_forgone_body` |
| V.3.1 | `test_sacrifice_choice_and_charge_use_one_valuation` |
| V.3.2 Price Held Response Retention | `test_holdback_ignores_held_interaction_lacking_color_source` (U4) |
| V.3.2 | `test_holdback_applies_identically_across_action_types` |
| V.3.2 | `test_next_turn_threat_probability_not_read_from_reactive_posteriors` |
| V.3.3 Price Graveyard Equity Change | `test_graveyard_equity_credited_only_with_a_return_path` |
| V.3.3 | `test_reanimation_credited_once_and_target_chosen_by_same_valuation` |
| V.3.4 Price Planeswalker Loyalty Change | `test_ultimate_loyalty_counted_once_in_cast_value` |
| V.4 Discount Play By Opponent Response | `test_opponent_response_blend_preserves_every_snapshot_term` |
| V.4 | `test_response_discount_is_continuous_as_interaction_probability_rises_from_zero` |
| V.5 Weigh Terminal Game Outcomes | `test_win_swing_credited_once_per_candidate` |
| V.5 | `test_only_terminal_leaf_emits_full_game_value` |
| V.5 | `test_reject_is_a_menu_veto_not_a_liftable_clamp` |
| V.5 | `test_terminal_win_not_rescaled_by_goal_or_phase_weight` |
| V.5 | `test_partial_damage_fire_decision_has_one_owner` |
| V.6 Convert Resource Quantity To Win Points | `test_card_in_hand_priced_identically_inside_and_outside_position_value` |
| V.6 | `test_life_scaling_constant_applied_only_by_converter` (ratchet) |
| V.6 | `test_goal_and_life_phase_weight_every_value_term` |
| V.6 | `test_lethal_life_phase_has_weight_row_for_every_archetype` |
| V.6 | `test_combo_enabler_outranks_vanilla_body_under_deploy_engine_goal` (U5) |
| chain facts (assembly spine) | `test_lands_in_hand_are_not_chain_fuel` |
| chain facts | `test_opponent_chain_in_flight_requires_mana_and_storm_evidence` |
| engine boundary | `test_engine_legality_never_calls_ai_valuation` (ratchet: `_eval_evoke` in `can_cast`, `BASELINE_SNAPSHOT` scoring) |

---

## Appendix A — claims dropped or corrected in verification

**Refuted:**

- **"A locked cast can win the main phase."** The main phase filters locked spells first. The
  bug lives only in flash deploy, plot, recast and ISMCTS (§3.4).
- **"The worthiness ratio is incommensurate."** Both sides are C3 inside `position_value`.
- **"Deferral exposure competes with ×20 penalties."** No-signal casts are dropped before the
  argmax.
- **"Self-fill always beats hand denial."** Hand denial prices creatures in C2 and can win at
  low opponent life.
- **"`burn_low_life_threshold` does not exist."** The field exists with default 10.
  `LOW_LIFE_BURN_DEFAULT` is import-only.
- **"Creatureless burn is always multiplied by ×0.1."** Only while the opponent is above the
  threshold.
- **"`choose_tutor_delivery` credit is ungated."** It now goes through the sink-gated
  `engine_completion_credit`. Only the "completes" tier is sink-blind.
- **"Stack threat and counter floor divide by different lives, so they are incommensurate."**
  This is a legitimate zero-sum pairing. The base term is the real mismatch.
- **"`card_combo_modifier` is incommensurate with the projection."** It is the same C1 unit.
  Only the ritual penalty and −990 are mismatched.
- **"`_eval_dash` is unwired."** It is wired via `should_dash`.
- **"The tap-out penalty ignores the opponent being tapped out."** Holdback prices the
  opponent's next turn.
- **"`_score_spell` ignores `life_phase`."** It gets it via `compute_play_ev`, but not in the
  overlays.
- **"Flash deploy bypasses no_signal."** A flash creature always emits a signal.
- **"`bottleneck_probability` peeks at the hidden hand."** Its scan is decklist-wide anyway.
- **Rejected as duplicates:**
  - `_self_fill_value` vs readiness boost (same owner);
  - `_forfeited_attack_charge` (one helper);
  - empty-board vs self-wipe penalties (mutually exclusive);
  - two interaction probabilities (different events);
  - `creature_value` vs `creature_threat_value` (layered single owner);
  - "block valuation via position deltas" (that line is the combat trick);
  - `am_dead_next` vs `opp_clock_discrete` ≤ 1 (identical);
  - the `estimate_opponent_response` density fallback (unreachable);
  - "life curve owned four times" (two of the four are dead code);
  - defender vs attacker two-turn lethal (different rules);
  - loyalty safety vs loyalty target value (different quantities);
  - `plan_turn` re-pricing (dead);
  - the GSZ + combo double credit (not reachable in the current decks).

**Corrected:**

- Equip coincides with C1 at 20 life with no opposing clock.
- `permanent_threat` is C1, not C2.
- combo_value can exceed 200.
- The persistent-power loss in the response blend is the projection's credit, not a
  current-state term.
- The perspective error mostly preserves argmax rankings.
- The MAIN2 claims for equip and tapped lands are milder than first stated.
- The cost-reducer "four prices" includes a boolean gate.
- Land animation's value is the base term of `opportunity_cost`.
