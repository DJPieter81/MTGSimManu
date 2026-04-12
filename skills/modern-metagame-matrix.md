---
name: modern-metagame-matrix
description: Generate interactive Modern metagame heatmaps from simulation data. Use this skill whenever the user wants to build a Modern meta matrix, metagame visualization, matchup heatmap, deck tier analysis, or interactive matchup grid. Also triggers on requests to visualize win rates across many decks, create tournament prep tools, or build interactive HTML/React dashboards showing deck-vs-deck performance. Use this skill even if the user just says "build the matrix" or "make the heatmap" or "run the metagame" in the context of MTG sim work. Triggers on: metagame matrix, meta matrix, matchup heatmap, tier list, weighted win rate, sideboard analysis, round-robin, bo3 simulation.
---

# Modern Metagame Matrix

Build interactive metagame matrices from MTG simulation data with card-level insights, sideboard analysis, weighted win rates, and strategic narratives.

## When to Use

- User wants a metagame matrix / matchup heatmap for any MTG format
- User asks for tier lists, deck rankings, or matchup analysis
- User wants to simulate round-robin bo3 matches between decks
- User wants sideboard guides derived from actual game data
- User asks for weighted win rates based on metagame representation

## Prerequisites

1. **MTGSimManu repo** cloned and on `main` branch
2. **ModernAtomic.json** card database assembled (merge parts if needed)
3. Python 3.11+, no external dependencies

```bash
git clone https://github.com/DJPieter81/MTGSimManu.git
cd MTGSimManu && git checkout main
```

If `ModernAtomic.json` is missing, merge the parts:
```python
import json, glob
merged = {}
for p in sorted(glob.glob('ModernAtomic_part*.json')):
    with open(p) as f:
        merged.update(json.load(f).get('data', json.load(open(p))))
with open('ModernAtomic.json', 'w') as f:
    json.dump({'data': merged}, f)
```

## Pipeline Overview

The skill runs a 4-phase pipeline. Each phase builds on the previous.

### Phase 1: Round-Robin Win Rates (fast, no verbose)

Run `scripts/metagame_fast.py` — loads DB once, runs N bo3 per pairing.

- **Speed**: ~13ms/game, 50 bo3/pair in ~170s, 100/pair in ~330s (split into 2 batches if timeout)
- **Output**: `metagame_100.json` with wins matrix, overall WR, per-matchup stats (avg turns, sweeps, G1 WR, comeback rate)
- **Key**: Load CardDatabase ONCE and reuse. Never reload per match.

```bash
python3 scripts/metagame_fast.py  # outputs metagame_100.json
```

If timeout is a concern, split into two 50-match batches with different seed offsets and merge.

### Phase 2: Card Insights (verbose, smaller sample)

Run `scripts/extract_kills.py` or inline — 20 verbose bo3/pair to extract:

- **Top casts** per side (which spells are cast most)
- **Damage sources** (direct damage cards)
- **Finishers** with kill-type analysis (storm/ETB/burn/combat)
- **Kill context**: storm count, overkill amount, attacker count, avg turn

```bash
python3 scripts/sb_track.py  # outputs card_insights.json + sb_actual.json
```

### Phase 3: Sideboard Analysis (from actual games)

The engine's `sideboard_manager.py` performs actual card swaps between G1 and G2/G3 using archetype-aware heuristics. Track:

- **Actual IN/OUT cards** (what the engine swapped)
- **SB card cast counts** (how often boarded-in cards were actually played)
- **Post-board WR delta** (G1 WR vs G2/G3 WR)

Coverage target: 78/78 matchups. If gaps exist, expand the `sideboard_manager.py` heuristics using deck-type flags:

```python
opp_is_aggro = any(w in opp_lower for w in ["energy", "zoo", "prowess", "affinity", "boros"])
opp_is_combo = any(w in opp_lower for w in ["storm", "living end", "goryo"])
opp_is_gy = any(w in opp_lower for w in ["goryo", "living end", "storm", "prowess"])
# etc.
```

### Phase 4: Weighted Win Rates

Load `decks/metagame.json` (meta share %) and compute:

```
weighted_wr[i] = Σ(win_rate[i][j] × meta_share[j]) / Σ(meta_share[j])  for j ≠ i
```

This weights matchups by how often you'd face each opponent in a real tournament.

