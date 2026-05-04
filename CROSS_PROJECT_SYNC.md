# Cross-Project Sync — MTGSimManu (Modern) ↔ MTGSimClaude (Legacy)

> **Last updated:** 2026-04-12
> **Read by:** Both CLAUDE.md files, Cowork, Claude Code
> **Rule:** Check this file before starting cross-project work

---

## Shared Modules (Modern maintains, both use)

| Module | Lines | Purpose | Legacy status |
|--------|-------|---------|---------------|
| `clock.py` | 328 | Turns-to-kill calculator | ✅ Adopted |
| `bhi.py` | 275 | Bayesian hand inference | ✅ Adopted |
| `strategic_logger.py` | 279 | AI reasoning traces | ✅ Adopted |
| `gameplan.py` | 545 | Declarative goal sequences | ✅ Adopted |

Keep these portable — no project-specific imports.

---

## Legacy → Modern (infrastructure adoption)

| # | Feature | Legacy file | Lines | Modern status | Priority |
|---|---------|-------------|-------|---------------|----------|
| 1 | Plugin deck architecture | `deck_registry.py` | 161 | ❌ Uses monolithic `modern_meta.py` | HIGH |
| 2 | Parallel sim execution | `parallel.py` | 111 | ❌ Serial only (2hrs for full matrix) | HIGH |
| 3 | Statistical significance | `hypothesis_testing.py` | 935 | ❌ No stat testing on WR diffs | HIGH |
| 4 | Metagame audit | `meta_audit.py` | 547 | ⚠️ Partial (`scan_results.py`) | MED |
| 5 | LLM game judge | `llm_judge.py` | 237 | ❌ Proposed but not implemented | MED |
| 6 | Post-sim verification | `verify.py` | 174 | ❌ Has tests but no post-sim checks | MED |
| 7 | Card validation | `card_validation.py` | 548 | ⚠️ Partial (oracle_parser) | LOW |
| 8 | Rich terminal tables | `verbose_table.py` | 666 | ❌ Raw text output | LOW |
| 9 | One-command refresh | `refresh_all.py` | 70 | ⚠️ Cowork task, no script | LOW |

### Adoption notes
- **#1 Plugin arch:** Would let users add decks by dropping a file. Currently requires edits to `modern_meta.py` + `gameplans/*.json` + `strategy_profile.py`. Legacy's `deck_registry.py` auto-discovers on import.
- **#2 Parallel:** Modern's 0.68s/Bo3 × 12,000 pairs = 2.3hrs serial. Legacy does 38 decks in minutes via multiprocessing. Would cut Cowork pipeline from 95min to ~20min.
- **#3 Hypothesis testing:** Modern reports 60% WR but can't say if it's significantly different from 50%. Legacy's `hypothesis_testing.py` adds p-values and confidence intervals. Critical for validating that sim WR differences are real.

---

## Modern → Legacy (AI + output adoption)

| # | Feature | Modern file | Lines | Legacy status | Priority |
|---|---------|-------------|-------|---------------|----------|
| 1 | Pro-insights function | `proInsights()` in build_dashboard.py | ~60 | ❌ Dashboard has card data + events but no auto-derived findings | HIGH |
| 2 | G1/G3/sweep/comeback stats | matchup_cards fields | — | ❌ Dashboard lacks G1 WR, went_to_3, sweeps, comebacks | HIGH |
| 3 | Sideboard guide section | `sbLines()` in dashboard | — | ❌ No SB swap display in matchup detail | MED |
| 4 | Bool-flag sideboard | `sideboard_manager.py` | 158 | ❌ Different SB approach | MED |
| 5 | Full combat sim | `combat_manager.py` | 334 | ❌ Simplified combat | LOW |
| 6 | 5-ordering turn planner | `turn_planner.py` | 1113 | ❌ Single ordering | LOW |
| 7 | Combo assessment | `combo_calc.py` | 652 | ❌ No combo scoring | LOW |
| 8 | Continuous effects | `continuous_effects.py` | 379 | ❌ No layer system | LOW |

