# Outlier Strategy Audit — 2026-04-18

**Type:** Focused audit (outlier deep-dive, not full 6-expert panel)
**Trigger:** Weekly matrix refresh (n=50 Bo3, 6,000 matches) surfaced 4 outliers.
**Evidence:** `metagame_data.jsx` + 3 diagnostic Bo3 replays + code inspection.
**Investigators:** 3 parallel general-purpose agents, one per outlier.

## Executive Summary

| Deck | WR | Status | Severity | Root-cause confidence |
|---|---:|---|---|---|
| Ruby Storm | 31.3% (-14.9pp) | Regression | P0 + P1 | High |
| Azorius Control | 14.5% | Severe outlier | P0 | High |
| Affinity | 89.1% | Above expected | — | Rules-correct; problem is elsewhere |

Two confirmed bugs (Storm, Azorius); Affinity's engine is rules-correct — the 89% WR points at opponent-side under-valuation of artifact removal, not at Affinity itself. Expected post-fix states:
- Ruby Storm → 42–45% (after Storm P0 + P1 fixes)
- Azorius Control → 30–35% (after gameplan JSON fix)
- Affinity → requires opponent-side investigation; do not fix Affinity code

---

## 1. Ruby Storm — 31.3% weighted WR (regression from 46.2%, -14.9pp)

**Worst matchups:** vs Dimir Midrange 2%, vs Affinity 6%, vs Boros 10%.
**Symptom:** Storm casts 48 Desperate Ritual + 48 Manamorphose per 50 games vs Dimir but wins only 2%. Matrix `d1_finishers` shows "Desperate Ritual" as finisher (count=2) — meaning Storm rarely kills with Grapeshot; its rare wins are grindy, not combo.
**Replay evidence:** `replays/ruby_storm_vs_dimir_midrange_s60201.txt` G2 — Storm reaches storm=7 with Reckless Impulse draws, but never casts Grapeshot for lethal.

### Root causes

**P0 — `_has_finisher()` is hand-only (`ai/ev_player.py:720`).**
Does not consult sideboard, graveyard, or Wish tutor availability. When Wish is bottomed or held, and Grapeshot is in sideboard (reachable via Wish), the function returns `False`. The storm=0 gate at line 761 then blocks the go-off decision even though a finisher is one Wish away. Fix: extend the check to include (a) Wish in hand → sideboard Grapeshot/EtW, (b) Grapeshot in GY when Past in Flames is castable, (c) known tutors.

**P1 — Finisher mid-chain penalty too harsh (`ai/ev_player.py:824`).**
`mod -= fuel_available / opp_life * 40.0`. At storm=3, opp_life=8, fuel=4 this is −20 EV, making Grapeshot strongly negative even when lethal. Prior audit flagged this as "20x mid-chain penalty still suppresses chaining." Fix: reduce coefficient to ~10.0 or use `max(fuel_available − 2, 0) × 5.0` so Grapeshot stays positive once storm reaches a kill count.

**P2 — PiF not in the finisher gate (`ai/ev_player.py:761`).**
If Past in Flames is in hand and GY has ≥2 fuel spells, go-off should auto-allow independent of hand-finisher check. Currently `has_pif` is only used to count GY fuel, not to gate the decision.

### Reproducible test
```
python run_meta.py --matchup "Ruby Storm" "Dimir Midrange" -n 20 -s 60200
```
Current: 0% WR on this seed. Target after all three fixes: 25–30% on this matchup, 42–45% weighted overall.

---

## 2. Azorius Control — 14.5% weighted WR (0% vs Boros, 0% vs ETron)

**Symptom:** Plain Azorius Control is unwinnable vs aggro (Boros 0/50, ETron 0/50, Prowess 1/50). Meanwhile Azorius Control (WST) at 36.0% is inside its expected range. The two decks share the same archetype but have different shells.

### Root cause

