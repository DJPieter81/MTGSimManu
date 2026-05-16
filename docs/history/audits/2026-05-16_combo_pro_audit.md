---
title: Pro Combo player audit — 2026-05-16
status: active
priority: secondary
session: 2026-05-16
depends_on:
  - docs/history/audits/2026-04-26_storm_pro_audit.md
  - CLAUDE.md
tags: [audit, pro-review, combo, storm, goryo, amulet, bowmasters, drawback-permanents]
summary: >
  AI plays Storm chains competently when storm > 0 but mis-plays
  pre-chain decisions: ignores per-our-draw life tax (Bowmasters
  blew up a winning Storm hand at 10 life), passes turns instead
  of digging when chain is dormant, and equity-leaks tap-out punish
  windows on control. Eight structural findings, all lift to
  Amulet/Goryo/Living End/Cascade combo decks.
---

# Pro Combo player audit — 2026-05-16

**Corpus:** 4 Bo3 replays (text + NDJSON) — Azorius vs Storm (Storm 2-0), Storm vs Dimir (Dimir 2-1), Dimir vs Boros (Boros 2-0), Boros vs Azorius (Boros 2-0).
**Lens:** professional Modern combo pilot — Storm/Goryo/Amulet specialist.
**Files:** `/home/user/MTGSimManu/replays/audit_{azorius_vs_storm_s60100,storm_vs_dimir_s60101,dimir_vs_boros_s60102,boros_vs_azorius_s60103}.{txt,ndjson}`

## Executive summary

**Overall grade: 6.0/10.** The chain-execution layer is sound — once Storm is `storm >= 1` with a finisher accessible (G2 Storm vs Dimir, both games of Azorius vs Storm), the AI sequences ritual→cantrip→ritual→Wish→Grapeshot like a pro. It correctly held Wish from EV -9.6 to EV +41.6 across one chain step (G2 Storm vs Dimir seq=231→233 NDJSON). It correctly fired Wish twice for Grapeshot lethal at storm=11 (G1 Azorius vs Storm T9).

The failures are all **pre-chain** or **drawback-permanent-aware** decisions:

