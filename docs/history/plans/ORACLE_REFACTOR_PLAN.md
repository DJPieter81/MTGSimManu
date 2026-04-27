# MTGSimManu — Oracle Refactor Plan

> **Session type:** Architecture-first. Read this entire file before writing any code.
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Goal:** Eliminate hardcoded card-specific logic from the engine. All card behaviour derived from oracle text, keywords, and card type fields.

---

## The problem

`engine/card_effects.py` contains 115 `EFFECT_REGISTRY.register()` handlers — 2,776 lines of card-by-card logic. Two failure modes:

1. **Wrong behaviour** (Phlage ETB, oracle attack damage triggers) — hardcoded to `opponent.life -= N` when oracle says "any target". A creature on T3 would be the correct target but the AI hits face.
2. **Maintenance cost** — every new deck requires adding handlers. The engine can't handle any card not explicitly registered.

`engine/oracle_resolver.py` already handles ~15 generic patterns correctly (ETB draw, ETB lifegain, attack triggers, bounce lands, etc). The refactor is: expand `oracle_resolver.py`, delete card-specific duplicates from `card_effects.py`.

---

## Scope analysis

### Category 1 — Already handled generically in oracle_resolver (DELETE from card_effects)

These handlers in card_effects.py duplicate logic that oracle_resolver already covers. Safe to delete after confirming oracle_resolver fires correctly.

| Pattern | oracle_resolver coverage | card_effects duplicates |
|---|---|---|
| ETB draw a card | ✅ `'enters' in oracle and 'draw' in oracle` | Sleight of Hand, Preordain, Wall of Omens, Reckless Impulse, Wrenn's Resolve |
| ETB deal N damage | ✅ line 105 — but hardcodes face | Phlage ETB (hardcodes face) |
| Attack trigger damage | ✅ line 234 — but hardcodes face | Phlage attack, Archon attack |
| ETB discard | ✅ Thoughtseize pattern | Thoughtseize (card_effects has a duplicate) |

### Category 2 — Generic pattern exists, but targeting is wrong (FIX in oracle_resolver)

These work mechanically but always hit face instead of choosing the best target. The fix: add a `_pick_damage_target(game, controller, amount)` helper that returns the best killable creature or falls back to face. Used by all "N damage to any target" patterns.

```python
def _pick_damage_target(game, controller, amount):
    """Oracle-driven: pick best target for N damage.
    Prefers killable creatures with high threat score.
    Returns instance_id of target creature, or None (= go face).
    """
    opp = game.players[1 - controller]
    import re as _re
    killable = [
        c for c in opp.creatures
        if (c.toughness or 0) - getattr(c, 'damage_marked', 0) <= amount
    ]
    if not killable:
        return None
    
    def threat_score(c):
        val = (c.power or 0) + (c.toughness or 0) * 0.3
        oracle = (c.template.oracle_text or '').lower()
        if 'whenever this creature attacks' in oracle: val += 8.0
        if _re.search(r'for each (artifact|creature|land)', oracle): val += 6.0
        val += max(0, (c.power or 0) - 3) * 0.8
        return val
    
    best = max(killable, key=threat_score)
    # Only target creature if its threat score exceeds face-burn value
    face_value = amount * 0.5  # rough face value
    if threat_score(best) > face_value:
        return best
    return None
```

This single function, added to `oracle_resolver.py`, replaces the targeting logic in:
- ETB "deal N damage to any target" (oracle_resolver line 110)
- Attack trigger "deal N damage" (oracle_resolver line 235)
- card_effects Phlage ETB
- card_effects Lightning Bolt, Lava Dart (already use targets[] correctly — leave alone)

### Category 3 — Genuinely unique, keep in card_effects

These cards have mechanics that require bespoke logic because no oracle pattern can fully express them:

| Card | Why it must stay | Notes |
|---|---|---|
| Goryo's Vengeance | EOT exile timing, give_haste | State machine logic |
| Scapeshift | Library search + Valakut trigger chain | Complex multi-step |
| Wish | Sideboard access | Unique zone interaction |
| Isochron Scepter | Imprint + activate | State stored on permanent |
| Phelia | Blink-on-attack + ETB return | Complex timing |
| Blood Moon | Continuous effect | Land type change |
| Walking Ballista | X-cost + activated ability | Dynamic P/T |
| Cranial Plating | Equip cost override + scaling P | Activated + continuous |
| Orcish Bowmasters | On-draw trigger | Unusual trigger zone |
| Ephemerate | Rebound | Graveyard/exile state |

These are ~25-30 cards. Everything else is a candidate for oracle_resolver.

### Category 4 — Simple wrappers, collapse to oracle patterns

34 handlers flagged as ≤8 lines. Most just call existing functions or do simple arithmetic. Examples:

- `Thoughtseize` — oracle already has "target opponent reveals hand, you choose nonland, they discard" → already handled in oracle_resolver. **Delete.**
- `Preordain` / `Sleight of Hand` — "look at top N cards, put M on bottom, rest in order" → add generic `scry_and_draw` oracle detection. **Delete card-specific, add oracle pattern.**
- `Manamorphose` — "add 2 mana of any color, draw a card" → oracle: `add` + mana symbols + `draw a card`. **Delete, handle in oracle mana production.**
- `Pyretic Ritual` / `Desperate Ritual` — mana production from oracle. **Already handled in generic mana system?** Verify.

---

## Implementation plan

