# MTGSimManu — Control Deck Fix Plan

> **Session type:** AI strategy + targeted fixes. Read this file before coding.
> **Pre-session:** `git pull && python merge_db.py && read CLAUDE.md && read PROJECT_STATUS.md`
> **Affected decks:** Azorius Control (14.8% → target ~35%), Azorius Control WST (30.7% → target ~45%)

---

## Root cause analysis

### Azorius Control (14.8%) — audit findings

**What works:** Counterspell responses fire correctly. Solitude exile works. Teferi Time Raveler bounce works. Isochron Scepter copies spells. Lorien Revealed cycles. Prismatic Ending exiles.

**What's broken:**

**1. Orim's Chant delta: −1.70 (worst card in any deck audited)**
Cast T7.5 avg — should be T2-4. Cast rate: 1.1x. WinCR: **3%**. Appears in **84% of losses**. This is the single biggest signal in the entire codebase: a card appearing in almost every loss while almost never in wins. The AI is casting Orim's Chant at sorcery speed in main phase doing nothing, instead of holding it as a response or using it with Isochron Scepter. Isochron Scepter imprinting Orim's Chant is the deck's entire lock plan; the AI casts Orim's Chant proactively and then has nothing to imprint.

**Fix:** `Orim's Chant` should only be cast:
- From Isochron Scepter activation (already works)
- As a response to opponent declaring attackers (prevent attacks this turn)
- Never proactively in main phase unless Isochron is on board and has no imprint

Add `"chant"` tag and gate proactive cast behind Isochron check.

**2. Supreme Verdict / Wrath of the Skies delta: −0.26 / −0.37**
Both WinCR ≤ 20%. Board wipes cast T6.4-T4.8 avg — fine. But WinCR is near zero. The deck has no creatures to follow up the wipe with (just Sanctifier en-Vec and Teferi). After wiping, it can't close. Not a bug — the deck construction needs a better threat after wipes. Sanctifier en-Vec at +1.25 delta is carrying the deck.

**3. Counterspell delta: −0.74**
WinCR 44% but cast mostly in losing games. The AI is holding counterspells too long — they appear at T6.8 avg vs opponent key plays. This is the `control_patience` issue: the AI hoards mana for counters but opponents flood out or resolve threats before counter mana is available. Timing is correct (response fires) but the AI needs to be more willing to tap out early and counter the right threats.

**4. Avg win turn T10.2 vs avg loss turn T8.2**
The deck wins on timeout more than damage. When it wins, games go longer. This confirms the deck has no clock — it stabilises but can't close.

---

### Azorius Control WST (30.7%) — audit findings

**What works:** Chalice +0.22 delta (correct). Wan Shi Tong +1.04 delta (best card in deck when it grows). Teferi TTR +0.91 delta. Sanctifier en-Vec +0.55.

**What's broken:**

**1. Wan Shi Tong doesn't grow from Primeval Titan / tutor searches**
`_primeval_titan_search` never calls `_trigger_library_search`. WST sits at 1/1 in Amulet and other tutor-heavy matchups where it should be a 5/5+ by T5.

**Fix:** At end of `_primeval_titan_search`, call `game._trigger_library_search(controller)` once per land fetched:
```python
# After each land is added:
game._trigger_library_search(controller)  # triggers WST counter + draw
```

Also: Summoner's Pact, Expedition Map, and any effect that shuffles/searches should call `_trigger_library_search`. Audit all card_effects.py handlers that do `player.library.remove()` or `shuffle()` after a search.

**2. March of Otherworldly Light delta: −0.38, Prismatic Ending delta: −0.50**
Both removal spells appear more in losses. MoL is tagged `cost_reducer` (wrong!) and `mana_source` (wrong!) instead of `removal`. Fix tags:
- `March of Otherworldly Light` → add `"removal"`, `"exile_removal"` tags; remove `cost_reducer`, `mana_source`

**3. Wrath of the Skies delta: −0.45**
WST deck casts Wrath T6.4 avg but WinCR only 18%. The deck has creatures on board (WST, Sanctifier) that Wrath also kills. The AI is self-wiping. Need to gate Wrath behind "I have fewer/smaller creatures than opponent" check — same `_should_board_wipe` logic needed.

**4. Counterspell delta: −0.15**
Counterspell appears slightly more in losses than wins. Same control-patience timing issue as regular Azorius.

---

## Fix 1 — Primeval Titan search calls _trigger_library_search

**File:** `engine/card_effects.py` → `_primeval_titan_search()`

