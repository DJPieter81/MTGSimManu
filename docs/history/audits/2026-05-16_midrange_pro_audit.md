---
title: Pro Midrange player audit — 2026-05-16
status: active
priority: secondary
session: 2026-05-16
depends_on:
  - docs/history/audits/2026-04-26_storm_pro_audit.md
  - CLAUDE.md
tags: [audit, pro-review, midrange, dimir, omnath]
summary: >
  Single-pilot pro midrange audit across 4 Bo3 seeds (Azorius vs Storm,
  Storm vs Dimir, Dimir vs Boros, Boros vs Azorius). The dominant
  structural finding: forced-discard projection is missing from
  `_project_spell`, which collapses Thoughtseize EV to ~ -0.06 in every
  state — the AI never values stripping the opponent's best card.
  Secondary findings: reactive cards (Counterspell / Mystical Dispute /
  Solitude) rot in hand because control's pass-loop has no
  "hold-for-trigger" heuristic against tap-out combo turns; and
  midrange has no flip-to-defense gear-shift once life hits the
  Bolt-zone. All seven findings lift to generic mechanisms that also
  benefit Grixis Shadow, 4c Omnath, Goryo's Vengeance, and any future
  hand-disruption / counter-heavy archetype.
---

# Pro Midrange player audit — 2026-05-16

**Pilot lens:** multi-PT-top-8 midrange specialist (Dimir, 4c Omnath, Grixis Shadow). Lens habits: discard at instant speed when possible, target the highest-value-now card, treat removal as a finite resource ranked by clock, flip from aggressor to controller when life ≤ Bolt-range.
**Corpus:** 4 Bo3 replays generated 2026-05-16
  - `replays/audit_azorius_vs_storm_s60100.txt` (Storm 2-0)
  - `replays/audit_storm_vs_dimir_s60101.txt` (Dimir 2-1)
  - `replays/audit_dimir_vs_boros_s60102.txt` (Boros 2-0)
  - `replays/audit_boros_vs_azorius_s60103.txt` (Boros 2-0)
  - Per-decision NDJSON (`.ndjson`) for each, opened to inspect `DECISION` events with `chosen` / `alternatives` / EV / `targets` / `state`.

## Executive summary

- **Overall grade: 5/10.** Midrange-side play is mechanically clean (legal lines, valid mana taps, valid blocks) but strategically blind on three load-bearing axes: (a) discard targeting & valuation, (b) reactive-card timing under tap-out windows, (c) gear shifts from proactive to defensive when life drops. Aggro-side play (Boros) and combo-side play (Storm) are noticeably better than the midrange and control sides — Boros is, in fact, the strongest pilot in this corpus.
- **Top 3 systemic issues** (all P0/P1):
  1. **Thoughtseize EV is ~ -0.06 in every state.** `_project_spell` does not decrement `opp_hand_size` for forced-discard, does not credit the post-strip board, and does not subtract the 2-life cost. So Thoughtseize projects as "draw nothing, lose your own card from hand" — strictly negative. The AI casts it only when no other spell is better, and never proactively to break up a combo turn or pre-empt a planeswalker.
  2. **Reactive cards rot under tap-out windows.** Azorius held Counterspell + Mystical Dispute G1 vs Storm while Storm chained 7 spells through 2 Ruby Medallions; held Counterspell + Mystical Dispute G2 vs Storm while Storm killed it on T4; held Counterspell vs Boros while Phlage hit face for 3 + 3 escape. Azorius never counters anything except the FIRST reactive opportunity offered by its own main-phase loop, never holds for a higher-value target.
  3. **No defensive gear shift.** Dimir on 9 life vs Boros with 4 attackers untapped casts Psychic Frog tapped-out into open WR mana (Thraben Charm answer up); Azorius at life 3 casts Teferi of Dom (5 CMC, no body, no immediate impact) instead of Solitude or Supreme Verdict. The AI's `goal` field flips to `close_game` but the play-selection doesn't change — `close_game` and `grind_value` produce identical scoring for defensive cards.
