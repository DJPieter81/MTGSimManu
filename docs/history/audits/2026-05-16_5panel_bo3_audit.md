---
title: Bo3 5-panel audit synthesis — 2026-05-16
status: active
priority: primary
session: 2026-05-16
depends_on:
  - docs/history/audits/2026-05-16_rules_audit.md
  - docs/history/audits/2026-05-16_control_pro_audit.md
  - docs/history/audits/2026-05-16_combo_pro_audit.md
  - docs/history/audits/2026-05-16_midrange_pro_audit.md
  - docs/history/audits/2026-05-16_aggro_pro_audit.md
  - docs/history/audits/2026-04-26_storm_pro_audit.md
  - CLAUDE.md
tags: [audit, pro-review, synthesis, multi-panel]
summary: >
  Five parallel panels (rules-engine, pro Control, pro Combo, pro Midrange,
  pro Aggro) audited 4 Bo3 replays. Three mechanisms reached 3+/5 panel
  consensus — chain projection missing damage-IN, counter triage on
  chain-fuel vs chain-payoff, and the pass-threshold trap. One outcome-
  decisive engine bug (impulse-draw routed through draw_cards firing
  Bowmasters triggers) is the single highest-impact fix in the corpus.
  Every recommendation passes the 6-point abstraction contract; 23
  card/deck-specific patches refused across the five panels.
---

# Bo3 5-panel audit — synthesis

**Corpus.** 4 Bo3 replays in `replays/` (seeds 60100–60103):

| Seed  | Match                                | Result            |
|-------|--------------------------------------|-------------------|
| 60100 | Azorius Control vs Ruby Storm        | Storm wins 2-0    |
| 60101 | Ruby Storm vs Dimir Midrange         | Dimir wins 2-1    |
| 60102 | Dimir Midrange vs Boros Energy       | Boros wins 2-0    |
| 60103 | Boros Energy vs Azorius Control      | Boros wins 2-0    |

**Panel grades.**

| Panel               | Grade  | Headline issue                                                   |
|---------------------|--------|------------------------------------------------------------------|
| Rules engine        | 6/10   | Impulse-draw routed through `draw_cards()` → Bowmasters fires    |
| Pro Control         | 4/10   | "Pass below threshold" runs the game out; counter on chain-fuel  |
| Pro Combo           | 6/10   | Chain projection sees damage OUT but not damage IN               |
| Pro Midrange        | 5/10   | Thoughtseize EV ≈ -0.06 in every state — `_project_spell` blind  |
| Pro Aggro           | 5/10   | Race math missing damage-on-draw tax; defender doesn't chump     |

**Strongest convergent signal across the panel:** Boros (aggro) plays competently; midrange and control are passive; combo executes chains correctly but mis-prices entry, exit, and self-damage during a chain.

---

## Cross-panel agreement matrix

Each row is a **generic mechanism** — symptoms differ by panel, the *cause* converges. Agreement is measured on the mechanism, not the wording.

