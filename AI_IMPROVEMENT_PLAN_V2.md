# MTGSimManu — AI Improvement Plan v2

> **Session type:** Planning + implementation. Read this entire file before writing code.
> **Previous plan:** `AI_STRATEGY_IMPROVEMENT_PLAN.md` — superseded. That plan addressed symptoms. This one addresses the architecture.
> **Pre-session:** `git pull origin main && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`

---

## Diagnosis: what is actually wrong

The audit data tells a clear story:

| Card | Win% when cast | Delta | Signal |
|---|---|---|---|
| Phlage | 84% | +0.67 | Best card — immediate effect, high EV |
| Guide of Souls | 81% | +0.48 | ETB effect + body |
| Goblin Bombardment | 42% | **−0.57** | Worst card — no immediate effect |
| Orim's Chant | 42% | −0.27 | Wrong timing |
| Thraben Charm | 62% | −0.10 | Late cast, correct card |

The pattern is not random bugs. It is a **single architectural failure** repeated in different forms:

> **The EV model is stateless with respect to time.**

`_score_spell()` computes: `position_value(after_cast) − position_value(now)`.

This is correct for spells with immediate effect. It is wrong for spells whose value is *conditional on future turns happening*. Goblin Bombardment scores positively because it adds a permanent to the board, increasing `my_creature_count` and implicitly raising `position_value`. But if the opponent kills you on T5 and Bombardment needs activations over T5-T8, the model never sees the zero.

Everything else — the wrong removal targets, the held Thraben Charm, the idle Cat Token — is a downstream consequence of this same issue. The AI doesn't know what "now" costs relative to "next turn."

---

## The three things to fix

### 1. Urgency-weighted EV (central fix — everything else follows from this)

**Where:** `ai/ev_evaluator.py` → `_project_spell()` and `EVSnapshot`

**What:** Add an `urgency_factor` property to `EVSnapshot` — already has `opp_clock`. This is a one-liner:

```python
@property
def urgency_factor(self) -> float:
    """Fraction of future turns we actually get. 1.0 = no urgency, 0.0 = dying now.
    Derived entirely from existing opp_clock — no new data needed."""
    return min(1.0, max(0.0, (self.opp_clock - 1) / 4.0))
```

Then in `_project_spell()`, detect whether the spell has *immediate* vs *deferred* effect using oracle text — no card names — and apply the discount:

```python
def _spell_generates_value_now(card) -> bool:
    """Does this spell do something useful on the turn it resolves?
    Oracle-driven detection. No card names."""
    oracle = (card.template.oracle_text or '').lower()
    tags = getattr(card.template, 'tags', set())
    # Creatures: attack next turn, body matters immediately
    if card.template.is_creature: return True
    # Instants/sorceries with direct effects
    if 'removal' in tags or 'board_wipe' in tags: return True
    if 'draw' in tags or 'cantrip' in tags: return True
    # ETB effects on non-creatures (enchantments/artifacts with enters triggers)
    if 'enters' in oracle and any(w in oracle for w in ('deal', 'gain', 'exile', 'create', 'draw')): return True
    # Activated abilities requiring future turns: "tap:", "sacrifice a creature:",
    # "pay {X}:" with no draw/damage in same clause
    if 'sacrifice a creature' in oracle and 'damage' in oracle: return False  # Bombardment
    if '{t}:' in oracle and 'add' not in oracle: return False  # tap abilities without mana
    return True  # default: assume immediate

# In _project_spell, after projecting position value delta:
if not _spell_generates_value_now(card):
    ev *= snap.urgency_factor  # discount deferred-value spells by kill proximity
```

**Effect:** Goblin Bombardment at opp_clock=2 gets multiplied by `(2-1)/4 = 0.25` — EV drops to near zero. Thraben Charm (immediate -1/-1 effect) is unaffected. This is the same formula applied generically across all 21k cards.

**Verify:**
```bash
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "Goblin Bombardment\|Thraben Charm\|Cast.*T[34]"
# Thraben Charm should appear T3-4, Bombardment should be skipped or appear late
python run_meta.py --matchup energy dimir -n 20 2>&1 | tail -3
# Bombardment should still fire vs slower opponents (opp_clock=6+)
```

