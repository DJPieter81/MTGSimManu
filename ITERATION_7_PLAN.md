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

---

## Fix 7 — Ocelot Pride end-step token trigger

**Status:** ✅ Confirmed working in Round 3 audit (grep pattern was wrong). Not a bug.

---

## Fix 8 — Endbringer activated abilities

**Signal:** Endbringer is cast and attacks correctly, but `{T}: deal 1 damage` and `{C}{C}{T}: draw a card` never fire. Ability objects in DB are tagged as `AbilityType.ETB` and `AbilityType.CAST` — wrong.

**Root cause:** The DB parser couldn't map `{T}: deal damage` → activated ability, so they default to ETB/CAST. The engine has no activated-ability-on-tap dispatch outside of planeswalkers.

**Fix:** In `engine/game_runner.py` → `_process_upkeep_activations()` (or a new `_activate_tap_abilities()` pass in MAIN1), detect permanents with `{T}:` patterns in oracle and dispatch them. Oracle-driven — no card names:

```python
def _activate_tap_abilities(self, game, active):
    """Activate {T}: abilities on non-PW, non-land permanents."""
    player = game.players[active]
    opp_idx = 1 - active
    for card in list(player.battlefield):
        if card.tapped or card.summoning_sick: continue
        oracle = (card.template.oracle_text or '').lower()
        # {T}: deal N damage to any target
        if '{t}: this creature deals 1 damage' in oracle:
            card.tapped = True
            opp = game.players[opp_idx]
            # Kill weakest creature or go face
            killable = [c for c in opp.creatures if (c.toughness or 0) - c.damage_marked <= 1]
            if killable:
                target = min(killable, key=lambda c: c.template.cmc)
                target.damage_marked += 1
                if target.is_dead: game._creature_dies(target)
                game.log.append(f"T{game.display_turn} P{active+1}: {card.name} pings {target.name}")
            else:
                opp.life -= 1
                game.log.append(f"T{game.display_turn} P{active+1}: {card.name} deals 1 to face")
        # {C}{C}{T}: draw a card  
        if '{c}{c}' in oracle and '{t}: draw a card' in oracle:
            mana = len(player.untapped_lands)
            if mana >= 2 and not card.tapped:
                # Only draw if ahead or card advantage needed
                from ai.ev_evaluator import snapshot_from_game
                snap = snapshot_from_game(game, active)
                if snap.my_hand_size <= 3 or snap.my_clock < snap.opp_clock:
                    game.tap_lands_for_mana(active, colorless=2)
                    card.tapped = True
                    drawn = game.draw_cards(active, 1)
                    if drawn:
                        game.log.append(f"T{game.display_turn} P{active+1}: {card.name} draws {drawn[0].name}")
```

Also fix: Endbringer untaps during **opponent's** untap step. Add to `resolve_untap_triggers()` or the untap step handler:
```python
# In TurnStep.UNTAP: untap opponent's permanents with "untap during each other player's untap step"
for card in opp_player.battlefield:
    oracle = (card.template.oracle_text or '').lower()
    if "untap this creature during each other player's untap step" in oracle:
        card.tapped = False
```

**Files:** `engine/game_runner.py` → `_process_main_phase()` + untap step

**Verify:**
```bash
python run_meta.py --verbose tron dimir -s 50700 2>/dev/null | grep -E "Endbringer.*ping|Endbringer.*draw|Endbringer.*deal"
# Expected: Endbringer pings or draws each turn
```

---

## Fix 9 — Lava Dart flashback (sacrifice Mountain)

**Signal:** Lava Dart casts from hand correctly but never flashbacks from GY.

**Root cause:** `can_cast()` checks `card.has_flashback` but never checks whether the flashback *cost* (sacrifice a Mountain) can be paid. The flashback cost is non-mana (land sacrifice) — there's no `flashback_cost` field on `CardTemplate` and no sacrifice-land payment path.

**Fix:** Parse flashback cost from oracle text when a card has `has_flashback = True` and it's being cast from GY. For `"Flashback—Sacrifice a [subtype]"` patterns, require that a matching land exists:

```python
# In can_cast(), after confirming card.has_flashback:
oracle = (template.oracle_text or '').lower()
import re
m = re.search(r'flashback.*sacrifice a (\w+)', oracle)
if m:
    land_type = m.group(1)  # e.g. 'mountain'
    has_land = any(land_type in (l.template.subtypes or []) or land_type in l.name.lower()
                   for l in player.lands if not l.tapped)
    if not has_land:
        return False  # can't pay flashback cost
```

