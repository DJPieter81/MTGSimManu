"""
Engine-layer constants — game rules and limits.

These are Magic: The Gathering rules constants, not AI tuning parameters.
AI tuning parameters live in ai/constants.py.
"""

# ── Player defaults ──
STARTING_LIFE = 20
MAX_HAND_SIZE = 7
STARTING_HAND_SIZE = 7
MIN_KEEP_HAND_SIZE = 5

# ── Game limits ──
MAX_TURNS = 25
MAX_LANDS_PER_TURN = 1

# ── Safety valves ──
SBA_MAX_ITERATIONS = 20
MAX_MANA_ITERATIONS = 20
MAX_STACK_RESOLVES = 100

# ── Win conditions ──
POISON_COUNTER_LETHAL = 10
MILL_LOSS_THRESHOLD = 0  # Lose when library is empty on draw

# ── Zone names ──
ZONE_HAND = "hand"
ZONE_BATTLEFIELD = "battlefield"
ZONE_GRAVEYARD = "graveyard"
ZONE_EXILE = "exile"
ZONE_LIBRARY = "library"
ZONE_SIDEBOARD = "sideboard"

# ── Basic land types (for domain, fetch targeting, etc.) ──
BASIC_LAND_TYPES = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest"})

# ── Mana colors ──
MANA_COLORS = frozenset({"W", "U", "B", "R", "G"})

# ── Shockland life cost ──
SHOCK_LAND_LIFE_COST = 2

# ── Fetch land life cost ──
FETCH_LAND_LIFE_COST = 1
