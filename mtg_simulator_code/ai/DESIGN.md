# Goal-Oriented AI Evaluation Framework

## Core Principle
Every AI decision is: **"Which legal action produces the best expected game state?"**

Game state quality is measured by a single `evaluate(game, player_idx) -> float` function
that combines multiple dimensions. Positive = good for player, negative = bad.

## Dimensions

### 1. Life Differential (weight: 1.0)
- `my_life - opp_life` baseline
- Non-linear: life below 5 is worth much more than life above 15
- Life is a resource — paying life for advantage is fine when ahead

### 2. Board Presence (weight: varies by role)
- Each permanent on battlefield has an **intrinsic value** based on:
  - Actual power/toughness (not base — includes all buffs, equipment, counters)
  - Keywords (flying, trample, lifelink, haste each add value)
  - Abilities (ETB value already spent, but activated abilities have ongoing value)
  - Mana production capability
- Sum of my permanents' value minus sum of opponent's permanents' value
- **Equipment without a creature** is worth very little (just potential)
- **Equipped creature** = creature value + equipment buff value (killing it is high-value)

### 3. Card Advantage (weight: 1.5-2.0)
- Cards in hand: each card ≈ 1.5-2.0 points (diminishes past 5)
- Cards in library: not directly counted but affects long-game
- Graveyard as resource: for decks with flashback/delve/reanimate, GY cards have value
- **Net card advantage of an action**: casting a spell that draws 2 = +1 card advantage

### 4. Mana/Tempo (weight: varies by game phase)
- Untapped lands = options = value
- Spending mana efficiently each turn is critical
- **Tempo**: mana spent vs mana-equivalent removed
  - Bolt (1 mana) killing a 3-mana creature = +2 tempo
  - 3-mana removal on a 1-drop = -2 tempo
- Available mana after action matters (can I still respond?)

### 5. Threat Density / Clock
- How many turns until I can deal lethal?
- How many turns until opponent deals lethal?
- Evasive damage (flying, unblockable) counts more reliably
- This is DERIVED from board presence, not separately tracked

### 6. Role Assessment (the WHO'S THE BEATDOWN question)
- Computed once per game from matchup + board state, updated each turn
- `role_score`: -1.0 (pure control) to +1.0 (pure beatdown)
- Shifts weights:
  - Beatdown: board presence weight UP, card advantage weight DOWN
  - Control: card advantage weight UP, life preservation UP
  - Midrange: balanced, shifts based on board state
- Determined by: archetype matchup, life totals, board state, cards in hand

### 7. Deck Knowledge / Opponent Modeling
- Know opponent's decklist (we have it — this is a simulator)
- Track what they've played → infer what's left in deck/hand
- **Threat anticipation**: if they have Wrath in deck and 4 mana up, don't overextend
- **Sideboard awareness**: post-board, know what hate cards they might have

### 8. Hand Quality
- Not just count but QUALITY of cards in hand
- A hand of 3 lands when you have 6 in play = low quality
- A hand with answers to opponent's strategy = high quality
- Uncastable cards (wrong colors, too expensive) = low value

## Decision Points

### Removal Targeting
```
for each legal target:
    hypothetical_state = remove(target)
    score = evaluate(hypothetical_state) - evaluate(current_state)
pick target with highest score
```
This naturally handles:
- Killing equipped creatures (huge eval swing because buff disappears)
- Killing creatures vs going face (eval captures both)
- Choosing between multiple threats

### Spell Casting Priority
```
for each castable spell:
    hypothetical_state = cast(spell)
    score = evaluate(hypothetical_state) - evaluate(current_state)
    score -= opportunity_cost(mana_spent, cards_left_in_hand)
pick spell with highest score, or pass if no spell improves eval enough
```

### Combat (Attack/Block)
```
for each attack configuration:
    for each likely block response:
        expected_state = simulate_combat(attackers, blocks)
        score = evaluate(expected_state)
    weighted_score = average over likely blocks
pick attack config with highest weighted score
```

### Stack Responses
```
threat_eval = evaluate(state_if_resolves) - evaluate(current_state)
for each possible response:
    response_eval = evaluate(state_after_response) - evaluate(current_state)
    net = response_eval - cost_of_response
if best net response > threshold: respond
```

## Card Intrinsic Value
Instead of hardcoding "Ragavan is good", derive value from stats + abilities:
- Base: power * 1.5 + toughness * 1.0
- Keywords: flying +2, trample +1, lifelink +2, haste +1.5, deathtouch +2, 
  first_strike +1.5, hexproof +2, indestructible +3
- Mana production: +2 per color produced
- Card draw on ETB/attack: +2 per card
- Removal on ETB: +3
- CMC as efficiency context: high stats at low CMC = more valuable

## Game Phase Weights
- Early (turns 1-3): tempo weight HIGH, mana development critical
- Mid (turns 4-6): board presence and card advantage balanced
- Late (turns 7+): card quality and inevitability matter most

## What Stays Hardcoded
- Combo sequencing (Storm, Living End, Goryo's) — these are unique play patterns
  that can't be derived from board eval alone
- Mulligan decisions — pre-game, no board to evaluate
- Land sequencing — color requirements are deck-specific
