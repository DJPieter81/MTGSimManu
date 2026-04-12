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
| 1 | Pro-insights dashboard | `build_dashboard.py` | 124 | ❌ Dashboard lacks proInsights() | HIGH |
| 2 | Bo3 replay viewer | `build_replay.py` | 591 | ⚠️ Has `game_replay.py` (840) but no HTML viewer | HIGH |
| 3 | Deck guide generator | `build_guide.py` | 270 | ⚠️ Has `gen_guides.py` (397) — compare features | MED |
| 4 | Bool-flag sideboard | `sideboard_manager.py` | 158 | ❌ Different SB approach | MED |
| 5 | Full combat sim | `combat_manager.py` | 334 | ❌ Simplified combat | LOW |
| 6 | 5-ordering turn planner | `turn_planner.py` | 1113 | ❌ Single ordering | LOW |
| 7 | Combo assessment | `combo_calc.py` | 652 | ❌ No combo scoring | LOW |
| 8 | Continuous effects | `continuous_effects.py` | 379 | ❌ No layer system | LOW |

### Adoption notes
- **#1 proInsights():** 5 auto-derived findings per matchup (G1→match swing, sweep asymmetry, speed gap, removal blind spots, zero comebacks). Inject via post-processing — no ENGINE string edits.
- **#2 Replay viewer:** Modern's `build_replay.py` produces standalone HTML with SVG life chart, collapsible turns, keyboard nav. Legacy's `game_replay.py` outputs text. Port the HTML builder.
- **#3 Deck guides:** Compare Modern's `build_guide.py` (Stars of Sim, G1→match swing, danger cards, tiered matchup spread) vs Legacy's `gen_guides.py` (7 features). Merge best of both.

---

## Common Standards

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
3. **Legacy:** Adopt `proInsights()` dashboard function (5 auto-derived findings per cell)
4. **Legacy:** Adopt `build_replay.py` HTML viewer (currently text-only replays)
5. **Both:** Keep shared modules (`clock.py`, `bhi.py`, `strategic_logger.py`, `gameplan.py`) portable