- **Top 3 things AI got right:**
  1. **Bowmasters timing** — Dimir consistently dropped Bowmasters T2 in every game it had it, correctly recognising the on-cast ping as immediate interaction.
  2. **Murktide Regent delve sequencing** — G3 of Storm-vs-Dimir, Dimir delved 5 cards and cast 7/7 Murktide on T6 from 9 life with the right lands untapped for Consider after. Clean execution.
  3. **Boros pilots competently across the board.** Curve-out priority, Phlage face-targeting when no killable creature exists, Galvanic Discharge face-mode at low opp life, escape recast — all clean.

---

## Per-match findings

### F1 — Thoughtseize projection bug (P0)

**Symptom.** Dimir holds Thoughtseize across 18 main-phase decisions over 3 games against Ruby Storm and Boros Energy. The DECISION event consistently records `chosen.ev = -0.05` to `-0.06` for Thoughtseize regardless of board state, opponent hand size, life total, or known-by-prior-discard hand composition. Storm `audit_storm_vs_dimir_s60101.ndjson` shows Dimir T2 with Bowmasters, Frog, Thoughtseize in hand at life 16 vs Storm dev'ing into a combo turn — Thoughtseize EV = -0.06, Bowmasters EV = +1.18. AI picks Bowmasters, never reconsiders Thoughtseize for the next 4 turns. Storm goes off T4 G1 and T5 G2 with full grip uninterrupted.

**Mechanism.** `ai/ev_evaluator.py:_project_spell` (line 1631) writes `opp_hand_size=snap.opp_hand_size` (line 1647) — unchanged from the snapshot. It also does not subtract Thoughtseize's 2-life cost from `my_life`. So the projected delta from "cast Thoughtseize" is exactly `my_hand_size -= 1` and a `+1 storm_count` increment that doesn't matter to Dimir. `evaluate_board(projected) - evaluate_board(snap)` rounds to ~ -0.05 (lost-card penalty), independent of context.

**Class size.** ~120-150 cards. Every forced-hand-attack spell in Modern: Thoughtseize, Inquisition of Kozilek, Duress, Hymn to Tourach, Liliana of the Veil (-2), Persecute, Mind Rot, Dauthi Voidwalker triggered exile, Pack Rat discard, every "look at hand and exile/discard" effect. Targets opponent → opponent loses card → board state changes. This is a single missing branch in projection, not a per-card fix.

**Subsystem.** `ai/ev_evaluator.py` — `_project_spell` is the only place that owns "what does the board look like after this spell resolves". Engine `_force_discard` already chooses the right card via `score_card_for_opponent_strip` (which IS oracle-driven and uses gameplan signals) — that part is correct. The bug is the EV layer not crediting the effect.

**Failing test, rule-phrased.**
```
test_force_discard_decrements_opp_hand_in_projection:
    Given snap with opp_hand_size=5,
    When _project_spell runs on a card whose oracle is
    "target opponent reveals their hand. Choose a non-land card. They discard it",
    Then projected.opp_hand_size == 4 AND projected.my_life == snap.my_life - 2.
```
(Generic phrasing — does not name "Thoughtseize". The same test, parametrised on oracle text, covers Inquisition / Duress / etc.)

**Lift-check.** Dimir is the obvious beneficiary, but **Grixis Death's Shadow** and **8-Rack-style** archetypes get the same lift, as does **Goryo's Vengeance** (which packs Thoughtseize in MB for combo protection). Storm's SB Thoughtseize lines (when it boards them vs control) also benefit. Plus: Dauthi Voidwalker's exile-on-damage trigger uses the same `_force_discard` plumbing — the EV upgrade extends transitively.

---

### F2 — Reactive cards never trigger on tap-out windows (P0)

**Symptom.** Across all three control-side games (Azorius G1 vs Storm, Azorius G2 vs Storm, Azorius G2 vs Boros), Azorius held Counterspell + Mystical Dispute / Solitude / Wrath of the Skies but failed to counter the high-value spells. The text logs show only ONE response counter in 12 storm-cast spells across G1/G2 vs Storm (Wrenn's Resolve T5 G1, and Wrenn's Resolve T4 G2). Past in Flames, Wish, the second Glimpse, and the chain-closer Grapeshot all resolved unchallenged in BOTH games. In G2 vs Boros, Azorius held Counterspell during Boros's T4 Phlage cast (4-mana hasted 3-damage swing) and passed both main phases instead of waiting at end-of-opp-turn or in response.

