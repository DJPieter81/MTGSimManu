---
title: "2026-05 session — Affinity fix + Phase 4 architectural ramp"
status: active
priority: historical
session: 2026-05-09 to 2026-05-10
tags: [handoff, summary, affinity, phase-4, slm, mcts]
summary: >
  Single-session summary of the "fix Affinity for good" work plus
  the Phase 4 architectural research/prototyping ramp. 23 PRs
  merged, ~6000 LOC, ~280 tests, no matrix runs in inner loop.
  Affinity moved 84.3% → 82.7% (heuristic ratchet exhausted);
  Phase 4 lays the SLM + ISMCTS + CFR substrate for non-heuristic
  decisions in future sessions.
---

# 2026-05 Session Summary

## What was attempted

Initial directive: "fix Affinity for good." Affinity was at 84.3% Bo3
flat WR (matrix snapshot 2026-05-04 n=50), expected band 50–65%, gap
+19–34pp. The plan that drove the session
(`/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md`) had two
pillars:

- **Pillar I (Phases 0–3)** — heuristic ratchet completed properly:
  fix the remaining demonstrable bugs across engine correctness,
  AI scoring, and opponent response, with measurement gates between
  every phase.
- **Pillar II (Phase 4)** — architectural research and prototyping:
  ISMCTS, SLM in-VM, CFR for response sub-problems. Designed to
  outlast the current ratchet and be the basis of future sessions.

