---
name: mtg-dashboard-refresh
description: Refresh MTG metagame dashboard with full card-level detail from verbose Bo3 simulations. Use this skill whenever the user wants to refresh, rebuild, or update the metagame dashboard/matrix after simulations, extract card-level data (damage, casts, finishers, sideboard swaps, insights) from verbose game logs, or rebuild the standalone HTML dashboard. Triggers on "refresh the dashboard", "rebuild the matrix", "update card data", "extract card data", "run the extraction", "refresh after sim", "update the heatmap with new data", or any post-simulation dashboard rebuild request. Also use when the user says "do N iterations and refresh", "rerun extraction", or complains about missing detail in the dashboard (insights, sideboard, damage, summaries). This skill covers the full pipeline from running verbose Bo3, extracting structured card data, merging into the JSX D object, and building the final HTML.
---

# MTG Dashboard Refresh

Rebuild the metagame dashboard with full card-level detail extracted from verbose Bo3 simulations. This is the post-simulation pipeline that turns raw game logs into a rich interactive dashboard with matchup insights, per-card damage, sideboard guides, finisher descriptions, and deck summaries.

## When to Use

- After running metagame simulations (matrix batches), to update the dashboard with new data
- When the dashboard is missing detail (insights, sideboard data, damage attribution, summaries)
- When the user wants a full refresh of the dashboard from scratch
- When updating card data after code changes to the engine

## Prerequisites

1. **MTGSimManu repo** checked out at project root
2. **ModernAtomic.json** card database assembled
3. **metagame_results.json** from prior `run_meta.py --matrix --save` runs (for win rate data)
4. **metagame_data.jsx** with existing D object (at minimum: decks, wins, matches_per_pair, overall, meta_shares)

## Pipeline

### Phase 1: Win Rate Matrix (if not already done)

Run the fast round-robin to get win percentages:

```bash
cd MTGSimManu
python3 run_meta.py --matrix -n 50 --save
```

This writes `metagame_results.json` with keys like `"Deck A|Deck B": percentage`. The `--save` flag overwrites each time, so for multiple batches, copy results between runs and merge by averaging percentages.

**Converting to JSX D object**: The D object needs `wins[i][j]` as win counts. Convert from percentages:
```python
wins[i][j] = round(pct * matches_per_pair / 100)
```

### Phase 2: Verbose Card Data Extraction

This is the core of the skill. Run `extract_card_data.py` to play verbose Bo3 matches for all deck pairs and extract structured card-level data:

```bash
python3 extract_card_data.py [bo3_per_pair]  # default: 10
```

This produces `card_data.json` with two top-level keys:

#### matchup_cards (keyed as "i,j" where i < j)

Each entry contains:
- `d1`, `d2`: deck names
- `d1_wins`, `d2_wins`: match wins from the verbose sample
- `avg_turns`: average game length
- `sweeps`, `went_to_3`, `g1_wins`, `comebacks`: series statistics
- `d1_top_casts`, `d2_top_casts`: top 3 most-cast cards per side (card + count)
- `d1_top_damage`, `d2_top_damage`: top 2 damage-dealing cards per side
- `d1_finishers`, `d2_finishers`: top 2 game-winning cards with `desc` field
- `insight`: auto-generated matchup narrative (WR, speed, sweep rate, comebacks)
- `d1_sb`, `d2_sb`: sideboard swap data (IN/OUT cards + cast counts)

#### deck_cards (array of 16 decks)

Each entry contains:
- `deck`, `idx`: deck name and index
- `mvp_casts`: top 5 most-cast cards across all matchups
- `mvp_damage`: top 3 damage-dealing cards
- `finishers`: top 4 game-winning cards with `desc`
- `summary`: auto-generated deck overview (WR, meta share, archetype, finishers)

### Phase 3: Merge Card Data into JSX

After extraction, merge `card_data.json` into the existing `metagame_data.jsx`:

```python
import json, re

with open('metagame_data.jsx') as f:
    src = f.read()
m = re.search(r'const D = (\{.*?\});\nconst N', src, re.DOTALL)
D = json.loads(m.group(1))

with open('card_data.json') as f:
    cd = json.load(f)

D['matchup_cards'] = cd['matchup_cards']
D['deck_cards'] = cd['deck_cards']

# Write compact (no indent) so the non-greedy regex in build_dashboard.py works
d_json = json.dumps(D, separators=(',', ':'))
with open('metagame_data.jsx', 'w') as f:
    f.write(f'const D = {d_json};\nconst N = D.decks.length;\n')
```

**Critical**: Write the JSX with compact JSON (no indentation). The `build_dashboard.py` uses a non-greedy regex `\{.*?\}` that breaks on indented JSON with nested braces.

