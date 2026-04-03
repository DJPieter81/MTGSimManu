# Unified Action Evaluation Framework

## Core Insight

Every decision in a game of Magic is the same question:
**"Does the benefit of this action exceed its cost, given the current game state?"**

Shock payment, blocking, evoking, combo timing, mulligan — they're all instances
of this. The current codebase has ~15 separate hardcoded functions with deck-name
overrides, turn-range brackets, and magic numbers. This framework replaces them
with one evaluation that derives behavior from board state.

## The Clock Abstraction

The key concept that eliminates most hardcodes is **clock** — how many turns
until a player wins or loses. Everything flows from this:

- A combo deck with a 2-turn clock naturally shocks aggressively (2 life is
  irrelevant when you win in 2 turns)
- A control deck at 5 life against aggro naturally doesn't shock (2 life is
  30% of remaining life, clock is long)
- A midrange deck blocks when the opponent's clock is faster than theirs
- Storm "goes off" when its clock reaches 1 (can win this turn)

## Board Assessment (replaces all per-decision analysis)

One function: `assess_board(game, player_idx) -> BoardAssessment`

```python
@dataclass
class BoardAssessment:
    # Clock: estimated turns to win/lose (lower = faster)
    my_clock: float        # turns until I can win
    opp_clock: float       # turns until opponent can win
    
    # Pressure: how urgent is tempo? (0.0 = no pressure, 1.0 = must act now)
    pressure: float        # derived from clock differential
    
    # Resources
    life_ratio: float      # my_life / 20 (how much life can I spend?)
    mana_available: int    # untapped lands + pool
    mana_needed: int       # cheapest castable spell in hand
    
    # Board presence
    my_power_on_board: int
    opp_power_on_board: int
    
    # Castability: what does having 1 more untapped mana enable?
    spells_unlocked: list  # spells that become castable with +1 mana
    colors_missing: set    # colors needed but not available
```

## Unified Value Function

One function: `action_value(assessment, action_type, context) -> float`

The value is always: **tempo_gain * urgency - resource_cost * conservation**

Where:
- `tempo_gain`: how much closer to winning does this action bring me?
- `urgency`: how much does tempo matter right now? (derived from clock)
- `resource_cost`: what do I pay? (life, cards, board presence)
- `conservation`: how much do I need to conserve resources? (derived from clock)

### Shock Payment (replaces should_pay_shock + AGGRESSIVE_SHOCK_DECKS)

```
tempo_gain = spells_unlocked_value  # value of casting something this turn
urgency = pressure                  # fast clock = high urgency
resource_cost = 2 / my_life         # 2 life as fraction of remaining
conservation = 1 - pressure         # slow clock = conserve life

shock_value = tempo_gain * urgency - resource_cost * conservation * 10
return shock_value > 0
```

No deck names. No turn brackets. A combo deck at 20 life with a spell to cast
naturally shocks (high urgency, low cost). A control deck at 6 life naturally
doesn't (low urgency, high cost).

### Evoke vs Hard-Cast (replaces hardcoded evoke logic)

```
evoke_value = removal_value * urgency  # killing their threat NOW
hardcast_value = body_value + removal_value  # body that stays + ETB
evoke_cost = body_value  # we lose the creature

if can_hardcast:
    return hardcast_value > evoke_value - evoke_cost
else:
    return evoke_value - evoke_cost > 0  # only option is evoke
```

### Blocking (replaces estimate_block_value turn brackets)

```
block_value = damage_prevented / my_life  # how much of my life does this save?
block_cost = creature_lost_value * conservation  # losing a blocker matters more when conserving
trade_bonus = attacker_value * urgency  # trading is better under pressure

return block_value + trade_bonus - block_cost > 0
```

### Storm Readiness (replaces ready_to_combo + storm_hold_rituals)

```
combo_damage = storm_count * 1 + hand_ritual_mana  # projected Grapeshot damage
can_win = combo_damage >= opp_life

if can_win: go off immediately
else: hold and accumulate (conservation mode)
```

## Migration Strategy

1. Implement `BoardAssessment` and `assess_board()` — pure derivation from game state
2. Implement `life_value(life, assessment)` — how much is 1 life point worth?
3. Replace `should_pay_shock` with `shock_value > 0` using the unified function
4. Replace evoke logic with `evoke_value vs hardcast_value`
5. Update blocking to use `block_value > 0`
6. Storm readiness becomes `can_win_this_turn()`
7. Delete AGGRESSIVE_SHOCK_DECKS, turn-range brackets, deck-name checks

## What Stays Deck-Specific

Only the **gameplan goals** stay per-deck (what cards to prioritize, what the
win condition is). The EVALUATION of whether to take an action is universal.
