# Zoo vs Dimir Bug Fix Report

## Summary

Fixed 8 bugs across the game engine and AI decision-making system. Dimir Midrange win rate improved from approximately 0% to 33% against Domain Zoo over 30 games, with average game length increasing from ~7 turns to 14.1 turns.

| Metric | Before Fixes | After Fixes |
|--------|-------------|-------------|
| Dimir Win Rate | ~0% | 33% (10/30) |
| Zoo Win Rate | ~100% | 67% (20/30) |
| Avg Game Length | ~7 turns | 14.1 turns |
| Avg Dimir Spells/Game | ~2 | 5.4 |
| Avg Zoo Spells/Game | ~3 | 5.8 |

---

## Bugs Fixed

### Bug 1 (CRITICAL): `_concern_survive` filters ALL Dimir creatures as "combo pieces"

**File:** `ai/spell_decision.py`

**Root Cause:** The SURVIVE concern filters out "combo pieces" (cards in any goal's `card_roles`) from survival candidates. For combo decks (Storm, Living End), this prevents wasting key pieces. But for Dimir Midrange, ALL creatures (Bowmasters, Voidwalker, Psychic Frog, Murktide) are classified as enablers/payoffs across goals. This meant when `am_dying=True`, the AI could only play non-creature spells like Consider and Counterspell — never deploying blockers.

**Fix:** Added archetype check — only filter combo pieces for actual combo archetypes (`COMBO`, `STORM`). For midrange/aggro/control, enablers ARE the survival plays.

### Bug 2: `_advance_reactive` only deploys threats on empty boards

**File:** `ai/spell_decision.py`

**Root Cause:** The DISRUPT goal's `_advance_reactive` function only deployed creatures when `not ctx.opp.creatures` (opponent has no creatures). Against Zoo, this was never true after T1.

**Fix:** Added fallback: also deploy threats when under pressure (`am_dying`) and we have no board presence, or when we have mana-efficient creatures to deploy alongside interaction.

### Bug 3: `_should_hold_for_interaction` too aggressive for midrange

**File:** `ai/spell_decision.py`

**Root Cause:** The function held mana open for interaction even when the player was dying and had no board presence. Midrange decks need to deploy threats before holding up interaction.

**Fix:** Never hold for interaction when `am_dying` is true and we have no creatures on the battlefield.

### Bug 4: Fatal Push `_can_kill` ignores CMC restrictions

**File:** `ai/spell_decision.py`

**Root Cause:** `_can_kill` returned True for any "destroy" spell regardless of CMC restrictions. Fatal Push can only destroy CMC ≤ 2 (or CMC ≤ 4 with revolt), but the AI would target Scion of Draco (CMC 12).

**Fix:** Added CMC awareness to `_can_kill` for conditional destroy effects. Checks oracle text for "mana value" restrictions and applies the correct CMC ceiling.

### Bug 5: Revolt tracking incomplete for fetch lands

**File:** `engine/game_state.py`

**Root Cause:** `_crack_fetchland` didn't increment `creatures_died_this_turn` (the revolt proxy). While fetch lands don't create creature deaths, the permanent leaving the battlefield should enable revolt.

**Fix:** Added `permanents_left_this_turn` tracking in `_crack_fetchland` to properly enable revolt for Fatal Push.

### Bug 6: Flash creatures with removal tag not deployed at EOT

**File:** `engine/game_runner.py`

**Root Cause:** In `_cast_instant_removal`, Bowmasters (flash + removal) was categorized as `instant_removal` only. If its 1-damage ETB couldn't kill any creature, it wouldn't be deployed at all — even though deploying a 3/3 body at EOT is valuable.

**Fix:** Flash creatures with removal tag are also added to `flash_creatures` as a fallback, so they can be deployed for their body even when the removal doesn't kill.

### Bug 7: Emergency re-include adds counterspells during main phase

**File:** `ai/spell_decision.py`

**Root Cause:** When `am_dying=True`, the emergency re-include path added ALL reactive-only cards back to the castable pool, including Counterspell. This led to Counterspell being cast proactively during the main phase (targeting nothing meaningful).

**Fix:** Emergency re-include skips counterspells during the main phase — they're only useful on the stack.

### Bug 8: Subtlety ETB not implemented + wasteful evoke targeting

**Files:** `engine/card_effects.py`, `ai/board_eval.py`, `engine/game_state.py`

**Root Cause:** Three related issues:
1. Subtlety had no registered ETB handler — evoking it did nothing
2. `_eval_evoke` didn't check if the ETB had valid targets
3. No last-minute target check before committing to evoke

**Fix:**
1. Added `subtlety_etb` handler that bounces the best opponent creature to top of library
2. Added target validation in `_eval_evoke` — won't evoke if opponent has no creatures
3. Added last-minute target check in `cast_spell` before committing to evoke

---

## Verification Results (30 games, seeds 50000-64500)

### Win Distribution
- Zoo wins: 20 (67%)
- Dimir wins: 10 (33%)

### Dimir Win Turns
9, 13, 13, 14, 14, 15, 16, 17, 17, 22 (avg 15.0)

### Zoo Win Turns
7, 9, 9, 11, 11, 11, 12, 13, 13, 13, 13, 13, 14, 15, 15, 16, 17, 17, 21, 22 (avg 13.6)

### Fatal Push Targeting (Verified Correct)
- Ragavan (CMC 1): correctly targeted
- Nishoba Brawler (CMC 2): correctly targeted
- Territorial Kavu (CMC 2): correctly targeted
- Orcish Bowmasters (CMC 2): correctly targeted
- Scion of Draco (CMC 12): never targeted (correct!)

### Subtlety Evoke Targeting (Verified Correct)
All evokes now bounce a real creature:
- Territorial Kavu, Scion of Draco, Orcish Bowmasters, Nishoba Brawler, Ragavan
- No empty-board evokes
- Subtlety also hard-cast when mana allows (getting 3/3 flying body + ETB)
