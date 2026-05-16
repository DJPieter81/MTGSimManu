---
title: "Phase 3 constants migration retry on v2 prompt + full cache warm"
status: active
priority: primary
tags: [affinity, validation, refactor, phase-3, llm-decision-scorer, prompt-tuning, retry, warm]
depends_on:
  - docs/diagnostics/2026-05-16_affinity_phase3_v2_prompt_tuned.md
supersedes: []
summary: "Retry of the parked Phase 3 migration (323dd53) on top of the v2 prompt (393a070) with a full 117/117 cache warm.  The earlier v2 ship was deferred because the partial warm (66/117) left storm/cascade/tempo on wildcard defaults, leaving a +0.3pp residual.  This branch completes the warm, sanity-checks LLM-derived weights for all 5 Phase 3 contexts (no inflation > 2× default), and re-validates Affinity field WR against the 81.6% baseline.  Result: see Acceptance gate section."
---

# Phase 3 constants migration on v2 prompt — empirical validation

## Headline

The 5 keyword-driven scaling constants (`LANDFALL_TRIGGER_VALUE`,
`ARTIFACT_LAND_SYNERGY_BONUS`, `CYCLING_CHEAP_COST_BONUS`,
`CYCLING_GY_REANIMATE_BASE`, `CYCLING_GY_REANIMATE_PER_POWER`) drop
from `ai/scoring_constants.py`; each use-site in `ai/ev_player.py`
now reads via `ai.llm_decision_scorer.weight(archetype, CTX_*)`.

Cache is fully warmed (117/117 rows under prompt v2).  LLM
calibration discipline holds: no Phase 3 context exceeds 2× its
default for any archetype.  The parked branch's cascade
`cycling_gy_reanimate_per_power = 3.5` over-credit (7× default)
collapses to 0.5 (exact default) under v2.

## What changed in code

### `ai/scoring_constants.py`
- Removed 5 named constants (`LANDFALL_TRIGGER_VALUE`,
  `ARTIFACT_LAND_SYNERGY_BONUS`, `CYCLING_CHEAP_COST_BONUS`,
  `CYCLING_GY_REANIMATE_BASE`, `CYCLING_GY_REANIMATE_PER_POWER`).
- Replaced with a documented "DROPPED in Phase 3" block referencing
  `ai/llm_decision_scorer.py`'s `DEFAULT_WEIGHTS` table.

### `ai/llm_decision_scorer.py`
- Added 5 `CTX_*` symbols.
- Added 5 wildcard rows in `DEFAULT_WEIGHTS` at the historical values
  (3.0, 4.0, 1.0, 4.0, 0.5).
- Added the 5 symbols to `__all__`.

### `ai/ev_player.py`
- Removed 5 imports from `ai.scoring_constants`.
- Added 5 `CTX_*` imports from `ai.llm_decision_scorer`.
- Updated 4 use-sites in `_score_land` (landfall + artifact synergy),
  `_score_cycling` (cheap-cost + GY reanimation base + per-power),
  and `_score_suspend` (per-creature reanimation EV) to call
  `_llm_weight(self.archetype, CTX_*)`.

### `tools/llm_cache_warm.py`
- Extended `_iter_decision_scorer_contexts` to include the 5 new
  contexts.  Grid grows from 9 × 8 = 72 rows to 9 × 13 = 117 rows.

### `tests/test_phase3_constants_migration.py` (NEW)
- 7 tests pinning the migration rule for each of the 5 constants
  + finiteness + warm-iteration coverage.  Cherry-picked from
  parked branch (`323dd53`).

### `tests/test_ev_player_constants_linkage.py`
- Removed 5 parametrize entries + 5 REQUIRED_IMPORTS for the
  migrated constants (now sourced from `llm_decision_scorer`).

### `tests/test_artifact_land_synergy_excludes_hand.py`
- Added autouse fixture forcing offline mode + bypassing the
  SQLite cache so the test is deterministic against any warmed
  state.

## Cold-cache parity

The 5 wildcard `(*, ctx)` rows in `DEFAULT_WEIGHTS` carry the
historical constant values verbatim.  When no archetype-specific
row is present and no cache hit is available, the helper falls
back to `_lookup_default("*", ctx)`, which returns the historical
value.  Cold-cache behaviour is therefore byte-identical to the
pre-Phase-3 constants.

## Cache warm

```
ANTHROPIC_API_KEY="$MTGSIM_LLM_KEY" \
  MTG_LLM_TOKEN_CAP_DECISION_SCORER=6000 \
  python3 tools/llm_cache_warm.py --task decision_scorer
```

- Initial pass: 1944.8s wall-clock, 104 rows populated.
  13 calls raised pydantic-ai schema-validation errors and were
  silently skipped (the agent layer is fail-soft per the existing
  contract).
- Second pass (idempotent retry over the 13 missing rows):
  185.3s, 13 rows added.
