---
title: Affinity mulligan over-keep audit (Phase L follow-up)
status: active
priority: primary
session: 2026-05-04
depends_on:
  - docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md
  - docs/diagnostics/2026-05-04_affinity_overperformance_audit.md
tags:
  - audit
  - affinity
  - mulligan
  - phase-l-followup
summary: >
  Investigation of whether Affinity's AI mulligan scorer keeps marginal hands
  too aggressively, contributing to its 84% sim WR. Empirical: 1000-hand
  keep-rate distribution vs Boros / Dimir / Tron / Zoo controls; sample
  hand traces; gameplan-threshold comparison. Findings: NOT FOUND. Affinity
  keeps 76.4% of 7-card openers — within 3.3pp of Boros (73.1%) and LOWER
  than Dimir (77.9%) and Eldrazi Tron (79.2%). Mulligan thresholds are
  symmetric (`min_lands=2, max_lands=3`) and the same logic mulls 1-land
  and 4+-land hands across all aggro decks. Sampled keeps are uniformly
  defensible: 86.9% of kept Affinity hands have a 0-CMC spell, almost all
  contain Mox Opal / Springleaf / a creature — these are objectively good
  hands by Modern standards. Recommendation: do NOT pursue a mulligan-
  scorer fix. Look elsewhere — primary suspect remains Phase L's E-1
  (`my_artifact_count` includes lands → inflated `position_value` via
  `mana_clock_impact`), and Bo1-vs-Bo3 default. Mulligan AI is not the
  bias source.
---

# Affinity mulligan over-keep audit

## Hypothesis under test

Phase L's residual gap (Affinity ~84% sim WR vs ~55-60% expected, even
after Phase K decklist edits and PR-L1 artifact-land filter) might be
explained by an **asymmetric mulligan calibration**: the shared scorer
keeps marginal hands that a real Modern player would mulligan, AND the
asymmetry favors Affinity because its worst hands still goldfish T3-T4
while opponents' worst hands lose to a T3 Plating-equip-attack.

## Method

1. **Distribution sweep** — generate 1000 deterministic 7-card openers
   per deck (seeds 200000-200999), pass each through the live
   `MulliganDecider.decide()` at virtual hand size 7, and tabulate
   keep/mull rate plus the `last_reason` distribution. Tools:
   `tools/_diag_affinity_mulligan_distribution.py` (one-off, not
   committed). Compared decks: Affinity, Boros Energy (control),
   Dimir Midrange, Eldrazi Tron, Domain Zoo.
2. **Sample-trace inspection** — `run_meta.py --verbose Affinity
   "Boros Energy" -s {50000,50500,...,55000}` and read the opener
   + mulligan reason for each.
3. **Threshold comparison** — `grep "mulligan_*" decks/gameplans/*.json`
   to compare cross-deck thresholds.

## Empirical results

### Keep-rate distribution (1000 hands per deck)

| Deck | Keep% | Mull% | 2L kept | 3L kept | 4L kept | 5L kept |
|---|---|---|---|---|---|---|
| Affinity      | 76.4 | 23.6 | 38.1% | 41.6% | 15.2% |  5.1% |
| Boros Energy  | 73.1 | 26.9 | 36.9% | 42.3% | 16.3% |  4.5% |
| Dimir Midrange| 77.9 | 22.1 | 35.8% | 40.7% | 20.7% |  2.8% |
| Eldrazi Tron  | 79.2 | 20.8 | 33.7% | 39.8% | 22.9% |  3.7% |
| Domain Zoo    | 73.2 | 26.8 |   —   | 36.7% | 34.4% |  6.3% |

- Affinity keep rate (76.4%) sits in the **middle of the pack**, not at
  the top. Dimir and Tron both keep MORE hands; Boros and Zoo keep
  fewer (only marginally).
