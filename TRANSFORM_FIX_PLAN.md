# MTGSimManu — Transform / Flip Card Fix Plan

> **Session type:** Targeted engine fixes. Read this file completely before writing code.
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Scope:** 4 DFC cards across 4 decks with missing or wrong transform implementations.

---

## Audit summary

| Card | Deck | Issue | Impact |
|---|---|---|---|
| Ajani, Nacatl Pariah | Boros Energy | Transform trigger not implemented | **High** — Cat dies every game, Ajani should flip constantly |
| Fable of the Mirror-Breaker | Jeskai Blink | Ch.II (loot) and Ch.III (transform) not implemented | **High** — Fable is Jeskai's key value engine |
| Ral, Monsoon Mage | Ruby Storm | Condition is deterministic (3 spells), not coin flip | **Medium** — Storm kills via Grapeshot but Ral as PW adds reach |
| The Legend of Roku | 4c Omnath | Ch.III transform creates hardcoded Avatar Roku token instead of actual back face | **Low** — minor card in the deck |

---

## Fix 1 — Ajani transform trigger

**File:** `engine/oracle_resolver.py` → `resolve_dies_trigger()` or new `resolve_cast_spell_trigger()` hook

**What's missing:** The oracle says `"Whenever one or more other Cats you control die, you may exile Ajani, then return him to the battlefield transformed"`. Nothing fires on `creature_dies`.

**The trigger pattern is generic:** `"whenever one or more other [Subtype] you control die"` + `"exile"` + `"return"` + `"transformed"`. Add it to `resolve_dies_trigger()` which fires when any creature dies.

```python
# In resolve_dies_trigger(), check CONTROLLER's other permanents for subtype-death triggers
def resolve_dies_trigger(game, card, controller):
    ...
    # ── "Whenever one or more other [Subtype] you control die → exile → return transformed" ──
    import re as _re_dt
    player = game.players[controller]
    for perm in list(player.battlefield):
        p_oracle = (perm.template.oracle_text or '').lower()
        # Detect: "whenever one or more other X you control die"
        m = _re_dt.search(r'whenever one or more other (\w+)s? you control die', p_oracle)
        if not m:
            continue
        subtype = m.group(1)
        # Check if the dying card matches the subtype
        dying_subtypes = [s.lower() for s in (card.template.subtypes or [])]
        if subtype not in dying_subtypes:
            continue
        # Check trigger has "return...transformed"
        if 'return' not in p_oracle or 'transformed' not in p_oracle:
            continue
        # Check perm is not already transformed
        if getattr(perm, 'is_transformed', False):
            continue
        # Transform: exile perm, return as planeswalker
        _transform_permanent(game, perm, controller)
```

```python
def _transform_permanent(game, perm, controller):
    """Generic: exile a permanent and return it transformed (DFC back face)."""
    player = game.players[controller]
    
    # Remove from battlefield
    if perm in player.battlefield:
        player.battlefield.remove(perm)
    perm.zone = 'exile'
    
    # Set transformed state — use back face data if available
    perm.is_transformed = True
    perm.damage_marked = 0
    
    # Set loyalty if back face is a planeswalker
    back_loyalty = perm.template.back_face_loyalty or 0
    if back_loyalty > 0:
        perm.loyalty_counters = back_loyalty
    
    # Return to battlefield
    perm.zone = 'battlefield'
    player.battlefield.append(perm)
    
    game.log.append(
        f"T{game.display_turn} P{controller+1}: "
        f"{perm.template.name} transforms! "
        f"(loyalty: {perm.loyalty_counters if back_loyalty else 'N/A'})"
    )
    
    # Fire ETB triggers for the transformed permanent
    game._handle_permanent_etb(perm, controller)
```

**For Ajani specifically:** back face is a 3-loyalty planeswalker. `back_face_oracle` contains:
- `[+2]`: Put +1/+1 counter on each Cat you control
- `[0]`: Create 2/1 Cat Warrior; if you control a red permanent, deal damage = creature count to any target
- `[-4]`: Each opponent chooses artifact/creature/enchantment/planeswalker, exile the rest

