# MTGSimClaude — AI Architecture & Structure Guide

## File Structure

```
MTGSimClaude/
├── sim.py          # Entry point: run_game(), run_match(), CLI, rules tests
├── engine.py       # Turn execution: bug_turn(), opp_turn(), all strategy functions
├── game.py         # State: GameState, PlayerState, get_attackers(), mulligan
├── rules.py        # Immutable rules: Card, Permanent, MTGRules static methods
├── cards.py        # Card database: make_*_deck() functions, DECKS dict, meta shares
├── config.py       # Constants: CardRoles, MatchupCategory, InteractionParams
├── interaction.py  # Threat classification, counter selection, Thoughtseize targeting
├── gameplan.py     # Stub (GAMEPLANS dict, assess(), active_goal())
├── full_sweep.py   # Batch runner for all matchups
└── decks/          # Plugin decks (eight_cast, tes, elves)
```

## Simulation Flow

```
sim.py: run_game(matchup)
  ├── london_mulligan() both players
  ├── coin flip: who goes first
  └── for turn 1..15:
        ├── bug_turn(gs, turn)      ← BUG's turn (engine.py)
        │     ├── untap, clear damage
        │     ├── draw (+ Bowmasters trigger check)
        │     ├── land drop (fetch crack → WST trigger)
        │     ├── tap lands → build mana budget
        │     ├── cast spells (priority order)
        │     │     └── each spell → _opp_reactive_counter() check
        │     ├── combat → gs.get_attackers() → resolve_combat()
        │     └── EOT: Vial deploy, Tamiyo flip check
        │
        └── opp_turn(gs, turn)      ← Opponent's turn
              ├── untap, clear sickness
              ├── draw
              ├── land drop
              ├── tap lands → om (opponent mana)
              ├── Port taps BUG lands
              ├── gameplan assessment
              └── _strategy_XXX()    ← matchup-specific AI
                    └── each spell → _try_counter_any() check
```

## Key AI Decision Points

### 1. BUG Spell Casting (engine.py: bug_turn)

Priority order inside bug_turn():
```
1. Wasteland (activated ability, uncounterable)
2. Thoughtseize (T1-3, strip best card via interaction.py)
3. Fatal Push (kill opponent creatures, priority: haste > deathtouch > biggest)
4. Snuff Out (free kill if Swamp in play)
5. Deploy creatures:
   - Tamiyo (CMC 1, flip walker)
   - Nethergoyf (CMC 2, grows)
   - Bowmasters (CMC 2, flash, ping engine)
   - Murktide (CMC 7 delve, needs GY fuel)
   - Kaito (CMC 3, hexproof engine)
   Each creature → _opp_reactive_counter() fires
6. Cantrips: Brainstorm (only with fetch available), Ponder
7. Combat: get_attackers() evaluates each creature's EV
```

### 2. Opponent Reactive Counter (engine.py: _opp_reactive_counter)

Called when BUG casts a spell. Opponent tries to counter it.
```python
def _opp_reactive_counter(gs, spell_card, log_list):
    # Single-pass scan of opponent hand
    counters_by_tag = {c.tag: c for c in o.hand if c.tag in _COUNTER_TAGS}
    
    # Skip cantrips (not worth countering)
    # Skip Thoughtseize (only counter if protecting key cards)
    
    # Classify threat level:
    is_major = win_condition OR combo_piece OR tag in (murk, kaito) OR cmc >= 4
    # Mirror: Bowmasters + Nethergoyf are major (army + GY growth)
    # Control with STP: don't FoW cheap creatures (card disadvantage)
    
    # Counter priority chain:
    FoN (free on BUG's turn) → FoW (free, pitch blue) → Counterspell (UU) 
    → Flusterstorm (U, instants/sorceries) → Pyroblast (R, blue spells)
    → Consign (3 mana) → Daze (return Island)
    
    # Hand-size gates: Counterspell needs 4+ cards, Flusterstorm needs 3+
    # Trinisphere: blocks FoW/FoN alternate costs (can't pitch for free)
```

### 3. BUG Countering Opponent Spells (engine.py: _opp_try_counter)

Called when opponent casts a spell during opp_turn().
```python
def _opp_try_counter(gs, spell_card, log_list):
    # Uses BUG's hand (gs.bug)
    # Trinisphere active → FoW/FoN disabled (can't free-cast)
    # Veil of Summer active → all counters disabled
    
    # is_major: win_condition, combo_piece, bowm/murk/kaito, cmc >= 3
    # Skip cantrips and Thoughtseize
    
    # Priority: FoN → FoW → Daze (with pay-through probability)
```

### 4. Combat Resolution (engine.py: resolve_combat)

```python
def resolve_combat(gs, attacker_player, defender_player, log_list):
    attackers = gs.get_attackers(attacker_player)  # EV-based selection
    
    # C2: tap all attackers
    # Bridge check: defender hand size blocks power > hand_size
    # Vial combat ambush: flash in blocker (DnT/Boros)
    
    # Blocker assignment (greedy, largest attacker first):
    #   1. Favorable trade (blocker kills attacker, survives)
    #   2. Even trade (both die)
    #   3. Chump (attacker power >= 3, spare blockers available)
    
    # Damage resolution: mutual damage, deathtouch, lifelink, flying
```

### 5. get_attackers() EV Assessment (game.py)

