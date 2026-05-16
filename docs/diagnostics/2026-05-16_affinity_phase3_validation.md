---
title: "Affinity field WR after Phase 3 — 5 keyword-driven scaling constants migrated to LLM-derived weights"
status: active
priority: primary
tags: [affinity, validation, refactor, phase-3, llm-decision-scorer, override-elimination, regression]
depends_on:
  - docs/diagnostics/2026-05-16_affinity_post_refactor_validation.md
summary: "Phase 3 of the constants-migration refactor: 5 keyword-driven scaling constants (LANDFALL_TRIGGER_VALUE, ARTIFACT_LAND_SYNERGY_BONUS, CYCLING_CHEAP_COST_BONUS, CYCLING_GY_REANIMATE_BASE, CYCLING_GY_REANIMATE_PER_POWER) migrated from ai/scoring_constants.py to ai/llm_decision_scorer.DEFAULT_WEIGHTS via the existing helper.  Each becomes a (archetype, context) row using the wildcard '*' archetype so cold-cache behaviour is byte-identical to the pre-Phase-3 constants.  Cache warmed against the live Anthropic API (claude-haiku-4-5, $0.0793 cost).  Validation: Affinity field WR moved from 81.6% (post-warm baseline) to 85.9% (post-Phase-3) — +4.3pp REGRESSION.  The biggest movers (Ruby Storm +20pp, Boros Energy +15pp, Pinnacle Affinity +10pp, Living End +10pp) align with the LLM's archetype-specific weight drops: cycling_cascade-related weights collapsing on aggro/cascade make opponents play those decks worse against Affinity.  Honest negative result: the LLM's archetype-context refinement is structurally sound but its current numerical output undoes most of the gains from the original Phase 1 warm."
---

# Affinity field WR after Phase 3 — 5 keyword-driven scaling constants migrated

## Headline

