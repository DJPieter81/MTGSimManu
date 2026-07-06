---
title: Engine + AI overhaul — 25 PRs in one parallel-wave session
status: archived
priority: historical
session: 2026-07-05
tags:
  - session-summary
  - engine
  - cr-rules
  - calibration
  - meta-refresh
  - data-provenance
summary: |
  Single-session overhaul across five parallel waves: engine/AI fix wave
  (#441-#445), probe wave (#447-#455), July 2026 meta refresh (#456-#458),
  tuning wave (#460-#465), definitive 19-deck matrix (#466) and showcase
  refresh (#467). Restored four CR rules from dead code, added CR 400.7
  object identity and CR 714 saga chapter abilities, removed the fabricated
  ModernAtomic_part9 and added MTGJSON provenance enforcement. Suite:
  2253 passed / 0 failed.
---

# Engine + AI overhaul (2026-07-05)

25 PRs merged to `main` in one session, organized as sequential waves of
parallel tracks. End state: test suite **2253 passed / 0 failed**, a
19-deck July 2026 metagame, and a definitive Bo3 n=20 matrix with
ground-truth calibration bands (12 in band / 17 out of band — the
out-of-band entries are the next session's divergence probes).

## Waves

1. **Engine/AI fix wave (#441–#445)** — mechanics fixes from the E/M
   probe series: karoo multi-mana, imprint-copy activation, tokens
   ceasing to exist on zone change, impulse-draw EV pricing, plus the
   replay linter tool.
2. **Probe wave (#447–#455)** — resolver/SBA unification (restored dead
   CR rules), clause-scoped oracle predicates, calibration bands,
   close-game gear-shift, saga chapter abilities, suite-green fixes,
   counter-triage gating, storm partial-chain math, and the canonical
   DB part-merge fix.
3. **Meta refresh (#456–#458)** — Storm-vs-Dimir canonical-DB gap
   forensics (part9 removal), LLM model registry refresh, July 2026
   19-deck registry + dashboard integration.
4. **Tuning wave (#460–#465)** — Instant Reanimator and Goryo's
   gameplan tuning, mulligan bottoming fix, CR 400.7 EOT-exile object
   identity, WR-anchor reconciliation on the fully merged tree.
5. **Definitive matrix (#466)** — 19-deck Bo3 n=20 on fully merged
   main + dashboard rebuild.
6. **Showcase refresh (#467)** — regenerated guide/replay artifacts and
   showcase stats.

## PR table

| PR | Description |
|----|-------------|
| #441 | engine: karoo/bounce lands produce their full multi-mana (E1) |
| #442 | engine(runner): imprint-copy activation — legality, true copy, real payment (E2) |
| #443 | engine: tokens cease to exist on zone change (E4) |
| #444 | tools: replay linter — every `--dump-replay` becomes a rules audit |
| #445 | ai(ev): per-spell projection prices self card-event taxes + lethal-to-self floor (M1, impulse draw) |
| #447 | engine: resolver/SBA unification — deleted dead Stack machinery; restored CR 608.2b, 704.5c, 704.5h, 704.5i from dead code |
| #448 | engine(oracle): clause-scoped predicates — `engine/oracle_clauses.py`, E5 whole-text conjunction fix |
| #449 | tools: calibration bands — every `--matrix --save` becomes a ground-truth audit |
| #450 | ai(goal): close_game re-weights play scoring — A2/M4 gear-shift |
| #451 | engine: CR 714 saga chapters grant real abilities (+ WR snapshot regen) |
| #452 | tests: main suite green — skip eval-harness smoke cleanly when pydantic_ai absent |
| #453 | ai(response): cheap-counter path consults chain bottleneck gate (M2 wave-2) |
| #454 | ai: storm partial-chain math (+ WR-anchor revert of a stale-DB artifact) |
| #455 | fix(db): single canonical part-merge — stale parts-1..8 recipe caused CI/local WR divergence |
| #456 | diag: Storm-vs-Dimir canonical-DB gap — fabricated `ModernAtomic_part9` removed, MTGJSON provenance test added |
| #457 | chore(llm): per-task model registry — generative-task defaults refreshed, model policy rebuilt |
| #458 | data: July 2026 meta refresh — 19-deck registry, dashboard D-object integration |
| #460 | tune(gameplan): Instant Reanimator — reanimation roles, combo sets, mulligan keys |
| #461 | fix(mulligan): bottoming protects gameplan-declared combo pieces |
| #462 | engine(triggers): delayed EOT-exile riders drop when the tracked object changes zones (CR 400.7, `battlefield_entry_seq`) |
| #463 | tune: Goryo's Vengeance + WR-anchor refresh (2 turn-count drifts from intentional fixes) |
| #464 | data: meta-refresh follow-up merge onto post-tuning main |
| #465 | test: definitive WR-anchor refresh on the fully merged July world |
| #466 | data(matrix): definitive 19-deck Bo3 n=20 on fully merged main + dashboard |
| #467 | docs(showcase): July 2026 refresh — 19-deck stats, regenerated guide + replay artifacts |

## Key findings

- **`ModernAtomic_part9` was fabricated data.** An unprovenanced ninth
  part file added 2026-05-10 carried ~30 hand-authored card texts that
  never came from MTGJSON. It was the root cause of the Storm-vs-Dimir
  canonical-DB outlier (see
  `docs/diagnostics/2026-07-05_storm_dimir_canonical_gap.md`). Removed
  in #456; `tests/test_db_part_provenance.py` now requires MTGJSON
  `meta.version` provenance on every part. New card data enters only
  via `update_modern_atomic.py` from a real MTGJSON export.
- **Four CR rules existed only in dead code.** CR 608.2b (all-targets-
  illegal spell fizzle), 704.5i (deathtouch destruction), 704.5h
  (lethal-damage destruction, with indestructible exemption), and
  704.5c (10+ poison loses) were implemented but unreachable — the live
  resolution path never called them. #447 unified the resolver and
  `SBAManager` so they actually fire. Lesson: "the rule is implemented"
  requires a test that reaches it through the live path.
- **Stacked-PR retarget gotcha.** In a parallel wave, retargeting a
  stacked PR after its base merges silently pulls the base's commits
  into the child's diff; merge order and post-merge reconciliation PRs
  (#464, #465) exist because of this. Retarget + rebase, then re-verify
  the diff is only your commits before merging.
- **Stale DB was the root cause of CI/local WR divergence.** A
  hand-rolled parts-1..8 merge recipe (bypassing `merge_db.py`) ran
  sessions on a silently stale DB — the source of the CI failures on
  #451/#454 (WR-anchor snapshots regenerated against different card
  texts). `merge_db.py` is now the single source of truth: it globs all
  parts and merges in numeric order. Always `python3 merge_db.py`
  before any sim-derived snapshot.