```python
def get_attackers(self, player):
    # Bridge: defender's hand size blocks creatures
    # For each creature:
    #   - Skip summoning sick, skip 0-power
    #   - Evaluate best_blocker_for(attacker):
    #     - No blocker → free damage, always attack
    #     - Flying vs no flyers → unblocked, attack
    #     - Favorable trade → attack
    #     - Even trade + board lead → attack
    #     - Losing trade → don't attack
    #     - Deathtouch blocker → don't attack
    #   - Near lethal → attack regardless
```

### 6. Threat Classification (interaction.py: classify_threat)

```python
def classify_threat(spell_card, gs):
    # Property-based (not tag-based):
    MUST (4): combo pieces, win conditions
    HIGH (3): lock pieces, engines, haste, mass removal, cmc >= 5
    MEDIUM (2): removal spells, cmc2+ creatures
    LOW (1): cantrips, rituals
```

### 7. Thoughtseize Targeting (interaction.py: best_proactive_target)

```python
def best_proactive_target(gs):
    # Score each card in opponent's hand:
    win_condition:  100
    combo_piece:     90
    lock_piece:      80
    FoW/FoN:         65
    engine:          50
    creature cmc3+:  40
    removal:         35
    ritual:          25
    cantrip:         10
```

## Opponent Strategy Architecture

Each opponent deck has a strategy function:
```python
def _strategy_XXX(player, opponent, gs, total_mana, log_fn, log_entries):
    # 1. Deploy lock pieces / rituals / setup
    # 2. Cast creatures (via Vial or hard-cast)
    # 3. Use removal (STP, Bolt, Push)
    # 4. Wasteland / Karakas (if available)
    # 5. Combat: _select_attackers() or custom logic
    # 6. Special mechanics (Initiative, Karn wish, Bridge hand-dump)
```

Strategy dispatch in opp_turn():
```python
if   matchup == 'prison':    _opp_prison(gs, om, log, log_entries)
elif matchup == 'eldrazi':   _opp_eldrazi(gs, om, log, log_entries)
elif matchup == 'show':      _opp_show(gs, om, log, log_entries)
# ... 18 matchup strategies total
```

## Key Mechanics Implemented

| Mechanic | Where | How |
|----------|-------|-----|
| Mana system | rules.py ManaPool | Color-indexed, pay_cost with delve |
| FoW/FoN | _opp_reactive_counter | Pitch blue card, _select_fow_pitch() |
| Daze | counter functions | Return Island, pay-through probability |
| Chalice | opp_can_cast() | spell_blocked_by_chalice(cmc) |
| Trinisphere | effective_cmc() | max(cmc, 3), blocks FoW alternate cost |
| Bridge | get_attackers() | defender's hand size blocks power > N |
| Wasteland | strategy functions | Destroy nonbasic, priority: dual > fetch |
| Karakas | DnT/Boros strategy | Bounce legendary (Murktide priority) |
| Eidolon tax | _eidolon_trigger() | 2 damage per BUG spell CMC >= 2 |
| Thalia tax | effective_cmc() | Noncreature spells cost +1 |
| Initiative | Boros strategy | Escalating damage 1/2/3 per turn |
| Mentor tokens | UWx strategy | _MONK_TOKEN prototype, trigger on noncreature |
| Bowmasters | bowmasters_triggers() | 1 ping per draw event, grow Orc Army |
| Narset lock | game.py draw() | Only 1 draw per turn when active |
| Blood Moon | LandPermanent | Nonbasics produce only R |
| Back to Basics | LandPermanent | Nonbasics don't untap |
| Karn recurring | Prison strategy | +1 wish each turn (Bridge > Trini > Chalice) |
| WST triggers | bug_turn fetch | +1/+1 and draw on BUG library search |
| Vial EOT | bug_turn end step | Deploy creature at counter CMC, uncounterable |

## Shared Helpers

```python
# Token prototypes (avoid repeated allocation)
_MONK_TOKEN = Card(name='Monk Token', ...)
_ORC_ARMY_PROTO = Card(name='Orc Army', ...)

# Counter tag set for O(1) lookup
_COUNTER_TAGS = {'fow','fon','daze','consign','counter','fluster','pyro','reb'}

# Shared attacker selection
def _select_attackers(player, opponent, hold_tags=('bowm','tamiyo'), desperate_life=8):
    # Skip summoning sick, 0-power, held-back value engines

# Shared helpers
opp_can_cast()         # mana + color + Chalice + Trinisphere gate
_opp_reactive_counter() # opponent counters BUG's spells
_opp_try_counter()     # BUG counters opponent's spells  
_try_counter_any()     # dispatch to the right counter function
_bug_force_of_vigor()  # free artifact/enchantment destruction
combat_declare()       # wrap attackers + resolve_combat
```

## Testing

```bash
python sim.py --test                        # 102 rules unit tests
python sim.py --matchup dimir --games 1 -v  # single verbose game
python sim.py --matchup all --games 500 --bo3  # full metagame sweep
```

## Adding a New Deck

1. `cards.py`: Add `make_XXX_deck()` returning 60 cards
2. `cards.py`: Add to `DECKS` dict and `MATCHUP_META`
3. `engine.py`: Add `_strategy_XXX()` function
4. `engine.py`: Add dispatch in `opp_turn()` elif chain
5. `cards.py`: Add postboard swap plan in `make_postboard_opp_deck()`
