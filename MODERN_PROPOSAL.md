# MTGSimManu — Cross-Pollination Proposal from MTGSimClaude (Legacy)

> **Date:** 2026-04-12
> **Author:** Analysis from MTGSimClaude benchmarking session
> **Scope:** Infrastructure, architecture, and process improvements. Does NOT propose changes to the EV scoring engine — that's Modern's strongest asset.

---

## Executive summary

MTGSimManu has the better AI brain (EV scoring, GoalEngine, LLM audit). MTGSimClaude has the better infrastructure (91× speed, plugin architecture, mature outputs, parallel processing). This proposal takes Legacy's infrastructure wins and maps them onto Modern's codebase — without touching the EV engine.

**Expected impact:** 3× faster iteration, zero-edit deck additions, automated outlier detection, and reproducible dashboard rebuilds.

**Note:** Modern submitted a counter-proposal (`LEGACY_MODERNISATION_PROPOSAL.md`) with 8 items for Legacy to adopt. Legacy accepted 5: strategic logger (`strategic_logger.py`, 279 lines), clock-based evaluation (`clock.py`, 328 lines), Bayesian hand inference (`bhi.py`, 275 lines), declarative gameplans, and symmetry averaging. These modules are now maintained by Modern for cross-project use — keep them portable.

---

## 1. Plugin deck architecture (HIGH priority)

