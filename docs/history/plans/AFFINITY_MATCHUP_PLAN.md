# MTGSimManu — Affinity Matchup Plan

> **Session type:** Targeted matchup analysis + fixes
> **Focus:** Affinity vs Boros Energy (90%) and Affinity vs Domain Zoo (97%)
> **Current grade:** C+  
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`

---

## Context from audit data

### Affinity (92.5% field WR — P0 inflated)

| Card | WinCR | Delta | Signal |
|---|---|---|---|
| Memnite | 94% | +0.43 | Core enabler — appears in wins |
| Frogmite | 100% | +0.41 | 100% win rate when cast — underplayed (0.4×/game) |
| Thought Monitor | 94% | +0.43 | Card advantage payoff |
| Signal Pest | 87% | **−0.30** | Appears more in losses — either cast in wrong spots or dying early |
| Springleaf Drum | 86% | **−0.39** | Same pattern as SP — enabler overcast in losing games |
| EE | 64% | **−0.54** | Misused: should be reactive silver bullet, not proactive |

Affinity's 90-97% WRs against Boros and Zoo are not realistic. In tournament play, Boros wins ~40-45% and Zoo wins ~35-40% against Affinity. The gap points to these opponents having broken AI — but Affinity's own decisions also have issues (see below).

### Domain Zoo (63.5% field — reasonable except Affinity)

| Observation | Detail |
|---|---|
| vs Affinity: 3% | Zoo never beats Affinity — this is too low even for a bad matchup |
| Ragavan delta: −0.09 | Gets removed 12× in 60 games — opponent removal always on Ragavan |
| Consign to Memory delta: −0.11 | Negative — being cast wrong or too late |
| Teferi delta: −0.07 | Negative in Zoo — likely cast against wrong matchups |
| EE delta: −0.54 (Affinity side) | EE cast proactively, not held for value |

---

## Root cause diagnosis

### Why Affinity wins at 90-97% (should be ~55-65%)

**1. Zoo and Boros don't correctly prioritise removing Cranial Plating.**
CP is the single most dangerous card — it turns a 0/2 Ornithopter into a 14/2 that ends the game in one hit. The opponents have Wear//Tear (Boros SB) and Leyline Binding, Boseiju (Zoo) that can hit it. Their `_permanent_threat_value` function doesn't rate unattached CP highly enough — it scores equipment by CMC (2), not by the board-state-dependent scaling (+1/+0 per artifact).

**2. Zoo doesn't attack into Affinity's early board.**
Ragavan + Scion of Draco on T2-3 should be pressuring Affinity before it assembles. Zoo's combat AI is holding back incorrectly when Affinity has small creatures that can't profitably block its large domain creatures.

**3. Affinity's EE is cast as a proactive curve play, not a reactive answer.**
EE should be held and popped on the opponent's combo/creature turn. The audit shows EE in losses at 0.67× vs 0.13× in wins — it's being cast proactively in losing games.

**4. Affinity equips CP to wrong creature in some situations.**
When Ornithopter is available (flying evasion), CP should go there over a ground creature. Current `_consider_equip` logic evaluates by power, not evasion + power combined.

---

## The three fixes

### Fix 1 — Equipment threat value in opponent targeting (Boros, Zoo, all decks)

**Files:** `ai/ev_player.py` → `_permanent_threat_value()`, `_choose_targets()`, `_pick_best_removal_target()`

**What:** `_permanent_threat_value` currently scores equipment by CMC. Unattached Cranial Plating (CMC 2) scores ~2. But CP attached to a creature adds `artifact_count` power — typically 8-14 points. Even unattached, CP-about-to-equip is the highest-priority target on the board.

The fix is oracle-driven: detect `+N/+0 for each artifact` or `+N/+N for each artifact` patterns and score them by the artifact count on the opponent's board, not by CMC.

```python
def _permanent_threat_value(self, perm, opp) -> float:
    """Oracle-driven threat value for nonland permanents.
    Equipment is valued by the power it adds, not its CMC.
    """
    from engine.cards import CardType
    t = perm.template
    oracle = (t.oracle_text or '').lower()
    tags = getattr(t, 'tags', set())
    
    base = (t.cmc or 0) + (perm.power or 0)
    
    # Equipment/enchantments that scale with artifacts — value = current artifact count
    if re.search(r'\+\w+/\+\w+\s+for each (artifact|creature|land)', oracle):
        artifact_count = sum(1 for c in opp.battlefield
                            if CardType.ARTIFACT in c.template.card_types)
        base = artifact_count + 2  # even unattached: N+2 represents equip threat
    
    # Planeswalkers
    if CardType.PLANESWALKER in t.card_types:
        base += 5 + (getattr(perm, 'loyalty_counters', 0) or 0)
    
    return base