The NDJSON DECISION events for Azorius show main-phase `pass` choices with the only alternative being expensive non-reactive cards (Isochron Scepter, Stock Up). Reactive cards never appear in the `alternatives` list because they're not legal as proactive plays — and there is no "predict opponent tap-out window, fire a discard/disruption pre-emptively" heuristic.

**Mechanism.** The response.py path (`decide_response` in `ai/response.py:42`) is invoked when the opponent puts a spell on the stack — it can correctly evaluate "should I counter THIS spell?". But it has no memory of "what should I have countered if I'd been patient?". It does not model the difference between countering Wrenn's Resolve (cantrip, +0.4 EV swing) vs countering Past in Flames (combo-enabler, ~+40 EV swing) — by the time Past in Flames is cast, the AI has already burned its counter on Wrenn's Resolve.

Inspection of `_held_counter_floor_ev` (line 375 in response.py) suggests the floor exists but isn't well-tuned for "Storm chain in progress" — it doesn't recognise that storm-count > 0 implies "more spells incoming, save the counter".

**Class size.** ~80-100 cards. Every conditional counterspell (Counterspell, Mana Leak, Force of Negation, Mystical Dispute, Spell Pierce, Stubborn Denial, Subtlety, Solitude-as-pitch, Force of Will) plus discard interaction at instant speed (Liliana's Caress triggers, Tibalt's Trickery on-stack). The mechanism is "rank stack items by EV swing, use highest-EV interaction for highest-EV target".

**Subsystem.** `ai/response.py` — owns the "respond to a stack item" decision. Needs a chain-aware heuristic: when opponent storm_count > 0 OR opponent has cost-reducer + 3+ cards in hand, raise the counter floor by `combo_chain_EV_estimate` so cheap chain-starters don't burn the counter.

**Failing test, rule-phrased.**
```
test_counter_prefers_chain_payoff_over_chain_starter:
    Given opp has Ruby Medallion in play AND storm_count > 0 AND 3+ cards in hand,
    When opp casts a ritual on the stack and I hold one Counterspell,
    Then decide_response returns False (pass), not True (counter).
    Replay scenario: counter the SECOND or THIRD spell in the chain
    (the one that converts mana into draw or a finisher), not the first ritual.
```
The corollary test: when opp casts a known finisher (Grapeshot, Tendrils, Past in Flames flashback), counter it unconditionally if mana is available.

**Lift-check.** Every U/X control deck benefits — Azorius Control, 4/5c Control, Dimir Midrange (which is a Counterspell deck), Jeskai Blink (when it boards Subtlety + Counter). Domain Zoo's Force of Negation post-board is the same shape. Test on Dimir T6 G3 in `audit_storm_vs_dimir_s60101.txt` line 965 — Dimir DID counter Reckless Impulse (correct, removing a chain extender). The same logic should fire for Azorius.

---

### F3 — No defensive gear-shift when life ≤ Bolt-range (P0)

**Symptom.** Two diagnostic decisions:

**(a) Dimir vs Boros G1, T5, life 9 vs 19, 4 attackers untapped, 4WR open across the board.** Dimir's `goal` flips to `close_game`, hand contains Psychic Frog (2/2 flyer post-combat-damage trigger, but no immediate impact) and Murktide Regent (uncastable, only 5 lands). AI casts Psychic Frog tapped-out. Boros casts Thraben Charm in beginning-of-combat: Frog dies for 6 damage (charm mode). Dimir attacked Boros for 0 damage that turn and gave up tempo equal to UB + a 2-CMC card.

DECISION event `g1t5d17` (NDJSON): `chosen.ev = 0.044`, alternatives all sub-zero. The projected EV doesn't credit "Boros has 5 untapped lands of correct colours + Thraben Charm density in their list" — `removal_pct` is recorded as 0.176, low for a deck running Thraben Charm + Galvanic Discharge + Phlage. Even on instinct, *don't tap out into 4 attackers and open mana at 9 life*.

