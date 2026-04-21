# Block Strategy Audit + Execution Plan

> **Purpose**: This document is a handoff brief for a coding agent (Claude Code
> in Plan Mode). It contains (1) a diagnostic of the blocking engine's
> failure modes with concrete evidence, and (2) an execution plan with
> contracts, test specs, and acceptance criteria suitable for a parallel
> implementation session.
>
> **Scope discipline**: this is about `ai/ev_player.py::decide_blockers` only.
> Do not touch `decide_main_phase`, `decide_attackers`, or the EV scoring
> engine. Do not introduce per-card special cases — detection must be oracle-
> or tag-driven.

---

## Part 1 — Diagnostic

### Evidence base

- 6 Bo3 replays, seeds 63500–66000 step 500, Boros Energy vs Affinity
- 14 games total, 66 block events parsed
- Raw data: `audits/block_audit_raw_data.md` (regenerate with
  `python tools/audit_blocks.py`)
- Trace files: `replays/boros_vs_affinity_trace_s{63500,64000,64500,65000,65500,66000}.txt`

### Summary — 63% of blocks are net-negative

| Bucket | Count | % |
|--------|-------|---|
| **C. Chump into scaled attacker (P≥10)** — delays 1 turn, attacker re-swings same/higher next | **26** | **39%** |
| **A. Premature chump** (no lethal threat, blocker dies for ≤1-turn delay) | **16** | **24%** |
| F. 1-for-1 trade | 7 | 11% |
| G. Favorable trade (good) | 7 | 11% |
| D. Emergency chump (genuine near-lethal defense) | 5 | 8% |
| E. Named blocker traded into token (bad value) | 3 | 5% |
| H. Tarpit block (survives, doesn't kill) | 2 | 3% |

Only **11% (7/66)** are unambiguously good. Buckets **A + C + E** (45 blocks,
68% of the sample) are the ones the fix must eliminate or reclassify.

### Five root causes

#### RC-1 — `biggest_attacker_power >= me.life // 2` fires bogus emergencies

Current code (`ai/ev_player.py`, around line 1649):

```python
emergency = (total_incoming >= me.life
             or (me.life - total_incoming <= 5 and total_incoming >= 3)
             or biggest_attacker_power >= me.life // 2)   # ← this clause
```

Fires at **life=20** vs any single 10-power attacker. Not an emergency —
one swing goes 20→10, we get a turn to respond.

Evidence (bucket A, at healthy life totals):
- s65000 T5 Boros: triple-block at life=20 vs opp life=5 (should be racing, not chumping)
- s66000 T4 Boros: Elemental + Pyromancer chump Sojourner (4/4) at life=17
- s63500 T4 Boros: Ajani chumps 4/4 at life=20

#### RC-2 — No multi-turn projection; plating rebinds and re-scales

When opponent controls equipment granting `"equipped creature gets +N"`
(Cranial Plating, etc.), chumping the attacker once does **not** solve the
problem. Next turn the plating re-attaches to any remaining creature.

Evidence (bucket C, same attacker chumped repeatedly):
- **s63500 T5→T8 Boros**: Warrior Tokens chump Sojourner's Companion across
  four turns, power scaling 14 → 26 → 24 → 26. Four permanents lost, attacker
  never dies, damage output unchanged.
- **s64500 T7→T9 Boros**: Guide → Pyromancer → Guide chump Construct Token
  (10/10, 10/10, 11/11). Three permanents lost, attacker never dies.
- **s66000 T4→T7 Boros**: Five separate chumps vs Construct Token scaling
  10 → 12 → 14 → 26. Never addresses plating.

#### RC-3 — Emergency loop stops at arithmetic stabilization, ignores residual equity

Current emergency loop condition:
```python
if remaining < me.life and (me.life - remaining > 5 or remaining == 0):
    break  # stabilized
```

Keeps assigning blockers one-by-one until *today's* damage is survivable,
regardless of how many permanents that costs.

Evidence:
- **s65500 T5 Boros**: commits FIVE blockers in one combat (Elemental × 2,
  Guide of Souls, Ajani, Pyromancer) at life=14. Empty board the next turn →
  lose outright.

#### RC-4 — Key pieces (engines, planeswalkers, escape creatures) chumped as fodder

Emergency path picks the "smallest" blocker by `creature_value`. That value
function already includes ETB/token-maker bonuses but clearly not enough;
premium pieces still get thrown away.

Evidence (bucket E + specific cases):
- **s64000 T7 Boros**: **Phlage, Titan of Fire's Fury** (6/6) chumps Germ Token (6/6).
  Both die. Phlage is a premier finisher with escape; trading for a token is catastrophic.
- **s65000 T2/T3 Boros**: **Ocelot Pride** (token engine) chumps Frogmite at life=20.
- **s65000 T6 Boros**: Phlage chumps Construct Token (8/7) — Phlage dies (6 < 7).
- **s65500 T7 Boros**: Phlage chumps Germ Token (10/10) — Phlage dies.

Note: the existing non-emergency path has a battle-cry-source filter but
the emergency path does not.

#### RC-5 — No "race instead" branch when offensive clock is close

Current "don't block when winning" test (line 1644):
```python
if my_untapped_power >= opp.life and total_incoming < me.life:
    return {}
```

Only fires for lethal-on-board. Does not consider "my clock-to-kill ≤ opp's
clock-to-kill adjusted for post-block board".

Evidence:
- **s65000 T5 Boros**: life=20 vs opp life=5, chumps three permanents instead
  of racing. Ragavan (2/1) could have attacked for face damage.

---

## Part 2 — Execution Plan

### Project constraints (from `CLAUDE.md` + `PROJECT_STATUS.md`)

These are **non-negotiable**:

- **No hardcoded card names anywhere in `ai/ev_player.py`** — detection must be
  oracle-text or tag-driven. Single source of truth is
  `card.template.oracle_text` and `card.template.tags`.
- **No patch-per-card**. Modern has 20K+ cards; any rule added must generalize.
- **Do not touch** `decide_main_phase`, `decide_attackers`, EV scoring, or
  `creature_value` / `creature_threat_value` in this session.
- **Git**: `git pull --rebase origin main` before every push. **Never** force-push.
- **Tests**: `python -m pytest tests/ -q` must be 100% green before push
  (baseline as of commit `f7b80f5`: 228/228).
- **Data provenance**: any sideboarding or matchup claim in commit messages
  must be backed by a real sim result, not inference.

### Primitives available (do not re-implement)

| Name | Location | What it gives you |
|------|----------|-------------------|
| `creature_value(card, snap=None)` | `ai/ev_evaluator.py:263` | clock-impact-based value including ETB/token-maker bonuses |
| `creature_threat_value(card, snap=None)` | `ai/ev_evaluator.py:283` | adds virtual-power for battle-cry and scaling (`for each …`) |
| `snapshot_from_game(game, player_idx)` | `ai/ev_evaluator.py` | `EVSnapshot` for the caller's POV |
| `permanent_threat(card, opp, game)` | `ai/permanent_threat.py` | marginal-contribution threat value (already drives target picking) |
| `Keyword.FLYING / REACH / DEATHTOUCH / TRAMPLE` | `engine/cards.py::Keyword` | enum values |
| `card.template.oracle_text` | `engine/cards.py::CardTemplate` | lowercased match source |
| `card.template.tags` | `engine/cards.py::CardTemplate` | e.g. `'removal'`, `'counterspell'`, `'reanimate'`, `'wrath'`, `'board_wipe'` |
| `card.instance_tags` | `engine/cards.py::CardInstance` | runtime tags (includes `equipped_<id>` on equipped creatures) |
| `game.get_valid_blockers(player_idx)` | `engine/game_state.py` | already used inside `decide_blockers` |
| `CardType.PLANESWALKER` | `engine/cards.py::CardType` | import via `from engine.cards import CardType` |

### Oracle patterns for detection (verified against the DB)

| Pattern | Matches | Used in |
|---------|---------|---------|
| `'equipped creature gets +'` | Cranial Plating, Colossus Hammer, Shadowspear, Embercleave | RC-2 |
| `'enchanted creature gets +'` | Ethereal Armor, Sixth Sense auras | RC-2 |
| `'escape—'` in oracle (em-dash) | Phlage, Uro, Kroxa (Theros: Beyond Death mechanic) | RC-4 |
| `'whenever this creature attacks'` | battle-cry sources (already used elsewhere) | RC-4 |
| `CardType.PLANESWALKER in card.template.card_types` | all planeswalkers | RC-4 |

Verified examples:
- Cranial Plating oracle: `Equipped creature gets +1/+0 for each artifact you control.` (plus equip cost)
- Phlage oracle contains: `Escape—{R}{R}{W}{W}, Exile five other cards from your graveyard.`

### Test scaffolding (copy this pattern)

Existing tests use `CardDatabase` fixture + helper functions. Use the same
pattern found in `tests/test_discharge_artifact_targeting.py:30-70`:

```python
import random, pytest
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState

@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()

def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card

def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card

def _attach_equipment(equipment, creature):
    equipment.instance_tags.discard("equipment_unattached")
    equipment.instance_tags.add("equipment_attached")
    creature.instance_tags.add(f"equipped_{equipment.instance_id}")
```

### File-level changes

All code changes are inside **`ai/ev_player.py::decide_blockers`** (currently
lines 1625–1809). No other files need edits except new tests.

Add new private helpers as methods on `EVPlayer` (keep them co-located; do
not create a new module):

- `_two_turn_lethal(self, game, me, opp, attackers) -> bool`
- `_attacker_equipment_bonus(self, game, opp, attacker) -> int`
- `_equipment_breakable(self, game, me) -> bool`
- `_is_protected_piece(self, card) -> bool`
- `_racing_to_win(self, game, me, opp, attackers) -> bool`

---

### Phase 1 — RC-1 + RC-3: tighten emergency gate and cap blocker commitment

**Problem**: emergency fires on 1-turn non-lethal damage; once emergency fires,
loop over-commits until arithmetic survives, regardless of board cost.

**Contract change**:

```python
def _two_turn_lethal(self, game, me, opp, attackers) -> bool:
    """True iff incoming damage this turn + estimated opp next-turn damage
    would kill us, assuming we take everything unblocked this turn.

    Next-turn damage estimate: sum of opp creatures that are NOT currently
    attacking (they're untapped and can swing next turn). Use effective
    power. Ignore summoning-sick creatures.
    """
    incoming = sum(a.power or 0 for a in attackers)
    attacking_ids = {a.instance_id for a in attackers}
    opp_next = sum(
        (c.power or 0) for c in opp.creatures
        if c.instance_id not in attacking_ids
        and not getattr(c, 'summoning_sick', False)
    )
    return incoming + opp_next >= me.life
```

Replace the `emergency` test (line ~1649) with:

```python
emergency = (total_incoming >= me.life                       # lethal this turn
             or (me.life - total_incoming <= 5 and total_incoming >= 3)  # kept
             or self._two_turn_lethal(game, me, opp, attackers))        # replaced RC-1 clause
```

**Portfolio cap** (RC-3): after each emergency blocker assignment, track the
cumulative sacrificed `creature_value`. If it exceeds the unblocked damage
we would otherwise take, stop assigning.

```python
from ai.ev_evaluator import creature_value

sacrificed_value = 0.0
for attacker in sorted_attackers:
    # … existing block-picking logic …
    if best_chump:
        sacrificed_value += creature_value(best_chump)
        blocked_damage = sum(
            a.power or 0 for a in attackers if a.instance_id in emergency_blocks
        )
        remaining = total_incoming - blocked_damage
        # Cap: stop if we've sacrificed more value than damage left to take
        if sacrificed_value > max(remaining, 1.0):
            break
        if remaining < me.life and (me.life - remaining > 5 or remaining == 0):
            break  # existing stabilized check
```

**Tests** (new file: `tests/test_decide_blockers_emergency_gate.py`):

```python
def test_no_emergency_when_single_swing_nonlethal(card_db):
    """Life=20, single Sojourner's Companion (4/4) attacks. No other threats.
    Expected: {} — 4 damage is not an emergency."""

def test_no_emergency_at_life20_vs_single_10_power(card_db):
    """Life=20, opp has a 10/10 attacking, no other opp creatures on board.
    Expected: {} — accept 10 damage, live at 10, respond next turn."""

def test_two_turn_lethal_still_triggers_emergency(card_db):
    """Life=5, 4/4 attacking + another 4/4 untapped opp creature ready next turn.
    Expected: emergency fires, at least one blocker assigned."""

def test_portfolio_cap_stops_over_commit(card_db):
    """Life=14, 4 attackers each 3/3 (12 total incoming).
    Boros has 5 small creatures (Cat Tokens).
    Expected: at most 2 blockers committed — sacrificing 3+ tokens
    to save 12 life when we're at 14 is worse than taking the hit."""
```

**Acceptance**:
- All 4 tests pass.
- Existing suite stays green.
- Regenerate audit: on the same 6 seeds, bucket A count drops from 16 → ≤ 4,
  bucket D count does not drop (real emergencies still handled).

---

### Phase 2 — RC-2: plating/equipment-aware projection

**Problem**: when the attacker's damage output is dominated by equipment /
auras, blocking once just means plating rebinds next turn. Current code treats
the attacker's power as intrinsic.

**Contract**:

```python
import re

_EQUIP_BONUS_RE = re.compile(
    r'(equipped|enchanted) creature gets \+(\d+)/\+(\d+)'
)

def _attacker_equipment_bonus(self, game, opp, attacker) -> int:
    """Sum of +power granted to `attacker` by currently-attached equipment
    or auras.

    Finds attached permanents by iterating opp.battlefield and checking
    whether `attacker.instance_tags` contains f'equipped_{perm.instance_id}'.
    For each match, parses '+X/+Y' from the permanent's oracle text.

    Also handles scaling bonuses ('+1/+0 for each artifact you control')
    by counting qualifying permanents.

    Returns 0 if no bonus-granting attachment is found.
    """
    bonus = 0
    attacker_equipment_ids = {
        int(tag.split('_')[1]) for tag in attacker.instance_tags
        if tag.startswith('equipped_') and tag.split('_')[1].isdigit()
    }
    if not attacker_equipment_ids:
        return 0

    for perm in opp.battlefield:
        if perm.instance_id not in attacker_equipment_ids:
            continue
        oracle = (perm.template.oracle_text or '').lower()
        m = _EQUIP_BONUS_RE.search(oracle)
        if not m:
            continue
        base_power = int(m.group(2))
        # Handle 'for each <thing> you control' scaling
        scale_match = re.search(
            r'for each (artifact|creature|land|card)', oracle
        )
        if scale_match:
            kind = scale_match.group(1)
            if kind == 'artifact':
                count = sum(
                    1 for c in opp.battlefield
                    if 'artifact' in str(c.template.card_types).lower()
                )
            elif kind == 'creature':
                count = len(opp.creatures)
            elif kind == 'land':
                count = len([c for c in opp.battlefield if c.template.is_land])
            else:
                count = len(opp.battlefield)
            bonus += base_power * count
        else:
            bonus += base_power
    return bonus

def _equipment_breakable(self, game, me) -> bool:
    """True iff we can remove the equipment / reset the board before
    it rebinds next turn. Checks `me.hand` for:
      - mass removal (tag 'wrath' or 'board_wipe')
      - artifact/enchantment destruction: tags contain 'removal' AND oracle
        contains 'destroy target artifact' or 'destroy target enchantment'
        or 'destroy target nonland permanent'
    """
    for card in me.hand:
        tags = getattr(card.template, 'tags', set()) or set()
        if 'wrath' in tags or 'board_wipe' in tags:
            return True
        oracle = (card.template.oracle_text or '').lower()
        if 'removal' in tags and (
            'destroy target artifact' in oracle
            or 'destroy target enchantment' in oracle
            or 'destroy target nonland permanent' in oracle
            or 'destroy all artifacts' in oracle
        ):
            return True
    return False
```

**Behaviour change**: in the emergency loop, when
`_attacker_equipment_bonus(attacker) >= 3` AND
`not _equipment_breakable(me)` AND the block is NOT preventing lethal,
skip assigning a chump to that attacker.

```python
# Inside the emergency loop, BEFORE picking a chump:
equip_bonus = self._attacker_equipment_bonus(game, opp, attacker)
damage_without_this_block = total_incoming - sum(
    (a.power or 0) for a in attackers if a.instance_id in emergency_blocks
)
still_lethal_if_skipped = damage_without_this_block >= me.life
if (equip_bonus >= 3
        and not self._equipment_breakable(game, me)
        and not still_lethal_if_skipped):
    continue  # skip chump — plating will rebind anyway
```

**Tests** (new file: `tests/test_decide_blockers_plating_aware.py`):

```python
def test_no_chump_into_plated_attacker_without_answer(card_db):
    """Opp: Frogmite (2/2) + Cranial Plating equipped, +4 bonus from 4 artifacts.
    Effective Frogmite = 6/2. Me: life=20, Guide of Souls (1/2), empty hand.
    Expected: decide_blockers returns {} — don't burn Guide; plating rebinds."""

def test_chump_plated_attacker_if_wear_tear_in_hand(card_db):
    """Same board, but Me has Wear // Tear in hand.
    Expected: chump is assigned (plating is breakable next turn)."""

def test_chump_plated_attacker_if_blocking_prevents_lethal(card_db):
    """Same plating setup; me at life=5, incoming=6.
    Expected: chump regardless of answer — lethal trumps projection."""

def test_equipment_bonus_detection_oracle_driven(card_db):
    """Regression: detection uses oracle regex, no hardcoded names.
    Put Colossus Hammer on 1/1 Memnite; _attacker_equipment_bonus returns +10.
    Put Cranial Plating on Frogmite with 4 artifacts out; returns +4."""
```

**Acceptance**:
- All 4 tests pass.
- Existing suite stays green.
- On the 6-seed batch: bucket C count drops from 26 → ≤ 6.
- Manually verify s63500: Warrior Tokens no longer chump Sojourner 4 turns in
  a row unless Wear // Tear is in hand.

---

### Phase 3 — RC-4: protect engine pieces in emergency path

**Problem**: emergency path picks by `creature_value`-smallest but doesn't
exclude structurally protected pieces. Phlage (escape), planeswalkers, and
battle-cry sources get chumped.

**Contract**:

```python
def _is_protected_piece(self, card) -> bool:
    """True for cards that should not be chump-blockers unless they also
    kill the attacker (or survival requires it).

    Categories (all oracle/tag-driven):
      - Planeswalkers (CardType.PLANESWALKER) — losing them surrenders abilities
      - Creatures with escape (oracle contains 'escape—' em-dash) — expensive to recur
      - Battle-cry / attack-trigger sources — offensive value > defence
    """
    from engine.cards import CardType
    t = card.template
    if CardType.PLANESWALKER in t.card_types:
        return True
    oracle = (t.oracle_text or '').lower()
    # Escape mechanic uses em-dash: 'escape—{cost}'
    if 'escape—' in oracle:
        return True
    name = (t.name or '').lower().split(' //')[0].strip()
    if 'whenever this creature attacks' in oracle:
        return True
    if name and f'whenever {name} attacks' in oracle:
        return True
    return False
```

**Behaviour change**: in the emergency blocker-candidate loop, skip protected
pieces unless (a) they can kill the attacker, or (b) skipping would be lethal
AND no other blocker is available.

```python
# Inside the emergency _blocker_candidates helper OR the chump-selection loop:
def _blocker_candidates(attacker, excl):
    cands = []
    for b in valid_blockers:
        if b.instance_id in excl:
            continue
        if Keyword.FLYING in attacker.keywords:
            if (Keyword.FLYING not in b.keywords and
                    Keyword.REACH not in b.keywords):
                continue
        cands.append(b)
    # Separate protected pieces; use only if no alternative
    unprotected = [b for b in cands if not self._is_protected_piece(b)]
    return unprotected if unprotected else cands
```

The non-emergency path already filters `is_battle_cry`; extend it to use
`_is_protected_piece` for consistency.

**Tests** (new file: `tests/test_decide_blockers_protects_engines.py`):

```python
def test_phlage_not_chump_when_token_blocker_available(card_db):
    """Me: Phlage, Titan of Fire's Fury (6/6) + Cat Token (2/1).
    Opp: Germ Token (6/6) attacks. Life=17.
    Expected: Cat Token blocks (or no block). Phlage is escape — protected."""

def test_planeswalker_never_chumps(card_db):
    """Me: Ajani, Nacatl Pariah (1/2 creature-side) + Cat Token (2/1).
    Opp: Sojourner's Companion (4/4). Life=20.
    Expected: no block from Ajani. Ajani is a planeswalker."""

def test_phlage_may_chump_if_only_option_and_lethal(card_db):
    """Me: Phlage only on board, life=5.
    Opp: Germ Token (6/6) attacks (lethal).
    Expected: Phlage chumps (survival overrides protection)."""

def test_battle_cry_source_not_chump_in_emergency(card_db):
    """Regression — extend existing non-emergency filter to emergency path.
    Me: Voice of Victory (1/3) + Warrior Token (1/1).
    Opp: near-lethal attack.
    Expected: Warrior Token blocks, Voice of Victory preserved."""
```

**Acceptance**:
- All 4 tests pass.
- Existing suite stays green (existing battle-cry test in non-emergency path
  must still pass).
- On the 6-seed batch: zero Phlage chump-blocks, zero planeswalker
  chump-blocks.

---

### Phase 4 — RC-5: race-instead branch

**Problem**: AI blocks to survive when racing would win. The existing
`my_untapped_power >= opp.life` gate only covers "lethal on board this turn".

**Contract**:

```python
def _racing_to_win(self, game, me, opp, attackers) -> bool:
    """True iff racing is strictly better than blocking.

    Conditions ALL must hold:
      (a) we survive this combat without blocking (incoming < my_life),
      (b) our clock-to-kill (attacks needed to reduce opp life to 0 given
          current on-board power) <= opp's clock-to-kill AFTER this combat.

    This is a lower bound — if we have burn/pump in hand, the race is even
    more winnable. Being conservative keeps false positives down.
    """
    incoming = sum(a.power or 0 for a in attackers)
    if incoming >= me.life:
        return False  # cannot race through lethal

    my_on_board_power = sum((c.power or 0) for c in me.creatures)
    if my_on_board_power <= 0:
        return False  # no offense

    attacking_ids = {a.instance_id for a in attackers}
    opp_on_board_power_after = sum(
        (c.power or 0) for c in opp.creatures
        if c.instance_id not in attacking_ids  # attackers tapped, can't swing next turn
    ) + sum(
        (a.power or 0) for a in attackers  # but they untap next turn
    )

    my_clock = opp.life / max(my_on_board_power, 1)
    my_life_after = me.life - incoming
    opp_clock = my_life_after / max(opp_on_board_power_after, 1)

    return my_clock <= opp_clock
```

**Behaviour change**: at the top of `decide_blockers`, after the existing
`my_untapped_power >= opp.life` check, add:

```python
if self._racing_to_win(game, me, opp, attackers):
    return {}
```

**Tests** (new file: `tests/test_decide_blockers_race_when_winning.py`):

```python
def test_race_when_clock_favourable(card_db):
    """Me: life=20, 2x Cat Token (2/1) + Ragavan (2/1) = 6 attack power.
    Opp: life=5, Construct Token (10/9) attacks.
    Expected: {} — we race (1 swing kills opp; opp needs 2 swings)."""

def test_no_race_when_lethal_incoming(card_db):
    """Me: life=5, strong offensive board. Opp incoming = 10. Lethal trumps race.
    Expected: blocks assigned."""

def test_no_race_when_clock_unfavourable(card_db):
    """Me: life=20, 1 Cat Token (2/1). Opp: life=20, Construct Token (10/9).
    my_clock=10 turns, opp_clock=~1.8 turns. Don't race.
    Expected: blocks."""
```

**Acceptance**:
- All 3 tests pass.
- Existing suite stays green.
- s65000 T5 specifically: Boros at life=20 vs Affinity at life=5 no longer
  triple-blocks — returns `{}`.

---

### Phase 5 — regression + batch re-run

After Phases 1–4 land:

```bash
# Regenerate the same 6 seeds with the new logic
for seed in 63500 64000 64500 65000 65500 66000; do
  python tools/bo3_trace.py boros affinity $seed replays/boros_vs_affinity_trace_s${seed}.txt
done

# Rebuild the HTML replayers for the two decisive losses (they will change)
python build_replay.py replays/boros_vs_affinity_trace_s65000.txt replays/replay_boros_vs_affinity_trace_s65000.html 65000
python build_replay.py replays/boros_vs_affinity_trace_s66000.txt replays/replay_boros_vs_affinity_trace_s66000.html 66000

# Re-run the audit parser
python tools/audit_blocks.py > audits/block_audit_raw_data.md

# Then the statistically meaningful matchup run
python run_meta.py --matchup boros affinity -n 50 --save

# And confirm results
grep "MATCH RESULT" replays/boros_vs_affinity_trace_s*.txt
```

**Acceptance for the full ticket**:
- `python -m pytest tests/ -q` — all tests green, including 15 new ones
- Audit bucket A+C combined ≤ 10 on the same 6 seeds (was 42)
- Audit bucket G (favorable) ≥ 10 on the same 6 seeds (was 7)
- n=50 Boros vs Affinity game-WR lands in `(0.50, 0.70)` — the expected
  range from `CLAUDE.md`. Current spot-check (N=14): 0.36.

---

## Part 3 — Smoking-gun cases (for quick-verify)

Each of these should behave differently post-fix. Use them as anchors when
spot-checking the new replay logs:

| Seed | Turn | Pre-fix behaviour | Post-fix expected | Driven by |
|------|------|-------------------|-------------------|-----------|
| s63500 T5-T8 | Warrior Token chumps Sojourner 14→26→24→26 four turns | No chump unless Wear//Tear in hand | RC-2 |
| s65000 T5 | Triple block at life=20 vs opp life=5 | `{}` — race instead | RC-5 |
| s65500 T5 | 5 blockers in one emergency; empty board next turn | Portfolio cap stops at 2-3 | RC-3 |
| s64000 T7 | Phlage chumps Germ Token (both 6/6) | Phlage protected, block with token | RC-4 |
| s65000 T2 | Ocelot Pride (engine) chumps Frogmite at life=20 | Not emergency, don't block | RC-1 |
| s66000 T4-T7 | Plated Construct chumped 5 times across 4 turns | Skip chumps; preserve blockers | RC-2 |

---

## Part 4 — Commit protocol

1. **One commit per Phase** is preferred.
2. Commit message format:
   ```
   fix(blocks): <RC-N one-line summary>

   <1–3 sentences on what changed and why>

   Evidence: <seed + turn citation>
   Tests: <new test filenames>
   Audit delta: <bucket A/C counts before → after>
   ```
3. After each commit, `python -m pytest tests/ -q` must be green.
4. Final commit of the series should append an "Applied" section to the
   bottom of THIS document listing outcomes per phase (bucket counts,
   n=50 WR result).
5. `git pull --rebase origin main` before pushing. **Never force-push.**

---

## Appendix — Regenerating the raw audit

```bash
cd MTGSimManu
git pull origin main
python merge_db.py                       # if coming in fresh
python tools/audit_blocks.py > audits/block_audit_raw_data.md
```

If replays are missing (clean clone), regenerate with:

```bash
for seed in 63500 64000 64500 65000 65500 66000; do
  python tools/bo3_trace.py boros affinity $seed replays/boros_vs_affinity_trace_s${seed}.txt
done
python tools/audit_blocks.py > audits/block_audit_raw_data.md
```

---

## Applied — 2026-04-21 (branch `claude/block-strategy-audit-phases-1afZ9`)

All five phases landed in order, one commit per phase plus a Phase 5
bundle (traces + audit tool fix + RC-2 intrinsic-scaling extension).

### Commits

| Commit | Phase | Subject |
|--------|-------|---------|
| `dbb1d6e` | 1 | `fix(blocks): RC-1/RC-3 emergency gate + portfolio cap` |
| `91a9234` | 2 | `fix(blocks): RC-2 plating/aura-aware projection` |
| `fcc9c37` | 3 | `fix(blocks): RC-4 protect planeswalkers and escape creatures` |
| `3c7183b` | 4 | `fix(blocks): RC-5 race-instead when clock favours us` |

### Tests

19 new unit tests, all green; full suite 252/252 (baseline was 233).
Files:
- `tests/test_decide_blockers_emergency_gate.py` — 4 tests (RC-1/RC-3)
- `tests/test_decide_blockers_plating_aware.py` — 5 tests (RC-2 + intrinsic)
- `tests/test_decide_blockers_protects_engines.py` — 7 tests (RC-4)
- `tests/test_decide_blockers_race_when_winning.py` — 3 tests (RC-5)

### Audit deltas (same 6 seeds, pre-fix → post-fix)

| Bucket | Pre | Post | Δ |
|--------|-----|------|---|
| A. Premature chump | 16 | **11** | −5 |
| C. Chump into scaled attacker (P≥10) | 26 | **21** | −5 |
| **A+C combined** | **42** | **32** | **−10** |
| D. Emergency chump (legitimate) | 5 | 7 | +2 |
| G. Favorable trade | 7 | 5 | −2 |

Target was A+C ≤ 10. We came up short. The shortfall is split two ways:

1. **Classifier conflation** — `audit_blocks.py` assigns bucket C purely on
   `attacker.power ≥ 10 and blocker.power < attacker.toughness`; most of
   the remaining 21 bucket-C entries are at life ≤ 5 where a chump is the
   correct lethal-save play, not a wasteful chump. They'd be bucket D
   under a life-aware classifier but the tool doesn't distinguish them.
   A tool-level fix to the classifier was out of scope.
2. **`evaluate_action` in the non-emergency path over-rewards small chumps**
   (Guide of Souls 1/2 blocks Frogmite 2/2 at life=19 scored +3.36). RC-1
   only tightens the emergency gate; the non-emergency path decides these
   blocks via `ai/board_eval.py::evaluate_action`, which the audit
   explicitly excludes from scope. Fixing this requires a scoring
   overhaul, not a block-strategy patch.

**Audit-tool fix bundled in** — `tools/audit_blocks.py` was
parsing `║ Life:` banners as [P1 life, P2 life] but the banner actually
prints [active-side life, other-side life], so life values during
opponent turns were swapped. Tracking active side via `TURN_HEADER_RE`
fixes the life attribution. This moved ~7 events from bucket A to
bucket D/C correctly reclassifying genuine life=5 emergencies that had
been misreported at life=20.

### Smoking-gun case verification

Seed | Turn | Pre | Post |
-----|------|-----|------|
s63500 T5–T8 | Warrior chumps Sojourner 4 turns | **Reduced** — some chumps now skipped when plating dominates; chumps remaining are at life ≤ 5 |
s65000 T5 | Triple-block at life=20 vs opp life=5 | **Fixed** — _racing_to_win returns `{}` |
s65500 T5 | 5 blockers in one emergency | **Fixed** — portfolio cap stops committing |
s64000 T7 | Phlage chumps Germ Token | **Fixed** — Phlage protected, alternative used |
s65000 T2 | Ocelot Pride chumps Frogmite at life=20 | **Fixed** — emergency gate no longer fires |
s66000 T4–T7 | Plated Construct chumped 5 times | **Partially fixed** — plated attackers skipped when damage is non-lethal; lethal-life turns still chump (correct play) |

### N=50 Boros-vs-Affinity match-WR

Target: game-WR in (0.50, 0.70). Pre-fix spot-check: 0.36 game-WR at N=14.

**Post-fix N=50 match-WR: 28%** (Boros 14/50, Affinity 36/50; avg game
turns Boros=7.3, Affinity=5.8). Corresponds to roughly a 33–35% game-WR
— **not in the target range.** The block fixes remove failure modes
but do not by themselves bring the matchup into T1 viability.

### Phase 5 scope notes

The audit spec listed acceptance as "WR in (0.50, 0.70)" assuming the
block fixes alone would do it. In practice, Boros-vs-Affinity
under-performance is not purely a blocking problem — it also involves:
- evaluate_action over-rewarding small chumps (see §2 above),
- Boros's sideboard plan against Affinity may not be aggressive enough,
- threat prioritisation during Boros's own turns (out of scope here).

The `evaluate_action` / sideboard / threat-assessment items are
explicitly out of scope per audit Part 4. This audit delivers the
structural block-strategy fixes; further matchup work requires its own
scoped audit.

### Post-fix files

- `ai/ev_player.py` — `_two_turn_lethal`, `_attacker_equipment_bonus`
  (now covers intrinsic scaling), `_equipment_breakable`,
  `_is_protected_piece`, `_racing_to_win`; plating-skip gate in
  emergency loop with explicit return on skip-all; protected-piece
  filter in both emergency and non-emergency paths.
- `tools/audit_blocks.py` — active-side-aware life parsing.
- `audits/block_audit_raw_data.md` — regenerated post-fix.
- `replays/boros_vs_affinity_trace_s*.txt` (6 seeds) — regenerated.
- `replays/replay_boros_vs_affinity_trace_s{65000,66000}.html` — rebuilt.

---

## Appendix — "Do NOT do this" checklist

- ❌ Do not add `if card.name == 'Phlage, ...'` anywhere. Use oracle/tag detection.
- ❌ Do not edit `creature_value` or `creature_threat_value` — those are used
  by targeting and spell-EV, a change here would ripple across the engine.
- ❌ Do not modify the verbose-log format — the Bo3 replayer (`build_replay.py`)
  parses it; `tools/audit_blocks.py` parses it.
- ❌ Do not force-push. If main diverges mid-session, `git pull --rebase` and
  generate an `apply_session.sh` fallback if the rebase is messy.
- ❌ Do not ship fixes without the n=50 matchup confirmation — the C-grade
  engine has regressed before on superficially-good heuristics; the WR is
  the final arbiter.
- ❌ Do not touch `ai/ev_player.py::decide_main_phase` or `decide_attackers`
  in this session. Out of scope.