```

This is generic — works for Cranial Plating, Nettlecyst, Sword of the Meek, any future scaling equipment.

**Verify:**
```bash
python run_meta.py --bo3 zoo affinity -s 60200 2>&1 | grep "Wear.*Tear\|Boseiju\|Cranial Plating"
# Expected: Zoo targets CP with Boseiju/binding before it equips, not after
python run_meta.py --bo3 energy affinity -s 60200 2>&1 | grep "Wear.*Tear\|Cranial Plating"
# Expected: Boros SB Wear//Tear targets CP in G2/G3
```

---

### Fix 2 — EE reactive hold (Affinity)

**Files:** `decks/gameplans/affinity.json` → `reactive_only` list, `ai/ev_player.py` urgency gate

**What:** EE shows up heavily in losses (0.67×) and barely in wins (0.13×). It's being cast proactively when there's no target. EE should be in `reactive_only` — held until opponent casts something worth hitting (X=CMC of target).

Add EE to Affinity's `reactive_only` in the gameplan, and add an override: release EE from reactive_only when `opp.life <= X+2` (use it as reach) OR when opponent has assembled a combo/key permanent worth hitting.

The release condition must be oracle-driven: EE exits reactive_only when opponent has a permanent with CMC matching X and high `_permanent_threat_value`.

**Verify:**
```bash
python run_meta.py --audit affinity -n 60 2>&1 | grep "Engineered Explosives"
# Target: EE WinCR > 70%, delta > 0 (currently 64%, −0.54)
```

---

### Fix 3 — CP equip target: prefer evasion

**Files:** `ai/ev_player.py` → `_consider_equip()`

**What:** When choosing what to equip CP to, the AI should strongly prefer flying/menace/trample creatures over ground creatures of the same or higher power. A 6/2 flying Ornithopter with CP is typically unblockable; a 6/6 Construct Token on the ground gets chump blocked.

```python
def _equip_target_score(self, creature, equip_card) -> float:
    """Score a creature as an equip target.
    Evasion multiplies the value since unblockable damage compounds.
    """
    from engine.cards import Keyword
    base = (creature.power or 0) + (creature.toughness or 0) * 0.3
    # Evasion multiplier — unblocked damage is worth 2× ground damage
    evasion_bonus = 1.0
    if Keyword.FLYING in creature.keywords: evasion_bonus = 2.0
    elif Keyword.MENACE in creature.keywords: evasion_bonus = 1.5
    elif Keyword.TRAMPLE in creature.keywords: evasion_bonus = 1.3
    return base * evasion_bonus
```

**Verify:**
```bash
python run_meta.py --bo3 affinity energy -s 60200 2>&1 | grep "Equip Cranial Plating"
# Expected: CP goes to Ornithopter when available, not to ground creature
```

---

## Implementation order

```
Fix 1: _permanent_threat_value scaling equipment    (~25 min)
    → verify Boros/Zoo target CP before equip
Fix 2: EE reactive_only in Affinity gameplan         (~15 min)
    → verify EE audit delta improves
Fix 3: CP equip target evasion preference            (~20 min)
    → verify CP on Ornithopter in replays
    
Full regression:
python run_meta.py --matchup affinity energy -n 30
python run_meta.py --matchup affinity zoo -n 30
python run_meta.py --matchup affinity prowess -n 20
python run_meta.py --matchup affinity dimir -n 20
```

Target outcomes:
- Affinity vs Boros: ~85% → ~65-70% (Boros now correctly targets CP)
- Affinity vs Zoo: ~97% → ~60-70% (Zoo removes CP before it equips)
- Affinity audit: EE delta positive, Signal Pest delta neutral

---

## What NOT to do

- No card names. Detect CP via oracle `+N/+0 for each artifact`, not `if card.name == 'Cranial Plating'`
- No matchup-specific branches. `_permanent_threat_value` must work for all 21k cards
- Don't hardcode `if deck == 'Affinity': add_EE_to_reactive`. Add to `affinity.json` gameplan data file
- Don't reduce Affinity's speed artificially — the goal is correct opponent play, not nerfing Affinity

---

## Robustness checklist

Before each fix:
- [ ] No card names in logic
- [ ] No power/toughness magic numbers
- [ ] No matchup/deck-name branches
- [ ] EVSnapshot is the data contract for new signals
- [ ] Verify with 4+ matchup pairs (not just Affinity matchups)

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `ai/ev_player.py` (`_permanent_threat_value`, `_consider_equip`), `decks/gameplans/affinity.json`
- [ ] Fix 1: scaling equipment threat value
- [ ] Verify Fix 1 — Boros/Zoo target CP
- [ ] Fix 2: EE reactive in affinity gameplan
- [ ] Verify Fix 2 — EE audit improves
- [ ] Fix 3: CP equip evasion preference
- [ ] Verify Fix 3 — CP on Ornithopter
- [ ] Full regression 4 matchups
- [ ] `python run_meta.py --audit affinity -n 60` — confirm EE delta flips positive
- [ ] `python run_meta.py --matchup affinity energy -n 30` — confirm WR moves toward ~65-70%
- [ ] Update `PROJECT_STATUS.md`
- [ ] Rebuild replays for both matchups
- [ ] Commit + push
