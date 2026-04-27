# MTGSimManu — AI Strategy Improvement Plan

> **Purpose:** Planning-mode document for Claude Code. Read this before any implementation.
> **Context:** Findings from 10 LLM judges + deep replay analysis (Boros Energy vs Affinity, seed 60100).
> **Date:** 2026-04-13
> **Grade at start of this work:** C

---

## How to use this document (Claude Code instructions)

1. **Read this entire file before writing a single line of code.**
2. For each task, read the referenced source files first.
3. Every fix must satisfy the **generic robustness checklist** in Section 2.
4. After each task, run the verification commands in Section 5.
5. Update `PROJECT_STATUS.md` with fix status and commit hash after each task group.
6. Commit atomically per task group — do not bundle unrelated changes.

---

## 1. Root cause taxonomy

The replay analysis and judge panel identified three distinct failure categories. Each has a different fix location and must be treated separately.

### Category A — Missing forward-looking threat evaluation

**Symptom:** AI casts slow do-nothing permanents (Goblin Bombardment T4) into fast-kill matchups. Thraben Charm held two turns while the board deteriorates.

**Root cause:** `_score_spell()` in `ev_player.py` computes *current-state* EV. It does not model "if opponent kills me on T5, a permanent that generates value over multiple turns is worth ~0 of that future value."

**NOT a matchup-specific fix.** The correct abstraction is: every spell's EV should be discounted by `min(1.0, turns_until_opponent_wins / turns_needed_to_generate_value)`. This is derived entirely from data already in `EVSnapshot` (`opp_clock`, `my_clock`, `opp_power`).

### Category B — Removal target priority ignores ongoing damage amplification

**Symptom:** GD not used on Signal Pest T2 despite having it in hand. `has_big_target` gate requires `power >= 4` — SP has 0 power. The battle cry premium added to `_choose_targets` helps when GD is used, but the `_reactive_only` gate prevents it from being used at all.

**Root cause:** The `has_big_target` check in the reactive-only override uses raw power as a proxy for threat level. This is wrong for:
- Battle cry sources (0 power, massive ongoing damage amplification)
- Unattached equipment about to be equipped (0 power on the equipment itself)
- Any 0-power creature with a triggered ability

**The fix must be oracle-driven and generic**, not a power threshold.

### Category C — Fetchland shock sequencing ignores life as a resource

**Symptom:** Boros paid 6 life in fetchland/shock costs by T2, before any combat. Against a T4-5 kill deck, 6 life ≈ 1.5 combat steps.

**Root cause:** `mana_planner.py` chooses fetch targets optimally for colour but does not model "cracking multiple shocks in the same turn raises opponent kill clock by N% — is that worth it vs playing a tapped land?"

**NOT a hard-coded life threshold fix.** The fix is: when multiple fetches are available, prefer to crack one per turn, and prefer non-shocking alternatives when the mana isn't needed immediately.

---

## 2. Generic robustness checklist (apply to EVERY fix)

Before implementing anything, answer these questions. If any answer is "no", redesign.

- [ ] **No card names hardcoded.** Detection must use oracle text, template fields, or keyword enums.
- [ ] **No power/toughness thresholds as magic numbers.** Use oracle-detected flags or relative comparisons.
- [ ] **No matchup-specific branching.** The fix must work for Affinity, Ruby Storm, Eldrazi Tron, Living End, and any future deck equally.
- [ ] **No archetype-specific branching** unless gated behind the existing `self.archetype` field.
- [ ] **EVSnapshot is the data contract.** New signals must be added to `EVSnapshot` as computed fields, not passed as parameters through the call chain.
- [ ] **Oracle detection follows the established pattern.** Use `(c.template.oracle_text or '').lower()` — never regex against card names.
- [ ] **Verify with 4+ matchup pairs**, not just the matchup that surfaced the bug.

---

## 3. Task breakdown

### Task Group 1 — Threat-prevention value for removal (Category B)

**Files:** `ai/ev_player.py`
**Functions:** `_execute_main_phase()` reactive-only gate, `_best_removal_target_value()`

#### The problem in code

```python
# ev_player.py ~line 273 — current gate
has_big_target = ('removal' in tags and
                  any((c.power or 0) >= prof.big_creature_power   # ← hardcoded threshold
                      for c in opp.creatures))
```

`prof.big_creature_power` defaults to 4. Signal Pest (0/1 battle cry) never passes this gate. The card sits in `_reactive_only` and never gets proactively cast.

#### Required fix

Replace the power threshold with a **threat-value function** that is oracle-driven:

