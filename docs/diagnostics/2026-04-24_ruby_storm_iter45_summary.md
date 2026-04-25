---
title: Ruby Storm 2-iteration session summary (Iter 4+5)
status: archived
priority: historical
session: 2026-04-24
depends_on:
  - docs/diagnostics/2026-04-24_3iteration_session_summary.md
tags:
  - session-summary
  - ruby-storm
  - iteration
  - meta-tuning
  - negative-result
summary: "Two consecutive iterations focused on Ruby Storm (Iter 4 + Iter 5). Each followed the standard pattern (10-agent parallel diagnostic → consolidated findings → parallel fix agents → merge → N=50 matrix). Aggregate result: 5 principled Storm fixes merged across the two iterations, N=50 matrix shows Storm WR REGRESSED from 22.8% to 18.8% (−4 pp). Same class of failure as Iter-3 Goryo's: each fix passes unit tests + single-matchup verification, but compound effect in the full 17-deck matrix is negative. Concrete root-cause hypothesis: layered patience gates over-clamp Storm's decision space; N=30 bisects too noisy to attribute the regression to a specific PR. Test suite 297 → 371 green (+74 across all 5 iterations this session). Recommendation: Storm at ~19-24% may be near 'deck-AI floor'; deck-list refactor or dedicated high-N study needed instead of more surgical fixes."
---

# Ruby Storm Iter 4+5 — session summary

## Headline

Five individually-verified Ruby Storm fixes merged over two iterations:

| PR | Bundle | Iter | Test status | Matchup verification |
|---:|---|---:|---|---|
| #164 | S-2 (finisher priority in mana-constrained turns) | 4 | 351/351 ✓ | Grapeshot out-scores March when mana tight ✓ |
| #165 | S-1a (PiF-as-finisher requires GY content + mana) | 4 | 352/352 ✓ | 4 gate-trigger tests ✓ |
| #166 | S-5 (storm-coverage escalation + draw cascade) | 4 | 352/352 ✓ | 4 refinement tests ✓ |
| #167 | S-3 (BHI discard probability prior) | 5 | 363/363 ✓ | Dimir derates combo_value ✓ |
| #168 | S-1b (Wish-as-finisher SB validation) | 5 | 363/363 ✓ | 4 finisher-path tests ✓ |

**Aggregate N=50 matrix effect on Ruby Storm**:

