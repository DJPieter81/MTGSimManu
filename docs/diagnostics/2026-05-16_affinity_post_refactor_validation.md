---
title: "Affinity field WR post Phase 1+2 refactor — validation gate"
status: active
priority: primary
tags: [affinity, validation, refactor, phase-1, phase-2, llm-decision-scorer, override-elimination]
depends_on:
  - docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md
  - docs/diagnostics/2026-05-16_wrath_enumeration_gate.md
  - docs/diagnostics/2026-05-16_cascade_combo_override_at_lethal.md
summary: "After Phase 1 (LLM decision-scorer + 8 archetype-tied constants dropped, PR #402) + Phase 2 (sweep all 15 archetype-conditional branches + position_value loses archetype param, PR #408), Affinity field WR is 85.9% vs the 85.6% diagnostic baseline. Delta +0.3pp within noise. The structural refactor produced zero behavior change because LLM calls fall back to DEFAULT_WEIGHTS (no live API) and MulliganPolicy / gameplan-JSON defaults preserve pre-refactor behavior. The override architecture is cleaned up; the proximate cause of Affinity 85% lies elsewhere."
---

# Affinity field WR post Phase 1+2 refactor — validation gate

## Headline

**Affinity field WR went from 85.6% to 85.9% post-refactor — delta +0.3pp (within noise band).**

The Phase 1+2 structural refactor did NOT move the Affinity outlier.

## Method

- Command: `python run_meta.py --field affinity --games 20` (Bo3 by default per CLAUDE.md canonical format)
- Wall-clock: ~2 min on this hardware (n=20 Bo3 with sideboarding)
- `main` HEAD: `3736d5a` (Phase 2 merged)
- Baseline: `docs/diagnostics/2026-05-10_affinity_85pct_opponent_side_root_cause.md` cites 85.6% from a prior n=20 Bo3 measurement at the same seed convention

## Per-opponent breakdown

```
Affinity vs field (avg 85.9%)

  vs Goryo's Vengeance        : 100%
  vs 4/5c Control             : 100%
  vs Eldrazi Tron             :  95%
  vs Amulet Titan             :  95%
  vs Azorius Control (WST v2) :  95%
  vs Azorius Control          :  95%
  vs 4c Omnath                :  90%
  vs Azorius Control (WST)    :  90%
  vs Izzet Prowess            :  85%
  vs Ruby Storm               :  80%
  vs Dimir Midrange           :  80%
  vs Boros Energy             :  75%
  vs Domain Zoo               :  75%
  vs Living End               :  75%
  vs Pinnacle Affinity        :  75%
  vs Jeskai Blink             :  70%
```

15 of 16 opponents lose to Affinity ≥ 70% of the time. The shape is unchanged from the pre-refactor diagnostic.

## Honest verdict

The refactor did not pay off in WR terms — but the structural change is sound, and the null result is informative.

### Why the refactor produced zero behavior change

1. **No live LLM cache.** All `ai/llm_decision_scorer.weight(...)` calls fall through to `DEFAULT_WEIGHTS` (sandbox has no API key). The default-weights table is byte-identical to the pre-refactor constants. PR #402's commit message documented this: *"the byte-identical offline-mode property means this PR is provably neutral in sim behavior."*
2. **MulliganPolicy defaults preserve pre-refactor behavior.** Phase 2 (PR #408) migrated 7 mulligan-side `if archetype == X` branches to `MulliganPolicy.*` flags in the gameplan JSON. The default values were chosen to reproduce the prior conditional behavior for each existing archetype. No deck saw its mulligan logic change.
3. **`uses_combo_chain_scoring` defaults align with the old `archetype in ('combo', 'storm')` test.** The 4 `ai/ev_evaluator.py` migrations all consult a gameplan field; for current 16 decks the field evaluates to the same boolean the old conditional produced.

In other words: Phase 1+2 successfully removed the override architecture without breaking anything — a clean structural win — but did not introduce any new behavior.

### What this tells us about the Affinity outlier

The proximate cause of Affinity's 85% is NOT the override architecture. The chain of fixes (#381, #389, #395, #396, #399, #402, #408) collectively cleaned up rule-correctness in `_score_suspend`, `position_value`, `_project_spell` sac-clause, X-cost board-wipe gate, archetype-conditional branches, and combo-clock override — and the WR is unchanged.

This rules out: "the heuristic EV layer is wrong about defensive saves" as a sufficient explanation. It is *necessary* (the prior diagnostics were correct about the bugs) but not *sufficient* to move Affinity.

What's left as the proximate cause:
- **Affinity-side overscoring** (the original Phase L hypothesis from `docs/diagnostics/2026-05-04_phase-l-affinity-ai-audit.md`): if Affinity's offensive plays are over-weighted by N EV units, fixing opponents' defensive scoring by M EV units changes nothing as long as M < the offensive over-credit
- **Mulligan-side**: opponents' opening hands may systematically be too greedy vs Affinity. PR #389 noted "mulligan keeps hands too greedy vs Affinity" as a candidate; Phase 2 migrated the mulligan branches but the *defaults* preserve the old greedy behavior
- **Sideboard policy**: Affinity-hate cards may not be coming in often enough, or with the right `IN/OUT` shape

## What would actually move Affinity

Three concrete paths (none implemented here — this doc names them, doesn't fix them):

1. **Live LLM cache warm.** Run `tools/llm_cache_warm.py --task decision_scorer --decks ALL` against the live API. The cache populates with model-derived weights; on the next sim, weights diverge from defaults and behavior changes. Requires API key + ~$0.20 token spend.

2. **Per-opponent MulliganPolicy tuning.** Identify the 4-5 opponents Affinity beats hardest (Goryo's, 4/5c Control, ETron at 100/100/95) and audit their mulligan policy. If they keep 6-card hands when they should mull to 5, tightening `MulliganPolicy.requires_combo_backup` or `key_card_min_cheap_relaxed` defaults would move those matchups. n=20 Bo3 per matchup confirms.

3. **Affinity-side scoring audit.** Revisit Phase L's Affinity-side overscoring hypothesis with fresh eyes given the cleaned-up architecture. The `artifact_count_includes_lands` audit was earlier; redo against current main and see if the over-credit is still present.

## What this doc supersedes / depends on

- This doc does NOT supersede PR #389's diagnostic — that diagnostic correctly named real bugs that have now been fixed. The bugs were just not the proximate cause.
- This doc DOES update the project's working hypothesis: "Affinity outlier root cause is opponent-side defensive scoring" → "Affinity outlier root cause is upstream of the EV scoring layer (mulligan, sideboard policy, or Affinity-side over-credit)."

Frontmatter `status: active`, `priority: primary` so the next session sees this as the hot pointer.
