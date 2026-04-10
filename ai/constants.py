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

# ══════════════════════════════════════════════════════════════
# Creature Value Weights (ev_evaluator.py creature_value)
# ══════════════════════════════════════════════════════════════

CREATURE_POWER_MULT = 1.0         # Base value per point of power
CREATURE_TOUGHNESS_MULT = 0.3     # Base value per point of toughness

# Keyword bonuses — keyed by Keyword enum value name (lowercase)
KEYWORD_BONUSES = {
    "flying": 2.0,
    "trample": 1.0,
    "haste": 1.5,
    "deathtouch": 2.0,
    "first_strike": 1.5,
    "double_strike_per_power": 1.0,  # per point of power (effectively doubles it)
    "hexproof": 2.0,
    "indestructible": 3.0,
    "menace": 1.0,
    "vigilance": 1.0,
    "undying": 2.0,
    "annihilator": 4.0,
    "prowess": 1.5,
    "cascade": 3.0,
    "reach": 0.5,
    "lifelink_per_power": 0.5,  # per point of power, capped at 5 power
    "lifelink_power_cap": 5,
}

# Tag-based ability bonuses
TAG_BONUSES = {
    "etb_value": 2.0,
    "card_advantage": 3.0,
    "cost_reducer": 2.5,
    "token_maker": 1.5,
}

# ══════════════════════════════════════════════════════════════
# Board Evaluation Weights (ev_evaluator.py)
# ══════════════════════════════════════════════════════════════

# Aggro board eval
AGGRO_DAMAGE_BONUS = 3.0          # Per-turn clock advantage value
AGGRO_MY_POWER_MULT = 1.5         # Weight for own power
AGGRO_OPP_POWER_MULT = 0.5        # Weight for opponent power (negative)
AGGRO_EVASION_BONUS = 0.5         # Per point of evasive damage
AGGRO_HAND_BONUS = 0.3            # Per card in hand
AGGRO_LIFELINK_BONUS = 0.3        # Per lifelink power

# Midrange board eval
MIDRANGE_MY_POWER_MULT = 1.0
MIDRANGE_OPP_POWER_MULT = 1.2     # Overweight opponent threats
MIDRANGE_CREATURE_COUNT_MULT = 0.5
MIDRANGE_CARD_ADVANTAGE_MULT = 1.5
MIDRANGE_MANA_MULT = 0.5
MIDRANGE_CLOCK_MULT = 1.5

# Control board eval
CONTROL_OPP_POWER_PENALTY = 2.0   # Per opponent power (penalty)
CONTROL_OPP_CREATURE_PENALTY = 1.0
CONTROL_MY_POWER_MULT = 1.5
CONTROL_MY_CREATURE_MULT = 0.5
CONTROL_HAND_DIFF_MULT = 2.0
CONTROL_HAND_SIZE_MULT = 0.5
CONTROL_MY_LIFE_MULT = 1.2
CONTROL_OPP_LIFE_MULT = 0.5
CONTROL_MANA_MULT = 0.8

# Combo board eval
COMBO_STORM_BASE = 3.0            # Base value per storm count
COMBO_STORM_ACCELERATION = 2.0    # Bonus when storm >= 5
COMBO_STORM_THRESHOLD = 5         # Storm count for acceleration bonus
COMBO_LIFE_MULT = 0.3             # Life matters less for combo
COMBO_HAND_MULT = 1.0             # Hand matters moderately
COMBO_BOARD_POWER_MULT = 0.3      # Board power matters little
COMBO_MANA_POOL_MULT = 2.0        # Mana in pool is VERY valuable
COMBO_CARDS_DRAWN_MULT = 3.0      # Cards drawn this turn
COMBO_GY_CREATURE_MULT = 2.0      # Creatures in GY (for reanimate decks)

# ══════════════════════════════════════════════════════════════
# EV Evaluator Projections (ev_evaluator.py)
# ══════════════════════════════════════════════════════════════

RITUAL_AVERAGE_PRODUCTION = 3.0   # Average mana from a ritual spell
MANAMORPHOSE_PRODUCTION = 2.0     # Net mana from Manamorphose (technically 2)
ETB_LIFE_GAIN_ESTIMATE = 3.0      # Average life gained from ETB triggers
ENERGY_PRODUCTION_ESTIMATE = 2.0  # Average energy per ETB card
PASS_MANA_WASTE_MULT = 0.5        # Penalty per unused mana when passing
PASS_OPP_DEVELOPMENT_DISCOUNT = 0.3  # Discount on opponent damage estimate
COMBO_PASS_MANA_WASTE_MULT = 1.0  # Combo decks waste more when idle
COMBO_FULL_HAND_PENALTY = 2.0     # Penalty when combo has full hand and doesn't act
COMBO_FULL_HAND_THRESHOLD = 5     # Hand size triggering full hand penalty
FUTURE_VALUE_DISCOUNT = 0.8       # Per-turn discount on future value
SURVIVAL_POWER_SCALING = 3.0      # Divides opponent power for threat assessment

# ══════════════════════════════════════════════════════════════
# Threat Evaluation (response.py)
# ══════════════════════════════════════════════════════════════

