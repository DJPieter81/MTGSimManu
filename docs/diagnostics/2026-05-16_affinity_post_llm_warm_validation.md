---
title: "Affinity field WR with warmed LLM cache — first WR movement of the diagnostic chain"
status: active
priority: primary
tags: [affinity, validation, llm-cache, decision-scorer, budget-fix, refactor]
depends_on:
  - docs/diagnostics/2026-05-16_affinity_post_refactor_validation.md
  - docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md
summary: "After bumping decision_scorer input_tokens_limit (PR #411) and warming the LLM cache against the live Anthropic API, Affinity field WR is 81.6% — down 4.3pp from the post-refactor baseline of 85.9%. First concrete WR movement of the entire diagnostic chain. Biggest shifts: Boros Energy 75→60%, Ruby Storm 80→65%, Living End 75→65%, 4/5c Control 100→90%. The LLM-derived weights diverge from defaults for many (archetype, context) pairs, and the divergence flows through to gameplay."
---

# Affinity field WR with warmed LLM cache — first WR movement

## Headline

**Affinity field WR went from 85.9% → 81.6% with the warmed LLM cache. Delta: -4.3pp.**

This is the **first concrete WR movement** of the diagnostic chain (PRs #381, #389, #395, #396, #399, #402, #406, #408, #410, #411). All prior fixes were structurally correct but produced byte-identical sim behavior; the LLM-derived weights are the first change that flows through to gameplay.

## What unblocked

PR #411 raises `decision_scorer` input_tokens_limit from 1000 → 2500. Before this fix, every live LLM call raised `UsageLimitExceeded` (system prompt is ~1700 tokens); the exception was silently caught and the helper returned `DEFAULT_WEIGHTS` values byte-identical to the pre-refactor constants. After the fix, live calls succeed and write to the SQLite cache at `cache/llm/responses.sqlite`.

Cache warm: `tools/llm_cache_warm.py --task decision_scorer` populated 72 (archetype, context) entries against `claude-haiku-4-5` in ~810s wall-clock.

## Per-opponent shift table

| Opponent | Pre-warm | Post-warm | Δ |
|---|---:|---:|---:|
| Goryo's Vengeance | 100% | 100% | 0 |
| Amulet Titan | 95% | 95% | 0 |
| 4c Omnath | 90% | 95% | +5 |
| Azorius Control | 95% | 95% | 0 |
| Eldrazi Tron | 95% | 90% | -5 |
| **4/5c Control** | 100% | 90% | **-10** |
| Azorius Control (WST v2) | 95% | 90% | -5 |
| Azorius Control (WST) | 90% | 90% | 0 |
| Izzet Prowess | 85% | 85% | 0 |
| Domain Zoo | 75% | 75% | 0 |
| Dimir Midrange | 80% | 75% | -5 |
| Pinnacle Affinity | 75% | 70% | -5 |
| Jeskai Blink | 70% | 65% | -5 |
| **Ruby Storm** | 80% | 65% | **-15** |
| **Living End** | 75% | 65% | **-10** |
| **Boros Energy** | 75% | 60% | **-15** |
| **Field avg** | **85.9%** | **81.6%** | **-4.3** |

## Why these decks moved most

The biggest shifts hit exactly the decks the earlier diagnostic chain identified as suffering most:

- **Boros Energy** (-15pp) — was the canonical example in PR #389's opp-side root cause diagnostic. Boros at 1 life choosing Phlage over Wrath of the Skies. The LLM-derived weights apparently improve Boros's defensive decision-making.
- **Ruby Storm** (-15pp) — combo deck with the combo-clock override (Phase 2 removed the structural override; LLM-derived `storm_threshold=8.5` vs default `5.0` likely changes Storm's payoff timing).
- **Living End** (-10pp) — the canonical cascade combo case from PR #396. LLM-derived `cycling_cascade_boost=0.0` for combo archetype (vs default `8.0`) reduces over-credit.
- **4/5c Control** (-10pp) — multi-color control deck with many archetype-conditional decision points.

The decks that didn't move (Goryo's, Amulet Titan, ETron) likely have LLM weights close to the defaults for their (archetype, context) cells.

## What this tells us

1. **The LLM-at-decision-time pattern works.** The cache returns reasoned, model-derived values that materially differ from the hand-tuned defaults. Cached output for `(combo, cycling_cascade_boost)`: `weight=0.0 confidence=0.95 rationale="Combo decks do not rely on cycling-cascade synergy; this mechanic is specific to Living End..."`
2. **The earlier diagnostics were correct about *what* was wrong — they were wrong about *how* to fix it.** PR #389 named two opp-side defects in `position_value` / `_project_spell`. The parked fix (`bdff379`) had structurally correct edits but moved WR by +1.3pp. The actual fix turned out to be: clean up the override architecture (Phases 1+2) AND replace tuned constants with LLM-derived weights. The two are necessary together.
3. **81.6% is still well above the 50-65% expected band.** The refactor + warm got us 4.3pp of movement; another 16pp+ is needed to land in band. Three follow-on paths remain:
   - Further LLM warm coverage (more (archetype, context) pairs, more decks)
   - Per-opponent MulliganPolicy tuning for the top remaining matchups (Goryo's 100%, Amulet 95%, 4c Omnath 95%, Azorius Control 95%)
   - Affinity-side scoring audit (the Phase L hypothesis: Affinity over-credits its own plays)

## Cache lifecycle note

`cache/llm/responses.sqlite` is `.gitignore`d (per `ai/llm_cache.py:21`). The validation in this doc was run with a one-shot warmed cache; the next session will start cold unless `tools/llm_cache_warm.py --task decision_scorer` is re-run. Cost: ~810s wall-clock, well under the $2 budget.

## Frontmatter rationale

- `status: active` — this is the current hot-state of the Affinity outlier investigation
- `priority: primary` — supersedes the post-refactor validation (which concluded "refactor was necessary but not sufficient"; this doc completes the "sufficient" story)
- `depends_on` cites the prior validation + the original opp-side root cause for traceability
