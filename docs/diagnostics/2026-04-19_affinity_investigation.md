---
title: Affinity Diagnostic — 3 Bo3 Traces
status: historical
priority: secondary
session: 2026-04-19
tags:
  - affinity
  - diagnostic
  - traces
summary: Original 3-Bo3 investigation (Boros/Jeskai/Dimir vs Affinity) that surfaced bugs 1-6. All listed engine bugs now landed via Claude Code.
---
# Affinity Diagnostic — Why Nobody Beats Affinity (3 Bo3 Traces)

**Date:** 2026-04-19  
**Goal:** Identify AI/EV bugs causing Affinity's 88% sim WR vs a real-world ~55% expected WR.

**Matchups investigated (Affinity "theoretically should struggle"):**

| Matchup | Sim WR | Real-world expected | Result this Bo3 | Seed |
|---|---|---|---|---|
| Boros Energy vs Affinity | 24% | ~55% | **Affinity 2-0** | 60200 |
| Jeskai Blink vs Affinity | 12% | ~50% | **Affinity 2-1** | 60300 |
| Dimir Midrange vs Affinity | 24% | ~55% | **Dimir 2-1** | 60400 |

Files:
- `replays/{matchup}_trace.txt` — single game (G1) with full AI reasoning
- `replays/{matchup}_bo3.txt` — full Bo3 match log
- `replays/{matchup}.html` — interactive Bo3 replayer

---

## EXECUTIVE SUMMARY — 5 Bugs Found

Ranked by impact on the 88% WR anomaly:

| # | Bug | Evidence | Impact | File in engine |
|---|---|---|---|---|
| **1** | **Cranial Plating EV ≈ −0.3 every turn** — scored as a dead 1-drop artifact | Boros trace T5–T7 (hoarded in hand 6 turns) | Critical — but works in Affinity's favor; artificially suppresses Affinity's curve so opp life totals look safer than they are | `ai/ev_scoring.py` `_score_spell()` |
| **2** | **Signal Pest EV = −4.2 to −4.1** (considered a bad cast) | Boros trace T5, Jeskai trace T5+ | High — delays battle-cry anchor, but Affinity wins anyway due to overpowered Plating numerics | `ai/ev_scoring.py` |
| **3** | **Memnite EV = −23.1** for a 0-mana creature | Dimir trace mid-game | High — 0-mana 1/1 should be strictly +value | `ai/ev_scoring.py` |
| **4** | **Orim's Chant cast on Boros's own M1 unkicked** for zero effect | Boros trace T4 (wastes mana + card vs empty board) | Medium — specific to Boros, costs ~1 tempo per game | `ai/ev_scoring.py` |
| **5** | **No targeting intelligence on Galvanic Discharge** — went face T2 instead of removing Ornithopter (future Plating target) | Boros trace T2 | Medium — lets the key Plating target live | `ai/combat_manager.py` or targeting logic |

**Root cause (per PROJECT_STATUS.md on-horizon):** `_score_spell()` is generic and deck-agnostic. It doesn't know Cranial Plating = deck's damage engine, Signal Pest = battle-cry anchor, Memnite = free ballast. The fix already proposed in the project: **`card_ev_overrides` in gameplan JSON + combo chain EV bypass**.

