---
title: Pro Control player audit — 2026-05-16
status: active
priority: secondary
session: 2026-05-16
depends_on:
  - docs/history/audits/2026-04-26_storm_pro_audit.md
  - CLAUDE.md
tags: [audit, pro-review, control, counterspell, planeswalker, tap-out]
summary: >
  Modern control specialist audit of 4 Bo3 replays (Azor/Storm,
  Storm/Dimir, Dimir/Boros, Boros/Azor). Control side is grossly
  passive: planeswalkers and sweepers are absent from candidate
  enumeration, counterspells fire on the first castable spell
  rather than the chain-payoff, and "pass below threshold" runs the
  game until the AI loses. Five structural findings; one is the
  smoking-gun root cause of Azorius's 0-2 vs Storm.
---

## Executive summary

Overall grade: **4/10**.  The control AI does not play control.
It plays "stand still with mana up and hope something kills me."
Across four matches it never proactively deployed a planeswalker
or a sweeper while it still had life to spare; it cast
Counterspell on the first available spell in a Storm chain rather
than the chain-finisher; and on every turn it had a 7-card hand
and 2-5 untapped lands, the AI's candidate list contained one
play (Isochron Scepter at −13.9 EV) and `pass`.

The same defect — **`compute_play_ev` returns negative EV for
control finishers that don't immediately change the visible board
(planeswalkers, conditional sweepers, Stock-Up-style refills)** —
is responsible for three of the five findings below.  It is the
control-side analogue of the Storm Wish-hold defect documented in
the 2026-04-26 audit: the projection layer can't see the value of
a card that wins games through later turn-cycles.

### Top 3 systemic issues (all lift to mechanism)

1. **Planeswalkers + scry-engine card-draw spells score below `pass_threshold` when projection can't see immediate board change.** Teferi, Time Raveler vs aggro at T4 (g1t4d11/d12, audit_boros_vs_azorius): chosen=`pass`, only alternative `cast_spell/Teferi at EV=-5.6`. Supreme Verdict, Solitude, Stock Up are similarly absent from candidate lists with playable mana available. Root: `compute_play_ev` in `ai/ev_evaluator.py:2569` projects board state delta; a planeswalker's +1/-X ability has zero same-turn board delta unless `evaluate_board` credits the loyalty ability outcome, which it does not. The deferral signal `planeswalker_loyalty` (line 1070) gets the card past the gate, but the EV is negative once `evaluate_board` runs.

2. **Counterspell triage targets first eligible spell, not chain-payoff.** Azorius vs Storm G2T4 (audit_azorius_vs_storm_s60100.txt:914-918): Storm chains 2× rituals → Wrenn's Resolve → Azorius taps UU on Wrenn's Resolve (a +2 draw, NOT a payoff), tapping out. Storm then casts Reckless Impulse → Wrenn's Resolve → Manamorphose → Pyretic Ritual → Valakut Awakening → Past in Flames → Wish → Grapeshot for lethal storm. The correct hold is Past in Flames or Wish — both single-card finisher-access spells with no in-hand replacement; Wrenn's Resolve is a +2 draw that the deck has 4 copies of. Root: `ai/response.py:42` `decide_response` scores `stack_threat` purely from the spell-on-stack's own EV, not from "P(this enables a payoff THIS turn)". A real Storm chain has 0-2 critical spells in it; countering the third ritual instead of Past in Flames is uniformly suboptimal.

3. **"Below pass_threshold" is a binary trap with no tap-out floor for proactive value.** Azorius G2 vs Storm at T3-T4 (g2t3d63/d64, g2t4d66/d67) — life [18, 16], 7-8 cards in hand, 2 lands, exactly ONE alternative enumerated (Isochron Scepter at −13.9 EV).  Counterspell, Solitude, Teferi, Supreme Verdict, Orim's Chant, Mystical Dispute all in hand but **not in the candidate list**. The AI passes 4 consecutive priority windows while Storm assembles 2 Medallions + Wish; Storm kills it on T4. Root: `ai/ev_player.py:620` `if best.ev < self.profile.pass_threshold: pass`. There is no "I have N untapped lands and no use for them next turn, so a proactive cast at negative EV is better than wasting the mana" rule. Pro control pilots tap out *all the time* when nothing on the stack and the opponent's clock allows.