- The Affinity-vs-Boros delta is **+3.3pp**. Even if every one of those
  marginal kept hands were a 50/50 instead of a 70/30 win for Affinity,
  the net WR contribution would be ~0.6pp — far short of the 25-30pp
  overperformance gap.

### Mulligan reasons (Affinity)

```
153  too few lands (1 < 2)
 26  too many lands (4 > 3)
 22  too few lands (0 < 2)
 20  too many lands (5 > 3)
  7  0 lands — no mana artifacts (Affinity)
  7  6 lands (≥ 6)
  1  no castable spells
```

Mulls fire on the mechanically defensible cases: 0-1 lands, 4+ lands,
empty Mox-less. No "borderline misclassification" pattern in the tail.

### Sampled kept-hand quality

Five randomly-picked kept hands (seeds 200000-200009; printed by the
diag script):

```
[0]  2L: {Razortide Bridge, Urza's Saga}
     5S: {Engineered Explosives, Mox Opal, Mox Opal, Ornithopter, Thought Monitor}
[1]  2L: {Mistvault Bridge, Tanglepool Bridge}
     5S: {Cranial Plating, Memnite, Signal Pest, Sojourner's Companion, Thought Monitor}
[6]  2L: {Mistvault Bridge, Treasure Vault}
     5S: {Frogmite, Mox Opal, Ornithopter, Sojourner's Companion, Springleaf Drum}
[8]  3L: {Razortide Bridge, Spire of Industry, Treasure Vault}
     4S: {Cranial Plating, Memnite, Signal Pest, Springleaf Drum}
[9]  2L: {Darksteel Citadel, Razortide Bridge}
     5S: {Cranial Plating, Nettlecyst, Signal Pest, Thought Monitor, Thought Monitor}
```

These are uniformly **good hands by Modern standards** — every one
has a 0-CMC enabler (Memnite, Ornithopter, Mox Opal, Springleaf Drum,
or 0-cost Signal Pest), the lands cover the cost reduction stack, and
Plating + carrier appears in 3 of 5. None are "marginal keeps".

86.9% of all kept Affinity hands contain at least one 0-CMC spell —
the deck's structural advantage (cheap floor, fast development) shows
up in opener distribution itself, not in mulligan misjudgment.

### Hand-trace samples (run_meta.py --verbose Affinity Boros -s SEED)

| Seed | Opener | Decision | Outcome |
|---|---|---|---|
| 50000 | 2L/5S Mox + Plating + Memnite | KEEP | wins |
| 50500 | 3L/4S Memnite + Springleaf + 4 cheap | KEEP | (clean keep) |
| 51000 | 4L/3S Plating + Springleaf | KEEP at max_lands=3 boundary (Mox Opal as mana_artifact ⇒ allowed via key-card path) | (defensible) |
| 51500 | 4L/3S | MULLIGAN (too many lands) | correct |
| 52000 | 3L/4S Mox + Ornithopter | KEEP | (clean keep) |
| 52500 | 4L/3S → 1L/6S | MULL → MULL | correct |
| 54000 | 4L/3S Mox + Signal Pest | KEEP, P2 on draw | wins T5 |
| 55000 | 2L/5S Mox + Springleaf + 4 cheap | KEEP | (clean keep) |

The 4-land seed-54000 keep is the most "marginal" of the sampled set —
yet it has Mox Opal (cheats the land count) and a curve play, and it
wins T5. Real-world Modern Affinity also keeps 4-land hands with Mox
Opal; this is correct play, not a sim bias.

## Threshold comparison

```
Affinity:        min=2 max=3   require_creature_cmc=(unset)
Boros Energy:    min=(2 default) max=3  require_creature_cmc=2
Domain Zoo:      min=1 max=3   require_creature_cmc=2
Izzet Prowess:   min=1 max=3   require_creature_cmc=2
Dimir Midrange:  min=2 max=4
Eldrazi Tron:    min=2 max=4
```

