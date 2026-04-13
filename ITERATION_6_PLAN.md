# MTGSimManu — Iteration 6: Broken Deck Rehabilitation

> **Session type:** Architecture-first. Read this entire file before writing any code.
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Grade entering:** C+

---

## Diagnosis

Four decks confirmed broken from audit data and verbose traces. Each has a distinct root cause.

### A. Living End (15% WR) — `aggression_boost_turns` consumed before creatures can attack

**Evidence:**
- Win conditions: all damage, avg win turn T9.4 — far too slow for a T4 combo deck
- Living End resolves, log shows `[Declare Attackers] P1 does not attack` immediately after
- Creatures enter with **summoning sickness** on the turn Living End resolves
- `aggression_boost_turns` is set to `1` in `_resolve_living_end()`
- `end_combat()` in `combat_manager.py` decrements `aggression_boost_turns` at end of *that same turn's combat*
- Result: the flag is consumed on the turn creatures can't attack anyway, and they don't attack the following turn

**Root cause in code:**
```python
# game_state.py _resolve_living_end() — current (broken):
self.players[controller].aggression_boost_turns = max(..., 1)
# Flag consumed in end_combat() same turn → gone before next turn attack
```

**Fix:** Set `aggression_boost_turns = 2`. The first decrement happens in end_combat on the turn of entry (wasted, creatures sick). The second survives to the next turn's combat and correctly lowers the attack threshold.

**File:** `engine/game_state.py` → `_resolve_living_end()`

```python
# AFTER fix:
self.players[controller].aggression_boost_turns = max(
    getattr(self.players[controller], 'aggression_boost_turns', 0), 2
)
```

One line change. No robustness concerns.

---

### B. Ruby Storm (20% WR) — rituals discounted by urgency_factor

**Evidence:**
- Pyretic Ritual: 2.75× in wins vs 1.79× in losses — delta +0.96 (best card but still losing)
- Grapeshot: avg cast T6.2, WinCR 43% — fires late and inconsistently
- Storm kills at T3-4 in wins, T6-7 everywhere else — the combo assembles but slowly
- Audit shows 119 Pyretic Ritual casts in 60 games — it fires. The problem is timing

**Root cause:**
`_spell_generates_value_now()` doesn't recognise mana rituals as immediate-effect spells. When `opp_clock` is low (opponent about to kill), the `urgency_factor` penalises rituals because they don't fit any immediate-value pattern. But for Storm, a ritual IS immediate — it adds mana to the pool this turn, which is spent this turn to chain into more spells and eventually Grapeshot.

**Detection pattern:** Oracle text of mana rituals contains `add` and mana symbols. This is generic — covers all present and future ritual effects.

**File:** `ai/ev_evaluator.py` → `_spell_generates_value_now()`

```python
# ADD this case before the deferred-value checks:
# Mana production: adds mana this turn — always immediate regardless of urgency
if 'add' in oracle and any(s in oracle for s in ('{r}', '{g}', '{b}', '{u}', '{w}', 'mana of any')):
    return True
```

**Verify the Goblin Bombardment discount is NOT broken:**
Bombardment oracle: `"sacrifice a creature: deal 1 damage"` — no `add` + mana symbol pattern. Still correctly deferred.

---

### C. Azorius Control (23% WR) — reactive cards spamming main phase

**Evidence:**
- Orim's Chant: 1.1×/game at avg T5.5 — being proactively cast constantly
- Prismatic Ending: 1.0×/game at avg T5.6 — same
- Both have +0.42/+0.43 win contribution delta — but are in losses at 1.0×
- Win conditions: 8 timeouts, 6 damage in 60 games — never assembles a clock
- Teferi, Hero of Dominaria: 0.6×/game at avg T8.0 — the win condition arrives too late

**Root cause — two stacked issues:**