---

### 2. Threat-prevention value for removal (oracle-driven, replaces power threshold)

**Where:** `ai/ev_player.py` → reactive_only gate (`_execute_main_phase`) and `_score_spell`

**What:** The `has_big_target` check uses `power >= 4`. This misses:
- Battle cry sources (0 power, ongoing damage amplification)  
- Unattached equipment about to equip (0 power, but +14 when attached)
- Any scaling threat

Replace with a `_threat_score(creature, game)` function that is entirely oracle-driven:

```python
def _threat_score(self, c, game) -> float:
    """Oracle-driven threat value for removal targeting.
    
    Returns a float on the same scale as creature_value().
    No card names. No hardcoded P/T thresholds.
    """
    val = creature_value(c)
    oracle = (c.template.oracle_text or '').lower()
    cname = (c.template.name or '').lower().split(' //')[0].strip()
    
    # Ongoing damage multipliers: anything that pumps OTHER attackers
    if 'whenever this creature attacks' in oracle: val += 8.0
    if cname and f'whenever {cname} attacks' in oracle: val += 8.0
    
    # Scaling threats: grow every turn
    if re.search(r'for each (artifact|creature|land|card)', oracle): val += 6.0
    
    # High raw power: still matters even without triggers
    val += max(0, (c.power or 0) - 3) * 0.8
    
    return val
```

Replace `has_big_target` with:
```python
has_high_threat = ('removal' in tags and
                   any(self._threat_score(c, game) >= prof.big_creature_power
                       for c in opp.creatures
                       if (get_burn_damage(spell.template.name) <= 0 or
                           get_burn_damage(spell.template.name) >= (c.toughness or 0))))
```

Also update `_pick_best_removal_target` and the burn targeting loop to use `_threat_score` instead of `creature_value`.

**Verify:**
```bash
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "Galvanic Discharge deals.*Signal\|T2 P1.*Galvanic"
# GD should kill Signal Pest T2 in G2 — confirmed by threat_score(SP) > 8.0 (battle cry)
python run_meta.py --matchup energy prowess -n 10 2>&1 | grep "Galvanic Discharge"
# GD should still target Monastery Swiftspear (prowess = scaling threat)
```

---

### 3. Fetch/shock life as a resource

**Where:** `ai/mana_planner.py` → `choose_fetch_target()`

**What:** When multiple fetches exist, cracking them all in one turn bleeds unnecessary life. The fix is not a life threshold — it's derived from the cost/benefit of the mana now vs the life cost:

```python
def fetch_crack_urgency(me, needed_mana_colors, snap) -> bool:
    """Is cracking this fetch for a shock land worth the life now?
    Returns False when the life cost outweighs the mana benefit.
    """
    # If mana is available from tapped lands already, defer
    tapped_land_mana = sum(1 for l in me.lands if l.tapped and not l.is_basic)
    if tapped_land_mana >= len(needed_mana_colors): return False
    
    # Don't double-shock in same turn if already paid shock life this turn
    shocks_this_turn = getattr(me, 'shocks_paid_this_turn', 0)
    if shocks_this_turn >= 1:
        # Only crack again if the mana is truly needed for a spell this turn
        castable_cost = min((c.template.cmc or 0) for c in me.hand
                            if not c.template.is_land) if me.hand else 99
        if castable_cost > len(me.lands) + 1: return False
    
    # Don't compound shock damage when life is already low relative to opp clock
    if snap.urgency_factor < 0.5 and shocks_this_turn >= 1: return False
    
    return True
```

Track `shocks_paid_this_turn` in `PlayerState` (reset on untap step alongside other turn trackers).

**Important:** This is the lowest-priority fix. The first two have larger impact and this one has more risk of breaking mana consistency in other decks. Do it last.

**Verify:**
```bash
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "pay.*life\|Boros Energy.*life" | head -6
# Should not be at 16 or below before T2 combat
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
# WR should not regress — Zoo also needs good mana
```