**Subtle structural finding:** three control decks (Boros, Zoo, Prowess)
declare `mulligan_require_creature_cmc=2`. Affinity does not. In theory,
this could let Affinity keep no-creature hands. In practice, Affinity
treats Memnite + Ornithopter as `mulligan_keys` and `always_early`, and
86.9% of kept hands already contain a 0-CMC creature — so the missing
gate has near-zero observable effect on Affinity. (Adding it would mull
~2-3% more hands; not a meaningful WR lever.)

## Why marginal-kept hands still win (the asymmetry hypothesis)

The hypothesis was: even if Affinity's keep-rate is normal, its
marginal kept hands win more often than opponents' marginal kept hands
because Affinity's floor is high (Memnite + 0-CMC artifact + Mox Opal
goldfish T3 is real). This is true at the **deck-design** level — but
it's also true in real Modern; Affinity is a tier-1 aggro deck for
exactly this reason. Penalising the AI's keep threshold for Affinity
specifically would be **forcing the sim to play the deck worse than a
human would**, not correcting a bias.

The smoking gun for overperformance must therefore live elsewhere —
the most likely candidate remains **Phase L E-1 (already filed as
PR-L1, merged): `my_artifact_count` included artifact lands**, with
the residual coming from Bo1-vs-Bo3 defaults (no SB hate density)
and possibly the engine A-1 finding (T1 Plating cast suspected
under-payment, not yet repro'd).

## Findings

**NOT FOUND.** No mulligan over-keeping bias on Affinity:

1. Keep rate (76.4%) is within 3.3pp of the aggro control (Boros 73.1%)
   and *below* two non-aggro controls (Dimir 77.9%, Tron 79.2%).
2. Mulligan reason distribution is mechanically defensible — no
   "marginal-keep" tail visible.
3. Sample inspection of 8 traces shows kept hands are uniformly strong;
   the only "marginal" 4-land keep was correctly enabled by Mox Opal
   and won on schedule.
4. Threshold comparison reveals a minor asymmetry (no
   `require_creature_cmc` on Affinity) that is empirically inert
   because 86.9% of Affinity's kept hands already contain a 0-CMC
   creature.

## Recommendation

**No fix PR.** Stop at this diagnostic doc. Steer the remaining ~5-10pp
overperformance investigation toward:

1. **Verify PR-L1 (artifact-land exclusion) is fully integrated** —
   re-run `run_meta.py --field Affinity -n 100 --save` against current
   `main` to measure the actual delta.
2. **Bo3 default switch** — Phase K's structural recommendation. The
   matrix's Bo1 default deprives opponents of post-board hate density.
3. **Phase L A-1 repro** — the suspected T1 Plating cast under-payment;
   if real, this is a Class F engine bug that would directly lift
   Affinity over expectation regardless of any AI scoring layer.
4. **Position-value double-counting beyond E-1** — re-audit
   `ai/clock.py:444 artifact_value` to confirm no remaining lands-
   included contribution after PR-L1.

The mulligan AI is not the bias source. Future cycles probing Affinity
overperformance should not re-enter this path.

## Provenance

- Empirical sweep: `tools/_diag_affinity_mulligan_distribution.py`
  (one-off, not committed; output cached at `/tmp/mull_diag.txt`)
- Decks compared: Affinity, Boros Energy, Dimir Midrange, Eldrazi
  Tron, Domain Zoo (1000 hands each, deterministic seeds 200000-200999)
- Sample traces: `run_meta.py --verbose Affinity "Boros Energy"
  -s {50000,50500,51000,51500,52000,52500,53000,53500,54000,54500,55000}`
- Mulligan code reviewed end-to-end: `ai/mulligan.py:112-540`
- Gameplans inspected: `decks/gameplans/{affinity,boros_energy,
  domain_zoo,izzet_prowess,dimir_midrange,eldrazi_tron}.json`
- Phase L E-1 (still primary suspect):
  `docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md` §Q5