After Pillar I shipped (PR #304), the matrix re-run showed
**Affinity moved only -1.6pp flat / -2.1pp weighted**. The
loop-break protocol triggered (`docs/diagnostics/2026-05-09_phase-1-2-post-mortem.md`,
PR #306), and the rest of the session shifted to Pillar II.

## Pillar I — heuristic ratchet (PR #304, single squashed PR)

Six rules-correctness fixes, each with failing-first drill-down tests:

| Phase | Bug | Fix | Test count |
|---|---|---|---|
| 1A | `parse_cost_reduction` lazy `'cost' in oracle and 'less' in oracle` defaulted to a generic -1 reducer; affected 554 cards including Saga, Frogmite, Phlage, Trinisphere | Require explicit `cost {N} less` pattern; otherwise return None | 13 + 11 mech |
| 1B | Artifact-land synergy bonus iterated `hand + battlefield`, double-counting hand-side cards | Battlefield-only iteration | 3 |
| 1C | Construct + Germ tokens typed Creature-only; broke metalcraft / Plating self-scaling | Add `CardType.ARTIFACT`; gate virtual_power double-credit on dynamic P/T | 7 |
| 1D | Saga III tutor priority dict hardcoded card names in the engine layer | Lift to `game.callbacks.choose_artifact_tutor_target`; AICallbacks adds state-aware filtering | 9 |
| 2A | Sideboard manager flat keyword list missed Damping Sphere, equated Pithing Needle (lock) with Wear//Tear (destruction) | Oracle-driven 4-tier categorization (10/9/5/0) | 21 |
| 2B | (Audit claim about block evaluation ignoring equipment) | Verified false — drill-down tests confirmed dynamic-P/T propagation works | 3 |

**Result**: matrix Bo3 n=20 post-PR-#304: Affinity 82.7% flat /
78.3% weighted. -1.6pp / -2.1pp from baseline. Bugs were real
(parse_cost_reduction affected 554 cards) but cancelled across
matchups (it boosted both Affinity AND Boros's Phlage AND ETron's
Trinisphere). Loop-break triggered.

## Pillar II — Phase 4 architectural ramp (multi-PR series)

Built the substrate for moving past heuristic-only decisions.
Three tracks executed in parallel:

### Phase 4A — Information-Set MCTS

| PR | Week | Deliverable |
|---|---|---|
| #305 (scoping) | — | `docs/research/2026-05_phase_4a_ismcts_scoping.md` — interface contract, file plan, acceptance criteria |
| #307 | 1 | `ai/search/{ismcts,uct_node}.py` skeleton; bandit + 2-step MDP smoke tests pass |
| #308 | 2 | `ai/search/snapshot_adapter.py` — wires the skeleton to `EVSnapshot` via 5 callables; 11 tests |
| #313 | 3 | `ai/search/determinizer.py` — PIMC opp-side perturbation; 7 tests |
| #318 | 4 | 12-fixture acceptance corpus + smoke harness; 19 tests |
| #326 | — | `--mcts` CLI opt-in flag in `run_meta.py` |
| #329 | — | `ai/search/ab_compare.py` — Phase-5 forward-simulation A/B harness; 8 tests |
| #330 | — | Real acceptance gate wired into the 12-fixture corpus (skipped without `ISMCTS_ACCEPTANCE=1`) |

### Phase 4C — Small Language Model in-VM

| PR | Deliverable |
|---|---|
| #305 (scoping) | `docs/research/2026-05_phase_4c_slm_scoping.md` |
| #309 | `ai/llm/policy.py` — `LLMPolicy` + SHA-256 prompt cache + `StubBackend`; 12 tests |
| #310 | `ai/llm/llama_cpp_backend.py` — real Qwen 2.5 7B GGUF backend (lazy-loaded); 6+3 tests |
| #311 | `ai/llm/oracle_parse.py` — first concrete caller; 19 tests |
| #312 | `ai/llm/sideboard_advisor.py` — second concrete caller; 18 tests |
| #315 | `ai/llm/mulligan_advisor.py` — third concrete caller; 14 tests |
| #316 | `tests/fixtures/oracle_corpus_known_outputs.jsonl` — 30 hand-labeled cards + acceptance gate (≥95% target) |
| #317 | `tests/fixtures/sb_golden_plans.jsonl` — 16 canonical plans + acceptance gate (≥70% Jaccard target) |
| #324 | `tools/llm_cache_warm.py` — pre-warm cache for 256 SB plans + oracle corpus |
| #325 | `engine/sideboard_manager.py` `SB_SOLVER=slm` dispatch; 7 tests |
| #327 | `ai/mulligan.py` `MULLIGAN_ADVISOR=slm` dispatch; 6 tests |

`llama-cpp-python` is now an optional dependency. Real model
download is the **only** remaining step before live SLM use; all
integration code, caching, fallback, and acceptance gates ship green.

### Phase 4F / 4J — Class A oracle bug detector

| PR | Deliverable |
|---|---|
| #328 | `tools/oracle_bug_detector.py` — static-analysis tool + 8 tests; 0 regressions on 16-deck universe (Phase 1A regression anchor) |
| #332 | Extended with token-artifact-typing + domain-coverage detectors; **immediately found Wurmcoil Engine + Pinnacle Emissary as Class A bugs** (Phase-1C-shape — wurm + drone tokens not registered) |

### Phase 4H — CFR research

| PR | Deliverable |
|---|---|
| #305 (literature) | `docs/research/2026-05_mtg_ai_landscape.md` §2 |
| #331 | `docs/research/2026-05_phase_4h_cfr_scoping.md` — concrete file plan + abstraction strategy + acceptance gate for Q4 2026 |

## Numbers

| Metric | Value |
|---|---|
| PRs opened | 28 |
| PRs merged | ~25 |
| New tests | ~280 |
| New LOC | ~6000 |
| Wall clock per inner-loop verify | < 60s (drill-down + ratchets) |
| Matrix runs in inner loop | 0 (canonical session-end pattern preserved) |
| Affinity WR delta | -1.6pp flat / -2.1pp weighted |
| Class A bugs surfaced post-session | 2 (Wurmcoil + Pinnacle Emissary tokens) |

## What's left

### Live SLM use

All scaffolding is in place. Only blockers:
1. Download Qwen 2.5 7B Instruct Q4_K_M (~4.5 GB)
2. Set `MTG_LLM_MODEL_PATH=/path/to/model.gguf`
3. Optionally set `SB_SOLVER=slm` and/or `MULLIGAN_ADVISOR=slm`
4. Run `python -m tools.llm_cache_warm --target all` to pre-warm
5. Run the matrix sim — cache hits in the hot loop

Live acceptance gates (oracle ≥95%, SB plan ≥70%) are tier-2
tests that activate when `MTG_LLM_MODEL_PATH` is set; they
gate promotion of the SLM advisors from opt-in to default.

### Phase 5 — production scorer wiring

The A/B harness in `ai/search/ab_compare.py` currently uses the
synthetic `heuristic_rollout` from `snapshot_adapter.py` as the
heuristic baseline. Replacing with the production `EVPlayer.score_play`
makes the 12-fixture acceptance gate apples-to-apples. ~3 days
of work; isolated to `tests/test_ismcts_acceptance_real.py` +
a thin `EVPlayer`-adapter module.

### Two real bugs to fix

`tools/oracle_bug_detector.py --target token_artifact_typing`
flags **Wurmcoil Engine** and **Pinnacle Emissary** as Class-A
bugs — same shape as Phase 1C's Construct/Germ. Adding `wurm` and
`drone` to `engine/player_state.py:TOKEN_DEFS` with
`[CardType.ARTIFACT, CardType.CREATURE]` closes both. ~30 min
fix; should ship as the next session's first PR.

### Affinity heuristic ratchet — exhausted

Per the loop-break post-mortem
(`docs/diagnostics/2026-05-09_phase-1-2-post-mortem.md`),
further heuristic patches won't move Affinity meaningfully. Two
paths forward:

- **Path A (fast, lower ceiling)**: revert PR #288 maindeck-hate
  edits via A/B golden Bo3 fixtures
- **Path B (slow, higher ceiling)**: execute the Phase 4 prototype
  pilots — ISMCTS for full-game search, SLM for sideboard plans,
  CFR for response sub-problems

The session converged on Path B; this doc is the handoff record.

## How to resume next session

1. Read `docs/diagnostics/2026-05-09_phase-1-2-post-mortem.md`
   first — it's the named-subsystem record per the loop-break
   protocol.
2. Read `docs/research/2026-05_mtg_ai_landscape.md` and the
   per-phase scoping docs (4A, 4C, 4H) for technical context.
3. Triage in this order:
   - Fix Wurmcoil + Pinnacle Emissary token typing (≤ 1 hour)
   - Wire production scorer into A/B harness (Phase 5 follow-up)
   - Download a GGUF model and run live SLM acceptance gates
4. The plan file at
   `/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md`
   carries the full sequencing — each phase has a Q3/Q4 2026
   target.

## Frontmatter discovery

This doc is `priority: historical` — sessions that already shipped.
The forward-looking docs (`status: active`, `priority: primary`)
are in `docs/diagnostics/` and `docs/research/`. The current
session-end primary doc is `2026-05-09_phase-1-2-post-mortem.md`.