```python
def _primeval_titan_search(game, controller):
    ...
    for land in to_put:
        player.library.remove(land)
        land.enter_battlefield()
        ...
        game.log.append(...)
        game._trigger_library_search(controller)  # ← ADD THIS
    game.rng.shuffle(player.library)
```

Also audit these search effects and add `_trigger_library_search` where missing:
- `summoners_pact_resolve` — searches for creature
- `expedition_map_activate` (if implemented) — searches for land
- `prismatic_ending_resolve` — does NOT search, skip
- `fetch_land` (`_crack_fetchland`) — already calls it ✅

**Verify:**
```bash
python run_meta.py --verbose "Azorius Control (WST)" "Amulet Titan" -s 50901 2>/dev/null | grep -E "Wan Shi.*counter|counter.*Wan|WST triggers"
# Expected: WST grows each time Titan searches
python run_meta.py --audit "Azorius Control (WST)" -n 40 2>/dev/null | grep "Wan Shi Tong"
# Target: WST avg turn improves, delta improves above +1.04
```

---

## Fix 2 — March of Otherworldly Light tags

**File:** `engine/card_database.py` → hardcoded tag overrides

```python
"March of Otherworldly Light": {"removal", "exile_removal", "instant_speed", "interaction"},
```

This fixes the AI scoring it as a mana source / cost reducer and instead treating it as exile removal for targeting decisions.

**Verify:**
```bash
python run_meta.py --verbose "Azorius Control (WST)" "Affinity" -s 50900 2>/dev/null | grep "March.*exile"
# Target: MoL exiles Cranial Plating or SC instead of being cast proactively
```

---

## Fix 3 — Orim's Chant proactive cast gate

**File:** `ai/ev_player.py` or `engine/game_runner.py`

Orim's Chant should only be proactively cast if Isochron Scepter is on board with no imprint. Otherwise hold for response/Scepter imprint.

```python
# In _score_spell or play selection:
if template.name == "Orim's Chant":
    has_scepter_no_imprint = any(
        c.name == "Isochron Scepter" and not any("imprint:" in t for t in c.instance_tags)
        for c in player.battlefield
    )
    if not has_scepter_no_imprint:
        return -5.0  # Don't cast proactively
```

Or more generically: cards tagged `"silence"` should have zero proactive EV unless there's an Isochron imprint opportunity.

**Verify:**
```bash
python run_meta.py --audit "Azorius Control" -n 40 2>/dev/null | grep "Orim"
# Target: Orim's Chant avg cast turn increases to T5+ (via Scepter), delta improves from -1.70
```

---

## Fix 4 — Board wipe self-damage check

**File:** `engine/game_runner.py` or `ai/ev_player.py`

Board wipes (Wrath of God, Supreme Verdict, Wrath of the Skies) should check: am I putting myself further ahead by wiping? Don't wipe if I have more power on board than opponent.

```python
def _should_board_wipe(game, controller):
    me = game.players[controller]
    opp = game.players[1 - controller]
    my_power = sum(c.power or 0 for c in me.creatures)
    opp_power = sum(c.power or 0 for c in opp.creatures)
    # Only wipe if opponent has more total power than me
    return opp_power > my_power and len(opp.creatures) >= 2
```

Add to `_score_spell` for board_wipe tagged spells.

**Verify:**
```bash
python run_meta.py --audit "Azorius Control (WST)" -n 40 2>/dev/null | grep "Wrath\|Verdict"
# Target: Wrath WinCR improves from 18% toward 40%+
```

---

## Implementation order

```
Fix 2: MoL tags (2 min, no risk)
Fix 1: Primeval Titan search → WST trigger (10 min)
    → verify WST grows in Amulet matchup
Fix 3: Orim's Chant proactive gate (15 min)
    → verify Orim's avg cast turn increases
Fix 4: Board wipe self-check (20 min)
    → verify Wrath WinCR improves
Full audit both Azorius decks n=40
```

---

## Session checklist