### Problem
Adding a deck to Modern requires edits to 3+ files: `decks/modern_meta.py`, `decks/gameplans/*.json`, `ai/strategy_profile.py`. This friction limits deck count (15 vs Legacy's 38) and introduces coupling.

### Legacy solution
```
decks/
  storm.py       ← drop file here, done
  eldrazi.py
  burn.py
  ...
deck_registry.py ← auto-discovers on import, no engine edits
```

Each deck module exports a `DECK_META` dict with decklist, strategy weights, meta share, and gameplan. `deck_registry.py` scans `decks/` at import time.

### Proposed implementation
```python
# decks/boros_energy.py
DECK_META = {
    'name': 'Boros Energy',
    'key': 'boros_energy',
    'archetype': 'aggro',
    'meta_share': 0.12,
    'decklist': { ... },  # mainboard + sideboard
    'gameplan': { ... },   # goal sequence (currently in gameplans/*.json)
    'strategy_weights': { ... },  # currently in strategy_profile.py
    'sideboard_plan': { ... },    # currently in sideboard_manager.py
}
```

```python
# deck_registry.py (new file, ~60 lines)
import importlib, pathlib

DECKS = {}
for path in pathlib.Path('decks').glob('*.py'):
    if path.name.startswith('_'): continue
    mod = importlib.import_module(f'decks.{path.stem}')
    if hasattr(mod, 'DECK_META'):
        DECKS[mod.DECK_META['key']] = mod.DECK_META

METAGAME_SHARES = {k: v['meta_share'] for k, v in DECKS.items()}
```

**Migration:** One-time extraction from `modern_meta.py` + `gameplans/` + `strategy_profile.py` into per-deck modules. Existing API unchanged — `DECKS` dict still works.

**Effort:** ~2 hours. No AI changes.

---

## 2. Template-driven dashboard rebuild (HIGH priority)

### Problem
`build_dashboard.py` generates HTML from scratch each time (34K file). Any design fix must be re-applied after every rebuild. The canonical `metagame_data.jsx` (105K) is both data and presentation.

### Legacy solution
```
templates/reference_meta_matrix.html   ← design lives here (750K)
```
Dashboard rebuild = swap 5 data constants (`D`, `DA`, `C`, `I`, `ARCH`) in the template. Design never changes. Required JS functions verified post-build with grep.

### Proposed implementation
1. Extract current `modern_meta_matrix_full.html` as `templates/reference_modern_matrix.html`
2. Refactor `build_dashboard.py` to inject data into template, not generate HTML
3. Separate `metagame_data.jsx` into data-only JSON + presentation template

**Effort:** ~3 hours. Eliminates "dashboard looks different after rebuild" bugs permanently.

---

## 3. Parallel processing (MEDIUM priority)

### Problem
Full 14×14 matrix at n=50 takes ~95 minutes. Iteration cycle is too slow for strategy debugging.

### Legacy solution
`parallel.py` — 30 lines of Python multiprocessing. ~3× speedup on multi-core machines. Transparent to the caller — same API.

### Proposed implementation
```python
# parallel.py
from multiprocessing import Pool
from functools import partial

def _run_pair(args, n_games):
    d1, d2 = args
    # Import inside worker to avoid shared state
    from run_meta import run_matchup
    return (d1, d2, run_matchup(d1, d2, n_games))

def run_matrix_parallel(decks, n_games=50, workers=4):
    pairs = [(d1, d2) for d1 in decks for d2 in decks if d1 != d2]
    with Pool(workers) as pool:
        results = pool.map(partial(_run_pair, n_games=n_games), pairs)
    return {(d1, d2): wr for d1, d2, wr in results}
```

**Constraint:** `CardDatabase` singleton (400MB) must be loaded per-worker or use shared memory. Legacy avoids this by having no DB — all cards are in-code.

**Estimated speedup:** 95 min → ~32 min (3× on 4 cores). Could go to ~16 min with 8 cores if memory allows.

**Effort:** ~1 hour + memory profiling.

---

## 4. Meta audit & outlier detection (MEDIUM priority)

### Problem
No automated way to detect when a sim result is unrealistic. Affinity at 82% WR went undetected until manual review.

### Legacy solution
`meta_audit.py` — post-simulation analysis that:
- Compares sim WR to expected tournament performance
- Flags matchups >15pp off expected
- Generates HTML audit dashboard
- Strategy audit checklist: does strategy deploy every win condition?

### Proposed implementation
```python
# meta_audit.py (port from Legacy, ~200 lines)
EXPECTED_RANGES = {
    'boros_energy': (0.50, 0.65),
    'affinity': (0.45, 0.60),      # NOT 82%
    'ruby_storm': (0.45, 0.58),    # NOT 37%
    '4c_omnath': (0.48, 0.62),     # NOT 29%
    ...
}

def audit_matrix(results):
    outliers = []
    for deck, (lo, hi) in EXPECTED_RANGES.items():
        actual = results[deck]['flat_wr']
        if actual < lo or actual > hi:
            outliers.append((deck, actual, lo, hi))
    return outliers
```

Would have caught Affinity (82% vs expected 45-60%), 4c Omnath (29% vs 48-62%), and Ruby Storm (37% vs 45-58%) immediately.

**Effort:** ~2 hours.

---

## 5. Symmetry measurement (MEDIUM priority)

### Problem
Unknown whether d1_vs_d2 + d2_vs_d1 ≈ 100%. If not, the engine treats p1 and p2 differently — a fundamental fairness bug.

### Legacy findings
- 42% of pairs within ±5%
- 60 pairs deviate >15%
- Worst: 41% deviation (mardu vs ocelot)
- Root cause: proxy strategies with asymmetric dispatch

### Proposed implementation
Add to `run_meta.py --matrix` post-processing:
```python
def check_symmetry(results):
    issues = []
    for (d1, d2), wr in results.items():
        if (d2, d1) in results:
            total = wr + results[(d2, d1)]
            if abs(total - 1.0) > 0.10:
                issues.append((d1, d2, wr, results[(d2, d1)], total))
    return sorted(issues, key=lambda x: -abs(x[4] - 1.0))
```

Run both orderings for every pair and report deviations. Flag any >10% as a bug.

**Effort:** ~30 minutes.

---

## 6. Provenance footer on all outputs (LOW priority)

### Problem
Dashboard and replay outputs don't show sim parameters (n_games, seed range, date, deck count, engine version). Makes it impossible to know which run produced the data.

### Legacy standard
Every output includes:
```
Simulated: 2026-04-12 | Decks: 38 | Games/pair: 30 | Engine: MTGSimClaude v1
Matrix: matrix_20260411_134630.json | Seeds: deterministic
```

### Proposed implementation
Add footer div to `build_dashboard.py` and `build_replay.py` output templates. Pull metadata from results JSON header.

**Effort:** ~20 minutes.

---

## 7. What NOT to adopt from Legacy

These are Legacy weaknesses that Modern already handles better:

| Legacy approach | Why it's worse | Modern's better solution |
|----------------|---------------|------------------------|
| Hard-coded strategy functions | O(decks) scaling, 787 if/elif branches | EV scoring — one engine for all decks |
| Manual card builders (119K cards.py) | Every new card = hand-written code | MTGJSON (21,795 cards auto-loaded) |
| G1-only matrix | No sideboard effects in batch data | Full Bo3 with bool-flag SB |
| No planeswalker tracking | Loyalty ticks not modeled | Partial tracking (needs EV fix) |
| 4-level threat classification | Too coarse for nuanced decisions | Continuous EV scoring |
| Tag-based card identity (73 tags) | Brittle, explosion over time | Oracle text + card_effects.py |

---

## 8. Implementation order

| # | Item | Effort | Impact | Dependencies |
|---|------|--------|--------|-------------|
| 1 | Meta audit + expected ranges | 2h | Catches broken decks immediately | None |
| 2 | Symmetry measurement | 30m | Finds engine fairness bugs | None |
| 3 | Plugin deck architecture | 2h | Unblocks rapid deck additions | None |
| 4 | Template dashboard | 3h | Stable visual output | None |
| 5 | Parallel processing | 1h + profiling | 3× faster iteration | Memory audit |
| 6 | Provenance footer | 20m | Traceability | Template dashboard |

Total estimated: ~9 hours of implementation. No changes to AI engine, EV scoring, or game rules.

---

## 9. Shared skills & standards

Both projects share the same Claude skills (`/mtg-meta-matrix`, `/mtg-deck-guide`, `/mtg-bo3-replayer-v2`, `/mtg-dashboard-refresh`). Adopting the template dashboard approach would let Modern use the same skill pipeline:

```
Skill: /mtg-meta-matrix
  ├── MTGSimClaude: templates/reference_meta_matrix.html + swap D,DA,C,I,ARCH
  └── MTGSimManu:   templates/reference_modern_matrix.html + swap D,DA,C,I,ARCH  ← NEW
```

Same methodology, format-specific data. The skill becomes truly cross-format.

---

*This proposal was generated from a live benchmarking session comparing both codebases. All numbers are from actual runs on 2026-04-12.*