```python
def _has_high_threat_target(self, game, spell) -> bool:
    """True if a removal spell has a high-value target worth proactively casting for.
    
    Threat value is oracle-driven — NOT based on raw power/toughness.
    Catches: battle cry sources, scaling threats, key enablers.
    """
    opp = game.players[1 - self.player_idx]
    tags = getattr(spell.template, 'tags', set())
    if 'removal' not in tags:
        return False
    
    from decks.card_knowledge_loader import get_burn_damage
    dmg = get_burn_damage(spell.template.name)
    
    for c in opp.creatures:
        remaining_toughness = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
        if dmg > 0 and dmg < remaining_toughness:
            continue  # can't kill it
        
        val = creature_value(c)
        oracle = (c.template.oracle_text or '').lower()
        cname = (c.template.name or '').lower().split(' //')[0].strip()
        
        # Oracle-detected threat premiums (no card names)
        if 'whenever this creature attacks' in oracle:
            val += 8.0   # battle cry, Ragavan-type triggers — ongoing damage multiplier
        if cname and f'whenever {cname} attacks' in oracle:
            val += 8.0
        if 'for each artifact' in oracle or 'for each creature' in oracle:
            val += 6.0   # scaling threats grow every turn
        if (c.power or 0) >= 4:
            val += (c.power or 0) * 0.5  # large threats still matter
        
        if val >= prof.big_creature_power:  # reuse threshold as EV floor, not power floor
            return True
    
    return False
```

Then replace `has_big_target` usage with `self._has_high_threat_target(game, spell)`.

**Also update `_pick_best_removal_target`** (currently just `max(creatures, key=creature_value)`) to use the same oracle-based scoring so the *chosen* target is the highest-threat one, not just the highest clock-value one.

#### Verification
```bash
# GD should kill Signal Pest on T2 in G2 of seed 60100
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "Galvanic Discharge deals\|T2 P1\|T1 P1"
# Expected: GD fires T2 on SP (not held until T4)

# GD should NOT proactively fire against non-threatening 1/1 vanilla creatures
python run_meta.py --matchup energy zoo -n 10 2>&1 | grep "Galvanic Discharge deals"
# Expected: fires on threats, not on 1/1 tokens when face damage is better
```

---

### Task Group 2 — Kill-clock discount on slow permanents (Category A)

**Files:** `ai/ev_evaluator.py`, `ai/ev_player.py`, `ai/clock.py`
**Functions:** `estimate_spell_ev()`, `_project_spell()`, `EVSnapshot`

#### The problem in code

`estimate_spell_ev()` computes `evaluate(after) - evaluate(before)`. This correctly captures the immediate board impact. It does NOT capture: "if I die on turn 5, a permanent that needs 3 turns to generate value is only worth 2/3 of its steady-state value."

`clock.py` already computes `opp_clock` (turns until opponent wins). This signal exists but is not fed back into spell value discounting.

#### Required fix

Add a `urgency_discount` factor to `EVSnapshot` and apply it in `_project_spell`:

```python
# In EVSnapshot (ev_evaluator.py)
@dataclass  
class EVSnapshot:
    # ... existing fields ...
    opp_clock: float = 999.0      # already exists
    my_clock: float = 999.0       # already exists
    
    @property
    def urgency_factor(self) -> float:
        """How urgent is the game state?
        
        Returns 0.0 (instant death) to 1.0 (no urgency).
        Used to discount slow-value permanents in fast matchups.
        """
        if self.opp_clock <= 0:
            return 0.0
        # Discount when opponent kills in <= 3 turns
        return min(1.0, self.opp_clock / 4.0)
```

Then in `_project_spell()`, apply the discount to permanents with no immediate effect:

```python
def _has_immediate_effect(card) -> bool:
    """True if the spell generates value on the turn it resolves.
    
    Oracle-driven. No card names.
    """
    oracle = (card.template.oracle_text or '').lower()
    tags = getattr(card.template, 'tags', set())
    
    if card.template.is_creature:
        return True  # creatures attack immediately next turn
    if 'removal' in tags or 'board_wipe' in tags:
        return True  # removes a threat now
    if 'draw' in tags or 'cantrip' in tags:
        return True  # card advantage now
    if 'enters' in oracle and ('deal' in oracle or 'gain' in oracle or 'exile' in oracle):
        return True  # ETB effect now
    # Enchantments/artifacts that only generate value through activations
    if 'sacrifice' in oracle and ('deal' in oracle or 'damage' in oracle):
        return False  # Goblin Bombardment — needs future activations
    if 'tap' in oracle and 'add' not in oracle and 'draw' not in oracle:
        return False  # tap-activated abilities without mana production
    return True  # default: assume some immediate value
```

Apply in `_project_spell`:
```python
if not _has_immediate_effect(card):
    snap = snap._replace(...)
    after_val *= snap.urgency_factor   # discount by kill-clock proximity
```

#### Critical constraint
`urgency_factor` must be derived solely from `EVSnapshot.opp_clock` — which is computed from oracle-detected power and keywords, not from deck names. This means it automatically adjusts for any fast aggro/combo opponent.

