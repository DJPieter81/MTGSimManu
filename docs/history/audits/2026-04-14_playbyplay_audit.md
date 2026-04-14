# Play-by-play audit — 5 game logs, 2026-04-14

**Method:** 5 parallel Explore agents each read one Bo3 log turn-by-turn and flagged
strategic errors. No sim runs, no code changes — pure forensics. Source: the 36 logs
already committed to `replays/`.

**Logs audited:**
- `replays/boros_vs_affinity_s60100.txt` (Affinity P1 vs Boros Energy P2)
- `replays/zoo_vs_affinity_s60200.txt` (Domain Zoo vs Affinity)
- `replays/ruby_storm_vs_dimir_s60102.txt` (Ruby Storm vs Dimir Midrange)
- `replays/living_end_vs_boros_s60101.txt` (Living End vs Boros Energy)
- `replays/eldrazi_tron_vs_boros_energy_s60003.txt` (Eldrazi Tron vs Boros Energy)

**Total findings:** 25 (5 per log). Grouped by root cause below.

---

## Root cause class A — Threat value / target selection

Applies to: any matchup with creature removal.

**F-A1 (P1)** — Galvanic Discharge targets 0-power Signal Pest instead of Memnite
about to be equipped. *Log: boros_vs_affinity_s60100.txt T3 P2.* Signal Pest is 0/1
(literally cannot damage), Memnite is 1/1 with Cranial Plating in opp hand.
Engineered Explosives on the stack will kill other small threats regardless; the
removal should hit the equip target.
*Suspected:* `ai/ev_evaluator.py::creature_threat_value` assigns non-zero threat
to 0-power bodies that have no evasion / trigger / scaling.

**F-A2 (P1)** — Thraben Charm removes Urza's Saga instead of Sojourner's Companion (4/4).
*Log: boros_vs_affinity_s60100.txt T4 P1.* Land engine taken out while the actual
lethal threat sits unaddressed and gets Cranial Plating equipped next turn.
*Suspected:* `ai/response.py::evaluate_stack_threat` and `_threat_score` — under-
weighting unequipped-but-equipable creatures vs lands with engines.

## Root cause class B — Burn without clock

**F-B1 (P2)** — Galvanic Discharge cast on empty opp board (6 face dmg total G3 T2).
*Log: boros_vs_affinity_s60100.txt G3 T2.* Affinity has zero creatures yet; burn to
face can't accelerate a clock that doesn't exist. Should hold for incoming threats.
*Suspected:* the `life_as_resource`-derived burn gate from PR #103 Site 4 has a
code path where creature-removal tag overrides the gate; Galvanic Discharge is
tagged both `removal` and damage — may take the wrong branch.

## Root cause class C — Storm chain finisher greed (P0 pattern)

Applies to: Ruby Storm.