The planeswalker activation loop in `game_runner.py` already handles `is_transformed` cards by reading `back_face_oracle` — so once Ajani transforms, his loyalty abilities should activate automatically.

**Verify:**
```bash
python run_meta.py --verbose energy zoo -s 50001 2>&1 | grep -E "Ajani.*transform|transforms|Avenger|loyalty"
# Expected: "Ajani transforms! (loyalty: 3)" when a Cat dies
python run_meta.py --matchup energy affinity -n 20 2>&1 | tail -3
# No regression
python run_meta.py --audit energy -n 60 2>&1 | grep "Ajani"
# Target: Ajani delta improves from -0.28 toward positive
```

---

## Fix 2 — Fable of the Mirror-Breaker Ch.II and Ch.III

**File:** `engine/game_runner.py` → `_process_saga_chapters()`

**What's missing:** The saga handler only has a path for Urza's Saga (construct token + tutor) and a generic transform path (creates hardcoded 4/4 Avatar). Fable falls into neither.

Fable's chapters:
- **Ch.I** ✅ — ETB creates 2/2 Goblin Shaman with haste (in `card_effects.py`)
- **Ch.II** ❌ — "You may discard up to two cards, if you do draw that many" — standard loot, no implementation
- **Ch.III** ❌ — "Exile this Saga, return transformed" — falls into the generic transform path which creates a 4/4 Avatar Roku instead

**Fix for Ch.II:** Add a loot branch in `_process_saga_chapters`. Detect by oracle: `"discard"` + `"draw"` pattern in Ch.II text. Use the existing `_force_discard` + `draw_cards` infrastructure.

**Fix for Ch.III:** The back face `Reflection of Kiki-Jiki` is an enchantment with `{T}: Copy target non-legendary creature you control`. The transform path should:
1. Exile the Saga
2. Return `perm.is_transformed = True` to battlefield as an enchantment
3. The activated ability fires in `_activate_utility_artifacts` or a new `_activate_kiki_reflection` path

The back face oracle is empty in the DB (`back_face_oracle = ''`). Add the back face ability to the card data or handle it by name in the transformed permanent's activation. Since we can't add oracle data at runtime, add a specific handler for Kiki-style `{T}: Copy target non-legendary creature` that works generically for any such permanent.

```python
# In _process_saga_chapters, add Fable handling before the generic transform path
elif 'goblin shaman' in card_oracle or ('discard' in card_oracle and 'draw that many' in card_oracle):
    # Fable of the Mirror-Breaker
    if lore == 2:
        # Ch.II: discard up to 2, draw that many
        player_cards = len(player.hand)
        to_discard = min(2, player_cards)
        if to_discard > 0:
            drawn = game.draw_cards(active, to_discard)
            names = ", ".join(c.name for c in drawn)
            game.log.append(f"T{game.display_turn} P{active+1}: "
                           f"Fable Ch.II: loot {to_discard} (drew: {names})")
    elif lore >= 3:
        # Ch.III: transform into Reflection of Kiki-Jiki
        sagas_to_transform.append(card)
```

And update the transform handler to set Fable's back face correctly (mark it as a copy-maker enchantment).

**Verify:**
```bash
python run_meta.py --verbose blink energy -s 50015 2>&1 | grep -E "Fable Ch\.|Reflection|transform|Kiki"
# Expected: Ch.II loot fires, Ch.III transform fires
python run_meta.py --matchup blink dimir -n 20 2>&1 | tail -3
# No regression in Jeskai WR
```

---

## Fix 3 — Ral, Monsoon Mage transform condition

**File:** `engine/oracle_resolver.py` → `resolve_spell_cast_trigger()`

**What's wrong:** Ral's condition is `"if you've cast three or more instant and/or sorcery spells this turn"` — deterministic. The current code uses `_handle_coin_flip_transform` (50/50). This is wrong; Ral should transform reliably on the 3rd spell each turn.

**Fix:** In `resolve_spell_cast_trigger()`, replace the coin-flip branch with a spell-count check:

