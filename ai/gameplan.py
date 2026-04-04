"""
Unified Gameplan Framework — Goal-Oriented Strategic Planning
=============================================================
Every deck declares a Gameplan: an ordered sequence of strategic Goals.
Each Goal maps card names to priorities and defines transition conditions.

Three layers:
  1. DeckGameplan  — static per-deck config (goals, card priorities, mulligan keys)
  2. BoardAssessor — dynamic per-turn board state analysis (clock, resources, threats)
  3. GoalEngine    — reactive per-decision goal selection with overrides

The AIPlayer calls:
    engine = GoalEngine(deck_name)
    action = engine.choose_action(game, player_idx)

This replaces both the hardcoded combo sequencing AND the generic evaluator
fallback with a single unified decision loop that works for all archetypes.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance


# ═══════════════════════════════════════════════════════════════════
# Goal Types — abstract strategic objectives
# ═══════════════════════════════════════════════════════════════════

class GoalType(Enum):
    """Abstract strategic goals that any deck can use."""
    # Setup goals
    DEPLOY_ENGINE = "deploy_engine"       # Play key enablers (Amulet, Medallion, etc.)
    FILL_RESOURCE = "fill_resource"       # Build a resource (graveyard, storm, mana, energy)
    RAMP = "ramp"                         # Accelerate mana development

    # Execution goals
    EXECUTE_PAYOFF = "execute_payoff"     # Fire the combo / deploy the finisher
    CURVE_OUT = "curve_out"               # Play threats on curve (aggro/midrange)
    PUSH_DAMAGE = "push_damage"           # Maximize damage output, attack aggressively

    # Interaction goals
    DISRUPT = "disrupt"                   # Discard, counter, remove key pieces
    PROTECT = "protect"                   # Hold up protection / countermagic
    INTERACT = "interact"                 # Remove threats, answer the board

    # Grind goals
    GRIND_VALUE = "grind_value"           # Generate card advantage, outvalue opponent
    CLOSE_GAME = "close_game"             # Convert advantage into a win


# ═══════════════════════════════════════════════════════════════════
# Goal — a single strategic objective with card priorities
# ═══════════════════════════════════════════════════════════════════

@dataclass
class Goal:
    """A strategic objective with card priorities and transition conditions."""
    goal_type: GoalType
    description: str

    # Card priorities: card_name -> priority score (higher = play first)
    # Cards not listed get a default priority of 0
    card_priorities: Dict[str, float] = field(default_factory=dict)  # DEPRECATED: use card_roles instead. Kept for card_scorer hint only.

    # Card categories: sets of card names grouped by role within this goal
    # e.g., {"enablers": {"Amulet of Vigor"}, "payoffs": {"Primeval Titan"}}
    card_roles: Dict[str, Set[str]] = field(default_factory=dict)

    # Transition: move to next goal when this condition is met
    # Condition is a callable: (game, player_idx) -> bool
    # If None, this goal persists until overridden
    transition_check: Optional[str] = None  # name of a method on GoalEngine

    # Minimum turns to stay in this goal before allowing transition
    min_turns: int = 0

    # Whether to cycle creatures (Living End style) or cast them normally
    prefer_cycling: bool = False

    # Whether this goal should hold up mana for instants
    hold_mana: bool = False

    # Target resource count for FILL_RESOURCE goals
    resource_target: int = 0
    resource_zone: str = "graveyard"  # "graveyard", "storm", "mana", "battlefield"
    resource_min_cmc: int = 0  # minimum CMC for creatures to count toward resource_target

    # Combo dig/hold control: which spell roles to cast vs hold when waiting.
    # dig_roles: roles that are safe to cast while digging (default: draw + tutor)
    # hold_roles: roles to save for the combo turn (default: fuel + finisher)
    # New combo decks only need to set these if they differ from defaults.
    dig_roles: Optional[Set[str]] = None   # None = {"draw", "tutor"}
    hold_roles: Optional[Set[str]] = None  # None = {"fuel", "finisher", "rebuy"}


# ═══════════════════════════════════════════════════════════════════
# DeckGameplan — static per-deck configuration
# ═══════════════════════════════════════════════════════════════════

@dataclass
@dataclass
class DecisionThresholds:
    """Per-deck tunable thresholds for the spell decision pipeline.

    These replace the hardcoded magic numbers in spell_decision.py
    and board_eval.py. Each deck archetype can set values that match
    its strategic needs (e.g., control tolerates more pressure before
    panic-removing; aggro answers blockers at lower thresholds).
    """
    # --- SURVIVE trigger ---
    # "Am I dying?" fires when opp_clock <= this AND opp_board_power >= min_board_power.
    # Empirically measured: clock<=4,power>=3 triggers ~50-70% vs aggro (archetype-dependent).
    # This is the baseline survival check — it's MEANT to trigger often.
    dying_clock: int = 4
    dying_min_board_power: int = 3

    # --- ANSWER: threat classification tuning ---
    # Categorical threat classification (MUST/HIGH/MED/LOW) replaces
    # float value thresholds. These control the MED-level checks:
    # Emergency clock: answer ALL creatures when this close to dying
    answer_emergency_clock: int = 2
    # Minimum power for a "meaningful" creature under pressure (MED level)
    answer_min_power: int = 3

    # --- ADVANCE (reactive): mana holdback for interaction ---
    # Minimum mana to leave untapped when deploying a threat.
    # Control: 2+ (hold up counterspell/removal). Aggro: 0 (tap out freely).
    deploy_mana_holdback: int = 2

    # --- Board wipe conservation ---
    # Don't use a board wipe on a single creature unless its value exceeds this.
    # Higher = save wraths for bigger boards. Lower = wrath single threats.
    wrath_single_target_min_val: float = 8.0

    # --- Evoke thresholds ---
    # Pressure level needed to evoke when we can hardcast next turn (0.0-1.0).
    # Higher = more reluctant to evoke (wait for hardcast).
    evoke_hardcast_next_turn: float = 0.7
    # Pressure level needed to evoke when colors are wrong.
    evoke_wrong_colors: float = 0.4
    # Don't evoke removal against creatures with power AND cmc at or below these.
    evoke_skip_small_power: int = 2
    evoke_skip_small_cmc: int = 2

    # --- Concern pipeline ordering ---
    # Defines which concerns fire and in what order. Each entry is a
    # concern name: "survive", "answer", "advance", "efficient".
    # The pipeline tries each concern in order; for fair decks,
    # competing candidates are compared by outcome evaluation.
    #
    # Default (midrange): survive first, then answer, then advance.
    # Control: advance before answer (deploy payoffs over removing)
    # Aggro: advance first (deploy threats, answer only blockers)
    # Combo: advance first (execute combo, survive only if lethal)
    concern_order: tuple = ("survive", "answer", "advance", "efficient")


# Archetype defaults — derived from empirical analysis of opp_clock
# distributions across 50-game samples per matchup type.
#
# Measured SURVIVE trigger rates for (clock<=N AND power>=M) vs Zoo:
#   clock<=4,power>=3: control 70%, midrange 49%, aggro 56%
#   clock<=3,power>=3: control 57%, midrange 41%, aggro 47%
#   clock<=2,power>=3: control 40%, midrange 33%, aggro 34%
#
# The dying_clock threshold controls how often SURVIVE fires.
# It should be high enough to catch real danger but not so high that
# it blocks payoff deployment. The legacy value (4) is well-tested
# from the Zoo/Dimir bugfix session (0% → 33% Dimir win rate).
# Per-archetype tuning adjusts OTHER parameters to compensate.
_ARCHETYPE_THRESHOLDS = {
    "aggro": DecisionThresholds(
        # Aggro: standard order works — answer blockers before deploying
        # so creatures can attack through. The difference from midrange
        # is in OTHER thresholds (deploy_mana_holdback=0, answer_min_power=4).
        concern_order=("survive", "answer", "advance", "efficient"),
        dying_clock=4,
        dying_min_board_power=3,
        answer_min_power=4,
        deploy_mana_holdback=0,
        wrath_single_target_min_val=10.0,
        evoke_hardcast_next_turn=0.5,
        evoke_wrong_colors=0.3,
    ),
    "midrange": DecisionThresholds(
        # Midrange: answer threats first, then deploy — classic reactive play.
        # Legacy sim: Dimir-style "interact then deploy" maps to this order.
        concern_order=("survive", "answer", "advance", "efficient"),
        dying_clock=4,
        dying_min_board_power=3,
        deploy_mana_holdback=1,
        evoke_hardcast_next_turn=0.6,
    ),
    "control": DecisionThresholds(
        # Control: advance payoffs BEFORE answering non-critical threats.
        # Key insight: Omnath (4 life ETB + 4/4 body) IS a survival play.
        # Only survive if actually lethal, otherwise deploy the payoff.
        concern_order=("survive", "advance", "answer", "efficient"),
        dying_clock=4,
        dying_min_board_power=3,
        deploy_mana_holdback=2,
        wrath_single_target_min_val=7.0,
        evoke_hardcast_next_turn=0.8,
        evoke_wrong_colors=0.5,
    ),
    "combo": DecisionThresholds(
        # Combo: advance the combo first, survive only if lethal.
        # Legacy sim: combo strategies execute their plan, not interact.
        concern_order=("advance", "survive", "efficient"),
        dying_clock=4,
        dying_min_board_power=3,
        deploy_mana_holdback=0,
        wrath_single_target_min_val=12.0,
        evoke_hardcast_next_turn=0.9,
    ),
}


def get_thresholds(gameplan: "DeckGameplan") -> DecisionThresholds:
    """Get decision thresholds for a deck, falling back to archetype defaults."""
    if gameplan.thresholds:
        return gameplan.thresholds
    return _ARCHETYPE_THRESHOLDS.get(gameplan.archetype, DecisionThresholds())


@dataclass
class DeckGameplan:
    """Complete strategic plan for a deck archetype."""
    deck_name: str
    goals: List[Goal]

    # Per-deck decision thresholds (None = use archetype defaults)
    thresholds: Optional[DecisionThresholds] = None

    # Mulligan: card names that are essential to keep
    mulligan_keys: Set[str] = field(default_factory=set)
    mulligan_min_lands: int = 2
    mulligan_max_lands: int = 4

    # Mulligan: effective CMC overrides for cards with cost reduction (e.g., domain)
    # Maps card_name -> effective_cmc for mulligan evaluation
    mulligan_effective_cmc: Dict[str, int] = field(default_factory=dict)

    # Mulligan: require at least one creature with CMC <= this value (0 = disabled)
    # For aggro decks that need early board presence
    mulligan_require_creature_cmc: int = 0

    # Mulligan: combo sets — need at least one card from EACH set to keep at 7
    # e.g., [["Goryo's Vengeance", "Persist"], ["Unmarked Grave", "Faithful Mending"]]
    # means hand must have at least one reanimate AND one enabler
    mulligan_combo_sets: List[Set[str]] = field(default_factory=list)

    # Land play priorities: card_name -> priority (higher = play first)
    land_priorities: Dict[str, float] = field(default_factory=dict)

    # Cards that should NEVER be cast proactively (only as responses)
    reactive_only: Set[str] = field(default_factory=set)

    # Cards that should be cast as early as possible regardless of goal
    always_early: Set[str] = field(default_factory=set)

    # Archetype tag for role-driven scoring: "aggro", "control", "combo", "midrange"
    archetype: str = "midrange"

    # Abstract combo readiness check: (game, player_idx, engine) -> (ready, confidence, reason)
    combo_readiness_check: Optional[Callable] = None

    # Fallback goals when primary plan is dead (e.g., combo pieces exiled)
    fallback_goals: Optional[List["Goal"]] = None

    # Cards that are critical to the primary plan (if all exiled, switch to fallback)
    critical_pieces: Set[str] = field(default_factory=set)


# ═══════════════════════════════════════════════════════════════════
# BoardAssessor — dynamic board state analysis
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BoardAssessment:
    """Snapshot of the current strategic situation."""
    my_clock: int          # turns until I can kill opponent (999 = no clock)
    opp_clock: int         # turns until opponent can kill me
    my_life: int
    opp_life: int
    my_board_power: int    # total power on my board
    opp_board_power: int
    my_creatures: int
    opp_creatures: int
    my_hand_size: int
    my_mana: int
    opp_mana: int
    turn_number: int
    gy_creature_count: int  # creatures in my graveyard
    storm_count: int        # spells cast this turn
    has_lethal: bool        # can I kill this turn?
    am_dead_next: bool      # will I die next turn?
    resource_ready: bool    # is my current goal's resource condition met?


class BoardAssessor:
    """Computes a BoardAssessment from the live game state."""

    @staticmethod
    def assess(game: "GameState", player_idx: int,
               current_goal: Optional[Goal] = None) -> BoardAssessment:
        me = game.players[player_idx]
        opp = game.players[1 - player_idx]

        my_power = sum(c.power for c in me.creatures if c.power and c.power > 0)
        opp_power = sum(c.power for c in opp.creatures if c.power and c.power > 0)

        # Clock calculation
        my_clock = 999 if my_power <= 0 else max(1, (opp.life + my_power - 1) // my_power)
        opp_clock = 999 if opp_power <= 0 else max(1, (me.life + opp_power - 1) // opp_power)

        # Lethal check: can total power kill this turn?
        has_lethal = my_power >= opp.life and opp.life > 0

        # Dead next turn: opponent can kill us
        am_dead = opp_power >= me.life and me.life > 0

        # Resource readiness for current goal
        resource_ready = False
        if current_goal:
            if current_goal.goal_type == GoalType.FILL_RESOURCE:
                if current_goal.resource_zone == "graveyard":
                    min_cmc = current_goal.resource_min_cmc
                    gy_creatures = sum(1 for c in me.graveyard
                                       if c.template.is_creature
                                       and (c.template.cmc or 0) >= min_cmc)
                    resource_ready = gy_creatures >= current_goal.resource_target
                elif current_goal.resource_zone == "storm":
                    resource_ready = me.spells_cast_this_turn >= current_goal.resource_target
                elif current_goal.resource_zone == "mana":
                    resource_ready = me.available_mana_estimate >= current_goal.resource_target
                elif current_goal.resource_zone == "battlefield":
                    resource_ready = len(me.creatures) >= current_goal.resource_target
            elif current_goal.goal_type == GoalType.DEPLOY_ENGINE:
                # Check if any engine card is on the battlefield
                engine_cards = current_goal.card_roles.get("engines", set())
                resource_ready = any(
                    c.name in engine_cards for c in me.battlefield
                )
            elif current_goal.goal_type == GoalType.RAMP:
                mana_target = current_goal.resource_target or 6
                resource_ready = me.available_mana_estimate >= mana_target

        gy_creatures = sum(1 for c in me.graveyard if c.template.is_creature)

        return BoardAssessment(
            my_clock=my_clock,
            opp_clock=opp_clock,
            my_life=me.life,
            opp_life=opp.life,
            my_board_power=my_power,
            opp_board_power=opp_power,
            my_creatures=len(me.creatures),
            opp_creatures=len(opp.creatures),
            my_hand_size=len(me.hand),
            my_mana=me.available_mana_estimate + me.mana_pool.total(),
            opp_mana=opp.available_mana_estimate,
            turn_number=game.turn_number,
            gy_creature_count=gy_creatures,
            storm_count=me.spells_cast_this_turn,
            has_lethal=has_lethal,
            am_dead_next=am_dead,
            resource_ready=resource_ready,
        )


# ═══════════════════════════════════════════════════════════════════
# GoalEngine — the unified decision loop
# ═══════════════════════════════════════════════════════════════════

class GoalEngine:
    """Unified decision engine that works for all deck archetypes.

    Decision loop:
    1. Assess board state
    2. Check for overrides (lethal, survival, goal transition)
    3. Select the active goal
    4. Score each legal play against the active goal's priorities
    5. Return the highest-priority play

    The same loop handles aggro curving out, combo assembling pieces,
    midrange grinding value, and control holding up answers.
    """

    def __init__(self, gameplan: DeckGameplan):
        self.gameplan = gameplan
        self.current_goal_idx = 0
        self.turns_in_goal = 0
        self._last_turn = -1
        self.strategic_logger = None  # injected by ReplayGenerator
        self._player_idx = 0  # set by caller
        self.turning_the_corner = False  # flipped when control/midrange stabilizes
        self.on_fallback_plan = False    # flipped when primary plan is dead
        self._role_cache = None          # cached role for this decision cycle
        self._role_cache_turn = -1       # turn the cache was set

    @property
    def current_goal(self) -> Goal:
        if self.current_goal_idx < len(self.gameplan.goals):
            return self.gameplan.goals[self.current_goal_idx]
        return self.gameplan.goals[-1]  # stay on last goal

    def advance_goal(self, game=None, reason: str = ""):
        """Move to the next goal in the sequence."""
        if self.current_goal_idx < len(self.gameplan.goals) - 1:
            old_goal = self.current_goal
            self.current_goal_idx += 1
            self.turns_in_goal = 0
            new_goal = self.current_goal
            if self.strategic_logger and game:
                self.strategic_logger.log_transition(
                    self._player_idx,
                    old_goal.description, new_goal.description,
                    game, reason or f"Transitioning from {old_goal.goal_type.value} to {new_goal.goal_type.value}")

    def choose_action(self, game: "GameState", player_idx: int,
                      excluded_cards: set = None) -> Optional[Tuple]:
        """Main entry point: choose the best action for this main phase.

        Returns: ("play_land", card, []) or ("cast_spell", card, targets) or None
        """
        player = game.players[player_idx]
        self._player_idx = player_idx  # track for logger

        # Track turns in current goal
        if game.turn_number != self._last_turn:
            self._last_turn = game.turn_number
            self.turns_in_goal += 1

        # Track player energy for dynamic damage estimation
        self._player_energy = player.energy_counters

        # Step 1: Assess board
        assessment = BoardAssessor.assess(game, player_idx, self.current_goal)

        # Step 1b: Cache role for this decision cycle
        self._cache_role(game, player_idx)

        # Step 1c: Check if primary plan is dead → switch to fallback
        self._check_plan_b(game, player_idx)

        # Step 1d: Detect turning the corner (control/midrange stabilizing)
        self._detect_turning_corner(game, player_idx, assessment)

        # Step 2: Check goal transitions
        self._check_transitions(game, player_idx, assessment)

        # Step 3: Get legal plays
        legal = game.get_legal_plays(player_idx)
        if not legal:
            return None

        if excluded_cards:
            legal = [c for c in legal if c.instance_id not in excluded_cards]
            if not legal:
                return None

        lands = [c for c in legal if c.template.is_land]
        spells = [c for c in legal if not c.template.is_land]

        # Step 3b: Filter out legendary permanents we already control
        # This MUST happen before overrides and spell scoring to prevent
        # the AI from wasting cards to the legend rule (SBA 704.5j)
        spells = [s for s in spells if not self._would_violate_legend_rule(s, player)]

        # Step 4: Play a land (always first priority)
        # EXCEPTION: defer land play if we're about to cast a landfall payoff.
        # Casting the payoff first lets the land trigger landfall (e.g., Omnath
        # gains 4 life per landfall, adds WURG on 2nd, deals 4 on 3rd).
        defer_land = False
        if lands and player.lands_played_this_turn < (1 + player.extra_land_drops):
            # Check if a landfall payoff is castable without the land drop
            all_payoffs = set()
            for g in self.gameplan.goals:
                all_payoffs.update(g.card_roles.get('payoffs', set()))
            for sp in spells:
                if sp.name in all_payoffs:
                    oracle = (sp.template.oracle_text or "").lower()
                    if 'landfall' in oracle or 'land enters' in oracle or 'whenever a land' in oracle:
                        if game.can_cast(player_idx, sp):
                            defer_land = True
                            break

            if not defer_land:
                from engine.card_database import FETCH_LAND_COLORS
                no_life_fetches = {"Prismatic Vista", "Fabled Passage", "Evolving Wilds", "Terramorphic Expanse"}
                safe_lands = [
                    l for l in lands
                    if l.name not in FETCH_LAND_COLORS
                    or l.name in no_life_fetches
                    or player.life > 1
                ]
                if safe_lands:
                    land = self._choose_land(player, safe_lands, spells, assessment, game, player_idx)
                    if land:
                        return ("play_land", land, [])

        # Step 5: Override checks
        override = self._check_overrides(game, player_idx, spells, assessment)
        if override:
            return override

        # Step 6: Score spells against current goal and board state
        return self._choose_spell(game, player_idx, player, spells, assessment)

    def _check_transitions(self, game: "GameState", player_idx: int,
                           assessment: BoardAssessment):
        """Check if we should advance to the next goal.
        
        Key insight from competitive play analysis: decks must adapt when
        they don't draw their engine cards. A real Amulet Titan player
        without Amulet still ramps to Titan via Dryad/Azusa. A Ruby Storm
        player without Medallion still chains rituals. The goal system
        must reflect this adaptability.
        """
        goal = self.current_goal

        # Don't transition if we haven't been in this goal long enough
        if self.turns_in_goal < goal.min_turns:
            return

        # Resource-based transition
        if assessment.resource_ready:
            self.advance_goal(game, reason=f"Resource condition met for {goal.goal_type.value} (zone={goal.resource_zone}, target={goal.resource_target})")
            return

        # DISRUPT: for combo decks, disruption is a setup phase, not the main plan.
        # Advance after min_turns so we move to the real combo setup.
        if goal.goal_type == GoalType.DISRUPT:
            if self.gameplan.archetype == "combo" and self.turns_in_goal >= goal.min_turns:
                self.advance_goal(game, reason=f"Disruption phase complete after {self.turns_in_goal} turns — moving to combo setup")
                return
            # Non-combo decks: advance after 3 turns of disruption
            if self.turns_in_goal >= 3:
                self.advance_goal(game, reason=f"Disruption phase timed out after {self.turns_in_goal} turns")
                return

        # DEPLOY_ENGINE: advance when engine is deployed or enough mana to go without.
        # For combo decks (Storm): Medallion is critical — wait for it, but don't wait forever.
        # For non-combo: advance after 2 turns as before.
        if goal.goal_type == GoalType.DEPLOY_ENGINE:
            player = game.players[player_idx]
            engine_names = goal.card_roles.get('engines', set())
            has_engine = any(c.name in engine_names for c in player.battlefield)

            if has_engine:
                self.advance_goal(game, reason="Engine deployed — ready to execute")
                return

            if self.gameplan.archetype == "combo":
                # Combo without engine: need more mana to compensate
                # With 4+ mana, rituals (net +1 each without Medallion) can still chain
                if assessment.my_mana >= 4 and self.turns_in_goal >= 1:
                    self.advance_goal(game, reason=f"No engine but {assessment.my_mana} mana — enough to chain without reducer")
                    return
                # Don't wait more than 4 turns — have to try something
                if self.turns_in_goal >= 4:
                    self.advance_goal(game, reason=f"Engine not found after {self.turns_in_goal} turns — forced to adapt")
                    return
            else:
                if self.turns_in_goal >= 2:
                    self.advance_goal(game, reason=f"Engine not found after {self.turns_in_goal} turns — adapting plan")
                    return

        # RAMP: advance when close enough to cast payoffs, even if not
        # at the exact resource target. With 4-5 mana, a player will
        # start looking for Titan rather than waiting for exactly 6.
        if goal.goal_type == GoalType.RAMP:
            target = goal.resource_target or 6
            # Advance when within 2 of target (bounce lands + Amulet can bridge the gap)
            if assessment.my_mana >= target - 2 and self.turns_in_goal >= 1:
                self.advance_goal(game, reason=f"Mana at {assessment.my_mana}/{target} — close enough to start deploying payoffs")
                return
            # Don't wait more than 3 turns ramping
            if self.turns_in_goal >= 3:
                self.advance_goal(game, reason=f"Ramp stalled after {self.turns_in_goal} turns at {assessment.my_mana} mana — moving on")
                return

        # FILL_RESOURCE: advance when resource target is met, or after long stall
        if goal.goal_type == GoalType.FILL_RESOURCE:
            if assessment.resource_ready:
                # Already handled above at line 377, but double-check
                self.advance_goal(game, reason=f"Resource target met")
                return
            # Don't stall forever — after 5 turns, move on regardless
            if self.turns_in_goal >= 5:
                self.advance_goal(game, reason=f"Resource phase stalled after {self.turns_in_goal} turns — moving on")
                return

        # Abstract combo readiness check: replaces hardcoded storm count checks
        if goal.goal_type == GoalType.EXECUTE_PAYOFF and self.gameplan.combo_readiness_check:
            is_ready, confidence, reason = self.gameplan.combo_readiness_check(game, player_idx, self)
            if self.strategic_logger:
                self.strategic_logger.log_combo_assessment(
                    player_idx, game, self.gameplan.deck_name,
                    is_ready, reason, details={'confidence': round(confidence, 2)})

        # General time-based fallback for any other setup goals
        if goal.goal_type in (GoalType.DEPLOY_ENGINE, GoalType.FILL_RESOURCE,
                              GoalType.RAMP, GoalType.DISRUPT, GoalType.INTERACT):
            if self.turns_in_goal >= 5:
                self.advance_goal(game, reason=f"Setup goal timed out after {self.turns_in_goal} turns — forcing transition")

    def _check_overrides(self, game: "GameState", player_idx: int,
                         spells: list, assessment: BoardAssessment) -> Optional[Tuple]:
        """Check for situational overrides that bypass the current goal.

        Override priority:
        1. LETHAL — if we can kill, do it now regardless of goal
        2. SURVIVAL — if we're dead next turn, prioritize defense
        3. OPPORTUNISTIC — if a high-value play is available, take it
        """
        player = game.players[player_idx]
        opp = game.players[1 - player_idx]

        # Override 1: LETHAL ON BOARD
        # If we have lethal damage, just attack (handled in combat).
        # But also check if a burn spell finishes the game.
        if assessment.opp_life <= 5:
            for spell in spells:
                if not game.can_cast(player_idx, spell):
                    continue
                damage = self._estimate_face_damage(spell)
                if damage >= assessment.opp_life:
                    if self.strategic_logger:
                        self.strategic_logger.log_override(
                            player_idx, "LETHAL BURN",
                            f"Cast {spell.name} for {damage} to face (opp at {assessment.opp_life})",
                            game, f"Opponent at {assessment.opp_life} life — {spell.name} deals {damage} for the kill")
                    return ("cast_spell", spell, [-1])  # go face

        # Override 2: STORM LETHAL
        # If storm count is positive, check if any storm-tagged finisher is lethal.
        # Detected by 'storm' keyword on the card, not card names.
        if assessment.storm_count > 0:
            from engine.cards import Keyword
            for spell in spells:
                if not game.can_cast(player_idx, spell):
                    continue
                has_storm = Keyword.STORM in getattr(spell.template, 'keywords', set())
                is_token_maker = 'token_maker' in getattr(spell.template, 'tags', set())
                if has_storm:
                    # Storm damage spell: lethal if copies >= opponent life
                    if assessment.storm_count + 1 >= assessment.opp_life:
                        if self.strategic_logger:
                            self.strategic_logger.log_override(
                                player_idx, "STORM LETHAL",
                                f"Cast {spell.name} at storm count {assessment.storm_count}",
                                game, f"Storm count {assessment.storm_count} + 1 copies >= opponent life ({assessment.opp_life}) — lethal storm")
                        return ("cast_spell", spell, [])
                elif is_token_maker and has_storm:
                    # Token storm spell: meaningful at 4+ copies (8+ tokens)
                    if assessment.storm_count >= 3:
                        if self.strategic_logger:
                            self.strategic_logger.log_override(
                                player_idx, "STORM TOKENS",
                                f"Cast {spell.name} at storm count {assessment.storm_count}",
                                game, f"Storm count {assessment.storm_count} — creating {(assessment.storm_count + 1) * 2} tokens")
                        return ("cast_spell", spell, [])

        # SURVIVAL is now handled by spell_decision.py's SURVIVE concern.
        # It runs inside _choose_spell with access to the full decision context,
        # including reactive_only emergency re-inclusion and combo piece exclusion.
        return None

    # ═══════════════════════════════════════════════════════════════════
    # New strategic components (from pro-level research)
    # ═══════════════════════════════════════════════════════════════════

    def _cache_role(self, game: "GameState", player_idx: int):
        """Cache the current role assessment for this decision cycle.
        
        Role (beatdown/control/balanced) influences spell scoring.
        Caching avoids redundant computation within a single turn.
        """
        if self._role_cache_turn == game.turn_number:
            return  # already cached this turn
        from ai.evaluator import assess_role
        self._role_cache = assess_role(game, player_idx)
        self._role_cache_turn = game.turn_number

    def _check_plan_b(self, game: "GameState", player_idx: int):
        """Check if the primary plan is dead and switch to fallback.
        
        Pro insight (Seth Manfield): combo decks that can't assemble their
        combo need to recognize when to pivot to a beatdown plan. A Storm
        player who drew Ral and creatures but no rituals should just attack.
        
        Detection: if ALL critical pieces are in exile, the plan is dead.
        """
        if self.on_fallback_plan:
            return  # already on Plan B
        if not self.gameplan.critical_pieces or not self.gameplan.fallback_goals:
            return  # no fallback configured

        me = game.players[player_idx]
        exiled_names = {c.name for c in me.exile}
        critical = self.gameplan.critical_pieces

        # Check if ALL copies of critical pieces are exiled or otherwise gone
        # (not in library, hand, or battlefield)
        available_zones = (
            [c.name for c in me.hand] +
            [c.name for c in me.library] +
            [c.name for c in me.battlefield]
        )
        available_critical = critical & set(available_zones)

        if not available_critical:
            # Primary plan is dead — switch to fallback
            self.on_fallback_plan = True
            old_goals = self.gameplan.goals
            self.gameplan.goals = self.gameplan.fallback_goals
            self.current_goal_idx = 0
            self.turns_in_goal = 0
            if self.strategic_logger:
                self.strategic_logger.log_transition(
                    player_idx,
                    old_goals[self.current_goal_idx].description if old_goals else "primary plan",
                    self.gameplan.goals[0].description,
                    game,
                    f"PLAN B: All critical pieces ({', '.join(critical)}) unavailable. "
                    f"Switching to fallback: {self.gameplan.goals[0].description}")

    def _detect_turning_corner(self, game: "GameState", player_idx: int,
                               assessment: BoardAssessment):
        """Detect when a control/midrange deck should switch from defense to offense.
        
        Pro insight (Cardmarket/LSV): 'Turning the corner' is when a control
        deck has stabilized the board and should start deploying threats
        instead of holding up answers. Key signals:
        - Board advantage (more/bigger creatures)
        - Card advantage (opponent running low)
        - Life is stable (not under lethal pressure)
        - A threat is available or deployed
        """
        if self.turning_the_corner:
            return  # already turned
        if self.gameplan.archetype not in ("control", "midrange"):
            return  # only relevant for reactive archetypes

        board_advantage = (
            assessment.my_creatures > assessment.opp_creatures or
            assessment.my_board_power > assessment.opp_board_power + 3
        )
        opp = game.players[1 - player_idx]
        card_advantage = (
            len(opp.hand) <= 2 or
            assessment.my_hand_size >= len(opp.hand) + 2
        )
        has_threat = assessment.my_clock <= 5
        life_stable = assessment.my_life > 5

        if board_advantage and card_advantage and has_threat and life_stable:
            self.turning_the_corner = True
            if self.strategic_logger:
                self.strategic_logger.log_turning_corner(
                    player_idx, game,
                    f"Board stabilized: {assessment.my_creatures}v{assessment.opp_creatures} creatures, "
                    f"power {assessment.my_board_power}v{assessment.opp_board_power}, "
                    f"opp hand {len(opp.hand)} cards. Deploying threats.")

    def _should_reserve_mana(self, game: "GameState", player_idx: int,
                             spell, assessment: BoardAssessment) -> Tuple[bool, str, int]:
        """Check if we should hold mana for instant-speed interaction.
        
        Pro insight (LSV): control decks should almost never tap out when
        the opponent has mana up, unless deploying a must-answer threat.
        
        Returns: (should_reserve, hold_for_name, mana_to_hold)
        """
        from ai.evaluator import Role

        # Never reserve if we're the beatdown
        if self._role_cache == Role.BEATDOWN:
            return False, "", 0

        # Never reserve if opponent is tapped out
        if assessment.opp_mana == 0:
            return False, "", 0

        me = game.players[player_idx]

        # Find best instant-speed interaction in hand
        interaction = [
            c for c in me.hand
            if c.template.is_instant and (
                'counterspell' in c.template.tags or
                'removal' in c.template.tags
            )
        ]
        if not interaction:
            return False, "", 0

        best = max(interaction, key=lambda c: c.template.cmc or 0)
        mana_to_hold = best.template.cmc or 0
        if mana_to_hold == 0:
            return False, "", 0

        # If we have enough mana for BOTH, no need to reserve
        spell_cost = spell.template.cmc or 0
        if assessment.my_mana >= spell_cost + mana_to_hold:
            return False, "", 0

        # Don't reserve to skip a critical payoff
        goal = self.current_goal
        if goal and spell.name in goal.card_roles.get("payoffs", set()):
            return False, "", 0

        # Don't reserve to skip urgent removal when under pressure
        if 'removal' in spell.template.tags and assessment.opp_board_power > 5:
            return False, "", 0

        return True, best.name, mana_to_hold

    def _choose_land(self, player, lands, spells, assessment: BoardAssessment,
                     game: "GameState" = None, player_idx: int = 0):
        """Choose which land to play using the unified ManaPlanner."""
        if not lands:
            return None

        if game is not None:
            from ai.mana_planner import analyze_mana_needs, choose_best_land
            # Pass effective CMC overrides so domain cards (Scion, Leyline) are
            # correctly treated as cheap spells for mana sequencing
            cmc_overrides = self.gameplan.mulligan_effective_cmc or None
            needs = analyze_mana_needs(game, player_idx, effective_cmc_overrides=cmc_overrides)
            turn = getattr(game, 'turn_number', 1)
            library = game.players[player_idx].library
            chosen = choose_best_land(lands, needs, self.gameplan.land_priorities, turn=turn, library=library)
            if chosen and self.strategic_logger:
                alt_names = [l.name for l in lands if l is not chosen]
                missing = list(needs.missing_colors) if needs.missing_colors else []
                reason_parts = []
                if missing:
                    produces = list(getattr(chosen.template, 'produces_mana', []))
                    fixes = [c for c in produces if c in needs.missing_colors]
                    if fixes:
                        reason_parts.append(f"fixes missing color(s): {', '.join(fixes)}")
                if not getattr(chosen.template, 'enters_tapped', False):
                    reason_parts.append("enters untapped for tempo")
                else:
                    reason_parts.append("enters tapped")
                reason = f"Playing {chosen.name} — " + "; ".join(reason_parts) if reason_parts else f"Playing {chosen.name} (best available)"
                self.strategic_logger.log_land(
                    player_idx, chosen.name, game, reason,
                    alternatives=alt_names[:3])
            return chosen

        # Fallback if game state not available (shouldn't happen in practice)
        land_prios = self.gameplan.land_priorities
        needed: Dict[str, int] = {}
        for spell in spells:
            cost = spell.template.mana_cost
            for color, count in [("W", cost.white), ("U", cost.blue),
                                  ("B", cost.black), ("R", cost.red),
                                  ("G", cost.green)]:
                if count > 0:
                    needed[color] = needed.get(color, 0) + count
        best, best_score = None, -999
        for land in lands:
            score = land_prios.get(land.name, 0.0)
            for color in land.template.produces_mana:
                if color in needed:
                    score += needed[color] * 2.0
            if not land.template.enters_tapped:
                score += 3.0
            score += len(land.template.produces_mana) * 0.5
            if score > best_score:
                best_score = score
                best = land
        return best

    def _choose_spell(self, game: "GameState", player_idx: int,
                      player, spells: list,
                      assessment: BoardAssessment) -> Optional[Tuple]:
        """EV-based spell selection — scores each candidate play by projected outcome.

        Replaces the concern pipeline (SURVIVE > ANSWER > ADVANCE > EFFICIENT)
        with a single EV-maximization loop. Each candidate spell is scored by
        projecting the resulting board state and evaluating it with the
        archetype's value function.

        Falls back to the legacy concern pipeline if the EV system fails.
        """
        from ai.ev_decision import choose_spell_ev, EVSpellDecision

        try:
            ev_result = choose_spell_ev(self, spells, game, player_idx, assessment)
            # Convert EVSpellDecision to the format expected by the rest of the system
            decision_card = ev_result.card
            decision_concern = "ev_best"
            decision_reasoning = ev_result.reasoning
            decision_alternatives = [(name, f"EV={ev:.1f}") for name, ev in ev_result.alternatives]
        except Exception as e:
            # Fallback to legacy concern pipeline on errors
            import sys
            print(f"[EV Decision ERROR] {type(e).__name__}: {e} — falling back to concern pipeline", file=sys.stderr)
            from ai.spell_decision import choose_spell
            legacy = choose_spell(self, spells, game, player_idx, assessment)
            decision_card = legacy.card
            decision_concern = legacy.concern
            decision_reasoning = legacy.reasoning
            decision_alternatives = legacy.alternatives

        # Create a duck-typed decision object for the rest of the method
        class _Decision:
            pass
        decision = _Decision()
        decision.card = decision_card
        decision.concern = decision_concern
        decision.reasoning = decision_reasoning
        decision.alternatives = decision_alternatives

        # Handle pass (no play)
        if decision.card is None:
            if self.strategic_logger:
                goal = self.current_goal
                self.strategic_logger.log_hold(
                    player_idx, game, goal.description,
                    decision.reasoning)
            return None

        # Handle cycling (returned by cycling-priority decks)
        if decision.concern == "advance" and "Cycling" in decision.reasoning:
            from engine.game_state import CYCLING_COSTS
            if decision.card.name in CYCLING_COSTS:
                if self.strategic_logger:
                    goal = self.current_goal
                    alts = [name for name, _ in decision.alternatives[:3]]
                    self.strategic_logger.log_spell(
                        player_idx, decision.card.name, decision.concern,
                        game, goal.description, decision.reasoning,
                        alternatives=alts)
                return ("cycle", decision.card, [])

        # Choose targets for the selected spell
        targets = self._choose_targets_for_goal(
            game, player_idx, decision.card, assessment)

        # Build target description for logging
        target_desc = ""
        if targets:
            target_names = []
            for t in targets:
                if isinstance(t, int) and t == -1:
                    target_names.append("opponent's face")
                elif hasattr(t, 'name'):
                    target_names.append(t.name)
            target_desc = ", ".join(target_names) if target_names else ""

        # If spell requires targets but we found none, pass
        if self._spell_requires_targets(decision.card) and not targets:
            if self.strategic_logger:
                goal = self.current_goal
                self.strategic_logger.log_hold(
                    player_idx, game, goal.description,
                    f"{decision.card.name} chosen but no valid targets found — passing")
            return None

        # Log the spell selection
        if self.strategic_logger:
            goal = self.current_goal
            alts = [name for name, _ in decision.alternatives[:3]]
            self.strategic_logger.log_spell(
                player_idx, decision.card.name, decision.concern,
                game, goal.description, decision.reasoning,
                alternatives=alts, target_desc=target_desc)

        return ("cast_spell", decision.card, targets)

    # _find_survival_play has been removed.
    # Survival logic now lives in spell_decision._concern_survive(),
    # which runs inside _choose_spell with full context including
    # reactive_only re-inclusion and combo piece exclusion.

    def _estimate_face_damage(self, spell) -> int:
        """Estimate how much damage a spell deals to face.
        
        Uses oracle text parsing and card tags instead of hardcoded card names.
        Falls back to ability description scanning for unknown cards.
        """
        # Check for energy-based scaling (e.g. Galvanic Discharge)
        if 'energy_scaling' in getattr(spell.template, 'tags', set()):
            energy = getattr(self, '_player_energy', 0)
            base = spell.template.cmc or 1
            return base + min(energy, 5)
        
        # Check for 'burn' or 'direct_damage' tags with damage in abilities
        tags = getattr(spell.template, 'tags', set())
        
        # Domain-scaling damage (e.g. Tribal Flames)
        if 'domain' in tags and ('burn' in tags or 'direct_damage' in tags):
            # Domain counts basic land types; estimate from our lands
            return 5  # max domain in 5c decks
        
        # Parse abilities for damage numbers
        for ab in spell.template.abilities:
            desc = ab.description.lower()
            if "damage" in desc:
                # Check if it can hit players/any target
                hits_face = any(kw in desc for kw in [
                    "player", "any target", "opponent", "each opponent",
                    "any one target", "target creature or player",
                    "to any",  # matches 'deal N damage to any'
                ])
                if hits_face:
                    # Extract the damage number
                    import re
                    nums = re.findall(r'(\d+) damage', desc)
                    if nums:
                        return int(nums[0])
                    # Check for X damage
                    if 'x damage' in desc:
                        return getattr(self, '_player_energy', 0) + (spell.template.cmc or 1)
        
        return 0

    def _would_violate_legend_rule(self, spell, player) -> bool:
        """Check if casting this spell would waste it to the legend rule."""
        from engine.cards import Supertype, CardType
        supertypes = getattr(spell.template, 'supertypes', [])
        is_legendary = Supertype.LEGENDARY in supertypes if supertypes else False
        if CardType.PLANESWALKER in spell.template.card_types:
            is_legendary = True
        if not is_legendary:
            return False
        return any(c.template.name == spell.template.name for c in player.battlefield)

    def _spell_requires_targets(self, spell) -> bool:
        """Check if a spell requires targets to be cast legally."""
        tags = spell.template.tags
        if "counterspell" in tags:
            return True
        if "removal" in tags and "board_wipe" not in tags:
            return True
        if "blink" in tags:
            return True
        for ability in spell.template.abilities:
            if ability.targets_required > 0:
                desc = ability.description.lower()
                if any(kw in desc for kw in ["destroy", "exile", "bounce",
                                              "return", "blink", "counter", "damage"]):
                    return True
        return False

    def _choose_targets_for_goal(self, game, player_idx, spell,
                                  assessment: BoardAssessment) -> list:
        """Choose targets considering the current goal context.

        Delegates to the existing targeting logic in AIPlayer,
        but with goal-aware adjustments.
        """
        # Import the targeting logic — we reuse AIPlayer's targeting
        # but the GoalEngine provides the spell selection
        from ai.evaluator import estimate_removal_value, estimate_permanent_value

        targets = []
        opp_idx = 1 - player_idx
        opp = game.players[opp_idx]
        me = game.players[player_idx]

        for ability in spell.template.abilities:
            if ability.targets_required <= 0:
                continue

            desc = ability.description.lower()

            # Blink: target own creatures
            if "blink" in spell.template.tags and "exile" in desc:
                if me.creatures:
                    etb = [c for c in me.creatures if "etb_value" in c.template.tags]
                    if etb:
                        best = max(etb, key=lambda c: estimate_permanent_value(
                            c, me, game, player_idx))
                        targets.append(best.instance_id)
                    else:
                        best = max(me.creatures, key=lambda c: estimate_permanent_value(
                            c, me, game, player_idx))
                        targets.append(best.instance_id)
                continue

            # Removal: target opponent creatures
            if "creature" in desc and ("destroy" in desc or "exile" in desc or "damage" in desc):
                if opp.creatures:
                    # Check if this is damage-based removal (not destroy/exile)
                    damage = self._estimate_face_damage(spell)
                    is_damage_based = damage > 0 and "destroy" not in desc and "exile" not in desc
                    if not is_damage_based:
                        # For destroy/exile, also check _estimate_face_damage == 0
                        # meaning it's not a burn spell but a hard removal
                        is_damage_based = damage > 0 and damage < 99
                    
                    # Filter to only creatures we can actually kill
                    if is_damage_based and damage < 99:
                        killable = [
                            c for c in opp.creatures
                            if damage >= (c.toughness or 0) - (getattr(c, 'damage_marked', 0) or 0)
                        ]
                    else:
                        killable = list(opp.creatures)  # destroy/exile kills anything
                    
                    if killable:
                        best = max(killable, key=lambda c: estimate_permanent_value(
                            c, opp, game, opp_idx))
                        targets.append(best.instance_id)
                    # If nothing is killable, don't append a target — spell will be skipped

            elif "artifact" in desc and ("destroy" in desc or "exile" in desc):
                from engine.cards import CardType
                artifacts = [c for c in opp.battlefield
                             if not c.template.is_land and
                             CardType.ARTIFACT in c.template.card_types]
                if artifacts:
                    best = max(artifacts, key=lambda c: estimate_permanent_value(
                        c, opp, game, opp_idx))
                    targets.append(best.instance_id)

            elif "permanent" in desc or "nonland" in desc:
                nonlands = [c for c in opp.battlefield if not c.template.is_land]
                if nonlands:
                    best = max(nonlands, key=lambda c: estimate_permanent_value(
                        c, opp, game, opp_idx))
                    targets.append(best.instance_id)

            elif "player" in desc or "any" in desc or "target" in desc:
                # Face vs creature decision — comparison-based, no scoring.
                # Ask: "Will removing this creature prevent more damage
                #       than sending this burn spell to face?"
                damage = self._estimate_face_damage(spell)

                if opp.creatures:
                    # Filter to only creatures this spell can actually kill
                    if damage > 0 and damage < 99:
                        killable = [
                            c for c in opp.creatures
                            if damage >= (c.toughness or 0) - (getattr(c, 'damage_marked', 0) or 0)
                        ]
                    else:
                        killable = list(opp.creatures)

                    # LETHAL: if we can kill opponent this turn, go face
                    if assessment.has_lethal and damage > 0:
                        targets.append(-1)
                    # CLOSE TO LETHAL: burn finishes them off with board damage
                    elif damage > 0 and opp.life <= damage + assessment.my_board_power:
                        targets.append(-1)
                    elif killable:
                        best = max(killable, key=lambda c: estimate_permanent_value(
                            c, opp, game, opp_idx))
                        best_power = best.power or 0

                        # Comparison: creature stays alive for N more turns.
                        # Its total future damage = power * turns_alive.
                        # Removing it saves that damage.
                        # Going face deals `damage` once.
                        # Remove if saved_damage > face_damage.
                        # Also always remove high-value engines/threats.
                        if assessment.opp_clock <= 3 and assessment.opp_board_power > 0:
                            # Under pressure — always remove
                            targets.append(best.instance_id)
                        elif damage == 0:
                            # Non-damage removal (destroy/exile) — always target creature
                            targets.append(best.instance_id)
                        else:
                            # How many turns will this creature live?
                            # Conservative: at least 2 turns (this turn + next)
                            turns_alive = max(min(assessment.opp_clock, 5), 2)
                            saved_damage = best_power * turns_alive

                            # Go face only if the burn damage exceeds the
                            # damage we'd prevent by removing the creature,
                            # AND the creature isn't a high-value engine
                            creature_val = estimate_permanent_value(
                                best, opp, game, opp_idx)
                            is_engine = creature_val >= 5.0

                            if is_engine:
                                targets.append(best.instance_id)
                            elif saved_damage > damage:
                                targets.append(best.instance_id)
                            else:
                                targets.append(-1)
                    elif damage > 0:
                        targets.append(-1)  # can't kill anything, go face instead
                    else:
                        pass  # no killable targets and no face damage — skip
                elif damage > 0:
                    targets.append(-1)  # no creatures, go face
                else:
                    targets.append(-1)

        return targets

    # ═══════════════════════════════════════════════════════════════
    # Mulligan support
    # ═══════════════════════════════════════════════════════════════

    def _effective_cmc(self, card) -> int:
        """Get effective CMC for mulligan evaluation, respecting cost reduction."""
        override = self.gameplan.mulligan_effective_cmc.get(card.name)
        if override is not None:
            return override
        return card.template.cmc or 0

    def decide_mulligan(self, hand: list, cards_in_hand: int) -> bool:
        """Goal-aware mulligan: keep hands that can execute the first goal.

        Improvements over v1:
        - Uses effective CMC (respects domain cost reduction for Scion/Binding)
        - Requires early creatures for aggro decks (mulligan_require_creature_cmc)
        - More nuanced land count evaluation
        """
        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]
        land_count = len(lands)

        # Always keep at 5 or fewer
        if cards_in_hand <= 5:
            return True

        # Auto-mulligan unplayable
        if land_count == 0 or land_count >= 6:
            return False

        # Check for key cards from the gameplan
        key_cards = self.gameplan.mulligan_keys
        has_key = any(c.name in key_cards for c in spells)

        # Land range check
        if land_count < self.gameplan.mulligan_min_lands:
            return False
        if land_count > self.gameplan.mulligan_max_lands and cards_in_hand == 7:
            return False

        # Count early plays using effective CMC (respects domain, convoke, etc.)
        early_plays = sum(1 for s in spells if self._effective_cmc(s) <= 3)
        early_creatures = [s for s in spells
                           if s.template.is_creature and self._effective_cmc(s) <= 3]

        # Creature requirement for aggro decks
        req_cmc = self.gameplan.mulligan_require_creature_cmc
        if req_cmc > 0 and cards_in_hand == 7:
            has_early_creature = any(
                self._effective_cmc(s) <= req_cmc for s in early_creatures
            )
            # At 7, aggro MUST have an early creature (or key card which is a creature)
            if not has_early_creature and not has_key:
                return False

        # Combo set requirement: need at least one card from EACH set
        combo_sets = self.gameplan.mulligan_combo_sets
        if combo_sets and cards_in_hand == 7:
            hand_names = {c.name for c in hand}
            for required_set in combo_sets:
                if not hand_names.intersection(required_set):
                    return False  # Missing an entire combo piece category

        # At 7 cards, be strict: need lands + key card or multiple playables
        if cards_in_hand == 7:
            if has_key and land_count >= 2:
                return True
            if has_key and land_count == 1 and early_plays >= 3:
                return True  # 1 land + key + many cheap spells (aggro)
            # Check if we have plays for the first 2-3 turns
            if land_count >= 2 and early_plays >= 2:
                return True
            return False

        # At 6, be more lenient
        if land_count >= 1 and (has_key or len(spells) >= 2):
            return True

        return False

    def card_keep_score(self, card, hand: list) -> float:
        """Score a card for mulligan bottoming. Higher = keep."""
        score = 0.0
        t = card.template
        lands_in_hand = sum(1 for c in hand if c.template.is_land)

        if t.is_land:
            score += 10.0 if lands_in_hand <= 3 else 2.0
            score += self.gameplan.land_priorities.get(card.name, 0.0) * 0.5
            if t.produces_mana:
                score += len(t.produces_mana) * 0.5
        else:
            # Base: prefer cheap spells
            score += max(0, 5 - (t.cmc or 0))

            # Key cards from gameplan
            if card.name in self.gameplan.mulligan_keys:
                score += 8.0

            # Cards in the first goal's card_roles (engines, payoffs, enablers)
            first_goal = self.gameplan.goals[0] if self.gameplan.goals else None
            if first_goal:
                for role_name, role_cards in first_goal.card_roles.items():
                    if card.name in role_cards:
                        # Weight by role importance: engines > payoffs > enablers
                        role_weight = {'engines': 8.0, 'payoffs': 7.0, 'enablers': 6.0,
                                       'fillers': 3.0, 'protection': 4.0, 'interaction': 5.0}
                        score += role_weight.get(role_name, 4.0)
                        break

            # Always-early cards
            if card.name in self.gameplan.always_early:
                score += 6.0

        return score


# ═══════════════════════════════════════════════════════════════════
# Generic Combo Readiness — data-driven, not deck-specific
# ═══════════════════════════════════════════════════════════════════

def generic_combo_readiness(game, player_idx: int, engine: "GoalEngine"):
    """Generic combo readiness check derived from gameplan data.

    Works for any combo archetype by inspecting:
    - Current goal's card_roles (payoffs, enablers, rituals)
    - Resource zone targets (storm count, graveyard count, mana)
    - Available cards in hand vs what the goal needs

    Returns: (is_ready: bool, confidence: float 0-1, reason: str)
    """
    me = game.players[player_idx]
    goal = engine.current_goal
    hand_names = {c.name for c in me.hand}
    bf_names = {c.name for c in me.battlefield}

    # Check payoff availability
    payoffs = goal.card_roles.get("payoffs", set())
    available_payoffs = payoffs & (hand_names | bf_names)
    if not available_payoffs and payoffs:
        return False, 0.1, f"No payoff available (need one of: {', '.join(list(payoffs)[:3])})"

    # Check enabler availability
    enablers = goal.card_roles.get("enablers", set())
    available_enablers = enablers & (hand_names | bf_names)

    # Resource zone checks
    if goal.resource_zone == "storm":
        storm = getattr(game, '_global_storm_count', 0)
        target = goal.resource_target or 5
        if storm >= target:
            return True, 0.9, f"Storm count {storm} >= {target}, ready to fire payoff"
        # Check ritual density in hand for potential storm
        rituals = goal.card_roles.get("rituals", set())
        ritual_count = len(rituals & hand_names)
        projected = storm + ritual_count
        if projected >= target and available_payoffs:
            return True, 0.7, f"Storm {storm} + {ritual_count} rituals in hand = {projected} projected (target {target})"
        return False, projected / max(target, 1), f"Storm {storm}, projected {projected}, need {target}"

    elif goal.resource_zone == "graveyard":
        gy_count = len(me.graveyard)
        target = goal.resource_target or 3
        min_cmc = getattr(goal, 'resource_min_cmc', 0)
        from engine.cards import CardType
        gy_creatures = sum(1 for c in me.graveyard
                          if CardType.CREATURE in c.template.card_types
                          and (c.template.cmc or 0) >= min_cmc)
        if gy_creatures >= target and available_payoffs:
            return True, 0.8, f"{gy_creatures} creatures in graveyard (target {target}, min_cmc {min_cmc}), payoff ready"
        return False, gy_creatures / max(target, 1), f"{gy_creatures} creatures in graveyard, need {target} (min_cmc {min_cmc})"

    elif goal.resource_zone == "mana":
        mana = len(me.untapped_lands)
        target = goal.resource_target or 5
        if mana >= target and available_payoffs:
            return True, 0.85, f"{mana} mana available (target {target}), payoff ready"
        return False, mana / max(target, 1), f"{mana} mana, need {target}"

    # Default: check if we have both enablers and payoffs
    if available_payoffs and (available_enablers or not enablers):
        confidence = 0.6 + 0.1 * len(available_payoffs) + 0.1 * len(available_enablers)
        return True, min(confidence, 1.0), (
            f"Payoff ({', '.join(available_payoffs)}) and "
            f"enablers ({', '.join(available_enablers) if available_enablers else 'none needed'}) ready")

    return False, 0.3, "Missing key combo pieces"


# ═══════════════════════════════════════════════════════════════════
# Deck Gameplan Registry — declarative configs for all 12 decks
# ═══════════════════════════════════════════════════════════════════

def _build_amulet_titan() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Amulet Titan",
        goals=[
            Goal(
                goal_type=GoalType.DEPLOY_ENGINE,
                description="Deploy Amulet of Vigor ASAP — it's the deck's engine",
                card_priorities={
                    "Amulet of Vigor": 25.0,
                    "Arboreal Grazer": 18.0,
                    "Explore": 15.0,
                    "Dryad of the Ilysian Grove": 14.0,
                    "Azusa, Lost but Seeking": 14.0,
                },
                card_roles={
                    "engines": {"Amulet of Vigor"},
                    "enablers": {"Arboreal Grazer", "Explore", "Dryad of the Ilysian Grove",
                                 "Azusa, Lost but Seeking", "Green Sun's Zenith"},
                },
            ),
            Goal(
                goal_type=GoalType.RAMP,
                description="Ramp to 5+ mana for Primeval Titan using bounce lands + Amulet",
                card_priorities={
                    "Dryad of the Ilysian Grove": 18.0,
                    "Azusa, Lost but Seeking": 18.0,
                    "Explore": 15.0,
                    "Summoner's Pact": 14.0,
                    "Green Sun's Zenith": 14.0,
                    "Arboreal Grazer": 12.0,
                    # Titan itself — if we have 6 mana, just cast it!
                    "Primeval Titan": 20.0,
                    "Cultivator Colossus": 18.0,
                },
                card_roles={
                    "enablers": {"Dryad of the Ilysian Grove", "Azusa, Lost but Seeking",
                                 "Explore", "Arboreal Grazer"},
                    "payoffs": {"Primeval Titan", "Cultivator Colossus"},
                },
                # 5 mana is enough with bounce lands — Simic Growth Chamber
                # untaps with Amulet and produces 2 mana, so 5 lands = 6+ mana.
                # Also, Castle Garenbrig converts 6 green to GGGGGG for Titan.
                resource_target=5,
                resource_zone="mana",
            ),
            Goal(
                goal_type=GoalType.EXECUTE_PAYOFF,
                description="Deploy Primeval Titan or Cultivator Colossus to close the game",
                card_priorities={
                    "Primeval Titan": 25.0,
                    "Cultivator Colossus": 22.0,
                    "Summoner's Pact": 20.0,
                    "Green Sun's Zenith": 18.0,
                },
                card_roles={
                    "payoffs": {"Primeval Titan", "Cultivator Colossus"},
                    "enablers": {"Summoner's Pact", "Green Sun's Zenith"},
                },
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Attack with Titan and close the game",
                card_priorities={
                    "Primeval Titan": 20.0,
                    "Cultivator Colossus": 18.0,
                },
            ),
        ],
        mulligan_keys={"Amulet of Vigor", "Arboreal Grazer", "Dryad of the Ilysian Grove",
                        "Summoner's Pact", "Green Sun's Zenith"},
        mulligan_min_lands=2,
        mulligan_max_lands=5,  # Titan wants lots of lands
        land_priorities={
            "Simic Growth Chamber": 10.0,
            "Gruul Turf": 10.0,
            "Selesnya Sanctuary": 10.0,
            "Boros Garrison": 10.0,
            "Tolaria West": 5.0,
            "Castle Garenbrig": 4.0,
            "Urza's Saga": 3.0,
            "Forest": 2.0,
            "Cavern of Souls": 2.0,
        },
        always_early={"Amulet of Vigor", "Arboreal Grazer"},
        archetype="combo",
        critical_pieces={"Primeval Titan", "Cultivator Colossus"},
        fallback_goals=[
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Plan B: attack with ramp creatures (Dryad, Azusa, Grazer)",
                card_priorities={
                    "Dryad of the Ilysian Grove": 15.0,
                    "Azusa, Lost but Seeking": 12.0,
                    "Arboreal Grazer": 8.0,
                },
            ),
        ],
    )


def _build_ruby_storm() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Ruby Storm",
        goals=[
            Goal(
                goal_type=GoalType.DEPLOY_ENGINE,
                description="Deploy Ruby Medallion or Ral, or accumulate rituals in hand",
                card_priorities={
                    "Ruby Medallion": 25.0,
                    "Ral, Monsoon Mage // Ral, Leyline Prodigy": 20.0,
                    # In DEPLOY_ENGINE, only cast cantrips to dig for pieces.
                    # Do NOT cast rituals yet — save them for the combo turn.
                    "Opt": 14.0,
                    "Sleight of Hand": 14.0,
                    # Manamorphose is a cantrip that replaces itself — OK to cast early
                    "Manamorphose": 15.0,
                },
                card_roles={
                    "engines": {"Ruby Medallion",
                                "Ral, Monsoon Mage // Ral, Leyline Prodigy"},
                    "fillers": {"Opt", "Sleight of Hand", "Manamorphose"},
                },
            ),
            Goal(
                goal_type=GoalType.EXECUTE_PAYOFF,
                description="Chain ALL rituals + cantrips in one turn, then fire Grapeshot/Empty the Warrens",
                card_priorities={
                    # Rituals FIRST — they generate mana AND build storm
                    "Pyretic Ritual": 30.0,
                    "Desperate Ritual": 30.0,
                    "Manamorphose": 28.0,  # cantrip + mana = chain extender
                    # Past in Flames lets us recast all rituals from GY
                    "Past in Flames": 26.0,
                    # Tutors find the kill — cast after rituals
                    "Wish": 22.0,
                    "Gifts Ungiven": 22.0,
                    # Cantrips dig for more rituals/finisher
                    "Opt": 20.0,
                    "Sleight of Hand": 20.0,
                    # Finishers — fire after building storm count
                    # Storm gating in _choose_spell prevents firing at low storm
                    "Grapeshot": 18.0,
                    "Empty the Warrens": 17.0,
                    # Galvanic Relay is a BACKUP plan — only if no finisher available
                    "Galvanic Relay": 12.0,
                },
                card_roles={
                    "payoffs": {"Grapeshot", "Empty the Warrens"},
                    "enablers": {"Pyretic Ritual", "Desperate Ritual", "Manamorphose",
                                 "Past in Flames", "Wish", "Gifts Ungiven",
                                 "Opt", "Sleight of Hand", "Galvanic Relay"},
                    "rituals": {"Pyretic Ritual", "Desperate Ritual", "Manamorphose"},
                },
                resource_zone="storm",
                resource_target=5,
            ),
        ],
        mulligan_keys={"Pyretic Ritual", "Desperate Ritual", "Ruby Medallion",
                        "Manamorphose", "Past in Flames", "Wish", "Gifts Ungiven"},
        mulligan_min_lands=1,
        mulligan_max_lands=3,
        mulligan_combo_sets=[
            # Need mana production (ritual or medallion)
            {"Pyretic Ritual", "Desperate Ritual", "Ruby Medallion",
             "Manamorphose", "Ral, Monsoon Mage // Ral, Leyline Prodigy"},
            # Need payoff access (finisher or tutor)
            {"Grapeshot", "Empty the Warrens", "Wish", "Gifts Ungiven",
             "Past in Flames", "Galvanic Relay"},
        ],
        land_priorities={
            "Spirebluff Canal": 5.0,
            "Steam Vents": 3.0,
            "Scalding Tarn": 4.0,
            "Mountain": 2.0,
            "Island": 1.0,
        },
        # Only Ruby Medallion is always_early — Ral should only deploy during DEPLOY_ENGINE
        # Casting Ral during EXECUTE_PAYOFF wastes mana that should go to rituals
        always_early={"Ruby Medallion"},
        archetype="combo",
        critical_pieces={"Grapeshot", "Empty the Warrens", "Galvanic Relay", "Past in Flames"},
        combo_readiness_check=generic_combo_readiness,
        fallback_goals=[
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Plan B: attack with Ral or any creatures",
                card_priorities={
                    "Ral, Monsoon Mage // Ral, Leyline Prodigy": 20.0,
                },
            ),
        ],
    )


def _build_living_end() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Living End",
        goals=[
            Goal(
                goal_type=GoalType.FILL_RESOURCE,
                description="Cycle creatures into GY while ramping to 3 mana for cascade",
                card_priorities={
                    # Street Wraith is FREE (pay 2 life) — always cycle first
                    "Street Wraith": 28.0,
                    # Paid cyclers: HIGH priority — we need 3+ creatures in GY
                    "Striped Riverwinder": 26.0,
                    "Architects of Will": 26.0,
                    "Curator of Mysteries": 26.0,
                    "Waker of Waves": 24.0,
                    # Cascade spells: LOWER than cyclers during FILL_RESOURCE
                    # Only cast when resource_target is met (checked in prefer_cycling)
                    "Shardless Agent": 15.0,
                    "Demonic Dread": 15.0,
                },
                card_roles={
                    "fillers": {"Street Wraith", "Striped Riverwinder",
                                "Architects of Will", "Curator of Mysteries",
                                "Waker of Waves"},
                    "payoffs": {"Shardless Agent", "Demonic Dread"},
                },
                prefer_cycling=True,
                # Need 2+ creatures in GY before cascading. Real Living End
                # cascades T3-T4 with 2-3 creatures in GY. Speed matters more
                # than a full graveyard against aggro.
                resource_target=2,
                resource_zone="graveyard",
            ),
            Goal(
                goal_type=GoalType.EXECUTE_PAYOFF,
                description="Cascade into Living End NOW — cast Shardless Agent or Demonic Dread",
                card_priorities={
                    # Cascade spells are the #1 priority
                    "Shardless Agent": 30.0,
                    "Demonic Dread": 30.0,
                    # Keep cycling for more value if we can't cascade yet
                    "Street Wraith": 12.0,
                    "Striped Riverwinder": 10.0,
                    "Architects of Will": 10.0,
                    "Curator of Mysteries": 10.0,
                    "Waker of Waves": 10.0,
                },
                card_roles={
                    "payoffs": {"Shardless Agent", "Demonic Dread"},
                    "fillers": {"Street Wraith", "Striped Riverwinder",
                                "Architects of Will", "Curator of Mysteries",
                                "Waker of Waves"},
                },
                prefer_cycling=True,
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Attack with reanimated creatures to close the game",
                card_priorities={
                    "Shardless Agent": 15.0,
                    "Demonic Dread": 15.0,
                },
            ),
        ],
        mulligan_keys={"Shardless Agent", "Demonic Dread", "Street Wraith",
                        "Striped Riverwinder", "Architects of Will"},
        mulligan_min_lands=1,
        mulligan_max_lands=4,
        mulligan_combo_sets=[
            # Need a cascade spell
            {"Shardless Agent", "Demonic Dread"},
            # Need cyclers to fill the graveyard
            {"Street Wraith", "Striped Riverwinder", "Architects of Will",
             "Curator of Mysteries", "Waker of Waves"},
        ],
        reactive_only={"Force of Negation", "Subtlety"},
        land_priorities={
            "Blooming Marsh": 5.0,
            "Botanical Sanctum": 5.0,
            "Verdant Catacombs": 4.0,
            "Misty Rainforest": 4.0,
            "Zagoth Triome": 2.0,
            "Forest": 2.0,
        },
        archetype="combo",
        critical_pieces={"Shardless Agent", "Demonic Dread"},
        combo_readiness_check=generic_combo_readiness,
        fallback_goals=[
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Plan B: attack with cycled creatures if cascade is unavailable",
                card_priorities={},
            ),
        ],
    )


def _build_goryos_vengeance() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Goryo's Vengeance",
        goals=[
            Goal(
                goal_type=GoalType.DISRUPT,
                description="Early disruption with Thoughtseize while setting up",
                card_priorities={
                    "Thoughtseize": 18.0,
                    "Unmarked Grave": 22.0,  # also setup
                    "Faithful Mending": 20.0,  # also setup
                },
                card_roles={
                    "enablers": {"Unmarked Grave", "Faithful Mending", "Thoughtseize"},
                },
                min_turns=1,  # Only 1 turn of disruption, then move to FILL_RESOURCE
            ),
            Goal(
                goal_type=GoalType.FILL_RESOURCE,
                description="Get Griselbrand or Atraxa into the graveyard",
                card_priorities={
                    "Unmarked Grave": 25.0,
                    "Faithful Mending": 22.0,
                    "Thoughtseize": 8.0,
                },
                card_roles={
                    "enablers": {"Unmarked Grave", "Faithful Mending"},
                },
                resource_target=1,
                resource_zone="graveyard",
                resource_min_cmc=5,  # Only count Griselbrand/Atraxa, not Solitude
            ),
            Goal(
                goal_type=GoalType.EXECUTE_PAYOFF,
                description="Reanimate with Goryo's Vengeance or Persist",
                card_priorities={
                    "Goryo's Vengeance": 25.0,
                    "Persist": 22.0,
                    "Unburial Rites": 18.0,
                    "Undying Evil": 15.0,
                    "Ephemerate": 14.0,
                },
                card_roles={
                    "payoffs": {"Goryo's Vengeance", "Persist", "Unburial Rites"},
                    "protection": {"Undying Evil", "Ephemerate"},
                },
                resource_min_cmc=5,  # Only reanimate high-CMC targets (Griselbrand/Atraxa)
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Attack with reanimated creature, protect with blink",
                card_priorities={
                    "Ephemerate": 15.0,
                    "Undying Evil": 12.0,
                    "Solitude": 10.0,
                },
                card_roles={
                    "protection": {"Ephemerate", "Undying Evil"},
                    "interaction": {"Solitude"},
                },
            ),
        ],
        mulligan_keys={"Goryo's Vengeance", "Persist", "Unmarked Grave",
                        "Faithful Mending", "Griselbrand", "Archon of Cruelty"},
        mulligan_min_lands=1,
        mulligan_max_lands=4,
        mulligan_combo_sets=[
            # Need a reanimation spell
            {"Goryo's Vengeance", "Persist", "Unburial Rites"},
            # Need an enabler or a target to discard
            {"Unmarked Grave", "Faithful Mending", "Tainted Indulgence",
             "Griselbrand", "Archon of Cruelty"},
        ],
        reactive_only={"Solitude", "Undying Evil", "Ephemerate"},
        always_early={"Unmarked Grave", "Thoughtseize", "Faithful Mending"},
        # NOTE: Goryo's/Persist removed from always_early — they need a GY target first
        archetype="combo",
        critical_pieces={"Goryo's Vengeance", "Persist", "Unburial Rites",
                         "Griselbrand", "Archon of Cruelty"},
    )


def _build_boros_energy() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Boros Energy",
        goals=[
            Goal(
                goal_type=GoalType.CURVE_OUT,
                description="Deploy efficient creatures on curve while interacting",
                card_priorities={
                    "Guide of Souls": 20.0,
                    "Ocelot Pride": 20.0,
                    "Ragavan, Nimble Pilferer": 22.0,
                    "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger": 18.0,
                    "Galvanic Discharge": 19.0,  # early removal is critical vs aggro
                    "Thraben Charm": 15.0,        # flexible removal/utility
                    "Voice of Victory": 16.0,
                    "Goblin Bombardment": 14.0,
                    "Seasoned Pyromancer": 12.0,
                    "Ranger-Captain of Eos": 12.0,
                },
                card_roles={
                    "enablers": {"Guide of Souls", "Ocelot Pride", "Ragavan, Nimble Pilferer"},
                    "payoffs": {"Ajani, Nacatl Pariah // Ajani, Nacatl Avenger",
                                "Seasoned Pyromancer", "Ranger-Captain of Eos"},
                    "interaction": {"Galvanic Discharge", "Thraben Charm"},
                },
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Attack aggressively, use removal to clear blockers",
                card_priorities={
                    "Galvanic Discharge": 15.0,
                    "Thraben Charm": 12.0,
                    "Phlage, Titan of Fire's Fury": 18.0,
                    "Blood Moon": 10.0,
                },
                card_roles={
                    "interaction": {"Galvanic Discharge", "Thraben Charm"},
                    "payoffs": {"Phlage, Titan of Fire's Fury"},
                },
            ),
            Goal(
                goal_type=GoalType.CLOSE_GAME,
                description="Convert board advantage into lethal damage",
                card_priorities={
                    "Goblin Bombardment": 15.0,
                    "Phlage, Titan of Fire's Fury": 12.0,
                },
            ),
        ],
        mulligan_keys={"Guide of Souls", "Ocelot Pride", "Ragavan, Nimble Pilferer",
                        "Ajani, Nacatl Pariah // Ajani, Nacatl Avenger"},
        mulligan_min_lands=2,
        mulligan_max_lands=3,
        mulligan_require_creature_cmc=2,  # Aggro: need a CMC 1-2 creature
        always_early={"Guide of Souls", "Ocelot Pride", "Ragavan, Nimble Pilferer"},
        archetype="aggro",
    )


def _build_domain_zoo() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Domain Zoo",
        goals=[
            Goal(
                goal_type=GoalType.CURVE_OUT,
                description="Deploy efficient threats: Ragavan → Kavu/Brawler → Scion",
                card_priorities={
                    "Ragavan, Nimble Pilferer": 22.0,
                    "Territorial Kavu": 20.0,
                    "Nishoba Brawler": 20.0,
                    "Scion of Draco": 16.0,
                    "Orcish Bowmasters": 18.0,
                    "Stubborn Denial": 5.0,
                },
                card_roles={
                    "enablers": {"Ragavan, Nimble Pilferer"},
                    "payoffs": {"Territorial Kavu", "Nishoba Brawler", "Scion of Draco"},
                    "interaction": {"Orcish Bowmasters"},
                },
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Attack with domain-powered creatures, remove blockers",
                card_priorities={
                    "Lightning Bolt": 15.0,
                    "Tribal Flames": 18.0,
                    "Leyline Binding": 14.0,
                    "Phlage, Titan of Fire's Fury": 16.0,
                },
                card_roles={
                    "interaction": {"Lightning Bolt", "Tribal Flames", "Leyline Binding"},
                },
            ),
            Goal(
                goal_type=GoalType.CLOSE_GAME,
                description="Burn face to close out the game",
                card_priorities={
                    "Lightning Bolt": 18.0,
                    "Tribal Flames": 20.0,
                },
            ),
        ],
        mulligan_keys={"Ragavan, Nimble Pilferer", "Territorial Kavu", "Nishoba Brawler"},
        mulligan_min_lands=2,
        mulligan_max_lands=4,  # Domain Zoo wants 4+ lands for domain count
        mulligan_effective_cmc={
            "Scion of Draco": 3,       # CMC 12 but domain cost ~3 (needs 3+ types)
            "Leyline Binding": 1,      # CMC 6 but domain cost ~1
        },
        mulligan_require_creature_cmc=2,  # Aggro: need a CMC 1-2 creature (Scion doesn't count)
        always_early={"Ragavan, Nimble Pilferer"},
        archetype="aggro",
    )


def _build_affinity() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Affinity",
        goals=[
            Goal(
                goal_type=GoalType.DEPLOY_ENGINE,
                description="Deploy artifact count enablers: Mox Opal, Springleaf Drum, 0-drops",
                card_priorities={
                    "Mox Opal": 25.0,
                    "Springleaf Drum": 22.0,
                    "Ornithopter": 20.0,
                    "Memnite": 20.0,
                    "Signal Pest": 18.0,
                },
                card_roles={
                    "engines": {"Mox Opal", "Springleaf Drum"},
                    "enablers": {"Ornithopter", "Memnite"},
                },
            ),
            Goal(
                goal_type=GoalType.CURVE_OUT,
                description="Deploy threats and equipment: Cranial Plating, Nettlecyst",
                card_priorities={
                    "Cranial Plating": 22.0,
                    "Nettlecyst": 20.0,
                    "Thought Monitor": 18.0,
                    "Sojourner's Companion": 16.0,
                    "Frogmite": 14.0,
                    "Signal Pest": 12.0,
                },
                card_roles={
                    "payoffs": {"Cranial Plating", "Nettlecyst"},
                    "enablers": {"Thought Monitor", "Sojourner's Companion", "Frogmite"},
                },
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Equip and attack for massive damage",
                card_priorities={
                    "Cranial Plating": 20.0,
                    "Signal Pest": 15.0,
                },
            ),
        ],
        mulligan_keys={"Mox Opal", "Springleaf Drum", "Cranial Plating",
                        "Ornithopter", "Memnite"},
        mulligan_min_lands=1,
        mulligan_max_lands=3,
        always_early={"Mox Opal", "Ornithopter", "Memnite", "Springleaf Drum"},
        archetype="aggro",
    )


def _build_eldrazi_tron() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Eldrazi Tron",
        goals=[
            Goal(
                goal_type=GoalType.RAMP,
                description="Assemble Tron lands or deploy Eldrazi Temple for fast mana",
                card_priorities={
                    "Expedition Map": 22.0,
                    "Chalice of the Void": 20.0,
                    "Eldrazi Mimic": 15.0,
                    "Matter Reshaper": 12.0,
                },
                card_roles={
                    "engines": {"Expedition Map"},
                    "enablers": {"Chalice of the Void"},
                },
                resource_target=5,
                resource_zone="mana",
            ),
            Goal(
                goal_type=GoalType.CURVE_OUT,
                description="Deploy Eldrazi threats: Thought-Knot, Reality Smasher",
                card_priorities={
                    "Thought-Knot Seer": 22.0,
                    "Reality Smasher": 22.0,
                    "Endbringer": 18.0,
                    "Walking Ballista": 15.0,
                    "Matter Reshaper": 12.0,
                    "Eldrazi Mimic": 10.0,
                },
                card_roles={
                    "payoffs": {"Thought-Knot Seer", "Reality Smasher", "Endbringer"},
                    "enablers": {"Walking Ballista", "Matter Reshaper"},
                },
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Attack with Eldrazi, use Ugin or All Is Dust as finishers",
                card_priorities={
                    "Ugin, the Spirit Dragon": 20.0,
                    "All Is Dust": 18.0,
                    "Kozilek's Command": 15.0,
                },
                card_roles={
                    "payoffs": {"Ugin, the Spirit Dragon", "All Is Dust"},
                },
            ),
        ],
        mulligan_keys={"Expedition Map", "Chalice of the Void", "Thought-Knot Seer",
                        "Reality Smasher", "Eldrazi Mimic"},
        mulligan_min_lands=2,
        mulligan_max_lands=5,
        land_priorities={
            "Urza's Tower": 8.0,
            "Urza's Mine": 8.0,
            "Urza's Power Plant": 8.0,
            "Eldrazi Temple": 10.0,
            "Cavern of Souls": 5.0,
        },
        always_early={"Expedition Map", "Chalice of the Void"},
        archetype="midrange",
    )


def _build_jeskai_blink() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Jeskai Blink",
        goals=[
            Goal(
                goal_type=GoalType.INTERACT,
                description="Establish board control with removal, counters, and evoke",
                card_priorities={
                    "Lightning Bolt": 22.0,
                    "Solitude": 22.0,             # evoke is free removal vs big threats
                    "Prismatic Ending": 20.0,
                    "Ragavan, Nimble Pilferer": 20.0,
                    "Snapcaster Mage": 15.0,
                    "Ephemerate": 14.0,           # blink Solitude for double removal
                    "Teferi, Time Raveler": 16.0, # early Teferi shuts down instants
                },
                card_roles={
                    "interaction": {"Lightning Bolt", "Prismatic Ending", "Counterspell",
                                    "Spell Snare", "Solitude"},
                    "enablers": {"Ragavan, Nimble Pilferer", "Snapcaster Mage",
                                 "Ephemerate"},
                },
                hold_mana=True,
                min_turns=2,
            ),
            Goal(
                goal_type=GoalType.GRIND_VALUE,
                description="Generate value with blink effects and ETB creatures",
                card_priorities={
                    "Solitude": 20.0,
                    "Ephemerate": 18.0,
                    "Subtlety": 16.0,
                    "Quantum Riddler": 15.0,
                    "Teferi, Time Raveler": 14.0,
                    "Spell Queller": 14.0,
                },
                card_roles={
                    "payoffs": {"Solitude", "Subtlety", "Quantum Riddler"},
                    "enablers": {"Ephemerate"},
                    "interaction": {"Teferi, Time Raveler", "Spell Queller"},
                },
                hold_mana=True,
            ),
            Goal(
                goal_type=GoalType.CLOSE_GAME,
                description="Close with Solitude beats or Snapcaster value",
                card_priorities={
                    "Solitude": 15.0,
                    "Snapcaster Mage": 12.0,
                },
            ),
        ],
        mulligan_keys={"Lightning Bolt", "Ragavan, Nimble Pilferer", "Counterspell",
                        "Solitude", "Prismatic Ending"},
        mulligan_min_lands=2,
        mulligan_max_lands=4,
        reactive_only={"Force of Negation", "Counterspell", "Spell Snare"},
        archetype="control",
    )


def _build_izzet_prowess() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Izzet Prowess",
        goals=[
            Goal(
                goal_type=GoalType.CURVE_OUT,
                description="Deploy prowess creatures ASAP",
                card_priorities={
                    "Monastery Swiftspear": 22.0,
                    "Dragon's Rage Channeler": 22.0,
                    "Slickshot Show-Off": 20.0,
                    "Cori-Steel Cutter": 18.0,
                },
                card_roles={
                    "enablers": {"Monastery Swiftspear", "Dragon's Rage Channeler",
                                 "Slickshot Show-Off", "Cori-Steel Cutter"},
                },
            ),
            Goal(
                goal_type=GoalType.PUSH_DAMAGE,
                description="Chain spells to trigger prowess and push damage",
                card_priorities={
                    "Lightning Bolt": 20.0,
                    "Lava Dart": 18.0,
                    "Unholy Heat": 16.0,
                    "Mutagenic Growth": 22.0,
                    "Violent Urge": 18.0,
                    "Mishra's Bauble": 15.0,
                    "Expressive Iteration": 14.0,
                    "Preordain": 12.0,
                },
                card_roles={
                    "enablers": {"Mutagenic Growth", "Violent Urge", "Mishra's Bauble",
                                 "Lava Dart"},
                    "interaction": {"Lightning Bolt", "Unholy Heat"},
                    "fillers": {"Expressive Iteration", "Preordain"},
                },
            ),
            Goal(
                goal_type=GoalType.CLOSE_GAME,
                description="Burn face to finish, deploy Murktide as backup",
                card_priorities={
                    "Lightning Bolt": 20.0,
                    "Lava Dart": 15.0,
                    "Murktide Regent": 18.0,
                },
            ),
        ],
        mulligan_keys={"Monastery Swiftspear", "Dragon's Rage Channeler",
                        "Slickshot Show-Off"},
        mulligan_min_lands=2,
        mulligan_max_lands=3,
        mulligan_require_creature_cmc=2,  # Prowess: need a CMC 1-2 creature
        always_early={"Monastery Swiftspear", "Dragon's Rage Channeler"},
        archetype="aggro",
    )


def _build_dimir_midrange() -> DeckGameplan:
    return DeckGameplan(
        deck_name="Dimir Midrange",
        goals=[
            Goal(
                goal_type=GoalType.DISRUPT,
                description="Disrupt opponent's hand and deploy efficient threats",
                card_priorities={
                    "Thoughtseize": 22.0,          # was 20 — T1 Thoughtseize is critical
                    "Fatal Push": 22.0,             # was 18 — T1 Push is critical vs aggro
                    "Orcish Bowmasters": 22.0,
                    "Dauthi Voidwalker": 20.0,
                    "Psychic Frog": 18.0,
                },
                card_roles={
                    "interaction": {"Thoughtseize", "Fatal Push"},
                    "enablers": {"Orcish Bowmasters", "Dauthi Voidwalker", "Psychic Frog"},
                },
                hold_mana=True,
            ),
            Goal(
                goal_type=GoalType.GRIND_VALUE,
                description="Grind with Psychic Frog, Bowmasters, and card advantage",
                card_priorities={
                    "Psychic Frog": 20.0,
                    "Orcish Bowmasters": 18.0,
                    "Archmage's Charm": 16.0,
                    "Consider": 12.0,
                    "Murktide Regent": 18.0,
                    "Subtlety": 14.0,
                },
                card_roles={
                    "payoffs": {"Psychic Frog", "Murktide Regent"},
                    "interaction": {"Archmage's Charm", "Drown in the Loch"},
                },
                hold_mana=True,
            ),
            Goal(
                goal_type=GoalType.CLOSE_GAME,
                description="Close with Murktide or Psychic Frog",
                card_priorities={
                    "Murktide Regent": 20.0,
                    "Psychic Frog": 15.0,
                },
            ),
        ],
        mulligan_keys={"Thoughtseize", "Orcish Bowmasters", "Psychic Frog",
                        "Fatal Push", "Dauthi Voidwalker"},
        mulligan_min_lands=2,
        mulligan_max_lands=4,
        reactive_only={"Counterspell", "Spell Pierce", "Drown in the Loch", "Subtlety"},
        archetype="midrange",
    )


def _build_4c_omnath() -> DeckGameplan:
    return DeckGameplan(
        deck_name="4c Omnath",
        goals=[
            Goal(
                goal_type=GoalType.INTERACT,
                description="Interact early while deploying Omnath ASAP for life gain",
                card_priorities={
                    # Omnath is THE priority — 4 life on ETB is crucial vs aggro
                    # and landfall triggers generate massive value
                    "Omnath, Locus of Creation": 26.0,
                    "Prismatic Ending": 22.0,
                    "Galvanic Discharge": 20.0,
                    "Teferi, Time Raveler": 20.0,
                    "Wrenn and Six": 18.0,
                    "Wrath of the Skies": 16.0,
                    "Solitude": 22.0,  # evoke removal available from T1
                    "Orim's Chant": 10.0,
                    "Ephemerate": 15.0,  # blink Solitude/Omnath
                },
                card_roles={
                    "interaction": {"Prismatic Ending", "Galvanic Discharge",
                                    "Orim's Chant", "Wrath of the Skies", "Solitude"},
                    "engines": {"Teferi, Time Raveler", "Wrenn and Six"},
                    "payoffs": {"Omnath, Locus of Creation"},
                },
                min_turns=2,  # transition after 2 turns, not 5
            ),
            Goal(
                goal_type=GoalType.GRIND_VALUE,
                description="Blink Omnath and value creatures for card/life advantage",
                card_priorities={
                    "Omnath, Locus of Creation": 25.0,
                    "Ephemerate": 22.0,  # blink Omnath = 4 more life + draw
                    "Solitude": 22.0,
                    "Eternal Witness": 18.0,
                    "Quantum Riddler": 16.0,
                    "Endurance": 14.0,
                    "Stock Up": 12.0,
                    "Phlage, Titan of Fire's Fury": 16.0,
                },
                card_roles={
                    "payoffs": {"Omnath, Locus of Creation"},
                    "enablers": {"Ephemerate", "Eternal Witness"},
                    "interaction": {"Solitude", "Endurance"},
                },
            ),
            Goal(
                goal_type=GoalType.CLOSE_GAME,
                description="Close with Omnath value or Phlage",
                card_priorities={
                    "Omnath, Locus of Creation": 20.0,
                    "Phlage, Titan of Fire's Fury": 18.0,
                    "Wrath of the Skies": 15.0,
                },
            ),
        ],
        mulligan_keys={"Omnath, Locus of Creation", "Teferi, Time Raveler",
                        "Wrenn and Six", "Solitude", "Prismatic Ending"},
        mulligan_min_lands=2,
        mulligan_max_lands=4,
        reactive_only={"Endurance", "Orim's Chant"},  # Solitude removed: evoke is proactive removal
        archetype="control",
    )


# ═══════════════════════════════════════════════════════════════════
# Registry: deck_name -> DeckGameplan builder
# ═══════════════════════════════════════════════════════════════════

_GAMEPLAN_BUILDERS = {
    "Amulet Titan": _build_amulet_titan,
    "Ruby Storm": _build_ruby_storm,
    "Living End": _build_living_end,
    "Goryo's Vengeance": _build_goryos_vengeance,
    "Boros Energy": _build_boros_energy,
    "Domain Zoo": _build_domain_zoo,
    "Affinity": _build_affinity,
    "Eldrazi Tron": _build_eldrazi_tron,
    "Jeskai Blink": _build_jeskai_blink,
    "Izzet Prowess": _build_izzet_prowess,
    "Dimir Midrange": _build_dimir_midrange,
    "4c Omnath": _build_4c_omnath,
}


def get_gameplan(deck_name: str) -> Optional[DeckGameplan]:
    """Get the gameplan for a deck.

    Tries JSON gameplans first (decks/gameplans/), then falls back
    to the legacy Python builders for backwards compatibility.
    """
    # Try JSON loader first
    try:
        from decks.gameplan_loader import load_gameplan
        plan = load_gameplan(deck_name)
        if plan:
            return plan
    except ImportError:
        pass

    # Fallback to legacy Python builders
    builder = _GAMEPLAN_BUILDERS.get(deck_name)
    if builder:
        return builder()
    return None


def create_goal_engine(deck_name: str) -> Optional[GoalEngine]:
    """Create a GoalEngine for a deck. Returns None if no plan is registered."""
    plan = get_gameplan(deck_name)
    if plan:
        return GoalEngine(plan)
    return None