### Phase 5: Dashboard Generation

Build a React JSX artifact embedding all data. Structure:

```jsx
const D = { decks, wins, matches_per_pair, overall, matchup_cards, deck_cards, meta_shares };
```

The dashboard provides:
- **Heatmap matrix** — clickable cells, sorted by weighted WR
- **Matchup detail panel** — strategic insight, stats, card data, sideboard guide
- **Deck profile panel** — overview, MVPs, finishers with kill descriptions, all matchups
- **Three WR columns** — flat WR, weighted WR (⚖), meta share (%)

## Key Design Decisions

### Performance
- **Single DB load**: CardDatabase takes ~5s to load 21K+ cards. Load once, reuse for all games.
- **Non-verbose for win rates**: 13ms/game. Verbose adds log parsing but same game speed.
- **Batch if needed**: Split 100/pair into 2×50 with different seed ranges to avoid timeouts.

### Data Accuracy
- **100 bo3/pair minimum** for stable win rates (±5-8% confidence)
- **Sideboard from actual games**, not heuristic recommendations
- **Kill analysis from game logs**, parsing the last 12 lines for kill type/card/context
- **Weighted WR** using real metagame representation data

### Sideboard Manager Coverage
The sideboard_manager uses deck-type boolean flags instead of fragile deck-name substring matching. Each flag covers multiple deck names:

| Flag | Matches |
|---|---|
| `opp_is_aggro` | energy, zoo, prowess, affinity, boros |
| `opp_is_combo` | storm, living end, goryo |
| `opp_is_gy` | goryo, living end, storm, prowess |
| `opp_is_artifacts` | affinity, tron, eldrazi |
| `opp_is_big_mana` | tron, eldrazi, titan, amulet |
| `opp_is_midrange` | midrange, dimir, omnath, control, blink, jeskai |
| `opp_is_creature` | all creature-based decks |
| `opp_is_blue` | dimir, izzet, jeskai, blink, omnath, control |

Add deck-specific fallback OUT targets (e.g., Affinity trims Nettlecyst vs combo, Dimir trims Drown in the Loch vs midrange) to reach 78/78 coverage.

### Dashboard UI Patterns
- Dark theme (#1a1814 base) with JetBrains Mono
- Gold (#c9a227) for primary highlights
- Green→red gradient for win rate cells
- Pill components for card names with category colors
- SB panel: green ▲ IN pills, red ▼ OUT pills, cast counts, WR delta indicators

## File Reference

| File | Purpose |
|---|---|
| `scripts/metagame_fast.py` | Phase 1: Fast round-robin runner |
| `scripts/extract_kills.py` | Phase 2: Kill context analysis |
| `scripts/sb_track.py` | Phase 3: SB swap + cast tracking |
| `references/sideboard_manager.py` | Improved SB heuristics (78/78 coverage) |
| `references/dashboard_component.md` | React component architecture notes |

## Adaptation Guide

To adapt for a different format (Legacy, Pioneer, etc.):

1. Replace `MODERN_DECKS` with format-specific deck lists
2. Update `decks/metagame.json` with format meta shares
3. Update sideboard_manager deck-type flags for new deck names
4. Adjust `ModernAtomic.json` card database (or use appropriate MTGJSON file)
5. Run the same 4-phase pipeline

The dashboard component is format-agnostic — it renders whatever data is in the `D` const.

## Current State (April 2026)

- 16 decks (incl. Pinnacle Affinity, Azorius Control WST)
- Canonical data: `metagame_data.jsx` (D object)
- Dashboard: `modern_meta_matrix_full.html` (vanilla JS, 163K)
- Live: https://djpieter81.github.io/MTGSimManu/modern_meta_matrix_full.html

## Full Pipeline (Cowork-ready)

```
git pull origin main → python merge_db.py → python run_meta.py --matrix -n 50 --save → python build_dashboard.py --merge → python build_guide.py --all outputs/ → auto-trigger replays → git commit and push
```

Read CLAUDE.md for complete instructions.

## Pro-Level Strategic Insights

`build_dashboard.py` auto-injects `proInsights(p, i, j)` into every dashboard build. Click any matchup cell → "Strategic Insights" panel with 1-3 findings: G1→match swing (≥12pp), sweep asymmetry, speed gap (≥1.5 turns), removal blind spots, zero comebacks. No manual work needed.
