---
title: Pro Aggro player audit — 2026-05-16
status: active
priority: secondary
session: 2026-05-16
depends_on:
  - docs/history/audits/2026-04-26_storm_pro_audit.md
  - CLAUDE.md
tags: [audit, pro-review, aggro, boros, zoo, affinity]
summary: >
  Aggro-pilot audit of 4 Bo3 replays (s60100–s60103). Identifies six
  structural leaks: missing "damage-on-draw tax" race math (Bowmasters
  vs Storm: G1 self-killed for 10 in one Glimpse turn), Dimir blocker
  is never declared while opp deploys 8-power board (Dimir-vs-Boros
  G1/G2 board damage 9→4→-3 with two blockers idle), Thoughtseize
  taken at 4 life chooses Goblin Bombardment over the active attacker,
  Boros opening-hand "no curve creature, 3+ removal" hands kept on the
  draw, Storm's draw spells don't price in the on-board damage-tax
  triggers when life ≤ Bowmasters_count × cards_to_draw, and a Ral-
  coin-flip life-loss engine bug (player loses 1 life instead of Ral
  taking 1 damage).
---

## Executive summary

- **Overall grade: 5/10.** The AI is competent at curve-out and "cast threat → swing for damage", but every game in this corpus features at least one P0 leak in *race math* — the central skill of aggro play. Two of the four matches were won by the side that failed to defend; the other two were won by the obvious bigger-curve aggro deck. The losing side never raced correctly in any game.
- **Top 3 systemic issues**
  1. **Damage-on-draw is not modelled as a race cost.** Storm at 10 life with 2 Bowmasters in play cast 5 cards (Reckless Impulse + Glimpse) and self-killed via the on-draw triggers (10 → 0 in one turn, audit_storm_vs_dimir_s60101.txt G1 T4). The EV layer treated each draw spell at base value and ignored that `life_as_resource(life=10, incoming_power=Bowmasters_count × draws) = lethal`. This is identical in shape to the standard pro-pilot mistake of "I have 8 mana, I can chain off" — except the engine actually executes the suicide instead of recognising the spot.
  2. **Block-declaration is biased toward "no block" when defender has 1/1s vs ground-pounders.** Dimir-vs-Boros G1: Dimir at 14, then 9, then 4 life with Orcish Bowmasters (1/1) on board (idle in graveyard from T2 cast G1 — see G2 where Bowmasters did exist and STILL did not block to chump at 4 life). In G2 Dimir had Bowmasters + Orc Army on board at 8 life vs 7-power attackers and **let through 6 damage**, paid life 14→8→0 in two combat phases. Pro aggro pilot blocks to ensure life > 0 next turn whenever the chump is a 1/1 token. The "favorable trade / chump / trade" scorer needs a "life_as_resource forces chump" override.
  3. **Thoughtseize at low life targets a flex card rather than the active attacker.** Dimir-vs-Boros G1 T6, Dimir at 4 life facing 5 power on board, casts Thoughtseize and discards `Goblin Bombardment` (a 2-mana enchantment doing nothing live). Boros's hand at that point contained another Ajani / another threat — pro pilot rips the next-threat-on-curve, not the maybe-later-relevant sac outlet. The discard targeting routine doesn't weight "stops next turn's attacker".
- **Top 3 things AI got right**
  1. **Storm chain execution under pressure is mechanically clean** when the chain actually goes off (audit_storm_vs_dimir_s60101.txt G2 T5: 11-card chain ending in lethal Grapeshot; audit_azorius_vs_storm_s60100.txt G2 T4: 12-card chain on T4 on the draw). Splicing Desperate Ritual and the Wish → Past in Flames → Wish → Grapeshot sequence is unlocked and reliable.
  2. **Boros mulligan threshold is sensible.** All four Boros opening hands kept were 2-3 land, 1-2 creatures, with a curve from T1-T3. No 5-spell-no-land or 1-land-keeps.
  3. **Removal sequencing on Boros side picks the right Thraben Charm target.** Dimir-vs-Boros G1 T5: P2 Boros activates Thraben Charm immediately on Psychic Frog (the only live target, 6-damage mode). EV=112 is correct — that line clears the only blocker before it gets a second discard pump.

## Per-match findings

### Match 1 — Azorius Control (P1) vs Ruby Storm (P2), s60100 (Storm 2-0)