And in `cast_spell()` when casting from GY with flashback, execute the sacrifice:
```python
# After confirming flashback cast:
m = re.search(r'flashback.*sacrifice a (\w+)', oracle_lower)
if m:
    land_type = m.group(1)
    to_sacrifice = next((l for l in player.lands 
                         if land_type in l.name.lower() or land_type in (l.template.subtypes or [])), None)
    if to_sacrifice:
        player.lands.remove(to_sacrifice)
        player.battlefield.remove(to_sacrifice)
        to_sacrifice.zone = 'graveyard'
        player.graveyard.append(to_sacrifice)
        game.log.append(f"T{game.display_turn} P{player_idx+1}: Flashback {card.name} — sacrifice {to_sacrifice.name}")
```

**Files:** `engine/game_state.py` → `can_cast()`, `cast_spell()`

**Verify:**
```bash
python run_meta.py --verbose prowess dimir -s 50700 2>/dev/null | grep -E "Lava Dart.*flashback|flashback.*Lava|sacrifice.*Mountain"
```

---

## Fix 10 — Mutagenic Growth phyrexian mana log visibility

**Signal:** Mutagenic Growth never appears in audit logs even when Prowess wins. The phyrexian mana payment code exists (`game_state.py` line 1449) but casts are silent in verbose output.

**Root cause:** The log for phyrexian mana payment (`player.life -= life_cost`) has no explicit log line. Only `Cast Mutagenic Growth (0)` appears, which looks like a free cast. AI also may not consider it outside of pump-targeting context.

**Fix:** Add explicit log line when phyrexian life payment fires:
```python
if phyrexian_count > 0 and player.life > phyrexian_count * 2:
    life_cost = phyrexian_count * 2
    player.life -= life_cost
    game.log.append(f"T{game.display_turn} P{player_idx+1}: "
                   f"Phyrexian mana — pay {life_cost} life for {template.name} (life: {player.life})")
```

Also check AI targets Swiftspear/Slickshot with Mutagenic Growth when attacking (prowess context).

**Files:** `engine/game_state.py` → phyrexian mana payment block

**Verify:**
```bash
python run_meta.py --verbose prowess energy -s 50700 2>/dev/null | grep -E "Phyrexian|Mutagenic"
```

---

## Audit Summary — All 16 Decks

| Card | Deck | Status | Priority |
|---|---|---|---|
| Ajani transform trigger | Boros | ❌ Missing | P0 — in TRANSFORM_FIX_PLAN |
| Fable Ch.II/III | Jeskai | ❌ Missing | P0 — in TRANSFORM_FIX_PLAN |
| Ral coin flip → deterministic | Storm | ⚠️ Wrong | P1 — in TRANSFORM_FIX_PLAN |
| Endbringer {T}: ping / draw | Tron | ❌ Missing | P1 — Fix 8 above |
| Lava Dart flashback sacrifice | Prowess | ❌ Missing | P2 — Fix 9 above |
| Mutagenic Growth phyrexian log | Prowess | ⚠️ Silent | P3 — Fix 10 above |
| Ragavan attack threshold | Boros | ⚠️ Suppressed | P1 — Fix 1 above |
| Goblin Bombardment lethal-push | Boros | ⚠️ Reactive | P1 — Fix 2 above |
| creature_value blank snapshot | All | ⚠️ Wrong | P1 — Fix 3 above |
| Continuous clock / exp urgency | All | ⚠️ Cliff | P2 — Fix 4+5 above |
| Living End post-combo push | Living End | ⚠️ Reverts | P1 — Fix 6 above |
| Sheoldred | Dimir | ✅ Works (SB only) | — |
| Solitude/Subtlety evoke | Blink | ✅ Works | — |
| Ephemerate blink | Blink | ✅ Works | — |
| Goryo's Vengeance + exile EOT | Goryo's | ✅ Works | — |
| Archon full trigger | Goryo's | ✅ Works | — |
| Chalice of the Void | Tron | ✅ Works | — |
| All Is Dust | Tron | ✅ Works (rare draw) | — |
| Walking Ballista ping/grow | Tron | ✅ Works | — |
| Thought-Knot Seer exile | Tron | ✅ Works | — |
| Primeval Titan ETB+attack | Amulet | ✅ Works | — |
| Amulet of Vigor untap | Amulet | ✅ Works | — |
| Summoner's Pact upkeep | Amulet | ✅ Works | — |
| Territorial Kavu domain | Zoo | ✅ Works | — |
| Leyline Binding domain cost | Zoo | ✅ Works | — |
| Leyline of the Guildpact | Zoo | ✅ Works | — |
| Bowmasters opp draw drain | Dimir | ✅ Works | — |
| Psychic Frog combat draw | Dimir | ✅ Works | — |
| Murktide delve sizing | Dimir | ✅ Works | — |
| Cascade (Shardless/Demonic) | Living End | ✅ Works | — |
| Past in Flames flashback | Storm | ✅ Works | — |
| Ocelot Pride end-step token | Boros | ✅ Works | — |
| Swiftspear prowess | Prowess | ✅ Works | — |
| Undying Evil temp keyword | Goryo's | ✅ Works | — |
| Griselbrand pay 7 draw 7 | Goryo's | ✅ Works | — |