**(b) Azorius vs Boros G2, T5, life 3, opponent at 21 life with Guide of Souls + Seasoned Pyromancer + 4 tokens on board.** Azorius casts Teferi, Hero of Dominaria (5 CMC, no body, +1 = "untap 2 lands"). Phlage escapes from graveyard on Boros's T6 and kills Azorius. The defensive plays available were Solitude (mainboard but not drawn this game — `Otawara, Soaring City` bounce on Phlage on the stack with Counterspell back-up was the survival line). Casting a 5-CMC card-draw planeswalker at life 3 vs aggro is the textbook "wrong gear" tell.

**Mechanism.** `goal` transitions (`grind_value` → `close_game`) are computed by `GoalEngine.current_goal` and consumed by the scoring layer, but the scoring layer does not actually differentiate. `_score_spell` returns the same projection-based EV regardless of which goal is active. There is no `defensive_mode` scoring branch that says "at my_life ≤ 3 vs aggressive opp_power ≥ 4, multiply non-defensive-card EV by 0 and prefer the lowest-CMC removal / blocker".

**Class size.** ~all of midrange + control (~250+ cards): every removal spell, every blocker, every life-gain card, every counterspell. When `opp_clock_discrete <= 2` and `my_life ≤ 5`, the scoring weights should rotate toward survival.

**Subsystem.** `ai/strategy_profile.py` already declares per-archetype weights — but the "panic mode" gate belongs in `ai/clock.py` (which already exposes `urgency_factor` and `opp_clock_discrete`) feeding into `ai/ev_evaluator.py:compute_play_ev` to upweight removal / blocker / lifelink when in the panic zone, downweight planeswalkers / card-draw / non-immediate-impact cards.

**Failing test, rule-phrased.**
```
test_panic_gear_at_lethal_minus_one:
    Given snap.my_life <= snap.opp_clock_one_turn_damage AND archetype != "combo",
    When ev_player.choose_play is called with hand
    containing a 5-CMC card-draw planeswalker AND a 4-CMC board-wipe,
    Then the board-wipe scores HIGHER than the planeswalker
    (currently the planeswalker scores higher because it has
    `card_draw` signal but the wipe also has `immediate_interaction`).
```

**Lift-check.** Dimir (this audit), 4c Omnath (lifegain triggers, must not over-extend pre-board), Azorius Control (this audit), Goryo's Vengeance (combo deck but its hate-piece slot must understand "I'm dying" mode), Living End (sideboard transformative — same gate applies G2). This is THE general mechanism for "stop trying to win, start trying to not lose".

---

### F4 — Discard advisor is correct but never seen because step 1 is broken (P1, contingent on F1)

**Symptom.** `ai/ev_evaluator.py:score_card_for_opponent_strip` (line 2900) correctly delegates to gameplan signals (`critical_pieces`, `always_early`, `mulligan_keys`) and oracle tags. This is the right design. But because F1 blocks Thoughtseize from ever firing on a sensible turn, the discard advisor is invoked perhaps once per match and at the wrong time (e.g. G1 T6 vs Boros at life 4 — far too late to disrupt the curve). The advisor's choice in that game stripped "Goblin Bombardment" — a defensible take, but the OPTIMAL pick would have been Ajani (which had already resolved) or Ranger-Captain of Eos (which had not been drawn yet). The TS-at-T6-on-life-4 timing is the bug.

**Mechanism.** Downstream of F1.

**Class size / subsystem.** Same as F1. Not a separate fix.

**Failing test.** Covered by F1's test plus a coverage test that asserts the advisor is called at least once per game where Thoughtseize is in starting hand.

**Lift-check.** Same as F1.

---

### F5 — Murktide projection ignores delve, so the AI never wants to cast it (P1)

**Symptom.** `audit_dimir_vs_boros_s60102.ndjson` G1 T5: Murktide Regent in hand at 5 lands; DECISION event records `ev = -30.98`. Murktide is the deck's premier finisher in this archetype but scores as if it cost 7 mana and contributed only 7 power. The AI's only reason for ever casting Murktide is exhaustion of alternatives — and even then it under-credits "delve fuels itself via the same fetch + draw stream you've already played", which is the entire reason midrange runs the card.

