# MTGSimManu — Iteration 7: Judge Panel Findings

> **Session type:** Architecture-first. Read this entire file before writing any code.
> **Source:** Three-judge LLM panel — Pro Tour Tactical, Strategic Systems, Maths & Architecture.
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Grade entering:** C+

---

## What the judges found

All three judges validated the architecture is sound. Issues are calibration and coverage gaps, not structural flaws. Every finding below has a clear, bounded fix.

---

## Fix 1 — Mandatory attacker oracle tag (Tactical + Strategic)

**Signal:** Ragavan delta −0.34. A T1 haste threat appears MORE in losses than wins. Attack threshold is suppressing it.

**Root cause:** `attack_threshold` penalises attacks where the attacker might trade. For creatures with `'whenever this creature deals combat damage to a player'` in oracle, the value is **zero if it doesn't deal damage**. The trade penalty is wrong — it should be overridden.

**Fix:** In `decide_attackers()` / `plan_attack()`, detect oracle combat-damage triggers and mark those creatures as always worth sending:

```python
def _has_combat_damage_trigger(creature) -> bool:
    """Oracle-driven: creature gets value from dealing combat damage to player."""
    oracle = (creature.template.oracle_text or '').lower()
    return 'deals combat damage to a player' in oracle or \
           'whenever this creature deals combat damage' in oracle
```

Apply in attack scoring: creatures with this flag receive a flat +3.0 bonus to attack EV regardless of trade risk. This covers Ragavan, Psychic Frog, and any future card with the same pattern — no card names.

**Files:** `ai/ev_player.py` → `decide_attackers`, `ai/turn_planner.py` → chip_damage bonus

**Verify:**
```bash
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "T[12] P1.*Attack\|Ragavan"
# Expected: Ragavan attacks T2 consistently
python run_meta.py --audit energy -n 60 2>&1 | grep "Ragavan"
# Target: Ragavan delta moves from -0.34 toward 0 or positive
```

---

## Fix 2 — Goblin Bombardment lethal-push activation (Tactical + Strategic)

**Signal:** Bombardment delta −0.69 post-urgency-fix. WinCR 42%. Cast rate correctly reduced, but activation timing is reactive (desperate) not proactive (lethal).

**Root cause:** Bombardment activates when the AI is losing. It should activate when `my_power + sacrifice_available >= opp_life` — push lethal now. The existing `_activate_goblin_bombardment` in `game_runner.py` fires at END_STEP but only when racing. It doesn't model "if I sacrifice all my tokens, can I reach lethal this turn?"

**Fix:** In `_activate_goblin_bombardment`, add a lethal-check mode: count all sacrificeable tokens + Bombardment activations. If total damage would kill the opponent this turn, sacrifice everything and push lethal. Oracle-driven: detect `sacrifice a creature: deal 1 damage` pattern on any permanent.

```python
# Lethal push: if sac-able creatures + combat damage >= opp_life, go for it
token_count = len([c for c in me.battlefield 
                   if c.template.is_creature and not c.template.is_land
                   and (c.power or 0) <= 1])  # small tokens worth saccing
if (snap.my_power + token_count) >= opp.life:
    # Sac everything and win
```

**Files:** `engine/game_runner.py` → `_activate_goblin_bombardment`

**Verify:**
```bash
python run_meta.py --audit energy -n 60 2>&1 | grep "Goblin Bombardment"
# Target: WinCR moves from 42% toward 55%+; delta from -0.69 toward -0.20
```

---

## Fix 3 — creature_value uses live snapshot not blank default (Maths)

**Signal:** Judge 3 identified that `creature_value(card)` calls `creature_clock_impact_from_card(card, _DEFAULT_SNAP)` — a hardcoded blank board (20/20 life, no creatures). This systematically overvalues small creatures (they look impactful on an empty board) and undervalues large ones (diminishing returns not modelled).

**Root cause:** `_DEFAULT_SNAP` in `ev_evaluator.py` is used as a constant for all creature valuations across all game states. The actual game context — life totals, existing board power, blockers — is ignored.