- Total: 2130s wall-clock (~36 min), 117/117 rows.
- Cost: ~$0.04 (117 calls × ~$0.00035 each on claude-haiku-4-5,
  haiku pricing × ~600 input tokens average per call after
  pydantic-ai schema/tool-definition overhead).

The v2 prompt fits within the `MTG_LLM_TOKEN_CAP_DECISION_SCORER=6000`
input cap; the default 1000-token cap silently rate-limited under v1
(see commit `d279ab9` — historical context).

## LLM weights observed (full v2-warmed cache)

```
context                              aggro  midrange   control     combo   cascade      ramp     storm     tempo
landfall_trigger_value                3.00      3.00      1.00      1.00      0.50      3.00      0.50      3.00
artifact_land_synergy_bonus           4.00      4.00      2.00      1.00      1.00      4.00      0.50      4.00
cycling_cheap_cost_bonus              1.00      1.00      0.50      1.50      1.50      0.50      1.50      1.50
cycling_gy_reanimate_base             0.00      0.50      0.50      4.00      4.50      0.00      0.00      0.50
cycling_gy_reanimate_per_power        0.10      0.30      0.00      0.50      0.50      0.30      0.10      0.10

(defaults: 3.0 / 4.0 / 1.0 / 4.0 / 0.5)
```

Critical comparison vs the parked v1 warm (323dd53 diag):

| context | v1-warmed (parked, 323dd53) | v2-warmed (this) | Δ |
|---|---:|---:|---:|
| cascade `cycling_gy_reanimate_per_power` | 3.5 (7× default) | 0.5 (default) | -3.0 |
| combo `cycling_gy_reanimate_per_power` | 2.5 (5×) | 0.5 (default) | -2.0 |
| cascade `cycling_gy_reanimate_base` | 6.5 | 4.5 | -2.0 |
| combo `cycling_gy_reanimate_base` | 8.0 (2×) | 4.0 (default) | -4.0 |
| combo `cycling_cheap_cost_bonus` | 8.5 (8.5×) | 1.5 | -7.0 |
| midrange `artifact_land_synergy_bonus` | 0.5 | 4.0 (default) | +3.5 |
| aggro `artifact_land_synergy_bonus` | 0.5 | 4.0 (default) | +3.5 |

The v2 prompt's calibration discipline + double-count guard + per-X
slope bound successfully eliminated every over-credit on the Phase 3
contexts.  The largest remaining divergence is cascade's
`cycling_gy_reanimate_base = 4.5` (1.125× default), inside the v2
prompt's stated `3.0..5.0 cascade/combo` band.

## Probe gate: no Phase 3 context exceeds 2× default

For 5 contexts × 8 archetypes = 40 cells:
- Zero cells exceed 2× their default value.
- The strictest comparison `|cached| > 2 × |default|` finds no
  candidates.
- Downward revisions (storm `landfall = 0.5` vs default 3.0, combo
  `artifact_land_synergy = 1.0` vs default 4.0) are mechanically
  sound — non-landfall, non-artifact decks should under-weight
  these contexts.

The probe was run inline (not committed as a tool) because the task
brief whitelist did not include a probe script.  Reproduction:

```python
import os
os.environ.pop("MTG_LLM_DECISION_SCORER_OFFLINE", None)
from ai import llm_cache
from ai.llm_models import select_model
from ai.llm_prompts import latest_version
from ai.llm_schemas import DecisionScoringWeights

model = select_model("decision_scorer")
version = latest_version("decision_scorer")  # → 2

def probe(arch, ctx):
    key = llm_cache.cache_key("decision_scorer", model, version,
                              f"archetype={arch}; context={ctx}")
    hit = llm_cache.get_cached(key, DecisionScoringWeights)
    return None if hit is None else float(hit.weight)
```

The cache lookup uses the **string** form
`f"archetype=...; context=..."` because `CachedAgent` (in
`ai/llm_agents.py`) stores responses under that key — not the
dict key returned by `llm_decision_scorer._cache_input`.  This is
an existing pre-Phase-1 quirk in the caching shim; the `weight()`
hot path round-trips through `agent.run_sync` which uses the
matching string key, so the warmed cache *is* consulted at sim
time despite `_try_cache_only` always missing.

## Affinity field WR (Bo3, n=20)

```
Affinity vs field (avg 81.9%)
  vs Goryo's Vengeance        : 100%
  vs Amulet Titan             :  95%
  vs 4c Omnath                :  95%
  vs Azorius Control          :  95%
  vs Eldrazi Tron             :  90%
  vs 4/5c Control             :  90%
  vs Azorius Control (WST)    :  90%
  vs Azorius Control (WST v2) :  90%
  vs Izzet Prowess            :  85%
  vs Domain Zoo               :  75%
  vs Dimir Midrange           :  75%
  vs Living End               :  70%
  vs Pinnacle Affinity        :  70%
  vs Jeskai Blink             :  65%
  vs Ruby Storm               :  65%
  vs Boros Energy             :  60%
```

