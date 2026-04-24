---
title: 3-Iteration fix session summary (Affinity→Living End→Defenders→Goryo's)
status: archived
priority: historical
session: 2026-04-24
depends_on:
  - docs/diagnostics/2026-04-23_affinity_consolidated_findings.md
  - docs/diagnostics/2026-04-24_living_end_consolidated_findings.md
tags:
  - session-summary
  - phase-12
  - iteration
  - meta-tuning
summary: "Three parallel-agent iteration cycles (investigation → consolidation → parallel fixes → matrix validation). Iter 1 Living End bundle: 7 PRs merged, LE +10 pp (24% → 38%), target hit. Iter 2 defender collapse: 1 PR merged (B3-Tune), Dimir +3.3 and AzCon WST +3.9 pp, target hit. Iter 3 Goryo's: 3 PRs merged but aggregate matrix -1.2 pp on Goryo's — individual fixes showed +10 pp verification but matchup-specific gates don't generalize to the full field. Also: Affinity deep-dive concluded the 88% WR is STRUCTURAL, not surgical — deck-list nerfs or permanent-redundancy threat model required. Final pytest 346/346 green (+49 from baseline 297)."
---

# 3-Iteration fix session — summary

## Headline

| Iteration | Target | PRs | Matrix outcome | Verdict |
|---|---|---:|---|---|
| **1** | Living End 24% underperf | 7 | 24% → 38% flat (+10 pp), +11 pp weighted | ✅ win |
| **2** | Defender collapse (Bundle 3 regression) | 1 (+2 reverted per gate) | Dimir +3.3, AzCon WST +3.9, Jeskai +1.0 | ✅ win |
| **3** | Goryo's 25% underperf + Affinity deep-dive | 3 (+1 diag) | Goryo's 26.4% → 25.2% (−1.2 pp) | ❌ regression |

**Meta tally**: 11 fix PRs merged (+49 tests to 346 total passing), 2 diagnostic docs published, 1 revert per data-driven gate, 1 structural finding on Affinity flagged for future work.

## Methodology

Each iteration followed the pattern:
1. **Parallel read-only investigations** (10 agents: matchup traces + code audits) consolidated into a findings doc with fix-bundle structure.
2. **Parallel fix agents in isolated worktrees**, each Option C (failing test first → fix → full pytest green → commit test+fix → push → open PR).
3. **Merge sequentially** with GitHub auto-rebase between merges; real conflicts delegated to rebase agents in worktrees.
4. **N=50 matrix validation** to measure aggregate effect.

## Per-iteration results

### Iteration 1 — Living End (26.4% → 38.4%)

Diagnostic doc: `docs/diagnostics/2026-04-24_living_end_consolidated_findings.md` (12 findings across 4 layers).

**7 PRs merged** (+30 tests):
- #150 LE-T1 VirtualBoard respects summoning sickness (1-line fix, highest-leverage — AI was counting summoning-sick creatures as attackers in race math)
- #151 LE-G1+G3+T2 config tuning (added Violent Outburst to critical_pieces; raised resource_target 2→4 to prevent premature cascade; fixed stale comment)
- #152 LE-G2 combo_clock per-archetype resource threshold (Living End declares `archetype_subtype: "cascade_reanimator"`, reads 6-resource threshold instead of Storm's 8)
- #153 LE-E2 suspend counter tracking (net-new engine feature: Rift Bolt, Ancestral Vision, Violent Outburst now work)
- #154 LE-A3 cascade patience gate (Storm-style clamp when GY < target)
- #155 LE-E1 Grafdigger's Cage continuous effect (both clauses, oracle-driven detection)
- #156 LE-A1+A2 cascade projection + graveyard asymmetry in EVSnapshot (cascade spells now project Living End's value; `opp_gy_creatures` tracked for symmetric reanimation)

### Iteration 2 — Defender collapse (B3-Tune)

Diagnostic: parallel agent-produced, inline in session. Root cause: Bundle 3's `HELD_RESPONSE_VALUE_PER_CMC = 7.0` was floor-level for CONTROL's `pass_threshold = −5.0`. A single held Counterspell (1 × 2 × 1.0 × 7.0 = −14) gated all mid-value plays.

**1 PR merged** (+4 tests):
- #157 B3-Tune — coefficient 7.0 → 4.0 + color-capacity early-exit (skip penalty when cast preserves held-counter colors) + A4 threshold revert (opp_hand_size ≥ 3 → ≥ 4)

**2 agents reverted per verification gate** (data-driven protocol):
- AzCon deck-list swap (WST v2 pattern with Chalice + Wan Shi Tong): +17 pp vs Boros but 0% vs Affinity; gate required ≥5 pp on verification matchup → reverted
- AzCon gameplan sequencing (Orim's Chant + INTERACT goal hold): principled fix, tests all green, but 0% → 0% vs Boros → reverted

Matrix effect: Dimir +3.3, AzCon WST +3.9, Jeskai +1.0, Amulet +2.2. Top-deck suppression: Affinity −1.7, Boros −1.3.

### Iteration 3 — Goryo's + Affinity deep-dive

Diagnostic: parallel agent Goryo's investigation (3 orthogonal bugs) + Affinity deep-dive (structural, not surgical).

**3 Goryo's PRs merged** (+15 tests):
- #158 GV-4 Goryo's EXECUTE_PAYOFF min_turns + min_mana gates (+10 pp vs Dimir at verification)
- #159 GV-1 Faithful Mending self-discard — gameplan-aware reanimation-fuel boost (reads `resource_min_cmc` from JSON, creatures ≥5 CMC score +100+cmc, outranking flashback's +90 and evoke-removal's +95)
- #160 GV-2 reanimation readiness gate + EOT creature projection discount (cascade-patience analog in opposite direction; Goryo's EOT-exile creatures project at 0.5× power for "one attack before exile")

Matrix effect: Goryo's **−1.2 pp** despite each fix showing +10 pp on its verification matchup. Hypothesis: single-matchup verification (vs Dimir, N=60) doesn't generalize to full 17-deck field. Subtle conflicts between fixes possible (GV-4's gameplan gate may prevent GV-2's boost from firing at the right time).

**Affinity deep-dive** (read-only, no commits):
- Affinity's 88% WR is STRUCTURAL: deck runs 6-8 cheap artifact creatures (carrier redundancy), evaluator scores cards independently, Plating functionally invulnerable because carrier replacement is instant.
- Conclusion: no surgical fix will move Affinity's WR. Need either deck-list nerfs (cut 2-4 creatures) or a new permanent-redundancy threat model (~200 lines in `ai/ev_evaluator.py`).

## What worked vs what didn't

**Patterns that worked:**
- **1-line leverage fixes with clear root cause** — LE-T1 (summoning sickness in VirtualBoard) shifted Living End +10 pp. Simple, correct, foundational.
- **Option C discipline** — every merged PR has a failing-test-first commit. Prevents "fix looks right but doesn't test the actual behavior".
- **Consolidated findings docs before fixes** — reduces scope creep, documents assumptions, lets fix agents cite concrete file:line.
- **Parallel fix agents in isolated worktrees** — 7 LE fixes done in parallel; would have been days sequentially.
- **Data-driven verification gates with reverts** — 2 AzCon agents correctly self-reverted when their fix didn't beat the +5 pp gate. Prevented committing principled-but-ineffective changes.

**Patterns that failed:**
- **Single-matchup verification gates can mislead** — Goryo's PRs all showed +10 pp vs Dimir but the full 17-deck matrix went backwards. Future iterations should validate against 3-5 diverse matchups, or prefer a mini-matrix at N=20.
- **Surgical fixes on structurally-overbuilt decks** — Affinity absorbed 7 PRs without moving. The evaluator's per-card independence can't be patched away.
- **Worktree-isolation leakage** — parallel agents repeatedly wrote into the shared main worktree, requiring each agent to isolate its own diff. Not blocker but noisy. Harness issue worth flagging.

## Final state

- **Main**: 11 Iter-1/2/3 fix PRs merged + 7 previous Affinity-session PRs.
- **Pytest**: 346/346 green (+49 from pre-iteration baseline 297).
- **Matrix (N=50) overall spread**: top deck Affinity 87.6% / bottom Azorius Control 16.4%. Mid-tier band (Dimir 54%, Pinnacle 65%, Jeskai 64%) healthier than pre-session.
- **Outstanding P0**: Affinity structural overperformance (flagged, needs deck-list nerf or threat-model rewrite).
- **Outstanding**: Ruby Storm 23% (PR #142 from a prior session still open, not yet merged).
- **Dashboard**: `modern_meta_matrix_full.html` updated on snapshot branches (`claude/iter1-matrix-snapshot`, `iter2-matrix-snapshot`, `iter3-matrix-snapshot`).

## Non-actionable findings banked for future iterations

- **AzCon deck-list is structurally weak** vs Affinity (0% WR). Chalice doesn't hit 0-CMC Ornithopter/Memnite. Needs Meltdown / By Force / artifact-hate to fix Affinity matchup.
- **Grafdigger's Cage + Suspend now implemented** (PR #155/#153); future cards using those mechanics will work automatically.
- **First-strike bug in CLAUDE.md P1 is stale** — current `_simulate_combat` handles first strike correctly via two sequential loops. Can be closed in the backlog.
- **Goryo's EXECUTE_PAYOFF `min_turns`/`min_mana` gates** are now a reusable gameplan primitive for other combo decks.

## Commit summary

| Commit | PR | Description |
|---|---:|---|
| f002b5d | #150 | LE-T1 VirtualBoard summoning sickness |
| 1f56af4 | #153 | LE-E2 suspend tracking |
| 400b38b | #155 | LE-E1 Grafdigger's Cage |
| e084747 | #151 | LE-G config tuning |
| d6849ef | #152 | LE-G2 combo_clock |
| 2d5c6e7 | #154 | LE-A3 cascade patience gate |
| e6a2e4a | #156 | LE-A1+A2 cascade projection + gy asymmetry |
| 510d0cf | #157 | B3-Tune holdback coefficient |
| 27882eb | #158 | GV-4 Goryo's gameplan gates |
| 5fbfaf6 | #159 | GV-1 Faithful Mending discard fuel |
| db46fd1 | #160 | GV-2 reanimation readiness gate |