**Fix:** Pass the current `EVSnapshot` to `creature_value` at call sites. The function signature becomes `creature_value(card, snap=None)` with fallback to `_DEFAULT_SNAP` for backwards compatibility.

```python
def creature_value(card: "CardInstance", snap: Optional[EVSnapshot] = None) -> float:
    from ai.clock import creature_clock_impact_from_card
    effective_snap = snap if snap is not None else _DEFAULT_SNAP
    return creature_clock_impact_from_card(card, effective_snap) * 20.0
```

Update all call sites in `ev_player.py` to pass `snap` where available. This is a non-breaking change — existing calls without `snap` continue to work.

**Files:** `ai/ev_evaluator.py` → `creature_value`, `ai/ev_player.py` → all `creature_value(c)` calls

**Verify:**
```bash
python run_meta.py --matchup energy affinity -n 20 2>&1 | tail -3
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
# No regression. Targeting of small vs large threats should improve.
python run_meta.py --audit energy -n 60 2>&1 | grep "Guide of Souls\|Ajani"
# Target: Guide of Souls delta moves from -0.18 toward 0
```

---

## Fix 4 — Continuous clock function (Maths)

**Signal:** Judge 3 identified `opp_clock = ceil(my_life / opp_power)` creates evaluation cliffs at multiples of `opp_power`. life=21,power=7 → clock=3; life=22,power=7 → clock=4. Near-equal states get different urgency weights.

**Fix:** Use continuous division for scoring purposes, ceiling only for boolean "will I die" checks.

```python
@property
def opp_clock(self) -> float:
    """Turns until opponent kills me — continuous for smooth gradient."""
    if self.opp_power <= 0:
        return 99.0
    return max(1.0, self.my_life / self.opp_power)  # continuous, not ceil

@property  
def opp_clock_discrete(self) -> int:
    """Integer turns for rule-based checks (will I survive untap?)."""
    if self.opp_power <= 0:
        return 99
    return max(1, math.ceil(self.my_life / self.opp_power))
```

Replace boolean death checks (`opp_clock <= 1`) with `opp_clock_discrete <= 1`. Leave the continuous version for urgency_factor calculation.

**Files:** `ai/ev_evaluator.py` → `EVSnapshot.opp_clock` property

**Verify:**
```bash
python run_meta.py --matrix -n 10 --save 2>&1 | tail -5
# No significant WR changes — this is a smoothing fix, not a directional one
python -m pytest tests/ -q 2>&1 | tail -5
```

---

## Fix 5 — Urgency factor stability near cliff (Maths)

**Signal:** `urgency_factor = (opp_clock-1)/4.0` is numerically unstable at `opp_clock=1`. A 0.1 error in power estimation swings factor by 2.5%, collapsing deferred spell EV near death.

**Fix:** Use an exponential approach that is smoother near the boundary:

```python
@property
def urgency_factor(self) -> float:
    """Fraction of future turns we actually get. Exponential curve — 
    smooth near opp_clock=1, quickly approaches 1.0 for safe states."""
    slack = max(0.0, self.opp_clock - 1.0)
    return 1.0 - math.exp(-slack / 2.0)
    # opp_clock=1 → 0.0 (dying), opp_clock=3 → 0.63, opp_clock=5 → 0.78, opp_clock=∞ → 1.0
```

This preserves the original intent (0 when dying, ~1 when safe) but is C∞ differentiable at the boundary and less sensitive to power estimation errors.

**Note:** This changes urgency_factor values for all decks. Run the full matrix after this change and verify no deck moves more than ±5pp.

**Files:** `ai/ev_evaluator.py` → `EVSnapshot.urgency_factor` property

**Verify:**
```bash
python run_meta.py --matrix -n 20 --save
# Compare to baseline. No deck should move >5pp.
python run_meta.py --audit energy -n 60 2>&1 | grep "Win rate\|Goblin Bombardment"
# Bombardment WinCR should not change significantly (already discounted correctly)
```

