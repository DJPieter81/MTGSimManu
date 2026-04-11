# LLM-Judge Strategy Audit Report

**Date:** 2026-04-11
**Sample:** ~168 games (8 trace matchups + 6 deck audits x28 games + 2 Bo3 matches + 15x15 matrix)
**Method:** 6-expert parallel panel, each reviewing different strategic domains
**Overall Grade: D+**

---

## Panel Summary

| Expert | Domain | Grade | Key Finding |
|--------|--------|-------|-------------|
| 1 | Mulligan & Openers | **C+** | Living End 46% mull rate too aggressive; aggro passes T1 with 1-drops |
| 2 | Mana & Sequencing | **D+** | Removal projection makes all cheap creatures negative EV; burn face on empty board |
| 3 | Combat & Threats | **C+** | Fatal Push mis-targets (battlefield vs stack); first strike missing from AI combat sim |
| 4 | Combo & Storm | **D+** | Storm never fires finisher; Goryo's combo completely non-functional |
| 5 | Control & Interaction | **D+** | Chalice hardcoded X=1; holdback broken vs non-creature decks |
| 6 | Rules & Engine | **B-** | Living End ETB triggers missing; ghost candidates in EV list |

---

## CRITICAL ISSUES (P0 — Game-Breaking)

### 1. Removal Projection Suppresses All Creature Deployment
**Experts 2, 3** | Files: `ai/ev_evaluator.py:539-572`

The `estimate_opponent_response` function penalizes creatures by removal probability so heavily that 1-drops and 0-cost creatures get negative EV. Guide of Souls = -7.6, Memnite = -7.4, Dragon's Rage Channeler = -5.1. Aggro decks pass T1-T3 with playable creatures in hand. Domain Zoo plays ONE spell in 13 turns.

**Impact:** Aggro archetypes are fundamentally broken. Boros's 75% WR is carried entirely by Phlage and burn, not creature beatdown.

### 2. Storm Never Fires the Finisher
**Expert 4** | Files: `ai/ev_player.py:393-484`

Past in Flames scores -5.8 EV even with 7 mana floating, 2 Ruby Medallions, and 9 spells in GY. The penalty formula (`gy_fuel / opp_life * 15.0`) makes PiF nearly uncastable. Grapeshot at 0.9x cast rate means the storm kill only works ~50% of games. Avg loss turn (T6.9) < avg win turn (T7.6) — Storm dies before going off.

**Impact:** Ruby Storm at 39% WR, should be 50%+.

### 3. Goryo's Vengeance Combo Is Non-Functional
**Expert 4** | Files: `ai/ev_player.py` combo scoring, `engine/card_effects.py` discard logic

AI holds 3x Griselbrand + Goryo's Vengeance for 13 turns and never reanimates. Root cause: the discard logic (Faithful Mending) never bins Griselbrand — it discards Solitude instead. With no Griselbrand in GY, Goryo's has no target. The combo never executes.

**Impact:** Goryo's Vengeance at 34% WR is winning via Solitude/Persist beats, not its intended combo.

### 4. Living End Missing ETB Triggers
**Expert 6** | File: `engine/game_state.py` `_resolve_living_end()` ~line 1710

Creatures returned by Living End use `enter_battlefield()` + `battlefield.append()` but skip `_handle_permanent_etb()`. Compare with `_blink_permanent()` which correctly calls ETB. This means Architects of Will, Subtlety, etc. get no ETB abilities on return.

**Impact:** Living End is weaker than it should be. 43% WR with 46% mulligan rate.

### 5. Chalice of the Void Hardcoded to X=1
**Expert 5** | File: `engine/game_state.py:1349-1351`

Chalice is hardcoded to X=1 regardless of opponent. Against Storm (CMC 2 rituals), Tron (CMC 3+), or Affinity (CMC 0), it should adapt. Worse: X=1 locks Azorius out of its OWN 1-CMC spells.

**Impact:** Chalice has -0.76 win delta and 12% WinCR for Azorius Control. The card is actively losing games.

---

## HIGH ISSUES (P1 — Significant Strategy Errors)

### 6. Wrath Cast on Empty Board
**Experts 2, 5, 6** | Files: `ai/ev_evaluator.py:272-279`, `ai/strategy_profile.py:94`

Azorius casts Wrath of the Skies T3 with 0 creatures on either side (EV=-0.1). The projection math produces near-zero delta when board is empty, and the -5.0 pass threshold lets it through. Board wipes with no targets should be hard-gated.

### 7. Burn to Face on Empty Board
**Experts 2, 6** | Files: `engine/card_effects.py:507-524`, `ai/strategy_profile.py:102`

Galvanic Discharge and Lightning Bolt fire at face T1-T2 with no creatures anywhere. `burn_face_mult=1.5` for aggro creates positive EV for face burn regardless of board state. Burn should require board presence or lethal reach.

### 8. Fatal Push Mis-Targets on Response
**Expert 3** | File: `ai/response.py:156-169`

When Fatal Push responds to an opponent casting a creature, `pick_removal_target_fn` targets the highest-value creature already on the battlefield — NOT the incoming threat on the stack. In Bo3, Dimir responds to Ragavan with Fatal Push but kills Ajani instead.

### 9. Holdback Broken vs Non-Creature Decks
**Expert 5** | File: `ai/ev_player.py:337-349`

Mana holdback only fires when `opp_power > 0`. Against Storm (0 creature power during setup), control taps out freely even with Counterspell in hand. Control needs holdback based on opponent threats, not just board power.

### 10. First Strike Missing from AI Combat Evaluation
**Expert 3** | File: `ai/turn_planner.py:398-478`

`_simulate_combat` applies all damage simultaneously — no first-strike step. The engine handles it correctly, but the AI's evaluation misjudges trades involving first-strike creatures.