### Top 3 things AI got right

1. **Drown in the Loch on Ocelot Pride T3** (audit_dimir_vs_boros, G1T3) — Bowmaster's previous trigger gave the GY pool to enable Drown for 2 mana, and the AI fired it on the right target (1-drop that would generate a Cat token).
2. **Wrath of the Skies T5 on 3-creature board** (audit_boros_vs_azorius, G1T5d15, EV 3.9) — correctly chose to sweep at 3-for-1. Sweeper timing was actually correct in this instance.
3. **Storm chain order (post-PiF)** — the Storm AI correctly cast PiF → Wish → Grapeshot in the right sequence once it was committed to going off; the storm-coverage math is working.

---

## Per-match findings

### Match 1 — Azorius Control vs Ruby Storm (seed 60100), Storm wins 2-0

#### Decision 1 — Game 1 T7-T8 main phase passivity (decision_id g1t7d28, g1t8d36, g1t8d37)

- **Symptom (deck-specific):** Azorius has Counterspell, Supreme Verdict, Solitude, Teferi (just died), Otawara in hand and 7+ lands. Storm has assembled 2 Medallions + Ral planeswalker on T6. T7 candidate list: `play_land/Hall of Storm Giants` (EV 7.0), `play_land/Meticulous Archive` (EV 7.0). Nothing else. T8: chosen = `Consult the Star Charts at EV −0.1` because that was the *only* spell scored.  Supreme Verdict, Solitude, Teferi NOT enumerated despite all being castable. Storm goes off T9 for lethal.
- **Mechanism (generic):** **`_score_spell` / `compute_play_ev` returns deferral-cost (negative) for control-finishers whose value materialises through future loyalty ticks or future trigger fires, because `evaluate_board(projected)` does not credit (a) planeswalker loyalty + ability projection, (b) Stock-Up-style "next 2 cards become 3 castable cards", (c) Wrath conditional on N+ creatures.** The deferral signals admit the card past gate 1 (`planeswalker_loyalty` fires), but the projection layer scores it ≤ 0 anyway. Any deck that wins games through cards-that-do-nothing-the-turn-they-resolve hits the same wall.
- **Class size:** Every Modern planeswalker (Teferi 3 + Teferi 5 + Jace + Liliana + Karn etc.), every cantrip refill (Stock Up, Consult the Star Charts, Memory Deluge, Brainstorm, Preordain), every conditional sweeper (Wrath of the Skies w/ X scale, Supreme Verdict). Easily 100+ Modern cards.
- **Subsystem owner:** `ai/ev_evaluator.py` (`compute_play_ev`, `evaluate_board`, projection of planeswalker loyalty into board value) — **NOT** `ai/ev_player.py`. The pass-threshold is operating correctly given the EV it receives; the EV is wrong.
- **Rule-phrased failing test:** `test_planeswalker_with_immediate_useful_ability_scores_positive` — a planeswalker whose entry-tick ability has a same-turn material effect (bounce, draw, +1 emblem with relevant clauses) must produce EV ≥ 0 when castable mana is available and the opponent's board does not lethal-kill the planeswalker on its first turn.
- **Lift-check:** Dimir Midrange's Liliana of the Veil; 4c Omnath's Wrenn and Six; Azorius Control's Teferi, Hero of Dominaria. Every planeswalker deck.

#### Decision 2 — Game 2 T4 Counterspell triage (decision_id g2t4d68-d80, audit_azorius_vs_storm_s60100.txt:903-994)

