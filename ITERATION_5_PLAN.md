# MTGSimManu — Iteration 5: Broken Deck Rehabilitation

> **Session type:** Architecture-first. Read this file completely before writing any code.
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Grade entering:** C+

---

## Current state

| Deck | Flat WR | Expected | Gap | Category |
|---|---|---|---|---|
| Affinity | 93% | ~60% | −33pp | P0 inflated |
| Ruby Storm | 25-30% | ~45% | −15pp | P1 broken |
| Living End | 3% | ~40% | −37pp | P0 broken |
| Azorius Control | 15% | ~35% | −20pp | P1 broken |
| Goryo's Vengeance | 29% | ~40% | −11pp | P2 low |

---

## Architectural diagnosis

These four decks fail for distinct reasons. Group them correctly or fixes interfere with each other.

### Group A — Affinity (P0 inflated): opponents don't answer CP

Covered separately in `AFFINITY_MATCHUP_PLAN.md`. Execute that plan first. Do not merge this with Groups B/C/D.

### Group B — Ruby Storm (P1 broken): combo fires but doesn't kill

**What the data shows:**
- Win conditions: `damage: 8, timeout: 1` in 30 games. Grapeshot kills 8 times only.
- Storm WR was 51% before session-4 fixes, now 30%. The urgency_factor fix likely discounts rituals when `opp_clock` is low — correct behaviour for normal decks, wrong for a combo deck that MUST fire this turn.
- Audit: Grapeshot WinCR 55%, cast AvgT T6.0 — it's firing but too late and too infrequently.
- Storm kills at T3 in its two wins, T6+ in everything else — there's no T4-5 kill line.

**Root cause:**
The `urgency_factor` discount in `_project_spell()` now penalises ritual spells (which have deferred value as mana enablers). But for Storm, a ritual is an *immediate* enabler — it contributes mana THIS turn. The `_spell_generates_value_now()` detection must recognise ritual spells as immediate (they add mana to the pool on resolution, which is used this same turn).

Additionally, `storm_patience` causes Storm to hold rituals until it "can go". This is correct in principle but the `can_go` threshold (`has_finisher and total_fuel >= min_fuel`) may be too conservative — Storm should fire at T3-4 with Grapeshot in hand + 2+ rituals, not wait until T5-6.

**The fix is NOT to special-case Ruby Storm.** The fix is to make `_spell_generates_value_now()` correctly detect that any spell which produces mana this turn (mana rituals, Manamorphose) is an immediate-effect spell and should NOT receive the urgency discount.

Oracle detection pattern: `'add' in oracle` (mana production) or `'mana' in oracle and 'add' in oracle` — already used in mana land detection.

### Group C — Living End (P0 broken): cascade fires, post-combo AI does nothing

**What the data shows:**
- WR: 3%. Win conditions: 4 damage + 1 mill in 30 games.
- Mulligan rate: 43% — the deck correctly identifies bad hands.
- Living End resolves (cascade fires) but the creatures don't attack. Avg win turn T10.2 when it wins — a 5-turn delay after cascade on T3-4.
- Verbose trace: cascade fires, Living End resolves, but then the AI continues in `curve_out` goal, not an aggression goal.

**Root cause:**
When Living End resolves, it creates a large board instantly (all graveyard creatures return). The AI's GoalEngine continues on the current goal sequence without recognising the board state has fundamentally changed. The deck needs to transition to `PUSH_DAMAGE` or `CLOSE_GAME` goal immediately after Living End resolves.

The fix belongs in `oracle_resolver.py` or `card_effects.py` (wherever Living End resolution is handled) — after all creatures enter, fire a GoalEngine advance signal. **This must be oracle-driven**: detect "return all creature cards from all graveyards to the battlefield" pattern, not hardcoded to Living End by name.

Additionally, post-combo creatures have summoning sickness on the turn they enter. Living End works by attacking the NEXT turn. The AI must recognise: "I just created a board of 3/3+ creatures, attack next turn with all of them."

### Group D — Azorius Control (P1 broken): win conditions never assemble

**What the data shows:**
- WR: 15% (up from 12% pre-fixes, but still broken).
- Win conditions: `damage: 2, timeout: 5` — winning by timeout/mill 5 times, combat only 2. This means the AI never assembles a real win condition.
- Teferi, Time Raveler cast avg T6.5 — late but present.
- Orim's Chant WinCR: 20% — heavily in losses. Same problem Boros had.
- Prismatic Ending WinCR: 13% — being cast proactively against wrong targets.
- Supreme Verdict WinCR: 17% — being held too long OR cast into an empty board.

**Root cause:** Three separate issues stacking:
1. **No clock win condition.** Control wins by eventually killing with a creature or planeswalker. Azorius needs Teferi, Hero of Dominaria to generate tokens or a Solitude/Verdict package to stabilise and finish. The audit shows `Teferi, Hero of Dominaria` at WinCR 53% (best in the deck) but only cast 0.6×/game — the AI doesn't recognise him as the win condition and scores him below interactive spells.
2. **Reactive cards cast proactively.** Orim's Chant (22% WinCR), Prismatic Ending (13% WinCR), Supreme Verdict (17% WinCR) all appear far more in losses. These are the `_reactive_only` problem — the urgency factor now correctly discounts them, but something overrides it.
3. **No "stabilise then win" phase recognition.** Control decks need to: (phase 1) survive early aggression with counters/removal, then (phase 2) deploy win conditions. The GoalEngine for Control likely has these phases but the AI keeps casting interaction even in phase 2.

