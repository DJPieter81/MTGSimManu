# Overnight Iteration Task

## Goal
Improve deck win rates toward EXPECTED ranges through small, safe, testable changes.
Each iteration: test → diagnose → fix ONE thing → retest → commit or revert.

## Current Outliers (April 13 2026)
| Deck | Current | Expected | Gap |
|------|---------|----------|-----|
| Affinity | 93% | 45-60% | +33pp (way too strong) |
| Living End | 5% | 30-70% | -25pp (broken) |
| ETron | 72% | 50-65% | +7pp |
| Boros Energy | 67% | 50-70% | OK but barely |

## Setup (every session)
```bash
cd /home/claude/MTGSimManu   # or clone if needed
git pull --rebase origin main
python3 merge_db.py
cat CLAUDE.md | head -100     # refresh context
cat PROJECT_STATUS.md | head -50
```

## Iteration Loop

### Step 1: Measure (30 seconds)
```bash
python3 iterate_test.py       # runs field test for outlier decks, n=5
```
This prints a table of all decks with WR and flags outliers.

### Step 2: Diagnose (2-3 minutes)
Pick the WORST outlier. Run a verbose trace:
```bash
python3 run_meta.py --verbose "OUTLIER_DECK" "OPPONENT" -s 60000
```
Read the log. Look for ONE of these root causes:
- **Doesn't attack** → check `decide_attackers`, `plan_attack` scoring
- **Doesn't block** → check `decide_blockers`, `_eval_block` thresholds
- **Doesn't cast key spell** → check `_score_spell` for that card type
- **Casts wrong spell** → check spell priority / EV ordering
- **Wrong mulligan** → check mulligan_keys, combo_sets in gameplan
- **SB doesn't bring in hate** → check `sideboard_manager.py` rules
- **Card effect missing/wrong** → check `card_effects.py` EFFECT_REGISTRY
- **Kill turn too slow** → check combo chain, ritual EV, finisher gate

### Step 3: Fix ONE thing (5-10 minutes)
Rules:
- Change at most ONE file, at most 20 lines
- No hardcoded card names (use oracle text patterns, tags, CMC/power thresholds)
- No new dependencies
- Keep existing tests passing: `python3 -m pytest tests/ -q` (if tests exist)

### Step 4: Retest (1-2 minutes)
```bash
# Test the specific matchup you fixed
python3 -c "
from run_meta import run_matchup
r = run_matchup('DECK1', 'DECK2', n_games=10)
print(f'{r[\"pct1\"]}-{r[\"pct2\"]}%')
"

# Quick regression check on 3 key matchups
python3 -c "
from run_meta import run_matchup
for d1, d2 in [('Boros Energy','Affinity'), ('Boros Energy','Domain Zoo'), ('Ruby Storm','Living End')]:
    r = run_matchup(d1, d2, n_games=5)
    print(f'{d1:20s} vs {d2:15s}: {r[\"pct1\"]:3d}-{r[\"pct2\"]:3d}%')
"
```

### Step 5: Commit or Revert
If the fix improved the target matchup AND didn't regress key matchups:
```bash
git add -A
git commit -m "fix: <one-line description>

<what was wrong>
<what changed>
<before/after WR>"
git pull --rebase origin main
git push origin main
```

If it regressed something:
```bash
git checkout -- .
```

### Step 6: Loop
Go back to Step 1. Stop after 20 iterations or when no outlier exceeds ±10pp from EXPECTED.

## Priority Order
1. **Living End 5%** — most broken, investigate cascade/cycling regression
2. **Affinity 93%** — needs more interaction from opponents (artifact removal, blocking)
3. **ETron 72%** — Chalice too effective, or opponents can't handle big creatures
4. After those three stabilize, sweep remaining decks

## Diagnosis Hints

### Living End at 5%
Likely causes:
- Cascade trigger broken (check oracle detection: `'sacrifices all creatures' + 'puts all creature cards'`)
- Cycling not filling GY fast enough (check cycling EV boost constants)
- Living End resolves but creatures die immediately (check SBA timing)
- Opponents kill too fast before T3 cascade (check blocking, may need to slow aggro)
Trace: `python3 run_meta.py --verbose "Living End" "Boros Energy" -s 60000`

### Affinity at 93%
Likely causes:
- Construct Token 10-18 P/T is correct but opponents lack answers
- Wrath of the Skies post-board not drawn/cast often enough
- Other decks need artifact removal in SB (Force of Vigor, Boseiju)
- Cranial Plating equip is instant-speed in real MTG, check if too free
Trace: `python3 run_meta.py --verbose "Affinity" "Boros Energy" -s 60000`

### ETron at 72%
Likely causes:  
- Chalice of the Void locks out too many decks (check if Chalice static effect fires)
- Map → Tron assembly too consistent after fix
- Opponents board poorly vs ETron
Trace: `python3 run_meta.py --verbose "Eldrazi Tron" "Domain Zoo" -s 60000`

## Safety Rules
- **Never force-push** (`git push --force` wipes user's commits)
- **Never rewrite build_*.py or run_meta.py** (infrastructure)
- **Never change deck lists** in `decks/modern_meta.py` (user controls these)
- **Never change CLAUDE.md** (project spec)
- **Max 20 lines changed per commit**
- **Always retest before committing**
- **If unsure, skip and move to next outlier**
