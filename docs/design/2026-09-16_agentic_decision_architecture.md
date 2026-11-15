---
title: Agentic decision architecture — orchestrator, sequencer and executor agents over a deterministic engine
status: active
priority: primary
session: 2026-09-16
supersedes: []
superseded_by: []
depends_on:
  - docs/design/2026-09-16_payoff_sequencing_design.md
  - docs/research/2026-05_mtg_ai_landscape.md
  - docs/research/2026-05_phase_4c_slm_scoping.md
  - docs/design/rules-foundation-sweep-tracker.md
tags: [design, architecture, agents, llm, ai-decision-quality, evals]
summary: >
  One root Decision Supervisor that orchestrates six branches: compile-time knowledge
  authoring (reasoning models, reviewed data committed under a five-verdict gate),
  sim-time situation assessment, line sequencing and seam execution (deterministic,
  name-free Python over the landed ai/assembly_state.py spine), deterministic
  verification, and eval-time divergence research (reasoning models over reconstructed
  states, measured before they are judged). Sims make zero model calls, read no
  environment on the decision path and replay byte-for-byte. The 2026-09-12 scoping
  ("an LLM in the loop is a separate research plan") is answered by caching over a
  finite abstract key at compile time and by measuring before judging at eval time.
  Live in-loop agents are a gated non-goal.
---

# Agentic decision architecture

This is the final architecture document. It combines five inputs:

- the winning architecture (the "Agentic Lane");
- the judges' recommended layering (compile time, sim time, eval time) and their
  runner-up ideas;
- every must-fix amendment from the four refuters (determinism, cost, contract and
  play quality, decomposition);
- every gap the completeness critic found in the previous draft.

Appendix A gives the disposition of each refutation. Appendix B maps each critic gap to the
section that closes it.

The main structural change from the winner: **no model runs in any sim process.** The
sequencing agent and the executor agents *are* the deterministic spine. Every model call
happens in a separate process, at compile time or at eval time, against committed or
reconstructed inputs. The root supervisor now sequences those processes as well as the
sim-time branches (§3.8).

---

## 1. Context and the constraint

### 1.1 Facts the design must respect (measured, with sources)