### Phase 1 — Add `_pick_damage_target()` to oracle_resolver (1 day)

This is the highest-value fix. Phlage killing Signal Pest instead of hitting face is the concrete example, but it affects every "any target" effect.

1. Add `_pick_damage_target(game, controller, amount)` to `oracle_resolver.py`
2. Update the ETB damage section (line 105-113): call `_pick_damage_target` instead of hardcoding face
3. Update the attack trigger damage section (line 234-240): same
4. In `card_effects.py` `phlage_etb`: remove the `game.players[opponent].life -= 3` hardcode, call the oracle_resolver helper instead OR delete the ETB handler entirely and let oracle_resolver handle it

**Verify:**
```bash
python run_meta.py --bo3 energy affinity -s 60100 2>&1 | grep "Phlage.*damage\|Phlage.*Signal\|Phlage.*Memnite"
# Expected: Phlage kills Signal Pest or Memnite on T3, not hits face
python run_meta.py --matchup energy zoo -n 20 2>&1 | tail -3
# No regression — Phlage still hits face when no killable target exists
```

### Phase 2 — Delete duplicate ETB handlers (1 day)

For each of the 34 "simple" handlers, verify oracle_resolver already fires the same logic. If yes, delete the card_effects handler. The EFFECT_REGISTRY is checked AFTER oracle_resolver, so deleting a handler means oracle_resolver takes over.

Work through them in groups:
- **Group A (draw):** Preordain, Sleight of Hand, Wall of Omens — oracle: `'draw' in oracle`
- **Group B (discard):** Thoughtseize — oracle already handles it
- **Group C (mana):** Manamorphose, Pyretic Ritual, Desperate Ritual — verify mana system handles them

For each deletion:
```bash
python run_meta.py --matchup [deck using card] [opponent] -n 10 2>&1 | tail -3
# WR should not change >5pp
```

### Phase 3 — Add missing oracle patterns for common effects (2 days)

Add to `oracle_resolver.py`:

```python
# Scry N: "look at top N cards, put any number on bottom, rest on top"
if re.search(r'scry\s+(\d+)', oracle):

# Modal spells with draw/damage/bounce modes (Thraben Charm, Kolaghan's Command)
# These stay in card_effects for now — too complex for Phase 3

# "When this enters, create N tokens"
if 'enters' in oracle and 'create' in oracle and 'token' in oracle:

# "Whenever you draw a card" triggers
if 'whenever you draw' in oracle:
```

### Phase 4 — Audit remaining handlers, mark truly unique (1 day)

After Phases 1-3: run `python run_meta.py --matrix -n 10` and compare to baseline. For any deck whose WR changed >5pp, trace the change to a deleted handler and either restore or improve the oracle detection.

Update `PROJECT_STATUS.md` with a "oracle coverage" metric: what % of cards in the current 16 decks are handled by oracle_resolver vs card_effects.

---

## Rules for the refactor

1. **Never delete a handler without verifying oracle_resolver covers it.** Add a test: `python run_meta.py --verbose [deck] [opp] -s SEED | grep [card name]` — confirm the effect still fires.

2. **oracle_resolver is the fallback for ETB and attack triggers.** card_effects handlers override it. Deleting a handler means oracle takes over — verify oracle is correct first.

3. **The 25-30 "genuinely unique" cards stay in card_effects.** Don't try to oracle-ify Scapeshift or Isochron Scepter. The goal is removing the ~80 handlers that are wrappers around patterns oracle already knows.

4. **`_pick_damage_target` must be oracle-driven inside.** No card names in the threat scoring. Use the same oracle patterns as `_threat_score` in `ev_player.py`.

5. **Run the full regression suite after each phase.** Not just the affected deck.

---

## What this enables long-term

Once oracle_resolver handles ~80% of effects:
- New decks added via `import_deck.py` work without any `card_effects.py` edits
- The remaining ~20% in card_effects are genuinely unique mechanics, clearly documented
- `card_effects.py` shrinks from 2,776 lines to ~600
- `oracle_resolver.py` becomes the single source of truth for card behaviour

---

## Regression baseline (run before starting)

```bash
python run_meta.py --matrix -n 10 --save
# Save this as the baseline. Compare after each phase.

python run_meta.py --audit energy -n 40 2>&1 | grep "Win rate"
python run_meta.py --audit affinity -n 40 2>&1 | grep "Win rate"
python run_meta.py --audit storm -n 40 2>&1 | grep "Win rate"
```

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Run regression baseline matrix (n=10), save numbers
- [ ] Read: `CLAUDE.md`, `PROJECT_STATUS.md`, `engine/oracle_resolver.py` (full), `engine/card_effects.py` (ETB and attack sections)
- [ ] Phase 1: add `_pick_damage_target()`, update ETB + attack damage targeting
- [ ] Verify Phase 1: Phlage kills Signal Pest; no regression in 4 matchups
- [ ] Phase 2: delete duplicate simple handlers (group A draw, group B discard)
- [ ] Verify Phase 2: WR stable across affected decks
- [ ] Phase 3: add scry + token creation oracle patterns
- [ ] Verify Phase 3: regression suite
- [ ] Phase 4: audit remaining, mark truly unique in comments
- [ ] Update `PROJECT_STATUS.md` with oracle coverage %
- [ ] `git commit -m "refactor: oracle-driven damage targeting, remove duplicate ETB handlers"`
- [ ] `git push origin main`