**Top 3 systemic issues:**
1. **Per-our-draw life tax not modelled.** `engine/card_effects.py` correctly fires Bowmasters' trigger on every draw, but `ai/ev_evaluator.py` and `ai/combo_calc.py` never charge our drawing spells (Reckless Impulse, Wrenn's Resolve, Glimpse the Impossible) against our own life total when an opp Bowmasters / Sheoldred / Underworld Dreams analogue is on board. **Class size: ~80 cards.** Storm vs Dimir G1 T4 — Storm at 10 life, 2 Bowmasters on board, casts Reckless Impulse (2 draws → 4 dmg → 6 life), then Pyretic Ritual, then Glimpse (3 draws → 6 dmg → 0). **The AI killed itself with its own draws.**
2. **Storm passes turns instead of digging when chain is dormant.** G3 Storm vs Dimir T4 main1, T4 main2, T5 main1, T5 main2 — Storm at 14→13 life under Bowmasters + Orc Army + Psychic Frog clock, holds Reckless Impulse / Past in Flames / Desperate Ritual, returns "below pass_threshold" all four checkpoints (NDJSON seq=341,345,371,375). Reckless Impulse is a cantrip whose entire purpose is the next-turn chain — passing with mana up while behind on board is the classic mis-play. Root: `ai/combo_calc.py:897-908` STORM_HARD_HOLD clamps rituals to ~-99 EV when `not has_finisher and not has_pif and not snap.am_dead_next`; the projection drops cantrip EVs to -10 from `urgency_factor` when `opp_clock > 1`. Both layers compound. **Class size: every combo deck that pre-cantrips into its chain — Storm, Amulet (Explore), Living End (Force of Negation drawback isn't this but Imperial Recruiter is similar), Cascade.**
3. **Tap-out windows on control not punished by AI's own pilot side.** G1 Azorius vs Storm T8 — Azorius taps out for Consult the Star Charts at X=5 (6 mana). Storm draws Gemstone Caverns next and goes off normally on T9 *without* recognising that "opp is tapped out, opp has fewer cards, this is the dream window." The win came at T9 *anyway*, but the AI passed T8 with combo pieces in hand and a tapped opp. Same shape G2 Storm vs Dimir T5 — opp tapped Archmage's Charm, Storm correctly went off the following step. The issue is the *trigger* isn't represented: Storm should *prefer* to combo on the turn right after an opp tap-out. **Class size: every combo deck vs every reactive deck — Storm, Amulet, Goryo, Living End, Cascade.**

**Top 3 things AI got right:**
1. **Tutor-as-finisher-access on the chain itself.** G2 Storm vs Dimir T5 — Wish jumped from -9.6 to +41.6 to +51.2 as the chain crystallised; both Wishes fired (one for Past in Flames, one for Grapeshot) — pro line.
2. **Splice onto Desperate Ritual.** G2 Storm vs Dimir T5 — AI spliced Desperate Ritual onto itself (`Splice Desperate Ritual onto Desperate Ritual`), getting 2×3R mana off one cast. This is the deepest cut in Storm chain math and the AI got it right.
3. **Storm count discipline.** G2 Azorius vs Storm T4 — AI fired Past in Flames at storm=10 before the first Grapeshot, banking 11 copies on Grapeshot #1 plus 12 on Grapeshot #2. The "rituals first, finisher last" sequencing is correct.

## Per-match findings

### F1. Bowmasters life-tax: Storm kills itself with its own draws
**Severity: P0.** Source: Storm vs Dimir G1 T4 (txt:208-254, ndjson seq=90-99).
- **Symptom.** Storm at 10 life, 2 Bowmasters on board. AI sequence: Ral (1R, transforms!) → Desperate Ritual (3R) → Reckless Impulse (1R, draws 2 → 2×Bowmasters trigger = 4 dmg, life 10→6) → Pyretic Ritual (1R, 3R) → Glimpse the Impossible (2R, draws 3 → 6 dmg, life 6 → 0). Storm lost on its own combo turn.
- **Mechanism.** `_estimate_combo_chain` in `ai/ev_evaluator.py:2645-2673` projects damage out but does NOT subtract per-draw-trigger damage from `snap.my_life`. The chain looks viable in projection ("damage = 12, can_kill" returns True) but the executor takes lethal damage mid-chain.
- **Class size.** ~80 cards: Orcish Bowmasters, Sheoldred the Apocalypse (2 to drawer), Underworld Dreams, Heliod's Pilgrim no — wait, every drawback permanent that pings the active player on each card draw. Generalises to "active-player per-draw life tax permanents on opp battlefield."
- **Subsystem.** `ai/ev_evaluator.py:_estimate_combo_chain` — projection layer.
- **Failing test (rule-phrased).** `tests/test_combo_chain_models_opp_per_draw_tax.py` — given opp has a permanent with `whenever [we] draw a card → deals 1 damage to that player`, and our hand's chain draws K cards, the chain's projected EV is reduced by K × per-draw-damage charged against my_life; if `my_life - K × per_draw_damage <= 0`, chain returns `can_kill=False` regardless of opp damage.
- **Lift-check.** Lifts every combo deck that draws into its chain (Amulet draws via Explore/Karn/Tolaria West cantrips, Goryo draws via Faithless Looting / Through the Breach setup, Cascade decks via cycled cantrips). Also lifts the *opponent* model: when WE play Dimir with Bowmasters, the AI should value Bowmasters higher against combo opponents (currently flat-scored).

### F2. Speculative-chain hold over-clamps cantrips when storm=0
**Severity: P0.** Source: Storm vs Dimir G3 T4-T5 (txt:836-922, ndjson seq=341,345,371,375).
- **Symptom.** Storm at 14→13 life, no Ral, hand contains Reckless Impulse + Pyretic Ritual + Desperate Ritual + Past in Flames. AI passes T4M1, T4M2, T5M1, T5M2 — four consecutive priority windows — with `reason: "below pass_threshold"`. Reckless Impulse alt scored EV -10.142. By T6 Storm has drawn into Scalding Tarn + Grapeshot, ritual chains incompletely (storm 5, 6 damage), opp survives, kills back T7.
- **Mechanism.** Two compounding penalties: (a) `combo_calc.py:897-908` STORM_HARD_HOLD on ritual at storm=0 with `not has_finisher and not has_pif and not am_dead_next` (correct in isolation — burns mana at phase end). (b) `ev_evaluator.py:2645-2673` chain-projection returns 0 because `_estimate_combo_chain` returns (False, 0, 0) when no finisher in hand — the cantrip's intrinsic dig value (+1.5 cards / 2 selections) is NOT added when projection is empty. So Reckless Impulse falls to projection floor minus urgency factor → ~-10 EV.
- **Class size.** ~200 cards: every cantrip in combo shells. Reckless Impulse, Wrenn's Resolve, Manamorphose, Faithless Looting, Thought Scour, Mishra's Bauble, Consider, Opt, Preordain — anything that digs without immediately closing.
- **Subsystem.** `ai/ev_evaluator.py:_estimate_combo_chain` AND `ai/combo_calc.py` `_card_combo_modifier`. The `dig` role at storm=0 currently returns 0; should return positive `cards_drawn × expected_card_ev` where `expected_card_ev` = library composition × `is_chain_fuel` ratio, derived from existing primitives.
- **Failing test (rule-phrased).** `tests/test_cantrip_fires_when_chain_dormant.py` — given storm=0, no finisher in hand, opp_clock > 1, mana available, a cantrip in hand whose expected dig hits a `is_chain_fuel` card with library composition ≥ 50%: the cantrip scores positive EV and is not gated by pass_threshold. (Generalises to: "any free-roll dig at expected positive value fires.")
- **Lift-check.** Amulet Titan's Explore/Magus of the Vineyard/Tolaria West, Goryo's Through the Breach setup turns where Looting digs, Living End cycling chain — all rely on cantrip-first-fuel-later sequencing. A fix here lifts every combo shell.

### F3. Tap-out punishment window not surfaced
**Severity: P1.** Source: Azorius vs Storm G1 T8 (txt:463-490), Storm vs Dimir G2 T5 (combo turn comes a turn late vs the obvious window).
- **Symptom.** Azorius taps out T8 for Consult the Star Charts (uses Otawara, Island, Hallowed Fountain ×2, Plains, Thundering Falls, Hall of Storm Giants, Meticulous Archive — but only 6 actually tapped). Storm has 6 lands, 3 Ruby Medallions, Past in Flames in graveyard. AI does *not* recognise "this is THE window to combo" — it pushes mana through normally and wins T9, but a faster line existed.
- **Mechanism.** `ai/bhi.py` tracks counter probability based on opp mana + colour, but doesn't escalate the "fire combo now" signal when opp_untapped_mana drops below their cheapest counterspell. The `counter_pct` field in DECISION events is 0.0 for these Storm combo spells when opp has 0 untapped islands, which is correct, but there's no *positive* signal "opp is tapped out, this is the dream turn."
- **Class size.** Every combo deck × every reactive opp. Storm vs Azorius/Dimir/4-5c Control/Jeskai. Goryo vs Dimir/Control. Amulet vs Control. ~6 of 16 modern matchups.
- **Subsystem.** `ai/bhi.py` — add a `tap_out_window` signal alongside `counter_probability` that fires when `opp_untapped_mana < min(known_counter_costs)`. `ai/ev_evaluator.py:_score_spell` consumes the signal as a multiplier on chain-EV when the chain is even-marginally lethal.
- **Failing test (rule-phrased).** `tests/test_combo_fires_on_opp_tap_out.py` — given opp's untapped mana is strictly less than their cheapest known counterspell cost AND we have a chain that deals ≥ opp_life × 0.6 damage, the AI fires the chain in preference to a "build engine for next turn" play, even when next-turn-EV is slightly higher (the tap-out window IS the EV).
- **Lift-check.** Lifts every combo deck. Particularly Goryo (one Through the Breach window vs counterspells), Living End (Force of Negation tap-down), Amulet (Cavern of Souls vs Counterspell turn).

### F4. Solitude not evoked on T4 against tempo Boros
**Severity: P1 (control side, but visible to combo lens).** Source: Boros vs Azorius G1 T4 (txt:202-260).
- **Symptom.** Azorius has Wrath of the Skies (2WW) + Solitude (3WW pitch white card) + Teferi Hero + Prismatic Ending in hand on T4 with 4 lands (Hallowed Fountain, Hallowed Fountain, Island, Steam Vents). Boros has Ajani Pariah + 2 Cat Tokens on board, 5 power. Azorius passes T4M1 doing nothing. Boros casts Phlage for 3 + crew attacks for 5 = -8 life. Azorius dies T7.
- **Mechanism.** Evoke is recognised (`ai/ev_player.py:760-768`) but the AI's evoke gate requires `snap.am_dead_next` for the desperate bonus. On T4 opp_clock_discrete is probably 3 (Phlage hits T4 for 3 = 16 life), so `am_dead_next=False` and Evoke is penalised by `card_clock_impact × EVOKE_CARD_LOSS_MULTIPLIER`. From a combo lens this is the same `urgency_factor` failure as F2 — the AI undervalues *acting now* when "act next turn" is available.
- **Class size.** All evoke elementals (Solitude, Subtlety, Endurance, Fury, Grief — 5 cards) plus pitch-card analogues (Force of Negation, Force of Will, Force of Vigor — 3 more). 8 cards minimum.
- **Subsystem.** `ai/ev_player.py:760-768` evoke overlay. The fix is symmetric to F3: when opp_clock_discrete ≤ 3 AND the evoke target neutralises ≥ 50% of opp's on-board power, evoke fires regardless of `am_dead_next`.
- **Failing test (rule-phrased).** `tests/test_evoke_fires_when_opp_clock_short_and_target_kills_power.py` — given an evoke creature in hand, opp_clock ≤ 3, the evoke target removes a creature whose `creature_threat_value` ≥ 30% of opp's total power: the evoke EV exceeds pass_threshold, AI fires.
- **Lift-check.** Lifts Azorius Control, 4/5c Control, Jeskai Blink (Solitude in MB), 4c Omnath (Solitude/Endurance). Five of the 16 decks contain evoke.

### F5. Bowmasters target priority — face-tap vs Ral-tap
**Severity: P2.** Source: Storm vs Dimir G1 T2 (txt:131-138).
- **Symptom.** Dimir casts Orcish Bowmasters on T2 with Storm at 17 life and no creatures. Bowmasters' ETB pings — Dimir taps the trigger to face for 1 (correct). Later turns Bowmasters' "whenever opp draws a card" triggers also tap to face. These are correct as long as Storm has no creatures. **However:** when Storm cast Ral T4 (G1 Storm vs Dimir doesn't have this but G3 does), Bowmasters should preferentially tap Ral (kill the engine) over face (do 1 more damage). The AI does tap Ral once Ral exists (G3 T3 line 819 — Ral blocks Bowmasters — Dimir's choice was forced by block, not by free choice on trigger). No clean evidence of *misprioritisation*, but the AI's target ranking isn't visible in these traces.
- **Mechanism.** Bowmasters' "deals N damage" trigger goes through `ai/ev_player.py` target selection. Need to verify the ranking puts creature kills above face damage when face damage doesn't accelerate clock.
- **Class size.** ~30 cards (pingers + ETB-damage creatures).
- **Subsystem.** `ai/ev_player.py:_score_target` (target selection for our own triggered abilities).
- **Failing test (rule-phrased).** `tests/test_pinger_targets_combo_engine_over_face.py` — given a pinger trigger with two targets (face dealing N damage where N < opp_life vs killing a creature whose `creature_threat_value` ≥ N × 5), AI picks the creature kill.
- **Lift-check.** Lifts Dimir, 4c Omnath (Ragavan ping), any deck with damage triggers (Phlage's "3 damage to any target" — though that's already on a 4-mana cast and gets prioritised more carefully).

### F6. Combo deck refusing to cast its own Wish at low storm count
**Severity: P1.** Source: Storm vs Dimir G3 T6 (txt:944-998).
- **Symptom.** Storm fires the chain on T6 at 13 life, sequence: Wrenn's Resolve countered → 2× Desperate Ritual (spliced) → Desperate Ritual → Pyretic Ritual → Grapeshot at storm=5 (6 damage to opp, leaves 14 life). Storm does NOT cast Wish for Past in Flames + Grapeshot. Wish was not in hand at this exact turn — but earlier T7 G3 (txt:1062-1102), Wish was again not cast despite Past in Flames + Reckless Impulse chain. Storm fizzles at storm=5 with opp at 14 life, dies to Murktide T7.
- **Mechanism.** The "fire-now" branch at `combo_calc.py:741-744` returns `(storm + 1) / opp_life × combo_value` if total_fuel > 0 — but when `total_fuel == 0`, the branch at lines 760-783 says "fire now baseline + 1 unit for expected next draw growth" → fires Grapeshot at storm=5. But the *correct* play is to first cast Past in Flames, then another ritual, then Grapeshot at storm=8 for 9 damage. The PiF-as-chain-extender path isn't taken because the assessor at line 905 says `has_pif = False` when GY fuel is thin or PiF mana check fails.
- **Class size.** All flashback-combo decks. Storm (Past in Flames), Dredge (Cabal Therapy + Bridge from Below), Living End (Living End itself).
- **Subsystem.** `ai/combo_calc.py:_has_viable_pif` — the gate currently checks GY fuel + mana to cast PiF, but doesn't account for "PiF gives flashback to entire GY including the rituals we'll have AFTER firing the next 2 ritual casts."
- **Failing test (rule-phrased).** `tests/test_pif_viability_includes_future_gy_fuel.py` — given PiF in hand, fuel in HAND of cmc ≤ mana_after_pif, library has ≥ 1 fuel card (post-PiF graveyard will have current GY + the fuel from hand): PiF scores positive and is preferred over the suboptimal direct-Grapeshot finish.
- **Lift-check.** Lifts Living End directly (the analogous question: "is the cascade chain viable given current GY plus what hits the GY when we cycle next turn"). Lifts Dredge symmetrically.

### F7. Mulligan logic over-eager to keep 4-land + 0-payoff hands
**Severity: P2.** Source: Boros vs Azorius G2 P2 (txt:481-489).
- **Symptom.** Boros opening hand: Marsh Flats, Windswept Heath, Elegant Parlor, Arid Mesa, Seasoned Pyromancer (3), Goblin Bombardment (2), Guide of Souls (1). 4 lands, 0 1-drops, 1 2-drop, 1 3-drop. Keeps on `Guide of Souls + 3 cheap spells`. This hand has flooded land count and no T1-T2 pressure — pro pilot mulligans.
- **Mechanism.** `ai/mulligan.py` accepts any hand with a "key card" + ≥3 "cheap spells". 4 lands isn't penalised hard enough. Pro rule: ≥ 4 lands in 7 cards on the draw = mulligan unless every spell is a 4+ drop you NEED to ramp into.
- **Class size.** Every aggro/tempo deck (Boros, Domain Zoo, Affinity, Prowess, Storm). ~8 of 16.
- **Subsystem.** `ai/mulligan.py:always_early` slack — the audit's F1.1 carryover from 2026-04-26. Suggested tighten from +2 to +1 slack on land count was deferred.
- **Failing test (rule-phrased).** `tests/test_aggro_mulligans_4_lands_no_one_drop.py` — given aggro/tempo deck, opening hand with 4 lands and zero 1-drops, gameplan declares `curve_out` goal: mulligan returns True.
- **Lift-check.** Lifts every aggro deck and Storm itself (Storm with 4 lands + Medallion + 3 expensive cards mulligans).

### F8. Ral's coin-flip damage tax not in chain projection
**Severity: P1.** Source: Azorius vs Storm G1 T6 (txt:355-385).
- **Symptom.** Storm casts Ral T6 (1R), then Desperate Ritual (1R) → Ral lost coin flip → 1 damage to Storm (life 17 → 16). The chain continues fine here (Storm at 16+ life, never threatened). But on a tighter life total (e.g. G1 Storm vs Dimir T4 at 10 life), each spell cast post-Ral has a 50% chance of dealing 1 damage to Storm. With 5 spells cast post-Ral, expected damage to self = 2.5.
- **Mechanism.** Ral's "flip a coin, lose → 1 damage" trigger is not modelled in `_estimate_combo_chain`. The chain projects as if Ral is free damage upside.
- **Class size.** ~10 cards (coin-flip / "may pay N life" / chained drawback-self-damage triggers). Karn Liberated's ult, Squee's Embrace pattern, etc.
- **Subsystem.** `ai/ev_evaluator.py:_estimate_combo_chain` — same site as F1. The fix is the same: subtract expected per-spell damage from `snap.my_life` during projection.
- **Failing test (rule-phrased).** `tests/test_chain_models_self_damage_per_spell_cast.py` — given own permanent with "whenever you cast a spell, 50% deals 1 damage to you" (or equivalent oracle), chain projection subtracts `0.5 × spells_in_chain` from my_life; if result ≤ 0, can_kill = False.
- **Lift-check.** Lifts any deck running Ral, Monsoon Mage (Storm). Generalises with F1 to the broader "model damage TO US during our combo turn" mechanism.

## Cross-match patterns

1. **Combo decks die during their own combo turn.** F1 + F8 + F2 share a root: the chain projection in `_estimate_combo_chain` models damage OUT but not damage IN. Any combo turn involving multi-draw cantrips against Bowmasters / Sheoldred / Underworld Dreams (F1), Ral coin-flip (F8), or simply "we ran out of mana mid-chain and have 0 mana up for an opp's instant-speed kill" (F2 indirectly), the AI sees +win-swing and ignores the self-damage. **Single shared fix:** add a `chain_self_damage_estimate(game, chain, snap)` primitive that walks the chain's spells and sums per-spell-cast self-damage (oracle text pattern: `whenever [we] cast → damage to [you|controller]` AND opp-controlled per-our-draw permanents). Subtract from `snap.my_life` in projection. Same primitive applies to OUR side modelling opp's drawback permanents.

2. **Pass-threshold gate ignores "build for next turn" plays.** F2 + F3 + F4 share a root: `pass_threshold = -3.0` (Storm profile) is too aggressive when the alternative is doing literally nothing on a turn we're at parity. The pro mindset: "if I can cast a Reckless Impulse that costs me 2 life (Bowmasters) and digs 2, I take the trade — the dig finds the chain." The AI's threshold gate currently picks "pass" over "cast cantrip at -10" because the projection penalises future-value plays without "this turn closes" pressure. **Single shared fix:** when `chosen.action == 'pass'` AND `best_alternative.action == 'cast_spell'` AND the alternative is a `is_chain_fuel` cantrip AND `expected_dig_value > pass_threshold + 5`, override and cast.

3. **Tap-out windows are positive signals, not just "low counter probability."** F3 + the F2 "build for next turn" cousin both miss the same idea: when opp can't interact, our chain's effective storm threshold is *lower* (storm 4 lethal becomes storm 4 ACTUAL — no fluster, no counter). The AI should *accelerate* its combo timeline when opp's untapped mana drops below their cheapest counter. **Single shared fix:** `ai/bhi.py` exposes `opp_tap_out_window: bool` which `ai/ev_evaluator.py` reads as a chain-EV multiplier (e.g. ×1.25) when fixed.

## Recommended fixes (ranked, all structural)

| Rank | Fix | Subsystem | Mechanism (oracle/tag/primitive) | Lift |
|---|---|---|---|---|
| **1** | Self-damage-during-chain projection | `ai/ev_evaluator.py:_estimate_combo_chain` | Walk chain spells; for each `cast_spell` event sum (a) per-spell self-damage from own permanents (oracle: `whenever you cast … damage to you`), (b) per-draw damage from opp permanents (oracle: `whenever … draws … damage`). Subtract from `snap.my_life`. If `my_life - tax ≤ 0`, `can_kill=False`. | Storm + Goryo + Amulet + Cascade + any deck vs Bowmasters/Sheoldred/Dreams |
| **2** | Cantrip-fires-when-dormant fix | `ai/combo_calc.py:_card_combo_modifier` dig branch + `ai/ev_evaluator.py:_estimate_combo_chain` | At storm=0 with no finisher, return positive EV from `cards_drawn × expected_card_ev` where `expected_card_ev = lib_chain_fuel_ratio × ai.clock.life_as_resource(snap.my_life)`. Already-existing primitives. | All cantrip-heavy combo decks: Storm, Amulet (Explore), Living End (cycle chain) |
| **3** | Tap-out window as BHI signal | `ai/bhi.py` + `ai/ev_evaluator.py:_score_spell` | `BHI.opp_tap_out_window = opp_untapped_mana < min(known_counter_costs)`. Use as chain-EV multiplier `1 + tap_out_factor` where factor derives from `1.0 - get_counter_probability()`. No literal. | All combo vs reactive matchups (~30% of matrix) |
| **4** | PiF viability accounts for post-PiF GY | `ai/combo_calc.py:_has_viable_pif` | Project graveyard *after* the spells in hand that fit in current mana have been cast. PiF is viable if `post_chain_gy_fuel + mana_to_cast_pif >= chain_to_close`. | Storm + Dredge + Living End (analogous post-cycle GY question) |
| **5** | Pinger trigger targets engine over face | `ai/ev_player.py:_score_target` for triggered abilities | When firing a damage trigger with target choice, prefer creature kill if `target.threat_value × P(removal_sticks) >= face_damage × 5`. Already-existing primitive (threat_value); new ratio is rules-justified (1 creature = ~5 face damage equivalent). | All ping decks (Dimir, Boros Ragavan, Phlage decks) |
| **6** | Evoke fires at opp_clock ≤ 3 with right target | `ai/ev_player.py` evoke overlay | When opp_clock_discrete ≤ 3 AND `evoke_target.threat_value >= 30% × opp_total_power`, suppress the card-loss penalty. Reuses existing threat_value primitive. | 5 of 16 decks (Solitude/Subtlety/Endurance/Fury/Grief decks) |
| **7** | Aggro mulligan tightens land slack | `ai/mulligan.py:always_early` | Pull slack from +2 to +1 on land count when gameplan declares `curve_out` AND no 1-drops in hand. (2026-04-26 audit F1.1 carryover.) | Boros, Domain Zoo, Affinity, Prowess, Storm itself |
| **8** | Combo-deck shock-pay deferral with look-ahead | `engine/game_runner.py:80-81` (the F4.1 retry) | Replace the `archetype == 'combo' and turn <= 8 → True` bypass with: "untap if (a) `enables_spell` returns True this turn OR (b) next-turn-planned-cast in `ai/gameplan.py:current_goal` requires `mana_color` not provided by untapped lands." Brings look-ahead awareness without breaking Goryo. | Storm + Goryo (the original F4.1 revert reason) |

## Unresolved — needs root-cause investigation

- **Why does Dimir mulligan a 2-land hand with Counterspell + Frog into a 2-land hand with Murktide?** Storm vs Dimir G1 P2 (txt:36-40) — Dimir kept the worse hand (Murktide can't be cast until T7+ and Frog beats Counterspell on tempo). This is a Dimir mulligan bug, not in scope of this audit (combo lens) but flagging.
- **Storm vs Dimir G3 T7 Storm at 13 life dies despite hand of 7 cards.** Storm fires a chain that draws 6+ cards (3× Reckless Impulse, Wrenn's Resolve, Glimpse + flashback), no Bowmasters this time so F1 doesn't apply, but Storm casts ZERO finishers (no Wish, no Grapeshot — chain just digs without closing). The chain's exit condition needs investigation — is `_estimate_combo_chain` recognising Past in Flames in GY but failing to find a flashback target?
- **Why did Azorius G2 not cast Wrath when Boros had 2 creatures + Pyromancer?** Boros vs Azorius G2 T4 (txt:701-728) — Azorius drew Monumental Henge instead of Wrath, but the hand had Stock Up (cast T3). Stock Up's 3 picks should have surfaced Wrath if Wrath was in library. Possible: Stock Up's pick ranking doesn't prioritise sweepers when board has 3 power. Out of scope (control-side).

## Patches I refused to write — why these are symptoms, not causes

1. **"Detect Bowmasters by name and discount Reckless Impulse EV."** Card-name patch — violates contract rule #1. The mechanism is "per-our-draw life tax from opp permanent." Fix at the projection layer (F1) covers Bowmasters, Sheoldred, Underworld Dreams, plus any unprinted future card with the same oracle pattern.
2. **"Hardcode Storm to always cast Reckless Impulse on T4 if at 14+ life."** Deck-name + numeric-threshold patch — violates rules #2 and #3. The mechanism is "cantrip with positive expected card EV beats pass at threshold T" derived from existing primitives.
3. **"Tighten Storm's pass_threshold from -3.0 to -1.0 in strategy_profile.py."** Magic-number patch — does not lift other decks and creates a regression on actually-bad situations (cast a dead spell into a counterspell). The fix is the conditional override in F2/Cross-pattern #2.
4. **"Add Wish + Past in Flames + Grapeshot to a per-deck combo priority list in modern_meta.py."** Card-list patch — violates rule #1 and the "engine never scores" rule. The PiF-viability mechanism in F6 surfaces the right preference from oracle text + GY state.
5. **"Add `archetype == 'combo' and storm == 0 → cast Reckless Impulse with +5 EV bonus.`"** Archetype-gate is allowed if it lives in gameplan JSON — but the better fix is generic: any deck with `is_chain_fuel` cards in hand and a dormant engine should dig. Lives in `ai/combo_calc.py` as a tag predicate, not as an archetype check.

## Note on the chain-execution layer

Once a chain starts firing (storm ≥ 1), the AI is genuinely strong. Across the corpus the AI:
- Sequenced 11+ rituals + cantrips correctly (Azorius vs Storm G1 T9 storm=11 lethal)
- Spliced Desperate Ritual onto Desperate Ritual (Storm vs Dimir G2 T5)
- Held Wish from -9.6 EV to +41.6 EV across one chain step (correctly recognising tutor-as-finisher-access only ONCE the chain extends the access)
- Fired Wish→Past in Flames then Wish→Grapeshot in the correct order

The gap is **pre-chain decisions** and **chain-turn drawback awareness**. The 2026-04-26 audit's headline F2.1/F3.1 (Wish hold penalty) appears to be largely fixed by PRs #314+; this audit confirms tutor-as-finisher-access is working as intended in the steady-state chain.

The new bugs are all at the *entry* and *exit* edges of the chain — entering at storm=0 (F2, F3, F8) and exiting at lethal range with damage TO US (F1, F6, F8). They form a single shape: **the chain projection sees damage OUT but not damage IN.**
