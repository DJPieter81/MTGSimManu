# MTGSimManu — Oracle Refactor Plan v2 (Aggressive)

> **Session type:** Architecture-first. Read this file completely before writing any code.
> **Supersedes:** ORACLE_REFACTOR_PLAN.md
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Goal:** Reduce card_effects.py from 115 handlers / 2,776 lines to ~20 handlers / ~500 lines. Everything else moves to oracle_resolver.py pattern matching.

---

## The architecture after this refactor

```
BEFORE:                            AFTER:
card_effects.py  2,776 lines       card_effects.py   ~500 lines
  115 handlers                       ~20 handlers (truly unique mechanics only)
oracle_resolver.py 481 lines       oracle_resolver.py ~900 lines
  ~15 patterns                       ~40 patterns (covers 95% of cards)
```

Resolution order stays the same: `EFFECT_REGISTRY` fires first, then `oracle_resolver` as fallback. After the refactor, most cards have NO registry handler — oracle handles them. The ~20 remaining registry handlers are for mechanics that genuinely cannot be expressed by oracle pattern matching.

---

## What stays in card_effects.py (truly unique mechanics)

These ~20 cards have state-machine logic, zone interactions, or multi-step chains that can't be oracle-detected:

| Card | Why |
|---|---|
| Goryo's Vengeance | EOT exile registration, haste grant, legendary selection |
| Scapeshift | Library search + Valakut trigger chain |
| Wish | Sideboard zone access |
| Isochron Scepter | Imprint state stored on permanent, activated ability |
| Phelia, Exuberant Shepherd | Blink-on-attack + delayed ETB return at end step |
| Blood Moon | Continuous layer effect (land type change) |
| Walking Ballista | X-cost enters with counters, activated ping |
| Cranial Plating | Equip cost override (1 not generic), artifact-count scaling |
| Orcish Bowmasters | "Whenever an opponent draws" — trigger zone is unusual |
| Engineered Explosives | Sunburst counter accumulation, X-targeted destroy |
| Ephemerate | Rebound + exile state tracking |
| Snap / Snappy | Flashback grant on ETB |
| Griselbrand | Activated ability on battlefield (pay 7 life, draw 7) |
| Ratchet Bomb | Tick-up-over-time, destroy-CMC=X |
| Past in Flames | Give flashback to all GY instants/sorceries |
| Living End / _resolve_living_end | Mass ETB + exile, goal transition |
| Arcbound Ravager | Modular counter transfer on death |

Everything else — ~95 of the 115 handlers — can be deleted and replaced with oracle patterns.

---

## The oracle patterns to add

### 1. Universal damage targeting helper (highest priority)

```python
def _pick_damage_target(game, controller, amount):
    """Return best creature target for N damage, or None (go face).
    Oracle-driven threat scoring. No card names.
    """
    opp = game.players[1 - controller]
    killable = [c for c in opp.creatures
                if (c.toughness or 0) - getattr(c, 'damage_marked', 0) <= amount
                and (c.toughness or 0) > 0]
    if not killable:
        return None
    def score(c):
        val = (c.power or 0) + (c.toughness or 0) * 0.3
        o = (c.template.oracle_text or '').lower()
        if 'whenever this creature attacks' in o: val += 8.0
        if re.search(r'for each (artifact|creature|land)', o): val += 6.0
        val += max(0, (c.power or 0) - 3) * 0.8
        return val
    best = max(killable, key=score)
    return best if score(best) > amount * 0.5 else None
```

Apply in:
- ETB `'any target' in oracle` section — replace `opponent.life -= amount` with `_pick_damage_target`
- Attack trigger `'damage' in oracle` section — same
- **Delete** `phlage_etb` from card_effects (oracle_resolver handles it via ETB damage pattern)

### 2. "Deal N damage to any target" spells (Lightning Bolt, Lava Dart, Grapeshot)

These already use `targets[]` correctly in card_effects. Keep them — they're simple and correct. Just make sure `_choose_targets` in `ev_player.py` calls `_pick_damage_target`-equivalent logic (already done in session 4).

### 3. Scry N (Preordain, Sleight of Hand, Heroes' Hangout)