### Per-opponent comparison vs anchors

| Opponent | v1 baseline (81.6%) | parked v1 Phase 3 (85.9%) | v2 partial warm (81.9%, 66/117) | v2 full warm + Phase 3 (this) | Δ baseline |
|---|---:|---:|---:|---:|---:|
| Goryo's Vengeance | 100 | 100 | 100 | 100 | 0 |
| Amulet Titan | 95 | 95 | 95 | 95 | 0 |
| 4c Omnath | 95 | 100 | 95 | 95 | 0 |
| Azorius Control | 95 | 95 | 95 | 95 | 0 |
| Eldrazi Tron | 90 | 90 | 90 | 90 | 0 |
| 4/5c Control | 90 | 90 | 90 | 90 | 0 |
| Az WST | 90 | 90 | 90 | 90 | 0 |
| Az WST v2 | 90 | 95 | 90 | 90 | 0 |
| Izzet Prowess | 85 | 80 | 85 | 85 | 0 |
| Dimir Midrange | 75 | 80 | 75 | 75 | 0 |
| Domain Zoo | 75 | 75 | 75 | 75 | 0 |
| Living End | 65 | 75 | 70 | 70 | **+5** |
| Pinnacle Affinity | 70 | 80 | 70 | 70 | 0 |
| Jeskai Blink | 65 | 70 | 65 | 65 | 0 |
| Ruby Storm | 65 | 85 | 65 | 65 | 0 |
| Boros Energy | 60 | 75 | 60 | 60 | 0 |
| **Field avg** | **81.6** | **85.9** | **81.9** | **81.9** | **+0.3** |

The full warm leaves every matchup identical to the partial-warm
anchor.  Interpretation: the 51 rows the partial warm skipped
(storm/cascade/tempo) did not affect Affinity matchups because

- Affinity itself is `aggro`, so its scoring uses aggro/midrange-ish
  weights regardless of opponent identity.
- Affinity's opponents' archetypes that change behavior under the
  new contexts are mostly cascade/combo (Living End, Ruby Storm,
  Goryo's Vengeance).  Those opponents' scoring DOES change with
  the new warm — but the EV deltas net out within the simulator
  noise for n=20 Bo3.
- Living End's +5pp residual vs baseline (65 → 70) is unchanged
  between partial and full warm, suggesting it is stochastic
  variance for n=20 Bo3 (per-matchup σ ≈ √(0.65×0.35/20) ≈ 11pp).

## Acceptance gate verdict

**Soft gate (task brief)**: Affinity field WR ∈ [79.6%, 83.6%]
(±2pp of 81.6% baseline = Bo3 n=20 noise band) → 81.9% is inside
the band → **PASS**.

**Strict gate (≤ 81.6%)**: 81.9% > 81.6% by 0.3pp → misses by less
than half the σ.  This is the same outcome as the parked v2 partial
warm.  Per the task brief's tolerance rule ("treat within-noise as
pass"), the migration ships.

**Movement summary**:
- v1 baseline → parked v1 Phase 3:        +4.3pp regression (HARD FAIL).
- parked v1 Phase 3 → v2 prompt:          -4.0pp recovery.
- v2 partial warm → v2 full warm:         ±0.0pp (no movement).
- v1 baseline → this commit (full warm):  +0.3pp (within noise).

The empirical loop has converged.  The v2 prompt + Phase 3 use-site
migration is the structurally correct end state: 5 keyword-driven
constants now sourced from the LLM helper with proper calibration
discipline.

## Ratchets + tests

- `python tools/check_abstraction.py` → exit 0
- `python tools/check_magic_numbers.py` → exit 0 (total 13, baseline 13;
  the 5 dropped constants were in `ai/scoring_constants.py` which is
  excluded from the magic-number scan, so the count is unchanged
  per the parked branch's note).
- `python tools/check_doc_hygiene.py` → exit 0
- `python -m pytest tests/test_phase3_constants_migration.py
                    tests/test_ev_player_constants_linkage.py
                    tests/test_llm_decision_scorer.py
                    tests/test_abstraction_contract.py -q`
  → all green.

## What this doc supersedes / depends on

- Builds on `docs/diagnostics/2026-05-16_affinity_phase3_v2_prompt_tuned.md`
  (which shipped the v2 prompt standalone after the partial 66/117
  warm).  Cache is now fully warmed (117/117) under the same v2
  prompt; the Phase 3 use-site changes land on top.
- Mirrors the diff structure of the parked branch `323dd53`; the
  diff is structurally identical, only the cache contents differ
  (v1 warm → over-credits; v2 warm → calibration discipline holds).

## Frontmatter rationale

- `status: active` — drives the Phase 3 migration decision.
- `priority: primary` — supersedes the deferred-migration note in
  the v2-prompt-only diag.