### Phase 4: Build Dashboard HTML

```bash
python3 build_dashboard.py metagame_data.jsx /path/to/output/modern_meta_matrix_full.html
```

Note: first arg is JSX path, second is output path. The default output path is `/mnt/user-data/outputs/modern_meta_matrix_full.html` which may not exist in all environments.

## Key Technical Details

### extract_card_data.py Internals

**Damage extraction** parses two patterns from verbose game logs:
1. **Combat damage**: `[Declare Attackers] P1 attacks with: Card1, Card2` followed by `[Combat Damage] N damage dealt`. Damage is distributed evenly across attackers.
2. **Direct damage**: `T{n} P{n}: CardName deals N damage` or `CardName: N damage to opponent`. Also handles planeswalker activations.

**Card name splitting**: MTG card names can contain commas (e.g., "Ragavan, Nimble Pilferer"). The splitter uses greedy matching against known card names from `MODERN_DECKS` to correctly parse attacker lists.

**Sideboard capture**: The `sideboard_manager.py` prints swap info to stderr. The extraction script redirects stderr during each `run_match()` call to capture `Sideboard (Deck vs Opp): +1 CardA, -1 CardB` lines. Uses try/finally to ensure stderr is always restored.

**Insight generation**: Auto-generated from matchup stats using templates:
- WR threshold: 80%+ = "crushes", 65%+ = "dominates", 55%+ = "favored", else "even"
- Speed: <=6t = "lightning fast", <=9t = "mid-speed", else "grindy"
- Sweep rate: 60%+ = "polarized", went-to-3 50%+ = "competitive"

**Finisher descriptions**: Context-aware based on card name patterns (burn, titan, evasive, combo).

**Deck summaries**: WR tier + archetype + top finishers + average kill turn.

### Known Issues

- Some matchups error with `max() arg is an empty sequence` (no games complete) or `'draw'` results. These are caught and logged; the matchup still gets partial data.
- `metagame.json` values are already percentages (not fractions). Don't multiply by 100 again.
- SB data coverage is ~30-35% of matchups because many are 2-0 sweeps with no sideboarding.
- Damage from Tokens (Warrior Token, Cat Token) is tracked separately from the card that created them.

### GameRunner API Reference

```python
from engine.card_database import CardDatabase
from engine.game_runner import GameRunner
from decks.modern_meta import MODERN_DECKS, get_all_deck_names

db = CardDatabase('ModernAtomic.json')  # ~5s load, do once
runner = GameRunner(db)

import random
random.seed(12345)  # seed externally, no seed param on run_match
result = runner.run_match(d1, MODERN_DECKS[d1], d2, MODERN_DECKS[d2], verbose=True)
# Returns MatchResult with .games (List[GameResult]), .winner_deck, .match_score

# GameResult fields: game_log (List[str]), winner_deck, turns, win_condition,
#   deck1_damage_dealt, deck2_damage_dealt (totals only, not per-card)
```

## Typical Invocation

Full refresh from scratch (takes ~15-20 min for 105 pairs x 10 bo3):

```bash
cd MTGSimManu
python3 run_meta.py --matrix -n 50 --save          # Phase 1: ~3 min
python3 extract_card_data.py 10                      # Phase 2: ~12 min
python3 -c "..."                                     # Phase 3: merge (inline)
python3 build_dashboard.py metagame_data.jsx out.html  # Phase 4: instant
```

Quick refresh (card data only, reusing existing WR matrix):

```bash
python3 extract_card_data.py 10
# merge card_data.json into metagame_data.jsx
python3 build_dashboard.py metagame_data.jsx out.html
```

## Verification

After building, check these metrics match expectations:
- Insights: 105/105 (all matchups should have auto-generated insights)
- SB data: ~30-35/105 (limited by 2-0 sweep rate)
- Finisher descs: ~400+ (2 per matchup per side)
- Damage data: 15/15 (all decks)
- Summaries: 15/15 (all decks)
- Dashboard HTML size: ~150-170KB for 16 decks (reference was 126KB for 16 decks)

## Pro-Level Strategic Insights (NEW)

`build_dashboard.py` now injects a `proInsights(p, i, j)` function via post-processing. Click any matchup cell → "Strategic Insights" panel (amber left border) with auto-derived findings:
- G1→match swing (≥12pp)
- Sweep asymmetry
- Speed gap between closers (≥1.5 turns)
- Removal blind spots (for losing matchups)
- Zero comebacks analysis

## Post-Dashboard: Generate Guides + Replays

After rebuilding the dashboard, also run:
```bash
python build_guide.py --all /mnt/user-data/outputs/  # All deck guides
# Then auto-trigger replays per CLAUDE.md
```
