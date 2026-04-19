# Failed Experiment: Mardu Energy (Apr 2026)

**Designed:** 2026-04-19
**Status:** FALSIFIED — below baseline Boros Energy at N=50 on two critical matchups
**Decision:** Not registered in MODERN_DECKS; decklist preserved here as reference.

## Hypothesis

Splashing black into Boros Energy (68.2% WWR base) would:
1. Add Orcish Bowmasters to solve Affinity's token swarm (sim's #1 problem deck)
2. Add Fatal Push as broader removal vs Thought Monitor / Nettlecyst-equipped / DRC
3. Add Thoughtseize SB vs Storm/Goryo/Amulet
4. Cost: −3 Seasoned Pyromancer, −2 Voice of Victory, −1 Static Prison, and some manabase consistency

Prediction: 70%+ WWR, ~40% vs Affinity, parity vs ET.

## Result (N=50 confirmed)

| Matchup | Mardu | Boros baseline | Δ | Verdict |
|---|---|---|---|---|
| Eldrazi Tron | **34%** | 60% | **−26pp** | Catastrophic |
| Affinity | **16%** | 24% | −8pp | Worse than baseline |
| Pinnacle Affinity | 50% | 62% | −12pp | Manabase tax |
| 4c Omnath | 75% | 52% | **+23pp** | Strong gain |
| Jeskai Blink | 75% | 64% | +11pp | Strong gain |
| 4/5c Control | 95% | 84% | +11pp | Strong gain |
| Living End | 95% | 86% | +9pp | Gain |
| Izzet Prowess | 70% | 62% | +8pp | Gain |
| Amulet Titan | 65% | 76% | −11pp | Loss |
| Dimir Midrange | 50% | 62% | −12pp | Loss |
| Average WR | 64.7% | 66.8% | −2.1pp | Net negative |

## Root causes of failure

1. **Bowmasters-vs-Affinity thesis falsified.** Affinity wins avg T6.3 vs Bowmasters. The 2-mana sweep only catches the first wave; Plating + Thought Monitor card advantage rebuilds fast enough to outrace. Anti-Affinity needs either faster clock or artifact-lock (Stony Silence), not a 2-mana sweeper.

2. **Fatal Push is dead in big-mana matchups.** Eldrazi Tron runs nothing ≤CMC 3 that matters (TKS 4, Smasher 5, Reshaper 3 usually blinked). 3 dead MB cards = −26pp vs ET.

3. **Manabase tax vs aggro mirrors.** 2 Godless + 2 Bloodstained + 1 Blood Crypt pulls 5 slots from the snow/fetch Boros package. Less Phlage synergy, more pain damage → 12pp loss vs Pinnacle Affinity.

## What the data actually shows

Black **helps vs blue-based midrange/control** (Omnath +23, Jeskai +11, 4/5c +11). This means **Bowmasters is a Dimir tool mis-deployed as an Affinity hoser**. Dimir Midrange already sits at 60% WWR running the same package correctly. The lesson:

> **Don't splash to solve a matchup. Pick a deck that wants the splash anyway.**

## Decklist (reference)

```
// Creatures (19)
4 Ragavan, Nimble Pilferer
4 Guide of Souls
4 Ocelot Pride
3 Orcish Bowmasters
4 Phlage, Titan of Fire's Fury

// Planeswalkers (4)
4 Ajani, Nacatl Pariah / Ajani, Nacatl Avenger

// Spells (14)
4 Galvanic Discharge
3 Fatal Push
3 Thraben Charm
1 Static Prison
3 Goblin Bombardment

// Lands (23)
4 Arid Mesa
3 Marsh Flats
2 Bloodstained Mire
2 Sacred Foundry
2 Godless Shrine
1 Blood Crypt
1 Arena of Glory
2 Elegant Parlor
2 Snow-Covered Plains
2 Snow-Covered Mountain
1 Swamp
1 Plains

SIDEBOARD
2 Thoughtseize
2 Wrath of the Skies
2 Engineered Explosives
2 Damping Sphere
2 Blood Moon
2 High Noon
2 Wear / Tear
1 The Legend of Roku
```

## Evidence files

- Pilot: N=20 field sweep (in-session, not saved)
- Confirmation: N=50 matchups
  - `run_meta.py --matchup "Mardu Energy" "Eldrazi Tron" -n 50` → 34%
  - `run_meta.py --matchup "Mardu Energy" "Affinity" -n 50` → 16%
  - `run_meta.py --matchup "Mardu Energy" "Pinnacle Affinity" -n 50` → 50%
- See also: `docs/diagnostics/2026-04-19_affinity_investigation.md` — reveals that Affinity's WR is partly driven by EV-scoring bugs, so any anti-Affinity brew tested in sim is testing against an inflated Affinity. Real-world Mardu would likely perform better vs Affinity than sim suggests, but the ET manabase problem is real either way.
