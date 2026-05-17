---
title: "Full 16×16 matrix re-measure post LLM-cache warm — exposes archetype-tag mislabeling"
status: active
priority: primary
tags: [llm-decision-scorer, full-matrix, validation, living-end, ruby-storm, archetype-taxonomy]
depends_on:
  - docs/diagnostics/2026-05-16_affinity_post_llm_warm_validation.md
  - docs/diagnostics/2026-05-16_affinity_post_refactor_validation.md
summary: "Re-ran the canonical Bo3 n=20 16-deck matrix with the warmed decision_scorer cache. Affinity moved only -0.7pp (vs PR #411's -4.3pp field-only measurement — close enough given the field shape diff). The bigger story is collateral: Living End -15.3pp, Ruby Storm -19.3pp, Amulet Titan -11.0pp, while 4/5c Control +12.3pp, Izzet Prowess +8.7pp. Root cause: the LLM correctly judges that 'combo archetypes don't use cycling/cascade mechanics' and zeros the cycling/cascade weights for the `combo` archetype — but DECK_ARCHETYPES lumps Living End (cascade), Ruby Storm (storm), Amulet Titan, and Goryo's all under `combo`. The LLM's archetype-correct call demolishes the decks that DO need those mechanics. The warm is not a clean win; it surfaces a taxonomy gap."
---

# Full 16×16 matrix re-measure post LLM-cache warm

## Headline

**The LLM-cache warm is NOT a globally clean win.** Affinity moves -0.7pp (within noise), but Living End drops -15.3pp and Ruby Storm drops -19.3pp. Net Σ-delta across 16 decks is ~0 (closed system), but individual decks shift by ±12-19pp, and **90 of 240 non-mirror matchups (37.5%) moved ≥15pp; 50 moved ≥25pp**.

This validates that broadening the measurement beyond Affinity field was the right call — without it we would have shipped a warm that silently destroyed Living End and Storm.

## Method

- **Pre-warm baseline:** `metagame_results.json` @ 2026-05-09T19:27:09 (Bo3, n=20, 16 decks, pre-LLM-warm), retrieved via `git show HEAD:metagame_results.json` before re-running.
- **Warm step:** `python -m tools.llm_cache_warm --task decision_scorer` with `ANTHROPIC_API_KEY=$MTGSIM_LLM_KEY`. 72 live LLM calls, 63 unique cache rows, $0.04 wall cost, 1024s wall-clock.
- **Post-warm matrix:** `python run_meta.py --matrix -n 20 --save` (Bo3, n=20, **17 decks** — WST v2 added since baseline). 136 matchups, 3 workers, ~100 min wall-clock.
- **Comparison:** restricted to the 16 decks common to both runs (WST v2 excluded since no baseline).
- **Sanity check:** mean WR across 16 decks = 50.0% in both pre and post (closed system); ΣΔ = 0.0pp ✓

## Per-deck field WR (16-common shape)

| Deck | pre% | post% | Δ pp |
|---|---:|---:|---:|
| Affinity | 82.7 | 82.0 | -0.7 |
| Eldrazi Tron | 70.3 | 74.3 | **+4.0** |
| Domain Zoo | 66.0 | 67.0 | +1.0 |
| Boros Energy | 69.0 | 65.3 | -3.7 |
| Pinnacle Affinity | 64.0 | 64.7 | +0.7 |
| Jeskai Blink | 60.7 | 60.7 | 0.0 |
| **4/5c Control** | **48.0** | **60.3** | **+12.3** |
| Dimir Midrange | 52.3 | 54.7 | +2.3 |
| 4c Omnath | 52.7 | 54.3 | +1.7 |
| **Izzet Prowess** | 37.7 | 46.3 | **+8.7** |
| Azorius Control (WST) | 30.0 | 38.7 | **+8.7** |
| **Living End** | **53.0** | **37.7** | **-15.3** |
| **Ruby Storm** | **56.0** | **36.7** | **-19.3** |
| **Amulet Titan** | **36.0** | **25.0** | **-11.0** |
| Azorius Control | 14.7 | 22.3 | +7.7 |
| Goryo's Vengeance | 7.0 | 10.0 | +3.0 |

**Mean WR:** pre=50.0, post=50.0 (closed system, sanity ✓). **Mean |Δ|:** 6.3pp.

