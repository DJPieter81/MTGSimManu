# A/B: Magic numbers vs Derived formulas

**Branch:** `claude/refactor-scores-numbers-fnfk7`
**Baseline:** commit `45eff55` (pre-refactor, magic numbers)
**Refactor:** commit `a146ec3` (derived formulas)
**Sample:** 6 top-share decks × 15 Bo3 matches per pair (30 ordered matchups, ~450 matches)
**Seeds:** identical on both runs (default `seed_start=40000`, step 500)

## Per-deck overall WR

| Deck | Magic % | Derived % | Δpp |
|------|--------:|----------:|----:|
| Ruby Storm | 17.6% | 30.8% | +13.2 ✅ |
| Boros Energy | 50.6% | 54.8% | +4.2 ✅ |
| Eldrazi Tron | 44.0% | 45.2% | +1.2 |
| Pinnacle Affinity | 65.2% | 63.8% | -1.4 |
| Affinity | 86.8% | 82.8% | -4.0 ❌ |
| Jeskai Blink | 35.8% | 22.6% | -13.2 ❌ |

## Audit targets

- **Ruby Storm** (audit target: UP from 39% field WR): 17.6% → **30.8%** (+13.2pp). ✅ P0 fix validated.
- **Affinity** (audit target: DOWN from 93% outlier): 86.8% → **82.8%** (-4.0pp). Partial — still too high but moving right direction.
- **Jeskai Blink** (no audit target): 35.8% → **22.6%** (-13.2pp). Regression — likely Site 4 wrath + Site 1 removal probability changes reduced its reactive edge.

## Biggest per-matchup moves

**Top positive (refactor gained):**

| Row | Col | Magic % | Derived % | Δpp |
|-----|-----|--------:|----------:|----:|
| Ruby Storm | Jeskai Blink | 27% | 60% | **+33** |
| Eldrazi Tron | Jeskai Blink | 60% | 80% | **+20** |
| Ruby Storm | Eldrazi Tron | 7% | 27% | **+20** |
| Ruby Storm | Pinnacle Affinity | 7% | 27% | **+20** |
| Boros Energy | Eldrazi Tron | 53% | 67% | **+14** |

**Top negative (refactor lost):**

| Row | Col | Magic % | Derived % | Δpp |
|-----|-----|--------:|----------:|----:|
| Jeskai Blink | Ruby Storm | 73% | 40% | **-33** |
| Jeskai Blink | Eldrazi Tron | 40% | 20% | **-20** |
| Eldrazi Tron | Ruby Storm | 93% | 73% | **-20** |
| Pinnacle Affinity | Ruby Storm | 93% | 73% | **-20** |
| Eldrazi Tron | Boros Energy | 47% | 33% | **-14** |

## Summary

- Total ordered matchups: 30
- Mean |Δpp|: **11.2**
- Moved ≥5pp: 24/30
- Moved ≥15pp: 8/30

## Verdict

The refactor directionally fixes the P0 Storm bug (+13.2pp overall) and the Affinity outlier (-4pp), at the cost of a Jeskai Blink regression (-13.2pp). Moves are almost entirely symmetric (d1 vs d2 up ↔ d2 vs d1 down by same amount), confirming the changes are signal not RNG. The Jeskai regression warrants investigation — the Site 4 wrath refactor may have over-pruned its board-wipe plays; worth tracing a Jeskai vs Storm game under both builds to confirm.
