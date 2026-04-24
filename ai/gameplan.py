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

    # Optional finer-grained archetype sub-type hint.  Used by
    # `ai.clock.combo_clock` to pick the resource-assembly target for
    # combo decks whose win condition is structurally different from
    # Storm's 8-resource chain (e.g. "cascade_reanimator" for Living
    # End: 3 mana + cascade spell + ~3 GY creatures fires the combo).
    # `None` falls back to the default assembly model.
    archetype_subtype: Optional[str] = None

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
        # Post-combo push override: while post_combo_push_turns > 0 (set by
        # mass-reanimate resolutions like Living End), force the final
        # PUSH_DAMAGE goal regardless of normal transition rules. This
        # keeps the deck swinging for 2-3 turns while the opponent has no
        # board, rather than reverting to curve_out / deploy_engine mid-push.
        me = self._get_me()
        if me is not None and getattr(me, 'post_combo_push_turns', 0) > 0:
            for g in self.gameplan.goals:
                if g.goal_type == GoalType.PUSH_DAMAGE:
                    return g
        if self.current_goal_idx < len(self.gameplan.goals):
            return self.gameplan.goals[self.current_goal_idx]
        return self.gameplan.goals[-1]  # stay on last goal

    def _get_me(self):
        """Return the PlayerState for this engine's player, or None.
        Requires the engine to have been linked to a game (see decide_main_phase)."""
        game = getattr(self, '_game_ref', None)
        if game is None:
            return None
        try:
            return game.players[self._player_idx]
        except Exception:
            return None

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

    def check_transition(self, game, player_idx: int):
        """Check if current goal should advance based on board state.
        Call at the start of each main phase."""
        # Pin the engine to this game/player so `current_goal` can detect
        # post_combo_push override without threading extra state through.
        self._game_ref = game
        self._player_idx = player_idx
        goal = self.current_goal
        me = game.players[player_idx]

        # Track turns in current goal
        turn = getattr(game, 'turn_number', getattr(game, 'display_turn', 0))
        if turn != self._last_turn:
            self.turns_in_goal += 1
            self._last_turn = turn

        # Don't advance past last goal
        if self.current_goal_idx >= len(self.gameplan.goals) - 1:
            return

        # Respect min_turns
        if self.turns_in_goal < goal.min_turns:
            return

        should_advance = False
        reason = ""

        gt = goal.goal_type

        if gt == GoalType.DEPLOY_ENGINE:
            # Advance when an engine card is on the battlefield
            engines = goal.card_roles.get('engines', set())
            deployed = [c.name for c in me.battlefield if c.name in engines]
            if deployed:
                should_advance = True
                reason = f"Engine online: {deployed[0]}"
            elif self.turns_in_goal >= 3:
                should_advance = True
                reason = "No engine after 3 turns, advancing anyway"

        elif gt == GoalType.DISRUPT:
            # Combo decks: advance once we've had time to disrupt
            should_advance = self.turns_in_goal >= 2
            reason = "Disruption window passed"

        elif gt == GoalType.FILL_RESOURCE:
            # Check resource target (GY creatures for Living End, mana for
            # Amulet, storm count for Storm, battlefield size for go-wide).
            zone = goal.resource_zone
            target = goal.resource_target or 3
            min_cmc = getattr(goal, 'resource_min_cmc', 0)
            resource_progress = 0
            if zone == "graveyard":
                from engine.cards import CardType
                resource_progress = sum(1 for c in me.graveyard
                                         if CardType.CREATURE in c.template.card_types
                                         and (c.template.cmc or 0) >= min_cmc)
            elif zone == "storm":
                resource_progress = me.spells_cast_this_turn
            elif zone == "mana":
                resource_progress = me.available_mana_estimate
            elif zone == "battlefield":
                resource_progress = len(me.creatures)

            if resource_progress >= target:
                should_advance = True
                reason = f"{resource_progress}/{target} ready in {zone}"
            # Payoff-in-hand fallback — require at least half the target so
            # we don't fire the combo into an empty resource pool. Living
            # End advancing at GY=0 bricks the combo: the cascade finds
            # Living End but returns zero creatures (seed 60103). Half-
            # target means the payoff is worth casting on the trajectory
            # we're already on, not a hope-and-pray.
            next_goal_idx = self.current_goal_idx + 1
            if (not should_advance
                    and next_goal_idx < len(self.gameplan.goals)):
                next_payoffs = self.gameplan.goals[next_goal_idx].card_roles.get('payoffs', set())
                has_payoff = any(c.name in next_payoffs for c in me.hand)
                half_target = max(1, target // 2)
                if (has_payoff and self.turns_in_goal >= 2
                        and resource_progress >= half_target):
                    should_advance = True
                    reason = (f"Payoff in hand, "
                              f"{resource_progress}/{target} partial in {zone}")

        elif gt == GoalType.INTERACT:
            # Control: advance after min_turns
            if self.turns_in_goal >= max(goal.min_turns, 2):
                should_advance = True
                reason = "Interaction phase complete"

        elif gt == GoalType.GRIND_VALUE:
            if self.turns_in_goal >= 2:
                should_advance = True
                reason = "Value phase complete"

        elif gt == GoalType.EXECUTE_PAYOFF:
            # Already executing — don't advance
            pass

        elif gt == GoalType.PUSH_DAMAGE:
            if self.turns_in_goal >= 2:
                should_advance = True
                reason = "Push damage phase complete"

        if should_advance:
            self.advance_goal(game, reason)


    # GoalEngine decision methods removed — EVPlayer handles all decisions.
    # GoalEngine is now a thin container for DeckGameplan + card role data.

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
            score += max(0, 5 - (t.cmc or 0))
            if card.name in self.gameplan.mulligan_keys:
                score += 8.0
            # Reactive-only cards shouldn't be mulligan-keep signals. A
            # deck's own gameplan marks them as "don't open with this" —
            # opening hand on the play / draw wants enablers and threats,
            # not answers waiting for a target. Cancel the mulligan_keys
            # bonus for cards the deck itself has flagged reactive
            # (audit F-R3-3 Zoo keeping Leyline Binding over creatures).
            if card.name in self.gameplan.reactive_only:
                score -= 8.0
            # Iterate ALL goals, not just the first. Multi-goal gameplans
            # place payoffs in later goals (Amulet Titan: Primeval Titan in
            # goal[1] RAMP, not goal[0] DEPLOY_ENGINE). Previously bottomed
            # the deck's win condition because only goal[0] was scanned.
            # Take the MAX role weight across goals so a payoff in any goal
            # gets the payoff weight regardless of goal ordering.
            best_role_weight = 0.0
            role_weight = {'engines': 8.0, 'payoffs': 7.0, 'enablers': 6.0,
                           'fillers': 3.0, 'protection': 4.0, 'interaction': 5.0}
            for goal in self.gameplan.goals:
                for role_name, role_cards in goal.card_roles.items():
                    if card.name in role_cards:
                        w = role_weight.get(role_name, 4.0)
                        if w > best_role_weight:
                            best_role_weight = w
            score += best_role_weight
            if card.name in self.gameplan.always_early:
                score += 6.0
            else:
                oracle = (t.oracle_text or '').lower()
                if any(kw in oracle for kw in ('destroy', 'exile target', 'damage to each')):
                    score += 4.0
            # Preserve critical-piece singletons. critical_pieces (from the
            # gameplan) enumerates the card names the deck cannot execute
            # its plan without — e.g. Storm's Grapeshot / Empty the Warrens
            # / Past in Flames. Bottoming the last copy of a critical piece
            # sabotages the deck's win condition. Boost score so a singleton
            # critical card never ends up the lowest-scored in hand.
            # Derivation: the "floor" equals the maximum achievable score
            # from normal role+key+cmc weights (8 engine + 8 key + 5 cmc_max
            # + 6 always_early = 27 cap). Setting floor at 20 keeps
            # criticals ranked above almost any normal keep.
            if card.name in self.gameplan.critical_pieces:
                same_copies_in_hand = sum(1 for c in hand if c.name == card.name)
                if same_copies_in_hand <= 1:
                    CRITICAL_SINGLETON_FLOOR = 20.0
                    score = max(score, CRITICAL_SINGLETON_FLOOR)
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
# Factory functions
# ═══════════════════════════════════════════════════════════════════

def get_gameplan(deck_name: str) -> Optional[DeckGameplan]:
    """Get the gameplan for a deck from JSON config."""
    try:
        from decks.gameplan_loader import load_gameplan
        return load_gameplan(deck_name)
    except ImportError:
        return None


def create_goal_engine(deck_name: str) -> Optional[GoalEngine]:
    """Create a GoalEngine for a deck. Returns None if no plan is registered."""
    plan = get_gameplan(deck_name)
    if plan:
        return GoalEngine(plan)
    return None