---

## Fix 6 — Post-combo push_turns for Living End (Strategic)

**Signal:** Judge 2 identified `aggression_boost_turns=2` is necessary but not sufficient. After the cascade attack turn, GoalEngine reverts to `curve_out`. Living End needs sustained aggression for 2-3 turns post-combo (opponent has no board).

**Fix:** Add `post_combo_push_turns` to `PlayerState`. Set to 3 when mass-reanimate resolves. Decrement each upkeep. While > 0, GoalEngine stays in `PUSH_DAMAGE`.

```python
# In _resolve_living_end(), after aggression_boost_turns:
self.players[controller].post_combo_push_turns = 3

# In GoalEngine.should_advance(), check:
if getattr(me, 'post_combo_push_turns', 0) > 0:
    return 'push_damage'  # stay aggressive
```

Oracle-driven: the `post_combo_push_turns` is set by the mass-reanimate oracle detection already in `_resolve_living_end` — no card names.

**Files:** `engine/game_state.py` → `PlayerState`, `_resolve_living_end`; `ai/gameplan.py` → `GoalEngine`

**Verify:**
```bash
python run_meta.py --matchup "Living End" tron -n 20 2>&1 | tail -3
# Target: Living End WR 20% → 35%+
python run_meta.py --verbose "Living End" "Eldrazi Tron" -s 50300 2>&1 | grep "Goal\|Attack\|post_combo"
# Expected: PUSH_DAMAGE goal sustained for turns after cascade
```

---

## Implementation order

```
Fix 4: Continuous clock (low risk, pure smoothing)          → verify matrix ≤±2pp drift
Fix 5: Urgency exponential (moderate risk, affects all)     → verify matrix ≤±5pp drift
Fix 1: Combat-damage-trigger attack bonus (Ragavan)         → verify Ragavan delta improves
Fix 3: creature_value live snapshot                         → verify targeting improves
Fix 2: Bombardment lethal-push                              → verify WinCR improves
Fix 6: Living End post-combo push                           → verify LE WR improves
Full regression matrix n=30
```

---

## Regression suite

```bash
python run_meta.py --matrix -n 10 --save 2>&1 | tail -5   # baseline before starting

# After each fix group:
python run_meta.py --matchup energy affinity -n 20 2>&1 | tail -3
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
python run_meta.py --matchup storm dimir -n 20 2>&1 | tail -3
python run_meta.py --matchup "Living End" tron -n 20 2>&1 | tail -3

# After all fixes:
python run_meta.py --audit energy -n 60 2>&1 | grep -E "Win rate|Ragavan|Bombardment|delta"
# Targets: Ragavan delta -0.34 → ≥ -0.10 | Bombardment delta -0.69 → ≥ -0.30
python run_meta.py --matrix -n 30 --save
```

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `ai/ev_evaluator.py` (EVSnapshot), `ai/ev_player.py` (decide_attackers, creature_value calls), `engine/game_runner.py` (_activate_goblin_bombardment), `engine/game_state.py` (_resolve_living_end)
- [ ] Baseline matrix n=10
- [ ] Fix 4: continuous clock property
- [ ] Fix 5: exponential urgency_factor
- [ ] Verify matrix ≤±5pp drift
- [ ] Fix 1: combat-damage-trigger attack bonus
- [ ] Fix 3: creature_value live snapshot
- [ ] Fix 2: Bombardment lethal-push
- [ ] Fix 6: Living End post-combo push
- [ ] Full regression suite
- [ ] `python run_meta.py --audit energy -n 60` — confirm Ragavan and Bombardment deltas improved
- [ ] Update `PROJECT_STATUS.md` grade + fix table
- [ ] `git commit -m "fix(iter7): combat-trigger attack bonus, Bombardment lethal-push, live snapshot creature_value, continuous clock, exponential urgency, LE post-combo push"`
- [ ] `git push origin main`