**Why this makes Affinity win anyway:** Even with the wrong EV on its own key cards, Affinity's numerics at combat time are so strong (Plating = +N/+0 where N = artifacts = 6–10) that a 1-turn delay doesn't kill the gameplan. Affinity recovers with free artifacts (Mox Opal, Springleaf Drum, Ornithopter). Boros/Jeskai/Dimir make similar EV mistakes on their own cards (Orim's Chant on own turn, delayed Bowmasters) but can't recover because their threats are honest-mana. **Asymmetric EV bug penalty.**

---

## ANNOTATED TRACE 1 — Boros Energy vs Affinity (Seed 60200) — Affinity 2-0

### Game 1: Affinity wins T5 via damage. Boros life went −1 before casting ANY relevant threat.

```
T1 Affinity | life=20 mana=0 hand=5+2L gy=0
  Hand: [Cranial Plating, Cranial Plating, Thought Monitor, Ornithopter, Sojourner's Companion]
  EV scores:
     +42.0  play_land: Spire of Industry <--       # Correct
     +19.0  play_land: Darksteel Citadel
      -0.0  cast_spell: Ornithopter  [h=-0.0]      # ← BUG 3 PRECURSOR
  >>> PLAY_LAND: Spire of Industry
```
> ⚠️ **Ornithopter EV = −0.0.** A 0-mana 0/2 flier. Should score strictly positive (free creature = +board). Generic `_score_spell()` penalizes for low power, ignoring that it's free. Same root as Memnite −23 bug.

```
T3 Affinity | life=17 mana=2 hand=5+0L gy=0
  Hand: [Cranial Plating, Cranial Plating, Thought Monitor, Sojourner's Companion, Signal Pest]
  Board: [Ornithopter (0/2)]
  EV scores:
      +8.0  cycle: Sojourner's Companion <--      # ← AI chooses to CYCLE
      -0.1  cast_spell: Signal Pest
      -0.1  cast_spell: Cranial Plating           # ← BUG 1: Plating = -0.1
      -0.1  cast_spell: Cranial Plating
  >>> CYCLE: Sojourner's Companion
```
> ⚠️ **Plating scored −0.1 on T3 with 2 mana available.** Correct move: cast Plating, hold for T4 equip. Instead AI cycles a bridgewalker for card selection, delaying Plating deployment by 4 more turns. **Affinity AI fails to execute its own gameplan.** Root: `_score_spell()` treats equipment as a generic 1-mana artifact.

```
T4 Boros Energy | life=14 mana=1 hand=[Ajani, Voice, Orim's Chant, Phlage, Ragavan]
  Opp board: [Ornithopter (0/2)] (life=17)
  EV scores:
      -0.1  cast_spell: Orim's Chant <--          # ← BUG 4
  >>> CAST_SPELL: Orim's Chant                    # ← Wastes 1 mana + 1 card
```
> 🛑 **BUG 4 confirmed.** Boros casts **Orim's Chant unkicked on its own Main Phase 1** with no opp interaction threats on the board. Effect: opp can't cast spells this turn — but it's Boros's turn, Affinity wasn't going to cast anything anyway, and the kicker (time-walk effect) wasn't paid. **Pure tempo loss.** AI doesn't know Orim's Chant is a "hold until kicker available" card. Fix: `card_ev_overrides` — Orim's Chant EV should be strongly negative unless kicker is payable.

```
T7 Affinity | life=12 mana=3 hand=[Cranial Plating, Cranial Plating, Springleaf Drum, Mox Opal]
  EV scores:
     +41.6  cast_spell: Thought Monitor <--       # Draw 2 hard-coded high
      +7.0  play_land: Razortide Bridge
      -0.3  cast_spell: Cranial Plating           # ← BUG 1 persists 4 turns later
      -0.3  cast_spell: Cranial Plating
  >>> CAST_SPELL: Thought Monitor
```
> 🛑 **BUG 1 now catastrophic.** Plating has been in Affinity's hand since T1, AI still scores it −0.3. Thought Monitor scores +41.6 because "draw 2" has a hard-coded high EV. **Deck archetype knowledge missing:** for Affinity, every turn without Plating in play costs ~2 damage. Thought Monitor is a cantrip, not a win condition. The EV gap should be inverted.

### Decisive turn — T9: Plating equipped, Thought Monitor becomes 12/2
```
T9 Affinity:
  Board: [Ornithopter (0/2), Signal Pest (0/1), Thought Monitor (2/2)]
  Permanents: [Springleaf Drum, Mox Opal, Cranial Plating, Springleaf Drum]
  >>> EQUIP: Cranial Plating → Thought Monitor
  Board after: [... Thought Monitor (12/2) ...]
  >>> ATTACK: Signal Pest, Thought Monitor
```
> ✅ **Finally the numerics work.** 10 artifacts give Plating +10/+0, Thought Monitor swings for 13 (with Signal Pest battle cry). Boros loses next turn. **Takeaway:** even with Affinity's own AI delaying Plating by 4 turns, the sheer magnitude of the equipped attack wins through. Boros had all of T2–T8 to race and instead wasted 1 card on Orim's Chant (bug 4), went face with Discharge (bug 5), and never developed enough board pressure. 

---

## ANNOTATED TRACE 2 — Jeskai Blink vs Affinity (Seed 60300) — Affinity 2-1

Same EV bugs on Affinity's side. Jeskai's additional failure: **Solitude (a 4-mana evoke 3-for-1 removal spell) is cast as a hardcast 3/2 instead of evoked**, and **Phelia's blink engine targets own creatures instead of removing threats**.

```
Affinity cards in hand (Jeskai trace line 96+):
  -0.1  cast_spell: Signal Pest        # BUG 2
  -0.1  cast_spell: Springleaf Drum    # Generic mana rock penalty
  -0.1  cast_spell: Cranial Plating    # BUG 1

Jeskai hand mid-game:
  Hand includes Solitude
  EV scores suggest Solitude is held for hardcast value, 
  not evoked when Affinity's creature count spikes.
```
> ⚠️ **Jeskai-specific bug suspected:** Solitude's evoke mode (exile a creature, then sacrifice Solitude) is the key vs Affinity. AI appears to evaluate it as a 3/2 body instead of a 3-for-1 removal spell. This would explain Jeskai's 12% WR being lower than Boros's 24%: Jeskai's entire anti-aggro plan is evoke-removal, and if evoke modes aren't EV'd correctly, the deck loses its identity. **Needs deeper trace investigation.**

---

## ANNOTATED TRACE 3 — Dimir Midrange vs Affinity (Seed 60400) — Dimir 2-1

**Only matchup where the underdog won.** Why? Dimir's plan is less EV-dependent:

```
T1 Dimir: Thoughtseize (-2.1 EV) — still cast, rips Affinity hand
T3 Dimir: Fatal Push on Ornithopter (-0.1 EV) — still cast, kills Plating target
T5 Dimir: Bowmasters cast +6.1 EV                  # ← FINALLY a correctly-scored card!
T? Dimir: Bowmasters cast +8.0 EV
```
> ✅ **Dimir wins because its threats are correctly scored.** Bowmasters at +6.1/+8.0 is legit (generic _score_spell likes 2-for-1 ETB triggers). Fatal Push + Thoughtseize go through despite low EV because nothing else beats them to threshold.

```
Affinity trace same game:
   -23.1  cast_spell: Memnite          # ← BUG 3 CONFIRMED
  >>> CAST_SPELL: Signal Pest          # (cast something else with higher EV)
```
> 🛑 **BUG 3 confirmed at absurd magnitude.** Memnite, a 0-mana 1/1 artifact creature (pure upside), scores **−23.1 EV**. The heuristic clearly has a "power-to-mana-ratio" term that divides by mana cost (giving −∞ as cost → 0), or a "low power = bad" penalty. Memnite should be **+5 to +8** (free artifact that enables Mox Opal metalcraft, triggers Signal Pest battle cry, equips Plating). **Affinity's three 0-mana creatures (Memnite, Ornithopter, signal Pest at 1-mana) are the engine of the deck and all mis-scored.**

---

## WHY THIS ASYMMETRICALLY PUNISHES AFFINITY'S OPPONENTS (NOT AFFINITY ITSELF)

Key insight from the traces:

1. **Affinity gets "free loans" from Mox Opal, Springleaf Drum, and 0-mana creatures.** Even if EV delays a cast by 1–2 turns, Affinity recovers because the mana is free. Thought Monitor draws 2 and rebuilds. Urza's Saga makes tokens.
2. **Boros / Jeskai / Dimir pay full mana for their threats.** Delay = permanent tempo loss. Orim's Chant wasted on T4 = no Phlage on T5. Fatal Push on Ornithopter T3 = no Bowmasters T3.
3. **Plating numerics are so strong at combat** that even a 4-turn-delayed Plating still one-shots the opponent. The deck's math cheats through its own AI's mistakes.

**This is why raising Affinity's opponents' EV fidelity matters MORE than fixing Affinity's own EVs.** Fixing Affinity's EV makes it ~90% WR (better curve). Fixing opponents' EV brings them from 12–24% up to 40–50% — actual meta balance.

---

## RECOMMENDED FIXES (in order of WR impact)

**FIX A — `card_ev_overrides` for Cranial Plating + Signal Pest + Memnite + Ornithopter** (1–2 hours)
```json
// decks/gameplans/affinity.json — add:
"card_ev_overrides": {
  "Cranial Plating": {"base_ev": 6.0, "ramp_scaling": true},
  "Signal Pest": {"base_ev": 4.0},
  "Memnite": {"base_ev": 3.0},
  "Ornithopter": {"base_ev": 3.0},
  "Springleaf Drum": {"base_ev": 4.0, "enables_affinity": true}
}
```
This aligns with the PROJECT_STATUS.md planned fix for the generic `_score_spell()` problem. Prediction: Affinity's **own** WR probably climbs 2–3pp (better curve), but symmetry means interaction still correct. **To really close the gap, also apply to opponents' key cards.**

**FIX B — `card_ev_overrides` for Orim's Chant (Boros) + Solitude evoke (Jeskai)** (1 hour)
```json
// decks/gameplans/boros_energy.json:
"card_ev_overrides": {
  "Orim's Chant": {"base_ev": -5.0, "hold_until": "kicker_available OR opp_ack_threat"}
}
// decks/gameplans/jeskai_blink.json:
"card_ev_overrides": {
  "Solitude": {"evoke_ev": 8.0, "hardcast_ev": 4.0}
}
```
Predicted impact: Boros vs Affinity 24% → 35–40%. Jeskai vs Affinity 12% → 30%. **Bigger impact than Fix A.**

**FIX C — Galvanic Discharge targeting heuristic** (30 min)
Add "prefer removing opp's artifact creatures that will become Plating targets" to the Discharge targeting logic. Cheapest fix for Boros matchup.

**FIX D (structural)** — Replace `_score_spell()` generic heuristic with archetype-aware scoring via `card_ev_overrides` everywhere. This is the PROJECT_STATUS.md "on the horizon" item. Multi-hour but addresses the root.

---

## WHAT I'D TEST NEXT

1. **Implement Fix A (Affinity overrides) alone.** Run N=50 Affinity vs field. Hypothesis: Affinity stays at 85–90% WR (minor curve gain). **Tests whether Affinity's OWN EV bugs matter.** If WR jumps to 95%, we have a problem (Affinity was self-limiting itself).
2. **Implement Fix B (Boros Orim's Chant + Jeskai Solitude evoke) alone.** Run N=50 Boros/Jeskai vs Affinity. Hypothesis: Boros 24% → 35%, Jeskai 12% → 25%. **Tests whether opponent bugs are the real problem.**
3. **Combine A + B.** Run full matrix N=30. Expected end-state: Affinity 70–75% WR (still #1 but not unbeatable), Boros/Jeskai proper anti-artifact matchups.

Do not commit any changes until the isolated tests above land. The file this doc lives in (`docs/diagnostics/2026-04-19_affinity_investigation.md`) is the paper trail; the 6 replay files are the raw evidence.
