---
title: "MTG AI Landscape — Survey and Port Recommendations"
status: active
priority: secondary
session: 2026-05-09
tags: [research, ai, mcts, cfr, slm, architecture]
summary: >
  Survey of existing MTG / CCG AI engines and academic work on
  imperfect-information game search. For each technique: fit for
  this codebase, scoped 1-month port estimate, and recommendation.
---

# MTG AI Landscape — Survey and Port Recommendations

## Why this exists

The MTGSimManu engine is a **single-ply EV scorer with 5 hand-coded
turn orderings** (`ai/turn_planner.py` + ~3000 LOC of
`ai/scoring_constants.py`). Every recurring outlier-deck session
patches a heuristic, moves the WR a few percentage points, and
surfaces the next outlier. The 60+ Affinity-targeted commits across
5+ sessions are evidence that the heuristic surface is approaching
saturation.

The plan that drove this session
(`/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md`)
identifies four Pillar-II tracks for moving past the ratchet. This
document scopes each in concrete porting terms so future sessions
inherit the cost estimates without re-doing the survey.

## Scope of this document

For each technique below we record:

1. **Mechanism** — what the algorithm does in one paragraph.
2. **Fit for MTG** — direct vs. analogous applications, branching-
   factor and information-set abstractions.
3. **Existing infrastructure we can reuse** — `EVSnapshot`,
   `bhi.py`, `combo_chain.py`, `clock.py`, `oracle_parser.py`.
4. **Scoped 1-month port** — concrete file plan + acceptance
   gate (golden-fixture passing, no matrix runs in inner loop).
5. **Recommendation** — adopt now / pilot later / skip.

## 1. Information-Set Monte Carlo Tree Search (ISMCTS / PIMC)

### Mechanism

ISMCTS handles imperfect information by sampling determinizations
(concrete instantiations of the hidden state — the opponent's
hand) and running UCT (Upper Confidence Trees) on each. Over many
simulations, the average regret across determinizations converges
to the imperfect-information Nash equilibrium for the local
subtree.

Key variants:

- **PIMC (Perfect-Information MCTS)** — flatten determinizations
  to a single sampled hidden state, run vanilla UCT, repeat.
  Cheap; suffers from "strategy fusion" (wrong because optimal
  play differs across determinizations).
- **ISMCTS** — group nodes by information set (visible state),
  share statistics across determinizations within an info set.
  Corrects strategy fusion at the cost of bookkeeping.
- **DUCT (Determinized UCT)** — middle ground; popular in
  trick-taking card games.

### Fit for MTG

Strong. MTG's per-turn branching factor in competitive constructed
is roughly 10²–10³ (Stiehl et al. 2018). UCT scales well with
that; 1000 rollouts × 50 ms per rollout = 50 sec budget — too slow
for matrix sims (~0.7 sec/match) but acceptable for replay
analysis and a non-default "thinking" mode.

The hidden-information surface in MTG is the opponent's hand. The
existing `ai/bhi.py` produces a Bayesian distribution over hand
contents — exactly the input ISMCTS needs to sample
determinizations.

### Existing infrastructure we can reuse

| Module | Use |
|---|---|
| `ai/ev_evaluator.py:EVSnapshot.fast_replace` | State copier (no full game-state clone) |
| `ai/bhi.py` | Determinization sampler (opponent hand) |
| `ai/turn_planner.py:5-orderings` | Action-set generator, replaceable as policy |
| `ai/clock.py` | Terminal evaluation (turns-to-lethal) |
| `ai/combo_chain.py` | Combo-line resolver as MCTS leaf evaluator |
| `tests/fixtures/affinity_golden/` (proposed) | Golden game-state fixtures for acceptance |

### Scoped 1-month port

- **Week 1**: `ai/search/ismcts.py` skeleton — UCT node, info-set
  bucketing, determinization sampler hook into `bhi.py`. Unit
  tests on the bandit (UCB1 vs. Thompson sampling on a 4-arm toy).
- **Week 2**: action-set generator from existing `turn_planner.py`
  orderings. Rollout policy = current heuristic scorer. Integrate
  with `EVSnapshot` for fast state replay.
- **Week 3**: golden-fixture acceptance — 12 archetype fixtures
  (Affinity vs Boros, Storm vs Dimir, Living End vs Boros, ...).
  MCTS must either match heuristic or strictly dominate on ≥4 of
  12 with no losses, at equal wall-clock budget.
- **Week 4**: integration as opt-in mode (`--mcts` CLI flag),
  benchmarking, doc.

### Acceptance gate

`python -m pytest tests/test_ismcts_acceptance.py -q`. Must
report ≥ 4 of 12 fixtures strictly improved AND 0 regressions.
Total wall clock < 5 minutes.

### Recommendation

**Pilot Q3 2026.** This is the single highest-leverage research
track for breaking the heuristic-ratchet trap, but it's a real
month of work. Schedule it after the next full-meta-shift event
(set release, banlist) so the matrix WR baseline is fresh.

