# MTG Simulator — Full Rewrite Plan

## What We Learned (40+ commits of iteration)

### What works (keep or adapt)
- **Card database** (`ModernAtomic.json` via 8 part files) — 21,759 cards with oracle text, types, P/T, mana costs, keywords
- **Card tag system** — auto-detected from oracle text: removal, creature, cantrip, ritual, etb_value, evasion, etc.
- **12 deck definitions** (`decks/modern_meta.py`) — mainboard + sideboard for Domain Zoo, Dimir Midrange, 4c Omnath, Boros Energy, Ruby Storm, Living End, Affinity, Izzet Prowess, Eldrazi Tron, Amulet Titan, Jeskai Blink, Goryo's Vengeance
- **Card effect handlers** (`engine/card_effects.py`) — EFFECT_REGISTRY with ETB/cast/resolve handlers per card
- **Basic game loop** — untap, draw, main, combat, main2, end step
- **Mana system** — color-aware mana production, greedy color constraint solving for can_cast
- **Combat resolution** — first strike, trample, deathtouch, lifelink
- **Storm/cascade mechanics** — storm copies, cascade resolution, Living End

### What failed (don't repeat)
1. **Concern pipeline (SURVIVE > ANSWER > ADVANCE > EFFICIENT)** — hard priority ordering blocks complex decks from executing their plans. Configurable ordering helped but is still rule-based.
2. **Hardcoded thresholds** (`opp_clock <= 4`, `val_threshold = 3.0`) — every threshold we tuned created regressions elsewhere. Even per-archetype thresholds are arbitrary.
3. **Multi-layer combo system** (GoalEngine → ComboReadiness → SpellSequencer → ChainSimulator) — 5 layers of abstraction that fight each other. Each layer makes assumptions the next layer violates.
4. **Generic concern pipeline for all archetypes** — aggro, control, combo, midrange all need fundamentally different decision models, not different parameters to the same model.
5. **Value-based play comparison** — tried replacing priority ordering with outcome projection (survival_delta + advancement_delta). Too many hardcoded weights; scoring is brittle.
6. **Patching approach** — each fix for one deck broke another. 40+ commits and Storm is still at 3%.

### Key insight from Legacy sim comparison
The Legacy sim (MTGSimClaude) uses:
- **Per-deck strategy functions** — not a generic pipeline
- **Explicit spell priority order** — cast in order, not concern-based
- **Mana budget tracking** — tap lands, track pool, cast what's affordable
- **Property-based threat classification** — MUST/HIGH/MED/LOW, not float thresholds
- **EV-based combat** — per-creature expected value assessment

### The fundamental missing capability
**Expected value calculation under known deck composition.**

Both players know their full 60-card decklist (composition, not order). This means:
- Draw spells have calculable EV (probability of finding specific cards)
- Future turns are predictable in distribution (know what % of draws are lands, spells, etc.)
- Combo decks can estimate "how many more draw steps until I assemble the combo?"
- Control decks can estimate "how likely is my next draw to be the payoff?"

## New Architecture: EV-Based Planning

### Core Decision Model

```
decide_play(game_state, player) → action:
    hand = player.hand
    deck_remaining = player.library_composition  # known cards, unknown order
    
    candidate_plays = get_legal_plays(game_state, player)
    
    best_play = None
    best_ev = -infinity
    
    for play in candidate_plays:
        # Project the immediate board state after this play
        projected = project_play(game_state, play)
        
        # Estimate future value:
        # - What cards will I likely draw? (deck composition math)
        # - What can I do with those cards? (chain simulator for combos)
        # - How does the opponent's board develop? (their deck composition)
        future_ev = estimate_future_value(projected, deck_remaining, turns_ahead=2)
        
        # Total EV = immediate impact + discounted future value
        ev = immediate_value(play, game_state) + 0.8 * future_ev
        
        if ev > best_ev:
            best_ev = ev
            best_play = play
    
    # Also evaluate "pass" (do nothing this turn)
    pass_ev = estimate_future_value(game_state, deck_remaining, turns_ahead=2)
    if pass_ev > best_ev:
        return Pass()
    
    return best_play
```

### Key Components

#### 1. Play Projector
```
project_play(game_state, play) → projected_state:
    # Simulate the play without mutating game state
    # For removal: reduce opponent board power
    # For creatures: add to our board
    # For rituals: increase mana pool
    # For draw spells: sample from deck composition
```

#### 2. Future Value Estimator
```
estimate_future_value(state, deck_composition, turns_ahead) → float:
    # Monte Carlo over possible draws:
    # - Sample N hands from remaining deck
    # - For each sample, run chain simulator (combo) or board projector (fair)
    # - Average the outcomes
    # 
    # Key simplification: don't simulate full game, just estimate
    # "can I kill?" and "can I survive?" from the projected state
```

