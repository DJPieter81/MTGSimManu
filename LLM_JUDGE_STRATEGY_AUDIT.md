# LLM-Judge Strategy Audit Report (v2)

**Date:** 2026-04-11 (re-audit)
**Sample:** ~210 games (6 trace matchups + 1 deck audit x28 games + 15x15 matrix x20 games)
**Method:** 6-expert parallel panel, each reviewing different strategic domains
**Previous Grade: D+ | Current Grade: C-**

---

## Panel Summary

| Expert | Domain | Prev | Now | Key Finding |
|--------|--------|------|-----|-------------|
| 1 | Mulligan & Openers | **C+** | **C+** | Living End mulligan unchanged; combo_set threshold still at ≤5 cards |
| 2 | Mana & Sequencing | **D+** | **C** | Removal projection FIXED for cheap creatures (Guide of Souls +2.6); evasion/lifelink tracking bug in removed state |
| 3 | Combat & Threats | **C+** | **B-** | First strike in AI combat FIXED; creature deployment working for aggro |
| 4 | Combo & Storm | **D+** | **C** | Goryo's combo NOW FIRES; Storm ritual mid-chain penalty still at 20x |
| 5 | Control & Interaction | **D+** | **D** | Chalice X FIXED; but Wrath still cast on empty board; 4c Omnath non-functional; no planeswalker scoring |
| 6 | Rules & Engine | **B-** | **B** | Living End ETB FIXED; Ephemerate lacks target validation; trace blocks not a bug |

---

## PREVIOUS P0 ISSUES — STATUS

| # | Issue | Status | Evidence |
|---|-------|--------|----------|
| 1 | Removal projection kills creatures | **FIXED** | Guide of Souls: -7.6 → +2.6 EV. Boros deploys T1 1-drops. Memnite/Ornithopter at +0.1 |
| 2 | Storm finisher uncastable | **PARTIAL** | PiF penalty reduced 15→5x. Storm wins via EtW/Grapeshot. But mid-chain ritual penalty still 20x |
| 3 | Goryo's combo non-functional | **FIXED** | Faithful Mending correctly bins Griselbrand T6. Goryo's reanimates T8. Draw 14 cards. Combo fires |
| 4 | Living End missing ETBs | **FIXED** | `_resolve_living_end()` now calls `_handle_permanent_etb()` at line 1734 |
| 5 | Chalice hardcoded X=1 | **FIXED** | X now chosen by opponent CMC distribution (`game_state.py:1354-1370`) |

---

## NEW/REMAINING CRITICAL ISSUES (P0)

### 1. 4c Omnath Completely Non-Functional
**Experts 2, 5** | Root cause: `ai/ev_player.py` `_score_spell()` — no planeswalker evaluation

4c Omnath at **37% flat / 29% meta-weighted WR** — worst deck in the format. In trace vs Dimir Midrange, Omnath holds Wrenn and Six, Quantum Riddler, and Galvanic Discharge for the entire game, never casting them. Only spell cast: Ephemerate targeting nothing.

**Root cause:** `_score_spell()` has explicit bonuses for removal, tutors, draw, and storm but **NO special case for `CardType.PLANESWALKER`**. Planeswalkers score ~0 EV because they have no immediate board impact (no power/toughness). Wrenn and Six (one of the best cards in Modern) is valued at -0.1 EV.

**Impact:** 4c Omnath, Jeskai Blink (Teferi), and any deck relying on planeswalkers for value is fundamentally handicapped.

### 2. Wrath Still Cast on Empty Board (Fix Insufficient)
**Experts 2, 5** | Files: `ai/ev_evaluator.py:272-275`

The penalty code exists (`projected.opp_life += 100` when `opp_creature_count == 0`), but it produces EV = -1.8 instead of deeply negative. With threshold of -5.0, the Wrath is still cast. Observed in Eldrazi Tron vs Azorius trace: Azorius casts **two** Wrath of the Skies on completely empty boards.

The +100 opp_life penalty is being washed out by `evaluate_board()` normalization. The fix needs to be a hard gate (skip the spell entirely), not a soft EV penalty.

**Impact:** Control decks waste cards and mana on empty sweepers, accelerating their losses.

---

## HIGH ISSUES (P1 — Significant Strategy Errors)

### 3. Storm Ritual Mid-Chain Penalty Still at 20x
**Expert 4** | File: `ai/ev_player.py:424-428`

