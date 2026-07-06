---
title: "Wiki: Protocols"
status: active
priority: secondary
session: 2026-07-05
tags:
  - wiki
  - protocols
  - workflow
summary: |
  Wiki page — session protocols: test-first fixes, the loop-break rule,
  the frontmatter doc registry, and WR-anchor refresh. Staged under
  docs/wiki/ pending wiki publication.
---

# Protocols

Working practices that keep a long-running, multi-session simulation project honest. These complement the [Abstraction-Contract](Abstraction-Contract) (which governs *what* code may look like) by governing *how* work proceeds.

## Test-first, same diff

Every fix lands as **failing test + fix in one commit**. The test goes red first, phrased as the rule it pins ("delayed EOT-exile riders drop when the tracked object changes zones"), then the fix turns it green. A fix without its red-first test is reverted on review. This is what made it possible to merge 25 PRs from parallel sessions in a single day without the tree going red.

## The loop-break rule

Diagnostic loops are the failure mode of AI-tuning work: three sessions of documentation, zero win-rate movement. The countermeasure is mechanical:

> If three consecutive commits target the same outlier deck without moving its win rate toward the expected band — **halt**. Replay the worst matchup (`run_meta.py --bo3`), identify the exact turn where EV diverges from correct play, and name the responsible subsystem in writing. No further code until that document exists.

Corollary: no second diagnostic phase on an outlier without a replay-based root cause first. Documentation is not progress.

## The frontmatter registry

There is no index file to drift out of date. Every document under `docs/` carries YAML frontmatter (`title`, `status`, `priority`, `session`, `supersedes`/`superseded_by`, `depends_on`, `tags`, `summary`) — **the frontmatter is the registry.** Session start is a grep, not a meeting:

```bash
# What drives this session
grep -rEl '^status: active' docs/ --include='*.md' | xargs grep -l '^priority: primary'

# Hypotheses already tested and failed — do not re-run
grep -rEl '^status: falsified' docs/ --include='*.md'
```

Status values: `active` (read and act), `superseded` (replaced, kept for history), `falsified` (tested, failed — the most valuable status: it prevents re-running dead experiments), `archived` (historical). Supersession is recorded in frontmatter, never by creating `PLAN_V2.md` — a hygiene checker enforces this and the root-directory allowlist.

## Win-rate anchors

A committed snapshot of reference win rates (`tests/test_wr_baseline_anchor.py`) pins simulation behaviour the way unit tests pin functions. When an intentional change legitimately moves a number, the snapshot is refreshed deliberately (`python tools/refresh_wr_baseline.py`) and committed with the change that caused it — drift is always explained, never absorbed. Hard-won corollary: regenerate anchors only against the canonical merged database (`python3 merge_db.py` first); a stale DB once produced anchor "drift" that was pure data skew.

## Reproducibility discipline

- Standard seeds everywhere (matchups 50000, matrix 40000, step 500) — every claim is re-runnable.
- Replay logs behind any diagnostic conclusion are committed to `replays/`.
- Every product figure traces to one function and one data file.