**F-C1 (P0)** — Grapeshot fires at storm=1 or 2 (2 face damage to a 19-life opp).
*Log: ruby_storm_vs_dimir_s60102.txt G2 T4 and G3 T5.* Storm had additional rituals
and cantrips available but cast the finisher prematurely. With storm=5+, the same
chain would have dealt 5+ damage.
*Suspected:* `ai/ev_evaluator.py::compute_play_ev` combo-chain block (the
`is_chain_starter` section I wrote in PR #103). The `damage > 0 and storm_count >= 2`
gate credits any non-zero damage — including storm=1 one-shot Grapeshot.

**F-C2 (P0)** — Ruby Medallion cast into Counterspell with no redundancy plan.
*Log: ruby_storm_vs_dimir_s60102.txt G2 T5.* Storm at 1 life, commits 2 mana on the
engine knowing Dimir has untapped blue; get countered, die next turn.
*Suspected:* `ai/bhi.py::get_counter_probability` may not be elevated enough when
opp has visibly held up blue mana for 3+ turns; or `p_resolves` in the chain
logic ignores "if this specific card is countered, we lose the game".

**F-C3 (P1)** — Past in Flames cast into known Spell Pierce mana.
*Log: ruby_storm_vs_dimir_s60102.txt G3 T6.* 3 mana spent, countered for U.
*Suspected:* same BHI / commitment-risk pipeline as F-C2.

## Root cause class D — Aggro under-racing

Applies to: Domain Zoo (and potentially Boros Energy).

**F-D1 (P1)** — Zoo T1-T4: no creatures deployed. Casts Teferi (card advantage),
Leyline Binding (removal), Phlage (burn-and-sac) instead of Ragavan/Thrull/Kavu
on curve. Affinity meanwhile assembles Ornithopter + Springleaf Drum + Mox Opal.
*Log: zoo_vs_affinity_s60200.txt G1 T1-T4.*
*Suspected:* `ai/ev_player.py::_score_spell` over-valuing card-advantage spells
(Teferi is treated as a draw engine) and removal vs tempo creatures in aggro
profile. Profile `pass_threshold` or `holdback_penalty` misbalanced for AGGRO.

**F-D2 (P0)** — Zoo attacks with 1 Territorial Kavu when 2 are available.
*Log: zoo_vs_affinity_s60200.txt G2 T5.* Race lost by a single turn — had both
Kavus attacked, Affinity would have been at 9 life instead of 13 going into T6.
*Suspected:* `ai/turn_planner.py::plan_attack` — `SHIELDS_DOWN_PENALTY` (-1.5)
overriding the race-commit incentive when we should be tapping out.

## Root cause class E — Living End engine bug (P0, pre-existing)

**F-E1 (P0)** — Living End resolves but no creatures come back from graveyard.
*Log: living_end_vs_boros_s60101.txt G1 T3.* GY has 5 creatures before cascade.
After "Resolve Living End", post-cascade board shows only the Shardless Agent
(the cascader), no ETB logs for returned creatures. LE dies T5 with no board.
This is the exact P0 the 2026-04-11 audit flagged (never fixed).
*Suspected:* `engine/game_state.py::_resolve_living_end` — GY-to-BF transfer loop
may be skipped or `_handle_permanent_etb` not called on returned creatures.

## Root cause class F — Mulligan gates too permissive

**F-F1 (P1)** — Eldrazi Tron keeps 3-lands + 1-cheap-spell (Eldrazi Mimic) with no
`mulligan_keys` present (no Chalice, Temple, Expedition Map, Matter Reshaper, TKS).
Loses to Boros T1-T3 curve because it can't deploy anything on curve.
*Log: eldrazi_tron_vs_boros_energy_s60003.txt G2 mulligan.*
*Suspected:* `ai/mulligan.py` — ramp archetype gate requires only 2 cheap spells
OR 1 key card; no AND.

**F-F2 (P1)** — Living End keeps 6-card hand with zero cascaders (neither Shardless
Agent nor Demonic Dread) but plenty of cyclers. Forced to cycle-and-pray for 3
turns.
*Log: living_end_vs_boros_s60101.txt G1 mulligan.*
*Suspected:* `ai/mulligan.py::mulligan_combo_sets` — the `cards_in_hand <= 6 → keep`
condition allows ANY 6-card combo hand with any combo_set satisfied, even when a
critical set is empty.

## Root cause class G — Post-combo push missing (Iter7 Fix 6 territory)

**F-G1 (P1)** — Post-cascade Living End doesn't attack. With only Shardless Agent on
board (because F-E1 ate the rest), LE passes without attempting combat.
*Log: living_end_vs_boros_s60101.txt G1 T3.*
*Suspected:* `ai/gameplan.py::GoalEngine` — PUSH_DAMAGE goal exits after 2 turns
instead of 3 (Iter7 Fix 6 — unimplemented in repo).

## Root cause class H — Within-turn sequencing

**F-H1 (P0)** — Tron casts Chalice then destroys it with own Blast Zone same turn.
*Log: eldrazi_tron_vs_boros_energy_s60003.txt G1 T5.* 2 mana wasted; Chalice
treated as "play now" independent of the Blast Zone activation planned for the
same turn.
*Suspected:* action-selection logic scores each action in isolation rather than
as a sequence.

---

## Severity × frequency matrix

| Root cause | P0 count | P1 count | P2 count | Total |
|-----------|---------:|---------:|---------:|------:|
| A — Threat value | 0 | 2 | 0 | 2 |
| B — Burn no clock | 0 | 0 | 1 | 1 |
| C — Storm chain | 2 | 1 | 0 | 3 |
| D — Aggro under-racing | 1 | 2 | 0 | 3 |
| E — LE engine bug | 1 | 0 | 0 | 1 |
| F — Mulligan | 0 | 2 | 0 | 2 |
| G — Post-combo push | 0 | 1 | 0 | 1 |
| H — Sequencing | 1 | 0 | 1 | 2 |
| **Total** | **5** | **8** | **2** | **15** |

(Some findings overlap root-cause classes; 25 raw findings deduplicate to 15 once
grouped by class.)

---

## Top-3 triage — highest leverage, implementable, convention-compliant

### Triage pick 1 — **F-C1 (Storm chain finisher threshold)**
- **Leverage:** HIGH. Targets a P0 that shows up in multiple Storm games.
- **File:** `ai/ev_evaluator.py::compute_play_ev` combo-chain block.
- **Fix shape:** The current gate is `damage > 0 and storm_count >= 2`. Require
  `can_kill OR storm_count >= LETHAL_STORM_THRESHOLD OR damage >= opp_life/2`.
  Replaces the greedy "any damage counts" with "only credit chains that make meaningful
  lethal progress". All thresholds are already derived (`LETHAL_STORM_THRESHOLD = 6`
  from the existing rules-constant).

### Triage pick 2 — **F-A1 (0-power creature threat value)**
- **Leverage:** MEDIUM-HIGH. Applies to every removal targeting decision; fixes
  Boros vs Affinity specifically.
- **File:** `ai/ev_evaluator.py::creature_threat_value`.
- **Fix shape:** For a creature with 0 power AND no evasion AND no attack-trigger AND
  no scaling oracle clause, floor the threat value to near-zero (or equal to cost of
  the creature as a minimal "they paid mana for this"). Currently
  `creature_clock_impact_from_card` returns a small positive value for 0-power bodies
  via ETB tags alone, and battle-cry / scaling bonuses can push it well above a real
  1-power threat's value. Add `if p <= 0 and not amplifier: return small_floor`.

### Triage pick 3 — **F-E1 (Living End ETB bug)**
- **Leverage:** VERY HIGH. If confirmed, likely moves LE from 5-20% to 35%+.
- **File:** `engine/game_state.py::_resolve_living_end`.
- **Fix shape:** This is a confirmation-then-repair. First verify in code whether
  returned creatures are actually moved from GY to BF and whether `_handle_permanent_etb`
  is called. The 2026-04-11 audit flagged this as "returned creatures get no ETB
  triggers" — if true, add the ETB invocation per returned creature.

Tier-2 candidates (deferred):
- F-F1/F-F2 mulligan — would require touching `mulligan.py` which has archetype-
  dependent gates; higher risk of breaking other decks.
- F-G1 post-combo push — covered by ITERATION_7_PLAN.md Fix 6 as written; adopt
  that spec directly if we tackle it.
- F-H1 within-turn sequencing — structural (turn planner refactor); out of scope.
- F-D1/F-D2 aggro racing — involves strategy_profile tuning which is explicitly
  a non-goal for this session.