New deck **Azorius Control (WST v2)** field WR vs baseline-16: **50.0%** (no pre to compare).

## Biggest matchup swings (|Δ| ≥ 30pp, ordered)

| Matchup | pre% | post% | Δ |
|---|---:|---:|---:|
| Living End vs 4/5c Control | 75 | 0 | **-75** |
| 4/5c Control vs Living End | 25 | 100 | **+75** |
| Living End vs Azorius Control | 70 | 0 | **-70** |
| Azorius Control vs Living End | 30 | 100 | **+70** |
| Izzet Prowess vs Living End | 40 | 100 | **+60** |
| Living End vs Izzet Prowess | 60 | 0 | **-60** |
| Living End vs 4c Omnath | 60 | 5 | **-55** |
| Living End vs Azorius Control (WST) | 55 | 0 | **-55** |
| 4c Omnath vs Living End | 40 | 95 | **+55** |
| Azorius Control (WST) vs Living End | 45 | 100 | **+55** |
| Ruby Storm vs Goryo's Vengeance | 100 | 60 | -40 |
| Pinnacle Affinity vs Living End | 60 | 100 | +40 |
| Goryo's vs Ruby Storm | 0 | 40 | +40 |
| Living End vs Pinnacle Affinity | 40 | 0 | -40 |
| Living End vs Dimir Midrange | 45 | 5 | -40 |
| Dimir Midrange vs Living End | 55 | 95 | +40 |
| Eldrazi Tron vs Pinnacle Affinity | 20 | 55 | +35 |
| Pinnacle Affinity vs Eldrazi Tron | 80 | 45 | -35 |
| Jeskai Blink vs Ruby Storm | 45 | 75 | +30 |
| Eldrazi Tron vs Ruby Storm | 60 | 90 | +30 |
| Ruby Storm vs Jeskai Blink | 55 | 25 | -30 |
| Ruby Storm vs Eldrazi Tron | 40 | 10 | -30 |
| Ruby Storm vs Living End | 55 | 25 | -30 |
| Amulet Titan vs Goryo's Vengeance | 95 | 65 | -30 |
| ...20 more at \|Δ\| ≥ 25pp | | | |

**Total |Δ| ≥ 15pp: 90/240 non-mirror cells (37.5%). |Δ| ≥ 25pp: 50.**

The Living End collapse dominates the list — it now loses 0-5% to nearly every control or midrange deck, where it previously beat them 45-75%.

## Root cause: archetype taxonomy collapses cascade/storm into `combo`

Sampling the warmed cache and the rationales the LLM produced for the cycling/cascade contexts:

> `(combo, cycling_cascade_boost) = 0.0 conf=0.95`
> *"Combo archetypes do not synergize with cycling-cascade mechanics; they win via ritual sequencing and tutor chains, not graveyard fuel"*

> `(combo, cycling_gameplan_boost) = 0.0 conf=0.95`
> *"Pure combo archetypes (e.g., Ad Nauseam, Murktide) don't rely on cycling mechanics"*

> `(combo, cycling_free_cost_bonus) = 0.0 conf=0.85`
> *"Combo decks don't generally benefit from free cycling costs; they prioritize ritual sequencing, tutors, and storm-count"*

> `(combo, cascade_free_spell_value) = 0.0 conf=0.90`
> *"Combo archetypes do not inherently benefit from cascade mechanics; cascade free-spell generation is a Living End/cycling-specific concept"*

> `(combo, combo_force_payoff_storm_threshold) = 8.5 conf=0.85`
> *"Combo decks (especially storm-based variants) gain substantial value when storm count reaches critical thresholds"*

The LLM is **domain-correct**: as a generic claim, "combo decks don't use cycling/cascade" holds. But `DECK_ARCHETYPES` registers:

```
Living End      → combo
Ruby Storm      → combo
Amulet Titan    → combo
Goryo's Vengeance → combo
```

So when the LLM zeros `combo`'s cycling/cascade weights and bumps `combo`'s storm-threshold, **Living End** (cascade-driven combo) loses its cycling EV entirely, **Ruby Storm** waits longer to fire (threshold 5.0 → 8.5), and **Amulet Titan** (cycling-irrelevant but cascade-irrelevant too) takes collateral damage from the boost flips.

**The LLM made the right call for its labels; the labels are wrong for these decks.**

## What this proves