#### Verification
```bash
# Goblin Bombardment should NOT be cast T4 when opponent has lethal board in 1 turn
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "Goblin Bombardment\|Thraben Charm" | head -10
# Expected: Thraben Charm cast earlier, Bombardment delayed or not cast

# Goblin Bombardment SHOULD be cast in a slower matchup where it has time to generate value
python run_meta.py --bo3 energy dimir -s 60100 2>&1 | grep "Goblin Bombardment"
# Expected: Bombardment cast at reasonable timing vs slower opponent
```

---

### Task Group 3 — Fetch/shock life management (Category C)

**Files:** `ai/mana_planner.py`
**Functions:** `choose_fetch_target()`, `analyze_mana_needs()`

#### The problem in code

`choose_fetch_target()` picks the optimal fetch target for colour coverage but does not model life as a resource. Currently two fetches can be cracked on the same turn for untapped duals, costing 2 fetch life + 2×2 shock life = 6 life before a single spell resolves.

#### Required fix

Add a `crack_urgency` check: only crack a fetch for an untapped shock if the mana is actually needed this turn. Otherwise, defer to crack next turn (or crack for a tapped land this turn if speed isn't needed).

```python
def should_crack_fetch_this_turn(game, player_idx, needed_colors) -> bool:
    """Determine if cracking a fetch for a shock is worth the life cost now.
    
    Returns False (defer) when:
    - We already have enough mana from tapped lands + untapped basics
    - We have already paid shock life this turn (diminishing returns)
    - We are below a life threshold where each point matters materially
    
    All thresholds derived from game state, not hardcoded constants.
    """
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]
    
    # How much damage is coming at us this turn?
    incoming = sum(c.power or 0 for c in opp.creatures if not c.tapped)
    
    # How many shocks have we already paid this turn?
    shocks_paid_this_turn = getattr(me, 'shocks_paid_this_turn', 0)
    
    # If we're already low and taking hits, be conservative with shocks
    effective_life = me.life - incoming
    if effective_life <= 6 and shocks_paid_this_turn >= 1:
        return False  # don't pay more shock when already low
    
    # If we already cracked a fetch this turn, defer the second one
    if shocks_paid_this_turn >= 1 and len(me.hand) > 0:
        return False  # stagger fetches across turns
    
    return True
```

**Note:** This requires adding `shocks_paid_this_turn` tracking to `PlayerState` (reset on untap), or detecting it from the game log. Keep it stateless if possible — derive from `me.life` changes this turn.

#### Verification
```bash
# Boros should not pay 6 life in fetch/shock by T2 on the play
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "pay.*life\|life:" | head -10
# Expected: not at 16 or below before T2 combat

# Boros fetch behaviour should not be impaired in normal operation
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
# Expected: WR similar to baseline
```

---

### Task Group 4 — Attack-before-deploy sequencing (Category A)

**Files:** `ai/turn_planner.py`, `ai/ev_player.py`
**Functions:** `TurnPlanner` ordering 5 configurations, `decide_attackers()`

#### The problem in code

`TurnPlanner` evaluates 5 turn orderings including `attack→deploy`. However, the ordering that wins is determined by aggregate EV. When "deploy a creature" has high EV and "attack" has marginal EV (e.g. 1/2 Ajani into a mostly clean board), the `deploy→attack` ordering wins. The Cat Token + Ajani both sitting home T2-T3 is a symptom.

The underlying issue: `_simulate_combat()` in `turn_planner.py` estimates opponent blocks correctly but underweights the **tempo cost of not attacking** — every turn you don't deal damage, the opponent has one more draw step.

#### Required fix

Add a `chip_damage_tempo_bonus` that scales with opponent's draw-step value when the game is close:

```python
# In plan_attack() — already has chip damage bonus:
# delta += result.damage_to_opp * 0.3  ← this exists

# Extend: when opponent has a fast clock, each damage point is worth more
# because it reduces the number of draws they get before dying
if board.opp_life > 0:
    turns_of_draws_removed = result.damage_to_opp / max(board.opp_life, 1)
    # Each opponent draw step has value proportional to their library quality
    # Approximate: 0.5 EV per draw step prevented
    delta += turns_of_draws_removed * 0.5
```

This is **not matchup-specific** — it applies to any game where dealing damage removes opponent draw steps.

#### Verification
```bash
# Cat Token should attack T2 alongside Ajani (Affinity board is only SP 0/1)
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "T2 P1\|T3 P1\|Attack with\|does not attack" | head -10
# Expected: attack with Cat Token + Ajani T3 at minimum

# Should not cause reckless attacks in combo matchups
python run_meta.py --matchup energy storm -n 10 2>&1 | tail -3
# Expected: WR not significantly worse (Storm doesn't block anyway)
```

---

## 4. Implementation order and dependencies

```
Task 1 (threat-prevention removal)   ← no dependencies, do first
    ↓ verify with 4 matchups
Task 2 (kill-clock urgency discount)  ← needs EVSnapshot, independent of Task 1
    ↓ verify Bombardment + Charm timing
Task 4 (attack sequencing bonus)      ← independent, lowest risk
    ↓ verify Cat Token attacks
Task 3 (fetch/shock life management)  ← most invasive, do last
    ↓ full regression on all matchups
```

After all 4 tasks: run full 16×16 matrix at N=30 to validate no regressions.

---

## 5. Regression test suite

Run after EVERY task group before committing:

```bash
# Quick smoke test (4 representative matchups)
python run_meta.py --matchup energy affinity -n 20 2>&1 | tail -3
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
python run_meta.py --matchup storm tron -n 20 2>&1 | tail -3
python run_meta.py --matchup dimir prowess -n 20 2>&1 | tail -3

# No crashes across all decks
python run_meta.py --matrix -n 5 --save 2>&1 | tail -5

# Specific signal checks
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep -E \
  "Galvanic Discharge deals|Thraben Charm|Goblin Bombardment enters|Attack with|does not attack" | head -20
```

Expected signals after all fixes:
- `Galvanic Discharge deals 3 to Signal Pest` — on T2 of G2
- `Thraben Charm` — cast by T4 of G1 (not T6)
- `Goblin Bombardment` — not cast when opponent has T4-5 kill clock
- `Attack with Ajani, Cat Token` — T3 at latest, not Ajani alone

---

## 6. What NOT to do

These are the anti-patterns the judge panel specifically flagged. Reject any implementation that does these.

| Anti-pattern | Why rejected | Generic alternative |
|---|---|---|
| `if card.name == 'Goblin Bombardment': discount` | Hardcoded card name | Detect via oracle: `'sacrifice' in oracle and 'damage' in oracle` |
| `if opponent_deck == 'Affinity': cast GD on SP` | Matchup-specific | Detect SP as threat via oracle battle cry + `_has_high_threat_target()` |
| `if power >= 4: use_removal` | Magic number | Oracle-detected threat value ≥ EV threshold |
| `if opp_clock <= 3: don't cast permanents` | Hardcoded threshold | `urgency_factor = min(1.0, opp_clock / 4.0)` — derived, not constant |
| `if turn <= 2: crack_fetch` | Turn-based heuristic | Derive from mana needed this turn vs life cost |
| `BATTLE_CRY_CARDS = ['Signal Pest', ...]` | Card list | `'whenever this creature attacks' in oracle` |

---

## 7. Cross-cutting architecture concern

The judges flagged a deeper issue: **the AI evaluates plays in isolation, not as a sequence**. Thraben Charm being "held" for two turns while the board deteriorates is a symptom of evaluating "what is the best play right now" rather than "what sequence of plays over the next 3 turns maximises my win probability."

This is a bigger architectural change than the four tasks above. Do NOT attempt it in this session. Document it for a future planning session:

> **Future work (not this session):** Multi-turn lookahead. The `TurnPlanner` currently simulates one combat. Extending it to simulate opponent's likely next turn (draw → deploy → attack) and Boros's response would let it correctly value "cast Thraben Charm now before opponent equips CP" over "cast Goblin Bombardment now, Charm next turn." This requires integrating opponent hand model (BHI) with the TurnPlanner — estimated 3-4 days of work.

---

## 8. Files to read before starting

In this order:
1. `CLAUDE.md` — quickstart and conventions
2. `ai/ev_player.py` — `_execute_main_phase()`, `_reactive_only` gate, `_has_high_threat_target` (to add)
3. `ai/ev_evaluator.py` — `EVSnapshot`, `estimate_spell_ev()`, `_project_spell()`
4. `ai/clock.py` — `position_value()`, `opp_clock` derivation
5. `ai/mana_planner.py` — `choose_fetch_target()`
6. `ai/turn_planner.py` — `plan_attack()`, chip damage bonus
7. `ai/strategy_profile.py` — `big_creature_power` threshold to replace

---

## 9. Session checklist

- [ ] `git pull origin main` before starting
- [ ] `python merge_db.py` before first sim
- [ ] Read all 8 files in Section 8
- [ ] Implement Task Group 1 → verify → commit
- [ ] Implement Task Group 2 → verify → commit
- [ ] Implement Task Group 4 → verify → commit
- [ ] Implement Task Group 3 → verify → commit
- [ ] Full regression: `python run_meta.py --matrix -n 30 --save`
- [ ] Update `PROJECT_STATUS.md` grade and fix table
- [ ] Rebuild replay: `python run_meta.py --bo3 energy affinity -s 60100 > replays/... && python build_replay.py ...`
- [ ] Commit all outputs and push
