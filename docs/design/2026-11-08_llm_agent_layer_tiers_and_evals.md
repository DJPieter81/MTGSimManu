---
title: LLM agent layer — model tiers, decision-model routing, typed inputs and full eval coverage
status: active
priority: secondary
session: 2026-11-08
supersedes: []
superseded_by: []
depends_on:
  - docs/design/2026-09-16_agentic_decision_architecture.md
  - docs/design/rules-foundation-sweep-tracker.md
tags: [design, llm, agents, evals, models, decision-model, determinism]
summary: >
  Every LLM task now has a typed output schema, a versioned system prompt, a
  typed input whose renderer is the user prompt, and golden evals (7 of 7 tasks,
  was 3 of 7). Defaults move off the superseded generation: the Opus tier for
  the difficult reasoning tasks, the Sonnet tier for committed tag
  classification, the Haiku tier for doc-freshness extraction, and the
  decision model (TypeSafe's Jev) for the per-decision scorer, with a
  Sonnet-tier fallback when its SDK is absent. Offline sims are unchanged:
  the committed decision weights are keyed by archetype and context, not by
  model.
---

# LLM agent layer — model tiers, decision-model routing, typed inputs, evals

## Why

Two of the three tiers the agent layer defaulted to were a generation
behind (the previous Sonnet and Haiku generations), the hard reasoning
tasks were not on the Opus tier at all, the decision model added as a
dependency was not the default for any task, and four of seven agents had
no eval set. User-prompt rendering lived in three tools as untyped string
building, and the eval harness fed the model raw JSON rather than the
prompt the tools actually send.

## What changed

### 1. Model tiers (`ai/llm_models.py`)

| Task | Tier | Why |
|---|---|---|
| `diagnose_replay` | Opus | Causal reasoning over a long Bo3 log: the difficult decision. |
| `synth_gameplan` | Opus | A multi-constraint strategy for a whole 60-card list. |
| `failing_test_spec` | Opus | Rule-phrasing a red test, where weaker answers name the card. |
| `handler_audit` | Opus | Reading handler source against printed modes; a miss is a silent rules bug. |
| `classify_oracle` | Sonnet | Closed-set tags, but committed and trusted for weeks; one false tag sank the 2026-10-02 A/B. |
| `audit_doc_freshness` | Haiku | Low-stakes extraction over every active doc. |
| `decision_scorer` | Decision model (Jev) | One typed answer per (archetype, context): its native shape. Fallback: Sonnet tier. |

The identifiers live only in `DEFAULT_MODELS` / `FALLBACK_MODELS` and the
price table. The current Opus tier lists below the previous Opus
generation, so the move costs less per token than before. Budget caps were
scaled by each task's price ratio so the same monthly call count fits
(`ai/llm_budgets.py`).

**Provider fallback.** `select_model` returns `FALLBACK_MODELS[task]` when
the default's client SDK cannot be imported (`PROVIDER_SDK_MODULES`). CI
installs no decision-model SDK, so CI resolves the decision scorer to the
fallback. Env vars and explicit overrides are never rewritten.

`classify_oracle` keeps its decision shape, but its default stays on the
Sonnet tier: the decision model's tag cache was measured and rejected
(rules-foundation tracker, 2026-10-02). Re-measure per run with
`MTG_LLM_MODEL_CLASSIFY_ORACLE`.

### 2. The scorer's decision shape (`ai/llm_schemas.py`)

A decision model answers yes/no questions, picks, probabilities in [0, 1]
and small rubrics. It does not give an unbounded float or free text.
`DecisionScoringWeights.weight` is an unbounded float, so on a decision
model the scorer asks one pick-one question over a grid of weights.
The grid spans the schema's documented [0, 20] range at the scorer
prompt's 1dp rounding: 201 options, under the provider's 255-option
cap. Every committed weight lies on the grid. `canonical_output`
(`ai/llm_agents.py`) maps the pick back to `DecisionScoringWeights`,
so the cache, the warm's export and every caller read one shape. The
scorer prompt gained a v3 that is neutral about which fields the model
fills. v2 is unchanged, per the append-only rule.

### 3. Typed inputs (`ai/llm_inputs.py`)

Each task has a strict input model with a pure `render()`. The three tools
that built prompt strings now build these models instead:
`compress_decklist` → `decklist_input`, `build_classify_input` →
`OracleClassifyInput.from_mtgjson`, and `build_freshness_input` →
`freshness_input`. The rendered text is byte-identical to before. This
was verified across every registered decklist plus a DB-miss row, 236
sampled MTGJSON cards plus an empty entry, and 8 docs, so string-keyed
cache rows stay valid. A typed input keys the response cache exactly
like the dict of its fields, so the scorer's lookup and live call
still share one key.

### 4. Evals (`tests/eval/`)

Goldens now cover all 7 tasks (was 3). New sets, each built by the
production builder from real repository data:

- **`decision_scorer` (6 pairs).** Expected is the committed weight; the
  band is the prompt's per-context prior.
- **`classify_oracle` (5 pairs).** Expected is the committed classifier
  tags.
- **`audit_doc_freshness` (3 pairs).** Real docs with the frontmatter
  rewound to `active`: one superseded, one falsified, one that stays
  active.
- **`failing_test_spec` (3 pairs).** The diagnose goldens' hypotheses,
  plus a landed rules unit whose real test file is the naming rule's
  output.

The four synth goldens were re-expressed as feature-row inputs; their
`expected` blocks are unchanged. The harness renders every input with the
production renderer, scores the canonical output, scores numeric fields
against bands and tag sets by Jaccard overlap, and runs offline via
`TestModel`. Real-model runs remain opt-in (`pytest tests/eval/ --run-eval`)
with a floor for every task.

## Determinism

With `MTG_LLM_DECISION_SCORER_OFFLINE=1` the scorer makes no call and reads
`ai/llm_decision_weights.json`, whose rows are keyed by archetype and
context only. `tests/test_llm_models.py` pins that the offline weight is
the committed row under both the default and the fallback model. The
seeded-game digest is identical before and after this change on the same
base (see the commit message for the line).

## Tests

- `tests/test_llm_agent_contract.py`: schema, prompt, renderer, goldens,
  threshold and floor for every task. Also checks that decision shapes
  are askable and within the option cap, and that the grid covers every
  committed weight. One test runs the scorer end to end on the decision
  model's real request path against a stub client (skipped without the
  SDK).
- `tests/test_llm_inputs.py`: renderer formats, golden-input validation,
  the cache-key identity and the failing-test naming rule.
- `tests/test_llm_models.py`, `tests/test_llm_metrics_pricing.py`,
  `tests/test_llm_decision_weights_file.py` and
  `tests/test_eval_harness_smoke.py`: tiers, fallback, current-family
  pricing (including the size-tiered Haiku rate), the decision-model
  cache round trip, and every task's goldens through the harness.