**Mechanism.** `_project_spell` projects power gain (`projected.my_power += p`) and treats `cmc = 7` as the cost. But the game-runner casts Murktide with delve (5 exiled, only UU spent) — this never feeds back into EV. The projection should detect delve in the oracle text and reduce the *effective* cost by `min(delve_max, graveyard_instant_sorcery_count)`, which raises the EV by `cost_reduction * mana_value_of_clock`.

**Class size.** ~30-40 cards. Every delve card (Murktide Regent, Dig Through Time, Treasure Cruise, Tasigur, Gurmag Angler, Become Immense). Also generalises to "alternate cost" projections — Force of Negation pitch-cost, Solitude evoke, Endurance evoke. The mechanism is "_project_spell looks at cost reducers AND alt-cost flags before computing `my_mana - cmc`".

**Subsystem.** `ai/ev_evaluator.py:_project_spell` (already has cost-reducer logic via Medallion, per F5.1 in the storm audit).

**Failing test.**
```
test_delve_reduces_projected_cost:
    Given snap with my_gy_instants_sorceries = 5,
    When _project_spell runs on a card with oracle containing "delve"
    and cmc 7,
    Then projected.my_mana == snap.my_mana - max(0, 7 - 5).
    Equivalent: the EV of casting a delve 7-drop with 5 IS in GY
    should be similar to casting a 2-drop 7/8 with the same body.
```

**Lift-check.** Grixis Death's Shadow (Gurmag Angler, Murktide), Izzet Prowess (Treasure Cruise occasionally), historical Delver lists (Treasure Cruise / Dig Through Time). One mechanism that also benefits the alt-cost family (Force of Negation pitch, Solitude evoke) when extended.

---

### F6 — Bowmasters value is right but the ping target should not always be face (P2)

**Symptom.** In `audit_dimir_vs_boros_s60102.ndjson` G2, Dimir casts Bowmasters T2 vs Boros's Ragavan and the engine correctly pings Ragavan for 1 (Ragavan dies, +1 token, +1 trade favourable). In G1 of the same match, Bowmasters comes down T2 BEFORE Ragavan and pings the opponent's face (Storm match, no creatures yet). Both are fine.