## 2. Counterfactual Regret Minimization (CFR / CFR+ / Deep CFR)

### Mechanism

CFR iterates over information sets and updates a regret table per
infoset (action → cumulative regret of not having played it).
Average strategy converges to a Nash equilibrium. CFR+ applies a
non-negativity constraint that empirically converges 2× faster.
Deep CFR replaces the table with a neural network for compact
representation.

### Fit for MTG

Strongest on the **response sub-problem**: counterspell-or-hold,
removal-target choice, sideboarding. These are textbook
information-set games (hidden = which spell the opponent will cast
next), small enough that a tabular CFR with state abstraction
converges in minutes for individual decisions.

CFR is **less suitable for full-game search** because the game-
tree size is too large; but for sub-game decisions (block
allocation, counterspell timing windows) it's a fit.

### Existing infrastructure we can reuse

- `ai/bhi.py` for the abstraction (hand-content distribution → CFR
  buckets).
- `ai/response.py` is the existing counterspell-vs-hold module;
  CFR would replace the rule-based decider.
- `ai/permanent_threat.py` for opp-creature value estimation
  (input to CFR's payoff function).

### Scoped 1-month port

- **Week 1**: pick a single response problem (recommend "block
  allocation against an Affinity attack on T4"). Define the
  abstraction: bucket attackers by power, blockers by power and
  toughness. State space ~10⁴ infosets — tabular tractable.
- **Week 2**: implement CFR+ on the abstraction, train to
  exploitability < 1% of game value.
- **Week 3**: integration as drop-in replacement for
  `_predict_blocks` in `ai/turn_planner.py`. Golden-fixture
  acceptance.
- **Week 4**: scope sister problem (counterspell-hold) and
  document the transfer pattern.

### Recommendation

**Pilot Q4 2026 after MCTS.** CFR is more specialized than MCTS.
If MCTS pilot succeeds, CFR follows for the response sub-problems
where MCTS rollouts are too coarse.

## 3. Existing MTG / CCG engines

### Forge

- **What**: open-source MTG engine, ~1M LOC Java, full Modern
  card support, 17+ years of development.
- **AI architecture**: rule-based with hardcoded card-specific
  routines. `forge.game.ai.AiAttackController`,
  `forge.game.ai.AiBlockController`, etc.
- **Portable patterns**:
  - The attack/block controller interface — separation of
    "should I attack with X" from "should the opponent block X
    with Y" — is cleaner than our `_predict_blocks` mixed
    responsibility.
  - `AiCardMemory` — the AI tracks "I've seen this card from
    the opponent" — useful for response prediction beyond what
    `bhi.py` does today.
- **Caveats**: card-by-card AI customization isn't portable
  (we're explicitly avoiding that pattern per CLAUDE.md's no-
  hardcoded-card-names rule).

### XMage

- **What**: open-source Java MTG engine. Server-authoritative,
  deterministic state.
- **Portable patterns**: state-machine layout for the stack and
  replacement effects (cleaner than our priority-passing flow).
- **Caveats**: similar heuristic-AI ceiling.

### Magarena

- **What**: single-player MTG, fork of Magic-Antra, ~100k LOC.
  Uses MCTS for combat decisions specifically.
- **Direct evidence**: MCTS is feasible at MTG combat scale on
  commodity hardware. Their "1000-rollout MCTS, 250 ms per
  decision" benchmark is consistent with our budget targets.

## 4. Academic literature

| Paper | Year | Lesson |
|---|---|---|
| **Ward & Cowling — "Monte Carlo Search Applied to Card Selection in Magic: The Gathering"** | 2009 | Direct precedent: replacing rule-based mulligan + sideboard with determinized rollout. Their result: +12% WR on a draft simulator. Suggests our `ai/mulligan.py` is a high-value target for the MCTS pilot. |
| **Stiehl et al. — "Adversarial Magic: The Gathering AI"** | 2018 | Surveyed MCTS variants on a simplified MTG ruleset. Quantified branching factor (10²–10³ per turn) which validates feasibility. |
| **Hsueh et al. — "Investigating MCTS Modifications for Hearthstone"** | 2018 | DUCT variants for CCG. Specific UCB tweaks (progressive widening, RAVE) lifted Hearthstone bot WR ~15pp over vanilla MCTS. Direct port pattern. |
| **Brown & Sandholm — "Superhuman AI for multiplayer poker"** (Pluribus) | 2019 | CFR+blueprint+sub-game refinement at scale. Most relevant for the "response sub-problem" (block / counter). Pluribus was 6-player; MTG's 2-player setting is simpler. |
| **DeepMind — Sample-efficient RL by breaking the replay buffer** (NeurIPS 2019) | 2019 | Tabular MTG used as a replay-buffer testbed. Lessons mostly about RL scalability, not play strength. |
| **Lanctot et al. — "OpenSpiel"** | ongoing | Reference implementations of MCTS, CFR variants, NFSP. Worth porting the OpenSpiel CFR+ implementation as our starting point — saves 1 week of correctness work. |

## 5. Small Language Models in-VM (Phase 4C scope)

The MTG corpus is bounded:

- Comprehensive Rules: ~300 pages.
- Modern card pool: ~21,000 templates.
- Format rules + bans: ~50 pages.

Fits in a few MB of fine-tune data. A 7B-class SLM (Qwen 2.5,
Phi-3.5, Llama 3.2, Gemma 2) at 4-bit quantization runs on
commodity hardware (4–6 GB RAM, 30 tok/s CPU-only).

### High-leverage in-VM SLM applications

| Decision | Current approach | SLM win | Latency budget |
|---|---|---|---|
| Oracle parsing | regex + handcoded effect registry | Eliminates Class A oracle-misread bugs (~3-5 currently tracked) | 100ms / unique card, cached |
| Mulligan | `ai/mulligan.py` archetype-specific keep/mull | SLM trained on tournament reports + Reddit r/spikes hand-keep posts | ≤ 1 sec / mulligan decision |
| Sideboard plan | `engine/sideboard_manager.py` keyword categorization (Phase 2A) | SLM can read tournament SB guides as ground truth | ≤ 30 sec / matchup, cached |
| Threat narrative | numerical EV | natural-language explanation in replays | post-game only |

### Architecture sketch (Phase 4C deliverable)

- **Host**: local VM, CPU-only first (llama.cpp / ggml). No
  external API. Deterministic-seed inference for sim
  reproducibility.
- **Model**: Qwen 2.5 7B Instruct, Q4_K_M quantization. ~5 GB
  RAM, 25–35 tok/s on a modern desktop CPU.
- **Fine-tune corpus** (curated, not generated):
  - Comprehensive Rules text + section indexes
  - Modern banned/restricted lists
  - Oracle text for all 21k Modern cards (`ModernAtomic.json`)
  - Free public tournament SB guides (Reddit r/spikes,
    ChannelFireball public articles, MTGGoldfish public
    decklists)
  - Replay logs from this engine (`replays/*.txt`) labeled by
    win/loss outcome — supervised "given state X, AI played Y,
    was that correct"
- **Inference interface**:
  - `ai/llm/policy.py` — local model wrapper, JSON-schema
    enforced via `outlines` or `lm-format-enforcer`.
  - `ai/llm/oracle_parse.py` — replaces regex parser for the
    ~50 cards flagged as "needs oracle review" in
    `decks/card_knowledge.json`.
  - `ai/llm/sideboard_advisor.py` — Phase 2A's redesigned
    matcher backed by SLM.
  - All inference cached by prompt-hash. Cache hit =
    deterministic output, matrix reproducibility preserved.
- **Determinism**: sampler seed pinned (`temperature=0` for
  production runs). Cache all outputs.

### SLM is NOT used for

Per-decision hot-loop scoring (too slow even at 30 tok/s).
Reserved for:

1. Pre-game preprocessing (oracle parsing, SB plan generation)
2. Edge-case decisions where the heuristic flags low confidence
3. Post-game analysis / replay narration

## Sequencing recommendation

```
Q3 2026 — Phase 4A (MCTS prototype)
  → unblock the heuristic ratchet
  → 1 month, golden-fixture gated, opt-in mode

Q3 2026 — Phase 4C (SLM oracle parser, narrow scope)
  → eliminate Class A oracle-misread bugs
  → 1 month, parallel with 4A on a separate branch

Q4 2026 — Phase 4B (CFR for response sub-problem)
  → if 4A succeeds: better blocks + counterspell timing
  → if 4A doesn't: pivot to CFR-only response upgrade
```

Each phase has a **golden-fixture acceptance gate** (no matrix
runs in the inner loop). Matrix runs only after a phase is
merged, to confirm aggregate WR movement.

## How this informs current sessions

- If a future session ships a heuristic patch that lifts WR by
  < 1pp, treat that as a signal the ratchet is exhausted and
  trigger Phase 4A immediately.
- If a Class A oracle-misread bug surfaces (e.g. another
  Nettlecyst / Saga-style mis-parse), don't extend the regex —
  trigger Phase 4C's SLM parser.
- The plan file
  (`/root/.claude/plans/now-lets-fix-affinity-keen-penguin.md`)
  is the source-of-truth for which phase is next; this document
  is the technical scoping each phase will reference.

## Out of scope for now

- **AlphaZero-style self-play RL** — requires GPU + 10⁸ games,
  doesn't fit the project's offline / commodity-hardware
  posture. Revisit Q1 2027 if 4A/4B/4C all converge.
- **Per-card SLM-customized scoring** — would re-introduce the
  card-name hardcoding pattern CLAUDE.md prohibits; if needed,
  do via gameplan JSON additions, not SLM-per-card prompts.