| Fact | Number | Source |
|---|---|---|
| Registered decks | **25** (`len(MODERN_DECKS)` at cb986ab). CLAUDE.md's "16" is stale. Every matrix figure below assumes 25 decks and 300 ordered pairs | `decks/modern_meta.py` |
| Engine cost per game (offline scorer, quiet box) | 0.26 CPU-s mean (0.05–1.41), 8.4 turns; slowest legitimate game 3.87 CPU-s | recon probe at 050b696; docs/diagnostics/2026-09-06_wall_clock_deadline_load_invariance.md |
| n=20 Bo3 matrix, 3 workers | 6000 matches, ≈ 14,800 games, **28 min 25 s** (≈ 5,115 worker-s) | rules-foundation tracker |
| n=60 Bo3 matrix | 18,000 matches, 4 h 26 min; with `--rules-audit` 4 h 48 min | tracker |
| One `--field` at n=20 | 480 Bo3 matches, **848 s** measured (Storm) | tracker |
| AI decisions per n=20 matrix | ≈ 0.95 M `decide_main_phase` iterations (63.7 per game), ≈ 1.58 M EVPlayer decisions, ≈ 3.2 M hook calls | recon probe × matrix size |
| Main-phase entries per game per side | **11–22.5** by deck (Storm 11.8, Amulet 15.5, Jeskai 22.0, Living End 22.5) | cost refuter, 16-game probe at cb986ab |
| Live LLM in the decision loop | **~3.7× slower and non-deterministic** (106 s vs 29 s for 3 Bo3; the same matchup read 0% vs 67%) | docs/diagnostics/2026-08-30_post_cage_fix_perf_and_provisional_wr.md |
| Per-game CPU budget | `GAME_TIMEOUT_SECONDS = 30.0` CPU-s via `time.process_time`, which counts every thread | ai/scoring_constants.py, engine/game_budget.py |
| WR anchor | 27 deterministic Bo1 (winner, turns) pins; budget rebound to `_ANCHOR_TIMEOUT_SECONDS = 600`; one environment-sensitive entry (Amulet Titan vs Living End @50000, xfail on flip) | tests/test_wr_baseline_anchor.py:49, :69 |
| CI | 5.2–6.4 min over the last 8 runs; `MTG_LLM_DECISION_SCORER_OFFLINE=1` job-wide; no API key. A live miss once stalled 1 h 30 min. Scheduled workflows: `weekly.yml` (Monday 06:00 UTC) only. **There is no nightly job** | .github/workflows/ |
| Rules auditor | 20 CR invariant classes; 0 violations on the fixed engine | engine/rules_audit.py, audits/*.jsonl |
| LLM-at-decision-time history | offline refactor WR-neutral; warm under a coarse taxonomy: Living End −15.3pp, Storm −19.3pp; committed Sonnet weights: "no field moves" (largest shift 1.0pp) | 2026-05-16 diagnostics; tracker :4633-4672 |
| Payoff sequencing U0–U3 | **all landed**. `ai/assembly_state.py` (`assemble` :349, `sink_damage` :244, `attack_reach` :125, `_p_resolves_cast` :325, `AssemblyState.best_line` :90), `ai/clock.win_swing` :766, `ai/ev_evaluator.project_counter_tax_payment` :2697. `EVPlayer.decide_main_phase` already calls `assemble` once per call and caches it (`self._assembly`, ai/ev_player.py:463). Replay gate passed; Toolbox 21.2 → 28.3 same-seed; Zoo guard flat | payoff design §5 execution status; cb986ab |
| Metrics accounting | **broken**. `MeteredAgent` calls `result.usage()`, but pydantic-ai 2.40.0 exposes `usage` as a property. The `TypeError` is swallowed, so every live call logs 0 tokens and $0, and the 30-day budget never accumulates | ai/llm_agents.py:431; cost refuter |
| Model pricing | Repo table: sonnet-5 **$3/$15**, opus-4-7 and opus-4-8 $5/$25, haiku-4-5 $1/$5; **no opus-5 row**. The Anthropic model table (cached 2026-06-24) lists `claude-sonnet-5` at **$2/$10** and `claude-opus-5` at $5/$25. The sonnet-5 row is inconsistent; P0 reconciles it | ai/llm_metrics.py:61-75 |

### 1.2 The prior out-of-scope decision (quoted in full, so it can be cited)

The decision lives in an **uncommitted** local plan file,
`~/.claude/plans/lets-create-plan-and-typed-flurry.md`, section "Wire an external LLM as the
decision scorer ('should we give the ai gpt astra?', 2026-09-12)". It is not in the repo, so
the operative text is reproduced verbatim here:

> **Not in scope (stated so it is a decision, not a drift)**
> An LLM that scores or chooses individual plays inside the decision loop. The 2026-05
> landscape doc (`docs/research/2026-05_mtg_ai_landscape.md`) and the Phase 4C SLM scoping
> cover that design; it would make every game a network-bound, non-deterministic call unless
> fully cached per state, which the loop cannot be. If that is what "give the AI gpt astra"
> means, it is a separate research plan.

The same plan also records the hook's limit: *"It does not choose plays; it sets eight
multipliers per archetype."* The repo-side record of that work is
`docs/design/rules-foundation-sweep-tracker.md` :4398-4433 (weights committed) and :4633-4672
(A/B: no field moves). The Phase 4C scoping states the same rule: "Hot-loop budget: 0 sec /
decision" (`docs/research/2026-05_phase_4c_slm_scoping.md`:155-156).

**Companion commit.** The commit that lands this document must also commit that plan section
as `docs/history/plans/2026-09-12_llm_decision_scorer_plan.md` with frontmatter
(`status: archived`). That path should then be added to `depends_on` above. It is not listed
yet because a `depends_on` entry must resolve to a file in the repo.

### 1.3 How this design answers the scoping

**This document is the separate research plan.** It answers the scoping in three ways.

1. **On the loop, it agrees with the scoping and supplies the missing numbers.** A fully
   agentic n=20 matrix would need 2.5 games × 11–22.5 main-phase entries × 2 sides × 6000
   matches ≈ **0.33–0.68 M** reasoning calls. At $0.07–0.56 and 30–300 s per call (§8), that
   is $23 k–$380 k and weeks of wall time, and live calls were measured non-deterministic.
   No design choice fixes this. Live in-loop play is therefore a **non-goal** (§10), and a
   structural rule enforces it: the sim import graph contains no model client (§7.1).
2. **It takes up the scoping's own escape clause, "unless fully cached per state".** The
   state the loop consults is abstracted to a finite, name-free key. Priorities are keyed by
   a `class_key` of at most **72** values per library, bounded by construction (§5.2).
   Reasoning models run **once per library version at compile time**. Their output is
   reviewed, gated by deterministic verdicts and committed. Sim-time Python reads it with no
   network, no SQLite and no environment variable. This generalises the precedent the repo
   already trusts (`ai/llm_decision_weights.json`, `_oracle_classifier.json`) from 72 scalars
   to typed lines.
3. **It asks the research question the scoping deferred: does a reasoning model see lines
   the deterministic sequencer misses?** The question is asked at **eval time**, on
   reconstructed real decision states, with forward simulation run **before** any model
   judgement (§3.3 branch 1.6). A positive answer becomes either a library entry that passes
   the gate or a rule-phrased engine unit. It never becomes a live pick. A negative answer
   is written up as `status: falsified` and ends the track.

---

## 2. The layering and the deterministic spine

```
 COMPILE TIME (1.1)  human-approved spend, needs a key, output PR-reviewed and committed
   census ─► fixture coordinates ─► Line Librarian ─► deterministic class ranking
          ─► Seam Policy Writer ─► Admit (integrity · legality · lethality · regression · cost)
   ⇒ decks/gameplans/_line_library/<library_id>.json   (content-addressed, shareable)
   ⇒ decks/gameplans/<slug>.json  sections: line_library_id, goal_transitions,
                                            tutor_delivery, sacrifice

 SIM TIME (1.2–1.4)  every run: matrix, field, matchup, Bo3, anchor, CI
   zero model calls · zero SQLite · zero environment reads on the decision path
   EVPlayer seam ─► Situation Assessor ─► Line Sequencer ─► Seam Executors ─► engine
                    (LineFrame, key)      (match·bind·      (cursor, action, attackers,
                                           project·price·    offered choices, reactive,
                                           rank)             validate)
   not eligible or no line ⇒ the existing EV path, byte-identical

 EVAL TIME (1.5 deterministic; 1.6 research)
   1.5  reconstruct (StopAtDecision) ─► grade ─► decision-quality audit ─► golden corpus
        ─► movement verdict                      [CI tier + weekly full corpus; keyless]
   1.6  shadow proposal ─► forward-sim measurement ─► diagnosis ─► unit spec
        [per-run $ cap; never in a sim; feeds 1.1 and engine units via PR]

 ROOT (1)  Decision Supervisor
   in-process dispatcher at sim time
   + tools/decision_supervisor.py for everything else: status · plan · run
     (derives the compile, re-verify and research actions from repo state;
      a human approves spend and quiet-box time)
```

### 2.1 Why each lifecycle holds what it holds

- **Sim time is deterministic by construction, not by configuration.** Code on the sim-time
  decision path imports neither `pydantic_ai` nor `ai.llm_*`. It opens no SQLite and reads no
  environment variable. `tests/test_sim_time_import_graph.py` pins this, together with an
  AST scan for `os.environ` / `os.getenv` in the sim-time modules.

  The switches that exist are **constructor arguments** that `run_meta` sets from CLI flags
  and passes into the multiprocessing workers explicitly:
  - `GameRunner(line_libraries=…)`, where `--no-line-library` gives the pre/post A/B;
  - `GameRunner(decision_overrides=…)`, eval tools only.

  Two existing instrumentation variables stay: `MTG_RULES_AUDIT` and the new
  `MTG_DECISION_AUDIT`. They change only what is **recorded**. A test pins that decisions are
  identical with them on and off. Because no model call exists in the sim, the whole class of
  determinism refutations R1–R10 cannot occur there: offline-flag leakage,
  budget-before-cache, namespace collision, schema drift, id minting, shadow side effects,
  env leakage into the anchor, and orphaned threads.

- **Compile time is where reasoning is cheap relative to its reuse.** One library serves
  every sim until its content address goes stale (§2.3). Spend is approved per run.

- **Eval time is where reasoning is safe relative to its variance.** A model's proposal is a
  hypothesis. It is measured by forward simulation, graded by deterministic oracles, and
  reaches the engine only as a reviewed golden, a failing test and a fix.

**The deterministic spine** runs:

1. `ai/assembly_state.py`, the one owner of engine-live, sink-reachable and lethal-line
   facts;
2. the line sequencer, which matches committed lines, binds them to engine-enumerated
   members, and projects and prices them with the same primitives;
3. the line cursor, a persisted `LineCursor` on the EVPlayer;
4. the existing seam methods.

### 2.2 Library identity under Bo3 sideboarding

Bo3 is canonical, and sideboarding changes the 60 cards in play in G2 and G3. The design
handles this as follows.

- **`library_id` is the census sha of the main + sideboard *union* (the 75).** The union is
  invariant across G1–G3, so one library serves all three games. The loader never falls back
  mid-match because of sideboarding.
- **Per-game activation.** At `GameRunner.run_game` start, the loader computes the **active
  shape set** of the post-board 60. It activates only the lines whose every `LineStep.shape`
  occurs in that set. This is a filter, not a re-key.
- **Replay.** A Bo3 replay reproduces the same choice because the library file is fixed (it
  is committed) and the post-board 60 is a deterministic function of (seed, decks, prior
  game results) through `engine/sideboard_manager`. `generated_by` in `metagame_results.json`
  records `line_library_ids` and `policy_hash`, and each DECISION event records `line_id` and
  `library_id`.
- **Seam sections are keyed per deck, deliberately.** `goal_transitions`, `tutor_delivery` and
  `sacrifice` live in `decks/gameplans/<slug>.json`, because they reference that deck's
  GoalEngine goals, which are data. The same union-census rule applies to the card-role paths
  they cite.

### 2.3 Freshness, staleness and what happens at sim time

Three digests, each with one job.

| Digest | Content | Stale ⇒ |
|---|---|---|
| `library_id` | sha256 of the canonical sorted multiset of name-free `ShapeToken`s over the 75, **plus** the `ShapeToken` Literal's sha and the census code's sha | the library no longer describes the deck |
| `line_path_digest` | sha256 of the sources that execute lines: `ai/line_*.py`, `ai/assembly_state.py`, `ai/policy_predicates.py`, `ai/decision_coordinate.py`, and the modules of the X / tutor / sacrifice callbacks (`engine/callbacks.py`, `engine/game_runner.py`, `ai/activation_ev.py`) | the library's Regression and Cost verdicts were measured on different line code |
| `behaviour_digest` | sha256 of tracked `engine/`, `ai/`, `decks/` sources **including dirty-tree diffs**, the ModernAtomic part hashes, `ai/llm_decision_weights.json` and `_oracle_classifier.json` | verdicts 4–5 describe an older engine |

**The census is computed at load time.** It reads typed `CardTemplate` fields only, about 75
field reads per deck, well under 1 ms. It is `lru_cache`d per (deck, process). Each
multiprocessing worker computes it once per deck it plays (25 decks is under 25 ms per
worker). No census is ever read from a file.

**A stale library at sim time is a hard failure, never a silent fallback.**

- A deck that **declares** `line_library_id` in its gameplan JSON, where that id ≠ the census
  computed now, raises `StaleLineLibraryError` at `GameRunner` construction. The error names
  the deck, both ids and the three remedies: recompile (needs a key), retire (delete the
  declaration, a visible behaviour change in the diff), or run a diagnostic.
- CI runs `tools/validate_line_library.py` (keyless, under 10 s) on every PR. So a DB re-merge,
  an oracle-predicate change or a `ShapeToken` growth that stales a library fails **the PR
  that caused it**, not a later matrix.
- A dirty tree that stales a library fails loudly at the first local run. Two runs of the
  same seed therefore cannot silently disagree.
- For diagnosis only, `run_meta --allow-stale-library` runs the deck at tier 0. It stamps
  `generated_by.stale_libraries=[…]` and marks the results file **NOT CALIBRATION-GRADE**,
  the same treatment as `aborted > 0`.

**Verdict freshness (verdicts 4–5).** The library's provenance stores the `line_path_digest`
and `behaviour_digest` at which Regression and Cost were measured.

- **Line-path drift is a CI failure.** If `line_path_digest` changed, the validator refuses the
  library until the committer re-runs verdicts 4–5. A PR that changes how lines execute must
  therefore re-measure the lines it changes.
- **Behaviour drift is a warning, with a rule.** When only `behaviour_digest` drifted,
  `decision_supervisor.py status` lists the library as *verdict-stale*. Verdicts 4–5 must be
  re-run:
  - before any WR claim that cites that deck;
  - before every `run_meta --matrix --save`, which otherwise stamps
    `generated_by.verdict_stale_libraries`;
  - at least at every weekly refresh, where `weekly.yml` reports it.

  This is not a CI failure, because blocking every engine commit on a quiet-box
  measurement would stall the repo.
- **Owner.** The re-run is owned by the root's plan (§3.8, action `reverify`) and executed by
  leaves 1.1.6.4–1.1.6.5.

### 2.4 Compile reproducibility

**The committed artifact is the reproducibility unit for sims.** In addition, every compile
run writes a **compile ledger**:
`replays/agentic/compile/<library_id16>_<run_id>.ledger.ndjson`.

- **Contents.** A header carries `run_id`, model ids, `prompt_version` per task,
  `model_settings`, `behaviour_digest` and the census. Each row holds one envelope and the
  **raw** model output, or the failure reason.
- **Committed with the artifact.** `tools/compile_decision_knowledge.py --replay LEDGER`
  re-derives the committed artifact byte-for-byte with no model call. Every step after the
  model is deterministic: canonicalisation, the name probe, class ranking (§3.3 1.1.4),
  dominance correction, and verdicts 1–3.
- **What is not reproducible.** Two *live* compile runs are not expected to agree, and the
  design never compares them. A/B of prompts or models compares gated artifacts (§7.1).

---

## 3. Function Structure Diagram (FWM)

### 3.1 Quantified root goal

**Root 1: "Produce Correct Engine-Seam Decisions".** This root is SUPERVISORY and is the
**Decision Supervisor** (§3.8). It is stateless and delegates only.

**Data output.**

- **(a) One legal member per seam call.** For every engine→AI seam call in every sim (9
  duck-typed EVPlayer methods and 11 `GameCallbacks` methods, plus the new
  `choose_x_value`), the result is exactly one member of the engine-enumerated legal set,
  matched by identity: `instance_id`, `ability_index`, target ids and `x_value`. When a
  committed line covers the seam, that member realises the line's next step. Otherwise it is
  the unchanged EV-path pick.
- **(b) Evidence when logging.** When a replay log is present, each DECISION carries
  `subsystems.line` evidence (§6).
- **(c) Committed decision knowledge.** Libraries and seam sections, each with gate
  provenance.
- **(d) Verification record.** A graded golden-decision corpus, plus per-run decision-quality
  findings.
- **(e) Research verdict.** A measured answer, or a recorded falsification, to "does a
  reasoning model find better lines?".

**Service level.**

| Dimension | Level | How derived / checked |
|---|---|---|
| Sim network calls / SQLite reads / decision-path env reads | 0 / 0 / 0 | import-graph and AST tests (§7.1) |
| Added sim CPU | **≤ +5% on the n=20 matrix** = ≤ 256 worker-s over ≈ 0.95 M iterations = **≤ 269 µs mean per `decide_main_phase` iteration**. Planned ≈ 170 µs (§3.2 budget table) | CostVerdict 1.1.6.5 (process_time probe, `aborted == 0`) |
| Anchor | byte-identical, unless a gated behaviour change is in the same commit and every flip has a justifying decision coordinate in the diverging log tail | `measure_lane` |
| CR rules audit | 0 violations (unchanged CR-invariant set; no play-quality check lives there, §7.3) | `--rules-audit` matrix |
| Decision quality | the **missed-lethal rate** (1.5.3) does not rise on any guard deck beyond 2 binomial σ; it falls on the target lane | §7.3 |
| Golden corpus | 100% of CI-tier LIVE goldens pass in CI; STALE ≤ 20% of the full corpus; full corpus green weekly | §7.2 |
| Kept behaviour units | MovementVerdict `keep`: effect ≥ `FIELD_MOVEMENT_PP` **and** a paired sign test with p < 0.05 **and** no guard significantly harmed (Holm, family α = 0.05) | §7.4 |
| Model spend | every compile or research run stays within an explicit per-run USD cap and call cap, and aborts loudly on breach | Provenance Keeper (§4.8) |

**Scope in.**

- Main-phase sequencing.
- The terminal attack of a committed line.
- The engine-offered choices a line depends on: tutor delivery, X value, sacrifice victim,
  goal transition.
- Compile-time authoring and gating of the data behind those choices.
- Eval-time grading of every DECISION and RESPONSE_DECISION seam.
- The research probe.

**Scope out.**

- Any model call in a sim process.
- A mulligan redesign. That seam receives only the hand.
- Sideboarding.
- The hidden engine→ai seams: `spell_resolution` → `select_modal_modes`,
  `oracle_resolver._pick_damage_target`, and the `engine/game_runner.py` `_activate_*`
  heuristics. These are listed as blind spots.
- Counter-wars beyond one response level.
- Line-driven blocks and responses.
- Engine rules changes beyond one uniform-per-kind `choose_x_value` callback.

**Scope of control.**

- New flat modules: `ai/line_census.py`, `ai/line_key.py`, `ai/line_menu.py`,
  `ai/line_library.py`, `ai/line_sequencer.py`, `ai/line_cursor.py`,
  `ai/policy_predicates.py`, `ai/decision_coordinate.py`, `ai/decision_audit.py`.
- Thin consumers in `ai/ev_player.py`, `ai/activation_ev.py`, `ai/gameplan.py` and
  `ai/assembly_state.py` (a public `p_resolves_cast`).
- `engine/callbacks.py` (one callback kind); `engine/cast_manager.py` and
  `engine/activated_effects.py` (X routed through that callback).
- `engine/replay_log.py` (schema 1.1); `engine/rules_audit.py` (a `decision_id` context field
  only); `engine/game_runner.py` (transport).
- `decks/gameplans/` data, `tools/*`, and `ai/llm_*.py`.

**Scope of effect.** Matrix and field WR, calibration bands, the anchor, CI wall-clock, the
rules census, and the decision-quality audit.

### 3.2 Conventions: classification, tool metadata, docstrings, hot-path budget

**Classification.**

- Only non-leaves are **SUPERVISORY**.
- A leaf is a **TOOL** (deterministic code) or an **AGENT** (exactly one typed model call per
  invocation, wrapped by the Provenance Keeper service). AGENT leaves are subtyped
  *reasoning* (open-ended synthesis) or *extraction* (classification over a closed
  vocabulary).
- A TOOL leaf that needs a human approval step says so. HITL is a property of the physical
  mapping (§3.8), not a leaf type.

**Probability symbols** (per `decide_main_phase` iteration unless stated otherwise):

| Symbol | Meaning | Value used for planning | Measured in |
|---|---|---|---|
| p_E | the iteration is a main-phase **entry** | ≤ 0.7 (22–45 entries vs 63.7 iterations per game) | P2 |
| p_L | the entry is **eligible**: the deck has an active library, or `self._assembly.best_line is not None` | ≤ 0.15 matrix-wide (engine and sink decks only) | P2, per deck |
| p_C | the cursor holds a live step at a seam call | ≤ p_E·p_L × mean steps per line | P3 |
| p_T | a replay log is attached | 0 in matrix / field / anchor; 1 in dumped Bo3 | — |
| p_X | a compile or eval process | — (not in sims) | — |

**Tool metadata.** Every leaf tool states exactly `FSD | Result | ServiceLevel |
ExecutionProbability`. ExecutionProbability is a probability per named invocation context,
never a per-game rate.

**Docstring template.** Every public function in the §3.1 flat modules and in the new `tools/`
must carry this block:

```python
def bind_line(line: Line, menu: HotMenu) -> BoundLine | None:
    """Bind an abstract line's steps to engine-enumerated menu members.

    FSD: 1.3.2 — Bind Line Steps To Menu Members
    Result: BoundLine | None
    ServiceLevel: <= 100 us per line (hot path)
    ExecutionProbability: p_E * p_L per decide_main_phase iteration, per line (<= LINE_CANDIDATES_MAX)
    """
```

`tests/test_fsd_docstrings.py` AST-scans those modules and checks four things:

1. every public function has the four fields;
2. every `FSD:` id exists in `tools/fsd_registry.json`, the machine-readable copy of §3.3
   (id → name, type);
3. every TOOL leaf in the registry has at least one implementing function;
4. every AGENT leaf maps to a `TaskSpec` row (§4.9).

**Hot-path budget.** Per `decide_main_phase` iteration, untraced. This table is the
derivation behind the root's ≤ 269 µs.

| Leaf | P | SL | Expected µs / iteration |
|---|---|---|---|
| 1.4.1 cursor peek | 1.0 | ≤ 2 µs | 2 |
| eligibility gate (part of 1.2.1) | p_E | ≤ 2 µs | 1.4 |
| 1.2.1 frame + 1.2.2 hot menu + 1.2.3 beliefs + 1.2.5 posture + 1.2.6 key | p_E·p_L | ≤ 20+20+50+5+30 = 125 µs | 13.1 |
| 1.2.4 assembly | p_E | 0 added (reuses `self._assembly`) | 0 |
| 1.3.1 match | p_E·p_L | ≤ 20 µs | 2.1 |
| 1.3.2 bind + 1.3.3 project + 1.3.4 price, × ≤ `LINE_CANDIDATES_MAX` (= 4) lines | p_E·p_L | ≤ 4 × (100+200+50) = 1,400 µs | 147 |
| 1.3.5 rank (includes projecting the EV path's top play as a one-step line) | p_E·p_L | ≤ 20 µs | 2.1 |
| 1.4.6 validate | p_C (≤ p_E·p_L·3) | ≤ 100 µs | ≤ 3 |
| 1.2.7 serialise context + `menu_digest` | p_T | ≤ 2 ms | 0 in matrix |
| **Total** | | | **≈ 170 µs (≈ +3.2% matrix, ≈ +55 s wall)** |

The leaf service levels are consistent with the root because each is weighted by its
early-exit probability. The worst case is bounded separately: `LINE_CANDIDATES_MAX = 4` and
`REPLAN_MAX_PER_PHASE = 1` cap one eligible entry at ≤ 1.6 ms. If P2 measures p_L above 0.25
on the matrix mix, the budget table fails before any behaviour change ships. The remedy is
then to lower `LINE_CANDIDATES_MAX`, never to raise the root budget.

### 3.3 FSD tree

Siblings are ordered by **result dependency**, 1.1 → 1.6: knowledge, then a situation view,
then a line, then a seam member, then verification of what was chosen, then research into
what was missed.

- **1 Produce Correct Engine-Seam Decisions** — SUPERVISORY (root, the Decision Supervisor)

  - **1.1 Compile Committed Decision Knowledge** — SUPERVISORY (the Knowledge Compiler;
    compile time)
    - *Result:* per library, a committed, gated, content-addressed artifact (lines plus class
      priorities) and per-deck seam sections, or a recorded refusal.
    - **1.1.1 Census Typed Card Shapes** — TOOL. A pure read of typed fields.
      - `census(deck75, card_db) → ShapeCensus` in `ai/line_census.py`, so the
        `check_oracle_runtime_parse` rglob scans it. It reads **typed `CardTemplate` fields
        only**, for example the sink shape classified at DB load by `is_mana_sink`. A
        `ShapeToken` without a typed field first gets one at DB load (Pattern A schema
        commit).
      - FSD 1.1.1 | Result: ShapeCensus, `library_id` | SL < 1 ms per deck | P = 1.0 per
        loader call (sim) and per compile run.
    - **1.1.2 Derive Class Fixture Coordinates** — TOOL. This is the missing legality-fixture
      leaf. It is derived, not hand-authored.
      - `fixture_coordinates(deck, seeds) → tuple[FixtureBoardSpec, ...]`.
      - It dumps same-seed Bo3 runs of the deck with evidence on (`subsystems.line.class_key`,
        P2) and picks up to `FIXTURES_PER_CLASS` coordinates per observed `class_key`. Each
        fixture is a real, reconstructable decision coordinate (§4.0), never a synthetic board.
      - A class with no observed coordinate gets no priorities, so its lines are never
        committed there.
      - FSD 1.1.2 | Result: FixtureBoardSpec set | SL ≈ 1–3 s per Bo3 × seeds | P = 1.0 per
        compile run.
    - **1.1.3 Author Candidate Line Library** — AGENT (reasoning).
      - `LineLibraryDraft` from `LibrarianInput`.
      - *Why a model:* the line space is sequences of up to 8 steps over 6 verbs × 14+
        shapes × 4 `x_policy` × 4 `target_role`. That is over 10³ step types per position
        and cannot be enumerated. A model proposes; verdicts 1–4 decide.
      - FSD 1.1.3 | Result: LineLibraryDraft | SL ≤ 300 s per call | P = 1.0 per compile run
        whose census changed (retries ≤ `COMPILE_RETRIES_MAX` = 2).
    - **1.1.4 Rank Lines Per Interaction Class** — **TOOL** (previously a model leaf; see the
      justification below).
      - `rank_classes(draft, fixtures) → RankingTable`. For each `class_key`, it binds,
        projects and prices every line on that class's fixture boards with the **sim-time
        tools 1.3.2–1.3.5**. It orders lines by mean score, then applies the dominance floor.
      - *Why not a model:* ranking over closed Literals, which the design then corrects
        deterministically by `p_resolves × win_swing`, is exactly what the projector computes
        on real states. A model would add variance without adding information. The Matchup
        Strategist agent is therefore deleted.
      - FSD 1.1.4 | Result: RankingTable | SL ≤ 5 s per library | P = 1.0 per draft.
    - **1.1.5 Author Seam Policy Sections** — AGENT (reasoning).
      - `SeamPolicyDraft`: `GoalTransitionRule`s (the payoff design's deferred RAMP →
        payoff owner), `TutorDeliveryPolicy` (tie-break within a tier), and `SacrificePolicy`
        (protected roles).
      - *Why a model:* choosing when a ramp plan should turn is a policy judgement over the
        deck's declared goals and roles. Searching it by field runs would be parameter-tuning
        to move a number, which is excluded. A model authors the rule, and the gate measures
        it.
      - FSD 1.1.5 | Result: SeamPolicyDraft | SL ≤ 300 s | P = 1.0 per deck the plan
        selects (§3.8).
    - **1.1.6 Admit Compiled Knowledge Artifact** — SUPERVISORY (the PolicyGate). Its five
      children have different executors and costs.
      - **1.1.6.1 Check Artifact Integrity** — TOOL (CI).
        - Checks the schema, the `schema_version` major, `library_id` = census,
          `policy_hash`, the absence of float weight fields, the name probe (§5.4) and
          prompt-vocabulary parity.
        - FSD 1.1.6.1 | Result: IntegrityVerdict | SL < 1 s | P = 1.0 per artifact per CI run.
      - **1.1.6.2 Verify Fixture-Board Line Legality** — TOOL (CI).
        - Reconstructs each fixture (1.5.1) and executes every step with `AICallbacks` wired
          and `MTG_RULES_AUDIT=1`. `rules_audit.drain()` must be empty for every board. A
          negative-control broken fixture must fail.
        - FSD 1.1.6.2 | Result: LegalityVerdict | SL < 6 s per library | P = 1.0 per artifact
          per CI run.
      - **1.1.6.3 Verify Projected Line Lethality** — TOOL (CI).
        - A `lethal_now` line must project damage ≥ opponent life via `sink_damage` /
          `attack_reach` on its fixtures. A number supplied by the library is never
          accepted. The dominance floor must hold.
        - FSD 1.1.6.3 | Result: LethalityVerdict | SL < 2 s | P = 1.0 per artifact per CI run.
      - **1.1.6.4 Measure Artifact Regression** — TOOL (committer, quiet box).
        - Uses the lifted auxiliary `measure_lane` (§3.6) on same-seed pre/post fields.
        - FSD 1.1.6.4 | Result: MovementVerdict | SL ≈ 50–60 min on 3 workers (10 fields) |
          P = 1.0 per new artifact, per `line_path_digest` change, and per verdict-stale
          re-verify.
      - **1.1.6.5 Measure Sim-Time Cost Envelope** — TOOL (committer, quiet box).
        - A `process_time` probe with library versus `--no-line-library` on the same seeds
          (the 27 anchor pairings Bo1 plus 25 decks × 2 seeds Bo3, 3 repeats). It reports
          measured p_E, p_L and µs per iteration against §3.2, and requires `aborted == 0`.
        - FSD 1.1.6.5 | Result: CostVerdict | SL ≈ 15 min | P = as 1.1.6.4.

  - **1.2 Assess Decision Situation** — SUPERVISORY (the Situation Assessor)
    - *Result:* one `LineFrame` per eligible main-phase entry (the hot-path view), and a
      serialisable `DecisionContext` wherever a trace or eval process needs one.
    - **1.2.1 Frame Hot-Path Decision View** — TOOL.
      - `frame(ev_player, game) → LineFrame | None`. It returns None when the entry is not
        eligible (the gate). It packages what the EV path **already computed**: `snap`,
        `self._assembly`, and the captured pre-filter enumeration (1.2.2).
      - FSD 1.2.1 | Result: LineFrame | None | SL ≤ 22 µs | P = p_E.
    - **1.2.2 Enumerate Legal Action Menu** — TOOL (a lifted auxiliary, §3.6).
      - *Hot path:* `hot_menu(frame) → HotMenu`. This is a view over the EV path's
        enumeration, captured **before** its strategic filters (legend rule,
        `lock_that_counters`, reactive-only). It adds the X range from the engine's
        `legal_x_range` query and targets from `enumerate_legal_targets`. There is no
        re-enumeration.
      - *Eval/trace:* `action_menu(game, idx, seam, excluded) → ActionMenu` with
        `menu_digest`.
      - Side-effect-free, including under a re-entrant `should_evoke`.
      - FSD 1.2.2 | Result: HotMenu / ActionMenu | SL ≤ 20 µs hot, < 5 ms full | P = p_E·p_L
        hot; p_T + p_X full.
    - **1.2.3 Estimate Opponent Interaction Beliefs** — TOOL.
      - `belief_view(bhi) → BeliefView`, wrapping `get_counter_probability`,
        `get_removal_probability`, `get_interaction_probability`, plus the soft-counter tax
        posterior once P3a lands.
      - FSD 1.2.3 | Result: BeliefView | SL ≤ 50 µs | P = p_E·p_L.
    - **1.2.4 Read Engine Assembly State** — TOOL.
      - Reads `self._assembly` (ai/ev_player.py:463). In eval processes it calls
        `ai.assembly_state.assemble` (:349). It never re-derives.
      - FSD 1.2.4 | Result: AssemblyState | SL 0 added hot | P = p_E.
    - **1.2.5 Resolve Plan Posture** — TOOL.
      - `posture(goal_engine) → PostureLiteral` through the total map
        `POSTURE_BY_GOAL_TYPE` over the closed `GoalType` enum (§5.2). The input is
        `current_goal.goal_type`, never the free-string goal name.
      - FSD 1.2.5 | Result: posture | SL ≤ 5 µs | P = p_E·p_L.
    - **1.2.6 Derive Abstract Assembly Key** — TOOL.
      - `abstract_key(frame, beliefs, posture) → AbstractAssemblyKey` (§5.2).
      - FSD 1.2.6 | Result: key, `class_key` | SL ≤ 30 µs | P = p_E·p_L.
    - **1.2.7 Serialise Decision Context** — TOOL.
      - `compile_decision_context(game, idx, coordinate) → DecisionContext`, wrapping
        `snapshot_from_game`, `snapshot_board`, `snapshot_state`, the clock read-outs
        (`opp_one_turn_damage`, `combat_clock`, `position_value`, quantised) and typed
        template flags.
      - FSD 1.2.7 | Result: DecisionContext | SL < 2 ms | P = p_T per DECISION; 1.0 per
        eval reconstruction.

  - **1.3 Determine Committed Turn Line** — SUPERVISORY (the Line Sequencer, the brief's
    "sequencing agent")
    - *Result:* at each eligible entry (and on invalidation), a `CommittedLine`, or None,
      meaning "the EV path owns this phase".
    - **1.3.1 Match Line Library Entries** — TOOL.
      - `match_lines(key, class_key, library) → tuple[Line, ...]`: the active lines whose
        preconditions hold, in `priorities[class_key]` order. Tier 0 always contributes
        `AssemblyState.best_line`, lifted to a `Line` by `ai/line_library.lift_best_line`.
      - FSD 1.3.1 | Result: candidate Lines (≤ `LINE_CANDIDATES_MAX`) | SL ≤ 20 µs |
        P = p_E·p_L.
    - **1.3.2 Bind Line Steps To Menu Members** — TOOL. **Selection only; no legality
      predicate is called here** (§3.6).
      - Each step picks a `HotMenu` member by verb and shape, sets `x_value` by `x_policy`
        from the menu's legal X range, and picks targets by `target_role` **from the menu's
        `enumerate_legal_targets` output**. Ties break by ascending `instance_id`, which is
        deterministic (§5.3).
      - FSD 1.3.2 | Result: BoundLine | None | SL ≤ 100 µs per line | P = p_E·p_L per line.
    - **1.3.3 Project Line Outcome** — TOOL.
      - `project_line(bound, frame) → LineProjection`, from `sink_damage` (:244) and
        `attack_reach` (:125) only. It does **not** use `compute_play_ev` or `VirtualBoard`,
        so the sequencer is decoupled from the EV path it competes with.
      - FSD 1.3.3 | Result: LineProjection | SL ≤ 200 µs per line | P = p_E·p_L per line.
    - **1.3.4 Price Line Resolution Probability** — TOOL.
      - The product over spell steps of `ai.assembly_state.p_resolves_cast` (made public in
        P2). The tax branch goes through `project_counter_tax_payment` (:2697) and the P3a
        posterior. Activation steps are priced at 1.0 against spell counters.
      - FSD 1.3.4 | Result: float | SL ≤ 50 µs per line | P = p_E·p_L per line.
    - **1.3.5 Rank Candidate Lines** — TOOL.
      - When a line is lethal, its score is `p_resolves × win_swing(snap)` (:766).
        Otherwise it is the `position_value` delta of its projection.
      - The EV path's top play is projected as a one-step line with the **same** projector,
        so both are compared in one unit.
      - A dominance floor applies, and ties go to `(−score, line_id)`.
      - It returns None unless some line strictly beats the EV path's one-step projection.
      - FSD 1.3.5 | Result: CommittedLine | None | SL ≤ 20 µs | P = p_E·p_L.

  - **1.4 Select Seam Action Members** — SUPERVISORY (the Seam Executors; one branch with four
    classification instances: main-action, attack, offered-choice, reactive)
    - *Result:* exactly one engine-legal member per seam call.
    - **1.4.1 Maintain Committed Line Cursor** — TOOL. It holds all line state and is the
      single owner of the refusal and re-plan protocol (SOD §3.7.2).
      - `LineCursor` on the EVPlayer offers `peek`, `advance`, `revalidate` (through 1.4.6),
        `invalidate(reason)` and `clear()`. `clear()` runs at each turn boundary.
      - FSD 1.4.1 | Result: next bound step | None | SL ≤ 2 µs peek | P = 1.0 per
        `decide_main_phase` call.
    - **1.4.2 Select Main-Phase Action** — TOOL. The single fallback owner.
      - It realises the verbs `play_land`, `cast`, `activate`, the cast/activate half of
        `tutor_deliver`, and **`hold`**. For `hold`, it returns None, which ends the main
        phase with the step's held cards kept in hand; the reactive seams later decide
        whether to use them (1.4.5).
      - With no cursor step, it returns the unchanged `decide_main_phase` body (:420).
      - Side channels (`_last_activation_ability_index`, `_last_played_target_reason`) are
        set **from the executed ref**.
      - FSD 1.4.2 | Result: (action, card, targets) | None | SL ≤ 5 µs over the EV path |
        P = 1.0 per `decide_main_phase` call.
    - **1.4.3 Declare Line Attackers** — TOOL.
      - For the verb `attack_all`, it returns the `attack_reach` set, the same primitive
        that projected the lethal. Otherwise it returns the unchanged `decide_attackers`
        (:3035).
      - FSD 1.4.3 | Result: list[CardInstance] | SL ≤ 1 ms | P = 1.0 per `decide_attackers`
        call.
    - **1.4.4 Select Engine-Offered Choice Member** — TOOL. Uniform per callback kind.
      - It realises the delivery and X half of `tutor_deliver`. `choose_tutor_target` and
        `choose_x_value` **honour the cursor** through `choose_tutor_delivery`; this is not a
        tie-break.
      - `choose_sacrifice` honours `SacrificePolicy` while `engine_live`.
      - Other kinds are unchanged `AICallbacks` / `best_choice`.
      - Re-entrant calls, such as `should_evoke` inside `can_cast`, bypass the cursor.
      - FSD 1.4.4 | Result: a member of the callback's legal list | SL ≤ 50 µs added |
        P = 1.0 per top-level callback.
    - **1.4.5 Choose Reactive Seam Decisions** — TOOL. The existing choosers, unchanged:
      `decide_blockers` (:3864), `decide_response` (:4132), `decide_combat_trick`,
      `decide_flash_deploy`, `decide_optional_recast`, `decide_mulligan` and
      `choose_cards_to_bottom`.
      - FSD 1.4.5 | Result: the seam's native type | SL = today's | P = 1.0 per reactive seam
        call.
    - **1.4.6 Validate Chosen Action Membership** — TOOL (a lifted auxiliary). It re-checks at
      **execution time** that the ref is still a member by identity, using pure predicates:
      `target_solver.can_be_targeted`, `ActivationManager.can_activate`,
      `CastManager.can_cast`, and a pure public wrapper over `CombatManager._can_block`.
      - FSD 1.4.6 | Result: ActionRef | Refusal | SL ≤ 100 µs | P = p_C.

  - **1.5 Verify Decision Correctness** — SUPERVISORY (the Decision Verifier; deterministic,
    keyless, CI plus weekly)
    - *Result:* a graded, reconstructable golden corpus; per-run decision-quality findings;
      and a typed movement verdict per unit.
    - **1.5.1 Reconstruct Golden Decision States** — TOOL (SOD §3.7.3).
      - `reconstruct(ref) → CapturedDecision | Stale`.
      - FSD 1.5.1 | Result: CapturedDecision | Stale | SL ≈ one game prefix (p50 0.3 s, p95
        1.4 s) after the shared DB load | P = 1.0 per golden or fixture.
    - **1.5.2 Grade Decisions Against Deterministic Oracles** — TOOL.
      - `grade(captured) → GradeVerdict`. It checks legal-set parity against
        `_last_candidates`, applies the lethal oracle (`best_line` + `attack_reach`), checks
        `replay_lint` R6 (pick not in the menu), and evaluates the expected action class.
      - FSD 1.5.2 | Result: GradeVerdict | SL < 50 ms | P = 1.0 per captured decision.
    - **1.5.3 Record Decision-Quality Findings** — TOOL. AI-side and opt-in
      (`MTG_DECISION_AUDIT`, instrumentation only).
      - `ai/decision_audit.check("line/lethal_missed" | "line/step_refused", …)`.
        `GameRunner` transports the findings as `GameResult.decision_findings`. The engine
        judges nothing (§7.3).
      - FSD 1.5.3 | Result: DecisionFinding rows | SL ≤ 5 µs per check | P = p_E·p_L when
        enabled, 0 otherwise.
    - **1.5.4 Curate Golden Decision Corpus** — TOOL with a HITL promotion step. This is the
      missing golden-creation leaf.
      - `curate(candidate) → GoldenDecisionRef | Refusal`. It ingests the four sources of
        §7.2, applies the name probe and the class-size ≥ 10 rule, requires red-on-record,
        and assigns CI tier or weekly tier by recorded reconstruction cost. It also enforces
        the STALE policy and offers `reanchor`.
      - FSD 1.5.4 | Result: GoldenDecisionRef | SL < 1 s plus human review | P = 1.0 per
        candidate.
    - **1.5.5 Assess Behaviour Unit Movement** — TOOL (uses `measure_lane`).
      - A *behaviour unit* is one commit that changes sim decisions and targets one deck's
        field. The project's word "lane" means the sequence of such units on one deck.
      - Output is `MovementVerdict` (§7.4), including the git-log loop-break counter.
      - FSD 1.5.5 | Result: MovementVerdict | SL ≈ 50–60 min per unit | P = 1.0 per
        candidate unit.

  - **1.6 Investigate Decision Divergences** — SUPERVISORY (the Divergence Investigator;
    research, eval time, paid)
    - *Result:* measured divergences, each converted to a library entry proposal, a
      rule-phrased unit or a recorded refusal; or a falsification doc.
    - **1.6.1 Propose Shadow Turn Lines** — AGENT (reasoning).
      - `ShadowProposal` from `ShadowInput` over a *reconstructed* context.
      - FSD 1.6.1 | Result: ShadowProposal | SL ≤ 300 s | P = 1.0 per sampled state (K per
        run).
    - **1.6.2 Measure Decision Outcome Divergence** — TOOL (SOD §3.7.4).
      - FSD 1.6.2 | Result: DivergenceMeasurement | SL ≈ 2n × one game (n = 20: ≈ 10 s mean,
        ≤ 60 s p95) | P = 1.0 per proposal whose first step ≠ the deterministic pick.
    - **1.6.3 Diagnose Measured Divergence Mechanism** — AGENT (reasoning).
      - `JudgeVerdict` from `DiagnosticianInput`. Invoked only when the measurement is
        `MEASURED` and clears `DIVERGENCE_MOVEMENT_PP`.
      - FSD 1.6.3 | Result: JudgeVerdict | SL ≤ 300 s | P = the measured clearing fraction,
        which P5 reports before quoting it.
    - **1.6.4 Specify Rule-Phrased Unit** — AGENT (extraction), using the existing
      `failing_test_spec` task (prompt v2).
      - FSD 1.6.4 | Result: FailingTestSpec + UnitSpec | SL ≤ 60 s | P = 1.0 per verdict
        with `correct_side = "proposal"`.

### 3.4 Completeness: necessity and sufficiency per non-leaf

Two tests per non-leaf:

- **necessity:** removing one child makes the parent fail;
- **sufficiency:** the children jointly produce the parent's whole result.

**Root 1.**

*Necessity:*

- Remove 1.1: lines exist only at tier 0, and the deferred seam owners (RAMP, delivery,
  sacrifice) have no data.
- Remove 1.2: each sibling re-derives state from `GameState` (the "four half-derivations"
  failure).
- Remove 1.3: plays are chosen single-ply per iteration, so a multi-iteration line is
  re-decided and can be abandoned.
- Remove 1.4: no seam receives a line-consistent member.
- Remove 1.5: "correct" is unverifiable, and bad data or regressing units can be committed.
- Remove 1.6: two consequences.
  - Grader failures from 1.5.2 have no mechanism-naming path other than manual 5-panel
    reading.
  - 1.1 has no line source for hold and control states the census-driven librarian never
    sees.

  1.6 is the lowest-value branch. Its falsification exit (P5) retires it by a documented
  root restatement, not silently.

*Sufficiency:* output (a) = 1.2 view → 1.3 line (or None) → 1.4 member, where 1.4.6 makes
the member legal and 1.4.1 keeps it line-consistent. (b) = 1.2.7 plus the evidence written by
1.4. (c) = 1.1. (d) = 1.5. (e) = 1.6. The root's plan (§3.8) invokes 1.1, 1.5 and 1.6, so
nothing in the root's result depends on an unowned invocation.

**1.1.**

*Necessity:*

- Remove 1.1.1: there is no content address.
- Remove 1.1.2: there are no fixtures, so legality and ranking cannot run.
- Remove 1.1.3: there are no lines beyond tier 0.
- Remove 1.1.4: priorities are undefined.
- Remove 1.1.5: the deferred owners have no data.
- Remove 1.1.6: unverified model output reaches sims.

*Sufficiency:* census → fixtures → draft → ranking → sections → admission yields exactly
the artifact and sections in 1.1's result. A draft that fails admission yields the recorded
refusal.

**1.1.6.**

*Necessity:* each verdict guards a distinct failure: malformed or stale data, an illegal
step, a false lethal claim, a WR regression, a CPU overrun.

*Sufficiency:* the five verdicts are exactly the gating rule of §7.5.

**1.2.**

*Necessity:*

- Remove 1.2.1: there is no hot-path view, so siblings would read `GameState` directly.
- Remove 1.2.2: there are no members to bind.
- Remove 1.2.3: pricing has no beliefs, so all-in lines into open counter mana win.
- Remove 1.2.4: engine and sink facts are unknown.
- Remove 1.2.5: the key has no posture, so hold and race are indistinguishable.
- Remove 1.2.6: there is no finite lookup.
- Remove 1.2.7: eval agents and evidence have no serialisable state.

*Sufficiency:* `LineFrame` = 1.2.1 ∘ {1.2.2, 1.2.3, 1.2.4, 1.2.5, 1.2.6}, and
`DecisionContext` = 1.2.7 over the same reads.

**1.3.**

*Necessity:*

- Remove 1.3.1: no committed knowledge is consulted.
- Remove 1.3.2: an abstract line cannot execute.
- Remove 1.3.3: ranking becomes opinion.
- Remove 1.3.4: an all-in spell outranks an uncounterable ability.
- Remove 1.3.5: nothing is committed.

*Sufficiency:* match → bind → project → price → rank yields a `CommittedLine` or None.

**1.4.**

*Necessity:*

- Remove 1.4.1: a line cannot survive across iterations or refusals.
- Remove 1.4.2, 1.4.3 or 1.4.4: that seam kind ignores the line. The return contracts are
  disjoint: a tuple, a list, a callback member.
- Remove 1.4.5: blocks, responses, mulligan, recast and flash have no chooser.
- Remove 1.4.6: an out-of-set member can reach the engine.

*Sufficiency:* **every line verb has a realising leaf, and every `SeamKind` maps to exactly
one leaf.** `tests/test_seam_coverage.py` pins both tables against the Literals.

| Line verb | Realised by |
|---|---|
| `play_land`, `cast`, `activate` | 1.4.2 |
| `tutor_deliver` | 1.4.2 (cast or activate the tutor) + 1.4.4 (target + X) |
| `attack_all` | 1.4.3 |
| `hold` | 1.4.2 (returns None; held cards stay in hand) |

| SeamKind | Leaf |
|---|---|
| `main_phase` | 1.4.2 |
| `attackers` | 1.4.3 |
| `callback:*`, including `callback:x_value` | 1.4.4 |
| `blockers`, `response`, `combat_trick`, `flash_deploy`, `optional_recast`, `mulligan`, `bottom` | 1.4.5 |

**1.5.**

*Necessity:*

- Remove 1.5.1: grades refer to unreachable states.
- Remove 1.5.2: there is no correctness signal.
- Remove 1.5.3: there is no matrix-wide decision-quality metric beyond WR.
- Remove 1.5.4: goldens are neither created, promoted nor kept fresh.
- Remove 1.5.5: units land without the movement, anchor and loop-break rules.

*Sufficiency:* corpus = 1.5.4 ∘ 1.5.1 ∘ 1.5.2; findings = 1.5.3; verdict = 1.5.5.

**1.6.**

*Necessity:*

- Remove 1.6.1: there are no candidate lines outside the library.
- Remove 1.6.2: diagnosis runs on narrative (the 2026-05-16 incident).
- Remove 1.6.3: there is no mechanism, subsystem or class size.
- Remove 1.6.4: a fix ships without a failing test.

*Sufficiency:* propose → measure → diagnose → specify yields each divergence's disposition.

### 3.5 Coupling basis

- **Function-biased.** Branches are grouped by the result each hands the root: knowledge,
  view, line, member, verification, research. They are not grouped by module, model tier or
  turn step.
- **Result dependency, not time.** The sibling order is result dependency. That order
  coincides with lifecycle order (compile before sim before eval), but it is justified by the
  results, not by the clock.
- **Time ordering appears only inside SODs** (§3.7): cursor revalidation, reconstruction,
  continuation. There is no Untap/Main/Combat node, because `TurnManager.iterate_turn` owns
  step order.
- **1.4's children are split by result entity**: a main-action tuple, an attacker list, a
  callback member, reactive choices, a membership verdict. Blocks and responses share 1.4.5
  because neither is line-driven.
- **1.2's children are split by source entity**: frame, legal set, beliefs, assembly, plan,
  key, serialisation.
- **No LLM branch.** Model tier is a leaf property, so AGENT and TOOL leaves sit together in
  1.1 and 1.6.
- **1.5 and 1.6 are separate on purpose.** 1.5 is deterministic, keyless and CI-run. 1.6 is
  paid and research-only. The previous draft mixed them, which weakened tool cohesion.

### 3.6 Rationalisation

**Lifted auxiliaries.** Each shared tool is declared once.

| Auxiliary | Home leaf | Also used by |
|---|---|---|
| `hot_menu` / `action_menu` | 1.2.2 | 1.3.2, 1.4.6, 1.5.2, 1.6.1 |
| `validate_member` | 1.4.6 | 1.4.1 (revalidate), 1.4.2–1.4.4 |
| `measure_lane` (`tools/measure_lane.py`) | 1.5.5 | 1.1.6.4 |
| `reconstruct` | 1.5.1 | 1.1.2, 1.1.6.2, 1.6.2 |
| sim-time tools 1.3.2–1.3.5 | 1.3 | 1.1.4 (ranking), 1.1.6.3 (lethality) |

**Legality is checked twice, at two different times, by one set of predicates.**

1. **Enumeration (1.2.2, time t₀).** The menu *is* the engine's legal set.
2. **Execution (1.4.6, time t₁ ≥ t₀).** Response windows or refusals may have changed the
   state, so membership is re-checked by the same pure predicates.

Binding (1.3.2) only *selects* from the menu. It calls no predicate.

**Single owners.**

- The refusal and re-plan protocol belongs to 1.4.1.
- Fallback to the EV path belongs to 1.4.2. 1.4.3 and 1.4.4 fall back by construction when
  the cursor is empty.

**The Provenance Keeper is a cross-cutting service, not an FSD node.** It wraps AGENT leaves
1.1.3, 1.1.5, 1.6.1, 1.6.3 and 1.6.4 as an SOD decorator (§3.7.1). Replay verification is an
eval **test**, not a function.

**Classification sub-types.**

- 1.4 has four executor instances.
- AGENT leaves are either reasoning (1.1.3, 1.1.5, 1.6.1, 1.6.3) or extraction (1.6.4).
- Goldens are sub-typed by `SeamKind`.

**Names.** Every node is Verb + Qualified Object with no means or locative clause. Renames
from the previous draft:

| Previous name | New name |
|---|---|
| Deliver Correct Play At Every Engine Decision Seam | Produce Correct Engine-Seam Decisions |
| Choose Legal Action At Each Engine Seam | Select Seam Action Members |
| Admit Compiled Artifact Through Policy Gate | Admit Compiled Knowledge Artifact |
| Measure Decision Divergence By Forward Simulation | Measure Decision Outcome Divergence |
| Assess Lane Movement | Assess Behaviour Unit Movement ("unit" is defined in 1.5.5) |

### 3.7 Sequence of Operations Diagrams (SOD)

#### 3.7.1 Provenance Keeper (compile and eval processes only)

```
caller(AGENT leaf) → Keeper.call(task, payload)
 1  env  = AgentEnvelope(task, run_id, output_schema_sha, behaviour_digest, prior_outputs_digest, payload)
 2  key  = llm_cache.cache_key(task, model, prompt_version, env)
 3  if mode == REPLAY:
       row = ledger_dict.get(key)                 # in-memory; no SQLite, no check_budget, no CallTimer
       row is None            → raise ReplayMiss(key, coordinate)          # abort the whole replay
       row.kind == "failure"  → return AgentFailure(row.reason)            # replays the recorded failure
       else                   → return output_cls.model_validate_json(row.output)
                                 (ValidationError → raise ReplaySchemaDrift; never a fallback)
 4  run caps: spent_usd + estimate > max_usd or calls >= max_calls → write PARTIAL marker; raise RunBudgetExceeded
 5  result = build_agent(task, model_settings=spec.settings).run_sync(env)   # native SDK timeout, max_retries=0
 6  success: validate → name_probe → append ledger row(kind="output", raw, usage) → return output
 7  failure (timeout | ValidationError | stop_reason in {max_tokens, refusal} | name_probe):
       append ledger row(kind="failure", reason) → return AgentFailure(reason)
       — a cancelled/timed-out call writes to NO cache (CachedAgent store is skipped when cancelled)
caller policy on AgentFailure — NO substitution of any pick:
   compile (1.1.3/1.1.5): count as a rejected draft; retry ≤ COMPILE_RETRIES_MAX; then a recorded refusal
   research (1.6.x): the state is reported "skipped: <reason>" in the run report and excluded from rates
```

The previous draft's "Substitute Deterministic Pick For Failed Agent Output" is removed. A
compile or research AGENT leaf has no deterministic pick to substitute. Sims, which do have
one, make no model calls.

#### 3.7.2 Cursor revalidation (1.4.1), per `decide_main_phase` call

```
EVPlayer.decide_main_phase(game, excluded_cards, excluded_activations)
 1  coord = counter.enter("main_phase")                     # first statement; §3.7.3
 2  … existing EV-path body runs unchanged up to its scored candidate list
    (captures the pre-filter enumeration for 1.2.2) …
 3  if cursor.turn != game.turn_number: cursor.clear()
 4  step = cursor.peek()
 5  if step and step.ref.identity in excluded_*:             # engine refused it last iteration
        cursor.invalidate("refused"); decision_audit.check("line/step_refused", …)
 6  elif step: ok = validate_member(step.ref, hot_menu)      # 1.4.6, identity + pure predicates
        if not ok: cursor.invalidate("stale_after_window")
 7  if cursor.empty and frame := frame(self, game) (eligible) and cursor.replans_this_phase < REPLAN_MAX_PER_PHASE:
        line = sequencer.commit(frame)                       # 1.3; None ⇒ EV path
        cursor.load(line); cursor.replans_this_phase += 1 if invalidated else 0
 8  step = cursor.peek()
    step is None          → return EV-path result (unchanged)           # single fallback owner
    step.verb == "hold"   → cursor.advance(); return None                # ends the phase
    else                  → set side channels from step.ref; cursor.advance(); return step.ref.as_tuple()
```

New cards drawn mid-phase **do not** trigger a re-plan unless the next step fails
validation. Re-plans are capped at `REPLAN_MAX_PER_PHASE = 1`, a named constant with a
rule-phrased test.

#### 3.7.3 Decision coordinates and halting at a coordinate (`StopAtDecision`)

```
ai/decision_coordinate.CoordinateCounter   (created by GameRunner.run_game per game;
                                            handed to both EVPlayers and to AICallbacks)
 enter(seam_kind):
   if self.depth > 0: return None             # re-entrant (should_evoke inside can_cast) — not counted
   self.depth += 1 (decremented by exit())
   coord = DecisionCoordinate(game_number, turn, actor, seam_kind, seq[(turn, actor, seam_kind)]++)
   if self.stop_at == coord: raise DecisionReached(coord, game, player)   # BEFORE any mutation by this seam
   if self.overrides and coord in self.overrides: self.pending_override = self.overrides[coord]
   return coord
```

- **Counted in every mode.** The counter runs untraced too, and never touches
  `ReplayLog._decision_seq`, which stays DECISION/RESPONSE_DECISION-only.
- **Halt mechanism: an exception raised at seam *entry*.** `enter` is the first statement of
  every seam method, before `check_transition` or any `_last_*` write. When it raises, the
  engine is between actions: the previous action has fully resolved, and the current seam
  has mutated nothing.
- **What the caller does.** The eval runner wraps `_run_pair` / `run_bo3` and catches
  `DecisionReached`. It keeps `game` and `player` as a `CapturedDecision`. It may call the
  seam once, on that captured state, to obtain the deterministic pick. It **never resumes**
  the game, and discards the state afterwards.
- **Budget.** Reconstruction rebinds `GAME_TIMEOUT_SECONDS` to `_ANCHOR_TIMEOUT_SECONDS`, as
  the anchor does.
- **Anchor check.** The capture compares `(turn, phase, actor, chosen card, candidates_n)`
  against the golden's anchor. Chosen card and `candidates_n` are read by running the seam
  once. A mismatch returns `Stale`.
- **Tie-break determinism.** `instance_id` comes from a per-`GameState` monotonic counter
  starting at 1 (engine/game_state.py:101, :132). Allocation order is the decklist dict order
  at deck load, then creation order during resolution. Each game has a new `GameState`, so
  G2 and G3 restart at 1. `tests/test_instance_id_determinism.py` pins this: two fresh
  processes with different `PYTHONHASHSEED` values produce the same `(instance_id, name)`
  sequence for the first 200 creations of seeds 50000 and 50500 in G1, G2 and G3.

#### 3.7.4 Forward-simulation continuations (1.6.2)

```
measure(ref, pick_a, pick_b, n):
 for i in 0..n-1:
   seed_i = int.from_bytes(sha256(f"{ref.canonical()}|continuation|{i}").digest()[:8], "big")
   for pick in (pick_a, pick_b):                          # common random numbers: same seed_i for both
     runner = GameRunner(db, rng=Random(ref.seed_for_path()),
                         decision_overrides={ref.coordinate: pick.identity},
                         after_coordinate=Reseed(seed_i))
     result = run under ref.seeding path (same prefix as the recording)
       · at ref.coordinate, 1.4.2/1.4.3/1.4.4 consume pending_override (identity must be in the menu,
         else OverrideNotInMenu → measurement refused as STALE)
       · after that seam returns: game.rng.seed(seed_i); game.rng.shuffle(p.library) for both players
     record (winner, turns)
 distinct = |{outcome pairs}|;  distinct < DIVERGENCE_MIN_DISTINCT_OUTCOMES → INSUFFICIENT_VARIANCE
 else wr_delta_pp, turns_delta, discordant count → MEASURED
```

- **The prefix is re-run from the seed.** The design does not fork a deep copy.
  - Why not fork: `GameState` has no `clone`. `deepcopy` of `GameState`, both EVPlayers, BHI
    and the cursor is unverified for aliasing: the `Random` is shared, and callbacks hold
    references.
  - Cost: each continuation is one game. That is 2n games per divergence, about 10 s mean at
    n = 20 (0.26 CPU-s per game), and ≤ 60 s p95 on Storm tails, after one DB load per
    process. There is no additional "reconstruction" term, because the prefix *is* the
    continuation's first part.
  - A future deepcopy fork would need a test that a forked game finishes byte-identical to a
    re-run.
- **How hidden libraries are reshuffled.** The reshuffle uses `game.rng.shuffle`, the
  primitive every engine shuffle site uses; there is no single shuffle-owner function.
- **Refusal when library order is known.** The measurement is refused at any coordinate
  after a resolved effect that revealed or ordered library cards (scry, surveil,
  look-at-top) in that game. `decision_audit` records that fact per game, and reshuffling
  would violate known information there.

#### 3.7.5 Decision Supervisor plan (root, outside the sim)

```
decision_supervisor.py status  (keyless; CI runs `status --strict`)
  stale libraries (declared id ≠ census)            → FAIL (CI)
  library line_path_digest ≠ current                → FAIL (CI)
  verdict-stale libraries (behaviour drift)          → WARN + action reverify(lib)
  STALE goldens: CI tier > 0                         → FAIL; full corpus > 20%  → FAIL
  calibration OUT findings ∪ decision-audit rises    → action select_lane(deck)
decision_supervisor.py plan    → SupervisorPlan{actions[], each: trigger, fsd_ref, est_usd, est_wall,
                                  requires_key, requires_quiet_box}
decision_supervisor.py run ID  → executes one action; spend actions require --max-usd and an interactive
                                  confirmation (HITL); quiet-box actions check /proc/loadavg first
```

### 3.8 Physical mapping: programmable versus HITL

| Function | Physical form | Human in the loop |
|---|---|---|
| 1 (root) | **sim time:** the in-process dispatch inside the EVPlayer seam methods and `AICallbacks`. **Otherwise:** `tools/decision_supervisor.py` (`status` / `plan` / `run`), which derives every compile, re-verify, curation and research action from repo state. It is stateless, and its plan is a pure function of files and digests | a human approves spend (`--max-usd`), quiet-box time and golden promotion. The human approves; the supervisor sequences |
| 1.1 | `tools/compile_decision_knowledge.py`, with 1.1.6.1–1.1.6.3 also in `tools/validate_line_library.py` (CI) | spend approval; artifact PR review |
| 1.2–1.4 | pure Python in the sim process (§3.1 flat modules) | none |
| 1.5 | `tools/golden_reconstruct.py`, `tools/decision_grader.py`, `ai/decision_audit.py`, `tools/golden_curate.py`, `tools/measure_lane.py`. CI tier in `abstraction-contract.yml`; full corpus in a new `weekly.yml` step | golden promotion; loop-break halt doc |
| 1.6 | `tools/shadow_line_probe.py`, `tools/divergence_measure.py` | spend approval; triage of verdicts |

---

## 4. Agent topology (derived from the FSD)

**Rules.**

- One branch is one agent boundary.
- A branch agent is a supervisory program. Models appear only inside AGENT leaves.
- A tool is called only by its own branch or is a declared lifted auxiliary (§3.6).
- Sim-time agents import nothing from `ai/llm_*` or `pydantic_ai`.

### 4.0 Shared schemas (all types referenced anywhere in this document)

```python
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

# ── coordinates, seams ─────────────────────────────────────────────────────────
SeamKind = Literal["main_phase", "attackers", "blockers", "response", "combat_trick",
                   "flash_deploy", "optional_recast", "mulligan", "bottom",
                   "callback:optional_cost", "callback:fetch", "callback:evoke", "callback:kick",
                   "callback:dash", "callback:discard", "callback:offered_cast",
                   "callback:sacrifice", "callback:artifact_tutor", "callback:mana_color",
                   "callback:tutor", "callback:x_value"]

class DecisionCoordinate(_Frozen):
    game_number: int; turn: int; actor: int; seam_kind: SeamKind; seam_call_seq: int

# ── menu ───────────────────────────────────────────────────────────────────────
ActionVerb = Literal["play_land", "cast_spell", "activate", "activate_ability", "equip", "cycle",
                     "suspend", "plot", "cast_plotted", "attack", "block", "pass"]

class ActionRef(_Frozen):
    ref_id: int                                   # index in menu; stable within one decision
    seam: SeamKind
    action: ActionVerb
    instance_id: int
    ability_index: int | None = None
    x_value: int | None = None                    # member of the engine's legal X range
    target_ids: tuple[int, ...] = ()              # -1 = opponent face; PLAYER_TARGET_SELF sentinel
    def identity(self) -> tuple: ...              # (action, instance_id, ability_index, x_value, target_ids)

class HotMenu(_Frozen):                           # hot path; no digest
    refs: tuple[ActionRef, ...]
    x_ranges: dict[int, tuple[int, int]]          # instance_id -> legal (min, max) from engine query

class ActionMenu(HotMenu):                        # eval / trace
    menu_digest: str                              # sha256 of sorted identity tuples

# ── views (DecisionContext is eval/evidence only) ──────────────────────────────
class CardView(_Frozen):
    instance_id: int; name: str; mana_value: int; types: tuple[str, ...]
    typed_flags: tuple[str, ...]                  # e.g. "x_cost","tutor","team_pump" (typed fields as data)
    oracle_summary: str = Field(max_length=200)

class PermanentView(_Frozen):
    instance_id: int; name: str; power: int | None; toughness: int | None
    tapped: bool; summoning_sick: bool; counters: dict[str, int]
    activated_abilities: tuple[tuple[int, str], ...]   # (index, kind)

class PlayerView(_Frozen):
    life: int; mana_by_color: dict[str, int]; hand_count: int
    hand: tuple[CardView, ...] | None             # None for the opponent
    battlefield: tuple[PermanentView, ...]; graveyard_count: int; library_count: int

class StackView(_Frozen):
    source_instance_id: int; controller: int; kind: Literal["spell", "ability"]; target_ids: tuple[int, ...]

class LineView(_Frozen):
    line_id: str; verbs: tuple[str, ...]; lethal: bool; p_resolves: float; projected_damage: int

class AssemblyView(_Frozen):                      # mirror of AssemblyState (no re-derivation)
    engine_live: bool; engine_spins_now: bool; mana: int | Literal["unbounded"]
    sink_access: tuple[str, ...]; best_line: LineView | None

class ClockView(_Frozen):                         # quantised by CLOCK_QUANTUM; never in a key
    my_turns_to_lethal: float; opp_turns_to_lethal: float; opp_one_turn_damage: int
    position_value: float; has_lethal: bool; am_dead_next: bool

class BeliefView(_Frozen):
    p_counter: float; p_removal: float; p_interaction: float
    p_soft_counter_tax: float | None              # None until P3a
    opp_open_mana: int

PostureLiteral = Literal["develop", "assemble", "hold", "race", "all_in", "stabilise"]

class PostureView(_Frozen):
    goal_type: str                                # GoalType enum value (closed)
    posture: PostureLiteral

class DecisionContext(_Frozen):
    coordinate: DecisionCoordinate; my: PlayerView; opp: PlayerView; stack: tuple[StackView, ...]
    menu: ActionMenu; key: "AbstractAssemblyKey"; assembly: AssemblyView; clocks: ClockView
    beliefs: BeliefView; posture: PostureView; goal: str   # goal name as data only

# ── abstract key (§5.2) ────────────────────────────────────────────────────────
SinkAccess = Literal["battlefield", "hand", "tutorable"]

class AbstractAssemblyKey(_Frozen):
    engine_state: Literal["off", "live", "spinning"]              # spinning implies live
    sink_access: tuple[SinkAccess, ...]                           # sorted, unique; () = none
    mana_band: Literal["0-2", "3-4", "5-7", "8+", "unbounded"]
    opp_open_mana_band: Literal["0", "1", "2", "3+"]
    opp_interaction: Literal["none", "soft", "hard"]
    lethal_reach: Literal["none", "with_line", "on_board"]
    posture: PostureLiteral
    def key(self) -> str: ...        # sha256(json.dumps(model_dump(), sort_keys=True))
    def class_key(self) -> str: ...  # sha256 of (opp_interaction, opp_open_mana_band, posture) ONLY

# ── compile vocabulary: closed Literals, no float weight / EV / score field ────
ShapeToken = Literal["x_damage_sink", "team_pump_sink", "team_counters_sink", "mana_engine_piece",
                     "engine_completer", "activated_tutor", "x_creature_tutor", "cantrip",
                     "removal_targeted", "counter_hard", "counter_tax", "haste_enabler",
                     "land_ramp", "payoff_big_mana"]     # grows only by PR (class-size ≥ 10 each)

class Predicate(_Frozen):
    field: Literal["engine_state", "sink_access", "mana_band", "opp_open_mana_band",
                   "opp_interaction", "lethal_reach", "posture"]
    op: Literal["eq", "in", "ge_band", "le_band", "contains"]
    value: str | tuple[str, ...]

class LineStep(_Frozen):
    verb: Literal["play_land", "cast", "activate", "tutor_deliver", "attack_all", "hold"]
    shape: ShapeToken
    x_policy: Literal["none", "max_affordable", "min_lethal", "keep_tax_payable"] = "none"
    target_role: Literal["none", "opp_face", "largest_blocker", "own_sink"] = "none"
    deliver_shape: ShapeToken | None = None       # tutor_deliver only

class Line(_Frozen):
    line_id: str                                  # sha256 of canonical dump
    purpose: Literal["lethal_now", "assemble_engine", "hold_interaction", "develop"]
    preconditions: tuple[Predicate, ...]
    steps: tuple[LineStep, ...] = Field(min_length=1, max_length=8)

class RankingTable(_Frozen):                      # output of TOOL 1.1.4 (replaces RankingDraft)
    priorities: dict[str, tuple[str, ...]]        # class_key -> ordered line_ids (≤ 72 keys)
    fixtures_used: dict[str, tuple[str, ...]]     # class_key -> fixture ids

class Verdict(_Frozen):
    name: Literal["integrity", "legality", "lethality", "regression", "cost"]
    passed: bool; detail: str
    measured_at_behaviour_digest: str; measured_at_line_path_digest: str

class GateVerdict(_Frozen):
    verdicts: tuple[Verdict, ...]                 # exactly five
    def admitted(self) -> bool: ...               # all passed

class GateProvenance(_Frozen):
    gate: GateVerdict; policy_hash: str; compile_ledger: str
    models: dict[str, str]; prompt_versions: dict[str, int]; run_id: str

class LineLibrary(_Frozen):
    schema_version: str; library_id: str; lines: tuple[Line, ...]
    ranking: RankingTable; provenance: GateProvenance

class GoalTransitionRule(_Frozen):                # read by GoalEngine.check_transition
    from_goal_type: str; to_goal_type: str        # GoalType values (closed)
    when: tuple[Predicate, ...]                   # over AbstractAssemblyKey fields only

class TutorDeliveryPolicy(_Frozen):               # tie-break *within* choose_tutor_delivery's tier
    prefer_shapes: tuple[ShapeToken, ...]

class SacrificePolicy(_Frozen):                   # protected while engine_state != "off"
    protected_shapes: tuple[ShapeToken, ...]

class SeamPolicyDraft(_Frozen):
    goal_transitions: tuple[GoalTransitionRule, ...] = ()
    tutor_delivery: TutorDeliveryPolicy | None = None
    sacrifice: SacrificePolicy | None = None

class VocabularySpec(_Frozen):                    # generated from the Literals above, never hand-written
    shape_tokens: tuple[str, ...]; verbs: tuple[str, ...]; x_policies: tuple[str, ...]
    target_roles: tuple[str, ...]; predicate_fields: tuple[str, ...]; goal_types: tuple[str, ...]

# ── sim-time line objects ──────────────────────────────────────────────────────
class LineProjection(_Frozen):
    mana_after: int; damage_projected: int; lethal: bool; cards_used: tuple[int, ...]
    position_value_delta: float

class BoundLine(_Frozen):
    line_id: str; tier: Literal[0, 1]; steps: tuple[tuple[LineStep, ActionRef | None], ...]
    holds: tuple[int, ...]                        # instance_ids kept for hold steps

class CommittedLine(_Frozen):
    bound: BoundLine; projection: LineProjection; p_resolves: float; score: float

# LineFrame is internal (not pydantic): ev_player, game (read-only by contract), idx, snap,
# assembly, hot_menu, beliefs, posture, key — built once per eligible entry.

# ── verification ───────────────────────────────────────────────────────────────
MechanismClass = Literal["sequencing_line_abandoned", "sink_not_activated", "all_in_into_counter",
                         "tutor_delivery_mismatch", "x_mis_sized", "hold_misjudged",
                         "lethal_not_taken", "illegal_step", "goal_transition_late",
                         "goal_transition_early", "sacrifice_of_engine_piece", "other"]

class ExpectedActionClass(_Frozen):               # card-free predicate over the executed ref + projection
    action_in: tuple[ActionVerb, ...] | None = None
    shape_in: tuple[ShapeToken, ...] | None = None
    must_be_lethal_line: bool | None = None
    must_not_pass: bool | None = None
    x_value_rel: Literal["max_affordable", "keep_tax_payable", "min_lethal"] | None = None

class GoldenProvenance(_Frozen):
    source: Literal["replay_gate", "grader_failure", "panel_finding", "shadow_divergence"]
    red_on_behaviour_digest: str; green_on_commit: str | None
    promoted_by: str                              # reviewer handle; required
    recorded_reconstruct_cpu_s: float

class FixtureBoardSpec(_Frozen):                  # a real coordinate, not a synthetic board
    ref: "GoldenDecisionRef"; class_key: str

class GoldenDecisionRef(_Frozen):
    seed: int; deck1: str; deck2: str             # fixture data (like wr_baseline_anchor.json)
    seeding: Literal["run_pair_bo1", "run_pair_bo3", "run_bo3_per_game"]
    coordinate: DecisionCoordinate
    anchor: tuple[int, str, int, str, int]        # (turn, phase, actor, chosen card, candidates_n)
    expected: ExpectedActionClass
    mechanism: MechanismClass
    status: Literal["LIVE", "STALE"]
    tier: Literal["ci", "weekly"]
    provenance: GoldenProvenance

class GradeVerdict(_Frozen):
    legal: bool; parity_with_last_candidates: bool; lethal_available: bool; lethal_taken: bool
    expected_class_met: bool | None; lint_rows: tuple[str, ...]

class DecisionFinding(_Frozen):
    rule: Literal["line/lethal_missed", "line/step_refused"]
    seed: int; deck1: str; deck2: str; coordinate: DecisionCoordinate; detail: str

class MovementVerdict(_Frozen):
    replay_gate: bool; target_delta_pp: float; target_discordant: int; target_sign_p: float
    guards: dict[str, tuple[float, int, float]]   # deck -> (delta_pp, discordant, p_harm)
    guards_flat: bool; anchor_flips_justified: bool; aborted: int
    missed_lethal_rate_delta: dict[str, float]; loop_break_count: int
    decision: Literal["keep", "revert", "escalate_n60", "halt"]

# ── research ───────────────────────────────────────────────────────────────────
class ProposedLine(_Frozen):
    ref_ids: tuple[int, ...] = Field(min_length=1, max_length=8)   # menu ref_ids only
    hold_ref_ids: tuple[int, ...] = ()
    claims_lethal: bool
    rationale: str = Field(max_length=300)

class ShadowProposal(_Frozen):
    lines: tuple[ProposedLine, ...] = Field(min_length=1, max_length=4)
    committed_index: int

class DivergenceMeasurement(_Frozen):
    n: int; distinct_outcomes: int; discordant: int; wr_delta_pp: float; turns_delta: float
    verdict: Literal["MEASURED", "INSUFFICIENT_VARIANCE", "STALE", "LIBRARY_ORDER_KNOWN"]

class JudgeVerdict(_Frozen):
    correct_side: Literal["deterministic", "proposal", "neither", "tie"]
    mechanism_class: MechanismClass
    mechanism: str = Field(max_length=240)        # card-free, CR-phrased (name probe)
    subsystem: str                                # existing Subsystem Literal (ai/llm_schemas.py:56)
    owning_symbol: str                            # file:function
    class_size_estimate: int = Field(ge=10)       # < 10 ⇒ recorded refusal, not a golden
    rule_phrased_test_name: str
    decisive_log_line: str

class UnitSpec(_Frozen):                          # alongside the existing FailingTestSpec
    rule_name: str                                # name probe applies
    mechanism_class: MechanismClass
    owning_module: str; test_path: str; expected_red_golden: str
    kind: Literal["engine_rule_unit", "ai_decision_unit", "library_entry"]
```

### 4.1 Decision Supervisor (FSD 1, root)

- **Responsibility.**
  - At sim time: route each seam call to 1.2 → 1.3 (only at eligible entries or on
    invalidation) → 1.4.
  - Elsewhere: derive and sequence every 1.1, 1.5 and 1.6 action (§3.7.5).
  - It holds no state. The cursor belongs to 1.4.1, and the plan is recomputed from files.
- **When it runs.** Every seam call. `status` runs in CI and weekly. `plan` and `run` run on
  demand.
- **Tier.** Pure code, because routing and triggering have no uncertainty worth a model.
- **Schemas.** `DecisionCoordinate`, `SeamKind`; `SupervisorPlan{actions: tuple[Action, …]}`,
  where `Action{id, kind: Literal["compile", "retire", "reverify", "reanchor", "curate",
  "select_lane", "investigate"], trigger, fsd_ref, est_usd, est_wall_s, requires_key,
  requires_quiet_box}`.
- **Tools.** None of its own. It delegates.
- **Evals.**
  - The import graph and no-env-read AST test.
  - "an untraced run counts coordinates without touching the replay sequence".
  - "a re-entrant callback is not counted".
  - Byte-identity: with no library declared and no eligible `best_line` path taken, the
    anchor (27 entries) and a 3-seed GameResult + non-evidence NDJSON equal the pre-change
    commit.
  - "`status --strict` fails on a stale library, a line-path-stale library and a STALE
    CI-tier golden" (three fixtures).
  - "the plan is a pure function of repo state" (two calls on an unchanged tree give equal
    plans).

### 4.2 Knowledge Compiler (FSD 1.1, compile time)

- **Responsibility.** Turn a deck's union census, fixture coordinates and gameplan goals into
  a gated `LineLibrary` plus per-deck seam sections, or a recorded refusal.
- **When it runs.** Only through a supervisor `compile` action: a census change on a deck
  that declares a library, or a lane the plan selected.
- **Tiers and model ids.** These are pinned in the TaskSpec table (§4.9).
  - **Line Librarian (1.1.3): reasoning.** Default `anthropic:claude-sonnet-5`, with an A/B
    arm `anthropic:claude-opus-5`. The pass criterion is in §7.1.
  - **Seam Policy Writer (1.1.5): reasoning,** `anthropic:claude-sonnet-5`.
  - **Class ranking (1.1.4): pure code.** This replaces the Matchup Strategist.
- **Input schemas.**

```python
class AgentEnvelope(_Frozen):          # ONLY input shape for new tasks (conversation state inside the key)
    task: str; run_id: str; output_schema_sha: str; behaviour_digest: str
    prior_outputs_digest: str; payload: dict      # payload = one of the *Input models below, dumped

class LibrarianInput(_Frozen):
    census: tuple[tuple[str, int], ...]           # (ShapeToken, count), sorted
    goal_types: tuple[str, ...]                   # the deck's GoalEngine sequence as GoalType values
    vocabulary: VocabularySpec                    # generated from the Literals
    fewshot_ref: str                              # name-free typed-shape boards; disjoint from goldens
    rejected_prior: tuple[str, ...] = ()          # line_ids rejected by the gate in this run, with reasons

class SeamPolicyWriterInput(_Frozen):
    census: tuple[tuple[str, int], ...]
    goal_types: tuple[str, ...]
    card_role_shapes: dict[str, tuple[str, ...]]  # role -> ShapeTokens (from gameplan card_roles, name-free)
    lane_evidence: dict[str, float]               # e.g. per-goal mean turn reached, payoff-affordable turn
    vocabulary: VocabularySpec
```

- **Outputs.** `LineLibraryDraft{lines: tuple[Line, …]}` and `SeamPolicyDraft`.
- **Tools.** `census` (1.1.1), `fixture_coordinates` (1.1.2), `build_agent(task)` behind the
  Keeper, `rank_classes` (1.1.4, which reuses 1.3.2–1.3.5), and `policy_gate` (1.1.6.x).
- **Evals.** See §7.1: the gate-pass rate, reference-line recall on held-out decks, the seam
  golden score, the name probe, and the CI validator.

### 4.3 Situation Assessor (FSD 1.2)

- **Responsibility.** Provide one `LineFrame` per eligible entry, and a `DecisionContext`
  when traced or reconstructed.
- **When it runs.** At sim time with P = p_E for the gate and p_E·p_L for the frame; on every
  reconstruction at eval time.
- **Tier.** Pure code. Every leaf is a projection.
- **Tools.**

| Tool | FSD | Wraps |
|---|---|---|
| `frame` | 1.2.1 | `self._assembly`, `snap`, captured enumeration |
| `hot_menu` / `action_menu` | 1.2.2 | the EV path's pre-filter enumeration; `legal_x_range`; `enumerate_legal_targets`; full: `get_legal_plays`, `can_cast`, `activation_candidates`, `land_animation_candidates`, `get_valid_attackers` / `get_valid_blockers`, `available_responses` |
| `belief_view` | 1.2.3 | `BayesianHandTracker.get_*_probability`, P3a posterior |
| (read) | 1.2.4 | `self._assembly` / `assemble` |
| `posture` | 1.2.5 | `POSTURE_BY_GOAL_TYPE` over `GoalEngine.current_goal.goal_type` |
| `abstract_key` | 1.2.6 | band constants in `ai/scoring_constants.py` |
| `compile_decision_context` | 1.2.7 | `snapshot_from_game`, `snapshot_board` / `snapshot_state`, clock read-outs |

- **Evals.**
  - `DecisionContext` round-trips and holds no `Callable`.
  - Key purity, permutation invariance and the cross-process `PYTHONHASHSEED` test.
  - `sink_access` is always sorted and unique (validator).
  - "every GoalType maps to a posture" (totality).
  - Menu enumeration mutates nothing: `game.log`, the zones and `game.rng.getstate()` are
    unchanged, including under a re-entrant `should_evoke`.

### 4.4 Line Sequencer (FSD 1.3, the "sequencing agent")

- **Responsibility.** Commit at most one line per eligible entry, using committed knowledge
  plus tier 0, with every value computed by the landed projectors and one pricing formula.
- **When it runs.** At sim time, P = p_E·p_L. At compile time, inside 1.1.4 and 1.1.6.3.
- **Tier.** Pure code. The ISMCTS ceiling diagnostic showed that searching over a projector
  blind to combo payoff amplifies noise. This projector is `assembly_state`, which sees that
  payoff.
- **Tools.**
  - `match_lines`, which wraps the `ai/line_library.py` loader. The loader is `lru_cache`d,
    refuses an unknown schema major, raises `StaleLineLibraryError` (§2.3) and filters to the
    active shape set.
  - `bind_line`, which selects from the menu only.
  - `project_line`, over `sink_damage` and `attack_reach`.
  - `price_line`, over `p_resolves_cast`, `project_counter_tax_payment` and P3a.
  - `rank_lines`, over `win_swing`, `position_value` and the dominance floor.
- **Evals.**
  - Schema introspection: no field named `weight`, `ev` or `score` in the compile
    vocabulary. `CommittedLine.score` is sim-internal and never serialised into a library.
  - Prompt-vocabulary parity.
  - Dominance: an ability line outranks a tax-dead cast line of equal reach.
  - Inertness: with no library and `best_line is None`, the pick equals the EV path,
    asserted with equality.
  - "every bound target id is a member of `enumerate_legal_targets`' output".
  - The lethal oracle (§7.1).

### 4.5 Seam Executors (FSD 1.4; main-action, attack, offered-choice and reactive instances)

- **Responsibility.** Return one legal member per seam call. The branch owns the cursor, the
  refusal protocol and the single fallback.
- **When it runs.** Every seam call.
- **Tier.** Pure code. The winner's Haiku executor is dropped: binding is deterministic, and
  every judge layering removed it.
- **State.** `LineCursor{line: CommittedLine, step_idx, turn, replans_this_phase,
  invalidated_reason}` lives on the EVPlayer and is internal.
- **Evals.**
  - The identity contract, tested over every `SeamKind`.
  - The engine reads the executed ref's `ability_index`.
  - Refusal invalidates the cursor once, re-plans at most once, and the phase ends within
    `max_actions`.
  - (X, target) agree with the bound step on a delivered-sink board.
  - `hold` ends the phase with the held cards still in hand.
  - Validation leaves the log and zones unchanged.
  - No cursor survives a turn boundary.
  - The verb→leaf and `SeamKind`→leaf coverage tables (§3.4).

### 4.6 Decision Verifier (FSD 1.5, eval time, deterministic)

- **Responsibility.** Own the corpus, grading, decision-quality findings and movement
  verdicts.
- **When it runs.** CI (the tier, under 10 s), weekly (the full corpus) and on demand.
- **Tier.** Pure code. Golden promotion is HITL.
- **Tools.**

| Tool | FSD | Wraps |
|---|---|---|
| `reconstruct` | 1.5.1 | `CoordinateCounter.stop_at`, `_run_pair` / `run_bo3` seeding, `_ANCHOR_TIMEOUT_SECONDS` |
| `grade` | 1.5.2 | `_last_candidates`, `best_line` + `attack_reach`, `replay_lint.lint_events` R6 |
| `decision_audit.check` | 1.5.3 | the `engine/rules_audit` opt-in pattern, re-homed AI-side |
| `curate` / `reanchor` | 1.5.4 | `tools/name_probe.py`, `reconstruct`, `grade` |
| `measure_lane` | 1.5.5 | `tests/test_wr_baseline_anchor.py`, `check_calibration.check_results` / `compute_trend`, `symmetry_check`, `rules_audit_report.summarize`, the paired statistics of §7.4, the git-log loop-break counter |

- **Evals.** See §7.1 and §7.2.

### 4.7 Divergence Investigator (FSD 1.6, research)

- **Responsibility.** Propose, measure, diagnose and specify. Measurement always comes before
  diagnosis.
- **When it runs.** Only through a supervisor `investigate` action with `--max-usd`. Never in
  a sim.
- **Tiers.** The Shadow Line Proposer (1.6.1) and the Divergence Diagnostician (1.6.3) are
  reasoning agents: default `anthropic:claude-sonnet-5`, A/B arm `anthropic:claude-opus-5`.
  The Unit Specifier (1.6.4) is extraction, `anthropic:claude-haiku-4-5` (the existing
  `failing_test_spec` task).
- **Input schemas.**

```python
class ShadowInput(_Frozen):
    context: DecisionContext                      # reconstructed; menu refs are the only actionable ids
    deterministic_top: tuple[tuple[int, str], ...]  # (ref_id, reason) of the EV path's top-N, no EV numbers
    vocabulary: VocabularySpec
    fewshot_ref: str                              # name-free; disjoint from any golden coordinate

class DiagnosticianInput(_Frozen):
    context: DecisionContext
    deterministic: tuple[ActionRef, LineProjection]
    proposal: tuple[ProposedLine, LineProjection]
    measurement: DivergenceMeasurement
    replay_excerpt: str                           # ai.llm_compression.compress_replay
    audit_rows: tuple[str, ...]                   # rules_audit + decision_audit rows joined by decision_id
    subsystems: tuple[str, ...]                   # the existing Subsystem Literal values

class UnitSpecifierInput(_Frozen):
    verdict: JudgeVerdict
    golden: GoldenDecisionRef
    test_conventions: str                         # rule-phrased naming rules, name-probe rules
```

- **Tools.** `propose_shadow` → `build_agent("shadow_line_proposer")`; `measure` (§3.7.4);
  `diagnose` → `build_agent("decision_diagnostician")`, `compress_replay`,
  `to_prompt_section`; `specify_unit` → `build_agent("failing_test_spec")` with prompt v2.
- **Evals.** See §7.1.

### 4.8 Provenance Keeper (cross-cutting service; compile and eval processes only)

- **SOD.** See §3.7.1.
- **Ledger.** Each run writes its own ledger, keyed by an envelope that holds `run_id`,
  `output_schema_sha`, `behaviour_digest` and `prior_outputs_digest`. The ledger is **never
  mirrored into the shared SQLite**.
- **Replay.** Replay uses only the in-memory ledger. It aborts on any miss or schema drift.
- **Timeouts.** Native SDK / `httpx` timeouts with `max_retries=0`. A cancelled call never
  writes to a cache.
- **Caps.** Per-run `--max-usd` and `--max-calls`. A breach aborts the run loudly and writes a
  partial-ledger marker; the run never silently falls back. The per-task 30-day caps remain
  as the outer bound once P0 fixes accounting.

### 4.9 TaskSpec table (collapses the 7-site registry; model ids pinned)

| Task | FSD | Tier | Model (default / A/B) | Output | Budget $/30 d | Token cap in | `model_settings` |
|---|---|---|---|---|---|---|---|
| `line_librarian` | 1.1.3 | reasoning | `anthropic:claude-sonnet-5` / `anthropic:claude-opus-5` | `LineLibraryDraft` | 25 | 24,000 | `max_tokens` 32,000, effort `high`, stream, timeout 600 s, retries 0 |
| `seam_policy_writer` | 1.1.5 | reasoning | `anthropic:claude-sonnet-5` | `SeamPolicyDraft` | 10 | 16,000 | `max_tokens` 32,000, effort `high`, timeout 600 s, retries 0 |
| `shadow_line_proposer` | 1.6.1 | reasoning | `anthropic:claude-sonnet-5` / `anthropic:claude-opus-5` | `ShadowProposal` | 30 | 16,000 | `max_tokens` 32,000, effort `high`, timeout 600 s, retries 0 |
| `decision_diagnostician` | 1.6.3 | reasoning | `anthropic:claude-sonnet-5` / `anthropic:claude-opus-5` | `JudgeVerdict` | 15 | 16,000 | as above |
| `failing_test_spec` (existing) | 1.6.4 | extraction | `anthropic:claude-haiku-4-5` | `FailingTestSpec` + `UnitSpec` | 2 | 4,000 | `max_tokens` 4,000 |

- **Why these models.** `claude-sonnet-5` is the model the repo already runs in production
  (the committed weights). `claude-opus-5` is the current Opus tier. P0 adds its pricing row;
  parity is pinned by tests/test_llm_models.py. It is the A/B arm only (§7.1 criterion).
- **The 4,096 trap.** Explicit `max_tokens` is mandatory: the pydantic-ai default of 4,096
  truncates thinking plus structured output.
- **No sampling parameters.** Sonnet 5 and Opus 5 reject them.
- **Refusals.** A `refusal` stop reason is recorded as an `AgentFailure`.

---

## 5. Context and schema engineering

### 5.1 Three state views and the hot-path access rule

1. **Engine objects** (`GameState`, `CardInstance`). Judged only by single-owner predicates.
2. **`LineFrame`.** The hot-path view the 1.3 and 1.4 siblings consume. It bundles the
   EV path's already-computed `snap`, `self._assembly`, the captured pre-filter enumeration
   (`HotMenu`), beliefs, posture and key. It carries a `game` handle, which is **read-only by
   contract**.
3. **`DecisionContext`.** Serialisable, for eval agents and evidence only.

**Access rule** (this replaces the previous draft's contradictory statement):

> On the hot path, sibling leaves read state only through `LineFrame` fields. They may pass
> `frame.game` into (a) single-owner engine predicates and queries (`can_cast`,
> `can_activate`, `can_be_targeted`, `enumerate_legal_targets`, `legal_x_range`,
> `_can_block` wrapper) and (b) `ai.assembly_state` primitives. They never read zones or
> players themselves.

`tests/test_line_state_access.py` AST-scans `ai/line_*.py` and fails on any attribute access
to `.hand`, `.battlefield`, `.graveyard`, `.library`, `.players`, `.stack` or `.zone`. The
only exemption is `ai/line_menu.py`'s eval-time `action_menu`. On the untraced hot path,
1.2.7 (`DecisionContext`) runs with P = 0. That is consistent with the rule, because nothing
on the hot path consumes it.

### 5.2 The abstract key: cardinality, class merge rule, posture mapping

**Full key cardinality, by construction:**

| Field | Values |
|---|---|
| `engine_state` | 3 |
| `sink_access` | 8 (sorted unique subsets of 3 values, including ()) |
| `mana_band` | 5 |
| `opp_open_mana_band` | 4 |
| `opp_interaction` | 3 |
| `lethal_reach` | 3 |
| `posture` | 6 |

That gives **25,920** keys. The full key is **never enumerated**. It is used for evidence
joins and for evaluating line preconditions (predicates), and neither of those depends on
the key count.

**Class merge rule.** Priorities depend only on the opponent's interaction profile and on the
deck's posture. Engine, sink, mana and reach facts are handled by preconditions, which filter
the candidate lines before ranking. So:

`class_key = sha256((opp_interaction, opp_open_mana_band, posture))` ⇒ **≤ 3 × 4 × 6 = 72
classes per library**, bounded by the Literal product with no reachability argument needed.

- `class_key ≠ key` by definition.
- `tests/test_ranking_class_bound.py` asserts `len(get_args(...))` products equal 72.
- P2 records the reachable count per deck from evidence, which is only an optimisation.
- Compile cost uses ≤ 72. Ranking is a tool, so no model call per class.

**Band edges** are named constants in `ai/scoring_constants.py`:

- `MANA_BAND_EDGES`;
- `OPEN_MANA_BAND_EDGES`;
- `INTERACTION_SOFT_P` and `INTERACTION_HARD_P`, thresholds on BHI probabilities.

Each has an inline justification and a rule-phrased test, for example "a board with
unbounded mana keys to the unbounded band" or "a posterior above the hard threshold keys to
hard interaction". Raw floats never enter a key.

**Posture mapping.** Goal *names* are free strings from gameplan JSON. The mapping reads the
closed `GoalType` enum (ai/gameplan.py:71) of `current_goal.goal_type`. The single owner is
`ai/line_key.POSTURE_BY_GOAL_TYPE`, which must be total (tested):

| GoalType | posture |
|---|---|
| DEPLOY_ENGINE | assemble |
| FILL_RESOURCE | assemble |
| RAMP | develop |
| EXECUTE_PAYOFF | all_in |
| CURVE_OUT | develop |
| PUSH_DAMAGE | race |
| DISRUPT | hold |
| PROTECT | hold |
| INTERACT | stabilise |
| GRIND_VALUE | develop |
| CLOSE_GAME | race |

A new `GoalType` value fails the totality test in the commit that adds it.

### 5.3 Cache-key and coordinate soundness

- **Keys.** Keys use `sort_keys=True` over pydantic dumps. A cross-process test varies
  `PYTHONHASHSEED`. A set-iteration probe covers menu construction.
- **Envelope keys** include:
  - `output_schema_sha`, so a schema edit is a miss, not a silent validation fallback (R4);
  - `run_id`, so recordings never collide (R3);
  - `behaviour_digest`, which replaces git-HEAD `engine_sha` (R8);
  - `prior_outputs_digest`, so conversation state is part of the key and `CachedAgent`'s
    blindness to kwargs cannot matter.
- **Replay verdicts are three-way:**
  - `IDENTICAL`;
  - `ENGINE_CHANGED` (the digest differs; the report names the first `menu_digest` mismatch
    by coordinate);
  - `NONDETERMINISM` (the digest is equal but the menus diverge).

  This keeps the Amulet vs Living End flip from being misattributed. Replay tests run each
  recording twice in fresh processes with different `PYTHONHASHSEED` values, and exclude
  `_ENVIRONMENT_SENSITIVE` pairs.
- **Coordinates** are `DecisionCoordinate`, counted by the supervisor-owned counter (§3.7.3),
  never by `ReplayLog._next_decision_id`. Goldens survive engine changes through the anchor
  tuple plus the STALE status.
- **Ties.** Ties in binding break by ascending `instance_id`, which is deterministic across
  processes and game numbers (§3.7.3, pinned by test).

### 5.4 Knowledge stays data; logic stays card-name-free

- **Card knowledge enters only as data:**
  - typed `CardTemplate` fields (census);
  - `decks/gameplans/<slug>.json` (`card_roles`, goals, and the new `line_library_id`,
    `goal_transitions`, `tutor_delivery`, `sacrifice` sections);
  - `decks/gameplans/_line_library/<library_id16>.json` (lines over `ShapeToken`s,
    priorities over class keys).
- **Why content-addressed and shared.** Libraries are stored by `library_id`, not by deck.
  Two decks with the same 75-card shape census share one file automatically, and nothing
  keys a library by deck name. The per-deck gameplan JSON only **declares** which
  `library_id` it expects, which is how staleness is detected (§2.3). Loading computes the
  census and opens `_line_library/<id16>.json`, so no deck-name lookup exists in `ai/` code.
- **The new sections extend `tools/check_gameplan_consistency.py`'s TOP_* registries** to
  every card-bearing path: `TOP_CARD_ROLE_PATHS` for `card_role_shapes`, and a new
  `TOP_SHAPE_LISTS`.
- **Few-shots.** Prompt few-shots in `ai/llm_prompts/` are name-free typed-shape boards,
  pinned disjoint from every golden coordinate and fixture board (§7.1 split rules).
- **Ledgers and goldens carry card names** because they are recordings and fixtures, like
  `replays/*.txt` and `tests/fixtures/wr_baseline_anchor.json`. No sim-time code reads them.

**Name-free validator** (`tools/name_probe.py`, the single owner):

- **Normalisation.** Casefold; strip `'`, `’`, `,` and `-`; collapse whitespace; match whole
  words or phrases on token boundaries.
- **Corpus A, always a failure.** Every key of `MODERN_DECKS`, and every card name in every
  registered main deck and sideboard. That is a few hundred names, all matched.
- **Corpus B, the full `CardDatabase`** (about 20 k names).
  - Names with ≥ 2 tokens are a failure on a phrase match.
  - **Single-token names** are a failure *unless* listed in
    `tools/name_probe_allowlist.json`. That list holds single-token card names that are also
    rules vocabulary: the entries of `engine/rules_audit_census.KEYWORD_WORDS`, CR glossary
    terms, and reviewed common words such as "Shock", "Opt", "Fog" and "Counterspell".
    Allowlisted hits are reported as warnings.
  - The allowlist changes only by PR, with a reason field per entry.
  - `tests/test_name_probe.py` pins that every allowlist entry is a single token with a
    reason, and that corpus A is never allowlisted.
- **Applied to:** `ShapeToken` and all Literal values of the compile vocabulary (once, in CI);
  committed library JSON except `provenance.models`; seam sections; prompt few-shots;
  `JudgeVerdict.mechanism` and `rule_phrased_test_name`; `UnitSpec.rule_name`; and golden
  `mechanism` fields.

### 5.5 Ratchets: every CI ratchet addressed

| Ratchet | How this design stays at or below baseline |
|---|---|
| `check_abstraction.py` (name conditionals, rglob `engine/` and `ai/`) | No `card.name ==` or `name in {…}` in any new module. The scan covers the flat modules automatically |
| deck-gate count (`deck_gate_count`) | There is no deck-name literal anywhere. Library selection is by census id, and the gameplan JSON's declared `line_library_id` is data read by the existing slug-based gameplan loader, not a conditional. Mechanics that differ by archetype go through `ShapeToken`s and GoalType |
| `check_magic_numbers.py` (non-recursive `ai/*.py`) | The new modules are **flat**, so they are scanned. They start at baseline 0 in `tools/magic_numbers_baseline.json`. Every threshold is a named constant with a test: `LINE_CANDIDATES_MAX`, `REPLAN_MAX_PER_PHASE`, the band edges, `FIXTURES_PER_CLASS`, `COMPILE_RETRIES_MAX`, `DIVERGENCE_MOVEMENT_PP`, `DIVERGENCE_MIN_DISTINCT_OUTCOMES`, `CLOCK_QUANTUM`, `GOLDEN_CI_BUDGET_S`, `GOLDEN_STALE_MAX_FRACTION` |
| `check_single_owner.py` | Targets: `bind_line` picks `target_role` only among `enumerate_legal_targets` output, so the owner still decides legality and the AI chooses among legal targets, as `_choose_targets` does. The detector only scans `engine/card_effects.py` and `game_runner.py`, but the design does not rely on that gap; the test "every bound target id is a member of the enumerator's output" pins it. No damage, life or counter writes are added |
| `check_zone_mutation.py` (`engine/*.py`) | No zone writes are added. The X routing in `cast_manager` and `activated_effects` changes an integer only. The pre-existing discard-for-pump mutation in `decide_attackers` is left untouched and recorded as debt |
| `check_oracle_runtime_parse.py` (rglob) | The census lives in `ai/line_census.py` and reads typed fields only. A `ShapeToken` without a typed field gets one at DB load first (Pattern A) |
| `check_card_name_registry.py` | No new `EFFECT_REGISTRY.register("Card Name", …)`. The design needs no card handler |
| `check_doc_hygiene.py` | This doc sits in `docs/design/` with frontmatter. No root `.md` and no `_V2` |
| `check_gameplan_consistency.py` | TOP_* extended for the new sections (§5.4) |

---

## 6. Integration seams

| Seam | Exact site | Change |
|---|---|---|
| Coordinates | new `ai/decision_coordinate.py`; `engine/game_runner.run_game` (creates the counter per game, hands it to both EVPlayers and `AICallbacks`) | `enter` is the first statement of every seam method (§3.7.3) |
| Main-phase consumer | `ai/ev_player.py` `decide_main_phase` (:420) | One capture line for the pre-filter enumeration, plus the §3.7.2 block after the body. **No policy-class refactor** of the ~4000-line bodies |
| Attack consumer | `decide_attackers` (:3035) | A terminal `attack_all` step returns the `attack_reach` set; otherwise unchanged |
| X seam | `engine/callbacks.py`: new `choose_x_value(game, idx, card, legal_range) -> int` (uniform per kind) and a `legal_x_range` query; `engine/cast_manager.py` X selection (~:1823-1871) and **`engine/activated_effects.py:194` `pick_activated_tutor_x`** routed through it | `DefaultCallbacks` and `AICallbacks` return today's value when the cursor is empty, so behaviour is byte-identical |
| Tutor delivery | `AICallbacks.choose_tutor_target` (`engine/game_runner.py`); `ai/activation_ev.choose_tutor_delivery` | The cursor's `deliver_shape` wins inside the legal set. The (X, target) agreement test is pinned. This also completes the payoff design's deferred "picker rewrite through `choose_tutor_delivery`" |
| Goal transition | `ai/gameplan.py` `GoalEngine.check_transition` and the RAMP branch (:570-572) | Reads `goal_transitions` when present; one owner of the RAMP transition (payoff §7 deferred) |
| Sacrifice | `ai/activation_ev.choose_sacrifice_victim` | Honours `SacrificePolicy` while `engine_state != "off"` |
| Pricing | `ai/assembly_state.py` `_p_resolves_cast` (:325) → public `p_resolves_cast`; `ai/bhi.py` soft-counter tax posterior (P3a) | One owner; no second BHI formula |
| Evidence | `EVPlayer._emit_decision_event` | Adds `coordinate` and `subsystems.line = {line_id, library_id, tier, step_idx, state_key, class_key, menu_digest, p_resolves, projected_damage, lethal, aborted_reason, policy_hash}` |
| Replay schema | `engine/replay_log.py` **1.1** (additive): `coordinate` on DECISION and RESPONSE_DECISION, `PLAY.targets` populated, `ACTIVATE` events | Compatibility plan in P1 (§9) |
| Rules audit | `engine/rules_audit.py`: rows gain an optional `decision_id` via `set_context` | **No new invariant here.** Play-quality checks live in `ai/decision_audit.py` (§7.3) |
| Decision audit | new `ai/decision_audit.py`; `GameResult.decision_findings`; `run_meta --decision-audit` (sets `MTG_DECISION_AUDIT` before workers spawn) | The engine only transports |
| Lint | `tools/replay_lint.py` R6 "pick not in the legal menu" (uses `menu_digest`) | |
| Runner | `run_meta._run_pair` asserts `MTG_LLM_DECISION_SCORER_OFFLINE` is set. `generated_by` stamps `line_library_ids`, `policy_hash`, `stale_libraries` and `verdict_stale_libraries`. The `--no-line-library` and `--allow-stale-library` CLI flags become **constructor arguments** passed to workers | "a field run appends zero rows to calls.jsonl" (test). Nothing on the decision path reads the environment |
| LLM infra | `ai/llm_agents.py`: `usage` property fix with a legacy fallback; `CachedAgent(offline=…)` raises `OfflineCacheMiss`; no store after cancellation; `model_settings` pass-through. `ai/llm_models.py`: the TaskSpec table (§4.9); an opus-5 pricing row; reconcile the sonnet-5 row against the published $2/$10. `ai/llm_decision_scorer.weight`: when offline, reads **only** the committed JSON and the table, never the gitignored SQLite (R1). `MTG_LLM_OFFLINE` with `MTG_LLM_DECISION_SCORER_OFFLINE` kept as the CI alias; per-task live allowlist `MTG_LLM_LIVE_TASKS` (compile and eval processes only) | Additive |
| Metrics | `ai/llm_metrics.log_call` gains `run_id` and `coordinate` | |

### 6.1 Relationship to the payoff-sequencing design

**This design wraps and feeds that design; it does not replace it.** The payoff design's
deterministic node, "Convert Assembled Engine Into Lethal Line", is the sim-time core. Leaves
1.2.4, 1.3.3, 1.3.4 and 1.3.5 are thin calls into `assemble`, `sink_damage`, `attack_reach`,
`p_resolves_cast` and `win_swing`. No value is re-derived.

**Which document drives.** Since U0–U3 landed, the payoff design has **no remaining ordered
units**. Its §5 execution status lists only deferred items. This document therefore drives
the session (`priority: primary`), and the payoff design becomes context.

**Ownership of the payoff design's deferred items** moves here:

| Payoff-design deferred item | New owner here |
|---|---|
| BHI tax-counter branch of `p_resolves` | P3a (`ai/bhi.py` soft-counter posterior) |
| Picker rewrite through `choose_tutor_delivery` | P3b (1.4.4) |
| `tutor_to_hand` accesses | P3b (an `assembly_state` access extension, test-first) |
| `payoff_affordable` + RAMP goal transition (§7) | P4a (1.1.5 `goal_transitions` + `check_transition`) |
| S-2 finisher lockout generalised to `best_line` (§7) | stays a **non-goal** here; recorded in §10 as open, owner unassigned |

**Companion edit in the landing commit.** This document cannot edit other files. The commit
that lands it must change the payoff design's frontmatter to:

```yaml
status: active
priority: secondary
superseded_by: []
```

It must also append this note to the payoff design's §5 execution status: "Deferred items
owned by docs/design/2026-09-16_agentic_decision_architecture.md §6.1 (P3a, P3b, P4a)."

**Dependency of these phases on payoff state.** Every payoff unit P3 and P4 rely on has
landed (53c95a5, af51c42). None depends on an unlanded payoff unit, and there is no U4. The
Toolbox replay gate has passed, so Toolbox is a **guard** here, not a target. Its traced
boards become held-out goldens.

---

## 7. Evals

### 7.1 Per-agent evals: metric, threshold, ground truth, split unit

**Ground truth is never a model output.** Every label comes from a deterministic oracle, a
landed replay gate, or a human-promoted panel finding.

| Agent | Metric (rule-phrased) | Threshold | Ground truth | Split unit | Runs in |
|---|---|---|---|---|---|
| Decision Supervisor | import graph; no env reads; coordinate counting; plan purity; `status --strict` fixtures | pass / fail | code | — | CI |
| Situation Assessor | serialisable; key purity / permutation / cross-process; menu mutates nothing; posture totality; `sink_access` canonical | pass / fail | code | — | CI |
| Line Sequencer | no float weight in the vocabulary; dominance; inertness (equality); bound targets ∈ enumerator; **lethal oracle**: "when `best_line` is lethal and in the active library, the committed first step lies on a lethal line" | pass / fail (deterministic equality, not a rate) | `assembly_state` | — | CI |
| Seam Executors | identity by `SeamKind`; side channel from executed ref; refusal protocol; (X, target) agreement; `hold`; validation purity; coverage tables | pass / fail | engine enumerators | — | CI |
| **Line Librarian** (1.1.3) | (a) **gate-pass rate**: the fraction of drafted lines passing verdicts 1–3; (b) **reference-line recall** on held-out decks: each reference line's step-shape sequence appears in the draft; (c) name probe | (a) ≥ 0.5; (b) ≥ 0.8; (c) 100% | reference lines are the tier-0 `best_line` shape sequences observed in P2 evidence, plus the payoff replay-gate line | **deck (census)**: goldens and references for held-out decks never appear in few-shots or prior prompts | `--run-eval` (live); (c) in CI |
| **Seam Policy Writer** (1.1.5) | closed-field exact match on `(from_goal_type, to_goal_type, predicate fields)` plus the gate verdict | ≥ 0.6, and the gate passes | `tests/eval/golden/seam_policy_writer/`: rules derived from promoted lane findings (the Amulet RAMP transition once P4a's gate passes; human-written expected transitions for ≥ 3 ramp decks) | deck | `--run-eval` |
| **Shadow Line Proposer** (1.6.1) | (a) **legality rate**: every proposed `ref_id` ∈ menu; (b) **action-class accuracy** on held-out goldens with a known expected class | (a) ≥ 0.95; (b) ≥ 0.6 | goldens (§7.2) | **decision coordinate**: few-shot boards are disjoint from every golden and fixture coordinate (pinned by test) | `--run-eval` |
| Divergence Diagnostician (1.6.3) | closed fields `subsystem` + `mechanism_class` | ≥ 0.6 | promoted 2026-05-16 and 2026-09-15 panel findings with human-labelled subsystem (≥ 3 pairs) | finding | `--run-eval` |
| **Unit Specifier** (1.6.4) | closed fields (`owning_module`, `kind`, `mechanism_class`) + name probe on `rule_name` | ≥ 0.6; name probe 100% | human-written specs for the same promoted findings | finding | `--run-eval`; probe in CI |
| Provenance Keeper | replay independent of calls.jsonl and the clock; replay touches no SQLite; a late response changes no replay or cache; an offline miss opens no socket (< 1 s); live-shaped result logs non-zero tokens and cost; month-to-date spend rises after a TestModel call; envelopes differing in run or schema have distinct keys | pass / fail | code | — | CI |

**Opus A/B pass criterion** (per reasoning task). The Opus arm replaces the Sonnet default
only if all three hold on the same held-out golden set:

1. the task's primary metric improves by **≥ 0.10 absolute**;
2. the gate-pass rate is not lower;
3. the measured cost per admitted artifact (or per MEASURED divergence) is recorded.

Otherwise Sonnet stays. The decision is written into the TaskSpec row's comment with the
eval run id.

### 7.2 Golden-decision corpus

**Sources** (each goes through 1.5.4 with human promotion):

1. **Replay gates of landed designs.** The payoff traced boards are held out.
2. **Deterministic grader failures** on dumped NDJSON: `line/lethal_missed` and R6 hits.
3. **5-panel promoted findings** (2026-05-16, 2026-09-15), converted to coordinates.
4. **Measured and diagnosed shadow divergences** (P5).

**Admission rules.**

- A golden stores an **expected action class**, not a menu digest.
- It must be **red on its recorded `behaviour_digest`** and green after its fix commit.
- Only goldens with a fully deterministic trajectory (no model pick in the prefix) are
  admitted.
- `_ENVIRONMENT_SENSITIVE` pairs are excluded.

**Tiers and the CI budget.**

- Reconstruction costs about one game prefix: p50 0.3 CPU-s, p95 1.4 s, maximum 3.9 s. The
  card DB load is shared with the suite's session fixture (`tests/_card_db_cache`).
- **CI tier:** goldens whose summed recorded reconstruction CPU ≤ `GOLDEN_CI_BUDGET_S = 3.0`,
  which is about 6–10 goldens at p50. `tests/test_golden_decisions.py -m golden_ci` runs them
  in the regression-surface step, and the test asserts the tier's recorded sum stays within
  budget.
- **Weekly tier:** everything else, in a new keyless step in the existing `weekly.yml`
  (Monday 06:00 UTC). The repo has no nightly workflow, and this design does not add one.
- **Local rule:** a PR touching the line-path files must run `-m golden_full` locally. The PR
  template line records it.

**STALE policy.**

- **Owner.** The supervisor's `status` lists STALE goldens.
- **CI tier.** Any STALE golden fails CI. The PR that made it STALE must **re-anchor** it
  (`tools/golden_curate.py reanchor`, which recomputes the anchor tuple on the new digest
  only if the expected class is still evaluable, with human confirmation) or **retire** it
  (delete it, with the reason in the commit message).
- **Full corpus.** It fails `status --strict` when STALE > `GOLDEN_STALE_MAX_FRACTION = 0.2`.
  The weekly job reports the count. The corpus cannot decay to all-STALE while CI stays
  green.

### 7.3 Decision-quality audit (AI-side) and the end-to-end quality metric

`line/lethal_taken` and `line/step_legal` were previously placed in `engine/rules_audit.py`.
They are play-quality checks, not Comprehensive Rules invariants, so they move to
**`ai/decision_audit.py`** as:

- `line/lethal_missed`: `best_line` was lethal and the committed first step was not on a
  lethal line;
- `line/step_refused`: a validated step was refused by the engine, which means the
  enumerator and the executor disagree.

The opt-in pattern and multiprocessing transport are the same as rules_audit
(`GameResult.decision_findings`). The engine transports and judges nothing.

**No zero-violation requirement is imposed on `lethal_missed`.** Projection optimism (§10
risk 3) makes some misses correct.

**End-to-end decision-quality metric.** Beyond WR, the design tracks the **missed-lethal
rate** per deck: `lethal_missed / entries_with_lethal_best_line`, over a same-seed
`--decision-audit` field or matrix. `measure_lane` reports it pre and post.

- It must not rise on any guard by more than 2 binomial σ.
- It is expected to fall on the target lane.

`line/step_refused` has a target of 0, as an enumerator/executor consistency invariant. A
non-zero count names the decision by coordinate, and that feeds the rules-census route to
the next unit.

### 7.4 End-to-end evals and statistical power

**Surfaces.**

- The WR anchor (27 entries). Each flip needs a justifying coordinate.
- Calibration bands (`check_results`, `--trend`): no IN→OUT transitions.
- The `--rules-audit` matrix: 0 CR violations.
- `--decision-audit`: §7.3.
- Symmetry check.
- The loop-break counter.

**Same-seed pairing.** Pre and post fields run the same seeds (50000 grid, n=20 Bo3, 24
opponents, N = 480 matches). Let d be the number of **discordant** matches (the outcome
changed). Under the null hypothesis, Δ has σ_Δ ≈ √d / N.

| Discordant fraction f = d/N | σ_Δ | Observed Δ needed for one-sided sign-test p < 0.05 |
|---|---|---|
| 0.10 | 1.44pp | ≈ 2.4pp |
| 0.20 | 2.04pp | ≈ 3.4pp |
| 0.30 | 2.50pp | ≈ 4.1pp |

- **The old rule was about 1σ.** The previous rule, "Δ ≥ `FIELD_MOVEMENT_PP` = 2.2pp", has a
  false-keep rate of about 19% at f = 0.3 (P(Z > 0.88)).
- **Keep rule.** Δ ≥ 2.2pp **and** an exact one-sided sign test on discordant pairs with
  p < `SIGN_TEST_ALPHA` = 0.05. That caps false keeps at ≤ 5%.
- **Inconclusive band.** When Δ ≥ 2.2pp but p ≥ 0.05, the decision is `escalate_n60`: one
  re-run at n=60 (1,440 matches, σ ÷ √3) on the extended grid. After that the unit is kept or
  reverted; it is never re-run again.
- **Guards** (Domain Zoo, Boros Energy, Ruby Storm, Jeskai Blink, plus Creatures Toolbox).
  "Flat" means no guard is significantly harmed under **Holm–Bonferroni** at family
  α = 0.05, **and** every guard has |Δ| < 2.2pp. With 5 guards each tested at an unadjusted
  0.05, the family false-alarm rate would be about 23%. Holm keeps it at 5%.
- **Cell σ.** `CELL_SIGMA_PP` is no longer a literal "≈ 11". It is
  `cell_sigma_pp(n, p=0.5) = 100·√(p(1−p)/n)` = 11.2pp at n = 20, the binomial σ of
  independent Bo3 matches in one cell. It is used only to size replay-gate cell reads, never
  in the keep rule.
- **Constants.** All constants live in `tools/measure_lane.py`, each with a rule-phrased
  test: "a unit with 2.3pp on 150 discordant matches escalates rather than keeps".

### 7.5 Gating rule before any compiled policy is committed (PolicyGate)

A library or seam section is committed **only** when all five verdicts pass. Their
provenance is written into the artifact.

| # | Verdict | Leaf | Where it runs | Re-run trigger |
|---|---|---|---|---|
| 1 | Integrity | 1.1.6.1 | CI, every PR | every PR |
| 2 | Legality on fixture coordinates, under `MTG_RULES_AUDIT=1`, `drain()` empty, negative control fails | 1.1.6.2 | CI | every PR |
| 3 | Lethality by `sink_damage` / `attack_reach`; dominance floor | 1.1.6.3 | CI | every PR |
| 4 | Regression: MovementVerdict `keep` (§7.4); anchor flips justified; no band OUT; `aborted == 0`; missed-lethal non-increase on guards | 1.1.6.4 | committer, quiet box | new artifact; `line_path_digest` change (**CI refuses until re-run**); behaviour drift before a matrix `--save`, a WR claim, or weekly (warning) |
| 5 | Cost: measured µs/iteration ≤ §3.2 budget; p_E and p_L recorded | 1.1.6.5 | committer, quiet box | as 4 |

---

## 8. Cost and wall-clock model

**Assumptions.**

- 25 decks and 300 ordered pairs.
- Planning prices are the **upper** of the repo table and the published table: Sonnet 5 at
  $3/$15 (published $2/$10), Opus 5 at $5/$25, Haiku 4.5 at $1/$5, per MTok.
- **No dollar figure below is a bound until P0 fixes `result.usage` accounting.** Today
  every call logs $0.
- P4 and P5 replace these ranges with measured p50/p95 per call: tokens including thinking,
  `stop_reason`, wall time.

**Opus/Sonnet multiplier, derived.** At equal token counts the per-token ratio is ($5/$3,
$25/$15) = **×1.67** against the repo table, or ($5/$2, $25/$10) = **×2.5** against
published prices. Thinking-token counts differ between models, so the ratio is measured in
P4 on the A/B arm. The ranges below use ×1.7–2.5.

### 8.1 Per unit

| Unit | Model calls | $ | Wall | Bounded by |
|---|---|---|---|---|
| **Compile, one library** | Librarian 1–3 (≈ 12–24 k in, 5–20 k out + thinking) + seam writer 0–2 | Sonnet ≈ $0.11–0.37 per call ⇒ **$0.1–1.9** per library; Opus ×1.7–2.5 | model 5–30 min; fixtures ≈ 2 min; verdicts 1–3 < 1 min; **verdict 4 = 5 pre + 5 post fields**: 10 × ≈ 14 min (848 s measured) ≈ 140 min serial, **≈ 50–60 min on 3 workers** (pre fields reused across units at an equal `behaviour_digest`); verdict 5 ≈ 15 min | content addressing; per-run `--max-usd` (default $10) and `--max-calls` (default 10); abort on breach |
| All 25 libraries (hypothetical) | 25 × above | ≈ $3–50 | model ≈ 2–12 h; verdict 4 ≈ 25 h on 3 workers | in practice 1–3 libraries per lane |
| Human review, per compile PR | — | — | **30–60 min** (lines over shape tokens plus the gate report) | one PR per library |
| Golden promotion | — | — | **10–15 min per golden** | CI-tier cap |
| **Per diagnostic Bo3** | 0 | $0 | ≈ 1–3 s engine (+ 6.5 s DB load once) | no model in the sim |
| **Per n=20 matrix** | 0 | $0 | 28 min 25 s × ≤ 1.05; planned ≈ +3.2% ≈ **+55 s** | §3.2 budget, CostVerdict |
| n=60 matrix / `--rules-audit` | 0 | $0 | 4 h 26 min / 4 h 48 min × ≤ 1.05 | same |
| `--decision-audit` overhead | 0 | $0 | ≤ 5 µs × p_E·p_L per iteration (negligible) | |
| Golden CI tier | 0 | $0 | ≤ 3 s reconstruction (+ shared DB) | `GOLDEN_CI_BUDGET_S` |
| Weekly full corpus | 0 | $0 | ≈ 0.3–1.4 s per golden (100 goldens ≈ 1–2 min) | STALE cap |
| **Research run, K states** | K proposer + ≤ K diagnostician + curated × specifier | proposer Sonnet ≈ $0.07–0.35, Opus ≈ $0.16–0.55; K = 40 ⇒ **$3–14 / $6–22**; diagnostician ≈ $0.05–0.2 × (clearing fraction × K); specifier < $0.01 | reconstruction K × ≤ 1.4 s; proposer K × 30–300 s ≈ 20 min–3.3 h serial (÷ concurrency ≤ 4); **measurement 2n games per divergence** ≈ 10 s mean, ≤ 60 s p95 (n = 20); triage 3–4 h human | K fixed per run; `--max-usd` (default $30); measure-before-judge gate |

**Why no per-decision call.** A fully agentic n=20 matrix is ≈ 0.33–0.68 M reasoning calls,
≈ $23 k–$380 k, and at 50 concurrent calls ≈ 2–47 days. The Phase 4C rule "hot-loop budget:
0 s per decision" is retained. Cost is bounded as follows:

- **sim time**, by construction;
- **compile time**, by content addressing plus per-run caps;
- **eval time**, by K plus per-run caps.

Once P0 lands, nothing is unbounded.

### 8.2 Per phase (totals, time box and cap)

| Phase | Engineering | Model $ (cap) | Machine wall | Human review | Time box |
|---|---|---|---|---|---|
| P0 LLM infra correctness | 2–3 d | $0 (TestModel only; any live call fails the phase) | 2 CI runs ≈ 15 min | 1 h | 6 d |
| P1 Eval spine | 4–6 d | $0 | seed dumps ≈ 1 min; corpus < 10 s CI | 6–10 goldens × 15 min ≈ 2.5 h | 12 d |
| P2 Abstraction and evidence | 2–3 d | $0 | 50 Bo3 evidence dumps ≈ 3 min; CostVerdict ≈ 15 min | 1 h | 6 d |
| P3a Tax posterior | 2–3 d | $0 | anchor + tests | 1 h | 6 d |
| P3b Cursor and seams | 5–8 d | $0 | ≤ 3 attempts × ≈ 1 h (10 fields) + ≤ 1 n=60 escalation (≈ 3 h) | 2 h | 16 d |
| P4a Seam sections (Amulet RAMP) | 3–5 d | expected $0.5–3 (cap $10) | fixtures + gate ≈ 1.5 h per attempt, ≤ 3 | 1–2 h | 10 d |
| P4b Line libraries | 4–6 d | expected $1–5 per library (cap $25 per phase) | ≈ 1.5 h per library | 1 h per library | 12 d |
| P5 Research probe | 4–6 d | expected $5–25 (cap $30) | 1–5 h | 3–4 h triage | 12 d |
| **Total** | ≈ 26–40 d | ≤ $65 capped | ≈ 15–25 h machine | ≈ 13–17 h | |

Overrunning a time box halts the phase with a `docs/` halt doc. That doc carries
`status: active` and `priority: primary`, per the CLAUDE.md loop-break rule.

---

## 9. Implementation phases

### 9.0 Dependency DAG, fallback and commit discipline

```
P0 ──► P1 ──┬──► P3b ──► P4b
  └──► P2 ──┤
            └──► P4a            (needs P1 fixtures + P2 key/loader vocabulary; NOT the cursor)
P0 ──► P3a  (independent; required only for the tax-hold goldens and the tax branch of 1.3.4)
P0 + P1 ──► P5  (P5 conversions reuse P4's gate when it exists; otherwise they become engine units only)
```

- **Sequencing rule 1 (merge, then measure).** U2+U3 are merged and their fields read
  (cb986ab). Measurements for P2, P3b and P4 are taken only after their own phase merges.
  P1 and P2 may run in parallel worktrees using Pattern A: a schema-first commit for
  `ai/decision_coordinate.py` / `ai/line_menu.py` / `ai/line_key.py`, then disjoint
  consumers.
- **Fallback if P2's evidence names no lane.** If no deck other than Toolbox (already moved)
  shows a non-null `best_line` spanning more than one iteration, and no line-relevant X,
  tutor or sacrifice callbacks, then **P3b is not built**. The phase records that as a
  `docs/diagnostics/` note with the evidence, and the work proceeds to **P4a**, whose mover
  (the RAMP transition) does not need the cursor. P4b stays deferred until a lane appears.
- **Commit discipline.**
  - P0, P1 and P2 are infrastructure. Each lands as a **sequence** of small commits. Each
    commit carries its failing rule-phrased test plus its implementation and keeps the suite
    green. None fixes a rules gap, so the CLAUDE.md rule "failing test + fix + rules-audit
    invariant in one commit" does not apply to them.
  - The rule applies in full to any P3b or P4 commit that exposes and fixes a **rules** gap,
    for example if routing X through `choose_x_value` surfaces a CR 601.2f defect. That
    commit carries the CR invariant.
  - Behaviour units (P3b, P4) carry their golden (red → green) and the MovementVerdict in the
    commit message.

### P0: LLM infra correctness and offline-by-construction (no behaviour change)

- **Files:**
  - `ai/llm_agents.py`: `usage` property with a legacy fallback; `CachedAgent(offline=…)`
    raising `OfflineCacheMiss`; no store after cancellation; `model_settings` pass-through.
  - `ai/llm_models.py`: the TaskSpec table; an opus-5 row; sonnet-5 reconciled.
  - `ai/llm_budgets.py` and `tests/eval/llm_eval.py`: read TaskSpec.
  - `ai/llm_decision_scorer.py`: when offline, read the committed JSON and the table only.
  - `run_meta.py`: `_run_pair` asserts the offline flag.
  - Tests: `tests/test_metered_agent_usage.py`, `tests/test_llm_offline_gate.py`,
    `tests/test_sim_weight_reads_only_committed_data.py`,
    `tests/test_field_run_writes_no_llm_rows.py`, `tests/test_llm_task_registry.py`.
- **Evals (red first):**
  - "a live-shaped result logs non-zero tokens and cost"
  - "month-to-date spend rises after a call"
  - "an offline agent never opens a socket on a miss"
  - "an offline weight never reads the gitignored response cache"
  - "a field run appends zero LLM rows"
  - "every registered task has a model, a price, a budget, a cap, a prompt and a threshold"
- **Stop/go.**
  - Go when the anchor is unchanged, all ratchets hold, both CI chunks are green, and
    spend accumulates under TestModel.
  - No-go on any anchor movement: that means the scorer's SQLite path was feeding sims.
    Write that up before continuing.

### P1: Eval spine (coordinates, replay schema 1.1, reconstruction, grader, decision audit, `measure_lane`, supervisor `status`)

- **Files:**
  - `ai/decision_coordinate.py`, `ai/decision_audit.py`
  - `engine/game_runner.py`: counter hand-off, `ACTIVATE` events, `PLAY.targets`,
    `set_context(decision_id=…)`, `decision_findings` transport
  - `engine/replay_log.py`, `engine/rules_audit.py`: the `decision_id` field only
  - `ai/ev_player.py`: `enter` calls, coordinate on DECISION
  - `tools/replay_lint.py`: R6
  - `tools/golden_reconstruct.py`, `tools/decision_grader.py`, `tools/golden_curate.py`,
    `tools/measure_lane.py`, `tools/name_probe.py`, `tools/name_probe_allowlist.json`,
    `tools/decision_supervisor.py` (`status` only), `tools/fsd_registry.json`
  - `tests/eval/golden/decision/`
  - Tests: `tests/test_golden_decisions.py`, `tests/test_decision_coordinate.py`,
    `tests/test_instance_id_determinism.py`, `tests/test_replay_schema_1_1.py`,
    `tests/test_measure_lane.py`, `tests/test_name_probe.py`, `tests/test_fsd_docstrings.py`
  - `.github/workflows/abstraction-contract.yml` (CI tier + `status --strict`) and
    `weekly.yml` (full corpus)
- **Replay schema 1.1 compatibility and rollback:**
  1. The **first** P1 commit makes `from_ndjson` and `build_replay.py` tolerant of unknown
     optional keys within a known major, if they are not already, *before* any writer
     changes. A later revert of the writer therefore leaves every 1.0 and 1.1 file readable.
  2. A test renders the committed `replays/*_decisions.ndjson` (1.0) and a new 1.1 dump.
  3. The text-log pipeline (`replays/*.txt`, `build_replay.py` text path,
     `tools/parse_replay_snapshots.py`) is pinned **byte-identical** on 3 seeds, because 1.1
     changes only NDJSON.
- **Evals:**
  - "an untraced run counts coordinates without touching the replay sequence"
  - "a re-entrant callback is not counted"
  - "instance ids are identical across processes and games"
  - "a golden reconstructs to its anchor tuple or reports STALE"
  - "a rules-audit row joins to its decision"
  - "decisions are identical with the decision audit on and off"
  - "a lane verdict escalates rather than keeps on an insignificant 2.3pp"
  - "a golden names no deck or card"
  - The first 6–10 goldens: payoff traced boards (held out) plus promoted panel findings.
- **Stop/go.**
  - Go when the anchor and all non-evidence NDJSON fields are byte-identical, the CI tier
    runs within budget, and every seeded golden reconstructs LIVE.
  - No-go if a reconstruction goes STALE on an unchanged digest. That is `NONDETERMINISM`:
    record it and fix it before any behaviour change.

### P2: Sim-time abstraction and evidence (no behaviour change)

- **Files:**
  - `ai/line_census.py`, `ai/line_menu.py`, `ai/line_key.py`
  - `ai/scoring_constants.py` (band edges)
  - `ai/assembly_state.py` (public `p_resolves_cast`)
  - `ai/ev_player.py` (enumeration capture, `subsystems.line` evidence)
  - `tools/magic_numbers_baseline.json` (new flat modules at 0)
  - Tests: `tests/test_line_key_soundness.py`, `tests/test_action_menu_pure.py`,
    `tests/test_sim_time_import_graph.py`, `tests/test_ranking_class_bound.py`,
    `tests/test_line_state_access.py`
- **Evals:**
  - key purity, permutation and cross-process tests
  - menu purity, including re-entrant `should_evoke`
  - import graph and no env reads
  - class bound = 72
  - `tools/check_magic_numbers.py --list` counts a literal planted in `ai/line_key.py`
- **Stop/go.**
  - Go when the anchor is byte-identical, the 3-seed NDJSON is identical except
    `subsystems.line`, and the **measured P2 increment is ≤ 50 µs per iteration** (planned
    ≈ 17 µs: 0.7 × 0.15 × 125 µs plus coordinate counting at ≈ 3.4 hook calls × 1 µs).
  - p_E and p_L are recorded per deck.
  - Then choose P3b's lane from the evidence, ranked against the calibration OUT findings,
    the rules-audit ranking and the decision audit. Apply the §9.0 fallback if none
    qualifies.

### P3a: Soft-counter tax posterior (prerequisite unit)

- **Files:** `ai/bhi.py`, `ai/assembly_state.py` (tax branch via
  `project_counter_tax_payment`), and `tests/test_bhi_soft_counter_posterior.py`.
- **Evals (red first):**
  - "a soft counter's tax is priced by the posterior, not by the hard-counter probability"
  - "an all-in X tutor whose payment leaves the tax counter live does not outrank a line
    that keeps it payable"
- **Stop/go.** Go when the tests are green and the anchor flips (if any) are justified. The
  tax-hold goldens then move from out-of-scope to the CI or weekly tier.

### P3b: Line cursor and line-consistent seams (first behaviour change)

- **Files:**
  - `ai/line_library.py` (tier 0 only), `ai/line_sequencer.py`, `ai/line_cursor.py`
  - `ai/ev_player.py` (§3.7.2 block; attackers)
  - `engine/callbacks.py` (`choose_x_value`, `legal_x_range`)
  - `engine/cast_manager.py` and **`engine/activated_effects.py`** (X routing)
  - `engine/game_runner.py` (`AICallbacks.choose_x_value` / `choose_tutor_target`)
  - `ai/activation_ev.py` (delivery; `tutor_to_hand` accesses)
  - tests for identity, refusal, `hold`, (X, target) agreement, the lethal oracle,
    dominance, coverage tables and the §3.2 budget
- **Evals:** all of §7.1 Sequencer and Executor. The lane's golden goes red → green.
- **Stop/go.**
  - Go when MovementVerdict is `keep`: replay gate passed, target Δ ≥ 2.2pp with sign-test
    p < 0.05, guards flat (Holm), no rise in missed-lethal on guards, anchor flips justified,
    `aborted == 0`, CostVerdict within budget.
  - `escalate_n60` runs once.
  - No-go: revert. A third flat commit on the lane triggers the loop-break halt doc.

### P4a: Seam sections and the compiler skeleton; first artifact on the RAMP goal transition (Amulet Titan, 23.3% field)

- **Files:**
  - `tools/compile_decision_knowledge.py` (sections path), `tools/agent_ledger.py` (Keeper)
  - `ai/policy_predicates.py`
  - `ai/gameplan.py` (`check_transition` and the RAMP branch read `goal_transitions`)
  - `decks/gameplans/amulet_titan.json` (section)
  - `tools/check_gameplan_consistency.py`
  - `ai/llm_prompts/seam_policy_writer_v1.md` + name-free `_fewshot.json`
  - `tests/eval/golden/seam_policy_writer/`, `tests/test_policy_gate_sections.py`
- **Evals:**
  - the relevant PolicyGate verdicts (legality on fixture coordinates, regression, cost)
  - the seam writer's golden score (§7.1)
  - the compile run's measured per-call cost distribution is recorded
- **Stop/go.**
  - Go when all verdicts pass and Amulet's field is `keep` under §7.4.
  - No-go: revert the section, write up why the transition did not move the lane, and
    re-choose from evidence. **There is no prompt-tuning loop to move a number.**

### P4b: Line libraries (needs P3b)

- **Files:**
  - `ai/line_library.py` (tier 1, active-shape filter, stale refusal)
  - `tools/compile_decision_knowledge.py` (librarian + ranking)
  - `tools/validate_line_library.py` (CI)
  - `decks/gameplans/_line_library/`
  - `ai/llm_prompts/line_librarian_v1.md` + name-free `_fewshot.json`
  - `tests/eval/golden/line_librarian/`, `tests/test_line_library_schema.py` (no float
    field, vocabulary parity, name probe, few-shot disjointness),
    `tests/test_stale_library_is_fatal.py`
- **Evals:**
  - all five verdicts, including the negative control
  - the librarian's gate-pass rate and recall
  - the Opus A/B per §7.1
- **Stop/go.** Go per library when MovementVerdict is `keep`. Otherwise the library is not
  committed and the refusal is recorded.

### P5: Research probe with measured divergences (eval time only)

- **Files:** `tools/shadow_line_probe.py`, `tools/divergence_measure.py`,
  `ai/llm_prompts/shadow_line_proposer_v1.md`, `decision_diagnostician_v1.md`,
  `failing_test_spec_v2.md`, `tests/eval/golden/decision_diagnostician/`,
  `tests/test_divergence_measure.py`, `tests/test_agent_ledger_replay.py`.
- **Evals:**
  - "a measurement with fewer than the minimum distinct outcomes is refused"
  - "a measurement after a library-top reveal is refused"
  - "a divergence is measured before it is diagnosed" (TestModel call count)
  - "continuations of both picks share seeds"
  - the Keeper replay tests
  - the proposer's legality and accuracy
  - the diagnostician ≥ 0.6
- **Stop/go.** Run K ≈ 40 states sampled from the corpus and the outlier cells.
  - Go to a second cycle only if at least one divergence is MEASURED, clears
    `DIVERGENCE_MOVEMENT_PP`, is diagnosed at class ≥ 10, and becomes a gated library entry
    or a landed rule-phrased unit.
  - No-go: write a `status: falsified` doc ("a reasoning model proposes no measured-better
    line on K reconstructed states"). Retire branch 1.6 with a root restatement.

---

## 10. Risks and non-goals

### Non-goals

- **Live or shadow in-process agentic play in any sim.** This covers any `--agentic` mode,
  any per-decision model call and any Haiku executor. Reopening it requires a separate FSD
  root and a separate design doc, justified by P5 divergences that compiled data *cannot*
  express. If that happens, all determinism amendments 1–10 apply.
- **Any WR claim from a model run.**
- Mulligan, sideboarding, the hidden engine→ai seams, counter-war depth, and line-driven
  blocks and responses.
- The S-2 finisher-lockout generalisation (payoff §7). It is recorded as open, with no owner.

### Risks and honest limits

1. **Play-quality upside is bounded and class-specific.** It exists where a line spans
   several iterations or depends on X, tutor or sacrifice agreement, or on a goal-transition
   rule. On curve-aggro decks the design is inert by construction. The largest known case
   (Toolbox) already moved. The remaining candidate movers (Amulet via the transition, the
   Eldrazi Tron X line) are unproven. No pp number is promised.
2. **Abstraction loss.** A class can merge states whose correct line differs. Mitigations:
   - preconditions filter by the full key;
   - tier 0 is state-exact;
   - binding runs against the exact menu;
   - fixture-derived ranking;
   - STALE and lethal-oracle failures expose collisions.
3. **Projection optimism.** Instant-speed removal and fogs are not modelled. This is why
   `lethal_missed` has no zero target and why measurement precedes diagnosis.
4. **Forward-sim variance.** Many coordinates will return `INSUFFICIENT_VARIANCE` or
   `LIBRARY_ORDER_KNOWN`, which limits P5. A refused verdict is better than a degenerate
   ±100% result.
5. **Statistical power.** Units with real effects below about 2.5pp at typical discordance
   will not be kept at n=20. This is accepted: the escalation is n=60 once, not repeated
   re-runs.
6. **Staleness friction.** The hard-fail staleness rule and line-path re-verification make
   some engine PRs pay a quiet-box measurement or retire a library. That friction is the
   price of no silent fallback. The `--allow-stale-library` diagnostic exists for local work.
7. **Compile-time model variance.** Only gated artifacts matter. Prompts and models are A/B'd
   by gate and golden metrics, never by comparing two live outputs.
8. **Contract drift through data.** Every new `ShapeToken` needs a class-size ≥ 10
   justification, a typed field, and PR review. The name probe and the TOP_* extension cover
   the new card-bearing paths.
9. **Environment nondeterminism** (the Amulet vs Living End flip). It is excluded from
   fixtures, and `NONDETERMINISM` is its own replay verdict.
10. **Loop-break exposure.** P3b, P4a and P4b halt on a third flat commit. P5 has an explicit
    falsification exit. Every phase has a time box.
11. **Rebase conflicts** on `ai/ev_player.py`, `engine/game_runner.py` and
    `ai/assembly_state.py`. Mitigation: Pattern A, with schema-first single commits and
    disjoint consumers.

### What remains research

- Whether reasoning models find measured-better lines at all (P5).
- Whether shape-census libraries generalise across decks.
- A mulligan seam with play/draw and opponent context.
- Migrating the hidden seams into `GameCallbacks`.

---

## Appendix A: Refutation ledger

**Determinism refuter**

| # | Amendment | Disposition |
|---|---|---|
| 1 | Split the offline switches; per-task live allowlist; `weight()` skips SQLite in sims | **Adopted** (P0; §6 LLM infra) |
| 2 | Replay bypasses `MeteredAgent` and SQLite, uses an in-memory ledger, aborts on miss | **Adopted** (§3.7.1) |
| 3 | Per-recording namespace; schema hash in key; no late writes | **Adopted** (`AgentEnvelope`; native timeouts; no store after cancellation) |
| 4 | Supervisor-owned coordinate counted in every mode | **Adopted** (§3.7.3) |
| 5 | Prove shadow inert; restore side channels | **Adopted by removal.** There is no in-process shadow mode. Side channels are set from the executed ref. The pin becomes "no library ⇒ byte-identical" |
| 6 | Guard at the builder, not `run_meta_matrix` | **Adopted by removal.** No sim-time agentic mode exists. The import-graph and no-env-read tests replace the guard |
| 7 | Behaviour digest; three replay verdicts; no raw floats in keys | **Adopted** (§2.3, §5.3) |
| 8 | Do not claim byte-identity past the xfail flip; run twice | **Adopted** (§5.3) |
| 9 | Native or out-of-process timeouts; CPU-budget test | **Adopted.** The CPU-budget interaction is moot in sims |
| 10 | Shadow-only until green | **Adopted and strengthened:** eval-time only |

**Cost refuter**

| Amendment | Disposition |
|---|---|
| Fix `usage` first | **Adopted** (P0; §8 precondition) |
| Measured per-deck counts; bounded re-plans | **Adopted** (§1.1; `REPLAN_MAX_PER_PHASE`; sim calls = 0) |
| `model_settings`; measure before quoting | **Adopted** (§4.9). **Partial rejection, one line:** the "11–14 s per Haiku call" figure is real (2026-05-16 warm wall ÷ 72) but averaged, so it is now cited only as an upper bound, not as latency |
| Cancellable timeouts; authoritative ledger | **Adopted** (§3.7.1) |
| Per-run cap; abort loudly | **Adopted** (§4.8, §8.2) |
| Engine-change-surviving coordinates | **Adopted** (anchor tuple + STALE; §7.2 policy) |
| Specified reseed hook | **Adopted** (§3.7.4: seed derivation, forced pick, re-run prefix) |
| Cheaper path first | **Adopted** (research last) |

**Contract / play-quality refuter**

| # | Amendment | Disposition |
|---|---|---|
| A1 | Rebase on HEAD; Toolbox not the target | **Adopted** (§1.1, §6.1) |
| A2 | `x_value`; `choose_x_value`; honoured delivery | **Adopted** (§4.0, §6) |
| A3 | Defined variance source | **Adopted** (§3.7.4) |
| A4 | Flat modules | **Adopted** (§5.5) |
| A5 | Pure-predicate validation; side-effect-free enumeration | **Adopted** (1.4.6, 1.2.2) |
| A6 | Tax posterior as a named prerequisite | **Adopted** (P3a) |
| A7 | Name-free few-shots, disjoint from goldens | **Adopted** (§5.4, §7.1) |
| A8 | CI-runnable bad-line detectors | **Adopted** (lethal oracle, R6, `menu_digest`, `step_refused`) |
| A9 | No EVPlayer policy seam | **Adopted** (thin consumers) |

**Decomposition refuter**

| # | Amendment | Disposition |
|---|---|---|
| D1 | Split into two roots | **Rejected, one line:** the brief makes the orchestrator the single root. It is now *realised*: the Decision Supervisor sequences compile and eval through `tools/decision_supervisor.py` (§3.7.5, §3.8), not only sim-time routing |
| D2 | Delete live 1.3 | **Adopted** for live execution. The deterministic executor branch (now 1.4) stays |
| D3 | Only non-leaves SUPERVISORY | **Adopted** (§3.2) |
| D4 | Validation under its guaranteed branch; menu auxiliary | **Adopted** (1.4.6; 1.2.2) |
| D5 | Keeper as a service; replay as a test | **Adopted** (§3.6, §3.7.1) |
| D6 | Cursor leaf; stateless supervisor | **Adopted** (1.4.1) |
| D7 | One fallback owner; one refusal owner | **Adopted** |
| D8 | Verb + Qualified Object; split attack and block | **Adopted** (§3.6 renames) |
| D9 | Re-point tools to main | **Adopted** (verified at cb986ab; `pick_activated_tutor_x` is at `engine/activated_effects.py:194`) |
| D10 | One metadata statement per tool | **Adopted** (§3.2; `test_fsd_docstrings.py`) |
| D11 | Coupling basis consistent | **Adopted** (§3.5) |

---

## Appendix B: Completeness-critic gap ledger

| Gap | Closed in |
|---|---|
| (a) Bo3 census keying and replay | §2.2 (union 75, per-game active-shape filter, replay reproduction) |
| (a) Silent staleness; census at load; mp workers | §2.3 (hard failure, CI validator, `--allow-stale-library` marks results not calibration-grade; census at load < 1 ms, per worker) |
| (a) Verdicts 4–5 freshness | §2.3 (`line_path_digest` CI refusal; behaviour-drift re-run rule and owner), §7.5 |
| (a) Compile reproducibility | §2.4 (artifact is the unit, plus a committed compile ledger and `--replay`) |
| (a) Forward-sim seeds, forcing, prefix vs fork | §3.7.4 |
| (a) Halt mechanism; instance-id determinism | §3.7.3 (exception at seam entry, never resumed; instance-id test) |
| (b)/(c) Service-level contradiction | §3.1, §3.2 budget table with early-exit probabilities; P2 stop/go reset to an attainable ≤ 50 µs increment |
| (b) State-access contradiction | §5.1 (`LineFrame`, access rule, AST test) |
| (b) Undefined schemas; missing inputs; `class_key` | §4.0 (all types), §4.2 and §4.7 (inputs), §5.2 (`class_key`) |
| (b) Census bound unsupported | §5.2 (72 classes by Literal product; full key 25,920, never enumerated) |
| (b) Posture mapping | §5.2 (total map over `GoalType`, owner `ai/line_key.py`) |
| (b) Agent evals incomplete; split unit; circularity; A/B; Opus id | §7.1, §4.9 |
| (b) 1.4.3 needs no model | now TOOL 1.1.4 (§3.3) |
| (c) Cost gaps (pre + post fields, per phase, human time, reconstruction, deck count, ×1.7) | §8.1, §8.2, §3.7.4, §1.1, §8 multiplier derivation |
| (d) Ratchets; deck gate; shared library | §5.5, §5.4 |
| (d) Name-free validator | §5.4 |
| (d)/(f) `lethal_taken` layer | §7.3 (moved to `ai/decision_audit.py`; no zero requirement) |
| (e) Payoff relationship: ownership, driver, dispositions, dependencies | §6.1 |
| (f) Golden CI budget; no nightly; STALE policy | §7.2 |
| (f) Statistical power; multiple comparisons; `CELL_SIGMA_PP`; end-to-end metric | §7.4, §7.3 |
| (g) Phase DAG; P2 fallback; caps and time boxes; schema 1.1 rollback; commit discipline | §9.0, §8.2, P1 |
| (h) Prior decision not citable | §1.2 (quoted in full; companion commit path) |
| (i) Frontmatter | `superseded_by: []`; `depends_on` extended; priority ordering in §6.1 |
| (j) Root is not the supervisor of all branches | §3.7.5, §3.8, §4.1 |
| (j) Sufficiency tests; `hold`; golden leaf; fixture leaf; re-run owner | §3.4, 1.4.2, 1.5.4, 1.1.2, §2.3 |
| (j) Granularity and cohesion (verdict split, `measure_lane` lifted, 1.5 split, legality once per time, `compute_play_ev` decoupled) | 1.1.6 children, §3.6, 1.5 / 1.6, 1.3.3 |
| (j) Result-dependency ordering | §3.3 order 1.1 → 1.6 |
| (j) Naming | §3.6 renames |
| (j) Tool metadata; docstring template and test | §3.2, §3.3 per leaf |
| (j) SOD contradiction; missing SODs | §3.7.1 (no substitution), §3.7.2–§3.7.5 |
| Minor: `pick_activated_tutor_x` location; environment read; Opus id | §6 X-seam row; §2.1 constructor arguments; §4.9 |
