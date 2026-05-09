---
title: "Phase 4C — Small Language Model in-VM oracle parser scoping"
status: active
priority: secondary
session: 2026-05-09
depends_on:
  - docs/research/2026-05_mtg_ai_landscape.md
tags: [research, ai, slm, llm, oracle, prototype]
summary: >
  Concrete plan for a fine-tuned 7B-class SLM running CPU-only in
  a VM, focused on oracle parsing and sideboard plan generation
  for the bounded MTG corpus. Architecture, model selection,
  determinism strategy, latency budget.
---

# Phase 4C — SLM in-VM Oracle Parser

## Why a domain-specific SLM works for MTG

The MTG corpus is **bounded and small**:

| Source | Approximate size |
|---|---|
| Comprehensive Rules | ~300 pages plain text |
| Modern card pool | ~21,000 templates |
| Format rules + bans | ~50 pages |
| Tournament SB guides (free public) | ~10⁵ examples |
| Replay logs from this engine | ~10² games and growing |

Total fine-tune corpus: **a few MB of text**. This is comfortably
inside the context that a 7B-class model can specialize on without
instruction-tuning forgetting. Fine-tuning is optional; even a
zero-shot Qwen 2.5 7B Instruct produces structured output if the
prompt is well-designed.

## What the SLM does (and doesn't do)

### Does

