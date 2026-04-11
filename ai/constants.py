"""
AI Constants — structural limits and opponent response modeling.

Most AI scoring is now derived from clock-based game mechanics (ai/clock.py).
Combat scoring constants live in ai/turn_planner.py (local to that module).

What remains here:
- Computational/safety limits (timeouts, max actions)
- Opponent response modeling (counter/removal effectiveness)
- Legacy creature_value() weights (used by blocking/targeting until Phase 5+)
"""

# ══════════════════════════════════════════════════════════════
# Structural / Safety Limits
# ══════════════════════════════════════════════════════════════

MAX_ACTIONS_COMBO = 40             # Max main phase actions for combo decks
MAX_ACTIONS_NORMAL = 20            # Max main phase actions for normal decks
GAME_TIMEOUT_SECONDS = 8.0         # Safety timeout per game
SHOCK_LETHAL_LIFE_THRESHOLD = 2    # Don't shock when life <= this
NO_CLOCK = 99.0                    # Sentinel: no clock (no win condition)

# ══════════════════════════════════════════════════════════════
# Opponent Response Modeling (ev_evaluator.py)
# ══════════════════════════════════════════════════════════════

# Estimated mana costs for opponent responses
COUNTER_ESTIMATED_COST = 2         # Most counters cost 2 (Counterspell, Mana Leak)
REMOVAL_ESTIMATED_COST = 1         # Most removal costs 1 (Bolt, Push, Ending)

# Damage removal effectiveness by creature toughness
DAMAGE_REMOVAL_EFF_HIGH_TOUGH = 0.3  # 4+ toughness: 30% of damage removal kills it
DAMAGE_REMOVAL_EFF_MID_TOUGH = 0.6   # 3 toughness: 60% of damage removal kills it