**Issue 1:** The `has_high_threat` gate (from v2 plan) releases reactive-only cards from the gate when there's a battle-cry/scaling threat. This is correct for aggro decks. For Control, releasing Orim's Chant because opponent has a dangerous creature is wrong — Orim's Chant doesn't kill creatures. The fix is a `control_patience` flag that keeps reactive spells locked until `am_dead_next` or `opp_clock <= 2`, ignoring `has_high_threat`.

**Issue 2:** Teferi Hero planeswalker EV undervalued. The existing planeswalker overlay adds `5 + 1.5 × loyalty`. Teferi Hero has loyalty 4 → gets +11. But he also draws every turn (-1 ability) and eventually makes a 1/1 token. His steady-state value is much higher than a loyalty-4 planeswalker with no text. The fix: add a bonus for planeswalkers whose oracle contains `draw` and `untap` (indicating they generate card advantage AND protect themselves).

**Files:**
- `ai/strategy_profile.py` — add `control_patience: bool = False`, set True in CONTROL profile
- `ai/ev_player.py` — reactive_only gate: if `control_patience`, override to `is_dying` only
- `ai/ev_player.py` `_score_spell()` — planeswalker overlay: add `untap` oracle bonus

```python
# strategy_profile.py — add to StrategyProfile dataclass:
control_patience: bool = False

# CONTROL profile:
CONTROL = StrategyProfile(
    burn_face_mult=0.0,
    control_patience=True,
)

# ev_player.py reactive_only gate — add after existing is_dying/has_high_threat check:
if prof.control_patience and not is_dying:
    continue  # Control holds all reactive spells until truly dying

# ev_player.py _score_spell planeswalker overlay — add after existing draw bonus:
if 'untap' in o and 'land' in o:
    ev += 3.0  # untap lands = mana advantage + draws = substantial ongoing value
```

---

### D. Goryo's Vengeance (38% WR) — not broken, but two weak cards dragging WR

**Evidence from audit:**
- Goryo resolves and attacks correctly (verified in verbose trace — seed 50501 shows T4 Archon attacks for 6)
- Problem is the 60% mulligan rate — deck is very hand-dependent
- Undying Evil: 10 casts, **0% WinCR** — never contributes to wins
- Unburial Rites: 8 casts, 25% WinCR — very late (avg T8.9)
- Thoughtseize: 66 casts at 38% WinCR — appears in many losses

**Root cause:** Undying Evil is in the deck but the engine doesn't implement its effect ("target creature gets +1/+1 until end of turn, if it would die, instead exile it with an undying counter..."). Without its effect, it's just a 1-mana do-nothing. Unburial Rites at T8.9 fires after the game is already decided.

**Fix:** This is not a planning issue — Undying Evil requires a full oracle implementation. Add it to the known implementation debt in PROJECT_STATUS.md. For now, add Undying Evil to the `reactive_only` list in the Goryo gameplan so the AI stops wasting mana on it.

**File:** `decks/gameplans/goryos_vengeance.json` — add `"Undying Evil"` to `reactive_only`

---

## Implementation order

```
Fix A: Living End aggression_boost_turns = 2     (1 line, 5 min)
    → VERIFY: Living End attacks turn after cascade
Fix B: Ritual immediate-effect detection          (5 lines, 10 min)
    → VERIFY: Storm avg kill turn drops to T4-5
Fix C1: control_patience flag                     (20 min)
    → VERIFY: Orim's Chant cast rate drops; Control WR improves
Fix C2: Teferi Hero untap bonus                   (5 min)
    → VERIFY: Teferi Hero cast rate increases
Fix D: Undying Evil → reactive_only               (2 min)
    → VERIFY: Undying Evil no longer cast proactively
```

---

## Robustness checklist (apply before every fix)

- [ ] No card names in engine or AI code — oracle text only
- [ ] No deck/archetype name branches outside StrategyProfile or gameplan JSON
- [ ] No hardcoded power/toughness magic numbers
- [ ] New flags use existing data contract patterns (EVSnapshot properties, StrategyProfile fields)
- [ ] Verify with 4+ matchup pairs, not just the target matchup
- [ ] `_spell_generates_value_now()` change verified: Goblin Bombardment oracle has no `add` + mana symbol → still correctly deferred