- **Symptom (deck-specific):** Storm casts Desperate Ritual (resolves), Pyretic Ritual (resolves), then Wrenn's Resolve (1R, +2 cards). Azorius taps UU on Wrenn's Resolve. Storm then chains Reckless Impulse → Wrenn's Resolve (no longer countered) → 2× Manamorphose+ritual → Past in Flames → Wish → 2× Grapeshot for 30+ storm copies. The chosen counter target was the *least valuable* card to counter — Wrenn's Resolve is a 4-of cantrip; Past in Flames and Wish are 1-2-ofs that fundamentally unblock the chain.
- **Mechanism (generic):** **`ai/response.py:_choose_response_targets` evaluates `evaluate_stack_threat(stack_item)` for the single spell on the stack with no model of "this spell is a chain link in a sequence whose payoff card hasn't been cast yet."** A Storm/Combo chain's value lives in the *chain*, not the individual link. The correct rule: when opponent's archetype is combo/storm AND a chain is mid-resolve (storm count ≥ 1 OR mana floated ≥ 2), the counter target preference must be `payoff_with_no_replacement_in_hand` (e.g. Past in Flames flashback grant, Wish/Tendrils-style closer) over chain-fuel cards. Both are oracle-detectable: payoffs have `storm` keyword or `each [spell]` oracle text; chain-fuel cards are `tag:cantrip` or `tag:ritual`.
- **Class size:** Every counterspell in the format vs every Storm/combo deck (Storm, Mardu Reanimator's Goryo cast, Living End's cycler chain, Cascade's spell, etc.). Per CLAUDE.md: when the AI has ONE counter vs an N-spell chain, it's strictly correct to hold for the bottleneck.
- **Subsystem owner:** `ai/response.py:42` `decide_response` + `ai/response.py:562` `evaluate_stack_threat`. The fix shape: chain-aware threat scoring. The threat value of the spell-on-stack must include `+P(this spell is the bottleneck that enables payoff this turn)` — derivable from BHI counter-probability of opponent's payoff cards + combo_calc reachability.
- **Rule-phrased failing test:** `test_held_counter_targets_chain_payoff_not_chain_fuel` — opponent (combo/storm archetype) has cast K rituals/cantrips this turn and now casts (a) a cantrip and (b) the storm-payoff in succession; the counter must fire on (b), not (a). Set-up uses tag predicates, no card names.
- **Lift-check:** Dimir Midrange's Counterspell vs Living End cycler-into-cascade; 4/5c Control's Force of Negation vs Cascade decks' Living End / Crashing Footfalls; Jeskai Blink's Subtlety vs Storm's PiF. Currently every counter-deck fires its counters on chain-fuel first.

#### Decision 3 — Game 1 T6 Otawara held; T9 Solitude shows the architecture

- **Symptom (deck-specific):** Otawara (channel: bounce a permanent for 3U) entered T4. Storm deployed Ruby Medallion T3, *second* Ruby Medallion T6, Ral planeswalker T6.  Otawara was channelled ZERO times across G1.  T9 Solitude on Storm permanents — no creature targets, evoke would force pitching a White card, hardcast cost 3WW, fired on Storm's empty board, useless. AI logged "cast_spell/Solitude EV 7.3" but Storm killed the next turn anyway.
- **Mechanism (generic):** **Permanent-bounce activated abilities (channel, sac-effects, "tap: return") are not evaluated as response-window plays for sorcery-speed permanents in opponent's main phase.**  The `decide_response` path only enumerates instants and flash; channel costs that produce instant effects are not surfaced.  A real control player sees a Medallion-fueled chain coming and channels Otawara on the *Medallion* to break the discount stack, even at 3U cost.
- **Class size:** Otawara, Soaring City; Boseiju, Who Endures; Mishra's Bauble's "sac" line; activated abilities on opponent's main phase generally.
- **Subsystem owner:** `ai/response.py` enumeration loop. `ai/ev_player.py` `_score_spell` for the channel ability.
- **Rule-phrased failing test:** `test_channel_ability_enumerated_in_response_window_when_target_present` — when opponent declares a spell that resolves into a high-value permanent, channel/sac abilities that can answer that permanent must appear in the response candidate list. Constructs by tag: oracle text contains "{cost}, Discard ... Channel" + activated ability `target permanent`.
- **Lift-check:** Dimir/Jeskai Otawara, Tron's Boseiju, 4c Omnath's Boseiju — every UWx pile in the format runs at least one channel land.

---

### Match 2 — Ruby Storm vs Dimir Midrange (seed 60101), Dimir wins 2-1

#### Decision 4 — Game 1 T3-T4 Dimir is the "control" deck and never plays defense

- **Symptom (deck-specific):** Dimir on the draw, deploys Bowmaster T2 (correct), Bowmaster T3 (creates 2 Orcs). Storm casts Ral T4 with mana left for Desperate Ritual + Reckless Impulse + Pyretic Ritual + Glimpse — Dimir has Counterspell + Archmage's Charm + Fatal Push in hand, never responds, never blocks anything. Storm's Reckless Impulse, Pyretic Ritual, Glimpse the Impossible all resolve with `counter_pct=0.0` — Dimir's reactive elements just sit there.  Game ends T4 Storm @ 19 → Dimir loses to Wish/Grapeshot. Wait — actually it was Storm being killed on T4! Re-read: at G1T4 the AI casts spells; eventually `P1 loses: life total 0` — Storm died at 0 life because Storm's life went 17→15→13→10 from Bowmasters and Storm did not have a winning chain in hand. Dimir won by *attacking with Bowmasters and Orcs*; Storm never won/cast a payoff. So Dimir's "passive" play here was actually correct — but the AI never recognized it had counters available to *protect* against a Storm chain coming on T5+.
- **Mechanism (generic):** Same as Decision 2 — counter triage. Dimir held Counterspell + Archmage's Charm through the whole game without firing, but more importantly, the AI never *committed* to "I am the control deck this matchup" — it kept playing on its own clock instead of clocking Storm. This is the **BHI matchup-role-assignment** gap: a midrange deck does not know whether to be the beatdown or the control deck based on the opponent's archetype.
- **Class size:** All midrange decks (Dimir, Jund, Mardu, etc.) — they flex role per matchup.
- **Subsystem owner:** `ai/gameplan.py` + `ai/strategy_profile.py` — the gameplan goal-engine must read opponent's archetype to switch from `grind_value` to `close_game` against combo opponents in pre-mulligan / early turns. Currently `Dimir Midrange` archetype just defaults to `grind_value → close_game` based on its own resource count, not opponent identity.
- **Rule-phrased failing test:** `test_midrange_vs_combo_role_flips_to_clock` — a midrange deck facing a combo archetype must shift `goal_engine.current_goal` to `clock_opponent` (or equivalent) by T2 if its opening hand has ≥1 efficient creature + ≥1 disruption.
- **Lift-check:** Mardu / Jund / Grixis Death's Shadow versions; every midrange deck. Also applies to Boros Energy vs Storm — Boros currently doesn't always race correctly.

#### Decision 5 — Game 3 T6 Dimir Counterspell on Reckless Impulse (audit_storm_vs_dimir_s60101.txt:965-1000)

- **Symptom (deck-specific):** Storm has Ruby Medallion in play, casts **Reckless Impulse** (1R, +2 cards). Dimir on T6 fires Counterspell. Storm proceeds: 3×rituals → Past in Flames → 2×Grapeshot for storm 5 → 6 → kills next turn. Dimir's counter was burned on a +2 cantrip; the actual payoff (Past in Flames) and the actual storm-enabler (Past in Flames flashback grant) resolved without disruption.
- **Mechanism (generic):** Identical to Decision 2 — counterspell fired on chain fuel, not on chain payoff.
- **Class size:** Same as Decision 2.
- **Subsystem owner:** `ai/response.py` chain-aware threat scoring. Same fix.
- **Rule-phrased failing test:** Same as Decision 2 (`test_held_counter_targets_chain_payoff_not_chain_fuel`). One test covers both occurrences.
- **Lift-check:** Same.

---

### Match 3 — Dimir Midrange vs Boros Energy (seed 60102), Boros wins 2-0

#### Decision 6 — Game 1 T3-T5 Dimir is at 14→9→4 with answers in hand, never casts a creature with counter-backup

- **Symptom (deck-specific):** Dimir at G1T5 is at 9 life facing Ragavan + Ajani + Cat token (5 power on board). Dimir hand: Psychic Frog, Dauthi Voidwalker, Thoughtseize, Counterspell (per board state shown). Dimir taps out for Psychic Frog. Boros responds with Thraben Charm in the upkeep, kills the Frog before it ever attacks. Dimir is at 4 life on T6, dies T6. **The Frog was the only line, but tapping out for it without counter-up cost the game.** Real Dimir player: cast Frog at end-of-Boros-turn with 1U up to Counterspell removal — or threat-fork: cast Voidwalker (3-power, BB, fits in 2 mana) + hold up Counterspell. Voidwalker also makes blocks possible.
- **Mechanism (generic):** **AI does not factor "this creature has counter-backup available if I don't tap out for it" into mana sequencing.** The `_score_spell` for Psychic Frog runs `compute_play_ev` which assumes the projection's `estimate_opponent_response` removal-probability factor is enough — but it doesn't model the *alternative*: cast Frog with mana up, which has lower P(removed) because counter is available. Decision g1t5d17 chose Frog at EV 0.0; the same Frog cast end-of-opp-turn would have been EV ≥ 5.0 because counter-backup eliminates the removal.
- **Class size:** Every threat with a CMC ≤ mana-available-minus-counter-cost; every counter-deck holding a creature. Affects Dimir Midrange, Jeskai Blink, Azorius Control, 4/5c Control whenever they deploy a threat.
- **Subsystem owner:** `ai/ev_player.py:_score_spell` mana sequencing layer + the turn_planner. The fix is in `ai/turn_planner.py` (5-ordering enumerator): one of the orderings should include "cast threat at end-of-opp-turn with X mana held back" as a planning option, not just "cast in main phase."
- **Rule-phrased failing test:** `test_threat_with_counter_backup_prefers_eot_cast_when_mana_permits` — control archetype with a Counterspell-class instant in hand + a creature whose CMC + counter-CMC ≤ open mana must rank end-of-opponent's-turn cast above main-phase cast.
- **Lift-check:** Jeskai Blink with Snapcaster + Counterspell sequencing; 4c Omnath with Solitude flash-in (the evoke alternative is already eot-castable). Currently all UWx decks tap out their threats in their own main phase rather than EOT.

---

### Match 4 — Boros Energy vs Azorius Control (seed 60103), Boros wins 2-0

#### Decision 7 — Game 1 T3-T4 Azorius passes priority with full hand vs developing Boros board

- **Symptom (deck-specific):** Boros plays Ajani T2 + Cat T2, Ajani T3 + Cat T3. Azorius has Counterspell, Teferi, Solitude, Prismatic Ending, Lórien Revealed, plus 2 lands → 3 lands → 4 lands. T3 fires Prismatic Ending on Ajani (correct, kills it before it can attack — well, the Cat was already out, so this is actually too late, but acceptable). T4: Azorius has Teferi castable. **Chosen: pass below_threshold. Alternative: cast_spell/Teferi at EV −5.7.** Boros next turn casts Phlage for 3 damage + gain 3, attacks for 5. Azorius dies T7.
- **Mechanism (generic):** Same as Decision 1 — Teferi scored at −5.7 because `compute_play_ev` doesn't credit the +1 (sorcery-as-flash, which DOES enable next-turn Verdict on aggro board), nor the −3 (bounce a creature + draw). Both abilities are immediately useful here.
- **Class size:** All planeswalker decks.
- **Subsystem owner:** `ai/ev_evaluator.py` `evaluate_board` + `compute_play_ev`. The planeswalker's `+1` and `-X` projected outcomes must be modelled as either (a) tick into expected loyalty pool with discounted future-turn ability fires, or (b) credit the immediate +1 ability's effect (Teferi's "sorcery as flash" enables otherwise-tapped-out turns to Verdict).
- **Rule-phrased failing test:** `test_planeswalker_with_immediate_useful_ability_scores_positive` (shared with Decision 1).
- **Lift-check:** All planeswalker decks.

#### Decision 8 — Game 1 T5 Wrath of the Skies at correct cost (positive)

- **Symptom (deck-specific):** Boros has Cat + Ajani + Cat on board. Azorius casts Wrath of the Skies with X=2, sweeps. EV=3.9.
- **Mechanism (generic):** When sweeper hits ≥3 creatures, the EV math works. This is a positive datapoint.
- **Class size:** N/A — just noting it.
- **Subsystem owner:** N/A.
- **Rule-phrased test:** existing (`test_sweeper_fires_at_3plus_creatures`). Confirmed green.

---

## Cross-match patterns

### Pattern A — "Pass below threshold" runs the game out

Across all 4 matches, the dominant Azorius/Dimir decision in main phases with mana available is `pass` because the enumerated alternatives are all negative-EV. This is the **structural** root cause: the AI's plan layer is correct (don't cast negative-EV spells); the EV layer is wrong (control finishers score negative because `evaluate_board` projection can't see their value).

Decisions g1t7d28, g1t8d36, g2t3d63/d64, g2t4d66/d67 (azorius_vs_storm), g1t3d7/d8, g1t4d13/d14, g1t5d18/d19, g1t6d22/d23 (dimir_vs_boros), g1t4d11/d12 (boros_vs_azorius) — all `pass below_threshold` with hand size ≥ 5 and ≥2 untapped lands.

A pro control pilot in those spots casts Teferi, casts Stock Up, casts Sheoldred, casts Liliana — *somebody*. They use their mana. The AI hoards.

### Pattern B — Counterspell triage burns on chain-fuel

Decisions 2 (g2t4d-counter in azorius_vs_storm) and 5 (storm_vs_dimir T6 counter) both burn a single counter on a chain-fuel cantrip while the actual payoff resolves later in the same turn. The fix is the same: chain-aware threat scoring in `ai/response.py`. This is the single highest-impact control-side fix in the audit.

### Pattern C — Channel/EOT activated abilities never fire reactively

Otawara held G1 across both Azorius matches. Boseiju, Mishra's Bauble, similar response-window activated abilities are not enumerated in `decide_response`. This is a smaller class but a real control-pilot habit ("channel Otawara on the Medallion in upkeep").

---

## Recommended fixes (ranked, all structural)

### Fix 1 — Planeswalker EV credit in `evaluate_board` (P0)

**Subsystem:** `ai/ev_evaluator.py` (`evaluate_board`, `_project_spell`)

**Mechanism:** A planeswalker's entry value = `+expected_loyalty_pool × per_loyalty_clock_impact + immediate_ability_outcome_value`.  Per-loyalty impact derives from `ai/clock.py` (each loyalty point ≈ one card's worth of clock pressure on opponent), no magic constant. Immediate ability outcome reuses the existing oracle parser used for instants (`oracle_signals_card_draw`, `is_immediate_interaction`, `bounce_predicate`).

**Failing test:** `tests/test_planeswalker_ev_positive_with_useful_immediate_ability.py` — a planeswalker template with `+1: Draw a card` and `-3: Return target permanent to its owner's hand` cast on T4 vs a 1-creature opponent board must produce EV ≥ 1.0 (clock-derived; not a magic threshold) when mana is available.

**Lift-check:** 4c Omnath (Wrenn and Six, Teferi Hero), Dimir Midrange (Liliana of the Veil), 4/5c Control (Teferi Hero), Azorius Control. Plus all Boros/Jeskai matchups where Ajani, Nacatl Pariah is currently scored fine but **other** planeswalkers (Ajani Hero, Ajani Goldmane) aren't.

### Fix 2 — Chain-aware threat scoring in `ai/response.py` (P0)

**Subsystem:** `ai/response.py:42` `decide_response`, `:562` `evaluate_stack_threat`

**Mechanism:** When opponent's archetype is `combo` or `storm` AND opponent has cast ≥1 chain-fuel spell this turn (oracle predicate: `'storm' in keywords OR 'cost_reducer' in tags ON BOARD OR ritual mana floated`), the threat score for a spell-on-stack is augmented by `P(this is the chain-bottleneck this turn)`. The bottleneck-probability is derivable from `ai/combo_calc.py` (already computes reachability) and BHI (opponent's hand inferred for payoff cards). No new constants; reuses combo_calc + BHI primitives.