```python
# oracle_resolver.py resolve_spell_from_oracle:
m = re.search(r'\bscry\s+(\d+)\b', oracle)
if m:
    n = int(m.group(1))
    # Simplified: peek at top N, keep best N-1, bottom 1
    # (full scry simulation not needed — approximation is fine)
    game.draw_cards(controller, 0)  # trigger draw step if needed
    # AI keeps all (simplified) — no oracle_resolver action needed beyond card draw
```

Actually: Preordain is "scry 2, draw 1" — the draw 1 is already caught by the generic draw pattern. The scry is a look-ahead filter. For simulation purposes, approximate scry as "draw" since the AI doesn't model deck order. **Delete Preordain, Sleight of Hand — draw pattern handles them.**

### 4. "Exile top N cards, may play until end of turn" (Reckless Impulse, Wrenn's Resolve, Galvanic Relay, Glimpse the Impossible)

```python
# In resolve_spell_from_oracle:
if 'exile' in oracle and 'may play' in oracle and ('until end of turn' in oracle or 'this turn' in oracle):
    m = re.search(r'exile the top (\w+|\d+) cards?', oracle)
    count = word_to_num.get(m.group(1), 2) if m else 2
    # Simplified: exile = draw (cards are accessible this turn)
    game.draw_cards(controller, count)
    game.log.append(f"T{game.display_turn} P{controller+1}: {card.name}: exile {count} → draw {count}")
```

**Delete:** Reckless Impulse, Wrenn's Resolve, Galvanic Relay, Glimpse the Impossible, March of Reckless Joy, Valakut Awakening.

### 5. Draw N cards (Wall of Omens, Thought Monitor, Omnath, Quantum Riddler, Eternal Witness partial)

Already in oracle_resolver ETB draw pattern. **Delete** these simple ETB draw handlers.

### 6. Destroy target nonland permanent / exile target (Prismatic Ending, March of Otherworldly Light, Abrupt Decay, Assassin's Trophy, Celestial Purge, Wear//Tear)

```python
# In resolve_spell_from_oracle:
# "exile target nonland permanent" / "destroy target nonland permanent"
if ('exile target' in oracle or 'destroy target' in oracle) and 'nonland' in oracle:
    opp = game.players[1 - controller]
    nonland = [c for c in opp.battlefield if not c.template.is_land]
    if nonland:
        # Pick highest threat value (uses _permanent_threat_value pattern)
        best = max(nonland, key=lambda c: _permanent_threat_value(c, opp))
        opp.battlefield.remove(best)
        best.zone = 'exile' if 'exile' in oracle else 'graveyard'
        (opp.exile if 'exile' in oracle else opp.graveyard).append(best)
        game.log.append(f"T{game.display_turn} P{controller+1}: {card.name} exiles/destroys {best.name}")
```

Where `_permanent_threat_value` is the oracle-driven function from the v2 AI plan. **Delete:** Prismatic Ending, March of Otherworldly Light, Abrupt Decay, Celestial Purge — keep Wear//Tear and Assassin's Trophy if they have special logic.

### 7. Reanimate (Goryo stays, but Persist, Unburial Rites)

```python
# "Return target creature card from a graveyard to the battlefield"
if 'return target creature card' in oracle and 'graveyard' in oracle and 'battlefield' in oracle:
    gy = game.players[controller].graveyard
    creatures = [c for c in gy if c.template.is_creature]
    if creatures:
        best = max(creatures, key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
        game.reanimate(controller, best)
```

**Delete:** Persist, Unburial Rites (both do this exact thing).

### 8. Return target permanent to hand (Sink into Stupor, Hurkyl's Recall partial)

```python
# "Return target nonland permanent to its owner's hand"
if 'return target' in oracle and 'hand' in oracle and 'nonland permanent' in oracle:
    opp = game.players[1 - controller]
    nonland = [c for c in opp.battlefield if not c.template.is_land]
    if nonland:
        best = max(nonland, key=lambda c: _permanent_threat_value(c, opp))
        opp.battlefield.remove(best)
        best.zone = 'hand'
        game.players[best.controller].hand.append(best)
```

### 9. Create N tokens (Empty the Warrens, Ajani Cat Token, token ETBs)

Most token creation is already in oracle_resolver. Verify patterns cover:
- "Create N X/X [type] token(s)" — already there
- "Create a 2/1 [type] token" — check the regex covers it