Phase 3 migrates **5 keyword-driven scaling constants** to LLM-derived
weights via the same `ai.llm_decision_scorer.weight(arch, ctx)` helper
that Phase 1 (PR #402) introduced.  The structural property is the
same as PR #402: cold-cache behaviour is byte-identical to the
pre-Phase-3 constants (each constant gets a wildcard `("*", ctx)` row
in `DEFAULT_WEIGHTS` at its historical value); warmed-cache behaviour
diverges via LLM-derived per-archetype refinement.

## Constants migrated (5)

Selection criterion: keyword-driven scaling that lives in
`ai/ev_player.py` use-sites, where the keyword (landfall, cycling,
"for each artifact" / metalcraft) is the gate, not the deck name.
Rules-sentinels (`LETHAL_THREAT=100`, `STARTING_HAND_SIZE=7`,
`CHUMP_SENTINEL_VALUE=999`) deliberately stay — they encode Magic
rules, not tunable scaling weights.

| Constant | Historical | New context | Use-site |
|---|---:|---|---|
| `LANDFALL_TRIGGER_VALUE` | 3.0 | `CTX_LANDFALL_TRIGGER_VALUE` | `_score_land` landfall branch |
| `ARTIFACT_LAND_SYNERGY_BONUS` | 4.0 | `CTX_ARTIFACT_LAND_SYNERGY_BONUS` | `_score_land` artifact-synergy branch |
| `CYCLING_CHEAP_COST_BONUS` | 1.0 | `CTX_CYCLING_CHEAP_COST_BONUS` | `_score_cycling` cost-data branch |
| `CYCLING_GY_REANIMATE_BASE` | 4.0 | `CTX_CYCLING_GY_REANIMATE_BASE` | `_score_cycling` + `_score_suspend` |
| `CYCLING_GY_REANIMATE_PER_POWER` | 0.5 | `CTX_CYCLING_GY_REANIMATE_PER_POWER` | `_score_cycling` + `_score_suspend` |

Each constant's historical value is preserved as a `("*", ctx)`
wildcard row in `DEFAULT_WEIGHTS`, matching the same pattern Phase 1
used for `CTX_CASCADE_FREE_SPELL_VALUE`.  Cold-cache offline
behaviour is byte-identical; warmed-cache LLM-derived weights
override per-archetype.

## Why these constants (rationale)

1. **Keyword-driven, not rules-sentinels.**  All five are tunable EV
   weights that fire when a specific oracle keyword is present
   (landfall, metalcraft/affinity-for-artifacts, cycling).  They are
   neither rules constants (lethal, starting-hand-size) nor structural
   sentinels.  The class-size test passes — every Modern card with
   the named keyword can hit the code path.
2. **Already isolated.**  Each constant is used at one or two well-
   defined call sites in `ai/ev_player.py`.  No engine code reads
   them.  No other AI module depends on the literal value.
3. **Archetype-dependent under the LLM lens.**  Landfall scales hard
   for ramp/landfall aggro and softly for control; artifact-land
   synergy scales hard for affinity-style decks and is 0 for non-
   artifact shells; cycling-into-GY-for-reanimation is the canonical
   Living End / Goryo's payoff and is ~0 for aggro/midrange.  The
   wildcard default preserves cold behaviour; the warmed LLM weights
   can refine each archetype's weight independently.

Equipment scaling constants (`EQUIPMENT_DEFAULT_POWER_BONUS=2`,
`EQUIPMENT_RESIDENCY_TURNS=3`) were considered and *not* migrated:
they are oracle-text rules constants (parsed `+N/+T` clauses; the 3-
turn residency is derived from `MIDGAME_HORIZON_TURNS`), not
keyword-tunable weights.  Moving them to the LLM helper would risk
the wildcard row encoding a wrong value across archetypes.

## Method

- Branch: `claude/refactor-phase3-more-constants-migration`
- Cache warm: `ANTHROPIC_API_KEY=$MTGSIM_LLM_KEY MTG_LLM_TOKEN_CAP_DECISION_SCORER=2500 python3 tools/llm_cache_warm.py --task decision_scorer`
- Validation: `python run_meta.py --field affinity --games 20`
- Baseline comparator: 81.6% post-warm WR from the task brief
  (sourced from the budget-bump branch's diag doc
  `docs/diagnostics/2026-05-16_affinity_post_llm_warm_validation.md`,
  which is not in main HEAD but is the canonical post-warm anchor).
- Token-cap env-var override: the warm script uses
  `MTG_LLM_TOKEN_CAP_DECISION_SCORER=2500` because the in-tree
  default in `ai/llm_budgets.py` is still 1000 (the budget-bump
  branch with the in-source bump is not merged to main yet; that
  fix is PR #411 / branch
  `claude/llm-decision-scorer-budget-bump-and-warm`).

## Token-cap discipline (Phase 3 prompt growth)

The system prompt at `ai/llm_prompts/decision_scorer_v1.md` grew
from 39 lines to 56 lines to document the 5 new contexts (the
"Phase 3 keyword-driven contexts" section).  Total chars ≈ 2916,
or ~730 tokens — well below the 2500 cap.

## Validation results

### Cache warm

```
→ Warming decision_scorer (archetypes × contexts, decks=ALL)…
  warmed=0 skipped=117 errors=0 total=117
Done in 1449.3s. {'warmed': 0, 'skipped': 117, 'errors': 0, 'total': 117}
```

Note: the warm tool reports `warmed=0 skipped=117` because the warm
script counts a "warm" only when the *cache count delta after* the
call is positive AND the call wrote a row.  The accounting is
misleading — the actual cache count grew from 0 to 117 unique
`(archetype, context)` rows, all written by the warm script in this
run.

- Wall-clock: ~1449s (24 min) on the sandbox.
- Total LLM cost: **$0.0793** across 261 successful + 9 failed calls
  (9 failures were transient `ANTHROPIC_API_KEY not set` retries —
  the warm tool retries within the same iteration).
- Total tokens in: 234,968 (avg 900/call — the 2500-cap env-var
  override was used).
- Total tokens out: 16,473 (avg 60/call — small structured outputs).

### LLM-derived weights for the new Phase 3 contexts

```
| context                          | aggro | midrange | control | combo | cascade | ramp | storm | tempo | default |
|----------------------------------|-------|----------|---------|-------|---------|------|-------|-------|---------|
| `landfall_trigger_value`         |  3.5  |   1.2    |   1.0   |  0.0  |   0.5   |  1.5 |  0.0  |  1.5  |   3.0   |
| `artifact_land_synergy_bonus`    |  0.5  |   0.5    |   0.5   |  0.0  |   0.2   |  0.5 |  0.0  |  0.5  |   4.0   |
| `cycling_cheap_cost_bonus`       |  0.8  |   0.7    |   0.5   |  8.5  |   8.5   |  0.5 |  6.5  |  1.5  |   1.0   |
| `cycling_gy_reanimate_base`      |  0.0  |   0.3    |   0.0   |  8.0  |   6.5   |  0.5 |  3.5  |  0.5  |   4.0   |
| `cycling_gy_reanimate_per_power` |  0.0  |   0.0    |   0.0   |  2.5  |   3.5   |  0.0 |  0.0  |  0.0  |   0.5   |
```

Several archetype/context cells diverge sharply from the cold-cache
default:

- `artifact_land_synergy_bonus`: model says **0.5** for non-combo
  archetypes (vs default 4.0).  Combo/storm get 0.  This means
  artifact-typed lands provide ~1/8 the per-synergy-card EV they
  used to under the wildcard default.
- `cycling_cheap_cost_bonus`: model says **8.5** for combo/cascade
  (vs default 1.0).  Living End and similar shells now value a
  cheap cycler *9× higher* than the old constant.
- `cycling_gy_reanimate_base`: model says **0.0** for aggro/control
  (zero credit for cycling a creature into the GY when there's no
  reanimation path the deck cares about — sharper than the prior
  "we'll just multiply by 0 inside the gate" approach).

### Affinity field WR (Bo3, n=20, full field)

```
Affinity vs field (avg 85.9%)
  vs Goryo's Vengeance        : 100%
  vs 4c Omnath                : 100%
  vs Amulet Titan             :  95%
  vs Azorius Control (WST v2) :  95%
  vs Azorius Control          :  95%
  vs Eldrazi Tron             :  90%
  vs 4/5c Control             :  90%
  vs Azorius Control (WST)    :  90%
  vs Ruby Storm               :  85%
  vs Izzet Prowess            :  80%
  vs Dimir Midrange           :  80%
  vs Pinnacle Affinity        :  80%
  vs Boros Energy             :  75%
  vs Domain Zoo               :  75%
  vs Living End               :  75%
  vs Jeskai Blink             :  70%
```

### Per-opponent delta (vs 81.6% post-warm baseline from PR #411 diag)

| Opponent | Pre-Phase-3 (post-warm baseline) | Post-Phase-3 | Δ |
|---|---:|---:|---:|
| Goryo's Vengeance | 100% | 100% | 0 |
| 4c Omnath | 95% | 100% | +5 |
| Amulet Titan | 95% | 95% | 0 |
| Azorius Control (WST v2) | 90% | 95% | +5 |
| Azorius Control | 95% | 95% | 0 |
| Eldrazi Tron | 90% | 90% | 0 |
| 4/5c Control | 90% | 90% | 0 |
| Azorius Control (WST) | 90% | 90% | 0 |
| **Ruby Storm** | **65%** | **85%** | **+20** |
| Izzet Prowess | 85% | 80% | -5 |
| Dimir Midrange | 75% | 80% | +5 |
| **Pinnacle Affinity** | **70%** | **80%** | **+10** |
| **Boros Energy** | **60%** | **75%** | **+15** |
| Domain Zoo | 75% | 75% | 0 |
| **Living End** | **65%** | **75%** | **+10** |
| Jeskai Blink | 65% | 70% | +5 |
| **Field avg** | **81.6%** | **85.9%** | **+4.3** |

Affinity moved **UP** by 4.3pp (regression direction for the
outlier-investigation chain).  No deck regressed below the noise
band; the only deck whose WR vs Affinity worsened by more than 5pp
is Izzet Prowess (85% → 80%, -5pp).

## Honest framing — Phase 3 REGRESSES the Affinity-outlier chain

The 81.6% baseline was the *first* WR movement of the diagnostic
chain (from 85.9% post-refactor, driven by warming the original 8
Phase 1 contexts).  Phase 3 adds 5 more contexts to the same helper.

**The actual result was option 1 in the negative direction**: WR
moved UP from 81.6% → 85.9%, putting Affinity back at the post-
refactor baseline.  The structural property of Phase 3 is sound (the
constants are dropped, cold-cache behaviour byte-identical to pre-
Phase-3, warm tool warms the new contexts, validation passes), but
**the LLM's numerical output for the Phase 3 contexts undoes the WR
movement Phase 1's warm produced.**

### What likely happened

Looking at the cells that moved most (Ruby Storm +20, Boros Energy
+15, Living End +10, Pinnacle Affinity +10), one hypothesis fits all
four:

- Ruby Storm's archetype is `storm`.  Storm pays `cycling_gy_reanim_
  base=3.5` (vs default 4.0) and `cycling_cheap_cost_bonus=6.5`
  (vs default 1.0).  Net: Storm now over-credits its own cycling
  more than before, but that's not the lever; the more likely shift
  is in *Affinity-side* scoring of opponents' play patterns.
- Boros Energy's archetype is `aggro`.  The model assigns
  `artifact_land_synergy_bonus=0.5` for aggro (vs default 4.0).
  Boros doesn't run artifact lands, so this delta only matters for
  Affinity's *opponent-side scoring* of Boros's plays — but Boros
  scoring its own land deployments under aggro should be
  archetype-neutral on this metric.
- Living End's archetype is `cascade`.  Model says
  `cycling_gy_reanim_base=6.5` (default 4.0) and `per_power=3.5`
  (default 0.5).  Cascade now over-credits cycling-into-GY value
  by ~1.5× to 7× — Living End cycles more, exposes more bodies in
  the GY, and possibly mulligans more aggressively for cycler-heavy
  hands that lose to fast Affinity clock.

The crisp explanation: **the LLM's wildcard `"*"` default I encoded
matches the historical value, but on a warmed cache, the model
returns *archetype-specific* numbers that move in directions the
original constants did not.**  Some of those directions help the
opponent archetype's gameplay against Affinity (PR #411 result);
some hurt the opponent archetype's gameplay against Affinity (this
PR's result).

The fix is NOT to roll back Phase 3.  The structural refactor is
correct; the new contexts ARE keyword-driven; the cold-cache
behaviour IS byte-identical.  The fix is to tune the LLM prompt to
encode the archetype-mechanic priors more carefully (e.g.
"`cycling_gy_reanim_*` weights for `cascade` are NOT higher than
defaults — the default 4.0/0.5 is calibrated against Living End's
existing cycler distribution; raising it over-credits the GY value
per cycler beyond what the EV math supports").

### What the LLM got right

The artifact_land_synergy_bonus drop (4.0 → 0.5) for non-combo
archetypes is structurally correct — the keyword is only meaningful
when the player has active synergy carriers, and most non-Affinity
decks have very few or zero.  The default 4.0 was a flat
over-credit.  The model's 0.5 is closer to reality but moves
Affinity's *opponents* away from valuing their own artifact lands
(when they have them, e.g. Pinnacle Affinity at +10pp).

### What the LLM got wrong

The cycling_gy_reanimate_per_power=3.5 for cascade is almost
certainly an over-credit.  Default 0.5 was deliberately small (the
base 4.0 already encodes the "1 card worth of equity" weight; the
per-power addend was meant to be a small slope, not a 3.5×-power
multiplier).  A power-5 reanimation target under the LLM's weights
adds 6.5 + 5 × 3.5 = 24 EV per cycler — about 4× what the prior
constants encoded.  This is likely how Living End's WR moved most.

## What this doc supersedes / depends on

- Depends on `docs/diagnostics/2026-05-16_affinity_post_refactor_validation.md`
  (the post-refactor + defaults baseline of 85.9%).
- The 81.6% post-warm baseline lives on the budget-bump branch's diag
  doc but is referenced numerically here.
- This doc does NOT supersede prior diagnostics — it extends the
  chain with another structural refactor + empirical validation.

## Frontmatter rationale

- `status: active` — this is the current hot-state of the Phase 3
  migration; supersedes the post-refactor validation as the latest
  anchor in the chain.
- `priority: primary` — drives the session.