**Failing test:** `tests/test_held_counter_targets_chain_payoff_not_chain_fuel.py` — opponent (storm archetype) casts a sequence of `tag:ritual` and `tag:cantrip` spells, then casts the `tag:storm_payoff` spell with no replacement in hand. The held Counterspell must fire on the payoff, not the chain-fuel.

**Lift-check:** Every counterspell deck in the format vs every combo deck. Currently affects ~32 of the 16×16 matchups directly.

### Fix 3 — Proactive tap-out floor when no defensive use for mana (P1)

**Subsystem:** `ai/ev_player.py:620` (`pass_threshold` gate)

**Mechanism:** If best non-pass play is in `[pass_threshold, 0)` AND opponent's clock allows the turn to be "spent" (`opp_clock_discrete ≥ 3`) AND there is no held-counter-class card in hand that needs the open mana (`_held_counter_floor_ev > 0` from `ai/response.py:375`), then the pass-threshold gate is relaxed by the held-mana-opportunity-cost (= sum of untapped mana × turn fraction remaining). This is the *negation* of `_holdback_penalty` (already exists at `ai/ev_player.py:1379`) — currently penalises tapping out when opp can respond; this fix awards tapping out when opp's clock won't matter.

**Failing test:** `tests/test_proactive_tap_out_when_clock_permits.py` — control archetype with a planeswalker in hand at 3 mana, opp board is empty, opp life is 18, opp_clock_discrete = 5; the AI must cast the planeswalker rather than pass, even at slight negative EV.