**Delete:** Empty the Warrens (2 Goblin tokens on storm), Ajani ETB (1 Cat token) — both match the existing `'create' in oracle and 'token' in oracle` pattern if the regex is robust enough.

### 10. Mana production spells (Pyretic Ritual, Desperate Ritual, Manamorphose)

These are handled by the mana production system already (`_produce_mana_from_oracle` or similar). Verify they fire without the card_effects handlers. If not, the mana system needs a one-time fix, then **delete** these handlers.

---

## Execution order

```
Step 0: Regression baseline
  python run_meta.py --matrix -n 10 --save
  Record all 16 WRs.

Step 1: Add _pick_damage_target() to oracle_resolver
  Update ETB damage section + attack trigger damage section.
  Delete phlage_etb from card_effects.
  VERIFY: Phlage kills Signal Pest T3 vs Affinity.

Step 2: Delete simple draw/discard handlers
  Candidates: Wall of Omens, Thought Monitor, Omnath, Quantum Riddler,
              Preordain, Sleight of Hand, Wrenn's Resolve, Reckless Impulse,
              Galvanic Relay, Glimpse the Impossible, Thoughtseize (already in oracle),
              Manamorphose draw part.
  For each: confirm oracle_resolver fires, then delete.
  VERIFY after each group: run --matchup for decks using those cards, WR stable.

Step 3: Delete simple removal/exile handlers
  Candidates: Prismatic Ending, March of Otherworldly Light, Abrupt Decay,
              Celestial Purge, Sink into Stupor, Assassin's Trophy.
  Add generic patterns to oracle_resolver if missing.
  VERIFY: removal still fires correctly in matchups.

Step 4: Delete simple reanimate handlers
  Persist, Unburial Rites → generic oracle reanimate pattern.
  VERIFY: Goryo's Vengeance deck WR stable.

Step 5: Delete token creation handlers
  Empty the Warrens, Ajani Cat ETB → verify existing token pattern covers them.
  VERIFY: Storm and Boros deck WR stable.

Step 6: Delete mana production handlers
  Pyretic Ritual, Desperate Ritual, Manamorphose mana → verify mana system.
  VERIFY: Storm WR stable.

Step 7: Full regression matrix
  python run_meta.py --matrix -n 20 --save
  No deck should change >5pp from baseline.

Step 8: Audit remaining handlers
  List what's left in card_effects.py.
  Add comment to each: "UNIQUE: [reason why this can't be oracle-detected]"
  Target: ≤25 handlers remain.
```

---

## Rules

1. **Delete one group at a time. Verify. Then proceed.** Don't bulk-delete 30 handlers in one commit.
2. **No card names in oracle_resolver patterns.** If you find yourself writing `if card.name == 'X'` in oracle_resolver, stop — that belongs in card_effects.
3. **The test is WR stability, not individual play correctness.** A ±3pp WR change after deleting a handler is acceptable noise at n=10. A ±10pp change means the oracle pattern is wrong or missing.
4. **Don't rewrite the mana system.** Ritual mana production is complex. If Pyretic Ritual removal breaks Storm >10pp, restore it and mark it as a deferred task.
5. **Commit after each step.** One commit per step, message format: `refactor(oracle): delete [card group], oracle pattern covers [pattern name]`

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Step 0: baseline matrix n=10, record all WRs
- [ ] Step 1: `_pick_damage_target`, delete phlage_etb, verify
- [ ] Step 2: delete draw/discard handlers (group at a time), verify each
- [ ] Step 3: delete removal/exile handlers, verify
- [ ] Step 4: delete reanimate handlers, verify
- [ ] Step 5: delete token handlers, verify
- [ ] Step 6: delete mana handlers, verify
- [ ] Step 7: full matrix n=20, confirm ≤5pp drift on all decks
- [ ] Step 8: audit remaining handlers, add UNIQUE comments
- [ ] `python -c "import re; txt=open('engine/card_effects.py').read(); print(len(re.findall(r'EFFECT_REGISTRY.register', txt)), 'handlers remaining')`
- [ ] Update PROJECT_STATUS.md with handler count before/after
- [ ] `git push origin main`