After Manamorphose (storm=1), rituals drop from +4.7 to -4.1 EV. The PiF penalty was reduced from 15→5x, but the mid-chain ritual penalty (`mod -= (storm+2)/opp_life * 20.0`) was NOT reduced. This creates a +13.3 EV swing that prevents Storm from building storm count. Ruby Storm at **40% flat / 37% meta WR**.

```python
# Line 428 — penalty multiplier still 20.0 (should match PiF fix of 5.0)
mod -= (storm + 2) / opp_life * 20.0
```

### 4. Evasion/Lifelink Not Subtracted in Removed State
**Expert 2** | File: `ai/ev_evaluator.py:491-492`

When calculating the "creature is removed" EV scenario, `my_evasion_power` and `my_lifelink_power` are copied unchanged from the projected state instead of being decremented. Flying/menace creatures show incorrect EV deltas because the "removed" scenario retains their evasion power contribution.

```python
# Lines 491-492 — BUG: should subtract evasion/lifelink if creature has those keywords
my_evasion_power=projected.my_evasion_power,     # ← not subtracted
my_lifelink_power=projected.my_lifelink_power,    # ← not subtracted
```

### 5. Ephemerate Cast With No Legal Targets
**Expert 6** | Files: `engine/card_effects.py:636-676`, `engine/game_state.py`

4c Omnath casts Ephemerate when there are zero creatures on the battlefield. The spell should be illegal without a legal target (MTG rules: you cannot cast a spell that requires targets if no legal targets exist). The spell "fizzles" at resolution but the mana and card are wasted.

### 6. Affinity 85% Win Rate — Format Warping
**Expert 3** | 15×15 matrix data

Affinity at **85% flat WR** beats 12 of 14 opponents. Avg win turn T5.7. Cranial Plating at 90% WinCR, Thought Monitor at 100% WinCR. The deck deploys 5 permanents T2 and overwhelms opponents who can't interact with artifacts. No deck has sufficient artifact removal.

**Root cause:** Opponent interaction is too weak — very few decks have artifact hate mainboard, and the AI doesn't sideboard. Affinity's free spells (Ornithopter, Memnite, Mox Opal) are evaluated at +0.1 EV and deployed aggressively.

### 7. Duplicate Chalice Deployment
**Expert 5** | File: `ai/ev_player.py`

Azorius casts 2x Chalice of the Void at the same X value in the same game. The second Chalice is redundant — both trigger simultaneously but provide no additional coverage. No logic checks whether a Chalice already exists on the battlefield.

---

## MEDIUM ISSUES (P2 — Suboptimal Play)

### 8. Living End Mulligan Still Too Aggressive
**Expert 1** | File: `ai/mulligan.py:60`

`mulligan_combo_sets` requires BOTH cascade spell + cycler at 6 cards. Only relaxes at ≤5 cards. Real LE pilots keep 6-card hands with just cyclers + lands. Should relax at 6 cards. Living End at **39% WR**.

### 9. Psychic Frog Negative EV (-3.2) in Dimir
**Expert 2** | File: `ai/ev_evaluator.py`

Despite being Dimir Midrange's best threat, Psychic Frog scores -3.2 EV on T5 with opposing creatures on board. Removal probability combined with evasion tracking bug (P1 #4) and mana cost produces deeply negative scores for a 2-mana flying threat. Dimir delays Frog deployment by 2-3 turns.

### 10. Tron Lands Not Differentiated
**Expert 2** | File: `ai/ev_player.py`

All Tron lands score identically (+15.0/+19.0). No bonus for completing the Tron set (Mine+Tower+Plant = 7 mana from 3 lands). Missing assembly logic reduces Tron's ramp consistency.

### 11. Dovin's Veto Dead Weight
**Expert 5**

UW counterspell that only counters noncreature spells. Against Eldrazi Tron, it sits dead in hand for the entire game. Azorius fills response slots with narrow answers.

---

## WHAT'S WORKING WELL

**Fixed since v1:**
- Creature deployment for aggro (Guide of Souls +2.4, Ragavan +8.2)
- Goryo's Vengeance full combo chain (Faithful Mending → bin Griselbrand → Goryo's → draw 14)
- Living End ETB triggers (creatures returned get proper ETBs)
- Chalice X selection (adaptive by opponent CMC distribution)
- First strike in AI combat simulation (two-phase damage)
- PiF penalty reduced (15x → 5x multiplier)
- Burn face gating (requires board presence or low opp life)