**Lift-check:** Affects every control deck on the early-turn deployment problem. Domain Zoo and Boros Energy on the *receiving* side will see the matrix shift (currently they get free turns to develop).

### Fix 4 — Channel / EOT activated abilities enumerated in response window (P2)

**Subsystem:** `ai/response.py` enumeration loop.

**Mechanism:** When iterating over candidate response actions for a stack item, include channel costs on lands in hand (`channel_cost` attribute on `LandTemplate`) and activated abilities on permanents in play whose effect targets a permanent (oracle predicate match: `'target' AND ('permanent' OR 'creature' OR 'artifact' OR 'enchantment' OR 'planeswalker')` AND the targeted permanent is on opponent's side).

**Failing test:** `tests/test_channel_ability_enumerated_when_target_present.py` — Otawara in hand + 4 mana + opponent has Ruby Medallion on battlefield → channel(Otawara) on Medallion must appear in candidate list when opponent passes priority.

**Lift-check:** Dimir's Otawara, Jeskai's Otawara, Tron's Boseiju, 4c Omnath's Boseiju.

### Fix 5 — Midrange role-flip vs combo opponents (P1)

**Subsystem:** `ai/gameplan.py` goal selection + `ai/strategy_profile.py`

**Mechanism:** A deck whose archetype is `midrange` AND whose opening goal would be `grind_value` should instead start at `clock_opponent` (or equivalent goal) when the opponent's deck archetype is `combo` or `storm` AND its own opening hand has ≥1 creature with `power ≥ 2` + ≥1 disruption spell (`tag:hand_attack OR tag:counterspell OR tag:graveyard_hate`). The role-flip is a property of the *matchup pair*, not the deck — implemented as a goal-selection predicate, not a per-deck override.

**Failing test:** `tests/test_midrange_role_flip_vs_combo.py` — Dimir Midrange first-turn `current_goal()` must equal `clock_opponent` when `opponent.archetype == 'combo'` and the opening hand satisfies the predicate above.

**Lift-check:** Every midrange deck (Dimir, Mardu Reanimator on the play, Death's Shadow variants if added).

---

## Unresolved — needs root-cause investigation

- **Why does the AI consider only 1-2 alternatives in candidate lists for control decks?** The Azorius T3-T4 G2 decisions enumerate exactly one alternative each — Isochron Scepter at -13.9 EV, never Counterspell (correctly held), but never Teferi, Solitude, Verdict, Orim's Chant. Either `_enumerate_legal_plays` is filtering these out at gate 1, or `_score_spell` is returning a "do not include" sentinel. Needs a trace of g2t3d63's enumeration. Hypothesis: planeswalker / 5-cost creature / sweeper are getting filtered by a "no signal" deferral gate that returns *empty signals* + skips entirely, rather than scoring at exposure cost. Distinct from Fix 1 (which fixes the EV) — this fix would be ensuring the cards *appear in candidates* even when EV is negative, so the user can see them in the alternatives list.

- **`assess_combo` interaction with Decision 4 (Dimir as control deck):** Dimir's gameplan currently treats Storm as "grind_value matchup" because Dimir's own creature count is 0 on T1-T2 — the goal engine isn't aware of the *opponent's* matchup-role assignment. The fix is Fix 5 above, but the trigger predicate (`opponent.archetype` access from the gameplan layer) requires plumbing in `ai/gameplan.py` that I haven't traced.

---

## Patches I refused to write — why these are symptoms, not causes

- **"When facing Storm, Azorius should auto-cast Teferi T3 if available."** — Card-name pair + deck-name gate. Symptom is real; the cause is the EV layer doesn't credit planeswalker loyalty (Fix 1). Patching the gameplan to force-cast Teferi T3 would help Azorius and break the next planeswalker deck.

- **"When Wrenn's Resolve is on the stack, do not counter."** — Card-name check. The cause is chain-bottleneck recognition (Fix 2). A per-card "do not counter" list is the canonical anti-pattern flagged in CLAUDE.md's hard prohibitions.

- **"When Ruby Medallion is on the battlefield, prefer to cast Otawara channel."** — Card-name gate on Storm-specific permanent. The cause is that channel abilities are not enumerated in response windows at all (Fix 4). Generic by tag, not by name.

- **"Storm should not chain past a Counterspell with one card in hand."** — A symptom of the chain-progression EV; root cause is already documented in the 2026-04-26 audit (F2.1 Wish hold-penalty in `combo_calc.py`). Out of scope for this control-focused audit.

- **"Dimir should always have UU up vs combo."** — Same as Fix 5 (role-flip) but card-name-shaped. Refused for the same reason.

---

## Reading order for next session

1. **This document — executive summary + Decisions 1, 2, 7** are the load-bearing findings.
2. **2026-04-26 Storm audit** — context for why combo_calc.py changes are touchy.
3. **`ai/ev_evaluator.py:2569` `compute_play_ev` and the projection layer** — Fix 1's surface area.
4. **`ai/response.py:42, 562` `decide_response`, `evaluate_stack_threat`** — Fix 2's surface area.
5. **`ai/ev_player.py:620` `pass_threshold` gate + `:1379` `_holdback_penalty`** — Fix 3's surface area.

## Verification protocol per fix

```bash
# Existing suite green
python -m pytest tests/ -q

# Azorius vs Storm field smoke (the loss being audited)
python run_meta.py --matchup "Azorius Control" "Ruby Storm" -n 30

# Dimir vs Boros field smoke
python run_meta.py --matchup "Dimir Midrange" "Boros Energy" -n 30

# Matrix gate (no deck regresses >5pp)
python run_meta.py --matrix -n 20
```

Per-fix expected directional shifts (audit's claim, to be validated):

- Fix 1 (planeswalker EV): Azorius +5-12pp vs aggro, +0-3pp vs combo. 4c Omnath +3-5pp. 4/5c Control +3-5pp.
- Fix 2 (chain-aware counter): Azorius +8-15pp vs Storm, Dimir +5-10pp vs Storm, Jeskai Blink +5-8pp vs Cascade/Living End.
- Fix 3 (proactive tap-out floor): Azorius +3-7pp across the field; small loss (0-3pp) on hyper-aggro because tapping out for a planeswalker against a 5-power board is sometimes wrong.
- Fix 4 (channel enumeration): +1-3pp on Azorius/Jeskai vs combo. Small.
- Fix 5 (midrange role-flip): Dimir +5-10pp vs Storm, Dimir +0-2pp elsewhere.

If actual matrix runs show <50% of the claimed shifts, the EV-projection model is more broken than the audit reads — escalate to a Phase 2c projection rewrite (out of scope here).