| Stage | WR flat | Δ from baseline |
|---|---:|---:|
| Pre-Iter-4 (post-3-iteration baseline) | 22.8% | — |
| Post-Iter-4 (after #164/#165/#166 merged) | 19.4% | **−3.4** |
| Post-Iter-5 (after #167/#168 merged) | 18.8% | **−4.0** |

Every fix was correct in isolation. The aggregate matrix moved the wrong direction.

## What worked vs what didn't

### What worked this session

- **Option C discipline held** — all 5 merged PRs have failing-test-first commits verifying the specific behavior intended. Tests still all green post-merge (371/371).
- **Diagnostic agents succeeded consistently** — 10-agent Iter-4 diag produced a clear fix-bundle plan. Iter-5 over-conservative-patience diagnosis identified specific numeric softening (S-5 line 1348 escalation 1.0→0.5, line 1349 coef 5.0→4.0) that was not applied due to agent failures.
- **Iter-5 retries succeeded for fix agents that initially failed** (S-1b and S-3 both landed on the retry).
- **Bug attribution** — every Storm fix had a crisp oracle-driven root cause: PiF needs GY content to be a finisher; Wish needs SB content to be a finisher; combo_calc needs to model discard not just counter.

### What didn't work

- **Compound gate over-conservatism**. Three patience gates (PR #142 + S-1a + S-5) stack; each is defensible alone, but together they hold Storm back from winning lines the pre-#142 AI would have taken. The over-conservative diagnostic agent identified this concretely but its softer values were never applied.
- **N=30 bisect is too noisy**. At matrix noise band ±5-10 pp for N=30, bisect agents produced contradictory "post-Iter-4 baseline" measurements (2.5% / 6.5% / 12% for the same SHA / same seed). Reverting candidates (S-1a showed +4 pp on bisect, S-5 showed −7 pp on revert) — inconclusive.
- **Single-matchup verification gates mislead (confirmed from Iter 3 Goryo's)**. Each Iter-4/5 fix showed positive signal at N=30-60 on a single matchup, but the 17-deck N=50 matrix effect was negative or null.
- **Filesystem race on shared `/home/user/MTGSimManu` worktree** — my own stashing operations were disrupting parallel agents' work, causing several fix agents to abort mid-task. The retry attempts on quiet-parallel windows succeeded.

## Fix-bundle architecture

**Iter 4 (3 merged, 3 failed)**:
- S-1a: PiF-as-finisher requires GY content + mana (MERGED #165)
- S-2: Finisher priority in mana-constrained EXECUTE_PAYOFF (MERGED #164)
- S-5: Mid-chain patience refinements (MERGED #166)
- S-1b: Wish-as-finisher SB validation (FAILED, retry in Iter 5 succeeded)
- S-3: BHI discard probability prior (FAILED, retry in Iter 5 succeeded)
- S-4: Gameplan config (SELF-REVERTED per verification gate)

**Iter 5 (2 merged, 2 failed/reverted, 3 bisects + 1 diag succeeded as read-only)**:
- S-1b retry (MERGED #168)
- S-3 retry (MERGED #167)
- S-4 soft retry (FAILED mid-task)
- Bisect S-1a: reverting improves Boros +4 pp at N=30 (inconclusive noise)
- Bisect S-2: reverting hurts Boros −10 pp at N=30 (S-2 is a keeper)
- Bisect S-5: reverting hurts Storm aggregate −7 pp at N=30 (but inconsistent with S-1a bisect's absolute numbers)
- Over-conservative diag: concrete softening proposed (not applied)

## Not-applied recommendations

The over-conservative diagnostic agent identified two specific softer values that were not applied:
- `ai/ev_player.py:1348` — escalation slope 1.0 → 0.5 (`escalation = 1.0 + max(0, (storm_coverage - 0.5) * 0.5)`)
- `ai/ev_player.py:1349` — base coefficient 5.0 → 4.0

Expected recovery per that agent: Storm WR 18.8% → 21-22% (partial closure of the session's −4 pp deficit).

Additionally, the S-1a bisect suggested reverting S-1a alone might recover +4 pp. Combined with the softer S-5 tuning, could potentially get Storm back to 23-25% — but validation at N=50+ needed.

## Banked findings (no action taken)

- **Jeskai Blink has NO counterspells** in the current decklist. Storm vs Jeskai is Ragavan tempo, not counter battle. PR #148 (cheaper-counter) is irrelevant for this matchup.
- **Ruby Storm has NO sideboard hate** for Chalice of the Void (no Engineered Explosives, Vexing Shusher, Boseiju). Chalice X=2 against Storm is a 100% loss. Structural deck problem.
- **Ruby Storm's gameplan is missing `archetype_subtype: "storm"`** — currently falls back to default which happens to equal storm's 8-resource target (no-op but a correctness gap).
- **Reckless Impulse has dual role** (ritual + cantrip) not reflected in mulligan combo sets.
- **Storm lacks discard anticipation** in mulligan (as distinct from BHI combo_calc — the S-3 fix only affected combo_calc, not mulligan).

## Process learnings

- **Don't stash files from main while parallel agents run.** Each agent's checkout to the shared worktree got clobbered. Future: `git worktree add` truly-separate worktrees per fix agent, OR serialize fix-agent execution.
- **Single-matchup verification gates don't generalize.** Future iterations should require 3-5 matchup verification OR N=100+ to commit a fix.
- **Layered patience gates compound.** When adding a new patience-like gate, prove that it fires ONLY on hands the pre-existing gates missed; don't stack penalties on top of each other for the same situation.
- **Matrix non-determinism is a blocker for surgical tuning.** A ±5 pp jitter at N=50 is fine for big moves (like LE +10 pp from Iter 1) but useless for tuning at the ±2 pp scale.

## Final state

- **Main**: 5 Iter-4/5 Storm PRs merged. pytest 371/371.
- **Storm N=50 WR**: 18.8% flat / 14.2% weighted (WORSE than pre-iteration 22.8%/17.1%).
- **Living End positive side effect**: +3.9 pp in Iter 5 (likely from S-3 BHI discard helping LE vs Dimir).
- **AzCon vanilla**: still at bottom of meta (16.2%). Structural deck list problem.
- **Affinity**: still 87.1% (structural, flagged earlier).

## Recommendation for future Storm work

Not: another surgical fix iteration. The evidence is that Storm's 18-23% band is near-floor given the current AI architecture. Options:

1. **High-N Storm-only validation study** — run Storm vs every opponent at N=200 each. Cost ~4 hours compute. Would settle whether S-1a/S-5 are actually hurting or if the matrix noise was misleading.
2. **Deck-list refactor** — add Engineered Explosives / Vexing Shusher / Boseiju to Storm's SB to handle Chalice. Add more cheap finishers (Grapeshot copies?) to reduce Wish dependence.
3. **Accept current state** — Storm is meant to be a glass-cannon combo deck. 20-25% WR matches the "brittle combo loses to hate" intuition. Re-allocate future iteration slots to other decks (AzCon vanilla 16%, Amulet Titan 47%).

## Commit summary (this session)

| Commit | PR | Description |
|---|---:|---|
| bceeaeb | #164 | S-2 finisher priority |
| f9b550d | #165 | S-1a PiF-as-finisher |
| 8253767 | #166 | S-5 mid-chain refinements |
| 172ce77 | #167 | S-3 BHI discard |
| c9b8ba9 | #168 | S-1b Wish SB validation |