---

## MEDIUM ISSUES (P2 — Suboptimal Play)

### 11. Living End Mulligan Too Aggressive
**Expert 1** | File: `ai/mulligan.py:60`

`mulligan_combo_sets` requires BOTH cascade spell + cycler, only relaxing at 5 cards. Real LE pilots keep 6-card hands with just cyclers + lands. Should relax at 6 cards.

### 12. Tron Lands Not Differentiated
**Expert 2** | File: `ai/ev_player.py:540-616`

All Tron lands score identically (+19.0). No bonus for completing the Tron set. Missing Tron assembly logic.

### 13. Empty the Warrens Underutilized
**Expert 4** | File: `engine/card_effects.py:881+`

EtW at 0.3x cast rate is nearly dead. Wish tutor strongly prefers Grapeshot. On T5-6 with storm=5, EtW makes 12 Goblins — often lethal. Threshold is too Grapeshot-biased.

### 14. Dovin's Veto 0% WinCR
**Expert 5**

Cast 4 times in 28 games, always in losses. The card is dead weight in Azorius.

### 15. Ghost Candidates in EV List
**Expert 6**

After a card is cast, the next EV evaluation still shows it as a candidate. The snapshot isn't recalculated after state changes within the same main phase.

---

## ENGINE / LOGGING ISSUES

### 16. Duplicate EV Blocks (Cosmetic)
**Expert 6**

Every turn shows identical EV scores 2-3x. This is Main1 + Main2 evaluation with no state change between them. Fix: add phase labels to trace output.

### 17. Silent Exception in Response Logic
**Expert 5** | File: `ai/response.py:109`

`except Exception: pass` swallows TurnPlanner failures, causing counter decisions to fall through to a weaker legacy path without logging.

---

## WHAT'S WORKING WELL

- **Fetch land prioritization** — correctly scored above basics/taplands
- **Cascade mechanics** — Living End cascade resolves correctly, finds the right spell
- **Storm copy counting** — CR 702.40a compliant
- **Counterspell targeting restrictions** — noncreature/instant-sorcery gates work
- **Turn structure** — Untap/Upkeep/Draw/Main/Combat/Main/End correct
- **Legend rule** — SBA 704.5j enforced
- **Orcish Bowmasters ETB** — deals 1 damage + creates Orc Army
- **Phlage ETB** — 3 damage + 3 life correctly
- **Ritual mana tracking** — correct through storm chains
- **Land-before-spell sequencing** — consistent across archetypes
- **Dimir Midrange strategy** — 57% WR, Bowmasters/Murktide deployment is sound
- **Living End cycling priority** — correctly cycles creatures before cascading
- **Cards-to-bottom logic** — reasonable scoring for mulligan bottoms

---

## RECOMMENDED FIX PRIORITY

| Priority | Issue | Location | Fix |
|----------|-------|----------|-----|
| P0 | Removal projection kills creatures | `ev_evaluator.py:539-572` | Cap removal discount for CMC<=1; reduce for aggro |
| P0 | PiF/Grapeshot penalty too harsh | `ev_player.py:393-484` | Reduce `gy_fuel/opp_life` multiplier from 15 to 5 |
| P0 | Goryo's discard logic | `card_effects.py` discard | Tag Griselbrand for discard priority in combo decks |
| P0 | Living End ETB missing | `game_state.py:~1710` | Call `_handle_permanent_etb()` after creature return |
| P0 | Chalice X hardcoded | `game_state.py:1349` | Choose X based on opponent CMC distribution |
| P1 | Wrath on empty board | `ev_evaluator.py:272` | Hard penalty when `opp_creature_count == 0` |
| P1 | Burn face with no clock | `strategy_profile.py:102` | Gate `burn_face_mult` on board presence |
| P1 | Fatal Push mis-target | `response.py:156-169` | Target stack spell, not battlefield creature |
| P1 | Holdback vs spell decks | `ev_player.py:337-349` | Trigger holdback on combo/spell opponents, not just power |
| P1 | First strike in AI sim | `turn_planner.py:398-478` | Add first-strike damage step to `_simulate_combat` |
| P2 | Living End mull threshold | `mulligan.py:60` | Relax combo_sets at 6 cards |
| P2 | Tron land assembly | `ev_player.py:540-616` | Bonus for missing Tron piece |
| P2 | EtW tutor bias | `card_effects.py:881+` | Lower storm threshold for EtW selection |

---

## BALANCE SNAPSHOT (8-game matrix)

| Deck | Flat WR | Tier | Notes |
|------|---------|------|-------|
| Affinity | 84% | T1 | Possibly inflated — 100% vs 4 decks |
| Boros Energy | 64% | T1 | Carried by Phlage, not creature curve |
| Kappa Cannoneer | 62% | - | |
| Domain Zoo | 62% | - | Paralyzed by removal fear in traces |
| Jeskai Blink | 63% | T1 | |
| Dimir Midrange | 59% | - | Best-tuned strategy in the sim |
| Eldrazi Tron | 56% | T1 | No Tron assembly logic hurts |
| Ruby Storm | 41% | T1 | Finisher gating too conservative |
| Living End | 39% | T2 | Missing ETBs + high mull rate |
| Amulet Titan | 54% | T2 | |
| Izzet Prowess | 34% | T2 | Creatures never deployed (removal fear) |
| 4/5c Control | 32% | T2 | |
| 4c Omnath | 35% | T2 | |
| Azorius Control | 30% | - | Chalice bug actively losing games |
| Goryo's Vengeance | 34% | T2 | Combo never fires |

---

*Report generated by 6-agent LLM judge panel running Claude Opus 4.6. Each expert independently analyzed game traces, deck audits, AI source code, and engine logic.*