| Mechanism                                                          | Rules | Control | Combo | Midrange | Aggro | Consensus | Severity |
|--------------------------------------------------------------------|:-----:|:-------:|:-----:|:--------:|:-----:|:---------:|:--------:|
| **M1.** Chain projection models damage OUT but not damage IN       |   ✓   |         |   ✓   |          |   ✓   | **3/5**   | **P0**   |
| **M2.** Counter triage targets chain-fuel, not chain-payoff        |       |   ✓     |   ✓   |    ✓     |       | **3/5**   | **P0**   |
| **M3.** `pass_threshold` trap — no proactive-tap-out floor          |       |   ✓     |   ✓   |          |       | **2/5**   | **P0**   |
| **M4.** `goal=close_game` is inert; no defensive gear-shift        |       |   ✓     |       |    ✓     |   ✓   | **3/5**   | **P0**   |
| **M5.** Planeswalker / conditional-X / refill EV undervalued       |       |   ✓     |       |          |       | **1/5**   | **P0**   |
| **M6.** `_project_spell` blind to forced-discard hand-size delta    |       |         |       |    ✓     |       | **1/5**   | **P0**   |
| **M7.** Tap-out windows not surfaced as positive fire signal       |       |         |   ✓   |    ✓     |       | **2/5**   | **P1**   |
| **M8.** Channel / EOT activated abilities not enumerated reactively|       |   ✓     |       |          |       | **1/5**   | **P1**   |
| **M9.** Delve / alt-cost / pitch cost reduction not in projection  |       |         |       |    ✓     |       | **1/5**   | **P1**   |
| **M10.** Burn target omits planeswalkers from "killable" set       |       |         |       |          |   ✓   | **1/5**   | **P1**   |
| **M11.** Discard targeting ignores "attacks-next-turn" weight      |       |         |       |          |   ✓   | **1/5**   | **P1**   |
| **M12.** Defender doesn't chump-block when next-turn forecasts lethal|     |         |       |          |   ✓   | **1/5**   | **P0**   |
| **M13.** Mid-range / control doesn't role-flip vs combo            |       |   ✓     |       |          |       | **1/5**   | **P1**   |
| **M14.** PiF viability doesn't include post-chain GY fuel          |       |         |   ✓   |          |       | **1/5**   | **P1**   |
| **M15.** Mulligan keeps land-heavy aggro/Storm hands               |       |         |   ✓   |          |       | **1/5**   | **P2**   |
| **R1.** Impulse-draw routed through `draw_cards()`                 |   ✓   |         |   ✓   |          |   ✓   | **3/5**   | **P0**   |
| **R2.** Galvanic Discharge per-card handler stale + illegal target |   ✓   |         |       |          |       | **1/5**   | **P1**   |
| **R3.** Land-ETB surveil triggers never fire                       |   ✓   |         |       |          |       | **1/5**   | **P1**   |
| **R4.** Teferi-TR static "opponents at sorcery speed" not enforced |   ✓   |         |       |          |       | **1/5**   | **P1**   |
| **R5.** `parse_x_cost` false-positives on Oracle-body X            |   ✓   |         |       |          |       | **1/5**   | **P2**   |
| **R6.** Ral coin-flip damages player.life rather than Ral          |       |         |   ✓*  |          |   ✓   | **1.5/5** | **P1**   |