---

## What NOT to do

Every fix must pass all of these. Reject implementations that fail any.

- **No card names in engine or AI logic.** `if card.name == 'Goblin Bombardment'` → rejected. Use oracle detection.
- **No `opp_deck_name` checks.** `if 'affinity' in opp_deck_name` → rejected. Derive from board state.
- **No hardcoded power/toughness thresholds as magic numbers.** `if power >= 4` → replace with oracle-derived threat scoring.
- **No matchup-specific if/else.** The same code path must handle Affinity, Storm, and Control identically — the EV numbers differentiate them, not branches.
- **EVSnapshot is the data contract.** New signals must be computed properties on EVSnapshot, not passed separately through the call chain.
- **Verify with 4+ matchups.** Not just the bug matchup.

---

## Order of implementation

```
Step 1: urgency_factor property on EVSnapshot          (5 min, 3 lines)
Step 2: _spell_generates_value_now() oracle detection  (20 min)
Step 3: Apply discount in _project_spell()             (5 min)
    → VERIFY: Bombardment T4 vs Affinity gone; still fires vs Dimir
Step 4: _threat_score() function                       (20 min)  
Step 5: Replace has_big_target with has_high_threat    (10 min)
Step 6: Update _pick_best_removal_target               (5 min)
    → VERIFY: GD kills SP T2 in G2; Thraben Charm cast T3-4 in G1
Step 7: Fetch urgency check                            (30 min)
    → VERIFY: no double-shock T1; no regression in Zoo, Prowess
```

After all steps:
```bash
python run_meta.py --matchup energy affinity -n 30 2>&1 | tail -3
python run_meta.py --matchup energy zoo -n 30 2>&1 | tail -3
python run_meta.py --matchup energy prowess -n 20 2>&1 | tail -3
python run_meta.py --matchup storm tron -n 20 2>&1 | tail -3
python run_meta.py --matrix -n 10 --save 2>&1 | tail -5  # smoke test full matrix
python run_meta.py --audit energy -n 60 2>&1 | grep -E "Win rate|Goblin Bombardment|Thraben Charm"
# Target: Bombardment win% > 55%, Thraben Charm avg cast turn < T4.5
```

---

## What this does NOT fix (for future sessions)

**Multi-turn lookahead.** Thraben Charm should be cast T4 not because of urgency discounting but because "if I don't cast it now, opponent equips CP next turn and the board is unrecoverable." This requires modelling the opponent's *next* action given the current board — a larger architectural change. The urgency discount gets us closer but doesn't fully solve sequencing.

**Mulligan hand quality vs matchup.** G1 hand had Bombardment + Pyromancer — both slow vs Affinity's T4 kill. The keep/mull logic doesn't ask "does this hand interact before T4?" It only checks land count and key card presence. Improving mulligan for aggro-vs-aggro requires adding "interaction quality" to the hand evaluator.

Both are documented in `MODERN_PROPOSAL.md` for a future planning session.

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `ai/ev_evaluator.py`, `ai/ev_player.py`, `ai/strategy_profile.py`, `ai/mana_planner.py`
- [ ] Step 1-3: urgency_factor + immediate-effect detection + discount
- [ ] Verify Step 1-3 with `--bo3 energy affinity -s 60100` and `--matchup energy dimir -n 20`
- [ ] Step 4-6: _threat_score + removal gate replacement
- [ ] Verify Step 4-6 with GD targeting check
- [ ] Step 7: fetch urgency (lowest priority — skip if time-constrained)
- [ ] Full regression 4 matchups
- [ ] `python run_meta.py --audit energy -n 60` — check Bombardment delta improved
- [ ] Update `PROJECT_STATUS.md` grade + fix table
- [ ] Rebuild replay: `python run_meta.py --bo3 energy affinity -s 60100 > replays/boros_vs_affinity_s60100.txt && python build_replay.py replays/boros_vs_affinity_s60100.txt replays/replay_boros_vs_affinity_s60100.html 60100`
- [ ] Commit + push