Fix approach: The `urgency_factor` is correct. The issue is that `_reactive_only` cards are leaking into the main phase through the `has_high_threat` gate (Fix 1 from v2 plan) — threat scoring is now broadly triggering. Add a `control_patience` flag to StrategyProfile that further suppresses reactive plays when `opp_clock >= 4` (no immediate threat). This is NOT a new concept — it mirrors `storm_patience` already in the profile.

---

## The three fixes (execution order)

### Fix 1 — Mana rituals are immediate-effect spells (Group B)

**File:** `ai/ev_evaluator.py` → `_spell_generates_value_now()`

```python
def _spell_generates_value_now(card) -> bool:
    oracle = (card.template.oracle_text or '').lower()
    tags = getattr(card.template, 'tags', set())
    if card.template.is_creature: return True
    if 'removal' in tags or 'board_wipe' in tags: return True
    if 'draw' in tags or 'cantrip' in tags: return True
    # ETB effects
    if 'enters' in oracle and any(w in oracle for w in ('deal','gain','exile','create','draw')): return True
    # MANA PRODUCTION: rituals produce mana this turn — never discount
    if 'add' in oracle and any(w in oracle for w in ('{r}','{g}','{b}','{u}','{w}','mana of any')): return True
    # Deferred-value activated abilities
    if 'sacrifice a creature' in oracle and 'damage' in oracle: return False
    if '{t}:' in oracle and 'add' not in oracle: return False
    return True
```

**Verify:**
```bash
python run_meta.py --matchup storm energy -n 20 2>&1 | tail -3
# Target: Storm WR rises from 15% toward 35%+
python run_meta.py --matchup storm dimir -n 20 2>&1 | tail -3
# Target: Storm WR rises from 0% toward 20%+
python run_meta.py --matchup energy dimir -n 20 2>&1 | tail -3
# No regression in normal decks
```

---

### Fix 2 — Post-combo goal transition (Group C)

**File:** `engine/card_effects.py` or `engine/oracle_resolver.py` — wherever "return all creatures from all graveyards" resolves.

Find the resolution handler for mass-reanimate sorceries (pattern: `oracle contains "return all creature cards" and "from" and "graveyard" and "battlefield"`). After all creatures enter the battlefield, push a goal transition signal.

```python
# In the resolution handler, after creatures enter:
# Detect mass-reanimate: "return all creature cards...from...graveyard...battlefield"
import re as _re
if _re.search(r'return all creature cards.+graveyard.+battlefield', oracle):
    # Signal GoalEngine: transition to PUSH_DAMAGE
    if hasattr(game, '_pending_goal_advance'):
        game._pending_goal_advance[controller] = 'post_combo_aggression'
    # Log for replay visibility
    game.log.append(f"T{game.display_turn} P{controller+1}: "
                    f"Mass reanimate resolved — transitioning to aggression")
```

Then in `ev_player.py` `_execute_main_phase`, check for `_pending_goal_advance` and call `goal_engine.advance_goal()` before scoring plays.

**Robustness:** The oracle pattern `return all creature cards...from...graveyard...battlefield` covers Living End, Patriarch's Bidding, and any future mass-reanimate card. No card names.

**Verify:**
```bash
python run_meta.py --matchup "Living End" tron -n 20 2>&1 | tail -3
# Target: Living End WR rises from 0% toward 30%+
python run_meta.py --verbose "Living End" "Domain Zoo" -s 50010 2>&1 | grep -E "aggress|PUSH|Goal|Attack"
# Expected: GoalEngine shows PUSH_DAMAGE after Living End resolves
```

---

### Fix 3 — Control patience gate (Group D)

**Files:** `ai/strategy_profile.py`, `ai/ev_player.py` → reactive_only gate

Add `control_patience: bool = False` to `StrategyProfile` and set it True for CONTROL archetype. When `control_patience` is True, further restrict reactive-only cards: only release them from the gate when `opp_clock <= 2` (actual emergency), not just `has_high_threat`.

```python
# In StrategyProfile
control_patience: bool = False  # suppress reactive plays until emergency

# In CONTROL profile
CONTROL = StrategyProfile(
    burn_face_mult=0.0,
    control_patience=True,
)
```

Then in the reactive_only gate:
```python
if spell.name in self._reactive_only:
    if not spell.template.is_creature:
        is_dying = snap.am_dead_next or (snap.opp_power >= prof.dying_opp_power
                                          and snap.opp_clock <= prof.dying_opp_clock)
        has_high_threat = self._has_high_threat_target(game, spell)
        # Control patience: only cast reactive spells when truly needed
        if prof.control_patience and not is_dying:
            continue  # hold everything until dying
        if not is_dying and not has_high_threat:
            continue
```