\* Combo panel framed Ral coin-flip as *a projection bug* (F8 — chain projection doesn't subtract expected self-damage); Aggro panel framed it as *an engine bug* (Pattern F — `player.life -= 1` is wrong, Ral should take damage). Rules panel didn't flag it (presumed working). Two distinct fixes; see disagreements below.

---

## Top consensus findings (≥3 panels)

### M1 + R1 — Damage-IN during own combo turn (P0, outcome-decisive)

The single most-impactful finding in the corpus. Three panels — Rules, Combo, Aggro — converge on the same root, framed differently:

- **Rules:** `engine/oracle_resolver.py:431-463` documents the impulse-draw path as a deliberate approximation through `game.draw_cards()`. The fan-out fires "whenever an opponent draws" triggers (Bowmasters, Sheoldred, Underworld Dreams) in violation of CR 121.1 — impulse-draw is *not* draw.
- **Combo:** `ai/ev_evaluator.py:_estimate_combo_chain` projects damage OUT (Grapeshot to opponent) but never subtracts per-spell or per-draw damage IN (Bowmasters on opp board, Ral coin-flip on our board) from `snap.my_life`.
- **Aggro:** Race-math layer is missing `opp_static_damage_per_card_event(snap)` — the AI casts a chain at 10 life that draws 5 cards into 2 Bowmasters and self-kills 10 → 0.

**Smoking gun:** `audit_storm_vs_dimir_s60101.txt` G1 T4 — Storm at 10 life, 2 Bowmasters on board, casts Ral → Desperate Ritual → Reckless Impulse → Pyretic Ritual → Glimpse the Impossible → `P1 loses: life total 0`. Storm killed itself on its own combo turn.

**Two-layer fix required (engine + AI), in this order:**

1. **Engine — split impulse-draw out of `draw_cards()`** (Rules R1 / Combo F1 root). New `resolve_impulse_draw(...)` exiles top-N into a tracked zone, marks `playable_until=eot+1`, and is *not* a draw. Bowmasters/Sheoldred/Dreams stop firing in violation of CR 121.1c.

2. **AI — chain self-damage projection** (Combo F1 + Aggro Pattern A). `_estimate_combo_chain` walks each spell, sums per-spell and per-draw self-damage (oracle predicate-driven, no card names), subtracts from `snap.my_life`. If `my_life - tax ≤ 0`, the chain returns `can_kill=False` regardless of the damage-out math.

The AI-side fix matters even after the engine fix because (a) Sheoldred/Dreams *should* still fire on real draws (Wrenn's Resolve is genuinely a draw), (b) Ral coin-flip *should* still tax the chain even after R6 is resolved (the damage source moves to Ral, but Ral dies and the chain still loses tempo).

**Class size:** ~30 cards for the engine fix (every "exile top N, may play" predicate); ~80 cards for the AI fix (every "whenever you cast / whenever you draw" opp permanent).

**Failing tests:**
- `tests/test_impulse_draw_does_not_trigger_opponent_draw_clauses.py::test_glimpse_the_impossible_with_two_bowmasters_deals_zero_self_damage`
- `tests/test_combo_chain_models_opp_per_draw_tax.py`

**Lift-check:** Storm, Izzet Prowess, Boros Energy (Bauble/Stage), 4c Omnath (Bauble triggers), Affinity (Thoughtcast). Engine fix also cleans up future cards in the impulse-draw family.

### M2 — Counter triage on chain-fuel vs chain-payoff (P0)

Three panels — Control, Combo, Midrange — flag the same mechanism. Azorius vs Storm G2T4 is the canonical example: Storm casts 2× rituals → Wrenn's Resolve → Azorius counters Wrenn's Resolve (a 4-of cantrip), Storm then chains through Past in Flames + Wish → Grapeshot for lethal. The counter was burned on chain-fuel; Past in Flames was the bottleneck and resolved uncountered.

**Mechanism (generic):** When opponent's archetype is combo AND opp has cast ≥1 chain-fuel spell this turn (oracle predicate: `'storm'` keyword OR `tag:cost_reducer` on board OR ritual mana floated), the threat score for a spell-on-stack must include `P(this spell is the chain-bottleneck enabling payoff this turn)`. That probability is derivable from `ai/combo_calc.py` (already computes reachability) and BHI (opponent's hand inferred for payoff cards). No new constants required.

**Failing test:** `tests/test_held_counter_targets_chain_payoff_not_chain_fuel.py` — opponent (combo archetype) casts a sequence of `tag:ritual` / `tag:cantrip` spells, then casts the `tag:storm_payoff` with no replacement in hand. The held Counterspell must fire on the payoff, not the chain-fuel.

**Subsystem:** `ai/response.py:42` `decide_response` + `:562` `evaluate_stack_threat` + `:375` `_held_counter_floor_ev`.

**Lift-check:** Every counterspell deck × every combo deck. Azorius, Dimir, Jeskai Blink, 4/5c Control vs Storm, Living End, Cascade, Goryo. Estimated 30+ of the 16×16 matchups.

### M4 — `goal=close_game` is inert; no defensive gear-shift (P0)

Three panels — Control, Midrange, Aggro — flag the same root: the `goal_engine` flips its label (`grind_value → close_game`) but the scoring layer doesn't multiply by per-goal weights. The same `_score_spell` projection runs in `close_game` as in `grind_value`.

Two diagnostic decisions cited by midrange:
- **Dimir at 9 life, 4 attackers untapped:** taps out for Psychic Frog into open Thraben Charm mana → Frog dies → 6 damage → Dimir at 4 → loses next turn.
- **Azorius at 3 life vs aggro board:** casts 5-CMC Teferi Hero (no body, no immediate impact) instead of removal or Solitude — dies next turn.

Aggro frames it as "the defender doesn't chump-block when life trajectory forecasts lethal" — same mechanism, different surface. Control frames it as "midrange decks don't role-flip vs combo" (a sibling pattern: gameplan label exists, scoring doesn't honour it).

**Mechanism (generic):** `ai/clock.py` already exposes `urgency_factor`, `opp_clock_discrete`, and `life_as_resource`. Add `clock.is_panic_zone(snap) → bool` keyed on `my_life ≤ max(3, opp_one_turn_damage)`. In `compute_play_ev`, when `is_panic_zone()` returns True:
- Multiply non-defensive EV by a `panic_dampener` (derived from `urgency_factor`, not a magic literal)
- Add a bonus equal to `lifegain_value` for spells with `lifelink` or "gain X life" oracle
- Block-decision side: defender must chump when `life_as_resource(life_after_no_block, opp_power_left_after_block) < 1.0` AND a chumpable token is available

`ai/strategy_profile.py` should also expose per-(archetype, goal) signal weights so `close_game` literally re-weights finishers up and cantrips down.

**Failing tests:**
- `tests/test_panic_gear_at_lethal_minus_one.py`
- `tests/test_defender_chumps_when_no_block_means_lethal_next_turn.py`
- `tests/test_close_game_upweights_finisher_over_cantrip.py`

**Lift-check:** Dimir, Azorius, 4c Omnath, Goryo's, Living End — every non-combo deck. Aggro-side lift: every deck that ever blocks with tokens.

---

## P0 findings, ranked by consensus + impact

1. **M1 + R1 — Impulse-draw / chain self-damage** (3/5 panels, outcome-decisive). Two-layer engine + AI fix.
2. **M2 — Chain-aware counter triage** (3/5). Single subsystem (`ai/response.py`), uses existing combo_calc + BHI primitives.
3. **M4 — Panic-zone gear-shift + role-flip** (3/5). New `clock.is_panic_zone` primitive feeds `compute_play_ev` and block scoring.
4. **M5 — Planeswalker / conditional-X / refill EV** (1/5 but isolated outcome-decisive cases). Azorius G2 enumerated 1 alternative for 4 consecutive turns; Teferi scored -5.7. Fix in `ai/ev_evaluator.py` `evaluate_board` (loyalty pool + immediate-ability projection).
5. **M6 — `_project_spell` blind to forced-discard** (1/5 but highest class-size: ~150 cards). Thoughtseize EV ≈ -0.06 universally — `_project_spell` doesn't decrement `opp_hand_size` for forced-discard or subtract life cost. One-branch fix.
6. **M3 — `pass_threshold` proactive-tap-out floor** (2/5). Already-existing `_holdback_penalty` at `ai/ev_player.py:1379` needs its negation: award tapping out when opp's clock won't matter.
7. **M12 — Defender panic chump** (1/5 but generic to every blocker). Companion to M4.

## P1 findings, ranked by consensus + impact

8. **M7 — Tap-out window as positive BHI signal** (2/5). Add `BHI.opp_tap_out_window = opp_untapped_mana < min(known_counter_costs)`; consume as chain-EV multiplier.
9. **R2 — Delete Galvanic Discharge per-card handler** (1/5). Pattern-level fix; same anti-pattern lurks across ~90 entries in `engine/card_effects.py`.
10. **R3 — Land-ETB triggers** (1/5). Surveil-dual cycle + Triomes silently no-op. Class ~30 lands.
11. **R4 — Teferi-TR static enforcement** (1/5). Latent in this corpus; lifts Grand Abolisher, Drannith Magistrate too.
12. **R6 — Ral coin-flip life-loss** (1.5/5, disagreement). See Disagreements section below.
13. **M9 — Delve / alt-cost in projection** (1/5). Murktide scored EV = -30.98 because `_project_spell` charges full 7 mana. Same site as the Medallion cost-reducer logic (per the 2026-04-26 audit's F5.1 fix).
14. **M10 — Burn targets planeswalkers** (1/5). Pure target-enumeration fix.
15. **M11 — Discard targets "attacks-next-turn"** (1/5). `bhi.predicted_turn_of_cast(card)` feeds the discard advisor.
16. **M13 — Midrange role-flip vs combo** (1/5). Companion to M4 — gameplan goal selection consults `opponent.archetype`.
17. **M14 — PiF viability includes post-chain GY** (1/5). Storm-specific instance of "look-ahead in chain assessment".

## P2 findings

18. **R5 — `parse_x_cost` excludes Oracle-body X** (1/5). Consult the Star Charts silently no-ops. ~8 cards.
19. **M8 — Channel / EOT abilities in response window** (1/5). Otawara held all game vs Storm Medallion chain.
20. **M15 — Aggro/Storm 4-land mulligans** (1/5, carryover from 2026-04-26 F1.1). Note Aggro panel said Boros mulligans were sensible in this corpus; only Combo panel flagged this. See Disagreements.

---

## Disagreements between panels

These need attention before any fix lands. Resolution criteria are noted.

### D1 — Ral coin-flip: engine bug or projection bug?

- **Aggro Pattern F** says `engine/oracle_resolver.py:638` does `player.life -= 1` on Ral's lose-flip — should damage Ral instead.
- **Combo F8** says the projection layer doesn't subtract expected per-spell coin-flip self-damage from chain projection. Doesn't claim the engine is wrong; treats it as a projection-layer gap.
- **Rules panel** doesn't flag Ral at all (lists Ral coin-flip among the things engine handles correctly).

**Resolution:** Read `engine/oracle_resolver.py:638` and the actual Oracle text. If Ral's current Oracle says "Ral takes 1 damage" (or equivalent) then Aggro is right and this is an engine bug. If Oracle says "you lose 1 life" then Rules is right and Combo's projection-layer fix stands alone. Either way the projection fix is needed for the AI-side decision math.

### D2 — Mulligan threshold on land-heavy aggro hands

- **Combo F7** says Boros's G2 P2 hand (4 lands, 1 2-drop, 1 3-drop) is a mulligan; AI kept it.
- **Aggro panel** says "Boros mulligan threshold is sensible. All four Boros opening hands kept were 2-3 land."

**Resolution:** Re-read `audit_boros_vs_azorius_s60103.txt` opening-hand section for G2 P2. If 4 lands kept, Combo is right and this is the 2026-04-26 F1.1 carryover. If 3 lands, Aggro is right and the finding is invalid for this corpus.

### D3 — Counter target selection: working or broken?

- **Control Decisions 2, 5** says counter targeting burns on chain-fuel.
- **Aggro Match 1** notes "Storm took 1 Counterspell (chose Wrenn's Resolve, not the highest-EV trigger spell) — Azorius's counter target selection is fine."

**Resolution:** These look contradictory but aren't. Aggro's lens is "did the counter resolve cleanly?" — yes, mechanically. Control's lens is "did the counter hit the correct target?" — no, strategically. Both are correct in their lens. Use Control's framing for the fix; the M2 finding stands.

---

## Patches we refused to ship — why these are symptoms, not causes

Aggregated across the five panels. Every refusal preserves the structural-fix discipline from CLAUDE.md. The deck-specific symptom is kept for diagnostic value; the patch is discarded.

| # | Refused patch                                                            | Source panel | Real fix |
|---|--------------------------------------------------------------------------|--------------|----------|
| 1 | "Hardcode Reckless Impulse / Wrenn's Resolve / Glimpse to skip Bowmasters triggers" | Rules        | R1 engine routing |
| 2 | "Hardcode Galvanic Discharge target to be creature"                       | Rules        | R2 delete handler |
| 3 | "Increase Storm's max life or special-case Bowmasters damage"             | Rules        | M1 projection fix |
| 4 | "Add `surveil_on_etb=True` field to land templates manually"              | Rules        | R3 oracle-ETB resolver |
| 5 | "Per-card handler for Consult the Star Charts"                            | Rules        | R5 `parse_x_cost` tighten |
| 6 | "When facing Storm, Azorius auto-casts Teferi T3 if available"            | Control      | M5 planeswalker EV |
| 7 | "When Wrenn's Resolve is on the stack, do not counter"                    | Control      | M2 chain-aware counter |
| 8 | "When Ruby Medallion is on battlefield, prefer to channel Otawara"        | Control      | M8 channel enumeration |
| 9 | "Storm should not chain past a Counterspell with one card in hand"        | Control      | Defer to 2026-04-26 F2.1 |
| 10 | "Dimir should always have UU up vs combo"                                 | Control      | M13 role-flip |
| 11 | "Detect Bowmasters by name and discount Reckless Impulse EV"              | Combo        | M1 projection fix |
| 12 | "Hardcode Storm to always cast Reckless Impulse on T4 if at 14+ life"     | Combo        | M3 + cantrip override |
| 13 | "Tighten Storm's pass_threshold from -3.0 to -1.0"                        | Combo        | M3 conditional override |
| 14 | "Add Wish + PiF + Grapeshot to a per-deck combo priority list"            | Combo        | M14 PiF viability |
| 15 | "Add `archetype == 'combo' and storm == 0 → cast cantrip with +5 EV`"     | Combo        | M3 generic dig EV |
| 16 | "If `card.name == 'Thoughtseize'` then EV += 5"                           | Midrange     | M6 `_project_spell` fix |
| 17 | "If Dimir at life ≤ 5 then don't cast Psychic Frog"                       | Midrange     | M4 panic-zone gate |
| 18 | "For combo opp matchups, hold Counterspell for the 3rd spell in chain"    | Midrange     | M2 chain-aware counter |
| 19 | "Multiply Thoughtseize EV by 5 in gameplan JSON"                          | Midrange     | M6 projection fix |
| 20 | "At life ≤ 3, never cast a planeswalker"                                  | Midrange     | M4 panic-zone (parametric) |
| 21 | "Special-case Bowmasters in Storm's chain EV"                             | Aggro        | M1 generic tax |
| 22 | "Boost Dimir chump-block bias by +X if `deck.archetype == 'midrange'`"    | Aggro        | M12 generic chump rule |
| 23 | "Lower Storm's `pass_threshold` globally"                                 | Aggro        | M1 chain bound by life |

**Total refused: 23.** All would have increased `tools/abstraction_baseline.json` or `tools/magic_numbers_baseline.json` if implemented.

---

## Contract-compliance audit of every recommended fix

Every fix from every panel re-checked against the 6-point contract from `CLAUDE.md`. Findings that fail any point are demoted to "Unresolved" instead of being shipped as a patch.

| Fix | No card-name? | No deck-gate? | No bare numeric? | Rule-phrased test? | Lift-check? | Engine/AI separated? | Verdict |
|-----|:-:|:-:|:-:|:-:|:-:|:-:|:------|
| **M1** engine routing            | ✓ | ✓ | ✓ | ✓ | ≥4 decks | ✓ engine-only | **ship** |
| **M1** AI projection             | ✓ | ✓ | ✓ | ✓ | ≥4 decks | ✓ ai-only    | **ship** |
| **M2** chain-aware counter       | ✓ | ✓ | ✓ | ✓ | ≥6 decks | ✓ ai-only    | **ship** |
| **M3** proactive-tap-out floor   | ✓ | ✓ | derived from `_holdback_penalty` | ✓ | ≥4 decks | ✓ ai-only | **ship** |
| **M4** panic-zone gear-shift     | ✓ | ✓ | derived from `urgency_factor` | ✓ | ≥5 decks | ✓ ai-only | **ship** |
| **M5** planeswalker EV credit    | ✓ | ✓ | derived from `clock` loyalty model | ✓ | ≥4 decks | ✓ ai-only | **ship** |
| **M6** `_project_spell` discard  | ✓ | ✓ | ✓ | ✓ | ≥3 decks | ✓ ai-only | **ship** |
| **M7** BHI tap-out window        | ✓ | ✓ | ✓ | ✓ | ≥6 matchups | ✓ ai-only | **ship** |
| **M8** channel enumeration       | ✓ | ✓ | ✓ | ✓ | ≥4 decks | ✓ ai-only | **ship** |
| **M9** delve / alt-cost          | ✓ | ✓ | ✓ | ✓ | ≥3 decks | ✓ ai-only | **ship** |
| **M10** burn-target planeswalker | ✓ | ✓ | ✓ | ✓ | ≥4 decks | ✓ ai-only | **ship** |
| **M11** discard attack-imminence | ✓ | ✓ | ✓ | ✓ | ≥4 decks | ✓ ai-only | **ship** |
| **M12** defender panic chump     | ✓ | ✓ | derived from `life_as_resource` | ✓ | universal | ✓ ai-only | **ship** |
| **M13** midrange role-flip       | ✓ | ✓ | ✓ | ✓ | ≥3 decks | ✓ ai-only | **ship** |
| **M14** PiF post-chain GY        | ✓ | ✓ | ✓ | ✓ | ≥2 decks (Storm, Living End) | ✓ ai-only | **ship** |
| **M15** mulligan land slack      | ✓ | depends on `gameplan.curve_out` (allowed) | ✓ | ✓ | ≥4 decks | ✓ ai-only | **ship** (after D2 resolved) |
| **R2** Galvanic delete handler   | ✓ | ✓ | ✓ | ✓ | ≥4 cards | ✓ engine-only | **ship** (reduces baseline by 1) |
| **R3** Land-ETB triggers         | ✓ | ✓ | ✓ | ✓ | ≥30 lands | ✓ engine-only | **ship** |
| **R4** Teferi-TR static          | ✓ | ✓ | ✓ | ✓ | ≥4 cards | ✓ engine-only | **ship** |
| **R5** `parse_x_cost` tighten    | ✓ | ✓ | ✓ | ✓ | ≥8 cards | ✓ engine-only | **ship** |
| **R6** Ral coin-flip             | ✓ | ✓ | ✓ | ✓ | universal | ✓ engine-only | **ship** (after D1 resolved) |

**21 fixes pass the contract.** Zero card-name patches, zero deck-name gates, zero bare numeric literals. Every fix is sourced from existing primitives in `ai/clock.py`, `ai/bhi.py`, `ai/combo_calc.py`, `ai/gameplan.py`, or `ai/strategy_profile.py`, or names a new constant with inline justification.

**Baseline movement:** R2 (Galvanic delete) would *decrease* `tools/abstraction_baseline.json` `card_name_check_count` by 1. No fix increases either baseline file.

---

## Recommended implementation order

This is the synthesis's recommendation for the follow-up PRs. The order is chosen so each PR can land independently and is verifiable on the existing matrix.

1. **PR-A (engine): M1-engine + R1.** Split impulse-draw out of `draw_cards()`. Single failing test green. Bowmasters / Sheoldred stop firing on Reckless Impulse / Wrenn's Resolve / Glimpse. Estimated matrix impact: Storm vs Dimir +8-15pp, Storm vs Esper +5-10pp, all impulse-draw decks vs Sheoldred decks +3-7pp.

2. **PR-B (AI): M1-AI.** Chain self-damage projection. Storm stops chaining past lethal at low life. Validates against `audit_storm_vs_dimir_s60101.txt` G1 T4 explicitly. Estimated matrix impact: Storm field +2-4pp (avoiding self-kills).

3. **PR-C (AI): M2.** Chain-aware counter target. Highest single-fix impact. Estimated: Azorius vs Storm +8-15pp, Dimir vs Storm +5-10pp, Jeskai Blink vs Cascade/Living End +5-8pp.

4. **PR-D (AI): M4 + M12.** Panic-zone + defender chump. Lifts midrange + control universally. Estimated: Dimir +5-7pp field, Azorius +3-5pp field. Small loss (0-3pp) on hyper-aggro mirrors (correctly chumping vs blowing out with combat trick).

5. **PR-E (AI): M5 + M3.** Planeswalker EV + proactive tap-out floor. Together they fix Azorius's "pass below threshold runs the game out" pattern. Estimated: Azorius +5-12pp vs aggro, +0-3pp vs combo.

6. **PR-F (AI): M6.** `_project_spell` discard. Single-branch fix, highest class-size (~150 cards). Estimated: Dimir +3-5pp field; lifts 8-Rack-style archetypes if added later.

7. **PR-G+ (engine + AI rest):** R2, R3, R4, R5, R6, M7, M8, M9, M10, M11, M13, M14, M15. Each independent.

After each PR: `python -m pytest tests/ -q` green; `python run_meta.py --matrix -n 20` confirms the directional shift; matrix delta ≥ +50% of the claimed shift required, else escalate.

---

## Replay-viewer cross-references

Each replay HTML is committed to the repo. Open in browser to verify panel claims:

- [Match 1 — Azorius vs Storm](../../replays/audit_azorius_vs_storm_s60100.html)
- [Match 2 — Storm vs Dimir](../../replays/audit_storm_vs_dimir_s60101.html) — **G1 T4 is M1's smoking-gun decision**
- [Match 3 — Dimir vs Boros](../../replays/audit_dimir_vs_boros_s60102.html)
- [Match 4 — Boros vs Azorius](../../replays/audit_boros_vs_azorius_s60103.html)

Specific decision IDs cited across the panel:
- **Azorius vs Storm:** `g1t7d28`, `g1t8d36`, `g2t3d63/d64`, `g2t4d66/d67`, `g2t4d68-d80`
- **Storm vs Dimir:** G1 T4 (chain self-kill, NDJSON `seq=90-99`); G2 T5 (Wish EV pivot, `seq=231-233`); G3 T4-T5 (pass-threshold trap, `seq=341,345,371,375`); G3 T6 (Dimir counter on Reckless Impulse, txt:965-1000)
- **Dimir vs Boros:** `g1t3d7/d8`, `g1t4d13/d14`, `g1t5d17` (Frog into Charm), `g1t5d18/d19`, `g1t6d22/d23` (T6 Thoughtseize on Bombardment)
- **Boros vs Azorius:** `g1t4d11/d12` (Teferi pass), G1 T5 Wrath of the Skies (positive datapoint), G2 T4 Phlage face vs Teferi (M10)

---

## Unresolved — needs root-cause investigation before any code lands

These were surfaced by individual panels but could not be lifted to a clean structural fix in this audit. They go to follow-up investigation, NOT to a patch.

1. **Why does Azorius enumerate only 1-2 alternatives in candidate lists?** (Control) g2t3d63 enumerated exactly one alternative (Isochron Scepter at -13.9 EV). Counterspell, Solitude, Teferi, Verdict were in hand but absent from `alternatives`. Distinct from M5 (which fixes the EV); this is about candidate-set composition. Needs an enumeration trace.

2. **Why did Azorius G2 cast Consult the Star Charts at -0.14 EV on T8 instead of holding for Counterspell on Storm's chain?** (Midrange) Possible duplicate of M2; possible separate "scry spells score even when they fizzle" issue.

3. **Bowmasters draw-trigger interaction in Dimir games.** (Midrange) Bowmasters never triggered on opp draws across 3 games. Either an engine bug (trigger never fires) or a target-selection bug (every choice was face). Needs a focused trace of `engine/card_effects.py:orcish_bowmasters` and the `on_draw_trigger` hook. May intersect M1 / R1 — if impulse-draw fires the trigger, real draws might be silently suppressing it via a guard.

4. **Storm partial-chain decision math** (Combo). Storm vs Dimir G3 T6 — Storm fired 5-copy Grapeshot for 5 damage instead of holding for lethal or going all-in. Likely `combo_chain.expected_chain_size` mis-estimation when 2-3 cantrips + 3+ rituals would be needed.

5. **Hall of Storm Giants' creature-land animation, Past in Flames self-flashback.** (Rules) Not observed in this corpus; cannot confirm or deny.

---

## Reading order for the next session

1. **This document** — synthesis (executive summary + matrix + Top consensus findings)
2. **`docs/history/audits/2026-05-16_rules_audit.md`** — Engine fixes R1-R5
3. **`docs/history/audits/2026-05-16_combo_pro_audit.md`** — Most detailed cross-pattern analysis (the "damage IN vs damage OUT" framing)
4. **`docs/history/audits/2026-05-16_control_pro_audit.md`** — Decisions 1, 2, 7 are load-bearing
5. **`docs/history/audits/2026-05-16_midrange_pro_audit.md`** — F1 is uniquely high class-size
6. **`docs/history/audits/2026-05-16_aggro_pro_audit.md`** — Cross-patterns A-F frame race math
7. **`docs/history/audits/2026-04-26_storm_pro_audit.md`** — context for combo_calc changes (touchy area)