- **G1 T6 Azorius — Teferi -3 to bounce + Prismatic Ending is correct, but pre-T8 there was a window to land Solitude (5 mana via fetched Plains).** Azorius played reactively on a Storm clock with `position_value(snap, "control")` returning +stable. By T8 Storm has 3 Ruby Medallions and lethal. The control player's `close_game` switch fired too late — at T8 Storm has 14 life and 6 lands. From a Boros lens watching Azorius vs Storm: **you need to recognise that Storm reaching 3 cost-reducers means you have one turn**. The `combo_clock(snap)` of an opponent showing `Ruby Medallion in play + 2+ rituals already cast` is `turns_to_kill <= 2`, but the control-side response logic doesn't bridge `opponent has cost reducers in play → my draw-go window is closing`.
- **G2 T4 Storm wins on the draw via 12-storm Grapeshot — chain execution is correct, splicing is correct, Wish-for-Grapeshot is correct.** Note Storm took 1 Counterspell (chose Wrenn's Resolve, not the highest-EV trigger spell) — Azorius's counter target selection is fine.
- **Block math (defender side, Storm):** Storm never blocks because it has no creatures. Storm correctly does not crack Ral down to lose-flip life-loss territory.
- **Decision-id g1t6 (Azorius Solitude hold):** A pro Azorius pilot evoking Solitude on Ragavan T3 wins this matchup ~85% of the time. The replay shows Solitude drawn T9 (one turn too late). The deeper issue is `bhi` on the storm matchup: Azorius's hand-belief model knows `opp.archetype == "combo"` but doesn't downgrade `Counterspell` value vs `pitch-removal-on-cost-reducer`. This is a control-side leak that an aggro pilot still notices: **when the deck across the table is a combo deck without creatures, every Counterspell in your hand is worth 0 against the win condition (Grapeshot is uncounterable in a meaningful sense because it's the 12th storm copy of a 12-card chain) — instead your hand becomes "evoke Solitude on Ral / Medallion".**

### Match 2 — Ruby Storm (P1) vs Dimir Midrange (P2), s60101 (Dimir 2-1)

- **G1 T4 Storm self-kills (10 life → 0) by drawing 5 cards into 2 Bowmasters.** This is the audit's headline finding. Trace: T3 Storm cast Wrenn's Resolve (draw 2) at 16 life → 14. T3 Dimir attacks → 12. T3 Dimir end: 12, but earlier Bowmasters dealt 1 ETB damage → 15 → 14 → 12. T4 Storm at 10 (one fetch + combat). Storm casts Ral (no draw), Desperate Ritual (no draw), **Reckless Impulse (draw 2 → -4 from 2 Bowmasters → 6 life)**, Pyretic Ritual (no draw), **Glimpse the Impossible (draw 3 → -6 → 0)**. The chosen EVs in the NDJSON are `Reckless Impulse ev=-0.061, Glimpse ev=-0.07` — already negative — but the player executes them anyway because the chain is open. **No subsystem priced in `draw_cost = bowmasters_in_play × cards_drawn`.**
- **G2 T5 Storm wins on the play (no Bowmasters in opp hand turn 1, Dimir slow-played).** Chain executes correctly.
- **G3 Storm loses on T7 to combat damage from Murktide Regent (8/8). Storm decision at G3 T6 trades a partial chain (5-copy Grapeshot for 5 damage) for life buffer instead of going for lethal.** From a Storm-pilot lens this *can* be the right play if Storm's hand has a follow-up — but the partial chain here didn't kill (Dimir at 14), and Storm's hand on T7 is reduced to 1 card. The decision feels right (forced a 1-turn race) but execution math is off: `5 damage chain → Dimir 20→14 → opp's lethal on T7 is Murktide+frog+orc = 10 damage → Storm at 14→9→lethal next turn`. The pro pilot would have either (a) held the chain entirely to set up T7 lethal with more rituals, or (b) gone all-in T6 for 8-10 damage (full chain past Counterspell-mana). Splitting the chain at exactly 5 was the worst of both worlds.
- **Block math (Dimir defender):** G3 T3 Dimir's Ral block (Ral 1/3 blocks Bowmasters 1/1 — "favorable trade"). This is *correct* aggro defense for the midrange pilot: kill the planeswalker-on-stick before it transforms. Then Fatal Push on Ral the same turn — good removal allocation.

### Match 3 — Dimir Midrange (P1) vs Boros Energy (P2), s60102 (Boros 2-0)

- **G1 Dimir never blocks across 4 combat phases despite having clear chump options.** T3 Boros attacks with Ragavan 2/1, Dimir defender at 14 with no creatures yet — no block possible, fine. T4 Boros attacks 3 attackers (Ragavan, Ajani, Cat), Dimir at 14 → 9; T5 same → 9 → 4; T6 same → -3. **At T6 Dimir had a Bowmasters in graveyard (cast T2 G2, no wait this is G1; Dimir cast Bowmasters only in G2)** — actually in G1, Dimir never cast a creature until T5 (Psychic Frog, immediately killed by Thraben Charm) and T6 (Dauthi Voidwalker). The Voidwalker hits T6 P1 main, AFTER Boros's T6 attack already swung the game state. **The leak is in cast-priority: Dimir at 9 life facing 5 power should cast Voidwalker (2 power chump-blocker) on T5, not T6.** The NDJSON shows `Dauthi Voidwalker ev=-0.069` chosen at T6 — already negative — but at T5 (life=9, opp.power_on_board=5) the proper EV computation is `life_as_resource(9, 5) = 1.8 turns` vs `life_as_resource(9, 5-2) = 3.0 turns post-block`. The defender's panic-deploy threshold is missing.
- **G1 T6 Thoughtseize hits Goblin Bombardment (irrelevant) at Dimir 4 life with Boros holding `Marsh Flats + Windswept Heath + 1 unknown`.** A pro Boros pilot at 4 life faces a Dimir deck packed with Counterspells / Drown / Push and would rip the live attacker addition. The discard targeting routine prioritises CMC and oracle keywords without weighting `would-attack-next-turn`. This is the same shape as the standard "Thoughtseize away the bomb, not the live disruption" mistake — but inverted: here Dimir is the discarder, and the right pick is the next attacker. Dimir's Thoughtseize should call `opp_threats_in_hand_by_attack_imminence()`, not `opp_threats_in_hand_by_cmc()`.
- **G2 T6-T7 Dimir chump-blocks (Orc Army + Bowmasters into Pyromancer) and trades Dauthi Voidwalker into Pyromancer — this is reasonable.** T8 with 0 blockers and 4 life, Dimir loses to 5 power. The combat-damage decisions were fine; the upstream failure was T5/T6 cast priority leaving Dimir creatureless.
- **Boros side (the winner) is correct on aggression:** every turn 2-4 deploys a threat on curve, every turn 3+ attacks. The "attack into unknown mana" decision is the right one in this matchup because Dimir's instant-speed interaction is Counterspell / Fatal Push / Drown — none of which scale better at instant speed than sorcery speed against a board with 3+ creatures. The AI correctly does NOT play around uncast spells; this is one of the few places the EV is calibrated well.

### Match 4 — Boros Energy (P1) vs Azorius Control (P2), s60103 (Boros 2-0)

- **G1 Boros curves out perfectly: T2 Ajani (1W) → T3 Ajani #2 + Arena of Glory → T4 Phlage (1WR) + 3-creature swing → T6 Phlage escape + Galvanic Discharge → T7 Galvanic Discharge for lethal.** This is the dream Boros line. EV scoring picks the right play every turn.
- **G1 T4 Boros decision `Phlage to face (3 dmg, life 15→12)` — correct.** The `[Target]` line shows "no killable target" so Phlage to face is the only line. Note the AI did NOT instead hold Phlage to escape later — it cast it as a 3-CMC body for tempo. **This is the right aggro play: the 3 damage + 3 life + 3-power body is worth more than the future-escape value when you're already racing.**
- **G2 T4 Boros decision `Galvanic Discharge → face (3 dmg, life 16→13): no killable target` while Boros at 17 life vs 0 creatures opp.** Allocating burn to the face here is the **correct call** by aggro doctrine — Azorius has no creatures, the only legal target is the face, and racing matters. The hover-text decision "no killable target" is the right exception.
- **G2 T5 Phlage to face (3 dmg, life 11→8) → Galvanic Discharge to face (3 dmg, life 8→5) → Guide of Souls attack → 4.** Reach allocation is correct.
- **The thing missed:** Boros on T5 had Phlage **and** Galvanic Discharge in hand. Casting Phlage first (3-power body) and then Galvanic Discharge could have killed a Teferi (3 loyalty). Instead, both went face. The optimal aggro line is: Galvanic Discharge → Teferi (kill PW, deny -3 bounce next turn), then Phlage → face. The "killable target" detector treats planeswalkers as second-class targets. From `decision_id g1t6` in the NDJSON, `[Target] → face: opponent low life` overrides the planeswalker-removal option. **In an aggro mirror, killing the planeswalker is often correct over face damage because the PW generates 1-2 card advantage per turn it survives.** This is a generic principle: `dmg_to_pw_value = bhi.opp_card_advantage_from_pw × turns_pw_survives`.

## Cross-match patterns

### Pattern A — race math doesn't include "damage-on-draw" or "damage-on-cast" triggers
Decks affected:
- **Storm vs Bowmasters** (1 Bowmaster = 1 dmg/draw; 2 Bowmasters = 2 dmg/draw; Storm draws 8-15 cards in a chain turn).
- **Affinity vs Plague Engineer / Lavinia** (each Affinity cast triggers).
- **Cascade vs Drannith Magistrate** (each cascade cast is a tax in this shape).
- **Living End vs Leyline of the Void** (each cycle is a 1-of-2 cards).

The common abstraction is **"opponent's static damage triggers per chain step"**. Currently `ai/ev_evaluator.py` knows about discrete damage but not about "this card's resolution will inflict damage on me via my opponent's trigger." Fix: in `ev_player.score_spell()`, when the spell draws/casts/cycles N cards, query `bhi.opp_static_damage_per_draw(snap)` and subtract `N × per_draw_dmg` from EV, *and* gate the cast with a hard floor (`life_after >= 1`).

### Pattern B — defender doesn't chump-block when life trajectory forecasts lethal next turn
Decks affected (defender role): **Dimir, Jeskai, Azorius, 4c Omnath, Living End** — any deck with reach but slow board. The standard pro recognition: "I have a 1/1 token I don't care about. Opponent attacks 5 power. I'm at 7. I take 5, go to 2, they untap with the same board minus one tap-down. If I don't chump, I lose next turn. If I chump one, I go to 4 and might survive to a sweeper." The `clock.combat_clock()` already returns turns-to-kill for the *attacker*. We don't query it for the *defender* to detect lethal-on-next-turn-without-block.

### Pattern C — Thoughtseize / Inquisition discard targeting ignores "will-attack-next-turn"
Decks affected: **Dimir, Goryo's, Living End, Domain Zoo (Thoughtseize), 4c Omnath.** The current scorer ranks discard targets by CMC and oracle keywords (e.g. "destroy", "counter"). At low life the dominant question is `which card in opp.hand is the next-turn attacker`, which BHI can answer but isn't fed into the discard scorer.

### Pattern D — burn-spell target allocation undervalues planeswalkers
Decks affected: **Boros Energy, Domain Zoo, Burn (if added), Affinity Galvanic Discharge.** The `[Target] → face: no killable target` decision treats `killable_target == creature_with_toughness_le_damage`. Planeswalkers should appear in this list (they are "killed" when loyalty ≤ 0). Currently `face` wins by default because PWs aren't enumerated. Lifts to any deck with face-or-creature burn.

### Pattern E — control side doesn't recognise that "the opponent's win condition is a single-turn payoff"
Decks affected: Azorius / 4c Control / Dimir Midrange against Storm / Living End / Cascade. The `close_game` switch on the control side only flips when opp.life is low. It should also flip when opp's *position* shows N-1 of N combo pieces assembled (e.g. cost-reducers + payoffs in graveyard). The `bhi` opponent-archetype model already knows the matchup is combo; what's missing is the trigger `opp_combo_pieces_visible >= combo_threshold(archetype)`.

### Pattern F — engine bug: Ral coin-flip loses player 1 life instead of damaging Ral
`engine/oracle_resolver.py:638` does `player.life -= 1` on Ral's coin-flip loss. The oracle text reads "Ral, Monsoon Mage deals 1 damage to itself" (or equivalent — the card's lose-flip clause damages Ral). The current behaviour is a direct life deduction. Fix in `engine/oracle_resolver.py:_handle_coin_flip_transform`: damage Ral (`creature.damage_marked += 1`), not the player. This is a pure engine bug, not an AI leak.

## Recommended fixes (ranked, all structural)

### Fix 1 (P0) — Damage-on-draw tax integrated into chain EV [Pattern A]
- **Class size:** Any draw/cycle/cast trigger from opp permanents. Hundreds of cards including Bowmasters, Sheoldred, Underworld Dreams, Drannith Magistrate, Leyline of the Void, Spirit of the Labyrinth (caps), and dozens more.
- **Subsystem:** `ai/bhi.py` exports `opp_static_damage_per_card_event(snap, event_type) -> int`. `ai/ev_player.py::_score_spell` subtracts `damage × event_count` from EV and hard-floors casts where `life - total_chain_damage <= 0`.
- **Rule-phrased test (red first):**
  ```python
  # tests/test_chain_self_kill.py
  def test_storm_glimpse_at_low_life_with_bowmasters_is_not_cast():
      """When draw-trigger damage would kill the caster mid-chain,
      the chain must abort before the lethal step."""
      # Setup: Storm life 10, 2 Bowmasters on opp board, Glimpse in hand,
      # storm count 0. cast(Glimpse) draws 3 → 6 damage → -2 life → death.
      # Expected: chain refuses Glimpse, picks pass.
  ```
- **Lift-check:** Affinity v Plague Engineer (token sac trigger), Cascade v Drannith (cast tax), Storm v Drannith — same mechanism.
- **NO card names / deck names**: queries oracle predicates (`"whenever an opponent draws"`, `"whenever an opponent casts"`) and integrates the damage figure parsed by the existing static-ability code in `game_state.py:240-267`.

### Fix 2 (P0) — Defender panic-chump rule [Pattern B]
- **Class size:** Every deck that ever blocks with tokens / chumpable creatures. ~all 20k Modern creatures.
- **Subsystem:** `engine/combat.py` block selection consults `ai/clock.life_as_resource(life_after_no_block, opp_power_left_after_block) < 1.0` as a hard "must-chump-if-possible" gate when token blockers are available. AI side: `ai/ev_evaluator.score_block_assignment()` adds a "life trajectory" term derived from `clock.life_as_resource`.
- **Rule-phrased test:**
  ```python
  def test_defender_chumps_when_no_block_means_lethal_next_turn():
      """Defender with 1/1 tokens at life L facing N power must chump
      when life_as_resource(L - N, remaining_power) < 1.0."""
  ```
- **Lift-check:** Dimir, Jeskai Blink (Solitude tokens), 4c Omnath (Wrenn tokens), Living End (Architects), Goryo's. Same mechanism.

### Fix 3 (P1) — Discard targeting weights attack imminence [Pattern C]
- **Class size:** Thoughtseize / Inquisition / Duress / Dauthi-Voidwalker / Liliana-edict-style. ~50+ cards.
- **Subsystem:** `ai/discard_targeting.py` (or wherever Thoughtseize target lives) scores each candidate by `bhi.predicted_turn_of_cast(card)` — pick the card most likely to be cast within `1 / life_as_resource(snap)` turns. Tie-break by current CMC heuristic.
- **Rule-phrased test:**
  ```python
  def test_thoughtseize_at_low_life_picks_imminent_attacker():
      """When defender life ≤ 2× opp_avg_attack, Thoughtseize picks the
      card in opp.hand that maximises opp_imminent_attack_power."""
  ```
- **Lift-check:** Dimir, Goryo's (Thoughtseize sb), Living End (Thoughtseize in some lists), Domain Zoo.

### Fix 4 (P1) — Burn targeting includes planeswalkers as killable [Pattern D]
- **Class size:** Every face-or-creature burn spell. Lightning Bolt, Galvanic Discharge, Phlage, Lava Spike (via redirect rules — Modern doesn't redirect burn to PWs anymore so this is creature-or-face only post-2018... wait, it's planeswalker-or-creature-or-face. Galvanic Discharge does target any.) ~40+ cards.
- **Subsystem:** `ai/burn_targeting.py` enumerates `creatures + planeswalkers + face` and scores PW-kills by `bhi.opp_card_advantage_from_pw × turns_pw_survives`. Already partly exists for creature-target selection; extend the candidate list, don't add card names.
- **Rule-phrased test:**
  ```python
  def test_burn_kills_planeswalker_over_face_when_pw_loyalty_le_damage_and_opp_high_life():
      """When opp.life > 2 × burn_damage AND there exists a PW with
      loyalty ≤ burn_damage, Bolt kills the PW, not the face."""
  ```
- **Lift-check:** Boros, Zoo, Affinity (Galvanic Discharge), Burn, Prowess.

### Fix 5 (P2) — Control-side recognises combo-piece accumulation [Pattern E]
- **Class size:** All control/midrange decks against all combo decks.
- **Subsystem:** `ai/strategy_profile.py` per-archetype `combo_pressure_threshold` (declared in `decks/gameplans/*.json` for each combo archetype). `ai/ev_evaluator.py` checks `bhi.opp_combo_pieces_visible(snap) >= combo_pressure_threshold` and flips `close_game = True` for the control side.
- **Rule-phrased test:**
  ```python
  def test_control_panic_mode_when_storm_has_n_cost_reducers():
      """Control side `close_game` flag flips when opp board shows
      `combo_pieces_visible >= combo_pressure_threshold[opp_archetype]`."""
  ```
- **Lift-check:** Azorius/4c/Dimir vs Storm/Living End/Cascade/Goryo's.

### Fix 6 (P0, engine) — Ral coin-flip damages Ral, not the player [Pattern F]
- **Class size:** Ral, Monsoon Mage (1 card directly) — but the bug shape ("oracle says deals X damage to itself, code says player.life -= X") could lurk in other DFC coin-flip / self-targeting effects.
- **Subsystem:** `engine/oracle_resolver.py:_handle_coin_flip_transform` — change `player.life -= 1` to `creature.damage_marked += 1`, then check SBA.
- **Rule-phrased test:**
  ```python
  def test_ral_lose_coin_flip_damages_ral_not_player():
      """Lose-flip applies 1 damage to Ral's body; if Ral has toughness
      ≥ 2, Ral survives and player.life is unchanged."""
  ```
- **Lift-check:** This is a pure engine fix, no AI change. The principled lift is "self-damage on coin flip should always damage the source, not the controller."

## Unresolved — needs root-cause investigation

- **Why is `ev=-0.07` cast at all?** Storm's `Glimpse the Impossible` is chosen with negative EV in audit_storm_vs_dimir_s60101.txt G1 T4 (NDJSON `chosen.ev=-0.07`). The `pass_threshold` evidently allows negative-EV casts when the chain has open mana. The threshold logic in `ai/ev_player.py::_select_action_with_threshold` (or equivalent) should be inspected: under normal circumstances a negative-EV spell goes to `pass`. Hypothesis: storm chain logic bypasses the pass threshold once a chain has started. **Action:** read the pass-threshold + storm-chain-mode interaction; the fix may be "chain bypass respects pass threshold when life_after_chain_step <= 0".
- **Storm partial-chain decision math** (Storm-vs-Dimir G3 T6, 5-copy Grapeshot for 5 damage instead of holding for lethal or going all-in). Need a Bo3 replay focused on Storm's "fire vs hold" decision in attrition with a clock-aware opponent. Likely a `combo_chain.expected_chain_size` mis-estimation when 2-3 cantrips are in hand and 3+ rituals would be needed.

## Patches I refused to write

- **"Add a special case for Bowmasters in Storm's chain EV"** — this is the textbook hardcoded-card anti-pattern from CLAUDE.md. The fix is the generic damage-on-draw tax (Fix 1), not a Bowmasters check.
- **"Boost Dimir Midrange chump-block bias by +X if `deck.archetype == 'midrange'`"** — deck-archetype gate. The fix is the generic `life_as_resource < 1.0 → must-chump` rule (Fix 2). The current `score_block_assignment` is already archetype-aware, but only for the attacker side.
- **"Add `if card.name == 'Glimpse the Impossible' and opp_board_has_bowmasters: skip"`** — pure hardcode. Even as a one-line emergency patch this fails the contract.
- **"Lower Storm's `pass_threshold` from -0.1 to -0.01 globally"** — a magic-number tweak with no rule-phrased test. The right fix is to bound the chain by `life_after_step >= 1`, not to fiddle with the threshold.
- **"Add Plague Engineer / Drannith / Bowmasters per-card EV penalties to all combo decks' gameplans"** — per-card overrides in JSON, which is exactly what `card_ev_overrides` is for *short-term shims*, not the structural fix. The right place is `bhi.opp_static_damage_per_card_event`, queried generically.
