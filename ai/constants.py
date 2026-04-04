"""
AI-layer constants — tuning parameters for strategic decisions.

These control how the AI evaluates board states, selects spells,
plans combat, and makes mana decisions. Adjust these to tune AI behavior.

Game rules constants live in engine/constants.py.
"""

# ══════════════════════════════════════════════════════════════
# Survival & Pressure Assessment (spell_decision.py, board_eval.py)
# ══════════════════════════════════════════════════════════════

# Clock thresholds — how many turns until opponent kills us
DYING_CLOCK_THRESHOLD = 4       # Opponent kills us in <=4 turns = "dying"
MUST_ANSWER_CLOCK_THRESHOLD = 2 # Very fast clock, must answer immediately
NO_CLOCK = 99.0                 # Default clock value when no threats

# Pressure thresholds
PRESSURE_HIGH = 0.8             # Board state is very threatening
PRESSURE_LOW = 0.2              # Board state is comfortable
PRESSURE_EVOKE_THRESHOLD = 0.7  # Pressure level to justify evoking

# Creature threat thresholds
CREATURE_VALUE_UNDER_PRESSURE = 3.0   # Min power to count as threat when dying
CREATURE_VALUE_NORMAL = 5.0           # Min value to count as must-answer normally
CREATURE_POWER_MEANINGFUL = 3         # Min power for a creature to matter under pressure

# ══════════════════════════════════════════════════════════════
# Combat Scoring (turn_planner.py)
# ══════════════════════════════════════════════════════════════

LETHAL_BONUS = 101.8                # Bonus for achieving lethal this turn
TWO_TURN_LETHAL_BONUS = 14.6       # Bonus for setting up 2-turn lethal
TRADE_UP_BONUS = 2.0               # Bonus for killing higher-value creatures
TRADE_DOWN_PENALTY = -4.5          # Penalty for losing higher-value creatures
EVASION_BONUS = 1.6                # Bonus per point of evasive damage
SHIELDS_DOWN_PENALTY = -2.5        # Penalty for tapping out with opponent mana up
MAX_ATTACK_CONFIGS = 32            # Maximum attack configs to evaluate

# Aggression scaling by opponent life
LETHAL_RANGE_HIGH = 8              # Opponent at <=8 life: push hard
LETHAL_RANGE_MID = 12              # Opponent at <=12 life: moderate aggression
LETHAL_RANGE_EXTENDED = 16         # Opponent at <=16 life: slight aggression
AGGRESSION_BONUS_HIGH = 0.8        # Aggression multiplier near lethal
AGGRESSION_BONUS_MID = 0.4         # Aggression multiplier at mid range
AGGRESSION_BONUS_EXTENDED = 0.15   # Aggression multiplier at extended range

# Block evaluation
BLOCK_VALUE_MULTIPLIER = 5.0       # Life value of damage prevention
TRADE_UP_VALUE_RATIO = 0.9         # Value ratio for trading up
EVEN_TRADE_VALUE_RATIO = 1.1       # Value ratio for even trades
DOUBLE_BLOCK_VALUE_THRESHOLD = 4.0 # Min creature value to double-block

# ══════════════════════════════════════════════════════════════
# Board Evaluation (board_eval.py)
# ══════════════════════════════════════════════════════════════

EVOKE_NO_TARGETS_PENALTY = -10.0   # Penalty for evoking with no valid targets
HARDCAST_PRIORITY_VALUE = 1.0      # Preference for hard-casting over evoke
LIFE_VALUE_AT_ZERO = 999.0         # Life value at critical low life

# ══════════════════════════════════════════════════════════════
# Mana Planning (mana_planner.py)
# ══════════════════════════════════════════════════════════════

# Fetch target scoring weights
MISSING_COLOR_WEIGHT = 20.0        # Weight for providing a missing color
NEEDED_COLOR_WEIGHT = 3.0          # Weight for needed (but not missing) color
SPELL_ENABLEMENT_URGENCY = 3.0     # Urgency multiplier for enabling a castable spell

# Tapped land penalties by turn
TAPPED_LAND_PENALTY_T1 = 30.0     # Turn 1: very bad to enter tapped
TAPPED_LAND_PENALTY_T2 = 20.0     # Turn 2: still bad
TAPPED_LAND_PENALTY_T3 = 12.0     # Turn 3: moderate
TAPPED_LAND_PENALTY_T4_PLUS = 5.0 # Turn 4+: minor

# Domain scoring
DOMAIN_WEIGHT_EARLY = 2.0         # Domain value turns 1-2
DOMAIN_WEIGHT_LATE = 4.0          # Domain value turns 3+
DOMAIN_CARD_CAP = 5               # Max domain cards to count

# Land bonuses
UNTAPPED_LAND_BONUS = 8.0         # Bonus for entering untapped
SHOCKLAND_BONUS = 7.0             # Bonus for shocklands (flexible)
FETCHLAND_FLEXIBILITY_BONUS = 4.0 # Bonus for fetchlands

# ══════════════════════════════════════════════════════════════
# Spell Evaluation (evaluator.py)
# ══════════════════════════════════════════════════════════════

# Ability bonuses for permanent evaluation
ETB_BONUS = 2.0
CARD_DRAW_BONUS = 3.0
COST_REDUCTION_BONUS = 2.5
RECURRING_BONUS = 1.5
COMBO_PIECE_BONUS = 2.0

# Removal scoring
MUST_KILL_TARGET_BONUS = 3.0
NO_VALID_TARGETS_PENALTY = -5.0

# Attack scoring
LETHAL_ATTACK_VALUE = 50.0

# ══════════════════════════════════════════════════════════════
# Turn Planning (turn_planner.py)
# ══════════════════════════════════════════════════════════════

# Board state scoring weights
HAND_SIZE_VALUE_MULTIPLIER = 2.6   # Card advantage value per card
MANA_VALUE_MULTIPLIER = 0.3       # Mana pool value per point

# Life scoring by range
LIFE_SCORE_CRITICAL_MULTIPLIER = 4.0  # Life 0-3: very valuable
LIFE_SCORE_LOW_MULTIPLIER = 2.5       # Life 3-7: quite valuable
LIFE_SCORE_MID_MULTIPLIER = 1.0       # Life 7-15: normal value
LIFE_SCORE_HIGH_MULTIPLIER = 0.3      # Life 15+: diminishing value

# Response thresholds
COUNTER_THRESHOLD = 5.5            # Min threat value to counter
COUNTER_CHEAP_THRESHOLD = 2.0     # Threshold for cheap counters
REMOVAL_RESPONSE_THRESHOLD = 5.2  # Min value for instant removal
BLINK_SAVE_THRESHOLD = 3.5        # Min creature value to blink-save
DO_NOTHING_PENALTY = 5.0          # Penalty for doing nothing

# Pre/post-combat planning
PRE_COMBAT_REMOVAL_BONUS = 2.5    # Bonus for removing blockers before combat
MANA_RESERVATION_WEIGHT = 5.2     # Value of holding up mana
POST_COMBAT_DEPLOY_BONUS = 0.9    # Bonus for deploying after combat