THREAT_CMC_CAP = 5                 # Cap CMC contribution to threat
THREAT_CMC_MULT = 0.5              # Multiplier for CMC-based threat
BOARD_WIPE_BASE_THREAT = 6.0       # Base threat for board wipes
COMBO_PIECE_THREAT = 7.0           # Threat value for combo pieces
BURN_DEFAULT_DAMAGE = 3            # Default burn damage if unknown
BURN_FACE_THREAT_MULT = 1.5        # Face burn threat multiplier
BURN_LOW_LIFE_THRESHOLD_PCT = 0.25 # Below this life% = burn is very threatening
BURN_LOW_LIFE_THREAT_BONUS = 4.0   # Bonus when burn threatens low life
LETHAL_BURN_THREAT_BONUS = 10.0    # Bonus when burn is lethal
CASCADE_THREAT = 8.0               # Threat from cascade spells
REANIMATE_THREAT = 8.0             # Threat from reanimation spells
REMOVAL_TARGET_THREAT_MULT = 0.5   # Fraction of target value as threat
CREATURE_HIGH_POWER_THREAT = 0.8   # Multiplier for 4+ power creatures
CREATURE_MID_POWER_THREAT = 0.6    # Multiplier for 2+ power creatures
INSTANT_REMOVAL_RESPONSE_THRESHOLD = 3.0  # Min value to respond with removal

# ══════════════════════════════════════════════════════════════
# Mana Planning extras (mana_planner.py)
# ══════════════════════════════════════════════════════════════

PAYOFF_MISSING_COLOR_BONUS = 15.0  # Bonus for enabling multi-color payoffs
TAPPED_SPELL_ENABLE_EARLY = 0.15   # Urgency discount T1-T2 for tapped lands
TAPPED_SPELL_ENABLE_LATE = 0.4     # Urgency discount T5+ for tapped lands
LAND_VERSATILITY_BONUS = 1.0       # Bonus per color a land produces
FETCH_PROXY_PENALTY = 1.0          # Penalty for fetch land life cost

# ══════════════════════════════════════════════════════════════
# Turn Planner extras (turn_planner.py)
# ══════════════════════════════════════════════════════════════

DEAD_LIFE_VALUE = -50.0            # Board value when dead
TRADE_DOWN_VALUE_RATIO = 1.5       # My lost > opp lost * this = bad trade
EXPENDABLE_CREATURE_VALUE = 3.0    # Creatures below this are expendable
SHIELDS_DOWN_VALUE_THRESHOLD = 5.0 # Min tapped value for shields-down penalty
SHIELDS_DOWN_DAMAGE_REDUCTION = 0.6  # Reduce penalty by this per damage ratio
SMALL_ATTACKER_VALUE = 4.0         # Tokens/small creatures below this
COUNTER_TAPPING_OUT_PENALTY = -1.5 # Penalty for spending mana to counter
BLINK_ETB_RETRIGGER_BONUS = 3.0    # Bonus for re-triggering ETB via blink
REMOVAL_IMPROVEMENT_THRESHOLD = 2.0  # Min improvement to apply removal

# ══════════════════════════════════════════════════════════════
# Game Runner (engine layer, but AI-tuning adjacent)
# ══════════════════════════════════════════════════════════════

MAX_ACTIONS_COMBO = 40             # Max main phase actions for combo decks
MAX_ACTIONS_NORMAL = 20            # Max main phase actions for normal decks
GAME_TIMEOUT_SECONDS = 8.0         # Safety timeout per game
SHOCK_LETHAL_LIFE_THRESHOLD = 2    # Don't shock when life <= this
SHOCK_LOW_LIFE_THRESHOLD = 4       # Only shock for critical colors at this life

# ══════════════════════════════════════════════════════════════
# 1-Ply Lookahead (ev_evaluator.py, ev_player.py)
# ══════════════════════════════════════════════════════════════

# Blend weights: final_ev = heuristic * HEURISTIC_WEIGHT + lookahead * LOOKAHEAD_WEIGHT
HEURISTIC_WEIGHT = 0.7             # Weight for additive-bonus heuristic score
LOOKAHEAD_WEIGHT = 0.3             # Weight for projected state delta

# Clamp raw lookahead to this range before blending (prevents clock blow-ups)
LOOKAHEAD_CLAMP_MIN = -20.0
LOOKAHEAD_CLAMP_MAX = 20.0

# Opponent response probabilities — estimated from open mana + deck archetype
# Counter probabilities (opponent has 2+ mana open)
COUNTER_PROB_REACTIVE_DECK = 0.25  # Control/tempo/midrange with 2+ mana
COUNTER_PROB_REACTIVE_LOW = 0.10   # Control/tempo/midrange with 1 mana
COUNTER_PROB_PROACTIVE_DECK = 0.10 # Aggro/combo/ramp with 2+ mana
COUNTER_PROB_NO_MANA = 0.0         # No mana open

# Removal probabilities (opponent has 1+ mana, we deployed a creature)
REMOVAL_PROB_REACTIVE_DECK = 0.25  # Control/midrange
REMOVAL_PROB_PROACTIVE_DECK = 0.15 # Aggro/combo/ramp

# Estimated mana costs for opponent responses
COUNTER_ESTIMATED_COST = 2         # Most counters cost 2
REMOVAL_ESTIMATED_COST = 1         # Most removal costs 1