1. **The warming pattern works in principle.** Live LLM calls produce different weights (62 cells diverged from defaults), the cache makes them deterministic, and the sim consumes them. This is structurally validated.
2. **Affinity outlier is NOT shifted by the warm at full-matrix scale.** -0.7pp is well within noise. PR #411's -4.3pp Affinity field measurement is reproducible if we restrict to the Affinity-field shape, but at full-matrix Affinity stays at 82%. The Affinity proximate cause is upstream of decision_scorer (mulligan or Affinity-side scoring, as the post-refactor doc predicted).
3. **Archetype taxonomy is the bottleneck.** Until `DECK_ARCHETYPES` distinguishes `cascade` (Living End), `storm` (Ruby Storm), and `combo` (Goryo's, Amulet), the LLM's archetype-level judgments will collide with deck-specific reality.

## The fast-path bug discovered along the way

`ai/llm_decision_scorer._try_cache_only()` queries the cache with a dict-keyed input (`{"archetype": ..., "context": ...}`), but the agent layer writes cache rows keyed by the run_sync prompt **string** (`"archetype=X; context=Y"`) — two different SHA-256s. Result: the resolver's "fast path" cache lookup ALWAYS misses; cache hits only happen via the agent wrapper one layer deeper.

This is a pre-existing bug (PR #411 had it; this measurement had it; the post-warm doc's results were measured with the same bug). It's correctness-neutral (agent layer cache still hits) but adds an indirection per `weight()` call. Filed as follow-up; not fixed in this measurement to keep the comparison apples-to-apples with PR #411.

## Follow-on paths (priority-ordered)

1. **Split `combo` archetype into `combo / cascade / storm` in DECK_ARCHETYPES.** This is the smallest principled change that re-routes Living End → `cascade` and Ruby Storm → `storm`, both of which already have dedicated cache rows. Expect Living End to recover most of its -15pp and Storm most of its -19pp. **Code change required.**

2. **Re-warm with new archetype labels.** The cycling cells for `cascade` are already in DEFAULT_WEIGHTS and were warmed at LLM weight 8.5 (close to default 8.0). The `storm` row for `combo_force_payoff_storm_threshold` was already in the table. Just exposing them through the archetype lookup is mostly a label fix.

3. **Fix `_try_cache_only` dict↔string mismatch.** Either pass the same string the agent uses, or change the agent to call with a dict. Restores the fast path; reduces per-`weight()` latency from 2x to 1x SQLite read.

4. **Audit Amulet Titan tagging.** Amulet uses neither cycling, cascade, nor storm-count — its `combo` tag is also dubious. Probably belongs in `ramp` (like Eldrazi Tron), which would re-route its weights cleanly.

5. **Audit Goryo's Vengeance tagging.** Reanimator-combo; not cascade, not storm. Either keep as `combo` (correct domain claim that cycling doesn't matter) and accept the +3pp it just gained, or split out a `reanimator` tag.

## Per-PR-#411 reconciliation

PR #411 measured Affinity field WR 85.9% → 81.6% (-4.3pp) using `--field affinity`. This run measures Affinity field WR 82.7% → 82.0% (-0.7pp) using the 16-deck restricted matrix. The 4pp gap reconciles via:
- `--field` shape includes the new 17th deck (WST v2) which I excluded here for apples-to-apples
- `--field` seed convention is matchups@50000 not matrix@40000; different seeds = different draws
- the -4.3pp run had a partially warmed cache (72 entries claimed there too, all decision_scorer); this run has 63 unique entries (some collisions). Functionally identical.

The two measurements are not contradictory; they measure slightly different surfaces.

## Verification

- Cache populated: `cache_stats(task='decision_scorer')` returns `entries=63, by_model=anthropic:claude-haiku-4-5` ✓
- Matrix completed: 136/136 matchups, 0 errors, symmetry OK (per run_meta output) ✓
- Closed-system sanity: mean WR pre = 50.0, post = 50.0; ΣΔ = 0.0 ✓
- LLM API spend: $0.0353 (well under $2/30d budget per `cache/llm/calls.jsonl`) ✓

## Frontmatter rationale

- `status: active` because the follow-on archetype split is the immediate next action
- `priority: primary` because this supersedes the Affinity-only field validation as the canonical post-warm signal
- `depends_on` cites the two prior post-warm/post-refactor docs that this completes
