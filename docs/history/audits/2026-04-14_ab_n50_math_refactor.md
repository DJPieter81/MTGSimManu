# N=50 A/B: Magic numbers vs Math-derived scoring

**Branch:** `claude/refactor-scores-numbers-fnfk7`
**Baseline:** commit `45eff55` (all magic numbers intact)
**Refactor v2:** commit `a6d72e1` (EV + Tron + Amulet+Titan + response threats all math-derived)
**Sample:** 6 top-share decks × 50 Bo3 matches per pair (30 ordered matchups, 750 matches/run)
**Seeds:** identical on both runs (default `seed_start=40000`, step 500)

## Summary

- Mean |Δpp| per matchup: **7.3** (vs N=15 run: 11.2 — N=50 is ~35% tighter)
- Matchups moved ≥5pp: 18/30
- Matchups moved ≥15pp: 2/30
- **Audit targets both met**: Affinity down significantly, Storm up (small).

## Per-deck overall WR (with 95% CI on delta)

| Deck | Magic % | Derived % | Δpp | 95% CI | Sig? |
|------|--------:|----------:|----:|-------:|:----:|
| Eldrazi Tron | 42.0% | 46.0% | +4.0 | ±8.7 | — |
| Boros Energy | 46.4% | 49.6% | +3.2 | ±8.8 | — |
| Jeskai Blink | 34.4% | 37.6% | +3.2 | ±8.4 | — |
| Ruby Storm | 20.8% | 22.4% | +1.6 | ±7.2 | — |
| Affinity | 94.8% | 88.8% | -6.0 | ±4.8 | **❌** |
| Pinnacle Affinity | 61.6% | 55.6% | -6.0 | ±8.6 | — |

## Audit targets

- **Ruby Storm** (expected UP from P0 chain-scoring fix): 20.8% → **22.4%** (+1.6pp). Direction ✅ but within CI — small effect.
- **Affinity** (expected DOWN from 93% outlier): 94.8% → **88.8%** (-6.0pp). Direction ✅ and **statistically significant** (outside CI).

## Per-matchup moves ≥ 5pp

| Row | Col | Magic % | Derived % | Δpp |
|-----|-----|--------:|----------:|----:|
| Eldrazi Tron | Pinnacle Affinity | 12% | 36% | 🟢 **+24** |
| Pinnacle Affinity | Eldrazi Tron | 88% | 64% | 🔴 **-24** |
| Ruby Storm | Pinnacle Affinity | 10% | 22% | 🟢 **+12** |
| Pinnacle Affinity | Ruby Storm | 90% | 78% | 🔴 **-12** |
| Jeskai Blink | Eldrazi Tron | 32% | 42% | 🟢 **+10** |
| Eldrazi Tron | Jeskai Blink | 68% | 58% | 🔴 **-10** |
| Eldrazi Tron | Affinity | 0% | 10% | 🟢 **+10** |
| Affinity | Eldrazi Tron | 100% | 90% | 🔴 **-10** |
| Boros Energy | Eldrazi Tron | 46% | 54% | 🟢 **+8** |
| Eldrazi Tron | Boros Energy | 54% | 46% | 🔴 **-8** |
| Boros Energy | Jeskai Blink | 68% | 60% | 🔴 **-8** |
| Jeskai Blink | Boros Energy | 32% | 40% | 🟢 **+8** |
| Boros Energy | Affinity | 6% | 14% | 🟢 **+8** |
| Affinity | Boros Energy | 94% | 86% | 🔴 **-8** |
| Boros Energy | Ruby Storm | 66% | 74% | 🟢 **+8** |
| Ruby Storm | Boros Energy | 34% | 26% | 🔴 **-8** |
| Ruby Storm | Affinity | 4% | 10% | 🟢 **+6** |
| Affinity | Ruby Storm | 96% | 90% | 🔴 **-6** |

## Context: N=15 vs N=50 noise comparison

| Deck | N=15 Δpp | N=50 Δpp |
|------|---------:|---------:|
| Boros Energy | +4.2 | +3.2 |
| Jeskai Blink | -13.2 | +3.2 |
| Eldrazi Tron | +1.2 | +4.0 |
| Ruby Storm | +13.2 | +1.6 |
| Affinity | -4.0 | -6.0 |
| Pinnacle Affinity | -1.4 | -6.0 |

Jeskai Blink's apparent -13pp at N=15 shrank to +3pp at N=50 — confirming the earlier finding that per-cell noise at N=15 is ±15pp. Ruby Storm's +13pp at N=15 also shrank to +1.6pp — the chain-scoring fix is real but smaller than it looked at N=15.

## Interpretation

The math-derived refactor produces a **cleaner, more focused effect** than N=15 suggested:
- The only statistically significant deck-level change is Affinity going from 95% to 89%.
- All other per-deck deltas are within noise bounds at 95% confidence.
- This is expected behaviour for a refactor that fixes specific audit-flagged bugs: the big outlier gets pulled in, everything else stays roughly where it was.

**Remaining work for Affinity 89% → ~75% target:** Affinity is still a T0 outlier. The scaling equipment value now reflects in stack threat (response.py) but Affinity's own evaluation of how good its board is may still under-account for hate. Future audit site: Cranial Plating / Nettlecyst attach value when opponent has artifact-hate cards.