The concern: there is no DECISION-event-visible "choose Bowmasters target" — the choice is hardcoded in the effect handler. The pro-pilot would, on a board where the opponent has a 1-toughness creature + has just cast a card-draw spell, ping the creature OR (on the Storm matchup with Wrenn's Resolve / Reckless Impulse) ping the OPPONENT to trigger Bowmasters on their draws. The current implementation handles "deals 1 damage to any target" but the draw-triggered "deals X damage where X is cards drawn" oracle component is at risk of being mis-targeted by future Bowmasters-style cards.

**Class size.** ~20 cards. Bowmasters, Soul-Scar Mage's prowess pings (different mechanic), every "deals 1 to any target" ETB (Skyclave Apparition, Spear Spewer, etc.). The fix is a generic "pick best ping target" mechanism keyed to oracle text.

**Subsystem.** `ai/ev_evaluator.py:choose_target` family (or wherever single-target damage targets are picked).

**Failing test.**
```
test_ping_targets_1_toughness_creature_over_face:
    Given opp board contains a 1/1 creature with positive threat_value,
    When a 1-damage ping effect resolves with both opp-face and the
    creature as legal targets,
    Then the chosen target is the creature (highest threat-eliminated value).
```

**Lift-check.** Dimir, Goryo's Vengeance (Plague Engineer ping), historical Death and Taxes (Skyclave Apparition).

---

### F7 — `goal=close_game` doesn't actually change scoring (P1)

**Symptom.** Across all 4 matches the `goal` field flips from `grind_value` → `close_game` when opp_life is low or my_clock is short. But the DECISION events at those moments show identical EV ranking to the `grind_value` turn before. Example: Storm vs Dimir G3 T6 has Dimir with `goal=close_game` and EV-orders cards identically to T5 where `goal=grind_value`. The goal label is consumed only by the strategic_logger.

**Mechanism.** `GoalEngine.current_goal` produces a label, but `_score_spell` doesn't multiply by a per-goal weight vector. The strategy_profile per-archetype weights are static across the game.

**Class size.** All midrange + control + ramp decks. The mechanism is: "when goal == close_game, upweight finishers and damage spells; when goal == defend, upweight removal and lifegain; when goal == grind_value, upweight card_advantage and creatures with ETB."

**Subsystem.** `ai/strategy_profile.py` exposes per-archetype weights — extend to per-(archetype, goal) weights, consumed in `ai/ev_evaluator.py:evaluate_board`.

**Failing test.**
```
test_close_game_upweights_finisher_over_cantrip:
    Given gameplan declares Murktide Regent as a finisher AND
    Consider as a cantrip, both in hand, both castable,
    AND goal == close_game,
    Then EV(Murktide) > EV(Consider) by at least the deck's declared
    finisher_priority_weight.
    Inverse: goal == grind_value, EV(Consider) >= EV(Murktide) when
    my_creature_count >= 1 (cards > more bodies in grind mode).
```

**Lift-check.** Every non-combo deck (combo decks have a 2-mode toggle that already works: deploy_engine → execute_payoff). Dimir, Omnath, Boros (curve_out → close_game), Azorius (interact → close_game). Test this on a Dimir T6 transition.

---

## Cross-match patterns

1. **Pattern: All three "AI is bad at" findings (F1, F2, F3) share a root.** Each is "the scoring layer doesn't credit a non-board-state-changing-but-resource-state-changing effect". F1: discard affects opp_hand_size, not the projection. F2: holding a counter affects future-spell-EV-distribution, not current-turn EV. F3: defensive gear-shift requires the scoring to be life-aware, but `position_value` only encodes life as a continuous variable, not a step function at the Bolt-zone threshold. All three argue for a richer projection model that includes opp hand composition and a discrete "panic" mode.

2. **Pattern: Boros plays well because it's a curve-out deck.** Aggro is the easiest archetype to pilot — every turn deploy the highest-EV body, attack, repeat. The simulator-side weakness compounds on archetypes that require *not* deploying: control passing on T3-T4 with Counterspell up, midrange holding back at 9 life. This is exactly the historical observation in the LLM-judge audit ("aggro overperforms; midrange and control under-perform").

3. **Pattern: Sheoldred never gets cast across 2 Bo3 games where the SB included 2 copies.** This is partly draw variance (2 in 60 cards, ~7% chance of drawing one per game), but the deeper issue: even when drawn, the EV evaluator under-credits "drain 2 per opp draw step" — there's no `opp_card_draw_rate` term in projection, so Sheoldred's life-swing is invisible until she's already in play. Not a finding by itself (sample size 0) but a flagged risk for the next deeper audit.

4. **Pattern: Mulligan logic is sound.** Every mulligan in the corpus was either reasonable (Storm's "no cost reducer and no ritual+cantrip+finisher backup" → mulligan) or forced by land count. Dimir's keep heuristic gates on `critical_pieces`, which is per-deck-correct. No findings here.

5. **Pattern: Manabase economy is clean.** No double-shock T1 mistakes observed; fetch-for-untapped-relevant-color sequencing was correct in every game; Marsh Flats / Polluted Delta crack timing aligned with mana needs. The fetch-for-Watery-Grave on a turn Dimir would hold up UU was the right read in every observed instance.

---

## Recommended fixes (ranked, all structural)

1. **[F1, P0] Project forced-discard in `_project_spell`.** Add a branch: if oracle matches `target opponent ... discards` (or `target player ... discards`), decrement `projected.opp_hand_size` by 1 (or by the count in oracle for Hymn / Persecute), and subtract any life-loss from `projected.my_life` (Thoughtseize: 2). The EV recovery comes for free from `evaluate_board` (which already weights opp_hand_size as `opp_card_value × opp_hand_size`). Rule-phrased test in F1. **No card names, no deck gates, oracle-driven, single subsystem.**

2. **[F2, P0] Chain-aware counter floor in `ai/response.py`.** Extend `_held_counter_floor_ev` to read `opp.storm_count > 0` AND `opp.has_cost_reducer_in_play` AND `opp.hand_size >= 3` → raise floor by `_estimate_combo_chain` result (already exists in `ai/combo_calc.py`). When floor > current stack item's EV swing, pass. Generic mechanism — same plumbing for Wrenn-and-Six counter saving, Liliana of the Veil edict targeting, etc. No card names. **One subsystem (response.py), uses existing primitives (combo_calc, BHI).**

3. **[F3, P0] Panic-mode gear shift in `ai/clock.py` + `ai/ev_evaluator.py`.** Add `clock.is_panic_zone(snap)` → True iff `snap.my_life <= max(3, snap.opp_one_turn_damage)`. In `compute_play_ev`, when `is_panic_zone()`, multiply non-defensive EV by `panic_dampener` (derived from `urgency_factor`, not magic) and add a bonus equal to `lifegain_value` for spells with `lifelink` or "gain X life" oracle. No bare literals — `panic_dampener` is `1 - urgency_factor` or similar derivation, with the formula written in a constants comment. Rule-phrased test in F3. **Lifts Dimir, Omnath, Azorius, Goryo's, Living End.**

4. **[F5, P1] Delve cost reduction in `_project_spell`.** Detect oracle text "delve" → reduce projected cost by `min(max_delve, my_gy_instants_sorceries)`. Mirrors the existing Medallion cost-reducer logic. **Generic to all delve cards, no card names, single test.**

5. **[F7, P1] Per-goal weight modulation in `ai/strategy_profile.py`.** Extend `StrategyProfile` with `goal_weights: dict[str, dict[str, float]]` keyed by (goal, signal). Default identity. Per-deck JSON in `decks/gameplans/*.json` can override. The scoring layer multiplies the signal score by the (goal, signal) weight. **Data-driven, no engine touch, no card names.**

6. **[F6, P2] Generic ping-target selection.** Refactor "deals N damage to any target" choosers into a single `choose_damage_target(damage_amount, candidates, threat_table)` that prefers (creature whose threat_value > 0 AND toughness <= damage_amount) over face. Already half-implemented for Galvanic Discharge — generalise.

---

## Unresolved — needs root-cause investigation

- **Did Counterspell actually fire on Wrenn's Resolve in G1 vs Storm by chance, or is the response.py logic random / undertested?** The DECISION events don't cover responses; the text log shows the engine fired the counter when `decide_response` returned True. Need a `RESPONSE_DECISION` ndjson event to make these reviewable.

- **Why did Azorius G2 vs Storm cast Consult the Star Charts at -0.14 EV on T8 instead of holding mana for Counterspell on Storm's incoming chain?** This may be the same root cause as F2 (no chain-awareness in pass logic) or a separate "scry/dig spells score even when they fizzle" issue. Worth a targeted investigation.

- **Bowmasters draw-trigger interaction.** Dimir's Bowmasters never triggered on opp draws in the 3 games where it was in play across Storm and Boros matchups. Either the engine doesn't fire the trigger (engine bug) or the AI chose face-pings every time (target-selection bug). Worth a focused look at `engine/card_effects.py:orcish_bowmasters` and the `on_draw_trigger` hook.

---

## Patches I refused to write

- **"If `card.name == 'Thoughtseize'` then EV += 5"** — class size 1, hardcoded card name, fails abstraction contract rule 1. The correct fix (F1) covers Thoughtseize plus ~150 other discard cards via oracle text. **Refused.**

- **"If Dimir at life ≤ 5 then don't cast Psychic Frog"** — deck-name gate, fails contract rule 2. The correct fix (F3) is the generic panic-zone gear shift, which lifts every midrange and control deck. **Refused.**

- **"For combo opp matchups, hold Counterspell for the 3rd spell in their chain"** — both deck-name AND magic-numeric ("3rd"). The correct fix (F2) reads storm_count and chain-EV from existing primitives. **Refused.**

- **"Multiply Thoughtseize EV by 5 in gameplan JSON"** — this is a card-EV override of the kind explicitly called out as anti-pattern in `CLAUDE.md` ("Per-card EV tables in code or JSON → extend oracle-driven detection in `creature_threat_value()` instead"). The advisory layer already has the right primitives; the fix is in `_project_spell`, not in a per-card weight. **Refused.**

- **"At life ≤ 3, never cast a planeswalker"** — bare numeric literal + bare card-type gate. The correct fix (F3) parametrises panic threshold off `opp_one_turn_damage` and applies a derived dampener, not a hard skip. **Refused.**
