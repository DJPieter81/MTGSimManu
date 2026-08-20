---
title: Ramp decks over-ramp and never deploy finishers (Eldrazi Ramp 17% field WR)
status: active
priority: primary
session: 2026-08-20
supersedes: []
superseded_by: []
depends_on: []
tags: [ai, scoring, ramp, ev_player, win-rate, calibration]
summary: >
  Bo3/verbose-replay root cause for the systemic under-performance of slow
  ramp decks. Eldrazi Ramp casts 9 mana-acceleration spells (incl. a 3rd
  Utopia Sprawl at ~10 mana) and ZERO 7+ CMC finishers across 3/3 sampled
  games, losing to a control deck by turn 10-13. Not a mana-shortage bug and
  not an engine can_cast bug — the AI keeps scoring incremental ramp above
  deployment even at surplus mana. Same signature explains the whole
  "aggro top of table / ramp+combo+control bottom" WR skew.
---

# Ramp decks over-ramp and never deploy finishers

## Symptom

Bo1 outlier-scan matrix (n=4, all 25 registered decks, 2026-08-20, current
branch `claude/mobilize-token-sacrifice-fix`) shows a clean structural skew:

| Top of table (above band) | | Bottom (below band) | |
|---|---|---|---|
| Pinnacle Affinity | 78% | Eldrazi Ramp | **17%** |
| Affinity | 77% | Creatures Toolbox | 21% |
| Domain Zoo | 75% | Goryo's Vengeance | 22% |
| Boros Energy | 74% | Jeskai Blink | 38% |
| Dimir Midrange | 73% | Instant Reanimator | 40% |
| Izzet Prowess | 72% | Amulet Titan | 42% |

Every over-performer is proactive aggro/tempo; every under-performer needs
setup (ramp / combo / control). The bottom decks hand near-free wins to the
whole field, inflating the top.

## Reproduction

```
python run_meta.py --verbose "Eldrazi Ramp" "4/5c Control" -s 50500
python run_meta.py --trace   "Eldrazi Ramp" "4/5c Control" -s 50000
```

Eldrazi Ramp loses even against slow, non-aggressive decks that give it all
the time it wants:

| Opponent | WR (n=6 Bo1) | avg game length |
|---|---|---|
| Azorius Control | 33% | T13 |
| 4/5c Control | 17% | T10.8 |
| Amulet Titan | 17% | T9 |
| Goryo's Vengeance | 33% | T11 |

## Root cause (replay-verified)

Threats cast across 3 sampled games (seeds 50000 / 50500 / 51000): **0, 0, 0.**

Seed 50500 full P1 cast sequence over 13 turns:

```
T2 Malevolent Rumble   (dig/ramp)
T3 Talisman of Impulse  (mana rock)
T4 Sowing Mycospawn     (ramp creature, fetches a land)
T5 Talisman of Impulse  (mana rock)
T6 Sowing Mycospawn     (ramp creature)
T7 Utopia Sprawl        (land aura, +1 mana)
T8 Utopia Sprawl        (land aura, +1 mana)
T9 Utopia Sprawl        (land aura, +1 mana)   <-- 3rd copy; deck runs only 3 Forests
T11 Icetill Explorer    (ramp creature)
T12 Kozilek's Return    (removal)
```

Nine mana-acceleration spells, one removal, **zero finishers** — despite the
deck ramping to a large mana pool (2 Talisman + 3 Utopia Sprawl + 2 Sowing
Mycospawn land-fetches + natural land drops).

Ruled OUT:
- **Engine `can_cast` bug** — direct harness: `can_cast(Sire of Seven Deaths
  {7})` with 8 generic lands returns `True`; `can_cast(Devourer {5}{C}{C})`
  with no colorless sources correctly returns `False`. Engine casting is
  correct.
- **Colorless {C}-pip mana bug** — Eldrazi Tron (62% WR, functional) runs the
  same Devourer of Destiny {5}{C}{C} and casts it. Colorless payment works.
- **Mana shortage** — seed 50500 ramps hard and still casts 0 threats; the 3rd
  Utopia Sprawl at ~10 mana is pure waste, proving surplus mana existed.

Confirmed cause: **the AI's scoring keeps incremental mana-acceleration ranked
above (or in place of) finisher deployment even once mana is in surplus.**
In the `--trace`, a held finisher (Sire of Seven Deaths, {7}) never appears in
the candidate EV-score list on turns where the deck's own mana was still below
7 — and on turns where mana was ample, the planner spent the turn on another
ramp spell (a 3rd Utopia Sprawl / a 4th Talisman) rather than deploying.

## Responsible subsystem

`ai/ev_player.py` spell scoring for mana-acceleration spells (`Talisman`-class
rocks, `Utopia Sprawl`-class land auras, and ramp-tagged spells). There is
land-side "rush a land to reach a big creature" logic (`RAMP_TO_BIG_NOW` /
`RAMP_TO_BIG_SOON`, ~L1912) but no **decay of ramp-spell value once mana is
already in surplus**, and no symmetric "deploy the finisher now that it is
affordable" pull strong enough to beat casting yet another accelerant.

## Proposed fix (generalization-first)

A mana-acceleration spell's marginal value must decay toward ~0 once the
controller can already pay for the most expensive card in hand (i.e. more mana
does not unlock any currently-uncastable play). Threshold derived from the
hand's max-CMC card, not a literal. This:

- retires the "3rd Utopia Sprawl at 10 mana" waste,
- frees the turn for the planner to select finisher deployment,
- generalizes to every ramp shell in the field (Amulet Titan, 4c Omnath,
  Eldrazi Tron, Broodscale Bloodchief) and to any deck holding surplus
  mana rocks — a fix that only helped Eldrazi Ramp would be a smell.

Generalization check (per CLAUDE.md): validate the matrix delta on at least
Amulet Titan and one Storm-side case in addition to Eldrazi Ramp. Failing test
first, phrased on the mechanic ("mana-acceleration value decays at surplus
mana"), no card names in `ai/`.

## Falsified sub-hypothesis (2026-08-20) — archetype flip is NOT the fix

Tried: flip `DECK_ARCHETYPES["Eldrazi Ramp"]` MIDRANGE → RAMP and rewrite
`decks/gameplans/eldrazi_ramp.json` to a RAMP/CURVE_OUT/CLOSE_GAME structure
(mirroring the functional Eldrazi Tron, which is RAMP at 62% WR), with a
resource_target of 7 mana and finisher card_priorities.

Result: **regression, 16.7% → 13.5% field WR (Bo1 n=4).** Reverted.

Why it failed: the `RAMP` StrategyProfile (`ai/strategy_profile.py:182`) is
`MIDRANGE` with only `holdback_applies=False` — there is no ramp-specific
"deploy the finisher once mana is online" behavior. Flipping the archetype
merely removed instant-speed mana holdback, stripping the Kozilek's
Return/Command interaction that was keeping the deck alive versus aggro, so it
durdled *harder*. Threats cast across the same 3 seeds stayed at 0/0/0.

Conclusion: the deficiency is in **generic threat-deployment scoring**, not in
archetype selection or gameplan JSON. A config-layer fix cannot close this gap;
it requires the ev_player scoring change described above (surplus-mana ramp
decay + a deployment pull), which is Phase-2-kernel-scale work, not a quick
win. Do not re-attempt the archetype flip.
```