Also ensure Teferi, Hero of Dominaria gets a proper EV boost — he's the win condition but scores poorly (no power/toughness). He should score similarly to planeswalkers with draw + token generation. The existing planeswalker overlay in `_score_spell` should already handle this (`loyalty bonus + draw bonus`). Check why his WinCR is only 53% at 0.6×/game.

**Verify:**
```bash
python run_meta.py --matchup "Azorius Control" zoo -n 20 2>&1 | tail -3
# Target: Control WR rises from 5% toward 25%+
python run_meta.py --audit "Azorius Control" -n 30 2>&1 | grep -E "Orim|Prismatic|Teferi.*Hero|win%|Win rate"
# Target: Teferi Hero cast rate increases, Orim's Chant WinCR improves
python run_meta.py --matchup energy dimir -n 20 2>&1 | tail -3
# No regression in midrange matchups
```

---

## Generic robustness checklist

Every fix must pass all of these before implementation:

- [ ] No card names in engine or AI logic
- [ ] No deck name or archetype checks outside `StrategyProfile` or gameplan JSON
- [ ] No hardcoded power/toughness thresholds
- [ ] Oracle detection covers the full card pattern, not just one card
- [ ] Tested on 4+ matchup pairs
- [ ] EVSnapshot is the single data contract — no new parameters through the call chain
- [ ] `_spell_generates_value_now` change verified not to break Goblin Bombardment discount

---

## What NOT to do

| Anti-pattern | Reject because | Generic alternative |
|---|---|---|
| `if deck_name == 'Ruby Storm': don't discount rituals` | Deck-specific | Oracle: `'add' in oracle and mana symbols` |
| `if card.name == 'Living End': trigger aggression` | Card-specific | Oracle: `return all creature cards.*graveyard.*battlefield` |
| `if archetype == 'control': never cast sorceries` | Too broad — control needs to cast Supreme Verdict | `control_patience` + `opp_clock <= 2` gate |
| `if storm_count >= 3: cast Grapeshot` | Hardcoded threshold | Already handled by `_estimate_combo_chain` — don't duplicate |
| Add Living End ETB attack triggers manually | Bypasses summoning sickness rules | Flag post-combo goal transition, let combat AI handle it naturally next turn |

---

## Implementation order

```
Fix 1 (ritual immediate effect)     ← lowest risk, highest Storm impact
    → verify Storm vs Energy, Storm vs Dimir
Fix 2 (post-combo goal transition)  ← medium risk, Living End P0
    → verify Living End vs Tron; verbose trace shows PUSH_DAMAGE
Fix 3 (control patience)            ← highest risk (affects all control decks)
    → verify Control vs Zoo; no regression vs Boros
Full regression suite (see below)
```

---

## Regression suite

Run after all three fixes:

```bash
# Broken decks — should improve
python run_meta.py --matchup storm energy -n 30 2>&1 | tail -3
python run_meta.py --matchup storm dimir -n 30 2>&1 | tail -3
python run_meta.py --matchup "Living End" tron -n 30 2>&1 | tail -3
python run_meta.py --matchup "Living End" zoo -n 20 2>&1 | tail -3
python run_meta.py --matchup "Azorius Control" zoo -n 20 2>&1 | tail -3
python run_meta.py --matchup "Azorius Control" energy -n 20 2>&1 | tail -3

# Healthy decks — must not regress
python run_meta.py --matchup energy affinity -n 20 2>&1 | tail -3
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
python run_meta.py --matchup blink dimir -n 20 2>&1 | tail -3
python run_meta.py --matchup prowess energy -n 20 2>&1 | tail -3

# Full matrix smoke test
python run_meta.py --matrix -n 10 --save 2>&1 | tail -5

# Audit the two most important broken decks
python run_meta.py --audit storm -n 60 2>&1 | grep -E "Win rate|Grapeshot|Pyretic|Desperate|avg win"
python run_meta.py --audit "Living End" -n 30 2>&1 | grep -E "Win rate|Living End|win conditions|avg win"
```

Target grades after this iteration:
- Ruby Storm: 25% → 35-40%
- Living End: 3% → 25-35%
- Azorius Control: 15% → 25-30%
- No regression > 5pp in healthy decks

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `ai/ev_evaluator.py` (`_spell_generates_value_now`), `ai/ev_player.py` (storm combo modifier, reactive_only gate), `engine/card_effects.py` or `oracle_resolver.py` (mass-reanimate handling), `ai/strategy_profile.py`
- [ ] Fix 1: mana ritual immediate detection
- [ ] Verify Fix 1 (Storm WR improves, Bombardment still discounted)
- [ ] Fix 2: post-combo goal transition (oracle-driven)
- [ ] Verify Fix 2 (Living End attacks next turn after cascade)
- [ ] Fix 3: control_patience gate
- [ ] Verify Fix 3 (Control WR improves, no midrange regression)
- [ ] Full regression suite
- [ ] Update `PROJECT_STATUS.md` with grade, fix table, audit metrics
- [ ] `git commit -m "fix(iter5): ritual immediate-effect, post-combo goal transition, control patience"`
- [ ] `git push origin main`