```python
# Replace coin-flip detection with conditional count
# OLD: if 'flip a coin' in oracle and 'instant or sorcery' in oracle:
# NEW: detect count-based transform
if ('exile' in oracle and 'transformed' in oracle
        and ('instant or sorcery' in oracle or 'instant and/or sorcery' in oracle)
        and permanent.template.is_creature
        and not getattr(permanent, 'is_transformed', False)):
    # Count spells cast this turn
    spells_cast = player.spells_cast_this_turn
    # Extract threshold from oracle: "three or more" = 3
    threshold = 3  # oracle-derived default
    import re as _re_ral
    m = _re_ral.search(r'(two|three|four|five|\d+) or more', oracle)
    if m:
        word_map = {'two': 2, 'three': 3, 'four': 4, 'five': 5}
        threshold = word_map.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else 3)
    if spells_cast >= threshold:
        _transform_permanent(game, permanent, caster_idx)
```

This is generic — works for Ral and any future card with `"if you've cast N or more [type] spells this turn → transform"`.

**Note on back face:** Ral's `back_face_oracle` is empty in the DB. After transform, `activate_planeswalker` will find no abilities. Add a fallback: if a transformed card has no back face oracle but has `back_face_loyalty > 0`, treat it as a `[+1] draw a card` / `[-1] deal 1 damage` generic planeswalker until the DB is updated.

**Verify:**
```bash
python run_meta.py --verbose "Ruby Storm" dimir -s 50052 2>&1 | grep -E "Ral.*transform|transforms|Prodigy"
# Expected: Ral transforms when 3+ spells cast in a turn
python run_meta.py --matchup storm dimir -n 20 2>&1 | tail -3
# Storm WR should improve slightly
```

---

## Fix 4 — Legend of Roku transform (low priority)

**File:** `engine/game_runner.py` → `_process_saga_chapters()` saga transform handler

**What's wrong:** Ch.III creates a hardcoded `Avatar Roku (4/4)` token. The actual Roku back face should be an Avatar with specific abilities.

**Fix:** When `back_face_oracle` is empty, keep the current behaviour (4/4 haste) as an approximation. This is acceptable for now — Roku is a minor card. Add a TODO comment.

---

## Implementation order

```
Fix 3: Ral condition (deterministic vs coin flip)          5 min, low risk
    → verify Ral transforms on 3rd spell

Fix 1: Ajani trigger + _transform_permanent helper         30 min
    → verify Ajani transforms when Cat Token dies

Fix 2: Fable Ch.II + Ch.III                               20 min
    → verify Fable loots on Ch.II, transforms on Ch.III

Fix 4: Roku (skip if time-constrained)                    5 min, cosmetic
```

---

## Robustness checklist

- [ ] `_transform_permanent()` is generic — no card names, works for any DFC
- [ ] The subtype-death trigger in `resolve_dies_trigger` is oracle-driven — detects "Cat", "Elf", any future subtype
- [ ] Ral threshold parsed from oracle text, not hardcoded as 3
- [ ] All transforms use existing `is_transformed` flag and `back_face_oracle` path
- [ ] `_handle_coin_flip_transform` is removed or gated behind a coin-flip oracle check

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `engine/oracle_resolver.py`, `engine/game_runner.py` (`_process_saga_chapters`), `engine/card_effects.py` (Ajani ETB, Fable ETB)
- [ ] Fix 3: Ral deterministic transform
- [ ] Verify Ral transforms on 3rd spell in Storm verbose trace
- [ ] Fix 1: Ajani trigger + `_transform_permanent` helper
- [ ] Verify Ajani transforms when Cat dies; check loyalty abilities activate
- [ ] Fix 2: Fable Ch.II loot + Ch.III transform
- [ ] Verify Fable Ch.II fires, Ch.III transform fires
- [ ] Regression: `python run_meta.py --matrix -n 10 --save`
- [ ] `python run_meta.py --audit energy -n 60 2>&1 | grep "Ajani"` — confirm delta improves
- [ ] Update `PROJECT_STATUS.md`
- [ ] `git commit -m "fix(transform): Ajani subtype-death trigger, Fable Ch.II loot + Ch.III, Ral deterministic condition"`
- [ ] `git push origin main`