1. **Oracle parsing** — replaces the regex-based
   `engine/oracle_parser.py` for cards flagged "needs review".
   Outputs structured `OracleEffect` with the same shape as the
   regex parser. Catches edge cases the regex misses:
   - "and/or" conjunctions (Nettlecyst's "artifact and/or
     enchantment")
   - "for each ... unless" gates
   - Multi-clause activated abilities with cost variants
2. **Sideboard plan generation** — given a (my deck, my SB,
   opponent deck) tuple, returns a structured swap plan. Phase
   2A's categorization tree backed by SLM rather than rule.
3. **Mulligan advice** — given a 7-card hand and deck name,
   returns keep/mull + reasoning.

### Doesn't

- **Per-decision hot-loop scoring** (too slow, even at 30 tok/s).
- **Combat math / game-tree search** (use MCTS for that).
- **Card-specific scoring overrides** (would re-introduce the
  hardcoding pattern CLAUDE.md prohibits).

## Architecture

### Hosting

- **Local VM**, CPU-only first. `llama.cpp` / `ggml` backend.
- 16-24 GB RAM target (model + cache + Python runtime).
- Optional GPU acceleration via CUDA / Metal — 2–10× speedup,
  not required for production.
- **No external API**. All inference is local; no data leaves
  the VM.

### Model selection

| Model | Size | RAM (Q4_K_M) | CPU tok/s | Notes |
|---|---|---|---|---|
| **Qwen 2.5 7B Instruct** | 7B | ~4.5 GB | 25-35 | First choice. Strong structured output. |
| Phi-3.5 Mini Instruct | 3.8B | ~2.5 GB | 40-60 | Fast, smaller corpus capacity. |
| Llama 3.2 8B | 8B | ~5 GB | 20-30 | Solid baseline. |
| Gemma 2 9B Instruct | 9B | ~5.5 GB | 18-25 | Strong reasoning. |
| Mistral 7B Instruct v0.3 | 7B | ~4.5 GB | 25-35 | Fallback. |

**Decision**: Qwen 2.5 7B Instruct as primary. Phi-3.5 as
secondary fallback when latency-critical.

### Quantization

- **Q4_K_M** for production (best accuracy / size tradeoff).
- Q5_K_M for high-stakes inference (oracle review on borderline
  cards).
- Q8_0 for ground-truth comparison runs only.

### Determinism (critical for matrix reproducibility)

1. **Sampler config**: `temperature=0`, `top_k=1`,
   `top_p=1.0`, fixed `seed`. Greedy decode.
2. **Cache by prompt hash**: SHA-256 of (model_name, quant,
   prompt) → response. Stored in
   `.cache/llm_responses/<hash>.json`.
3. **Cache hit path**: zero-cost lookup, deterministic output
   guaranteed. Matrix reproducibility preserved.
4. **Cache miss path**: invoke model, store response. Logged so
   matrix runs can identify when new cards trigger fresh
   inference.

### Inference interface

```python
# ai/llm/policy.py

class LLMPolicy:
    """Local SLM wrapper with cache + structured-output guarantee."""

    def __init__(self, model_name: str = "qwen2.5-7b-instruct",
                 quant: str = "q4_k_m",
                 cache_dir: str = ".cache/llm_responses"):
        ...

    def generate(self, prompt: str, schema: dict | type) -> Any:
        """Run greedy decode with JSON-schema enforcement.

        Caches by SHA-256 of (model, quant, prompt). Cache hit
        returns parsed object; cache miss invokes model, parses
        per schema, stores.
        """
        ...
```

### Specialized callers

| Module | Schema |
|---|---|
| `ai/llm/oracle_parse.py` | `OracleEffect` (mirrors `engine.oracle_parser` output) |
| `ai/llm/sideboard_advisor.py` | `SideboardPlan` (list of `(card, +/-N)` swaps) |
| `ai/llm/mulligan_advisor.py` | `MulliganDecision` (keep/mull + reasoning) |

Each caller composes its own prompt template; all share the
`LLMPolicy.generate` cache.

## Latency budget

Matrix sim runs ~0.7 sec / Bo3 match. To preserve matrix
throughput:

- **SLM is invoked at most once per game start** (not per
  decision):
  - Sideboard plan: 1 call per Bo3 match (G1 doesn't sideboard,
    G2/G3 share the plan).
  - Oracle parsing: amortized across all games — once per
    unique card-name, cached forever.
  - Mulligan advice: optional (only if heuristic flags low
    confidence).
- **Cache pre-warm**: at session start, pre-warm the cache for
  all 16 deck × 16 deck = 256 sideboard plans. ~256 × 30 sec =
  ~2 hours one-time cost. Subsequent matrix runs hit the cache.
- **Hot-loop budget**: 0 sec / decision. SLM never enters the
  per-turn evaluation loop.

## Phase 4C deliverables

1. `docs/research/2026-05_phase_4c_slm_scoping.md` — this doc.
2. `ai/llm/policy.py` — `LLMPolicy` skeleton with cache.
3. `ai/llm/oracle_parse.py` — first concrete caller.
4. `tests/fixtures/oracle_corpus_known_outputs.jsonl` — 200
   hand-labeled oracle texts with expected structured-effect
   outputs. Acceptance: ≥ 95% agreement at temperature 0.
5. `ai/llm/sideboard_advisor.py` — second caller.
6. `tests/fixtures/sb_golden_plans.jsonl` — for each of 16
   matchups, a community-sourced "good plan". Acceptance:
   SLM output overlaps ≥ 70% of swaps with the canonical plan.
7. `tools/llm_cache_warm.py` — pre-warm cache for 256 SB
   plans, idempotent.

## Acceptance gates (no matrix runs)

Each gate runs in seconds (cache hit path) or minutes (cold
path).

| Gate | Test | Wall clock |
|---|---|---|
| Oracle agreement | `tests/test_llm_oracle_parse_agreement.py` — 200-card corpus | < 10 sec (cache) / ~10 min (cold) |
| SB plan overlap | `tests/test_llm_sb_plan_overlap.py` — 16 matchups | < 60 sec (cache) / ~8 min (cold) |
| Determinism | `tests/test_llm_determinism.py` — repeat same prompt 10× | < 5 sec |
| Cache integrity | `tests/test_llm_cache_roundtrip.py` — write/read invariants | < 2 sec |

## Decision gates

After 4C lands:

- If oracle parser beats regex by > 5% accuracy on the labeled
  corpus, escalate to "default for review-flagged cards".
- If SB advisor matches canonical plans on > 70% of matchups,
  promote to opt-in production via env var `SB_SOLVER=slm`.
- If both, the heuristic ratchet effectively retires for these
  decision surfaces.

## Sequencing within Q3 2026

```
Week 1: LLMPolicy skeleton + cache infrastructure.
        Determinism tests pass on Qwen 2.5 7B Instruct CPU.
Week 2: oracle_parse.py caller + 200-card labeled corpus.
        Agreement gate passing.
Week 3: sideboard_advisor.py caller + 16-matchup canonical
        plans. Overlap gate passing.
Week 4: tools/llm_cache_warm.py + integration as opt-in.
        Documentation. Open PR.
```

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Q4_K_M loses too much accuracy | Low | Compare Q4 vs Q8 on the 200-card corpus. If gap > 3%, fall back to Q5. |
| Greedy decode produces brittle outputs | Medium | JSON schema enforcement via `outlines` rejects malformed; retry once at temp=0.3 if so. |
| Cache invalidation on model upgrade | Medium | Cache key includes model name + quant. Upgrade = new cache namespace, old cache still valid for old runs. |
| 16×16 SB plan pre-warm too slow | Low | Run overnight, commit cache snapshot. |
| Tournament-report corpus noisy | High | Curate only 5 high-quality sources. Treat as supervised, not zero-shot. |

## Out of scope

- Fine-tuning. Zero-shot is sufficient for these tasks; fine-
  tuning adds 1-2 weeks of work for marginal accuracy gain.
  Revisit if zero-shot agreement < 90% on the labeled corpus.
- Reinforcement-learning from replay logs. Phase 5+ work.
- Multi-turn conversation. Each call is single-turn structured
  output.
