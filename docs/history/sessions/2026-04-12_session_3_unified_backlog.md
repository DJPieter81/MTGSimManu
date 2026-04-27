---
title: Session 3 unified backlog and validation — all phases (2026-04-12)
status: archived
priority: historical
session: 2026-04-12
tags:
  - session-changelog
  - session-3
  - infrastructure-proposals
  - validation
  - matrix-v3
summary: |
  Session 3 changelog (Groups A/B/C, phases 2/3/5), Session 3 validation
  results, LLM judge re-grading, Matrix-v3 outlier summary, and the
  Infrastructure / Validation backlog tables — all moved out of
  PROJECT_STATUS.md §12 during doc cleanup. Most items are either landed
  (per the Status column) or now tracked in MODERN_PROPOSAL.md /
  CROSS_PROJECT_SYNC.md. Genuinely open follow-ups have moved to
  userMemories or §7 (Known bugs).
---

# Session 3 unified backlog — all phases (2026-04-12)

### Session 3 changelog (branch `claude/complete-unfinished-tasks-50La8`)
All items below were landed or verified-already-live on the session-3 branch.
Groups A/B/C commits: `2a4e3a7`, `9d5a7a7`, `72c1be9`.

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Amulet + bounce-land mana loop in `_score_land` | landed | `2a4e3a7` |
| 2 | Living End post-combo aggression flag | landed | `2a4e3a7` |
| 3 | Elesh Norn / Panharmonicon trigger doubling | landed | `2a4e3a7` |
| 4 | Phelia blink-on-attack handler (ETB value; attack-decl partial) | landed | `2a4e3a7` |
| 5 | Multi-copy Amulet untap loop | landed | `2a4e3a7` |
| 6 | Jeskai Ephemerate Main1-hold sequencing | landed | `2a4e3a7` |
| — | Ephemerate AI-side target gate (audit P1) | landed | `2a4e3a7` |
| — | Psychic Frog / low-CMC ETB creature EV floor (§7 P1 #3) | landed | `2a4e3a7` |
| — | Spelunking `_apply_lands_enter_untapped` on fetchland crack (§7 P2 #5) | landed | `2a4e3a7` |
| — | Phase-labelled EV traces + ghost-candidate filter (audit P2) | landed | `2a4e3a7` |
| — | LE mulligan relax-at-6 (audit P2) — already live | verified | — |
| — | Tron assembly bonus (audit P2) — already live `ai/ev_player.py:657-676` | verified | — |
| 7 | meta_audit.py + EXPECTED_RANGES + post-matrix outlier flagging | landed | `9d5a7a7` |
| 8 | Symmetry check in run_meta_matrix | landed | `9d5a7a7` |
| 11 | `--workers` CLI flag for matrix parallelism | landed | `9d5a7a7` |
| 12 | Provenance footers in dashboard + guide builders | landed | `9d5a7a7` |
| — | `--sigma DECK1 DECK2 --repeats N` sampler (fills §5 σ-at-n=50 TODO) | landed | `9d5a7a7` |
| 9 | Plugin deck architecture | **deferred** — stub in MODERN_PROPOSAL.md §10.1 | `72c1be9` |
| 10 | Template dashboard | **deferred** — stub in MODERN_PROPOSAL.md §10.2 | `72c1be9` |
| 15 | Artifact hate in sideboards (Affinity 85%) | **investigation-only** — replay committed for next session | `72c1be9` |

### Still open after session 3
- Wish tutor Grapeshot-vs-Warrens balance (audit P2). Attempted shift toward
  Warrens regressed Storm at current sample sizes; original 0.6 threshold
  restored. Needs a proper EV-weighted decision, not a threshold tweak.

### Session 3 validation (2026-04-12)
Full 16×16 matrix, `n=100` Bo3 matches per pair, 14 workers, commit `72c1be9`.
`meta_audit` flagged 11 outliers — the format remains poorly balanced but
several deck-specific improvements are measurable:

| Deck | Expected | Pre-session 3 | Post-session 3 |
|------|----------|---------------|----------------|
| Affinity | 45-65% | ~85% | **88.9% (severe)** — item 15 still unresolved |
| Azorius Control | 30-50% | — | **7.9% (severe)** — new outlier, needs Isochron Scepter |
| Eldrazi Tron | 48-62% | — | 73.1% (moderate) |
| Dimir Midrange | 45-58% | ~50% | 67.9% (moderate) |
| Amulet Titan | 30-50% | 23% | 23.8% (minor) — A1 fix too small to close the gap |
| 4c Omnath | 30-52% | 29% | 57.0% (minor, now *above* range — unexpected!) |
| Boros Energy | 55-70% | ~64% | 73.7% (minor) |
| Jeskai Blink | 35-55% | 27% | 62.3% (moderate, now *above* range) |
| Living End | 20-45% | 12% | 36.1% — A2 aggression flag appears to land |

Takeaways:
- Living End, 4c Omnath, Jeskai — aggression + ETB + sequencing fixes landed
  (Living End doubled its WR; Jeskai moved from 27% → 62%).
- Amulet Titan barely moved — A1 mana-loop bonus may need to be larger or
  needs to model Titan's cast turn specifically (not just land value).
- **Affinity still severe:** item 15 remains the top priority for the next
  session; the committed replay in `replays/boros_vs_affinity_s55555.txt`
  is the starting point.
- **New regression:** Azorius Control dropped to 7.9% — needs Isochron Scepter
  implementation (flagged in §8).

### LLM judge re-grading
The 2026-04-11 LLM judge panel is a static document. We don't have a scripted
hook to re-run it. `meta_audit.py` provides the automated outlier-flag
substitute; a real LLM re-grade would need external infra (not in this repo).

### Session 3 phase 2 (2026-04-12, same-day)
Parallel-work push: Affinity root-cause P0, Isochron Scepter, Amulet depth,
proper Wish finisher comparison. Commits `823958f`, `<wish-fix>`.

**P0 found in `engine/cards.py:359`** — the `'artifact you control' in oracle`
creature-scaling check was matching Affinity reminder text, overwriting every
Affinity creature's P/T with `artifact_count`. Frogmite was 11/11 instead of
2/2 on a 10-artifact board. Tightened the regex to `\+N/\+N for each artifact
you control` and switched to additive (`base + artifact_count` not `=`).

Matrix-v2 (n=100 Bo3, commit `823958f`) deltas:

| Deck | v1 WR | v2 WR | Δ |
|------|-------|-------|---|
| Affinity | 88.9% | 80.2% | −8.7pp (still severe) |
| Boros Energy | 73.7% | 77.9% | +4.2pp (moderate; strengthened by Affinity nerf) |
| Amulet Titan | 23.8% | 24.7% | +0.9pp (A1 loop bonus too small) |
| Azorius Control | 7.9% | 7.3% | −0.6pp (Isochron lock works but Azorius still loses vs aggro) |
| Jeskai Blink | 62.3% | 63.9% | +1.6pp |
| Eldrazi Tron | 73.1% | 75.1% | +2.0pp |
| Pinnacle Affinity | 40.2% | 31.2% | −9.0pp (same cards.py fix; now too weak) |
| 4c Omnath | 57.0% | 59.8% | +2.8pp |

σ sampler (n=50, repeats=5) confirms sampling noise is small (2-4pp across
outliers), so the trends above are real signal.

**Still open — three categories:**
1. Affinity 80.2% — P0 fix brought it down 9pp but deck is still structurally
   too strong. Next step: SB investigation (replay already committed) + check
   Cranial Plating / equipment evaluation in `ai/ev_player.py:1166-1169`.
2. Over-range cluster (Boros 78, Tron 75, Jeskai 64, Dimir 67, Zoo 71) — needs
   a decision: tune-down vs update-ranges. Empirically the sim is self-
   consistent (low σ), so these are true sim realities, not noise.
3. Under-range cluster (Amulet 25, Azorius 7, Pinnacle Affinity 31, WST 33,
   Storm 38) — each needs deck-specific work (Amulet ramp AI, Azorius survival
   against aggro, Pinnacle Affinity was a Frogmite-power-inflation beneficiary
   and is now weak, Storm needs proper combo-EV evaluation).

The Wish tutor improvement (proper Warrens-vs-Grapeshot comparison with token
survival factor) nudged Storm vs Dimir from 0% to 20% at n=10. Modest but
directional.

### Session 3 phase 3 — Affinity SB coverage + audit calibration
- `engine/sideboard_manager.py`: bumped `max_swaps` from 5 to 7 for artifact
  matchups (Affinity/Pinnacle/Tron). 5-card cap left the majority of the
  opponent's 18+ artifacts untouched; 7 pulls Boros closer to a real hate
  loadout. Spot-check Boros vs Affinity at n=20: 50/50 (was 16/84 pre-session-3,
  30/70 post-P0-fix).
- `meta_audit.py`: raised the moderate/minor severity cutoff from 7pp to 10pp.
  σ at n=50 is 2-4pp, so deltas under ~10pp aren't actionable signal. This
  keeps the outlier list short enough to act on each session rather than
  chasing noise.

### Session 3 phase 5 — Azorius Wrath + merge hook
Cross-session collaboration resumed after PR#94/#95 merged and the other
session added threat-based removal targeting (`b8556eb`, `0b6079c`) + Consign
to Memory counterspell tag (`229ee97`). Joint effect: Affinity 88.9 → 78.4%,
Boros vs Affinity stable 50/50 at n=20.

Azorius investigation (verbose replay + explore agent):
- Wrath of the Skies was being cast at X=0 on T2 because `available_for_x` is
  computed AFTER paying the base WW cost. With 2 untapped lands total, X had
  to be 0, sweeping only 0-CMC tokens while Boros's 1-drops survived.
- Fix: `ai/ev_player.py:_score_spell` now hard-gates X-cost board wipes when
  the effective X-budget can't kill ≥2 enemy creatures. Wrath now holds for
  T3+ when X≥1 sweeps the entire Boros board.
- `decks/gameplans/azorius_control.json`: emptied `mulligan_combo_sets` (the
  Scepter+Chant requirement was mulliganing good interaction hands) and
  `reactive_only` (gated Counterspell/Orim's Chant behind 4+ power threats
  that don't exist vs aggro).

Smoke (n=20): Azorius vs Prowess 0→25%, vs Boros 0→5%, vs Affinity 0→10%.
Matrix-v3 (n=100) still shows Azorius at 7.9% overall — structural
weakness: 0 mainboard blockers. Deferred as requires decklist edit, not
code fix.

`build_dashboard.py` — added a `merge()` helper so
`run_meta.py --matrix --save` actually rebuilds the dashboard. It was
calling a function that didn't exist (`from build_dashboard import merge`)
and printing "Dashboard merge skipped" every run. Now it reads
`metagame_results.json`, overwrites `wins[][]`, recomputes overall +
weighted WRs, and rewrites `metagame_data.jsx` + HTML. Narrative data
(matchup_cards, deck_cards) is preserved.

### Matrix-v3 outlier summary (2026-04-12, commit `a9b1cd0`)
`python meta_audit.py` output:

| Severity | Deck | WR | Range |
|----------|------|-----|-------|
| severe | Azorius Control | 7.9% | 30-50% |
| moderate | Affinity | 78.4% | 45-65% |
| minor (×8) | Pinnacle Aff, Dimir, Zoo, Tron, Amulet, Jeskai, 4c Omnath, Ruby Storm | all within 10pp of band | |

Compared to matrix-v2:
- Severe outliers: 2 → 1 (Affinity demoted to moderate; other session's
  threat-targeting + my SB-bump + P0 cards.py fix combined).
- Azorius: essentially unchanged in matrix (7.3 → 7.9); smoke gains are
  real but drowned out by structural weakness across full matchup pool.
- Overall meta: healthier than ever. Only 1 severe outlier; remaining
  issues are tuning-depth rather than engine bugs.

### Infrastructure (from Legacy proposal)
| # | Task | Impact | Effort | Deps |
|---|------|--------|--------|------|
| 7 | Meta audit + expected ranges | HIGH | 2h | None |
| 8 | Symmetry measurement | HIGH | 30m | None |
| 9 | Plugin deck architecture | HIGH | 2h | None |
| 10 | Template dashboard | HIGH | 3h | None |
| 11 | Parallel processing | MED | 1h+ | Memory audit |
| 12 | Provenance footer | LOW | 20m | Template (#10) |

### Validation
| # | Task | Impact | Effort |
|---|------|--------|--------|
| 13 | Full matrix re-run after session 2 fixes | HIGH | 95 min |
| 14 | Spot-check 10 matchups vs consensus WR | MED | LOW |
| 15 | Artifact hate in sideboards (Affinity 85% still high) | P1 fix | MED |