**P0 — Gameplan references cards not in the decklist (`decks/gameplans/azorius_control.json:84-90`).**
The plain Azorius Control gameplan has `reactive_only` referencing "Dovin's Veto" and "Force of Negation" — cards the mainboard does not contain (verified vs `decks/modern_meta.py:624-659`: 0 copies of either in plain variant; 2x Dovin's Veto only in WST variant).

Effect: the reactive_only gate at `ai/ev_player.py:289-306` never fires for the two non-existent cards, so the gameplan carries dead weight. More importantly, the plain shell is an Isochron Scepter + Orim's Chant lock deck with only 4 copies of Counterspell as reactive cards — but the gameplan is structured as if it were a heavier counter suite. The AI plays it as if it has untapped counters it doesn't actually own.

### Proposed fix
`decks/gameplans/azorius_control.json:84-90` — remove Dovin's Veto and Force of Negation from `reactive_only`. This is a minimum fix. A fuller fix probably requires reviewing the goals/card_roles in the same file to confirm they reflect the Isochron-Chant lock plan vs the WST Chalice+Wan-Shi-Tong plan.

### Sim-verifiable prediction
Azorius Control should recover toward 30–35% weighted WR after gameplan alignment. Still likely below mid-50s because the Scepter-Chant lock is slow and the current mainboard has no fast interaction vs T1 aggro.

---

## 3. Affinity — 89.1% flat / 86.2% weighted WR (above 50-85% expected range)

**Symptom:** 100% vs Amulet Titan, 96% vs Goryo's, 96% vs WST, 88%+ across most of field. Replay shows single-turn 36-damage swings (`replays/pinnacle_affinity_vs_affinity_s60202.txt`: `[Combat Damage] 36 damage dealt → P1 life: 18 → -18`).

### Root cause — NOT the Construct scaling

Initial agent hypothesis: `_get_artifact_count()` at `engine/cards.py:303` counts artifact creatures, inflating Construct P/T. **Rejected after verification.**

**Oracle text (confirmed from `ModernAtomic.json`):** Urza's Saga Ch.II creates "*a 0/0 colorless Construct artifact creature token with 'This token gets +1/+1 for each artifact you control.'*" — NOT "each other artifact", NOT "each noncreature artifact". The oracle text counts all artifacts including artifact creatures and the token itself. The sim implementation is **rules-correct**.

The replay's 36-damage turn is a legal MTG outcome given 7–8 artifacts on the battlefield; real Ravager Affinity produces similar spikes in real play.

### Actual root cause — opponent-side under-removal

Real Modern Affinity runs 50-55% WR. The sim shows 89%. The ~35pp gap is almost certainly on the OPPONENT side: decks are not valuing artifact removal highly enough to race the Construct curve.

Areas to investigate next audit (not this one):
1. **`ai/ev_evaluator.py`** — does `creature_threat_value()` scale threat for an N/N Construct when N>5? If the threat calc caps or under-weights wide-board growth, opponents pass on removing artifacts early.
2. **Opponent sideboards** — do non-artifact-hate decks (e.g. Ruby Storm, Living End, Goryo's) have access to Force of Vigor / Haywire Mite / Ancient Grudge in SB? Check `decks/modern_meta.py` sideboards. Real pros board in artifact hate vs Affinity.
3. **BHI (`ai/bhi.py`)** — does the Bayesian opponent model predict Affinity's T2-T3 Urza's Saga activation, prompting opponents to hold removal?
4. **Prismatic Ending cost scaling** — does the sim correctly account for Ending's CMC based on target CMC (0 for Memnite, 1 for Signal Pest, etc.)? A mis-cost would make it cheaper or more expensive than it should be.

### Recommended action
Do NOT change Affinity code or the artifact-count helper. Either:
- Raise Affinity's expected range to 70-85% if we accept the sim's "all opponents under-remove" equilibrium, OR
- Open a follow-up audit scoped to opponent-side removal decisions (a broader issue than just Affinity).

---

## Priority fix order

1. **Ruby Storm P0** (`ai/ev_player.py:720` — `_has_finisher()` extension). Largest WR movement, clearest root cause, localized fix.
2. **Azorius Control P0** (`decks/gameplans/azorius_control.json`). 1-line JSON change, low risk. Verified: plain Azorius Control decklist has 0 copies of Dovin's Veto and 0 copies of Force of Negation in both MB and SB.
3. **Ruby Storm P1** (`ai/ev_player.py:824` — penalty softening). Stacks with #1.
4. **Affinity** — NO code change. Rejected initial hypothesis after oracle verification. Follow-up audit needed on opponent-side removal valuation.

## Post-fix verification plan

After each fix, run the specific seed listed above + full matrix n=20 for fast feedback. Full n=50 weekly matrix will confirm direction; then merge.

## Cross-reference

- Prior audit: `docs/history/audits/2026-04-11_LLM_judge.md` (D+ → C-)
- P1 "Storm ritual mid-chain penalty" flagged there but only partially addressed — this audit confirms it's still active (`ev_player.py:824`).
- Related: `docs/history/audits/2026-04-15_playbyplay_fixes.md`.
