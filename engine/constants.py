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
# Counted in HALF-turns, so this binds at roughly display turn MAX_TURNS//2.
# It is a WALL-CLOCK SAFETY VALVE — a pathological game must not run
# forever — and NOT a tiebreak: reaching it is a draw (CR 104.4), decided in
# `game_runner.adjudicate_capped_game`.
#
# Raised 25 -> 60 on 2026-08-30. At 25 it bound at display turn ~12 and
# adjudicated real games: 10 of 24 on a control-heavy sample, and 32% of
# Domain Zoo vs Azorius Control games. Because the old adjudication compared
# LIFE TOTALS it was archetype-biased, systematically deflating control.
# Measured sec/game post-warmup: 25 -> 0.23, 40 -> 0.34, 60 -> 0.35,
# 80 -> 0.35, 120 -> 0.35, with zero capped games from 60 upward. 60 is the
# smallest value that buys the whole effect; above it costs nothing and
# gains nothing. See docs/diagnostics/2026-08-30_turn_cap_deflates_control.md
MAX_TURNS = 60
MAX_LANDS_PER_TURN = 1

# ── Safety valves ──
SBA_MAX_ITERATIONS = 20
MAX_MANA_ITERATIONS = 20
MAX_STACK_RESOLVES = 100
# Activation re-entry bound. NOT a CR limit — the rules place no cap on how
# many abilities may be activated. Depth 1 means "no activation may begin while
# another is being paid for or is resolving". It is the only reachable value in
# the current tranche (no resolution path can recurse into activation), and it
# exists so that a later tranche which routes a sacrifice cost through the death
# machinery inherits a bound instead of a hang.
ACTIVATION_MAX_DEPTH = 1

# CR 726.4: a player shortcutting a loop with no decision points proposes a
# FINITE number of iterations. An unbounded mana engine (a self-untapping
# mana source whose untap cost is fully replaced away — see
# `ActivationManager.unbounded_mana_engines`) is credited this many mana in
# every capacity estimate, and payment executes only the iterations a cost
# actually needs. Four starting life totals lets any {X}{X} finisher exceed
# a doubled life total, which is the largest sink a single turn can use.
LOOP_SHORTCUT_MANA = 4 * STARTING_LIFE

# ── Player targets ──
# A spell's target list carries permanent instance ids; a PLAYER target is
# encoded as a negative sentinel. -1 has always meant "the opponent's face"
# (burn to face); -2 is the caster themself, legal for every "target
# player" / "any target" requirement (CR 115.1) — the self-discard outlet
# line of a graveyard deck. Resolve either through
# `engine.target_solver.player_index_for_target`.
PLAYER_TARGET_OPPONENT = -1
PLAYER_TARGET_SELF = -2

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