**Consistently working:**
- Fetch land prioritization (+32-35 EV, correctly above basics)
- Cascade mechanics (Living End resolves correctly)
- Storm copy counting (CR 702.40a compliant)
- Counterspell targeting restrictions (noncreature/instant-sorcery gates)
- Turn structure (Untap/Upkeep/Draw/Main/Combat/Main/End)
- Legend rule (SBA 704.5j enforced)
- Orcish Bowmasters / Phlage ETBs
- Ritual mana tracking through storm chains
- Land-before-spell sequencing
- Living End cycling priority (cycles before cascading)
- Dimir Midrange strategy (58% WR, best-tuned deck)
- Kappa Cannoneer artifact synergy (56% WR)
- Domain Zoo creature deployment (corrected from v1)
- Boros Energy aggro curve (64% WR, deploys T1)
- Walking Ballista X-cost handling (correct)

---

## RECOMMENDED FIX PRIORITY

| Priority | Issue | Location | Fix |
|----------|-------|----------|-----|
| P0 | Planeswalker scoring missing | `ev_player.py:_score_spell()` | Add base EV bonus for planeswalkers (+3-5 based on loyalty) |
| P0 | Wrath on empty board (fix insufficient) | `ev_evaluator.py:272-275` | Hard-gate: return -999 EV instead of soft penalty |
| P1 | Storm ritual mid-chain 20x penalty | `ev_player.py:428` | Reduce multiplier from 20.0 → 5.0 (match PiF fix) |
| P1 | Evasion/lifelink removed state bug | `ev_evaluator.py:491-492` | Subtract evasion/lifelink power when creature removed |
| P1 | Ephemerate no target validation | `game_state.py:cast_spell()` | Check target exists before putting spell on stack |
| P1 | Duplicate Chalice prevention | `ev_player.py` | Check if Chalice already on board before positive EV |
| P1 | Affinity format dominance | Decklist / interaction | Add artifact removal to more sideboards; Affinity 85% is unrealistic |
| P2 | Living End mull threshold | `mulligan.py:60` | Relax combo_sets at 6 cards |
| P2 | Psychic Frog negative EV | `ev_evaluator.py` | Fix evasion bug (P1 #4) should resolve this |
| P2 | Tron land assembly | `ev_player.py` | Bonus for completing Tron set |

---

## BALANCE SNAPSHOT (20-game 15×15 matrix)

| Deck | Flat WR | Meta WR | Tier | Change from v1 |
|------|---------|---------|------|-----------------|
| Affinity | 85% | 82% | T1 | +1% — dominant, possibly unrealistic |
| Boros Energy | 64% | 64% | T1 | 0% — stable, creature curve now works |
| Eldrazi Tron | 65% | 57% | T1 | +9% — benefits from Chalice fix |
| Domain Zoo | 65% | 57% | — | +3% — creature deployment improved |
| Dimir Midrange | 58% | 58% | — | -1% — stable, best-tuned strategy |
| Kappa Cannoneer | 56% | 53% | — | new deck |
| Jeskai Blink | 56% | 50% | T1 | -7% — hurt by planeswalker undervaluation |
| Izzet Prowess | 51% | 42% | T2 | +17% — major improvement from creature fix |
| Ruby Storm | 40% | 37% | T1 | +1% — marginal, mid-chain penalty hurts |
| Living End | 39% | 38% | T2 | 0% — ETB fix helps, mulligan still hurts |
| 4c Omnath | 37% | 29% | T2 | +2% — still broken (no planeswalker scoring) |
| 4/5c Control | 36% | 30% | T2 | +4% — slightly improved |
| Amulet Titan | 36% | 32% | T2 | -18% — regression, needs investigation |
| Azorius Control | 34% | 31% | — | +4% — Chalice fix helps but still weak |
| Goryo's Vengeance | 26% | 25% | T2 | -8% — combo fires but still loses to aggro |

---

## GRADE JUSTIFICATION

**C- (up from D+):** Five P0 issues from v1 are now fixed (removal projection, Goryo's combo, Living End ETBs, Chalice X, first strike). Aggro archetypes now deploy creatures correctly. But two new P0 issues emerged (4c Omnath non-functional, Wrath fix insufficient), four P1 issues remain active, and Affinity at 85% WR warps the format. Control archetypes are still fundamentally weak. The sim correctly plays ~8 of 15 decks at reasonable strategy level.

---

*Report generated by 6-agent LLM judge panel running Claude Opus 4.6. Each expert independently analyzed game traces, deck audits, AI source code, and engine logic.*