---

## Anti-patterns to reject

| What | Why rejected | Correct approach |
|---|---|---|
| `if deck == 'Ruby Storm': don't discount rituals` | Deck-specific | Oracle: `'add' in oracle and mana symbol` |
| `if card.name == 'Living End': set aggression=2` | Card-specific | Already in `_resolve_living_end()` — just change the value |
| `if archetype == 'control': never cast Orim's Chant` | Too broad — Control still needs to cast it when dying | `control_patience` + `am_dead_next` gate |
| `if card.name == 'Undying Evil': skip` | Card-specific | Add to `reactive_only` in gameplan JSON data |

---

## Regression suite

```bash
# Target improvements
python run_meta.py --matchup "Living End" tron -n 30 2>&1 | tail -3
# Living End WR: 0% → 25%+

python run_meta.py --matchup storm energy -n 30 2>&1 | tail -3
# Storm WR: 15% → 30%+

python run_meta.py --matchup storm dimir -n 30 2>&1 | tail -3
# Storm WR: 0% → 20%+

python run_meta.py --matchup "Azorius Control" zoo -n 20 2>&1 | tail -3
# Control WR: 5% → 20%+

# Must not regress
python run_meta.py --matchup energy affinity -n 20 2>&1 | tail -3
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
python run_meta.py --matchup blink dimir -n 20 2>&1 | tail -3

# Full smoke test
python run_meta.py --matrix -n 10 --save 2>&1 | tail -5

# Audit signals
python run_meta.py --audit "Living End" -n 40 2>&1 | grep -E "Win rate|avg win turn|Win conditions"
# Target: avg win turn T6-7 (down from T9.4), damage wins increase

python run_meta.py --audit storm -n 60 2>&1 | grep -E "Win rate|Grapeshot|avg win"
# Target: Grapeshot avg cast T4-5 (down from T6.2), WR 30%+

python run_meta.py --audit "Azorius Control" -n 40 2>&1 | grep -E "Win rate|Orim|Prismatic|Teferi.*Hero"
# Target: Orim's Chant cast rate drops from 1.1×/game; Teferi Hero rate rises

python run_meta.py --verbose "Living End" "Eldrazi Tron" -s 50300 2>&1 | grep -E "aggress|Goal|Attack with|does not attack" | head -10
# Expected: creatures attack the turn AFTER Living End resolves (not same turn)
```

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `engine/game_state.py` (`_resolve_living_end`, `end_combat`), `ai/ev_evaluator.py` (`_spell_generates_value_now`), `ai/strategy_profile.py`, `ai/ev_player.py` (reactive_only gate, planeswalker overlay), `decks/gameplans/goryos_vengeance.json`
- [ ] Fix A: `aggression_boost_turns = 2`
- [ ] Verify Fix A (verbose trace shows attack turn after Living End)
- [ ] Fix B: mana ritual immediate detection in `_spell_generates_value_now`
- [ ] Verify Fix B (Storm avg kill turn drops; Bombardment discount unchanged)
- [ ] Fix C1: `control_patience` in StrategyProfile + CONTROL profile + gate
- [ ] Fix C2: Teferi Hero `untap` oracle bonus
- [ ] Verify Fix C (Orim's Chant rate drops; Control WR improves)
- [ ] Fix D: Undying Evil → `reactive_only` in gameplan JSON
- [ ] Full regression suite
- [ ] Update `PROJECT_STATUS.md` grade + fix table + audit metrics
- [ ] `python run_meta.py --matrix -n 30 --save` (larger matrix for cleaner numbers)
- [ ] `git commit -m "fix(iter6): LE aggression timing, ritual immediate-effect, control patience, undying evil hold"`
- [ ] `git push origin main`
