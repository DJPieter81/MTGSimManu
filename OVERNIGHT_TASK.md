# OVERNIGHT_TASK.md — Claude Code Iterate Loop

## Goal
Run small targeted fixes to bring deck win rates closer to expected Modern metagame ranges. Each fix must be tiny, testable, and independently committable.

## Setup (run once)
```bash
cd /home/user/MTGSimManu  # or wherever repo lives
git pull origin main
python merge_db.py
python iterate.py --check --n 5    # baseline check
```

## Loop (repeat until no HIGH outliers remain)

### Step 1: Identify worst outlier
```bash
python iterate.py --check --n 5
```
Read the outlier analysis. Pick the **single worst outlier** (highest gap).

### Step 2: Diagnose root cause
For the chosen outlier, run a verbose trace:
```bash
python run_meta.py --verbose "DeckA" "DeckB" -s 90000
```
Look for:
- **Deck too strong**: Cards that shouldn't work that way, missing opponent interaction, broken combo
- **Deck too weak**: Key cards not being cast, bad mulligan, combo not firing, missing card effects

### Step 3: Make ONE small fix
Rules:
- Change **at most 10 lines** of code
- Touch **at most 1 file**
- NO new card effects (those are session work)
- OK to change: EV scores, thresholds, SB rules, mulligan weights, tag assignments, blocking logic
- NEVER hardcode card names — use tags, oracle text patterns, or CMC/power thresholds

Common fix patterns:
```python
# SB: add board-in/out rule
if any(w in opp_lower for w in ["newdeck"]):
    if any(w in card_lower for w in ["hate_card"]):
        board_in_priority.append((card_name, count, 8))

# EV: adjust spell scoring threshold
if 'removal' in tags and snap.opp_creature_count >= 3:
    ev += snap.opp_creature_count * 2.0  # boost wrath vs wide boards

# Blocking: adjust threshold
if biggest_attacker_power >= 4:  # was >= me.life // 2
    emergency = True

# Mulligan: adjust combo set
# In decks/gameplans/deckname.json, adjust mulligan_combo_sets
```

### Step 4: Test the fix
```bash
python iterate.py --matchup "DeckA" "DeckB" --n 10
```
Compare to the expected range in iterate.py EXPECTED_H2H dict.

**Accept if**: WR moved ≥5pp toward expected range AND no other matchup regressed >10pp
**Reject if**: WR moved away from expected or caused regression

### Step 5: Commit or revert
```bash
# If accept:
git add <changed_file>
git commit -m "Fix: <one-line description>

<what was wrong>
<what was changed>  
<before→after WR for the target matchup>"
git push origin main

# If reject:
git checkout -- <changed_file>
```

### Step 6: Re-check field
```bash
python iterate.py --check --n 5
```
Loop back to Step 1.

## Priority fix list (ordered by expected impact)

### P0: Affinity too strong (85% → target 50-60%)
- **Root cause**: Construct Token 10-18 P/T with no counterplay
- **Fix areas**: 
  - `ai/ev_player.py` _score_spell: boost Wrath EV when opponent has 3+ creatures
  - `ai/board_eval.py` _eval_block: increase life_value scaling for big attackers
  - `engine/sideboard_manager.py`: ensure all creature decks board in sweepers vs Affinity

### P1: Blocking underused (all matchups)
- **Root cause**: Normal (non-emergency) blocks rarely fire because `_eval_block` returns <0
- **Fix area**: `ai/board_eval.py` _eval_block — increase damage_prevented weight
- **Test**: Check combat logs for "does not block" frequency

### P2: Storm kill speed (T5.5 → target T4)
- **Root cause**: AI casts rituals individually across turns instead of saving for one burst
- **Fix area**: `ai/ev_player.py` storm patience cantrip gate
- **Test**: `python iterate.py --matchup "Ruby Storm" "Amulet Titan" --n 10`

### P3: Goryo's field WR (25% → target 35%)  
- **Root cause**: Reanimate happens T5+ instead of T2-3
- **Fix area**: `ai/ev_player.py` reanimate_override EV, mulligan priority
- **Test**: `python iterate.py --matchup "Goryo's Vengeance" "Living End" --n 10`

### P4: Control decks too weak (all 3 control variants)
- **Root cause**: No win conditions implemented (Colonnade, Jace, Teferi ult)
- **Skip for now** — needs full card effect implementations (session work)

## Important constraints
- **Never force-push** — wipes user's GitHub commits
- **Always `git pull --rebase origin main` before pushing**
- **Run `python -m pytest tests/ -q` after engine changes** (if tests exist)
- **Keep iterate_log.json** — it tracks progress across iterations
- **Stop after 20 iterations** or when no HIGH outliers remain
- **Each iteration should take <5 minutes** (n=5 tests are fast)

## Files you can change
| File | What to change | What NOT to change |
|------|---------------|-------------------|
| `ai/ev_player.py` | EV scores, thresholds, attack/block logic | Function signatures, class structure |
| `ai/board_eval.py` | Block/attack evaluation weights | Action types |
| `ai/mulligan.py` | Mulligan thresholds, hand scoring | Mulligan interface |
| `engine/sideboard_manager.py` | Board-in/out rules, swap limits | Swap loop mechanics (just fixed) |
| `ai/strategy_profile.py` | Profile thresholds per archetype | Profile structure |
| `decks/gameplans/*.json` | Mulligan keys, combo sets, goal sequences | File format |

## Files you must NOT change
- `engine/game_state.py` — core engine, too risky for overnight
- `engine/card_effects.py` — card implementations need human review
- `engine/cards.py` — data model
- `engine/combat_manager.py` — combat rules
- `build_*.py` — output tools
- `run_meta.py` — CLI interface (except bugs)