### Adoption notes
- **Legacy dashboard is already interactive (767K)** — has clickable heatmap, card-level data (finishers, casts, attackers, damage), "What Happens" events (Lock/Hate, Removal, Counters, Pivotal), game plans, deck profiles, and tier system. What it lacks is the `proInsights()` auto-derived findings and Bo3-specific stats (G1 WR, G3 rate, sweeps, comebacks).
- **Legacy already has HTML replays** — `replay_oops_vs_dimir_flash.html` has dark theme, game tabs, life tracking. Same v2 replayer format as Modern.
- **#1 proInsights():** Port the 60-line JS function and inject via post-processing in `build_matrix_html.py`. Needs matchup_cards fields (G1 wins, sweeps, comebacks) extracted during sim.
- **#3 Deck guides:** Legacy's `gen_guides.py` (397 lines) already produces 7-feature guides. Compare with Modern's `build_guide.py` (270 lines) — merge Stars of Sim section and 6 pro-level findings.

---

## Common Standards

### Match format (canonical: Bo3)
- **Modern (MTGSimManu):** As of 2026-05-04, `run_meta.py` defaults all
  matrix / matchup / field runs to **Bo3 with sideboarding**. Bo1 is
  diagnostic-only (`--bo1` flag). Driver: 2026-05-04 user directive
  *"many people sideboard against artifacts. so we should rely on g1
  stats, should always be bo3. we should note this throughout"*.
  Class H "Bo1 hate-card density" findings in the combo-audit
  methodology are largely Bo1-framing artifacts under this default
  (see `docs/design/2026-05-04_modern_combo_audit_methodology.md` top
  caveat).
- **Legacy (MTGSimClaude):** Has historically used a G1-only / Bo1
  matrix per its own `CROSS_PROJECT_SYNC.md`. **Action item for the
  next Legacy session:** verify whether Legacy's matrix still defaults
  to Bo1; if so, the same Bo3 reframe applies, and Legacy's Class H
  audit findings (lessons #29/#30) inherit the same caveat. If Legacy
  is already on Bo3, treat this as confirmation that the two projects
  are aligned.
- **Both:** WR numbers cited in cross-project diagnostics should
  carry an explicit `format: bo1|bo3` tag so adoption decisions
  aren't made on incomparable measurements.

### File naming
- Canonical data: `metagame_data.jsx` (not `metagame_14deck.jsx` or `meta_fresh.json`)
- Dashboard: `modern_meta_matrix_full.html` / `legacy_meta_matrix.html`
- Deck guides: `guide_{deck_slug}.html`
- Replays: `replay_{d1}_vs_{d2}_s{SEED}.html`

### GitHub Pages
- Modern: `https://djpieter81.github.io/MTGSimManu/`
- Legacy: `https://djpieter81.github.io/MTGSimClaude/`
- All links in templates MUST be absolute (not relative)
- HTML for Pages must be committed to repo, not just `/mnt/user-data/outputs/`

### Skills
- Stored in `skills/` folder in each repo
- Format: `{skill-name}.md` with frontmatter
- Shared skills: `/mtg-meta-matrix`, `/mtg-deck-guide`, `/mtg-bo3-replayer-v2`

### Provenance footer (all HTML outputs)
```
Simulated: {date} · {N} decks · {games}/pair · Engine: {repo}
Source: {data_file} · Shell: ManusAI · Strategy: Claude · Owner: DJPieter81
```

---

## Next Actions

1. **Modern:** Adopt `parallel.py` + `hypothesis_testing.py` (cuts matrix time 5×, adds stat rigor)
2. **Modern:** Adopt `deck_registry.py` (enables user deck additions without code edits)
3. **Legacy:** Port `proInsights()` into `build_matrix_html.py` (5 auto-derived findings per cell)
4. **Legacy:** Extract G1 WR, G3%, sweeps, comebacks during sim — feed into dashboard
5. **Legacy:** Add sideboard guide section to matchup detail panel
6. **Both:** Keep shared modules (`clock.py`, `bhi.py`, `strategic_logger.py`, `gameplan.py`) portable
7. **Both:** Merge deck guide pipelines — Legacy's 7-feature `gen_guides.py` + Modern's Stars/findings `build_guide.py`