- [ ] `git pull origin main`
- [ ] `python merge_db.py`
- [ ] Fix 2: MoL tags in `card_database.py`
- [ ] Fix 1: `_primeval_titan_search` + audit other search handlers for `_trigger_library_search`
- [ ] Verify: WST grows from Titan searches in verbose trace
- [ ] Fix 3: Orim's Chant proactive cast gate
- [ ] Verify: `python run_meta.py --audit "Azorius Control" -n 40` — Orim's delta improves
- [ ] Fix 4: Board wipe self-damage check
- [ ] `python run_meta.py --matchup "Azorius Control" affinity -n 20`
- [ ] `python run_meta.py --matchup "Azorius Control (WST)" "Amulet Titan" -n 20`
- [ ] `python run_meta.py --matrix -n 10 --save` — confirm no regressions
- [ ] Update `PROJECT_STATUS.md`
- [ ] `git commit -m "fix(control): Orim's Chant proactive gate, WST/Titan search trigger, MoL tags, board wipe self-check"`
- [ ] `git push origin main`

---

## Sideboard Coverage Fix — 6 decks with 0 sideboard changes

**Decks:** Ruby Storm, Affinity, Amulet Titan, Goryo's Vengeance, Living End, Pinnacle Affinity

**Root cause:** `sideboard_manager.py` only has 8 `board_in` conditions, all keyed on opponent archetype keywords. Cards with valid keyword matches fail because the opponent condition doesn't fire. 6 decks never sideboard at all in any matchup.

**Missing patterns:**

```python
# Pattern 1: Protective counterspells vs interactive decks (Goryo, LE, Storm, Amulet)
# Board in Force of Negation, Flusterstorm vs any deck with counters/removal
if any(w in opp_lower for w in ['control', 'dimir', 'jeskai', 'blink', 'energy',
                                  'zoo', 'prowess', 'omnath']):
    if any(w in card_lower for w in ['force of negation', 'flusterstorm', 'mystical dispute']):
        board_in_priority.append((card_name, count, 8))

# Pattern 2: Graveyard hate FROM combo decks (Goryo boards Leyline vs LE, Affinity boards Relic)
if any(w in opp_lower for w in ['goryo', 'living end', 'dredge', 'affinity', 'pinnacle']):
    if any(w in card_lower for w in ['leyline of the void', 'relic', 'rest in peace',
                                      'endurance', 'nihil', 'tormod', 'crypt']):
        board_in_priority.append((card_name, count, 9))

# Pattern 3: Hate artifacts board in OWN artifact hate (Affinity boards Hurkyl's vs mirror)
if any(w in opp_lower for w in ['affinity', 'pinnacle']):
    if any(w in card_lower for w in ['hurkyl', 'haywire', 'wear', 'meltdown']):
        board_in_priority.append((card_name, count, 8))

# Pattern 4: Extra win cons in Storm (more Grapeshot/Empty vs slower decks)
if any(w in opp_lower for w in ['control', 'tron', 'omnath', 'blink']):
    if any(w in card_lower for w in ['empty the warrens', 'grapeshot']):
        board_in_priority.append((card_name, count, 6))

# Pattern 5: Trinisphere from Amulet vs storm/cascade (must resolve before they combo)
if any(w in opp_lower for w in ['storm', 'living end', 'cascade']):
    if 'trinisphere' in card_lower:
        board_in_priority.append((card_name, count, 9))

# Pattern 6: Board wipes from Affinity vs token/wide decks
if any(w in opp_lower for w in ['energy', 'zoo', 'prowess']):
    if any(w in card_lower for w in ['brotherhood', 'meltdown', 'explosives', 'ratchet']):
        board_in_priority.append((card_name, count, 8))

# Pattern 7: Torpor Orb / Ethersworn Canonist from Affinity vs ETB/spell decks
if any(w in opp_lower for w in ['blink', 'omnath', 'jeskai', 'storm']):
    if any(w in card_lower for w in ['torpor', 'canonist', 'ethersworn']):
        board_in_priority.append((card_name, count, 7))
```

**Board-out patterns also need expansion** — currently only boards out removal vs combo, but many matchups need to board out dead cards (e.g., Affinity boards out Metallic Rebuke in non-counter matchups).

**Files:** `engine/sideboard_manager.py`

**Verify:**
```bash
python3 -c "
from engine.sideboard_manager import sideboard
from decks.modern_meta import MODERN_DECKS
for d1, d2 in [('Affinity','Goryo\\'s Vengeance'), ('Goryo\\'s Vengeance','control'),
               ('Living End','Boros Energy'), ('Amulet Titan','Ruby Storm')]:
    d = MODERN_DECKS[d1]
    m, s = sideboard(d['mainboard'], d['sideboard'], d1, d2)
    changes = sum(1 for c in set(list(m)+list(d['mainboard'])) if m.get(c,0) != d['mainboard'].get(c,0))
    print(f'{d1} vs {d2}: {changes} changes')
"
# Target: all 4 show >0 changes
```