#### 3. Chain Simulator (reuse existing combo_chain.py)
```
find_all_chains(hand, mana, medallions, payoffs) → chains:
    # Already implemented. Finds all viable spell sequences
    # with mana arithmetic. Returns ChainOutcome with storm count,
    # damage, tokens, mana trace.
```

#### 4. Board State Evaluator
```
evaluate_board(state) → float:
    # Simple: my_clock - opp_clock
    # my_clock = opponent_life / my_power (turns to kill)
    # opp_clock = my_life / opponent_power (turns until I die)
    # Positive = I'm winning, negative = I'm losing
```

### Per-Archetype Specialization

Instead of one pipeline with different parameters, each archetype has a different **value function**:

- **Aggro**: value = damage dealt this turn + (board power × expected survival turns)
- **Control**: value = threats answered + (payoff_in_hand × probability_of_casting_it)
- **Combo**: value = chain_simulator_lethal_probability × storm_damage
- **Midrange**: value = board_advantage + card_advantage

These are VALUE FUNCTIONS, not rule sequences. The decision loop above uses the archetype's value function to score each candidate play.

### Deck Composition Math

```python
class DeckKnowledge:
    """What a player knows about their deck."""
    full_decklist: Dict[str, int]    # all 60 cards
    seen_cards: Set[str]             # cards drawn/exiled/etc.
    
    @property
    def remaining(self) -> Dict[str, int]:
        """Cards still in library (known composition, unknown order)."""
        return {name: count - seen for name, count in full_decklist.items()
                if count - seen > 0}
    
    def probability_of_drawing(self, card_name: str, draws: int) -> float:
        """Hypergeometric: P(drawing at least 1 copy in N draws)."""
        copies = self.remaining.get(card_name, 0)
        deck_size = sum(self.remaining.values())
        if deck_size == 0 or copies == 0:
            return 0.0
        # P(not drawing any) = C(deck-copies, draws) / C(deck, draws)
        p_miss = 1.0
        for i in range(draws):
            p_miss *= (deck_size - copies - i) / (deck_size - i)
        return 1.0 - p_miss
```

### What to Reuse
- `engine/card_database.py` — card loading and template creation
- `engine/cards.py` — CardTemplate, CardInstance data models
- `engine/mana.py` — ManaCost, ManaPool
- `engine/card_effects.py` — EFFECT_REGISTRY with all card handlers
- `engine/game_state.py` — core state mutations (play_land, cast_spell, combat_damage)
- `engine/combat_manager.py` — combat resolution
- `ai/combo_chain.py` — chain simulator (mana arithmetic for combos)
- `decks/modern_meta.py` — deck definitions
- `decks/gameplan_loader.py` + JSON gameplans — deck configuration data

### What to Rewrite
- `ai/spell_decision.py` — replace concern pipeline with EV-based decision
- `ai/ai_player.py` — simplify to: get legal plays → score each → pick best
- `ai/gameplan.py` — keep DeckGameplan data, rewrite GoalEngine to use value functions
- `ai/board_eval.py` — simplify to clock-based evaluation
- `ai/combo_readiness.py` — delete, replaced by chain simulator + EV
- `ai/spell_sequencer.py` — delete, replaced by chain simulator
- `ai/evaluator.py` — simplify to archetype value functions
- `ai/turn_planner.py` — keep CombatPlanner, delete TurnPlanner
- `engine/game_runner.py` — clean up main loop, equipment fix

### Test Plan
```
Target win rates (pre-sideboard game 1):
  Zoo vs Dimir:     55-65% Zoo (Zoo is favored but Dimir interacts)
  Zoo vs Omnath:    55-65% Zoo (Omnath stabilizes sometimes)
  Zoo vs Storm:     55-65% Zoo (Storm can goldfish T3-4)
  Zoo vs Boros:     45-55% (mirror-ish aggro)
  Storm vs Dimir:   35-45% Storm (Dimir has counters but Storm is fast)
  Storm vs Omnath:  50-60% Storm (goldfish vs slow control)
  Omnath vs Dimir:  45-55% (value vs interaction)
  
Test method:
  - 200 games per matchup, multiple seed ranges
  - Track: win rate, avg game length, key card cast rates
  - Per-deck validation: "does the deck execute its gameplan?"
    - Storm: Medallion T2 rate, combo turn, finisher resolution rate
    - Omnath: Omnath cast rate, landfall triggers, Wrath usage
    - Boros: T1 creature rate, energy generation, token creation
```

## Card Database Setup

The `ModernAtomic.json` (145MB) is assembled from 8 part files in the repo:

```python
import json
merged = {'meta': None, 'data': {}}
for i in range(1, 9):
    with open(f'ModernAtomic_part{i}.json') as f:
        part = json.load(f)
    if merged['meta'] is None:
        merged['meta'] = part.get('meta')
    merged['data'].update(part['data'])
with open('ModernAtomic.json', 'w') as f:
    json.dump(merged, f)
# Result: 21,795 cards
```
