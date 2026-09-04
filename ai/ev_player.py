"""EV-Based AI Player — data-driven MTG decision engine.

Architecture: get legal plays → score each via StrategyProfile → pick best.
All weights in ai/strategy_profile.py. All card effects from oracle text.
Combat, blocking, and response decisions delegate to existing modules.
"""
from __future__ import annotations
import os
import random
import re
from typing import Dict, List, Optional, Tuple, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate, Keyword
    from engine.stack import StackItem

from ai.deck_knowledge import DeckKnowledge
from ai.ev_evaluator import (
    EVSnapshot, snapshot_from_game, evaluate_board, creature_value,
    creature_threat_value,
)
# Phase 1 refactor: archetype-tied scaling weights are sourced from
# the LLM-at-decision-time helper, cached per (archetype, context).
# See `ai/llm_decision_scorer.py` for the contract.
from ai.llm_decision_scorer import (
    weight as _llm_weight,
    CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD,
    CTX_TRON_MANA_ADVANTAGE,
    CTX_AMULET_TITAN_MANA_BONUS,
    CTX_CYCLING_CASCADE_BOOST,
    CTX_CYCLING_GY_URGENCY,
    CTX_CYCLING_GAMEPLAN_BOOST,
    CTX_CYCLING_FREE_COST_BONUS,
)
from ai.scoring_constants import (
    conservative_land_retention,
    held_response_value_per_cmc,
    STARTING_HAND_SIZE,
    opp_threat_prob_from_density,
    REANIMATE_OVERRIDE_BONUS,
    FREE_CAST_TEMPO_BONUS,
    EVOKE_CARD_LOSS_MULTIPLIER,
    EVOKE_DESPERATE_BONUS,
    EVOKE_NO_TARGET_PENALTY,
    MIDGAME_HORIZON_TURNS,
    GAME_HORIZON_MIN_TURNS,
    GAME_HORIZON_MAX_COST_REDUCER,
    GAME_HORIZON_MAX_TRON,
    MODERN_AVG_GAME_LENGTH,
    NONCREATURE_COUNTER_DEAD_FLOOR,
    REMOVAL_THREAT_PREMIUM_SCALE,
    CHEAP_REMOVAL_ACTION_BONUS,
    LANDFALL_DEFERRAL_PENALTY,
    LAND_GAMEPLAN_PRIORITY_SCALE,
    X_BOARD_WIPE_WASTE_FLOOR,
    BLINK_FIZZLE_FLOOR,
    BLINK_ETB_RETRIGGER_BONUS,
    CHUMP_SENTINEL_VALUE,
    NO_CLOCK_FACE_VAL_MULTIPLIER,
    LANDFALL_TRIGGER_VALUE,
    ARTIFACT_LAND_SYNERGY_BONUS,
    CYCLING_CHEAP_COST_BONUS,
    CYCLING_GY_REANIMATE_BASE,
    CYCLING_GY_REANIMATE_PER_POWER,
    AVG_CREATURE_POWER,
    CLOCK_IMPACT_LIFE_SCALING,
    REANIMATE_TARGET_MIN_POWER,
    CONTROL_PATIENCE_OPP_CLOCK_THRESHOLD,
    LAND_SACRIFICE_MIN_LANDS,
    BIG_CREATURE_CMC_FLOOR,
    PLANESWALKER_DEFAULT_LOYALTY,
    NONCREATURE_COUNTER_AGGRO_POWER,
    NONCREATURE_COUNTER_AGGRO_HAND,
    PHYREXIAN_LIFE_PENALTY_SCALE,
    OPP_HAND_FULL_HOLDBACK_THRESHOLD,
    HOLDBACK_PROBABILITY_FLOOR,
    PATIENCE_GATE_REJECT_SENTINEL,
    PLAY_VALUE_FLOOR,
    LAND_BASE_EV,
    LAND_UNTAPPED_USEFUL,
    LAND_UNTAPPED_IDLE,
    LAND_TAPPED_STALL,
    LAND_TAPPED_MINOR,
    BOUNCE_LAND_AMULET_MANA,
    RAMP_TO_BIG_NOW,
    RAMP_TO_BIG_SOON,
    LAND_COLOR_ENABLES_SPELL,
    LAND_NEW_COLOR_GENERIC,
    LAND_FETCH_FLEXIBILITY,
    TRON_PIECES_REQUIRED,
    CYCLING_GY_URGENCY_FLOOR,
    DESPERATION_LIFE_FLOOR,
    ATTACK_THRESHOLD_REDUCTION_AGGRESSION,
    ATTACK_THRESHOLD_REDUCTION_ANTI_COMBO,
    COMBAT_TRIGGER_DAMAGE_BONUS,
    COMBAT_ENERGY_TRIGGER_BONUS,
    EMERGENCY_BLOCK_LOW_LIFE,
    EMERGENCY_BLOCK_INCOMING_FLOOR,
    PLATING_REBOUND_EQUIP_BONUS,
    EMERGENCY_BLOCK_STABILIZE_LIFE_GAIN,
    LOW_LIFE_BURN_DEFAULT,
    PUMP_DISCARD_LAND_FLOOR,
    PUMP_DISCARD_SPELL_GLUT,
    ATTACK_TRIGGER_OC_MAX,
)

# RC-2 — parse "equipped/enchanted creature gets +X/+Y" bonuses from
# oracle text. Detects Cranial Plating, Embercleave, Colossus Hammer,
# Ethereal Armor auras, etc., without naming any card.
_EQUIP_BONUS_RE = re.compile(
    r'(equipped|enchanted) creature gets \+(\d+)/\+(\d+)'
)

# Replay-log presentation constants — used only by
# `_emit_decision_event` to format the structured DECISION events for
# the HTML viewer.  These are display/serialization parameters, not
# part of any scoring formula:
# - rules constant: 3 decimals is the precision the HTML EV bars need;
#   beyond that the diff between two close plays is below human noise.
# - rules constant: 4 alternatives is the renderer's display cap; the
#   AI considers the full candidate list, only the top-4 runner-ups
#   show in the UI to keep each decision card scannable.
# - sentinel: 0.0 is the EV displayed for a "pass" decision, used only
#   to compute the gap = chosen_ev - alt_ev.
_REPLAY_EV_PRECISION = 3
_REPLAY_TOP_N_ALTS = 4
_REPLAY_PASS_EV_SENTINEL = 0.0

# ─────────────────────────────────────────────────────────────
# Archetype detection
# ─────────────────────────────────────────────────────────────

# Archetype detection — single source of truth in strategy_profile.py
def _get_archetype(deck_name: str) -> str:
    from ai.strategy_profile import DECK_ARCHETYPES, ArchetypeStrategy, DECK_ARCHETYPE_OVERRIDES
    # Per-deck overrides (e.g., Ruby Storm → "storm" instead of generic "combo")
    if deck_name in DECK_ARCHETYPE_OVERRIDES:
        return DECK_ARCHETYPE_OVERRIDES[deck_name]
    arch = DECK_ARCHETYPES.get(deck_name)
    return arch.value if arch else "midrange"


# ─────────────────────────────────────────────────────────────
# Turn-planner factory — opt-in ISMCTS via MTGSIM_USE_MCTS
# ─────────────────────────────────────────────────────────────
#
# Phase 4A (docs/research/2026-05_phase_4a_ismcts_scoping.md) ships
# the ISMCTS planner as an OPT-IN replacement for the heuristic
# TurnPlanner. The default matrix-sim path is unchanged: when the
# environment variable ``MTGSIM_USE_MCTS`` is unset (or empty / "0"
# / "false"), this factory returns a vanilla ``TurnPlanner``. When
# the flag is truthy, it returns an ``ISMCTSPlanner`` configured
# with the heuristic planner as fallback so any method the rest of
# the AI stack reaches for (``evaluate_response``, etc.) keeps
# working via delegation.
#
# Truthy values: any non-empty string except a small set of
# well-known "off" tokens. Off tokens are deliberately permissive
# so the flag composes cleanly with shells / CI runners that pass
# ``MTGSIM_USE_MCTS=0`` or ``MTGSIM_USE_MCTS=false`` to disable.
_MCTS_FLAG_ENV = "MTGSIM_USE_MCTS"
_MCTS_OFF_TOKENS = {"", "0", "false", "no", "off"}


def _mcts_flag_enabled() -> bool:
    """Return True iff ``MTGSIM_USE_MCTS`` is set to a truthy value."""
    raw = os.environ.get(_MCTS_FLAG_ENV, "")
    return raw.strip().lower() not in _MCTS_OFF_TOKENS


def _build_turn_planner():
    """Construct the planner used by the response decider.

    Default: heuristic ``TurnPlanner``. When the opt-in flag is set,
    construct an ``ISMCTSPlanner`` with the heuristic as fallback.
    See module-level comment for flag semantics.
    """
    from ai.turn_planner import TurnPlanner
    if not _mcts_flag_enabled():
        return TurnPlanner()
    # Opt-in path: MCTS planner with heuristic safety net.
    from ai.search.ismcts import ISMCTSPlanner
    return ISMCTSPlanner(fallback=TurnPlanner())


# ─────────────────────────────────────────────────────────────
# Play representation
# ─────────────────────────────────────────────────────────────

class Play:
    """A candidate play with its EV score and lookahead reasoning."""
    __slots__ = ('action', 'card', 'targets', 'ev', 'reason', 'ability_index',
                 'heuristic_ev', 'lookahead_ev', 'counter_pct', 'removal_pct', 'target_reason',
                 'no_signal')

    def __init__(self, action: str, card, targets: list, ev: float, reason: str, target_reason: str = ''):
        self.action = action  # "play_land", "cast_spell", "cycle"
        self.ability_index = None  # set for 'activate' plays only
        self.card = card
        self.targets = targets
        self.ev = ev
        self.reason = reason
        self.target_reason = target_reason
        self.heuristic_ev = ev      # original heuristic score (before blend)
        self.lookahead_ev = 0.0     # raw lookahead delta
        self.counter_pct = 0.0      # opponent counter probability
        self.no_signal = False      # deferral flag: no this-turn signal fired
        self.removal_pct = 0.0      # opponent removal probability


# ─────────────────────────────────────────────────────────────
# EVPlayer — the complete AI player
# ─────────────────────────────────────────────────────────────

class EVPlayer:
    """EV-based AI player. All decisions are EV comparisons.

    Scoring driven by StrategyProfile (ai/strategy_profile.py).
    Card effects resolved from oracle text (engine/oracle_resolver.py).
    """

    def __init__(self, player_idx: int, deck_name: str,
                 rng: random.Random = None):
        self.player_idx = player_idx
        self.deck_name = deck_name
        self.archetype = _get_archetype(deck_name)
        self.rng = rng or random.Random()
        self._pw_activated_this_turn: Set[int] = set()
        self._last_target_reason: str = ""
        self.strategic_logger = None
        # Optional structured replay log (engine/replay_log.py).
        # When set, decide_main_phase emits a DECISION event with the
        # full sorted candidate list (chosen + runner-ups + EV gap) so
        # the HTML replayer can show why other plays lost.  None ==
        # no overhead beyond a sentinel check.
        self.replay_log = None

        # Strategy profile — data-driven weights for this archetype
        from ai.strategy_profile import get_profile
        self.profile = get_profile(self.archetype)

        # DeckKnowledge — initialized on first decision when we see the library
        self._dk: Optional[DeckKnowledge] = None
        self._dk_initialized = False

        # Keep the gameplan for compatibility (mulligan CMC overrides, etc.)
        from ai.gameplan import create_goal_engine
        self.goal_engine = create_goal_engine(deck_name)

        # Combat planner — reuse existing
        from ai.turn_planner import CombatPlanner
        self.combat_planner = CombatPlanner()

        # Phase 2c.3 cache: `assess_combo` is O(chains) expensive
        # (worst case ~10K simulations per call) and `_score_spell`
        # invokes it for every legal play.  All spells scored within
        # one `decide_main_phase` call share the same EVSnapshot, so
        # identity-based caching is sufficient and correct: the snap
        # changes when the game state changes, and a new snap means
        # a new id().
        self._assess_snap_id: int = 0
        self._assess_value = None

        # Mulligan decider — reuse existing.
        #
        # Phase 2 sweep: the prior `_COMBO_ALIASES = {"storm"}` /
        # `archetype in [e.value for e in ArchetypeStrategy]` membership
        # checks are gone.  Storm and any other "extension" archetype
        # are now classified by per-deck `mulligan_policy` data carried
        # on the gameplan (see `ai.gameplan.MulliganPolicy`).  The
        # `MulliganDecider` reads that policy directly; the
        # `ArchetypeStrategy` enum we still pass here is purely for
        # legacy field initialization on the decider — it does NOT
        # gate behaviour anymore.  The mapping is a static dict, not
        # an `in (...)` conditional.
        from ai.mulligan import MulliganDecider
        from ai.strategy_profile import ArchetypeStrategy
        _ARCHETYPE_BY_NAME = {e.value: e for e in ArchetypeStrategy}
        # Storm is a combo extension archetype that lacks its own
        # enum value; map it onto COMBO for the legacy enum-typed
        # field.  All real behaviour comes from the gameplan policy.
        _ARCHETYPE_BY_NAME.setdefault("storm", ArchetypeStrategy.COMBO)
        arch_enum = _ARCHETYPE_BY_NAME.get(
            self.archetype, ArchetypeStrategy.MIDRANGE)
        self._mulligan_decider = MulliganDecider(arch_enum, self.goal_engine)

        # Response decider — reuse existing.
        # Planner construction goes through ``_build_turn_planner``
        # so the opt-in ``MTGSIM_USE_MCTS`` flag (Phase 4A) can swap
        # in the ISMCTS planner without changing default behavior.
        from ai.response import ResponseDecider
        self.turn_planner = _build_turn_planner()
        self._response_decider = ResponseDecider(
            player_idx, self.turn_planner, self.strategic_logger)

        # Bayesian Hand Inference — track opponent hand probabilities
        from ai.bhi import BayesianHandTracker
        self.bhi = BayesianHandTracker(player_idx)

        # Storm patience: track whether we've decided to "go off" this turn
        self._going_off_turn: int = -1  # turn number when we decided to go off

        # Card role cache from gameplan (for combo sequencing)
        self._payoff_names: Set[str] = set()
        self._engine_names: Set[str] = set()
        self._fuel_names: Set[str] = set()
        self._interaction_names: Set[str] = set()
        self._reactive_only: Set[str] = set()
        # Per-turn cache for _held_tax_counter_liveness — the scan walks
        # the opponent's whole pool, and holdback runs per candidate.
        self._tax_liveness_cache: dict = {}
        if self.goal_engine:
            gp = self.goal_engine.gameplan
            self._reactive_only = gp.reactive_only
            for goal in gp.goals:
                self._payoff_names.update(goal.card_roles.get('payoffs', set()))
                self._payoff_names.update(goal.card_roles.get('finishers', set()))
                self._engine_names.update(goal.card_roles.get('engines', set()))
                self._fuel_names.update(goal.card_roles.get('fuel', set()))
                self._interaction_names.update(goal.card_roles.get('interaction', set()))

    def _init_deck_knowledge(self, game: "GameState"):
        """Initialize DeckKnowledge from the current game state."""
        if self._dk_initialized:
            return
        me = game.players[self.player_idx]
        decklist = {}
        for zone in [me.hand, me.library, me.graveyard, me.exile, me.battlefield]:
            for card in zone:
                decklist[card.name] = decklist.get(card.name, 0) + 1
        land_names = set()
        for zone in [me.library, me.battlefield, me.hand]:
            for card in zone:
                if card.template.is_land:
                    land_names.add(card.name)
        self._dk = DeckKnowledge.from_game_state(me, decklist)
        self._dk._land_names = land_names
        self._dk_initialized = True

    # ═══════════════════════════════════════════════════════════
    # MULLIGAN
    # ═══════════════════════════════════════════════════════════

    def decide_mulligan(self, hand: List["CardInstance"],
                        cards_in_hand: int) -> bool:
        """Keep or mulligan. Delegates to MulliganDecider.

        Land-floor invariant — the s60200 bug fix
        ----------------------------------------
        The bottoming policy in ``MulliganDecider.choose_cards_to_bottom``
        guarantees ``min(min_lands, lands_in_hand)`` lands in the kept
        hand.  But that floor is unsatisfiable when ``lands_in_hand == 0``:
        no amount of bottoming can conjure a land.  The mulligan
        decision is therefore the only place where a 0-land hand can
        be rejected before the engine commits it.

        The previous ordering short-circuited via ``mulligan_always_keep``
        (defaults to 5) BEFORE the 0-land hard floor, forcing the AI to
        keep guaranteed-loss hands at the down-to-5 step
        (replays/affinity_vs_boros_energy_s60200.txt: Boros kept
        0 lands + 7 spells, lost on T3).  Reordering — hard-floor first,
        always-keep second — preserves the same "don't mulligan further
        below 5" intent for keepable hands while honoring the land
        invariant the trigger declares.
        """
        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]

        # 0-land hard floor takes precedence over hand-size leniency.
        # Delegated to MulliganDecider so the rule lives in one place
        # (and so deck-class exceptions like Affinity's mox-artifact
        # mana base apply uniformly).
        if len(lands) == 0:
            keep = self._mulligan_decider.decide(hand, cards_in_hand)
            self.mulligan_reason = getattr(self._mulligan_decider, 'last_reason', '')
            if not keep:
                return False
            # MulliganDecider allowed it (Affinity-style mox artifact
            # exception) — fall through to normal evaluation below.
        if cards_in_hand <= self.profile.mulligan_always_keep:
            # Goal-conjunction reachability pierces the always-keep
            # floor by exactly ONE hand size (2026-08-27 reanimator
            # lever 2): at the floor itself, a hand that covers no
            # declared role path fully AND cannot dig toward the
            # missing role is a guaranteed non-assembly — one more
            # mulligan buys a real chance at a plan piece.  Below the
            # floor the veto never fires, so there is no
            # mull-to-oblivion (mirrors the 0-land hard floor's
            # ordering above; rule pinned by
            # tests/test_mulligan_scores_goal_conjunction.py).
            if (cards_in_hand == self.profile.mulligan_always_keep
                    and self._mulligan_decider.conjunction_unreachable(hand)):
                self.mulligan_reason = (
                    "goal conjunction unreachable — no role-path "
                    "coverage and no castable dig card"
                )
                return False
            self.mulligan_reason = f"only {cards_in_hand} cards — always keep"
            return True
        if len(lands) >= self.profile.mulligan_bad_land_count:
            self.mulligan_reason = f"{len(lands)} lands (≥ {self.profile.mulligan_bad_land_count})"
            return False
        result = self._mulligan_decider.decide(hand, cards_in_hand)
        self.mulligan_reason = getattr(self._mulligan_decider, 'last_reason', '')
        return result

    def choose_cards_to_bottom(self, hand: List["CardInstance"],
                                count: int) -> List["CardInstance"]:
        return self._mulligan_decider.choose_cards_to_bottom(hand, count)

    # ═══════════════════════════════════════════════════════════
    # MAIN PHASE — the core EV decision
    # ═══════════════════════════════════════════════════════════

    def decide_main_phase(self, game: "GameState",
                          excluded_cards: set = None,
                          excluded_activations: set = None
                          ) -> Optional[Tuple[str, "CardInstance", List[int]]]:
        """Score every legal play, pick the best one.

        Returns: ("play_land", card, []) or ("cast_spell", card, targets) or None
        """
        # Invalidate last-call's candidate snapshot up-front so every return
        # path (including early-returns when `legal` is empty) leaves
        # `_last_candidates` consistent with the *current* decision — not the
        # previous one. Prior bug: re-entry with nothing castable returned
        # without clearing, so trace/debug consumers read stale candidates
        # (e.g. `cast_spell: Ajani` after Ajani had already resolved).
        self._last_candidates = []
        # Reset UNCONDITIONALLY: decide_main_phase has several early-return
        # paths, and a stale index would make the engine exclude the wrong
        # (instance_id, ability_index) pair — the case that spins a phase.
        self._last_activation_ability_index = None

        self._init_deck_knowledge(game)
        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]

        # Consume any post-combo goal-advance signal set by mass-reanimate
        # resolution (e.g. Living End). Engine sets game._pending_goal_advance
        # when a board-resetting cascade lands; AI advances to PUSH_DAMAGE
        # so it stops casting curve spells and starts swinging.
        pending = getattr(game, '_pending_goal_advance', None)
        if pending and self.player_idx in pending:
            if self.goal_engine:
                self.goal_engine.advance_goal(game,
                                              reason='post_combo_aggression')
            del pending[self.player_idx]

        # Check if current goal should advance before evaluating plays
        if self.goal_engine:
            self.goal_engine.check_transition(game, self.player_idx)

        snap = snapshot_from_game(game, self.player_idx)

        # ── ACTIVATION region — activated win-condition lines ──
        # Battlefield permanents' activated abilities that represent
        # win conditions (creature-land animation) are Play candidates
        # like any cast: enumerated here so they compete on EV, and
        # scored entirely from clock primitives in ai/activation_ev.py.
        # Enumerated BEFORE the legal-plays early return: an empty hand
        # must not silence an activatable win condition (the Azorius P0
        # — the AI sat on animate lands while decking).  Pre-combat
        # only: the animation exists to attack this turn.
        activation_plays: List[Play] = []
        from engine.game_state import Phase as _Phase
        if game.current_phase == _Phase.MAIN1:
            from ai.activation_ev import land_animation_candidates
            for perm, act_ev, act_reason in land_animation_candidates(
                    game, self.player_idx, snap):
                activation_plays.append(
                    Play("activate_ability", perm, [], act_ev, act_reason))

        # Generic activated abilities. Enumerated OUTSIDE the MAIN1 gate
        # above — that gate belongs to land animation, which exists to attack
        # this turn. The per-effect timing restriction (pump is MAIN1-only)
        # lives in `activation_candidates`, where it can be justified per
        # effect kind rather than applied wholesale.
        from ai.activation_ev import activation_candidates
        for _perm, _ab_idx, _tgts, _ev, _reason in activation_candidates(
                game, self.player_idx, snap, excluded=excluded_activations):
            # Holdback is the ONLY real mana-cost signal in this score:
            # position_value's mana term is clamped by max(0, mana_diff), so
            # spending mana contributes exactly 0.0 to the projection.
            #
            # SIGN: `_holdback_penalty` returns a signed ADJUSTMENT and every
            # cast/cycling/equip call site adds it — negative when open mana
            # has a defensive use, positive when holding mana serves nothing.
            # An earlier revision SUBTRACTED it here, which inverted both
            # branches: on a flooded board with an empty hand the +3.6
            # "spend it" bonus became a -3.6 penalty and annihilated every
            # candidate (a horizon-land cash-in scoring +0.075 raw never
            # survived). Zero activations across four seeded games was the
            # observable symptom.
            _ev += self._holdback_penalty(
                me, opp, snap, _perm.template.activated_abilities[_ab_idx]
                .cost.mana.cmc, game=game)
            if _ev <= 0.0:
                continue
            _play = Play("activate", _perm, list(_tgts), _ev, _reason)
            _play.ability_index = _ab_idx
            activation_plays.append(_play)

        legal = game.get_legal_plays(self.player_idx)
        if not legal and not activation_plays:
            return None
        if excluded_cards:
            legal = [c for c in legal if c.instance_id not in excluded_cards]
            if not legal and not activation_plays:
                return None

        lands = [c for c in legal if c.template.is_land]

        # Identify suspend cards (sorcery-speed special action, distinct
        # from casting). Suspend-only cards (CMC 0, suspend keyword) are
        # not hand-castable — they reach legal_plays only through the
        # suspend branch in engine/game_state.py::get_legal_plays.
        # Exclude them from `spells` so they aren't also scored as cast
        # candidates against a non-existent cast path.
        suspend_cards = [c for c in me.hand
                         if game.can_suspend(self.player_idx, c)]
        suspend_only = [c for c in suspend_cards
                        if not game.can_cast(self.player_idx, c)]

        spells = [c for c in legal
                  if not c.template.is_land
                  and c not in suspend_only]

        # Identify cycling cards (special action, not casting)
        cycling_cards = [c for c in me.hand if game.can_cycle(self.player_idx, c)]

        # Filter legends we already control
        spells = self._filter_legend_rule(me, spells)

        candidates: List[Play] = []

        # Score cycling plays (Living End style — cycle creatures to GY, then cascade)
        for card in cycling_cards:
            ev = self._score_cycling(card, snap, game, me, opp)
            candidates.append(Play("cycle", card, [], ev,
                                   f"Cycle: {card.name}"))

        # Score suspend plays (Living End / Ancestral Vision / etc.).
        # Suspend is a sorcery-speed special action. EV is gated by
        # _payoff_reachable_this_turn (no faster route in hand) and by
        # the opponent's clock (resolution must arrive before lethal).
        for card in suspend_cards:
            ev = self._score_suspend(card, snap, game, me, opp)
            candidates.append(Play("suspend", card, [], ev,
                                   f"Suspend: {card.name}"))

        # Plot (CR 702.170) — a sorcery-speed special action generalizing the
        # warp/suspend deferred-cast family. Typed-field driven
        # (template.plot_cost), no card names. Two plays:
        #   * cast_plotted: a card plotted on an EARLIER turn is cast for FREE
        #     now — pure upside, scored as the card's normal cast EV.
        #   * plot: pay the (usually cheaper) plot cost and bank the card for a
        #     free cast later. Enumerated ONLY for cards NOT castable at full
        #     cost this turn, so plotting never displaces simply casting now —
        #     it converts a card you could not deploy into a free future play.
        # This enumeration runs in the sorcery-speed main-phase context (same
        # as suspend/cycling above), so no separate phase gate is needed.
        for card in list(me.exile):
            if game.can_cast_plotted(self.player_idx, card):
                ev = self._score_spell(card, snap, game, me, opp)
                candidates.append(Play("cast_plotted", card, [], ev,
                                       f"Cast plotted: {card.name} (free)"))
        for card in me.hand:
            if (getattr(card.template, 'plot_cost', None) is not None
                    and game.can_plot(self.player_idx, card)
                    and not game.can_cast(self.player_idx, card)):
                ev = self._score_spell(card, snap, game, me, opp)
                candidates.append(Play("plot", card, [], ev,
                                       f"Plot: {card.name}"))

        # Score land plays — lands compete with spells for priority
        if lands and me.lands_played_this_turn < (1 + me.extra_land_drops):
            # A fetchland whose printed activation cost includes a life
            # payment is unplayable at or below that life total — the
            # crack would kill us.  Both the cost and its absence are on
            # the card (`template.fetchland.life_cost`), so no name table
            # and no life-free-fetch exception list.
            safe_lands = [
                l for l in lands
                if l.template.fetchland is None
                or l.template.fetchland.life_cost == 0
                or me.life > l.template.fetchland.life_cost
            ]
            for land in safe_lands:
                ev = self._score_land(land, me, spells, game)
                candidates.append(Play("play_land", land, [], ev,
                                       f"Land: {land.name} (EV={ev:.1f})"))

        # Score spell plays
        #
        # COMBO KILL OVERRIDE: if chain evaluator sees a lethal line,
        # force-advance goal to EXECUTE_PAYOFF so ritual/draw cards
        # get scored as chain starters instead of generic spells.
        # The profile's ``has_combo_chain`` flag captures both COMBO
        # and STORM archetypes (see strategy_profile.py); the storm
        # _estimate_combo_chain math (ritual-first simulation) applies
        # uniformly to both.
        if self.goal_engine and self.profile.has_combo_chain:
            from ai.ev_evaluator import _estimate_combo_chain
            can_kill, storm_count, damage, chain = _estimate_combo_chain(
                game, self.player_idx)
            # Phase 1 refactor: storm-threshold weight sourced from the
            # LLM helper, cached per archetype.  Historical value 5.0
            # (the default-table fallback) preserves prior behaviour
            # offline; cache-warmed weights tune per-archetype.
            _storm_threshold = _llm_weight(
                self.archetype,
                CTX_COMBO_FORCE_PAYOFF_STORM_THRESHOLD,
            )
            if can_kill or storm_count >= _storm_threshold:
                # Force goal to last phase (EXECUTE_PAYOFF / CLOSE_GAME)
                while self.goal_engine.current_goal_idx < len(self.goal_engine.gameplan.goals) - 1:
                    self.goal_engine.advance_goal(game, f"Combo kill detected (storm={storm_count})")

        # REANIMATE PRIORITY OVERRIDE: if hand has reanimate spell AND
        # graveyard has a creature with power >= 5, force-cast it immediately
        # — UNLESS the deck's EXECUTE_PAYOFF goal declares pacing/mana gates
        # (min_turns / min_mana_for_payoff) that say "not ready yet". GV-4:
        # Goryo's at 24.9% flat fires T3 when mana-light; gates let the
        # gameplan defer the override until it's actually safe.
        reanimate_override = None
        from engine.cards import CardType
        gy_big = [c for c in me.graveyard
                  if CardType.CREATURE in c.template.card_types
                  and (c.template.power or 0) >= REANIMATE_TARGET_MIN_POWER]
        payoff_gates_ready = True
        if self.goal_engine:
            from ai.gameplan import GoalType, is_ready_for_payoff
            cur_goal = self.goal_engine.current_goal
            if cur_goal.goal_type == GoalType.EXECUTE_PAYOFF:
                payoff_gates_ready = is_ready_for_payoff(
                    cur_goal,
                    turns_in_goal=self.goal_engine.turns_in_goal,
                    mana_available=me.available_mana_estimate,
                )
        if gy_big and payoff_gates_ready:
            for spell in spells:
                if 'reanimate' in getattr(spell.template, 'tags', set()) and game.can_cast(self.player_idx, spell):
                    reanimate_override = spell
                    break

        for spell in spells:
            if not game.can_cast(self.player_idx, spell):
                continue

            # Skip PURE counterspells in main phase (nothing to target).
            # Multi-mode cards (Drown in the Loch, Archmage's Charm) that can
            # counter OR do something else should be allowed through.
            tags = getattr(spell.template, 'tags', set())
            oracle = (spell.template.oracle_text or '').lower()
            is_pure_counter = ('counterspell' in tags and 'removal' not in tags
                               and 'draw' not in oracle)
            if is_pure_counter:
                continue

            # Skip reactive-only NON-CREATURE spells unless:
            # - We're dying (survival override)
            # - It's removal with a high-threat target (oracle-driven, not raw power)
            if spell.name in self._reactive_only:
                if not spell.template.is_creature:
                    prof = self.profile
                    # "Am I dying?" is a LIFE-RELATIVE question, and
                    # `opp_clock_discrete` (= ceil(my_life / opp_power)) is
                    # exactly that quantity — it already returns a no-clock
                    # sentinel when the opponent has no power, so a short clock
                    # cannot fire on an empty board.
                    #
                    # The old form also required `opp_power >=
                    # prof.dying_opp_power`, an ATTACKER-SIZE floor. That made
                    # death-by-a-thousand-cuts invisible: a board of small
                    # creatures never met the floor no matter how short the
                    # clock, so a control deck bled from 20 to 0 holding its
                    # removal and deployed it only once one attacker happened
                    # to grow past the floor — several turns and ~10 life too
                    # late (post-sweep control-execution audit, seed 50000).
                    # Size is redundant once the clock is life-relative; the
                    # floor is dropped rather than patched around.
                    is_dying = (snap.am_dead_next
                                or snap.opp_clock_discrete <= prof.dying_opp_clock)
                    has_big_target = self._has_high_threat_target(game, spell, snap)
                    # A blink held "for protection" must be castable
                    # PROACTIVELY when it would clear a live pending
                    # end-of-turn-exile rider on an own permanent
                    # (Goryo's-style detriment): the rider fires at OUR
                    # end step, so cast-later loses the body outright
                    # (CR 400.7 new-object rule; engine side in PR #462).
                    # RC-1 decision layer, docs/diagnostics/
                    # 2026-07-05_goryos_field_13pct_root_cause.md.
                    clears_detriment = (
                        'blink' in tags
                        and bool(self._pending_eot_exile_riders(game)))
                    # has_big_target overrides control_patience: if a real
                    # threat is on board (oracle-driven threat floor), the
                    # reactive-only spell should fire proactively even for
                    # control decks that otherwise hold until late. Audit
                    # finding: Azorius Prismatic Ending sat in hand until
                    # Cranial Plating had already locked the game.
                    if is_dying or has_big_target or clears_detriment:
                        pass  # allow through reactive-only gate
                    elif (prof.control_patience
                          and snap.opp_clock_discrete >= CONTROL_PATIENCE_OPP_CLOCK_THRESHOLD):
                        continue  # control: no pressure, no threat — hold
                    else:
                        continue  # non-control: no threat, not dying — hold

            ev = self._score_spell(spell, snap, game, me, opp)
            targets = self._choose_targets(game, spell)

            # Reanimate override: massive boost when big creature is in GY
            if reanimate_override and spell.instance_id == reanimate_override.instance_id:
                ev += REANIMATE_OVERRIDE_BONUS  # force-cast reanimation when target ready

            # Spells that need targets but have none = skip
            if self._spell_requires_targets(spell) and not targets:
                continue

            _tgt_reason = getattr(self, "_last_target_reason", "")
            self._last_target_reason = ""
            candidates.append(Play("cast_spell", spell, targets, ev,
                                   f"{spell.name} (EV={ev:.1f})",
                                   target_reason=_tgt_reason))

        # Consider equipping unattached equipment
        equip_play = self._consider_equip(game, me)
        if equip_play:
            candidates.append(equip_play)

        # Activated win-condition lines compete with casts/lands on EV
        # (enumerated above, before the legal-plays early return).
        candidates.extend(activation_plays)

        if not candidates:
            self._last_candidates = []
            return None

        # Enrich spell candidates with counter/removal probabilities for trace output
        from ai.ev_evaluator import compute_play_ev
        for play in candidates:
            if play.action == "cast_spell":
                _, info = compute_play_ev(
                    play.card, snap, self.archetype, game, self.player_idx,
                    detailed=True)
                play.heuristic_ev = play.ev
                play.lookahead_ev = play.ev
                play.counter_pct = info['counter_pct']
                play.removal_pct = info['removal_pct']
                play.no_signal = bool(info.get('deferral', False))

        # Sort by EV, pick the best
        candidates.sort(key=lambda p: p.ev, reverse=True)
        self._last_candidates = candidates

        # Pass-preference tiebreaker (design: docs/design/
        # ev_correctness_overhaul.md §3, §4).  A cast with no same-turn
        # signal delivers no value casting-now vs casting-later — the
        # state after cast is reachable next turn at identical cost.
        # Preserve hand optionality: skip no-signal casts regardless of
        # whatever the overlay-adjusted EV happens to be, and fall
        # through to the next-best candidate (which might be a land, an
        # equip activation, or a different cast).  Lands and equip
        # activations are never deferred.
        non_deferred = [
            p for p in candidates
            if not (p.action == "cast_spell" and p.no_signal)
        ]
        if not non_deferred:
            return None
        best = non_deferred[0]

        # M3: replaced the per-archetype `pass_threshold` binary gate
        # with a comparison against the rules-level `PLAY_VALUE_FLOOR`
        # sentinel (-5.0).  `best.ev` already incorporates the signed
        # `holdback_cost` from `_holdback_penalty` — negative penalty
        # when defensive use exists, positive bonus when none — so the
        # gate is the *signed* play-value comparison the M3 brief
        # specifies.  Plays whose signed value lands below the floor
        # are passed; plays above the floor execute.  Same constant
        # is used by `PATIENCE_GATE_REJECT_SENTINEL` (-10.0) which
        # clamps fizzle/cascade-patience spells strictly below the
        # floor.  See M3 brief in
        # `docs/history/audits/2026-05-16_5panel_bo3_audit.md`.
        if best.ev <= PLAY_VALUE_FLOOR:
            self._emit_decision_event(game, candidates, chosen=None,
                                      pass_reason="at or below play-value floor")
            return None

        self._last_played_target_reason = getattr(best, "target_reason", "")
        # Carry the chosen ability index across the AI/engine seam. Set on
        # EVERY path (None when the chosen play is not an activation) so a
        # stale value can never reach the engine's exclusion bookkeeping.
        self._last_activation_ability_index = getattr(
            best, "ability_index", None)
        self._emit_decision_event(game, candidates, chosen=best)
        return (best.action, best.card, best.targets)

    def _emit_decision_event(self, game, candidates, chosen,
                             pass_reason: str = "") -> None:
        """Push a DECISION event onto the structured replay log.

        This is the single connection point between EV scoring and the
        replayer.  It runs only when ``self.replay_log`` is non-None,
        so the production matrix path pays only the cost of a sentinel
        check.

        ``chosen`` is the Play returned to the caller (or None on
        pass).  Up to ``_REPLAY_TOP_N_ALTS`` runner-ups are emitted
        with their EV gap; this is the data the HTML uses to
        highlight close decisions and surface "this was suboptimal vs
        X" feedback.
        """
        log = getattr(self, "replay_log", None)
        if log is None:
            return
        from engine.replay_log import snapshot_state

        # Display-precision and top-N constants — presentation only,
        # not part of any scoring formula.  Kept module-private and
        # named so the magic-numbers ratchet stays at baseline=0.
        _PREC = _REPLAY_EV_PRECISION
        _TOP_N = _REPLAY_TOP_N_ALTS
        _PASS_EV = _REPLAY_PASS_EV_SENTINEL

        def _ser(p):
            card_name = getattr(p.card, "name", None) if p.card else None
            tgt_names = []
            for t in (p.targets or []):
                name = getattr(t, "name", None)
                tgt_names.append(name if name else f"id:{t}")
            return {
                "action": p.action,
                "card": card_name,
                "ev": round(float(p.ev), _PREC),
                "heuristic_ev": round(float(p.heuristic_ev), _PREC),
                "reason": p.reason or "",
                "target_reason": getattr(p, "target_reason", "") or "",
                "targets": tgt_names,
                "counter_pct": round(float(p.counter_pct), _PREC),
                "removal_pct": round(float(p.removal_pct), _PREC),
            }

        chosen_dict = _ser(chosen) if chosen else {
            "action": "pass", "card": None, "ev": _PASS_EV,
            "reason": pass_reason or "no candidate above threshold",
            "targets": [],
        }
        chosen_ev = chosen_dict.get("ev", _PASS_EV)
        alt_dicts = []
        for p in candidates:
            if chosen is not None and p is chosen:
                continue
            d = _ser(p)
            d["gap"] = round(chosen_ev - d["ev"], _PREC)
            alt_dicts.append(d)
            if len(alt_dicts) >= _TOP_N:
                break

        goal = ""
        if self.goal_engine and self.goal_engine.gameplan and \
                self.goal_engine.gameplan.goals:
            ge = self.goal_engine
            idx = ge.current_goal_idx
            if idx < len(ge.gameplan.goals):
                goal = ge.gameplan.goals[idx].goal_type.value

        actor = game.players[self.player_idx].deck_name
        log.emit_decision(
            actor=actor,
            pidx=self.player_idx,
            chosen=chosen_dict,
            alternatives=alt_dicts,
            state=snapshot_state(game),
            goal=goal,
            candidates_n=len(candidates),
        )

    # ═══════════════════════════════════════════════════════════
    # SCORING — per-archetype spell evaluation
    # ═══════════════════════════════════════════════════════════

    def _overlay_land_sacrifice_fizzle(self, ev: float, t, me) -> float:
        """Clamp land-sacrifice tutors (Scapeshift shape) into the patience-
        reject band when the cast is not worth its own mana base. Two gates,
        both driven by the parse-once typed field
        `CardTemplate.is_land_sacrifice_tutor`, no card names:

        1. Fizzle floor (original): fewer than the minimum lands and the
           engine fizzles the cast outright.
        2. Payoff-reachability (2026-08-26 Amulet re-diagnosis, primary
           root cause): the tutor converts an untapped base into fetched
           lands whose bounce ETBs return co-entrants — with no untapped-
           entry watcher the conservative retained yield is ceil(N/2)
           TAPPED lands (see `conservative_land_retention`). Fired without
           a deployable payoff that the post-resolution board can cast,
           the "ramp" spell halves the caster's own mana base (replay-
           verified: 6 of 12 walked losses, including a hand-held payoff
           locked out permanently). So the cast is allowed only when a
           payoff-role card in hand — excluding cards that are themselves
           this tutor shape, since the gameplan lists the tutor among its
           payoffs and self-justification reopens the blind-ramp hole —
           costs no more than the retained-land estimate. Watchers make
           retention = N: the untap-trigger pattern (Amulet of Vigor
           class) and the lands-enter-untapped static (Spelunking class),
           matched by the same oracle predicates the engine's
           LandManager uses.
        """
        if getattr(t, 'is_land_sacrifice_tutor', False):
            my_land_count = sum(1 for c in me.battlefield if c.template.is_land)
            if my_land_count < LAND_SACRIFICE_MIN_LANDS:
                return min(ev, PATIENCE_GATE_REJECT_SENTINEL)

            has_watcher = False
            for w in me.battlefield:
                w_oracle = (w.template.oracle_text or '').lower()
                if (('whenever' in w_oracle and 'enters tapped' in w_oracle
                        and 'untap it' in w_oracle)
                        or ('lands you control enter' in w_oracle
                            and 'untapped' in w_oracle)):
                    has_watcher = True
                    break
            retained = (my_land_count if has_watcher
                        else conservative_land_retention(my_land_count))
            payoff_costs = [
                c.template.cmc or 0
                for c in me.hand
                if c.template.name in self._payoff_names
                and not c.template.is_land
                and not getattr(c.template, 'is_land_sacrifice_tutor',
                                False)
            ]
            if not payoff_costs or min(payoff_costs) > retained:
                return min(ev, PATIENCE_GATE_REJECT_SENTINEL)
        return ev

    def _overlay_cascade_patience(self, ev: float, t, snap: EVSnapshot, me) -> float:
        """Clamp a cascade enabler when the graveyard is too thin AND no
        reanimate payoff remains reachable in the library — the cascade would
        resolve into a vanilla body and a dead reanimation. Gating on payoff-
        reachability (not the raw ev) is required because a cascade enabler
        that is itself a creature (Shardless Agent 2/1) scores a positive body
        EV that masks the dead cascade. When a payoff IS reachable, defer to
        the projection (cascade-payoff must-fire; test_cascade_payoff_must_fire)."""
        if getattr(t, 'is_cascade', False):
            fill_target = self._cascade_graveyard_target()
            if (fill_target > 0
                    and snap.my_gy_creatures < fill_target
                    and not self._library_has_reanimate_payoff(me)):
                return min(ev, PATIENCE_GATE_REJECT_SENTINEL)
        return ev

    def _gate_x_tutor_payoff(self, ev: float, card, t, snap: EVSnapshot,
                             me, game) -> float:
        """Delivery-conditioned EV + payoff patience for X-cost creature
        tutors (Green Sun's Zenith shape, parse-once typed field
        `CardTemplate.x_creature_tutor_data`). Shaped like the sibling
        gates (`_overlay_land_sacrifice_fizzle`, `_gate_x_cost_board_wipe`,
        `_overlay_cascade_patience`): clamp into the patience-reject band
        or adjust EV from resource primitives, no card names.

        Live bug this closes (2026-08-26 Amulet re-diagnosis, 2/12 walked
        losses): the tutor was cast at whatever X current mana allowed and
        scored blind to what it could deliver — X=2/4/4 all fetching the
        same 1-mana body, burning a 4-of payoff-access below the 6-mana
        payoff's cost while that payoff sat reachable in the library.

        Three terms, all consulting the SAME engine-side X picker
        (`pick_creature_tutor_x_value`) the cast path uses:

        1. Delivery conditioning: nothing fetchable at any affordable X
           means the cast is a fizzle — clamp. Otherwise credit the best
           deliverable target's mana value and charge the X-gap
           (`creature_tutor_x_net_value`, mana units), converted at the
           projection's per-mana clock scale — the value credited is what
           the tutor actually delivers, not a flat tutor bonus.
        2. Waste charge is inside that same net value: X above the
           delivered target's cost is mana buying nothing, charged 1:1.
        3. Hold/patience: when the library's payoff ceiling sits above
           the deliverable band, this copy is the last in-hand access
           (no other X-creature-tutor in hand), the body we would settle
           for does not accelerate the mana trajectory (typed ramp
           signals: ETB-land-from-hand with a land actually in hand,
           mana production, extra land drops), the forfeited gap exceeds
           the deliverable's own value (waiting more than doubles what
           the tutor delivers — both sides in mana units), and the
           trajectory (one land drop per turn, a rules constant) reaches
           the payoff within the surviving horizon (`snap.opp_clock`) —
           hold. Early small-X ramp fetches pass untouched via the
           acceleration test; a lethal opposing clock releases the hold
           (better a small body now than a payoff we never live to cast).
        """
        data = getattr(t, 'x_creature_tutor_data', None)
        if not data or not t.x_cost_data or game is None:
            return ev
        from engine.cast_manager import (pick_creature_tutor_x_value,
                                         creature_tutor_x_net_value)
        from ai.clock import mana_clock_impact
        mult = (t.x_cost_data or {}).get('multiplier', 1) or 1
        x_budget = max(0, int(snap.my_mana) - (t.cmc or 0)) // mult
        best_x, target, top = pick_creature_tutor_x_value(
            game, self.player_idx, x_budget, t)
        if target is None:
            # Nothing fetchable at any affordable X — the cast delivers
            # nothing; delivery-conditioned EV is a fizzle.
            return min(ev, PATIENCE_GATE_REJECT_SENTINEL)

        delivered_cmc = target.template.cmc or 0
        per_mana = mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
        # Delivered value in mana units: the body's mana value — or, when
        # the delivered piece completes an unbounded mana engine with the
        # board (engine-side rules query; CR 726.4 shortcut material),
        # the engine's shortcut allowance, which is the mana the piece
        # actually delivers. Same credit the activated-tutor branch of
        # ai/activation_ev.py applies.
        from engine.activation import ActivationManager
        from engine.constants import LOOP_SHORTCUT_MANA
        engine_bonus = 0
        if ActivationManager.would_complete_unbounded_engine(
                game, self.player_idx, target.template):
            engine_bonus = LOOP_SHORTCUT_MANA - delivered_cmc
        ev += ((creature_tutor_x_net_value(best_x, delivered_cmc)
                + engine_bonus) * mult * per_mana)

        top_cmc = top.template.cmc or 0
        forfeited_gap = top_cmc - delivered_cmc
        if forfeited_gap > delivered_cmc:
            # The payoff ceiling is more than double the deliverable body.
            other_access_in_hand = any(
                c is not card
                and getattr(c.template, 'x_creature_tutor_data', None)
                for c in me.hand)
            target_tags = getattr(target.template, 'tags', set()) or set()
            land_in_hand = any(c.template.is_land for c in me.hand)
            accelerates = (
                ('etb_land_from_hand' in target_tags and land_in_hand)
                or bool(getattr(target.template, 'produces_mana', None))
                or getattr(target.template, 'extra_land_drops', 0) > 0)
            if not other_access_in_hand and not accelerates:
                payoff_total_cost = (t.cmc or 0) + top_cmc * mult
                # Mana trajectory: one land drop per turn (rules constant).
                turns_to_afford = max(
                    0, payoff_total_cost - int(snap.my_mana))
                if turns_to_afford <= snap.opp_clock:
                    return min(ev, PATIENCE_GATE_REJECT_SENTINEL)
        return ev

    def _gate_x_cost_board_wipe(self, ev: float, t, tags, snap: EVSnapshot, opp,
                                game=None):
        """Hard gate: hold an X-cost board wipe when its X-budget can't
        meaningfully clear the board. Returns a clamped float to short-circuit
        scoring, or None to continue. Kill count is derived from the oracle's
        destroy/exile clause over all matching nonland permanents (creatures +
        artifacts + enchantments), not creatures alone."""
        opp_nonland = [c for c in opp.battlefield if not c.template.is_land]
        # NOTE: `opp_nonland` is deliberately NOT part of this guard. An empty
        # opposing board is not the "gate does not apply" case — it is the
        # MAXIMALLY wasteful case, and requiring targets here meant the gate
        # short-circuited precisely when the wipe was most wasteful. The
        # kill-count checks below already floor a zero-kill sweep; they simply
        # were never reached. Observed: a control deck cast its sweeper for
        # X=0 into a creatureless board on turn 4, destroying 0 permanents and
        # discarding its best answer against aggro.
        if not ('board_wipe' in tags and t.x_cost_data):
            return None
        from engine.cards import CardType
        total_mana = snap.my_mana
        base_cost = t.cmc or 0
        x_budget = max(0, total_mana - base_cost)
        mult = (t.x_cost_data or {}).get('multiplier', 1) or 1
        cap = x_budget // mult
        o_wipe = (t.oracle_text or '').lower()
        clause_m = re.search(r'\b(?:destroy|exile)\b(.*?)(?:\.|$)', o_wipe, re.S)
        clause = clause_m.group(1) if clause_m else ''
        type_words = {
            'creature': CardType.CREATURE,
            'artifact': CardType.ARTIFACT,
            'enchantment': CardType.ENCHANTMENT,
            'planeswalker': CardType.PLANESWALKER,
        }
        destroyed_types = {ct for word, ct in type_words.items() if word in clause}
        if not destroyed_types:
            destroyed_types = {CardType.CREATURE}
        # Judge the wipe at the X the AI will ACTUALLY pick at resolution
        # (engine.cast_manager.pick_wipe_x_value), not the max-affordable
        # `cap`. The gate and the resolution-time picker must agree:
        # counting kills at `cap` let a single worthless in-budget target
        # (a power-0 high-MV enchantment) pass the gate, after which the
        # picker chose X=0 and the sweeper was thrown away. If the picker's
        # value-maximizing X destroys nothing, hold the wipe regardless of
        # desperation.
        effective_x = cap
        if game is not None:
            try:
                from engine.cast_manager import pick_wipe_x_value
                best_x, _best_score, best_kill_count = pick_wipe_x_value(
                    game, self.player_idx, cap)
                effective_x = best_x
                if best_kill_count == 0:
                    return min(ev, X_BOARD_WIPE_WASTE_FLOOR)
            except Exception:
                effective_x = cap  # picker unavailable — fall back to cap
        killable = [c for c in opp_nonland
                    if (set(c.template.card_types) & destroyed_types)
                    and (c.template.cmc or 0) <= effective_x]
        kill_count = len(killable)
        killable_power = sum((c.power or 0) for c in killable)
        from ai.clock import LifePhase, life_phase
        desperate = life_phase(snap) in (LifePhase.PANIC, LifePhase.LETHAL)
        if not desperate:
            if kill_count == 0:
                return min(ev, X_BOARD_WIPE_WASTE_FLOOR)
            if kill_count == 1 and killable_power < 2:
                return min(ev, X_BOARD_WIPE_WASTE_FLOOR)
            if (kill_count == 1
                    and snap.opp_hand_size
                    >= OPP_HAND_FULL_HOLDBACK_THRESHOLD):
                # A one-kill sweep is spot removal. A wipe's value curve
                # rises with the opponent's board; while they still hold
                # a development-threshold grip (same signal the mana-
                # holdback gate uses), the bigger sweep is still coming
                # — spending the sweeper now forfeits it (2026-07-06
                # azorius aggro-defense diagnostic).
                return min(ev, X_BOARD_WIPE_WASTE_FLOOR)
        elif kill_count == 0:
            return min(ev, X_BOARD_WIPE_WASTE_FLOOR)
        return None

    def _score_spell(self, card: "CardInstance", snap: EVSnapshot,
                     game: "GameState", me, opp) -> float:
        """Score a spell using clock-based projection.

        Base score = position_value(after_cast_and_response) - position_value(now)
        This replaces ~300 lines of additive bonuses with game-mechanics math.

        Overlays for logic the projection can't capture:
        - Evoke: 2-card cost not modeled by projection
        - Combo sequencing: within-turn ordering (storm patience, PiF, finishers)
        - Mana holdback: opportunity cost of tapping out
        """
        from ai.ev_evaluator import compute_play_ev
        t = card.template
        tags = getattr(t, 'tags', set())
        p = self.profile

        # ── Phase 2 dispatcher — combo categories ──
        # Builds a 5-outcome distribution for ritual / cascade /
        # reanimate / finisher / combo-tutor spells and returns its
        # expected-value (Δ(P_win) units).  Flag is OFF in Phase 2a so
        # this branch is dead at runtime; flipping the flag in Phase 2b
        # is a one-line change and exercised by the dispatcher tests.
        from ai.outcome_ev import OUTCOME_DIST_COMBO, build_combo_distribution
        if OUTCOME_DIST_COMBO:
            dist = build_combo_distribution(card, snap, game, me, opp,
                                            self.bhi, self.archetype, p)
            if dist is not None:
                return dist.expected_value()

        # ── Base: projection-based EV ──
        # Projects board after cast + opponent response, returns clock delta
        # Pass BHI for Bayesian-updated opponent response probabilities.
        # Goal + gameplan-role context feed the M4 gear-shift: the
        # current goal (GoalType.value) selects the goal_weights row
        # (close_game re-weights finishers up / cantrips down), and
        # cards declared in the gameplan's finishers/payoffs role
        # buckets carry the synthetic ROLE_FINISHER_TAG.  Knowledge
        # location: roles come from decks/gameplans/*.json via
        # self._payoff_names — no card names in code.
        from ai.strategy_profile import ROLE_FINISHER_TAG
        goal_value = None
        if self.goal_engine is not None:
            goal_value = self.goal_engine.current_goal.goal_type.value
        role_tags = (frozenset({ROLE_FINISHER_TAG})
                     if card.name in self._payoff_names else frozenset())
        ev = compute_play_ev(card, snap, self.archetype, game, self.player_idx,
                             bhi=self.bhi, goal=goal_value,
                             role_tags=role_tags)

        # ── Free cast bonus (generic) ──
        # Any spell offered for 0 effective mana (Ragavan exile, cascade,
        # suspend, Wish-style effects) represents pure card advantage.
        # Tag: _free_cast_opportunity set by whatever granted the cast.
        # Rule: ev >= 0 always (never skip a free spell that doesn't hurt).
        #       +1.5 bonus on top of projection to reflect tempo gain.
        if getattr(card, "_free_cast_opportunity", False):
            ev = max(ev, 0.0)  # floor: never negative
            ev += FREE_CAST_TEMPO_BONUS  # tempo: got it for free

        # ── Land-denial overlay (LD mechanic class) ──
        # Board projection sees no delta from destroying a land (lands
        # carry no power/toughness), so the spell's entire value lives
        # in the opponent's mana development — tempo term + scarcity
        # premium derived from mana_clock_impact / card_clock_impact
        # in ai/land_denial.py.  Typed-field gate (parse-once); covers
        # every classified destroy-target-land spell.
        if getattr(t, 'destroys_target_land', False):
            from ai.land_denial import land_denial_value
            ev += land_denial_value(t, game, self.player_idx, snap)

        # ── Evoke overlay: projection doesn't model 2-card cost ──
        if ('evoke' in tags or 'evoke_pitch' in tags) and snap.my_mana < (t.cmc or 0):
            # Evoking costs an extra card — subtract its future clock value
            from ai.clock import card_clock_impact
            ev -= card_clock_impact(snap) * EVOKE_CARD_LOSS_MULTIPLIER  # losing a card is significant
            # But if we're dying, evoking removal is still worth it
            if snap.am_dead_next:
                ev += EVOKE_DESPERATE_BONUS
            elif snap.opp_creature_count == 0 and 'removal' in tags:
                ev -= EVOKE_NO_TARGET_PENALTY  # never evoke removal with no targets

        # Oracle text lower-cased once for all downstream checks.
        t_oracle = (t.oracle_text or '').lower()

        # ── Combo sequencing overlay ──
        # Phase D third attempt (simulator v2 with hold_value) also
        # collapsed Storm field to 0% — see docs/PHASE_D_DEFERRED.md.
        # Root cause: the simulator's `expected_damage = 0` when no
        # closer is in hand, even though Storm has positive-value
        # build-toward-closer plays via Wish/tutors.  card_combo_modifier
        # had this nuance (tutor-as-finisher-access branch); the
        # simulator-driven evaluator doesn't, and the marginal/flat/
        # hold-value approaches all collapse Storm to ≤ 5%.
        #
        # Live decisions remain on card_combo_modifier until a
        # simulator v3 ships that models "intermediate value" of
        # casting fuel BEFORE the closer is reached (requires library
        # composition / draw-probability modelling — beyond v2).
        if self.profile.has_combo_chain and self.goal_engine is not None:
            from ai.combo_calc import assess_combo, card_combo_modifier
            snap_id = id(snap)
            if snap_id != self._assess_snap_id:
                self._assess_snap_id = snap_id
                self._assess_value = assess_combo(
                    game, self.player_idx, self.goal_engine, snap)
            ev += card_combo_modifier(card, self._assess_value, snap, me, game,
                                       self.player_idx)

        # Land-sacrifice tutor (Scapeshift shape) fizzle gate.
        ev = self._overlay_land_sacrifice_fizzle(ev, t, me)

        # Cascade patience gate (LE-A3).
        ev = self._overlay_cascade_patience(ev, t, snap, me)

        # X-cost creature tutor (GSZ shape): delivery-conditioned EV,
        # X-gap waste charge, and payoff hold (2026-08-26 re-diagnosis).
        ev = self._gate_x_tutor_payoff(ev, card, t, snap, me, game)

        # ── Reanimation readiness gate (GV-2) ──
        # Mirror shape of the cascade patience gate above, but in the
        # OPPOSITE direction: cascade is clamped when the GY is thin
        # (cascade hits into an empty board); reanimation is BOOSTED
        # when the GY has a target (the whole point of reanimation is
        # to set up a big body we couldn't hardcast, so once the
        # set-up is complete we should be eager to fire).
        #
        # Gate fires when ALL of:
        #   1. Spell is a reanimation — either tagged `reanimate` (see
        #      engine/card_database.py:655 for tag assignment) OR the
        #      oracle contains the canonical reanimate phrasing "return
        #      target creature card from your graveyard to the
        #      battlefield". Oracle-driven fallback catches cards the
        #      tagger may miss.
        #   2. The deck is a graveyard-reanimator shell — its gameplan
        #      declares a FILL_RESOURCE goal with
        #      `resource_zone == "graveyard"`. Reuses the helper that
        #      powers the cascade gate. Non-reanimator decks return 0
        #      and the gate does not fire.
        #   3. Graveyard creature count >= the gameplan's declared
        #      `resource_target`. Same threshold the FILL_RESOURCE goal
        #      uses to transition into EXECUTE_PAYOFF — gameplan-driven.
        #
        # When all three hold, boost EV by `snap.opp_life / 2.0`. The
        # magnitude scales with how much damage the reanimated body
        # still has to deal: at 20 life the boost is +10 (a decisive
        # shove past pass_threshold even if the projection discount
        # ate most of the base EV); at 5 life it drops to +2.5
        # (reanimation already wins soon anyway so the nudge is
        # smaller). No magic number — derived from the snapshot.
        is_reanimate_tagged = 'reanimate' in tags
        is_reanimate_oracle = (
            'return target creature card from your graveyard to the battlefield'
            in t_oracle
        )
        if is_reanimate_tagged or is_reanimate_oracle:
            fill_target = self._cascade_graveyard_target()
            if fill_target > 0 and snap.my_gy_creatures >= fill_target:
                # Boost: opp life still to burn through, halved to
                # reflect that reanimation covers ~half the damage
                # gap in expectation (the rest comes from follow-up
                # turns / burn / bonus triggers). Stays well below
                # the +40 hard override in decide_main_phase — this
                # is a soft nudge, not a force-cast.
                ev += snap.opp_life / 2.0

        # ── S-2: EXECUTE_PAYOFF finisher mana-sequencing gate ──
        # Observed in Storm vs Affinity T3 (game 1): Storm in
        # EXECUTE_PAYOFF holds Grapeshot in hand AND has enough mana
        # to fire it RIGHT NOW. The AI nonetheless prefers a non-
        # finisher cantrip (March of Reckless Joy) because the
        # gameplan card-priority weights are similar across
        # cantrips and finishers. Casting the cantrip drops
        # available mana below the finisher cost, the finisher
        # never fires, and Storm wastes its storm-count window.
        #
        # Gate: when ALL of
        #   1. The current goal is EXECUTE_PAYOFF (the deck has
        #      decided it's combo-time — gameplan-driven signal).
        #   2. The player holds at least one finisher in hand —
        #      detected by the STORM keyword (Kw.STORM is the
        #      oracle-parsed marker on Grapeshot, Empty the
        #      Warrens, Tendrils, etc.). Keyword-driven, not
        #      card-name driven.
        #   3. The candidate spell is NOT itself a finisher.
        #   4. Casting the candidate would leave less mana than
        #      the cheapest finisher's cmc — i.e. the candidate
        #      mana-sequences the finisher OUT of this turn.
        # then penalize the candidate by `opp_life / 2.0` — same
        # magnitude shape as the reanimation-readiness boost above
        # (line 576): the reanimator GAINS opp_life/2 when its
        # set-up is complete; here we LOSE opp_life/2 when our
        # candidate would tear our set-up down. Symmetric
        # derivation, no magic numbers.
        if self.goal_engine is not None:
            from ai.gameplan import GoalType
            cur_goal_s2 = self.goal_engine.current_goal
            if cur_goal_s2.goal_type == GoalType.EXECUTE_PAYOFF:
                from engine.cards import Keyword as Kw
                finishers_in_hand = [
                    c for c in me.hand
                    if Kw.STORM in getattr(c.template, 'keywords', set())
                    and c.instance_id != card.instance_id
                ]
                is_self_finisher = (
                    Kw.STORM in getattr(t, 'keywords', set())
                )
                if finishers_in_hand and not is_self_finisher:
                    # Effective costs, not printed CMC (5-panel audit
                    # Unresolved #4 — partial-chain decision math).
                    # With cost reducers on board (Ruby Medallion,
                    # Electromancer shells) the finisher's REAL cost
                    # shrinks — comparing printed cmc made the gate
                    # fire when the finisher could not actually be
                    # locked out, suppressing all chain fuel below
                    # the payoff so the payoff fired FIRST at a
                    # sub-lethal storm count (trace: Azorius vs Ruby
                    # Storm s60100, Grapeshot fired at storm=2 for 4
                    # damage into 17 with Glimpse still castable).
                    # Route through the W0-F cost primitive — the
                    # single owner of cost-modification math.
                    from ai.effective_cmc import effective_cmc
                    cheapest_finisher_cost = min(
                        effective_cmc(f, snap, game=game,
                                      player_idx=self.player_idx)
                        for f in finishers_in_hand
                    )
                    candidate_cost = effective_cmc(
                        card, snap, game=game,
                        player_idx=self.player_idx)
                    # A ritual candidate REBUILDS mana — credit its
                    # oracle-derived production (same template
                    # property `combo_chain.classify_card` reads), so
                    # a mana-positive ritual is never treated as
                    # locking the finisher out.
                    ritual_data = getattr(t, 'ritual_mana', None)
                    mana_produced = ritual_data[1] if ritual_data else 0
                    post_cast_mana = (snap.my_mana - candidate_cost
                                      + mana_produced)
                    if post_cast_mana < cheapest_finisher_cost:
                        # Finisher_unlock_chance: 1.0 when the
                        # finisher IS castable right now (mana >=
                        # effective cost); else 0.0. Oracle-derived
                        # from current snapshot, no magic numbers.
                        finisher_unlock_chance = (
                            1.0 if snap.my_mana >= cheapest_finisher_cost
                            else 0.0
                        )
                        ev -= finisher_unlock_chance * snap.opp_life / 2.0

        # ── Amulet + Titan ramp combo ──
        # Generic detection: if we hold Primeval Titan (or any 6-mana "when
        # this creature enters, search for two lands" creature) AND this card
        # is Amulet of Vigor, the acceleration is enormous — each Amulet +
        # bounce-land loop effectively doubles our ramp, enabling Titan 1-2
        # turns earlier. `card_combo_modifier` is gated on
        # `profile.has_combo_chain`, which Amulet Titan does not declare,
        # so wire ramp combo detection directly here. Similarly bump the
        # Titan itself when Amulet is already down.
        # (t_oracle defined above near the combo_modifier call.)
        is_amulet = ('whenever' in t_oracle and 'enters tapped' in t_oracle
                     and 'untap it' in t_oracle)
        has_titan_in_hand = any(
            'search your library' in (c.template.oracle_text or '').lower()
            and 'two' in (c.template.oracle_text or '').lower()
            and 'land' in (c.template.oracle_text or '').lower()
            for c in me.hand if c.template.is_creature)
        has_amulet_on_board = any(
            ('whenever' in (c.template.oracle_text or '').lower()
             and 'enters tapped' in (c.template.oracle_text or '').lower()
             and 'untap it' in (c.template.oracle_text or '').lower())
            for c in me.battlefield)
        is_titan_like = (t.is_creature and (t.cmc or 0) >= BIG_CREATURE_CMC_FLOOR
                         and 'search your library' in t_oracle
                         and 'two' in t_oracle and 'land' in t_oracle)
        # Amulet + Titan mana synergy: deterministic rules math.
        # When Titan ETBs with Amulet on the battlefield, both fetched
        # lands come in tapped and Amulet untaps them → +2 lands worth
        # of mana are available the same turn. Bounce lands (Simic
        # Growth Chamber, etc.) are even better under Amulet — they
        # bounce a land for re-play while staying untapped for another
        # tap next turn. Floor the effect at 2 lands untapped; bounce
        # lands compound further but we don't model that precisely.
        # Phase 1 refactor: AMULET_TITAN_MANA_BONUS dropped; the scaling
        # factor is now `_llm_weight(self.archetype,
        # CTX_AMULET_TITAN_MANA_BONUS)` (historical 4.0 = 2 lands × 2
        # mana, preserved as the default-table fallback).
        from ai.clock import mana_clock_impact
        mana_impact = mana_clock_impact(snap)  # value per point of mana
        if is_amulet and has_titan_in_hand:
            # P(Titan lands in time) proxy: how many turns until we can
            # cast a 6-drop. If we're at 4+ lands, near-immediate.
            turns_to_cast = max(1, BIG_CREATURE_CMC_FLOOR - len(me.lands))
            _amulet_w = _llm_weight(self.archetype, CTX_AMULET_TITAN_MANA_BONUS)
            # Discount by turns — Amulet benefit realized only once Titan lands.
            ev += (_amulet_w * mana_impact * CLOCK_IMPACT_LIFE_SCALING) / turns_to_cast
        if is_titan_like and has_amulet_on_board:
            _amulet_w = _llm_weight(self.archetype, CTX_AMULET_TITAN_MANA_BONUS)
            # Immediate payoff when Titan is being cast now.
            ev += _amulet_w * mana_impact * CLOCK_IMPACT_LIFE_SCALING

        # ── Non-creature permanent overlay (Pattern B) ──
        # Planeswalker loyalty-pool scoring moved to
        # `ai.ev_evaluator.expected_future_value`, which is composable
        # across permanent types and credited through
        # `EVSnapshot.persistent_power` so the same `urgency_factor`
        # decay applied to recurring-trigger tokens applies to PW pools
        # as well. The previous inline `pw_bonus` here double-counted
        # once the projection layer started crediting the pool — see
        # M5 / 2026-05-16 audit Control Decision 1 + 7.
        from engine.cards import CardType
        if not t.is_creature and not t.is_instant and not t.is_sorcery:
            if 'cost_reducer' in tags:
                # Saves ~1 mana per spell over the remaining game — derive
                # from card_clock_impact × turns_remaining rather than +4.
                from ai.clock import card_clock_impact, combat_clock, NO_CLOCK
                my_c = combat_clock(snap.my_power, snap.opp_life,
                                     snap.my_evasion_power, snap.opp_toughness)
                opp_c = combat_clock(snap.opp_power, snap.my_life,
                                      snap.opp_evasion_power, snap.my_toughness)
                turns = min(my_c, opp_c)
                if turns >= NO_CLOCK:
                    turns = MIDGAME_HORIZON_TURNS  # rules constant: Modern midgame horizon
                turns = max(GAME_HORIZON_MIN_TURNS, min(turns, GAME_HORIZON_MAX_COST_REDUCER))
                ev += turns * card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING

        # ── Activated win-condition line: planeswalker ultimate ──
        # A walker whose ultimate wins/locks the game is a win
        # condition when its loyalty trajectory reaches the line
        # inside the opponent-clock horizon (Track H — the loyalty-
        # pool projection above credits generic activations but not
        # the win line itself).  Value derived in ai/pw_ability.py
        # from clock primitives; pinned by
        # tests/test_pw_ultimate_line_valued_when_reachable.py.
        if CardType.PLANESWALKER in t.card_types:
            from ai.pw_ability import ultimate_win_line_value
            ev += ultimate_win_line_value(
                t.oracle_text or '', t.loyalty or 0, snap)

        # ── Duplicate Chalice-of-the-Void / hate permanent penalty ──
        # Casting a second Chalice with the same X is useless (same CMC
        # locked). The value of a redundant permanent is zero; penalty
        # equals the mana we'd waste casting it, derived from mana_clock_impact
        # rather than a flat -8.
        if t.x_cost_data and 'charge_counter' in (t.oracle_text or '').lower():
            existing = [c for c in me.battlefield if c.name == t.name]
            if existing:
                from ai.clock import mana_clock_impact
                cmc = t.cmc or 2
                ev -= cmc * mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING

        # ── Redundant non-stacking static permanent ──
        # When a non-creature, non-spell permanent with the same name
        # is already on the battlefield AND its oracle text describes
        # a pure static ability (no triggered abilities, no per-cast
        # cost reduction, no per-instance scaling), the second copy
        # adds no marginal value — Blood Moon, Damping Sphere,
        # Trinisphere, Leyline of Sanctity / Void all share this
        # shape.  Penalize the cast so the AI advances toward its
        # win condition instead.
        #
        # Cards that DO stack (must NOT be penalised):
        #   * "Whenever ... enters" / "When this enters" — each
        #     copy fires its own ETB trigger (Spelunking → +1 draw
        #     + land drop; Amulet of Vigor → another untap event)
        #   * "Whenever a player casts" / "Whenever you cast" —
        #     each copy triggers separately on the same cast
        #   * "cost {N} less" — cost reductions are cumulative
        #     (Ruby Medallion, Goblin Electromancer)
        #   * "for each" / "for every" — explicit scaling
        #   * Active "{T}: ..." abilities — per-source activation
        #
        # Detection is oracle-driven (no card names).  Penalty = mana
        # we'd waste, derived via `mana_clock_impact(snap)` so the
        # scaling matches the rest of the EV pipeline.
        if (not t.is_creature and not t.is_instant and not t.is_sorcery
                and not t.is_land):
            same_name_on_bf = any(c.name == t.name for c in me.battlefield)
            if same_name_on_bf:
                oracle_lower = (t.oracle_text or '').lower()
                stacks = (
                    getattr(t, 'has_scaling_effect', False)  # for each/every
                    or getattr(t, 'is_cost_reducer', False)  # cost reducers
                    or getattr(t, 'has_recurring_trigger', False)  # triggered
                    or getattr(t, 'has_self_trigger', False)  # ETB/attack/die triggers
                    or 'when ' + t.name.lower() + ' enters' in oracle_lower
                    or '{t}:' in oracle_lower           # tap abilities
                )
                if not stacks:
                    from ai.clock import mana_clock_impact
                    cmc = t.cmc or 1
                    ev -= cmc * mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING

        # ── Board wipe hard gate ──
        # Empty-board wrath provides no creature-removal benefit.  The
        # opportunity cost = mana spent + card consumed.  Mana cost via
        # the standard pipeline (cmc × mana_clock_impact × 20.0); card
        # loss is one EV unit (the smallest meaningful EV difference,
        # standing in for "one card of expected value").  No sentinel
        # — if the wrath has independent positive EV (artifact destroy
        # mode, scry rider, etc.) it can still pass the threshold.
        if 'board_wipe' in tags and snap.opp_creature_count == 0:
            from ai.clock import mana_clock_impact
            waste_penalty = ((t.cmc or 0) * mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
                             + 1.0)  # +1 = card loss (EV unit)
            ev -= waste_penalty

        # ── Self-wipe gate ──
        # When we're ahead on the board and not dying, wiping destroys our
        # own equity (WST, Sanctifier, etc.) for no net gain. Audit: Wrath
        # of the Skies WinCR 18% in WST because the deck self-wipes its own
        # value engines. If we're winning the board fight and have time
        # (opp_clock_discrete >= 3), board wipes are strictly negative EV.
        if 'board_wipe' in tags and snap.opp_creature_count > 0:
            from ai.clock import mana_clock_impact
            am_dying = snap.am_dead_next or snap.opp_clock_discrete <= 2
            ahead_on_board = (
                snap.my_creature_count >= snap.opp_creature_count
                and snap.my_power > snap.opp_power
            )
            if ahead_on_board and not am_dying:
                # Same opportunity-cost penalty as above + lost-equity
                # for our own creatures wiped.  Each lost creature
                # contributes its `permanent_threat` value (already
                # imported elsewhere in this method).
                from ai.permanent_threat import permanent_threat
                me_lost = sum(
                    permanent_threat(c, me, game)
                    for c in me.battlefield
                    if c.template.is_creature
                )
                waste_penalty = ((t.cmc or 0) * mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING
                                 + 1.0 + me_lost)
                ev -= waste_penalty

        # X-cost board-wipe waste gate (hold when X can't meaningfully clear).
        x_gate = self._gate_x_cost_board_wipe(ev, t, tags, snap, opp, game)
        if x_gate is not None:
            return x_gate

        # ── Blink/flicker hard gate: no legal target means the spell fizzles ──
        # Engine safely bails (Ephemerate returns early), but AI should never
        # score a mana-wasting fizzle as positive EV. Detect by oracle pattern
        # "target creature you control" on an instant/sorcery.
        if ('blink' in tags or getattr(t, 'has_exile_own_creature', False)) \
                and (t.is_instant or t.is_sorcery) \
                and len(me.creatures) == 0:
            return min(ev, BLINK_FIZZLE_FLOOR)

        # ── Blink credits re-triggering an on-board ETB-value creature ──
        # Re-firing a value ETB (Solitude re-exile, Quantum Riddler redraw)
        # while keeping the body is the proactive premise of flicker decks.
        # The reactive response path already credits BLINK_ETB_RETRIGGER_BONUS
        # for this; the main-phase scorer did not, so a blink held for value
        # scored ~0 and was never cast. Credit the re-trigger when we control
        # an `etb_value` creature the blink could target. Tag-gated — every
        # flicker spell x every ETB-value creature, no card names.
        if ('blink' in tags and (t.is_instant or t.is_sorcery)
                and any('etb_value' in getattr(c.template, 'tags', set())
                        for c in me.creatures)):
            ev += BLINK_ETB_RETRIGGER_BONUS

        # ── Pre-combat blink forfeits the target's attack (CR 400.7) ──
        # The blinked permanent re-enters as a NEW, summoning-sick object
        # carrying only its printed keywords — so blinking an attack-
        # capable target in OUR Main 1 forfeits its combat step this
        # turn (a temporary haste grant dies with the old object).
        # Charge that forfeit at its clock price: one combat step of the
        # presumed target (`ai/clock.forfeited_attack_clock_impact` —
        # power kill-fraction, lifelink swing included), converted to EV
        # units via CLOCK_IMPACT_LIFE_SCALING like every other clock
        # term in this method. This REPLACES the old flat
        # BLINK_M1_HOLD_PENALTY nudge (2.0), which was empirically never
        # decisive: 4/4 assembled reanimation lines still blinked
        # pre-combat and forfeited the whole swing — once with lethal on
        # board (docs/diagnostics/2026-08-27_reanimator_pair_root_cause.md).
        # Presumed targets are the creatures the positive terms presume:
        # live EOT-exile riders (threat-credit block below) and
        # etb_value creatures (retrigger bonus above). Post-combat
        # (Main 2) and opponent-turn casts are never charged — the play
        # loop re-enumerates the blink in Main 2 (game_runner MAIN2 →
        # _execute_main_phase), where the rider credit below prices
        # keeping the body.
        if ('blink' in tags and (t.is_instant or t.is_sorcery)
                and game is not None
                and getattr(game, 'active_player', None) == self.player_idx
                and 'MAIN1' in str(getattr(game, 'current_phase', ''))):
            presumed = list(self._pending_eot_exile_riders(game))
            _seen = {c.instance_id for c in presumed}
            presumed += [
                c for c in me.creatures
                if c.instance_id not in _seen
                and 'etb_value' in getattr(c.template, 'tags', set())]
            charges = [self._forfeited_attack_charge(c, snap)
                       for c in presumed
                       if self._blink_would_forfeit_attack(c)]
            if charges:
                ev -= max(charges)  # swing first, blink post-combat

        # ── Blink clears a live pending EOT-exile detriment (CR 400.7) ──
        # A delayed "exile it at the beginning of the next end step"
        # rider (Goryo's Vengeance / Sneak Attack shape) tracks a
        # specific object; blinking makes the permanent a new object and
        # sheds the rider (engine side: PR #462 object identity).
        # Keeping the body past end of turn is worth its full threat
        # value — derived from `creature_threat_value`, the same
        # principled subsystem removal targeting uses.  Sequencing: the
        # rider only fires at OUR end step, so in our MAIN1 an attack-
        # capable rider should swing first (the temporary body still
        # deals combat damage) and be blinked post-combat — pre-combat
        # the credit is withheld and the forfeited-attack charge above
        # prices the wait, steering the cast to MAIN2.  RC-1 decision
        # layer, docs/diagnostics/
        # 2026-07-05_goryos_field_13pct_root_cause.md and
        # 2026-08-27_reanimator_pair_root_cause.md.
        if ('blink' in tags and (t.is_instant or t.is_sorcery)
                and game is not None):
            riders = self._pending_eot_exile_riders(game)
            if riders:
                best_rider = max(
                    riders, key=lambda c: creature_threat_value(c, snap))
                in_own_main1 = (
                    getattr(game, 'active_player', None) == self.player_idx
                    and 'MAIN1' in str(getattr(game, 'current_phase', '')))
                if in_own_main1 and self._blink_would_forfeit_attack(
                        best_rider):
                    # Credit withheld: the forfeit charge above already
                    # prices casting now vs. after combat.
                    pass
                else:
                    ev += creature_threat_value(best_rider, snap)

        # ── Reserve mana for a blink that clears a live rider ──
        # The credit above prices the blink; this prices everything
        # ELSE: a competing cast that would strand the held blink
        # forfeits the clearance value (same primitive, opposite sign).
        if 'blink' not in tags and game is not None:
            ev += self._blink_reservation_penalty(
                me, snap, cost=t.cmc or 0,
                exclude_instance_id=card.instance_id, game=game)

        # ── Noncreature-only counter dead vs creature-heavy opponents ──
        # Dovin's Veto / Negate can't target creature spells.
        # Gate positive EV when opponent's board is all creatures and hand
        # is likely all creatures too (aggro decks like Boros).
        if ('counterspell' in tags and t.counter_target_kind == 'noncreature_spell'
                and snap.opp_creature_count >= 2
                and snap.opp_power >= NONCREATURE_COUNTER_AGGRO_POWER
                and snap.opp_hand_size <= NONCREATURE_COUNTER_AGGRO_HAND):
            # Opponent is an aggro deck running out of cards — counter is dead
            ev = min(ev, NONCREATURE_COUNTER_DEAD_FLOOR)

        # ── Removal threat-premium overlay ──
        # The projection subtracts raw power when removal resolves, but
        # battle-cry / scaling creatures (e.g. Signal Pest, Ragavan) carry
        # threat beyond their P/T. Compensate with a decision-layer bonus
        # derived from `creature_threat_value` — the same function used by
        # the gate (`_has_high_threat_target`) and target picker, so all
        # three decisions stay consistent.
        if ('removal' in tags and not 'board_wipe' in tags
                and not t.is_creature and opp.creatures):
            from decks.card_knowledge_loader import get_burn_damage
            burn_dmg = get_burn_damage(t.name) if t.name else 0
            reachable = []
            for c in opp.creatures:
                if burn_dmg > 0:
                    rem = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
                    if rem > burn_dmg:
                        continue
                reachable.append(c)
            if reachable:
                best = max(reachable, key=lambda c: creature_threat_value(c, snap))
                premium = creature_threat_value(best, snap) - creature_value(best, snap)
                if premium > 0:
                    # Scale: premium * 0.5 (battle-cry ≈ +4 ev) brings removal
                    # into tiebreaker range with equal-CMC deploys. The extra
                    # +1.0 for 1-CMC lets cheap efficient removal (Galvanic
                    # Discharge, Unholy Heat) eke out a win over an equal-CMC
                    # deploy, modelling real-world play where a 1-mana
                    # removal leaves room for a second action.
                    ev += premium * REMOVAL_THREAT_PREMIUM_SCALE
                    if (t.cmc or 0) <= 1:
                        ev += CHEAP_REMOVAL_ACTION_BONUS

                # ── Spot-removal timing deferral (BHI-driven) ──
                # Pro-annotation rule (replay seed 60100 G1 T1):
                # "Spot-removal value depends on the best target
                # available across the next 2 turns, not just the
                # current best target."
                #
                # When BHI predicts a higher-EV target arriving within
                # the next 2 turns, reduce the EV of a CHEAP removal
                # cast on the current low-tier target.  Detection:
                #   * Cheap = effective CMC ≤ 1.  Higher-CMC removal
                #     occupies a different turn slot (e.g. T3 Path)
                #     and isn't typically interchangeable across turns.
                #   * "Higher-EV target arrives" = BHI's
                #     `p_higher_threat_in_n_turns` against the
                #     opp library + hand size.
                #
                # The deferral is a ONE-DIRECTION reduction: it never
                # *adds* score, only reduces it.  Existing logic still
                # decides "is this a valid removal target"; this layer
                # adds "is this the right TIME for this removal".
                if (t.cmc or 0) <= 1 and self.bhi is not None:
                    # Initialise BHI on demand - decide_main_phase
                    # may run before any priority pass has triggered
                    # initialize_from_game.
                    if not self.bhi._initialized:
                        self.bhi.initialize_from_game(game)
                    target_value = creature_threat_value(best, snap)
                    p_better = self.bhi.beliefs.p_higher_threat_in_n_turns(
                        current_target_value=target_value,
                        turns=2,
                        opp_library=opp.library,
                        opp_hand_size=len(opp.hand),
                        snap=snap,
                    )
                    # REMOVAL_DEFERRAL_TARGET_GAP encodes the typical
                    # ``creature_threat_value`` gap between a 1-power
                    # vanilla body and a premium (battle-cry / equipped /
                    # scaling) future target - same scale as the
                    # threat-premium term above. Derivation lives in
                    # ai/scoring_constants.py.
                    from ai.scoring_constants import REMOVAL_DEFERRAL_TARGET_GAP
                    deferral_penalty = p_better * REMOVAL_DEFERRAL_TARGET_GAP
                    ev -= deferral_penalty

        # ── Artifact/enchantment-hate removal overlay ──
        # Spells like Wear // Tear, Boseiju, Force of Vigor target non-
        # creature permanents. `_project_spell` models removal as
        # creature-killing; that projection gives ~zero EV when opp has
        # no threatening creatures. For artifact/enchantment-hate, the
        # real target-value is the marginal contribution of the best
        # hittable permanent — exactly what `permanent_threat` returns.
        # Detection is purely oracle-driven (target artifact / enchantment
        # / nonland permanent / noncreature permanent); no card names.
        if ('removal' in tags and not 'board_wipe' in tags
                and not t.is_creature):
            o_lower = (t.oracle_text or '').lower()
            hits_noncreature = ('target artifact' in o_lower
                                or 'target enchantment' in o_lower
                                or 'target nonland permanent' in o_lower
                                or 'target noncreature' in o_lower)
            if hits_noncreature:
                from engine.cards import CardType
                from ai.permanent_threat import permanent_threat
                candidates = []
                for c in opp.battlefield:
                    if c.template.is_land:
                        continue
                    if c.template.is_creature and 'target creature' not in o_lower:
                        continue
                    if ('target artifact' in o_lower
                            and CardType.ARTIFACT not in c.template.card_types):
                        if not ('target enchantment' in o_lower
                                or 'target nonland' in o_lower
                                or 'target noncreature' in o_lower):
                            continue
                    candidates.append(c)
                if candidates:
                    best = max(candidates,
                               key=lambda c: permanent_threat(c, opp, game))
                    tv = permanent_threat(best, opp, game)
                    # Marginal threat is already in position-value units
                    # (the same scale as _score_spell's lookahead delta).
                    # Add it directly — no halving / no tier remapping.
                    ev += tv

        # ── Mana holdback (Bundle 3 A1, A3, A4) ──
        # Scaled, color-aware penalty for tapping out while holding
        # instant-speed interaction. Implemented in _holdback_penalty so
        # _score_cycling and _consider_equip can reuse the same gate.
        # Fast-skip when this profile doesn't hold, the candidate IS an
        # instant, or it has flash — none of those tap out.
        holdback = 0.0
        if p.holdback_applies and not t.is_instant and not t.has_flash:
            holdback = self._holdback_penalty(
                me, opp, snap, cost=t.cmc or 0,
                exclude_instance_id=card.instance_id, game=game)
            ev += holdback

        # ── Stax lock-piece overlay (P1-1) ──
        # `stax_lock_ev` returns a positive EV for stax permanents
        # (Chalice, Blood Moon, Canonist, Torpor Orb) based on
        # opponent deck composition. The bonus is GATED on
        # `holdback >= 0`: if tapping out for this play would
        # forfeit held instant-speed interaction, the overlay must
        # not crowd out the concrete answer. Without this gate the
        # AI casts T2 Chalice over a held Counterspell (the WST
        # regression that caused the previous wiring to be reverted).
        #
        # M3 (signed holdback): `holdback >= 0.0` captures both the
        # pre-M3 "no holdback" state (= 0.0) AND the new positive-
        # bonus branch (no defensive use → proactive tap-out is
        # actively rewarded).  Equivalent intent: the gate fires
        # whenever the held-response penalty path is silent.
        if holdback >= 0.0:
            from ai.stax_ev import stax_lock_ev
            ev += stax_lock_ev(t, me, opp, snap)

        phyrexian_count = getattr(t, 'phyrexian_pip_count', 0)
        if phyrexian_count > 0:
            life_cost = phyrexian_count * 2
            ev -= life_cost / max(1, snap.my_life) * PHYREXIAN_LIFE_PENALTY_SCALE

        return ev

    def _holdback_penalty(self, me, opp, snap: EVSnapshot, cost: int,
                          exclude_instance_id: Optional[int] = None,
                          game: "GameState" = None) -> float:
        """Signed mana-holdback cost (M3 — proactive tap-out).

        Returns the EV adjustment for tapping out by `cost` mana on a
        sorcery-speed play.  Sign convention:

        - Negative (penalty) when the open mana has a defensive use —
          held instant-speed interaction + an opp able to deploy a
          counterable threat.  This is the original Bundle 3 / B3-Tune
          behaviour, unchanged.
        - Positive (bonus) when no defensive use exists — no held
          interaction, opp has no follow-up threat, etc.  Holding mana
          for nothing is strictly worse than spending it on a marginal
          play, so the bonus rewards proactive deployment.  Replaces
          the deleted binary `pass_threshold` gate (M3, audit
          `docs/history/audits/2026-05-16_5panel_bo3_audit.md` — control
          panel + combo cross-pattern #2).

        Pre-M3 history (penalty branch unchanged):
        A1 — penalty scales by `counter_count × counter_cmc × opp_threat_prob`.
        A3 — extracted as a helper for `_score_cycling` and `_consider_equip`.
        A4 — opp-spell-deck threshold kept at `opp_hand_size >= 4`.
        A5 — colored-aware amplifier when tap-out empties a held color.

        M3 — bonus branch added (no new magic numbers): bonus scales by
        `cost × held_response_value_per_cmc(0.0) × (1 - opp_action_prob)`.
        Same per-CMC value primitive used by the penalty side, evaluated
        at the no-artifact-threat baseline.  When the opponent is
        certain to act next turn (opp_action_prob → 1.0) the bonus
        collapses to zero — the original "no holdback at all" floor.

        Profile gate (`holdback_applies`) controls only the PENALTY
        branch — AGGRO / COMBO / STORM / RAMP declare they have no
        defensive use for mana, so the penalty side is skipped and the
        function returns the proactive bonus directly.  M3: those
        archetypes also benefit from positive tap-out signal (the
        audit's "combo cross-pattern" — Storm chains break when fuel
        is held under a threshold gate that has no defensive
        justification on the combo turn).
        """
        p = self.profile

        # ── Profile-side penalty gate (early bonus return) ───────────
        # Profiles with `holdback_applies=False` (AGGRO / COMBO / STORM
        # / RAMP) declare they have no defensive use for mana — the
        # penalty branch never fires for them.  M3: return the
        # proactive bonus directly so these decks get a positive tap-
        # out signal in every state, not just the opp-tapped-out case.
        if not p.holdback_applies:
            return self._proactive_tap_out_bonus(snap, opp, cost)

        # ── Holdback relevance gate ──────────────────────────────────
        # When opp has no creatures AND a small hand they cannot present
        # a follow-up threat.  Pre-M3 the function returned 0.0 here and
        # left the binary `pass_threshold` gate to decide whether to
        # pass.  M3: there is no defensive use for the held mana, so
        # return a POSITIVE bonus instead — the AI should proactively
        # spend the mana on the best available play.
        opp_has_spells = (snap.opp_hand_size >= OPP_HAND_FULL_HOLDBACK_THRESHOLD
                          and snap.opp_power == 0)
        holdback_relevant = (snap.opp_power > 0
                             or snap.opp_hand_size >= OPP_HAND_FULL_HOLDBACK_THRESHOLD
                             or opp_has_spells)
        if not holdback_relevant:
            return self._proactive_tap_out_bonus(snap, opp, cost)

        # ── Find held instant-speed interaction in hand ──────────────
        # Oracle/tag-driven (no card names). counter_cmc is the average
        # cost of held interaction — used to size the penalty.
        held_costs: list = []
        held_weights: list = []
        held_colors: set = set()
        from ai.card_classes import is_held_interaction
        for c in me.hand:
            if exclude_instance_id is not None \
                    and c.instance_id == exclude_instance_id:
                continue
            tmpl = c.template
            # Membership comes from the central class registry — the
            # cast-lock omission that made control tap out vs
            # creatureless combo was the founding incident
            # (docs/proposals/2026-07-09_structural_findings.md #2).
            if not is_held_interaction(tmpl):
                continue
            # Tax-counter liveness (the 1a framework's holdback side).
            # A "counter unless its controller pays {N}" counter stops a
            # spell only when casting it leaves the opponent unable to pay
            # the tax; against a pool of cheap effective costs it stops
            # ~nothing, and reserving mana for it is reserving mana for
            # nothing. Weight its held value by the fraction of the
            # opponent's castable pool it actually answers (0 → drop it
            # entirely). Hard counters keep weight 1 and are untouched.
            # docs/diagnostics/2026-08-26_decider_loss_root_cause.md
            # (secondary root cause: the stranded-finisher decider loss).
            weight = 1.0
            tax = getattr(tmpl, 'counter_tax_amount', 0) or 0
            if tax > 0 and 'counterspell' in tmpl.tags:
                weight = self._held_tax_counter_liveness(game, opp, tax)
                if weight <= 0.0:
                    continue
            held_costs.append(tmpl.cmc or 0)
            held_weights.append(weight)
            mc = tmpl.mana_cost
            for code, attr in (
                ('W', 'white'), ('U', 'blue'), ('B', 'black'),
                ('R', 'red'), ('G', 'green'),
            ):
                if getattr(mc, attr, 0) > 0:
                    held_colors.add(code)

        if not held_costs:
            # M3: no held interaction → no defensive use → return bonus.
            # Same `_proactive_tap_out_bonus` primitive used by the
            # opp-no-threat branch above; both encode "holding mana for
            # nothing is strictly worse than spending it on a marginal
            # play."  The bonus collapses to zero when the opponent is
            # certain to threaten, so this never over-fires.
            return self._proactive_tap_out_bonus(snap, opp, cost)


        # ── Color-capacity early-exit (Iteration-2 B3-Tune) ──────────
        # If every held counter can still be paid AFTER this cast —
        # i.e. remaining sources of every held color are still >= the
        # max held counter CMC — the held interaction is not at risk
        # and there's no capacity to penalise. We pay the cast's
        # generic cost from off-color mana FIRST (rational optimum),
        # and only dip into held-color sources when off-color runs out.
        # Post-cast floor for color c:
        #   remaining_c = max(0, my_by_color[c] - max(0, cost - off_c))
        # where off_c = my_mana - my_by_color[c]. This captures the
        # common case of a control deck with enough off-color mana
        # (Mountains, Plains, colorless) to cover the cast while
        # leaving U / B untouched for a held Counterspell.
        my_by_color = getattr(snap, 'my_mana_by_color', {}) or {}
        if held_colors:
            max_counter_cmc = max(held_costs)
            color_capacity_preserved = True
            for color in held_colors:
                available_now = my_by_color.get(color, 0)
                off_color_mana = max(0, snap.my_mana - available_now)
                must_tap_from_color = max(0, cost - off_color_mana)
                remaining_after = max(0, available_now - must_tap_from_color)
                if remaining_after < max_counter_cmc:
                    color_capacity_preserved = False
                    break
            if color_capacity_preserved:
                return 0.0

        # ── Opp-threat probability (BHI-derived) ─────────────────────
        # If BHI has been initialised it gives a calibrated probability
        # the opponent has a follow-up threat we'd want to interact
        # with. Fallback heuristic for un-initialised BHI: blend opp
        # board pressure (power per turn already on the table) with
        # opp hand density (more cards in hand = more likely to deploy
        # a real threat). All values clamped to [0.1, 1.0] — even a
        # quiet board has some baseline threat probability.
        opp_threat_prob = self._estimate_opp_threat_prob(snap, opp)

        # ── Lost-response-VALUE model (supersedes Brief A1's pile
        # scaling — docs/diagnostics/2026-08-26_decider_loss_root_cause.md
        # second-pass forensics) ──────────────────────────────────────
        # Lands untap every turn, so mana held now buys responses only in
        # THIS turn's response window: the value at risk is exactly the
        # held responses that no longer fit after the cast, not the whole
        # pile. A1's count-scaling charged an interaction-heavy hand the
        # full pile for every tap-out (a draw spell observed at EV -51.8,
        # a 3-CMC engine at -24.6), so a reactive deck holding four
        # answers could never deploy anything and never presented a
        # clock. Cheapest-first packing approximates the worst-case
        # response sequence (counter the cheapest threats first to keep
        # options open); each packed response contributes its liveness-
        # weighted CMC, so a payable-through tax counter is worth only
        # its live fraction here too.
        pairs = sorted(zip(held_costs, held_weights))

        def _packed_value(mana: int) -> float:
            m, v = mana, 0.0
            for c, w in pairs:
                if m >= c:
                    m -= c
                    v += w * c
                else:
                    break
            return v

        lost_value = max(0.0, _packed_value(snap.my_mana)
                         - _packed_value(max(0, snap.my_mana - cost)))

        # Penalty fires only when tapping out actually forfeits response
        # value — if every held response still fits, holdback is moot.
        if lost_value <= 0:
            return 0.0

        # Scale: lost_value × opp_threat_prob ×
        # held_response_value_per_cmc(p_artifact_threat). The per-CMC
        # value is the function-form from ai/scoring_constants.py,
        # sourced from BHI's artifact-threat belief: floored at the
        # Iter-2 base (4.0) for the average opponent, ramped to 6.0
        # against Affinity-class. A full tap-out that strands the whole
        # pile reproduces A1's original magnitude; a partial tap-out
        # charges only what it costs.
        p_art = 0.0
        try:
            if self.bhi and self.bhi._initialized:
                p_art = self.bhi.beliefs.p_artifact_threat
        except Exception:
            pass
        held_value_per_cmc = held_response_value_per_cmc(p_art)
        # Liveness-weighted mean per held card — consumed by the A5
        # color-availability amplifier below as "the value of one
        # typical held response"; a payable-through tax counter's
        # stranding is worth only its live fraction there too.
        counter_cmc = (sum(w * c for w, c in zip(held_weights, held_costs))
                       / len(held_costs))
        base_penalty = (lost_value
                        * opp_threat_prob
                        * held_value_per_cmc)

        # ── Color-availability amplifier (A5) ────────────────────────
        # If this play would leave us with FEWER sources of a held
        # color than the held interaction needs, the held spell becomes
        # uncastable (not merely tempo-delayed). For each color in
        # held_colors that this play empties, escalate the penalty.
        # A play that taps the only U source while we hold a UU
        # Counterspell forfeits the response entirely — even if our
        # generic mana count would otherwise suggest we have spare.
        # Approximate post-play color availability: the play consumes
        # `cost` lands worth of mana; in the worst case this includes
        # every land producing a held color.
        remaining_mana = max(0, snap.my_mana - cost)
        color_kills = 0
        for color in held_colors:
            available_now = my_by_color.get(color, 0)
            # If after the play remaining_mana < the held cost in this
            # color (cost includes generic from these lands), the
            # response is uncastable. Approximation: when the play
            # consumes >= every untapped source of this color, the
            # response is dead.
            if available_now > 0 and remaining_mana < available_now:
                # Conservative: fire amplifier whenever the post-play
                # generic-mana floor < the # of held color sources we
                # had — captures the "Sacred Foundry tapped, no U
                # left" pattern that A5 targets.
                if available_now <= cost:
                    color_kills += 1
        if color_kills > 0:
            # Uncastable held interaction = a free opponent spell. Add
            # the full per-counter response value on top of the lost-
            # capacity penalty (same coefficient as base_penalty above
            # — `held_value_per_cmc` already incorporates the artifact-
            # threat ramp).
            base_penalty += (color_kills * counter_cmc
                             * opp_threat_prob
                             * held_value_per_cmc)

        return -base_penalty


    def _held_tax_counter_liveness(self, game, opp, tax: int) -> float:
        """Fraction of the opponent's castable pool a held tax counter can
        actually stop (CR 601 + the 1a "unless its controller pays {N}"
        framework): a spell is stopped only when paying its own effective
        cost leaves the caster below the tax. Pool-level knowledge only —
        hand + library counted together, the same convention BHI uses for
        its priors — never card location.

        Capacity is the opponent's NEXT-turn mana (they untap everything),
        mirroring the payment path's own terms: lands + floating mana +
        Tron bonus. Effective costs go through `effective_cmc` from the
        opponent's own snapshot so affinity/delve/domain discounts price
        correctly (a domain deck's nominal 12-drop is a real 2-drop).

        Returns a [0, 1] fraction; 1.0 when no game context is available
        (legacy call sites keep the pre-liveness behaviour) and 0.0 when
        the opponent's pool holds no castable spell the counter stops.
        Cached per (turn, opp land count, tax) — the pool only shifts by
        a draw a turn and the scan walks the whole library.
        """
        if game is None:
            return 1.0
        capacity = (len(opp.lands) + opp.mana_pool.total()
                    + opp._tron_mana_bonus())
        opp_idx = 1 - self.player_idx
        cache_key = (game.turn_number, len(opp.lands), tax)
        cached = self._tax_liveness_cache.get(cache_key)
        if cached is not None:
            return cached
        from ai.effective_cmc import effective_cmc
        from ai.ev_evaluator import snapshot_from_game
        opp_snap = snapshot_from_game(game, opp_idx)
        castable = live = 0
        for c in list(opp.hand) + list(opp.library):
            if c.template.is_land:
                continue
            try:
                eff = effective_cmc(c, opp_snap, game=game,
                                    player_idx=opp_idx)
            except Exception:
                eff = c.template.cmc or 0
            if eff <= capacity:
                castable += 1
                if capacity - eff < tax:
                    live += 1
        frac = (live / castable) if castable else 0.0
        self._tax_liveness_cache[cache_key] = frac
        return frac

    def _proactive_tap_out_bonus(self, snap: EVSnapshot, opp,
                                 cost: int) -> float:
        """EV bonus for tapping out when no defensive use exists (M3).

        Sign convention: this is the *positive-cost* branch of
        `_holdback_penalty` — when there is nothing to hold the mana
        for, spending it on a marginal play is strictly better than
        passing.  The bonus replaces the deleted binary `pass_threshold`
        gate (audit `docs/history/audits/2026-05-16_5panel_bo3_audit.md`).

        Formula (no new magic numbers — composed entirely from existing
        primitives):

            bonus = cost
                  × held_response_value_per_cmc(0.0)
                  × (1 - opp_action_prob)

        - `cost` is the mana that would otherwise sit unused.  Bigger
          plays unlock proportionally more bonus, mirroring how the
          penalty side scales with `counter_cmc × counter_count`.
        - `held_response_value_per_cmc(0.0)` is the no-artifact-ramp
          baseline of the same per-CMC value primitive used by the
          penalty side.  Treating the bonus and the penalty as two
          sides of the same coefficient keeps the signed model
          symmetric — a sole source-of-truth, the function in
          `ai/scoring_constants.py`.
        - `(1 - opp_action_prob)` discounts the bonus against the BHI-
          derived probability the opponent will threaten next turn.
          Certain opp action collapses the bonus to zero (the original
          "no holdback at all" floor); a quiet opponent yields the
          full bonus.  Same `_estimate_opp_threat_prob` primitive
          drives both branches.
        """
        # `_estimate_opp_threat_prob` clamps at `HOLDBACK_PROBABILITY_FLOOR`
        # on the low end — a quiet opp still has a baseline top-deck
        # threat probability, so the bonus is never fully (1.0 ×) the
        # per-CMC value.  We use the SAME clamp here to keep penalty
        # and bonus symmetric.
        opp_action_prob = self._estimate_opp_threat_prob(snap, opp)
        per_cmc = held_response_value_per_cmc(0.0)
        return float(cost) * per_cmc * (1.0 - opp_action_prob)

    def _estimate_opp_threat_prob(self, snap: EVSnapshot, opp) -> float:
        """Probability opponent will deploy a meaningful threat next turn.

        Derived from:
        - BHI removal/counter beliefs (when initialised) — already a
          calibrated posterior reflecting observed plays + deck
          composition.
        - Otherwise: opp board power as fraction of our life (creatures
          already on the table) + opp hand density (cards left to
          deploy) + archetype aggression hint from the opp's deck.
        Output clamped to [0.1, 1.0]; even a quiet opponent has some
        baseline threat from top-decks.
        """
        # BHI path
        try:
            bhi = self.bhi
            if bhi and bhi._initialized:
                # P(opp threatens us this/next turn) ≈ max of P(removal)
                # and P(follow-up creature inferred from non-counter
                # density). The first term is the calibrated posterior
                # over reactive cards (counters + removal + burn). The
                # second term derives the unknown-hand threat
                # probability from the BHI density prior — the rule is
                # `1 - (1 - density) ** opp_hand_size`, the standard
                # Bernoulli-trials formula already used by every other
                # density-based BHI belief. This replaces the prior
                # flat `0.5 * hand_factor` weighting which inflated the
                # threat probability identically against any opp at
                # equal hand size, regardless of pool composition.
                p_action = max(bhi.beliefs.p_removal,
                               bhi.beliefs.p_counter,
                               bhi.beliefs.p_burn)
                p_unknown_threat = opp_threat_prob_from_density(
                    bhi.beliefs.p_threat_in_hand_density,
                    snap.opp_hand_size,
                )
                # Combine: either the known interaction posterior OR a
                # density-derived unknown-threat draw fires holdback.
                # MAX (not SUM) keeps the result a probability in
                # [0.1, 1.0] without silent over-saturation.
                return max(HOLDBACK_PROBABILITY_FLOOR, min(1.0, max(p_action, p_unknown_threat)))
        except Exception:
            pass

        # Heuristic fallback — combine three signals:
        # (a) opponent has creatures on board → they're playing threats,
        #     expect more (signal saturates at 2+ creatures).
        # (b) hand size as a fraction of starting hand (7) — more cards
        #     = more chances to draw a real threat.
        # (c) clock pressure — opponent's existing power as fraction of
        #     our life (we want to interact when they're close to lethal).
        # We take the MAX of these so any one strong signal triggers
        # full holdback; sum-and-divide undercounts when (e.g.) the
        # board already has visible threats but they're all 1/1s.
        creature_signal = min(1.0, snap.opp_creature_count / 2.0)
        hand_signal = min(1.0, snap.opp_hand_size / STARTING_HAND_SIZE)
        clock_signal = 0.0
        if snap.my_life > 0 and snap.opp_power > 0:
            clock_signal = min(1.0, snap.opp_power / max(1, snap.my_life))
        return max(HOLDBACK_PROBABILITY_FLOOR, min(1.0,
                            max(creature_signal, hand_signal, clock_signal)))

    def _score_land(self, land, me, spells, game) -> float:
        """Score a land play using clock-derived values.

        Land value = mana enables spells → spells change clock.
        Higher priority than most spells (mana is fundamental).
        """
        from ai.clock import card_clock_impact
        snap = snapshot_from_game(game, self.player_idx)

        # Rules constants used by this function.  Each is justified against
        # the scale set by `_score_spell`: spells typically score between
        # -5 (pass_threshold) and +15 (high-EV cast), so land scores must
        # live in a comparable range and land plays must generally outrank
        # spells of the current turn (mana is fundamental).  Every cost /
        # bonus below is derived from that shared scale or from game-rules
        # facts (timing: tapped lands cost 1 turn of mana availability;
        # color enabling unlocks 1 spell per new color; bounce-land loops
        # generate +1 land worth of mana per turn).
        # Land scoring constants imported from scoring_constants.
        # Local aliases for the color/fetch tier (different names in
        # the centralised module to avoid collision with downstream
        # callers).
        COLOR_ENABLES_SPELL = LAND_COLOR_ENABLES_SPELL
        NEW_COLOR_GENERIC = LAND_NEW_COLOR_GENERIC
        FETCH_FLEXIBILITY = LAND_FETCH_FLEXIBILITY

        ev = LAND_BASE_EV

        current_untapped = me.untapped_mana_capacity()
        hand_spells = [s for s in me.hand if not s.template.is_land]
        has_castable_spells = any(
            (s.template.cmc or 0) <= current_untapped + 1
            for s in hand_spells
        )
        has_one_drops = any((s.template.cmc or 0) <= 1 for s in hand_spells)

        # Amulet of Vigor family: a battlefield permanent with oracle pattern
        # "whenever a permanent you control enters tapped, untap it" makes
        # enters-tapped lands behave as untapped for mana-availability. Detection
        # mirrors engine/game_state.py:_apply_untap_on_enter_triggers so we don't
        # hardcode card names.
        has_untap_enabler = any(
            ('whenever' in (c.template.oracle_text or '').lower()
             and 'enters tapped' in (c.template.oracle_text or '').lower()
             and 'untap it' in (c.template.oracle_text or '').lower())
            for c in me.battlefield
        )

        # A pay-to-untap land (shock: untap_life_cost > 0) can enter
        # untapped for a small life cost, so for mana-availability it
        # behaves as an untapped colour source — do not apply the
        # tapped penalty to it. (The engine still enters it tapped
        # unless the life is actually paid; this is the AI valuing it
        # as the premium dual it is.)
        effectively_tapped = (land.template.enters_tapped
                              and not has_untap_enabler
                              and land.template.untap_life_cost == 0)
        if not effectively_tapped:
            ev += LAND_UNTAPPED_USEFUL if has_castable_spells else LAND_UNTAPPED_IDLE
        else:
            if has_castable_spells:
                if current_untapped == 0 and has_one_drops:
                    ev -= LAND_TAPPED_STALL
                else:
                    ev -= LAND_TAPPED_MINOR

        # Amulet + bounce-land mana loop: the bounce land returns a land, which
        # re-triggers the Amulet untap → net +1 mana/turn. Detect via oracle.
        if has_untap_enabler:
            land_oracle = (land.template.oracle_text or '').lower()
            is_bounce_land = (
                "return a land you control to its owner's hand" in land_oracle
                or "return an untapped land you control to its owner's hand" in land_oracle
            )
            if is_bounce_land:
                ev += BOUNCE_LAND_AMULET_MANA

        # High-CMC creature ramp priority: when a CMC 6+ creature is in hand
        # and this land brings us to casting threshold, rush the land.
        # (Primeval Titan, Cultivator Colossus, Reality Smasher, etc.)
        high_cmc_creature = next(
            (c for c in me.hand if c.template.is_creature and (c.template.cmc or 0) >= BIG_CREATURE_CMC_FLOOR),
            None)
        if high_cmc_creature:
            target_cmc = high_cmc_creature.template.cmc or BIG_CREATURE_CMC_FLOOR
            effective_mana_after = current_untapped + (1 if not effectively_tapped else 0)
            # Amulet doubles tapped-land mana: add +1 if we have enabler + tapped land
            if has_untap_enabler and land.template.enters_tapped:
                effective_mana_after += 1
            if effective_mana_after >= target_cmc:
                ev += RAMP_TO_BIG_NOW
            elif effective_mana_after >= target_cmc - 2:
                ev += RAMP_TO_BIG_SOON

        # New colors: enables spells we couldn't cast → direct clock impact
        existing_colors = set()
        for l in me.lands:
            existing_colors.update(l.template.produces_mana)
        # A fetchland's colour contribution is the set of colours it can
        # SEARCH FOR, not the mana it taps for (it taps for none, or for
        # {C}).  Both come off the printed text via `template.fetchland`.
        fetch_profile = land.template.fetchland
        is_fetch = fetch_profile is not None
        land_produces = (set(fetch_profile.colors) if is_fetch
                         else set(land.template.produces_mana))

        new_colors = land_produces - existing_colors
        # Gate the anticipatory color-diversity bonus by whether the hand
        # actually contains colored-cost spells. A pure-colorless hand
        # (classic Affinity: Mox Opal, Ornithopter, Cranial Plating) gets
        # no value from colored mana access — otherwise a rainbow land
        # like Spire of Industry out-scores the strictly better artifact
        # land purely on "might be useful later" potential.
        hand_needs_colors = any(
            (s.template.mana_cost.white + s.template.mana_cost.blue
             + s.template.mana_cost.black + s.template.mana_cost.red
             + s.template.mana_cost.green) > 0
            for s in me.hand if not s.template.is_land
        )
        if hand_needs_colors:
            ev += len(new_colors) * NEW_COLOR_GENERIC

        # Specific spell enablement: this land's colors unlock a spell in hand
        # Check me.hand (not just legal spells) so color-gated 1-drops count
        for spell in me.hand:
            if spell.template.is_land: continue
            mc = spell.template.mana_cost
            spell_colors = set()
            for code, attr in [("W","white"),("U","blue"),("B","black"),("R","red"),("G","green")]:
                if getattr(mc, attr, 0) > 0: spell_colors.add(code)
            missing_for_spell = spell_colors - existing_colors
            if missing_for_spell and missing_for_spell & land_produces:
                ev += COLOR_ENABLES_SPELL

        if is_fetch:
            ev += FETCH_FLEXIBILITY

        # Artifact-land synergy bonus. When the player's visible cards
        # carry artifact-scaling text, an artifact-typed land contributes
        # beyond its mana: it bumps Mox Opal's metalcraft count, adds a
        # point to Cranial Plating / Nettlecyst scaling, and lowers the
        # cost of Thought Monitor / Frogmite affinity discounts.
        #
        # Per-signal bonus is derived from "+1 power (or +1 mana) per
        # artifact × residency × mana_clock_impact × 20":
        #   1 power × ~4 residency turns × ~0.05 impact × 20 = ~4.0.
        # Using a single rules constant (SYNERGY_ARTIFACT_BONUS) keeps
        # the derivation traceable without per-card magic.
        from engine.cards import CardType
        if CardType.ARTIFACT in land.template.card_types:
            # E-2 (Phase L): count battlefield scaling cards only.
            # Hand-side scaling cards are scored separately when the AI
            # considers casting them; counting them here double-books
            # the same expected value. Mirrors PR-L1's symmetry —
            # artifact lands contribute marginal +1 to *active* scaling
            # effects, not to hand-side intent.
            synergy_signals = 0
            for c in me.battlefield:
                if c is land:
                    continue
                if c.template.has_artifact_synergy:
                    synergy_signals += 1
            if synergy_signals > 0:
                ev += synergy_signals * ARTIFACT_LAND_SYNERGY_BONUS

        # Landfall: each trigger ≈ ETB effect value (life, damage, ramp)
        landfall_count = sum(1 for c in me.battlefield
                             if 'landfall' in (c.template.oracle_text or '').lower())
        if landfall_count > 0:
            triggers = 2 if is_fetch else 1
            ev += landfall_count * triggers * LANDFALL_TRIGGER_VALUE

        # Tron land assembly bonus: detect via "Urza's" subtype (shared by all 3 pieces).
        # Completing the set unlocks {C}{C}{C} production — a huge mana jump.
        # Replaces previous flat +20/+8/+3 magic numbers with a principled
        # derivation: completed Tron = +4 mana/turn (7 colorless from the
        # three lands vs 3 mana from any three vanilla lands). Over the
        # remaining game (expected turns from combat_clock), that mana
        # advantage compounds at mana_clock_impact per point. Partial
        # progress is discounted by P(drawing the missing piece) using
        # actual library composition — no hardcoded probabilities.
        is_tron_piece = "Urza's" in (land.template.subtypes or [])
        if is_tron_piece:
            current_tron = [c for c in me.lands if "Urza's" in (c.template.subtypes or [])]
            # Count distinct Tron pieces (Tower / Mine / Power-Plant have unique subtypes)
            tron_types_present = {
                next((s for s in (c.template.subtypes or []) if s != "Urza's"), None)
                for c in current_tron
            }
            new_type = next((s for s in (land.template.subtypes or []) if s != "Urza's"), None)
            completing = new_type not in tron_types_present
            if completing:
                after_count = len(tron_types_present) + 1
                # Phase 1 refactor: Tron-assembly scaling weight sourced
                # from the LLM helper, cached per archetype.  Historical
                # 4.0 (= +4 mana / turn over 3 vanilla lands) lives in
                # the default-table fallback.
                # Expected remaining turns = time for the mana advantage to
                # compound. Use the slower of the two clocks (game ends when
                # someone dies). NO_CLOCK stalls → long game.
                from ai.clock import combat_clock, mana_clock_impact, NO_CLOCK
                my_c = combat_clock(snap.my_power, snap.opp_life,
                                     snap.my_evasion_power, snap.opp_toughness)
                opp_c = combat_clock(snap.opp_power, snap.my_life,
                                      snap.opp_evasion_power, snap.my_toughness)
                expected_turns = min(my_c, opp_c)
                if expected_turns >= NO_CLOCK:
                    expected_turns = MODERN_AVG_GAME_LENGTH  # rules constant: Modern avg game length
                expected_turns = max(GAME_HORIZON_MIN_TURNS, min(expected_turns, GAME_HORIZON_MAX_TRON))
                # Mana-clock impact gives value per point of mana advantage.
                mana_impact = mana_clock_impact(snap)
                _tron_w = _llm_weight(self.archetype, CTX_TRON_MANA_ADVANTAGE)
                completed_value = (_tron_w * expected_turns
                                   * mana_impact * CLOCK_IMPACT_LIFE_SCALING)
                # 20.0 scales mana_clock_impact (1/opp_life ~= 0.05) back to
                # board-eval units — same convention as creature_value().
                #
                # P(find missing piece(s) in remaining turns) from actual
                # library composition: count Tron pieces in library + tutors
                # (Sylvan Scrying, Expedition Map). No hardcoded magic.
                missing = TRON_PIECES_REQUIRED - after_count
                if missing == 0:
                    ev += completed_value
                else:
                    tron_sources = sum(
                        1 for c in me.library
                        if ("Urza's" in (c.template.subtypes or [])
                            or 'sylvan scrying' in (c.template.name or '').lower()
                            or 'expedition map' in (c.template.name or '').lower()))
                    lib_size = max(1, len(me.library))
                    # P(any given draw hits a piece/tutor)
                    p_hit = tron_sources / lib_size
                    # P(enough hits in expected_turns draws) — binomial,
                    # simplified to independence across draws.
                    p_assemble = 1.0 - (1.0 - p_hit) ** (expected_turns * missing)
                    ev += p_assemble * completed_value

        # Landfall deferral: cast landfall creature FIRST, then play land
        current_mana = me.untapped_mana_capacity() + me.mana_pool.total() + me._tron_mana_bonus()
        for spell in me.hand:
            if spell.template.is_land:
                continue
            oracle = (spell.template.oracle_text or '').lower()
            if 'landfall' not in oracle:
                continue
            if game.can_cast(self.player_idx, spell):
                ev -= LANDFALL_DEFERRAL_PENALTY  # defer land so creature resolves first
                break

        # ── Gameplan land-priority hook (Track H handoff, G finding 2) ──
        # ``land_priorities`` is per-deck DATA (decks/gameplans/*.json)
        # that previously reached only mulligan bottoming.  Consuming
        # it here lets a gameplan order engine-land sequencing in game
        # without any card names in code.  The scale keeps the term a
        # land-vs-land tiebreaker, not a land-vs-spell override.
        if self.goal_engine:
            declared = self.goal_engine.gameplan.land_priorities.get(
                land.name, 0.0)
            ev += declared * LAND_GAMEPLAN_PRIORITY_SCALE

        return ev

    def _cascade_graveyard_target(self) -> int:
        """Return the FILL_RESOURCE goal's `resource_target` for GY creatures.

        Used by the LE-A3 cascade patience gate to determine how many
        creatures must be in the graveyard before a cascade enabler is
        allowed to fire. Gameplan-declared — no magic numbers.

        Returns 0 when the deck has no FILL_RESOURCE goal targeting the
        graveyard, which disables the gate (non-reanimator cascade decks,
        or decks that use cascade for a non-graveyard payoff).
        """
        if not (self.goal_engine and self.goal_engine.gameplan):
            return 0
        from ai.gameplan import GoalType
        for goal in self.goal_engine.gameplan.goals:
            if (goal.goal_type == GoalType.FILL_RESOURCE
                    and goal.resource_zone == "graveyard"):
                return int(goal.resource_target or 0)
        return 0

    def _has_reanimation_path(self, game, me) -> bool:
        """True if the deck has an oracle-visible way to return
        creatures from graveyard to battlefield — required for the
        `cycle creature into GY = future reanimate target` bonus to
        fire (design §2.E).

        Scans the gameplan's cascade/reanimator declarations and the
        visible library/hand/battlefield for oracle text that returns
        creatures from graveyard.  No hardcoded card names.
        """
        # Cached per-turn result (recomputed each turn as hand/graveyard
        # changes).  Cheap enough to compute on demand if cache absent.
        if game is not None:
            turn_cache = getattr(self, '_reanimation_cache_turn', -1)
            cached_val = getattr(self, '_reanimation_cache_val', None)
            if turn_cache == game.turn_number and cached_val is not None:
                return cached_val

        # Gameplan-driven: cascade + prefer_cycling is the Living End
        # signature.  We accept this as authoritative when present.
        if self.goal_engine and self.goal_engine.gameplan:
            gp = self.goal_engine.gameplan
            if getattr(gp, 'prefer_cycling', False):
                self._reanimation_cache_turn = (
                    game.turn_number if game else -1)
                self._reanimation_cache_val = True
                return True

        # Oracle-driven: scan visible cards for "return ... from
        # graveyard ... to the battlefield" patterns.  Cards like
        # Living End, Unburial Rites, Persist, Goryo's Vengeance, and
        # creatures like Ephemerate-via-Persist all match.
        zones = [me.hand, me.battlefield]
        # Library visibility is a simplification — in real play we
        # know our deck.  DeckKnowledge provides it when initialised.
        if self._dk is not None:
            zones.append(me.library)
        for zone in zones:
            for c in zone:
                if self._oracle_is_reanimate(c.template.oracle_text):
                    self._reanimation_cache_turn = (
                        game.turn_number if game else -1)
                    self._reanimation_cache_val = True
                    return True

        self._reanimation_cache_turn = game.turn_number if game else -1
        self._reanimation_cache_val = False
        return False

    @staticmethod
    def _oracle_is_reanimate(oracle: str) -> bool:
        """True if oracle returns a creature from a graveyard to the
        battlefield (Living End, Unburial Rites, Persist, Goryo's, ...).
        Excludes "from your hand ... to the battlefield" by requiring
        'graveyard' to precede 'battlefield'. No card names."""
        o = (oracle or '').lower()
        if 'from' not in o or 'graveyard' not in o:
            return False
        if ('return' in o and 'battlefield' in o) or (
                'put' in o and 'battlefield' in o):
            gy_idx = o.find('graveyard')
            bf_idx = o.find('battlefield', gy_idx)
            return gy_idx >= 0 and bf_idx >= 0
        return False

    def _library_has_reanimate_payoff(self, me) -> bool:
        """True if the library still contains a reanimate payoff for a
        cascade to recover into. Distinct from `_has_reanimation_path`,
        which short-circuits True on the gameplan `prefer_cycling` flag
        regardless of library contents — the cascade patience gate must
        know whether the payoff is actually reachable in the deck, not
        merely that the deck is a reanimator archetype."""
        return any(
            self._oracle_is_reanimate(c.template.oracle_text)
            for c in me.library
        )

    def _score_cycling(self, card, snap, game, me, opp) -> float:
        """Score cycling using clock-derived values.

        Cycling = draw 1 card + put creature in GY (for Living End).
        Constants calibrated so cycling outscores creature-casting when
        the gameplan requires GY filling before cascade.
        """
        # EV scaling constants — see ai/scoring_constants.py for derivations.

        from ai.clock import card_clock_impact

        # Drawing a card: future clock change
        ev = card_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING  # scale to match spell scores

        # Cycling creatures into GY: Living End-style reanimation gameplan.
        # Design: docs/design/ev_correctness_overhaul.md §2.E — the
        # "creature in graveyard = future reanimation target" bonus fires
        # ONLY when the deck has a visible reanimation path.  A dead
        # creature in Boros Energy's graveyard is not equity.
        if card.template.is_creature and self._has_reanimation_path(game, me):
            power = card.template.power or 0
            # Creature in GY = future reanimation target
            ev += (CYCLING_GY_REANIMATE_BASE + power * CYCLING_GY_REANIMATE_PER_POWER)

        # Cycling cost: cheaper = better tempo.
        # Phase 1 refactor: free-cycling weight sourced from the LLM
        # helper (historical 2.0).
        cost_data = card.template.cycling_cost_data
        if cost_data:
            if cost_data.get('life', 0) > 0:
                ev += _llm_weight(self.archetype, CTX_CYCLING_FREE_COST_BONUS)
            elif cost_data.get('mana', 0) <= 1:
                ev += CYCLING_CHEAP_COST_BONUS  # cheap cycling

        # Cascade in hand: filling GY is urgent — MUST cycle before cascade.
        # Phase 1 refactor: scaling weights sourced from the LLM helper,
        # cached per archetype.  Historical values (8.0, 6.0, 10.0) live
        # in the default-table fallback.
        has_cascade = any(getattr(c.template, 'is_cascade', False) for c in me.hand
                         if not c.template.is_land)
        if has_cascade:
            ev += _llm_weight(self.archetype, CTX_CYCLING_CASCADE_BOOST)
            # Count creatures already in GY — less urgency if GY is full
            from ai.predicates import count_gy_creatures
            gy_creatures = count_gy_creatures(me.graveyard)
            if gy_creatures < CYCLING_GY_URGENCY_FLOOR:
                ev += _llm_weight(self.archetype, CTX_CYCLING_GY_URGENCY)

        # Gameplan prefer_cycling: massive boost (Living End, etc.)
        if self.goal_engine:
            current_goal = self.goal_engine.current_goal
            if current_goal and getattr(current_goal, 'prefer_cycling', False):
                ev += _llm_weight(self.archetype, CTX_CYCLING_GAMEPLAN_BOOST)

        # Bundle 3 A3 — same holdback gate as _score_spell. Cycling
        # taps lands too; it must respect held instant-speed interaction.
        cost_data = card.template.cycling_cost_data
        cycling_mana_cost = cost_data.get('mana', 0) if cost_data else 0
        ev += self._holdback_penalty(
            me, opp, snap, cost=cycling_mana_cost,
            exclude_instance_id=card.instance_id, game=game)

        return ev

    def _score_suspend(self, card, snap, game, me, opp) -> float:
        """EV for paying a card's suspend cost (CR 702.62a).

        Suspend is a sorcery-speed special action: pay the suspend
        cost, exile the card with N time counters; remove one each
        upkeep; cast for free when the last is removed.

        EV model — every term derived from existing primitives:

          payoff = expected_gy_creatures_at_resolution
                   × per_creature_reanimation_ev
                   × P(survive_to_resolution_turn)
          waste  = mana_clock_impact(snap) × suspend_mana_cost
          EV     = payoff - waste

        Where:
          - per_creature_reanimation_ev reuses
            CYCLING_GY_REANIMATE_BASE + AVG_CREATURE_POWER
            × CYCLING_GY_REANIMATE_PER_POWER (the same per-creature
            equity the cycling scorer assigns to graveyard-fill).
          - expected_gy_at_resolution = current GY creatures plus
            min(hand_cyclers, N counters) — every upkeep before
            resolution is an opportunity to cycle one more creature.
          - P(survive_to_resolution_turn) =
            1 - exp(-max(0, opp_clock - resolution_offset)
                    / PERMANENT_VALUE_WINDOW).  Reuses the same
            exponential clock-slack curve as
            ``EVSnapshot.urgency_factor`` so suspend's clock
            sensitivity matches the rest of the deferred-permanent
            scoring layer.  Collapses to 0 at opp_clock<=resolution
            (death before payoff) and asymptotes to 1 at long
            clocks (Tron-style empty boards) — no hard cutoff, no
            sentinel.

        Two gates make suspend defer to faster lines:

          1. A faster castable finisher route is in hand — cascade
             enabler / storm finisher / tutor that is *castable in
             current mana state*.  An uncastable cascade card (e.g.
             {1}{B}{R} cascade with a BUG mana base, or a 3-mana
             cascade on T2 with 1 land) does not constitute a
             faster route — suspend is the parallel plan and scores
             on its own merit.  The diagnostic at
             ``docs/diagnostics/2026-05-10_living_end_5pct_root_cause.md``
             documents how the prior unconditional check zeroed
             suspend EV in 92.7% of enumerations.
          2. opp_clock < resolution_offset — the opponent kills us
             before suspend resolves.  Replaced the prior hard
             ``return 0.0`` with the survival-probability discount
             above; collapses to ~0 at opp_clock<=1 (lethal next
             turn) but preserves an EV gradient between 1 and
             resolution_offset where the suspend may still pay off
             if the opponent stumbles or we stabilise.

        Class size: every Modern suspend card — Living End, Ancestral
        Vision, Crashing Footfalls, Restore Balance, Wheel of Fate,
        Lotus Bloom, Greater Gargadon, future printings.  No card
        names; reads SUSPEND keyword + parsed clause.
        """
        import math
        from ai.clock import mana_clock_impact
        from engine.cards import Keyword as _Kw
        from engine.cast_manager import CastManager

        # Gate 1: a CASTABLE faster route to the same finisher in
        # hand?
        #
        # Suspend resolves on T+N+1.  If a cascade enabler / storm
        # finisher / tutor is in hand AND castable in current mana
        # state, the same payoff is reachable in 1-2 turns instead
        # — suspend is strictly slower and should defer.
        #
        # The castability check is critical: a cascade card stuck
        # behind color requirements (e.g. {1}{B}{R} with no red
        # source) or behind a CMC the mana base cannot yet pay is
        # not a "faster route"; it is a parallel plan that competes
        # with suspend on resource alignment, not on speed.
        #
        # Note: this gate intentionally does NOT include the
        # cantrip-dig branch from _payoff_reachable_this_turn.  A
        # cycler draws a card; it does not by itself cast the
        # finisher.  For suspend, the semantically correct gate is
        # "fast castable finisher present", not "any payoff route
        # present".
        for c in me.hand:
            if c is card:
                continue
            kws = c.template.keywords or set()
            tags_c = c.template.tags or set()
            is_storm = _Kw.STORM in kws
            is_tutor = 'tutor' in tags_c
            is_cascade = getattr(c.template, 'is_cascade', False)
            if not (is_storm or is_tutor or is_cascade):
                continue
            # Castability gate — only an *actually castable* faster
            # route blocks suspend.  Uses engine's canonical
            # can_cast helper so the check stays oracle-driven
            # (color sources, generic mana, alt-cost paths) and no
            # card-specific mana-cost logic leaks into ai/.
            if game.can_cast(self.player_idx, c):
                return 0.0

        parsed = CastManager._parse_suspend_clause(card.template)
        if parsed is None:
            return 0.0
        counters, cost = parsed

        # Payoff: expected GY creatures × per-creature equity.
        gy_creatures = sum(1 for c in me.graveyard
                           if c.template.is_creature)
        hand_cyclers = sum(1 for c in me.hand
                           if c.template.is_creature
                           and game.can_cycle(self.player_idx, c))
        expected_gy = gy_creatures + min(hand_cyclers, counters)

        per_creature_ev = (CYCLING_GY_REANIMATE_BASE
                           + AVG_CREATURE_POWER
                           * CYCLING_GY_REANIMATE_PER_POWER)
        payoff_ev = expected_gy * per_creature_ev

        # Gate 2 (clock-derived gradient): time-to-resolution vs
        # opponent clock.  Resolution arrives at T+resolution_offset
        # = counters + 1 (each upkeep removes one counter; the
        # cast happens on the upkeep when the last is removed —
        # that's N upkeeps + the cast turn).
        #
        # P(survive_to_resolution_turn) is a two-stage discount,
        # both derived from existing clock primitives:
        #
        #   1. ``snap.urgency_factor`` — P(we get to act at all)
        #      across the opp_clock - 1 horizon.  Same primitive
        #      that discounts other deferred permanents (Goblin
        #      Bombardment, planeswalker tick value etc.).
        #   2. Additional exponential decay for the extra turns
        #      needed past the urgency horizon to reach
        #      resolution_offset.  Reuses
        #      ``PERMANENT_VALUE_WINDOW`` (the same rules constant
        #      that urgency_factor and ai.sideboard_solver use).
        #
        # At opp_clock <= 1 (lethal next turn): urgency_factor = 0,
        # survival = 0 — the regression case from
        # docs/diagnostics/2026-05-10_living_end_5pct_root_cause.md.
        # At opp_clock >> resolution_offset (Tron-style stall):
        # survival asymptotes to 1.0.  In between (e.g. opp_clock
        # 2, resolution_offset 4) survival is small but non-zero,
        # preserving the EV gradient the prior hard cutoff erased.
        resolution_offset = counters + 1
        opp_clock = getattr(snap, 'opp_clock', None)
        PERMANENT_VALUE_WINDOW = 2.0  # magic-allow: shared rules constant
                                      # (see ai/ev_evaluator.urgency_factor,
                                      # ai/sideboard_solver) — deferred-value
                                      # residency horizon.
        if opp_clock is not None:
            urgency = getattr(snap, 'urgency_factor', 1.0)
            horizon_gap = max(0.0, resolution_offset - opp_clock)
            horizon_decay = math.exp(-horizon_gap / PERMANENT_VALUE_WINDOW)
            survival = urgency * horizon_decay
            payoff_ev = payoff_ev * survival

        # Waste: mana committed produces no this-turn signal — same
        # shape as ev_evaluator._compute_exposure_cost.
        waste = (cost.cmc or 0) * mana_clock_impact(snap)

        return payoff_ev - waste

    # ═══════════════════════════════════════════════════════════
    # COMBAT — reuse existing CombatPlanner
    # ═══════════════════════════════════════════════════════════

    def decide_attackers(self, game) -> List["CardInstance"]:
        """Decide which creatures to attack with."""
        from ai.turn_planner import extract_virtual_board
        from engine.cards import Keyword

        valid = game.get_valid_attackers(self.player_idx)
        if not valid:
            return []

        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]

        # Pre-combat pump (Psychic Frog etc.)
        from engine.oracle_clauses import any_ability_with
        for creature in valid:
            oracle = (creature.template.oracle_text or "").lower()
            # Only fire when the discard cost's OWN ability pumps this
            # creature — i.e. a single ability paragraph carries both
            # "discard" and "+1/+1" ("Discard a card: put a +1/+1 counter
            # on this creature"). A whole-oracle "+1/+1" substring test
            # false-fires on a discard-cost creature whose +1/+1 lives in
            # an unrelated ability (Hardened Academic's discard grants
            # lifelink; its +1/+1 is a separate graveyard trigger),
            # fabricating counters it can never actually make.
            if (getattr(creature.template, 'has_discard_effect', False)
                    and any_ability_with(oracle, 'discard', '+1/+1')):
                prof = self.profile
                # Smart discard: protect removal/counters, discard excess lands/dupes/uncastable
                hand_lands = [c for c in me.hand if c.template.is_land]
                hand_spells = [c for c in me.hand if not c.template.is_land]
                board_names = {c.name for c in me.battlefield}
                protect_tags = {'removal', 'counterspell'}
                discardable = []

                # 1. Excess lands (keep 1 for next land drop)
                if len(hand_lands) >= 2 and len(me.lands) >= PUMP_DISCARD_LAND_FLOOR:
                    discardable.extend(hand_lands[1:])
                elif len(hand_lands) >= 1 and len(me.lands) >= prof.pump_extra_lands_threshold:
                    discardable.extend(hand_lands)

                # 2. Duplicates of cards already on battlefield
                for c in hand_spells:
                    tags = getattr(c.template, 'tags', set())
                    if c.name in board_names and not (tags & protect_tags):
                        if c not in discardable:
                            discardable.append(c)

                # 3. High-CMC spells we can't cast soon
                if len(hand_spells) >= PUMP_DISCARD_SPELL_GLUT:
                    for c in hand_spells:
                        tags = getattr(c.template, 'tags', set())
                        if tags & protect_tags:
                            continue
                        if (c.template.cmc or 0) > len(me.lands) + prof.pump_uncastable_cmc_buffer:
                            if c not in discardable:
                                discardable.append(c)

                # 4. When pumped and opp is low, also discard cheap cantrips
                if getattr(creature, 'plus_counters', 0) >= 1 and opp.life <= prof.burn_low_life_threshold + 2:
                    for c in sorted(hand_spells, key=lambda x: x.template.cmc or 0):
                        tags = getattr(c.template, 'tags', set())
                        if tags & protect_tags:
                            continue
                        if ('cantrip' in tags or 'draw' in tags) and c not in discardable:
                            discardable.append(c)
                            break

                pumps = min(len(discardable), prof.pump_max_discards)
                for i in range(pumps):
                    card_to_discard = discardable[i]
                    if card_to_discard in me.hand:
                        me.hand.remove(card_to_discard)
                        card_to_discard.zone = "graveyard"
                        me.graveyard.append(card_to_discard)
                        # Permanent +1/+1 counters, not temp mods
                        if hasattr(creature, 'plus_counters'):
                            creature.add_plus_counters(1, game)
                        else:
                            creature.temp_power_mod += 1
                            creature.temp_toughness_mod += 1
                break

        # Lethal: alpha strike — exclude 0-power non-trigger creatures (deal 0 damage).
        def _has_combat_value(c):
            """True if attacking with this creature produces board value.
            Covers: positive power, damage-on-hit triggers, and any
            on-attack triggers (battle cry, tapping, anthem effects, etc.).
            Fully oracle-driven — no card names.
            """
            if (c.power or 0) > 0:
                return True
            # Damage-on-hit triggers (Ragavan, etc.) — typed field from oracle_parser.
            if c.template.has_combat_damage_player_trigger:
                return True
            # On-attack triggers — typed field set at DB load time by
            # oracle_parser.parse_has_attack_trigger.
            if getattr(c.template, 'has_attack_trigger', False):
                return True
            return False
        total_power = sum(c.power for c in valid if (c.power or 0) > 0)
        if total_power >= opp.life:
            # On-board lethal if unblocked: send what is NEEDED. A creature
            # whose non-combat worth (`ai.clock.noncombat_opportunity_cost`
            # — mana production, unbounded-engine membership, abilities,
            # equipment ceiling; life-point units) exceeds the damage it
            # adds (its power, the same units) stays home when the rest
            # still reach lethal — the same rule the combat planner's
            # lethal shortcut applies, so both paths agree.
            from ai.clock import noncombat_opportunity_cost
            from ai.ev_evaluator import snapshot_from_game
            _snap_lethal = snapshot_from_game(game, self.player_idx)
            kept = [c for c in valid if _has_combat_value(c)]
            worth = {c.instance_id: noncombat_opportunity_cost(c, me, _snap_lethal)
                     for c in kept}
            for c in sorted(kept, key=lambda c: -worth[c.instance_id]):
                if worth[c.instance_id] > (c.power or 0) and (
                        sum((k.power or 0) for k in kept) - (c.power or 0)
                        >= opp.life):
                    kept.remove(c)
            return kept

        # No blockers = free damage. Always attack into an empty board.
        # Still exclude 0-power non-trigger creatures — tapping them is pure waste.
        opp_blockers = game.get_valid_blockers(1 - self.player_idx)
        if not opp_blockers and valid:
            return [c for c in valid if _has_combat_value(c)]

        # ── Free attackers: creatures that survive any block always attack ──
        # A creature is "free" if no untapped blocker has enough power to kill it,
        # OR it has evasion that makes it unblockable in practice.
        # Fix 1: block_ratio upper-bound removed — any blocker with power >= our
        #         toughness is a real threat regardless of how oversized it is.
        # Fix 2: 0-power creatures with no combat triggers are never free attackers —
        #         they deal 0 damage even unblocked and only waste a tap.
        free_attackers = []
        non_free = []
        for c in valid:
            # A creature with a triggered combat-damage ability has value even at
            # 0 power (e.g. future designs).  Pure 0-power creatures with no such
            # trigger are excluded from free_attackers.
            # Typed fields replace runtime oracle checks:
            #   has_combat_damage_player_trigger — on-hit trigger (Ragavan class)
            #   has_attack_trigger               — on-attack trigger (all forms)
            has_combat_trigger = (
                c.template.has_combat_damage_player_trigger
                or getattr(c.template, 'has_attack_trigger', False)
            )
            deals_damage = (c.power or 0) > 0 or has_combat_trigger

            # True if any untapped blocker can kill this attacker (power >= toughness).
            # No upper-bound filter: a 4/4 blocking a 1/2 is a real threat.
            can_die_to_block = any(
                (b.power or 0) >= (c.toughness or 0)
                for b in opp_blockers
                if not b.tapped
            )
            # Evasion: flying attacker with no flying/reach defenders.
            # Reach check uses oracle text for generality (no keyword enum dependency).
            is_evasive = (
                Keyword.FLYING in c.keywords and not any(
                    Keyword.FLYING in b.keywords or Keyword.REACH in b.keywords
                    for b in opp_blockers if not b.tapped)
            )
            # Phase 3 veto retirement: evasion removes combat risk regardless
            # of power.  A creature that is genuinely unblockable (is_evasive)
            # will not die to any legal block — its "free" status comes from
            # evasion, not from the damage it deals.  Removing the deals_damage
            # gate allows 0-power flyers to be scored by the planner rather
            # than categorically excluded.
            #
            # Non-evasive creatures still require deals_damage to join the free
            # pool: without damage they contribute nothing to the clock, and a
            # tap is a real cost if the creature has blocking/ability value.
            if is_evasive or (deals_damage and not can_die_to_block):
                free_attackers.append(c)
            elif deals_damage:
                non_free.append(c)
            else:
                # 0-power, 0-trigger, non-evasive, not safe from blocks:
                # attack EV ≈ 0, leave out of the pool entirely.
                non_free.append(c)

        # If ALL our creatures are free attackers, just send them all
        if not non_free and free_attackers:
            return free_attackers

        # ── Determine opponent archetype for anti-combo aggression ──
        opp_deck_name = getattr(opp, 'deck_name', '')
        opp_archetype = 'midrange'  # default
        try:
            from ai.gameplan import get_gameplan
            opp_gp = get_gameplan(opp_deck_name)
            if opp_gp:
                opp_archetype = opp_gp.archetype
        except Exception:
            pass

        # ── Racing rule: when opp life is within 2x our board power, race ──
        # Also account for opponent's tapped state: if most of their creatures are
        # tapped they can only block with untapped creatures — effectively less defence.
        opp_untapped_blockers = [c for c in opp.creatures if not c.tapped]
        opp_untapped_block_power = sum(c.power or 0 for c in opp_untapped_blockers)
        # Effective damage we can deal: total power minus what the untapped wall absorbs
        effective_damage = max(0, total_power - opp_untapped_block_power)
        is_racing = (
            total_power > 0 and (
                opp.life <= 2 * total_power          # standard race
                or opp.life <= effective_damage * 2  # opponent mostly tapped
            )
        )
        # Desperation: we're low on life and going to lose anyway — maximise damage
        is_desperate = me.life <= DESPERATION_LIFE_FLOOR and total_power > 0 and opp.life > 0

        # ── Anti-combo: vs spell-based decks, creature attacks are always right ──
        from ai.strategy_profile import get_profile as _get_opp_profile
        opp_is_spell_deck = _get_opp_profile(opp_archetype).has_combo_chain

        # CombatPlanner
        try:
            vboard = extract_virtual_board(game, self.player_idx)
            attack_plan, score_delta = self.combat_planner.plan_attack(vboard)

            threshold = self.profile.attack_threshold
            # When opponent is low, attack more aggressively to close the game.
            # Non-aggressive profiles override aggro_closing_threshold_reduction
            # to 0.0, so the subtraction is a no-op for them — no archetype
            # gate needed at the call site.
            if opp.life <= self.profile.burn_low_life_threshold:
                threshold -= self.profile.aggro_closing_threshold_reduction

            # Post-payoff aggression: when the goal layer has advanced to
            # PUSH_DAMAGE the deck has declared "the army on the table is
            # how I win" — the gameplan-final goal of every combo /
            # cascade / reanimator shell.  Loosen the attack-EV threshold
            # so a borderline-trade combat still fires.  Generalises the
            # legacy ``aggression_boost_turns`` flag, which only fired on
            # Living End resolution and left Goryo's Vengeance, Through
            # the Breach, Crashing Footfalls, and any future
            # cascade-into-army payoff stranded on a default threshold
            # that the trade-down penalty can sink them under.
            #
            # Either signal — engine-set boost flag OR goal-layer
            # declared — fires the same reduction; they're alternative
            # paths to the same post-payoff state.  P1-3.
            post_payoff_active = (
                getattr(me, 'aggression_boost_turns', 0) > 0
                or self._is_push_damage_goal()
            )
            if post_payoff_active:
                threshold -= ATTACK_THRESHOLD_REDUCTION_AGGRESSION

            # Racing: when we can kill in ~2 swings, be aggressive
            if is_racing:
                threshold -= ATTACK_THRESHOLD_REDUCTION_AGGRESSION

            # Anti-combo: opponent won't block with creatures, so attacks are free
            if opp_is_spell_deck:
                threshold -= ATTACK_THRESHOLD_REDUCTION_ANTI_COMBO

            # Bonus EV for combat damage / attack triggers the planner doesn't model
            trigger_bonus = 0.0
            if attack_plan:
                for vc in attack_plan:
                    # Typed field on VirtualCreature — replaces dead getattr(vc, 'oracle', None) check.
                    if vc.has_combat_damage_player_trigger:
                        trigger_bonus += COMBAT_TRIGGER_DAMAGE_BONUS  # Ragavan: Treasure + exile ≈ 1.5 EV
                    # Energy-on-attack trigger (Guide of Souls) — different mechanic,
                    # no typed field in batch-5 schema; left for a future batch.
                    # Note: c_oracle was always '' on VirtualCreature (no oracle attr),
                    # so this bonus was dead code; it remains a no-op until migrated.

            if attack_plan and (score_delta + trigger_bonus) > threshold:
                attack_ids = {vc.instance_id for vc in attack_plan}
                planner_picks = [c for c in valid if c.instance_id in attack_ids]
                # Always include free attackers even if planner didn't pick them
                free_ids = {c.instance_id for c in free_attackers}
                for c in free_attackers:
                    if c.instance_id not in attack_ids:
                        planner_picks.append(c)
                return planner_picks
        except Exception:
            pass

        # Fallback: always send free attackers + creatures that can trade favorably
        safe = list(free_attackers)
        for c in non_free:
            has_dmg_trigger = c.template.has_combat_damage_player_trigger
            has_atk_trigger = getattr(c.template, 'has_attack_trigger', False)
            if has_dmg_trigger and (c.power or 0) > 0:
                # e.g. Ragavan: attack if our power kills their best blocker
                # (even trade gains the on-hit trigger).
                killable = [b for b in opp_blockers if (c.power or 0) >= (b.toughness or 0)]
                if killable:
                    safe.append(c)
            elif has_atk_trigger and not has_dmg_trigger:
                # On-attack trigger fires on declaration — value is delivered
                # before blockers are chosen, so even a creature that dies in
                # combat still fires.  Use opportunity_cost to gate: only send
                # the creature if its ongoing board value is below the trigger-
                # value threshold (ATTACK_TRIGGER_OC_MAX).  This replaces the
                # old "power > 0 AND has_combat_damage_player_trigger" veto
                # which categorically excluded every 0-power trigger source.
                from ai.ev_evaluator import snapshot_from_game
                from ai.clock import opportunity_cost
                _snap = snapshot_from_game(game, self.player_idx)
                _oc = opportunity_cost(c, me, _snap)
                if _oc < ATTACK_TRIGGER_OC_MAX:
                    safe.append(c)

        # If racing, desperate, or vs combo, send everything even if risky.
        # Still exclude 0-power non-trigger creatures — they add no damage.
        if (is_racing or is_desperate or opp_is_spell_deck) and valid:
            # "Everything" still prices what each body gives up: a creature
            # whose NON-combat worth (`ai.clock.noncombat_opportunity_cost`
            # — mana production, unbounded-engine membership, abilities,
            # equipment ceiling; life-point units) exceeds the damage it
            # would add (its power, the same units) stays home. A vanilla
            # body has no such worth and is sent exactly as before.
            from ai.clock import noncombat_opportunity_cost
            from ai.ev_evaluator import snapshot_from_game
            _snap_all_in = snapshot_from_game(game, self.player_idx)
            return [c for c in valid
                    if _has_combat_value(c)
                    and (c.power or 0) >= noncombat_opportunity_cost(
                        c, me, _snap_all_in)]

        return safe if safe else []

    def _is_push_damage_goal(self) -> bool:
        """True iff the GoalEngine has advanced to ``PUSH_DAMAGE``.

        ``PUSH_DAMAGE`` is the deck-declared post-payoff phase in the
        gameplan JSON — every combo / cascade / reanimator shell ends
        on this goal once its win condition has been deployed.  When
        the goal layer has reached it, the deck is in "win with the
        army on the table" mode and combat thresholds should reflect
        that priority.

        Generic by construction: the goal lives in
        ``decks/gameplans/*.json``; no card-name or deck-name lookup.
        Returns False when the AI has no goal engine (decks without a
        gameplan JSON) or when the engine is on an earlier goal.
        """
        # Local import to avoid a module-level dependency on
        # gameplan.GoalType (kept consistent with other call sites
        # in this file that import lazily).
        from ai.gameplan import GoalType
        if self.goal_engine is None:
            return False
        try:
            return self.goal_engine.current_goal.goal_type == GoalType.PUSH_DAMAGE
        except Exception:
            return False

    def _two_turn_lethal(self, game, me, opp, attackers) -> bool:
        # incoming this turn + opp's uninvolved creatures that can swing next turn
        incoming = sum(a.power or 0 for a in attackers)
        attacking_ids = {a.instance_id for a in attackers}
        opp_next = sum(
            (c.power or 0) for c in opp.creatures
            if c.instance_id not in attacking_ids
            and not getattr(c, 'summoning_sick', False)
        )
        return incoming + opp_next >= me.life

    def _attacker_equipment_bonus(self, game, opp, attacker) -> int:
        """Sum of +power on `attacker` that would persist after a chump —
        the plating rebinds, the Construct respawns with the same clause.

        Covers two sources:
          (1) Equipment / aura bonuses attached to the attacker
              ('equipped/enchanted creature gets +X/+Y' on the attached
              permanent). Handles 'for each <qualifier>' scaling.
          (2) Intrinsic scaling on the attacker's own oracle
              ('+X/+Y for each artifact/creature/land you control') — the
              Urza's Saga Construct Token pattern and similar.
        """
        bonus = 0

        def _scaled(base_power: int, oracle: str) -> int:
            scale_match = re.search(
                r'for each (artifact|creature|land|card)', oracle
            )
            if not scale_match:
                return base_power
            kind = scale_match.group(1)
            if kind == 'artifact':
                count = sum(
                    1 for c in opp.battlefield
                    if 'artifact' in str(c.template.card_types).lower()
                )
            elif kind == 'creature':
                count = len(opp.creatures)
            elif kind == 'land':
                count = len(
                    [c for c in opp.battlefield if c.template.is_land]
                )
            else:  # 'card' — count nonland permanents as a proxy
                count = len(opp.battlefield)
            return base_power * count

        # (1) Equipment / aura attached bonuses
        attached_ids = set()
        for tag in attacker.instance_tags:
            if not tag.startswith('equipped_'):
                continue
            tail = tag.split('_', 1)[1]
            if tail.isdigit():
                attached_ids.add(int(tail))
        for perm in opp.battlefield:
            if perm.instance_id not in attached_ids:
                continue
            oracle = (perm.template.oracle_text or '').lower()
            m = _EQUIP_BONUS_RE.search(oracle)
            if not m:
                continue
            bonus += _scaled(int(m.group(2)), oracle)

        # (2) Intrinsic scaling on the attacker's own oracle. Mirrors the
        # engine's detection in cards.py::_dynamic_base_power.
        a_oracle = (attacker.template.oracle_text or '').lower()
        m2 = re.search(
            r'\+(\d+)/\+\d+\s+for\s+each\s+(artifact|creature|land|card)\s+you\s+control',
            a_oracle,
        )
        if m2:
            bonus += _scaled(int(m2.group(1)), a_oracle)

        return bonus

    def _is_protected_piece(self, card) -> bool:
        """RC-4: card should not be thrown away as a chump unless it also
        kills the attacker or survival requires it.

        Categories (all oracle/tag-driven — no card-name lookups):
          - Planeswalkers — losing them surrenders loyalty abilities.
          - Creatures with the escape mechanic ('escape—' em-dash) —
            expensive to recur; represent long-term value.
          - Attack-trigger sources ('whenever this creature attacks',
            or 'whenever <name> attacks') — offensive value > defence.
        """
        from engine.cards import CardType
        t = card.template
        if CardType.PLANESWALKER in t.card_types:
            return True
        # Escape creatures are expensive to recur — typed field replaces em-dash oracle check.
        if t.escape_cost is not None:
            return True
        if getattr(t, 'has_attack_trigger', False):
            return True
        return False

    def _racing_to_win(self, game, me, opp, attackers) -> bool:
        """RC-5: True iff racing strictly beats blocking.

        All three conditions must hold:
          (a) we survive this combat unblocked (incoming < my life),
          (b) we have offensive power on-board,
          (c) my clock-to-kill (opp.life / my on-board power) is no worse
              than opp's clock-to-kill AFTER this combat (my post-combat
              life / opp's total next-turn power).

        Conservative: we use raw power and ignore burn/pump in hand. If
        the clocks are equal or we are faster, racing is preferred.
        """
        incoming = sum(a.power or 0 for a in attackers)
        if incoming >= me.life:
            return False  # cannot race through lethal
        my_on_board_power = sum((c.power or 0) for c in me.creatures)
        if my_on_board_power <= 0:
            return False
        attacking_ids = {a.instance_id for a in attackers}
        opp_on_board_power_after = sum(
            (c.power or 0) for c in opp.creatures
            if c.instance_id not in attacking_ids
        ) + sum((a.power or 0) for a in attackers)
        if opp_on_board_power_after <= 0:
            return True  # opp has no follow-up threat — freely race
        my_clock = opp.life / max(my_on_board_power, 1)
        my_life_after = me.life - incoming
        opp_clock = my_life_after / max(opp_on_board_power_after, 1)
        return my_clock <= opp_clock

    def _equipment_breakable(self, game, me) -> bool:
        """True iff we can plausibly remove or reset the equipment next turn.

        Checks `me.hand` for:
          - mass removal (tag 'wrath' / 'board_wipe')
          - artifact/enchantment destruction (tag 'removal' AND oracle
            destroys target artifact / enchantment / nonland permanent)
        """
        for card in me.hand:
            tags = getattr(card.template, 'tags', None) or set()
            if 'wrath' in tags or 'board_wipe' in tags:
                return True
            if 'removal' in tags and (
                card.template.can_destroy_artifact
                or card.template.can_destroy_enchantment
                or card.template.can_destroy_nonland_permanent
            ):
                return True
        return False

    def _score_block_lifespan_delta(
        self, game, attacker, blocker, my_life: int,
        my_power: int, opp_power: int,
    ) -> float:
        """Lifespan-delta score for a single (attacker, blocker) pair.

        Composes ``ai.clock.score_block_assignment`` for both the
        block and no-block post-states; returns the delta.

        Single formula — no chump / trade / favorable-trade enum.
        Caller picks the (attacker, blocker) pair with the highest
        delta.  Positive ⇒ block helps; non-positive ⇒ block is wasted.
        """
        from ai.clock import score_block_assignment
        from ai.ev_evaluator import snapshot_from_game
        from engine.cards import Keyword

        snap = snapshot_from_game(game, self.player_idx)

        a_pow = attacker.power or 0
        a_tou = attacker.toughness or 0
        b_pow = blocker.power or 0
        b_tou = blocker.toughness or 0

        b_kills_attacker = (b_pow >= a_tou) or (
            Keyword.DEATHTOUCH in blocker.keywords
        )
        a_kills_blocker = (a_pow >= b_tou) or (
            Keyword.DEATHTOUCH in attacker.keywords
        )

        # Post-block state — trample lets excess punch through.
        has_trample = Keyword.TRAMPLE in attacker.keywords
        damage_through = max(0, a_pow - b_tou) if has_trample else 0

        my_life_after_block = my_life - damage_through
        opp_power_after_block = opp_power - (a_pow if b_kills_attacker else 0)
        my_power_after_block = my_power - (b_pow if a_kills_blocker else 0)
        if a_kills_blocker:
            # What the dead blocker gives up BEYOND its power — mana
            # production, unbounded-engine membership, activated
            # abilities, equipment ceiling — priced by the one owner of
            # that question (`ai.clock.noncombat_opportunity_cost`, value
            # units at the life-point scale) and charged to the block
            # post-state as virtual life, which `life_as_resource` already
            # converts to survival turns. Its power is charged through
            # `my_power_after_block` above; nothing is counted twice.
            from ai.clock import noncombat_opportunity_cost
            my_life_after_block -= noncombat_opportunity_cost(
                blocker, game.players[self.player_idx], snap)

        my_life_after_no_block = my_life - a_pow
        opp_power_after_no_block = opp_power
        my_power_after_no_block = my_power

        block = score_block_assignment(
            snap,
            my_life_after=my_life_after_block,
            opp_power_after=opp_power_after_block,
            my_power_after=my_power_after_block,
        )
        no_block = score_block_assignment(
            snap,
            my_life_after=my_life_after_no_block,
            opp_power_after=opp_power_after_no_block,
            my_power_after=my_power_after_no_block,
        )
        return block - no_block

    def _log_block_assignments(self, game, blocks, id_to_attacker,
                                id_to_blocker, my_power_total,
                                opp_power_total, tag: str) -> None:
        """Shared logging for both the coverage/emergency and
        optimization/normal block-assignment passes below. ``tag`` is
        ``"BLOCK-EMERGENCY"`` or ``"BLOCK"`` — the replay pipeline
        (``build_replay.py``, per CLAUDE.md) parses both markers.
        """
        for atk_id, blk_ids in blocks.items():
            atk = id_to_attacker.get(atk_id)
            for blk_id in blk_ids:
                blk = id_to_blocker.get(blk_id)
                if atk and blk:
                    a_pow = atk.power or 0
                    b_pow = blk.power or 0
                    b_tou = blk.toughness or 0
                    a_tou = atk.toughness or 0
                    delta = self._score_block_lifespan_delta(
                        game, atk, blk,
                        my_life=game.players[self.player_idx].life,
                        my_power=my_power_total,
                        opp_power=opp_power_total,
                    )
                    game.log.append(
                        f"T{game.display_turn} P{self.player_idx+1}: "                        f"  [{tag}] {blk.name} ({b_pow}/{b_tou}) "                        f"blocks {atk.name} ({a_pow}/{a_tou}) — "                        f"lifespan_delta={delta:+.2f}"
                    )

    def decide_blockers(self, game, attackers) -> Dict[int, List[int]]:
        """Decide blocking assignments.

        Phase 2b (docs/design/rules-foundation-sweep-tracker.md):
        joint two-pass assignment, replacing the old greedy
        per-attacker loop whose ``my_life_now`` recomputation
        pessimistically assumed every OTHER still-undecided attacker
        already dealt its full damage — driving both the "block" and
        "no-block" hypothetical post-states to the same -100
        (already-dead) floor and producing a delta of exactly 0 for
        every candidate. That silently emptied ``emergency_blocks``
        and fell through, ungated, into the non-emergency path, which
        then spent BOTH available blockers double-blocking one
        attacker for a "clean kill" while leaving a second, equally
        dangerous attacker with zero blockers (audited bug #4,
        empirically reproduced in this commit's test).

        Pass 1 (coverage, emergency turns only): force-block attackers
        (biggest power first) with the CHEAPEST available blocker —
        ``ai.clock.opportunity_cost`` (Phase 2a) — until the actual
        joint remaining damage from attackers NOT yet covered is
        survivable. "Joint" is the fix: ``unblocked_damage`` is
        recomputed each iteration directly from the current coverage
        set, never from a per-attacker guess about what the others
        will do.

        Pass 2 (optimization): once survival is secured for a
        covered attacker, swap in a strictly better blocker (a clean
        kill / favorable trade) if the cheapest-to-lose pass-1 pick
        can't achieve it but another unused blocker can — bounded to
        attackers pass 1 already committed to, so it never spends a
        blocker pass 1 deliberately left uncommitted (that's the
        stabilize/portfolio behaviour: once safe, additional optional
        blocks are not forced). On non-emergency turns pass 1 is
        skipped entirely and this pass runs over every attacker,
        exactly replacing the old "normal path" — except the
        categorical vetoes (0-power blocker, hard battle-cry
        exclusion) are retired: the positive-lifespan-delta threshold
        is the only gate now, with a soft (fallback, not exclusionary)
        preference for non-protected/non-battle-cry candidates via
        ``_is_protected_piece``, matching pass 1's own candidate-pool
        pattern.

        The old ``sacrificed_value`` "portfolio cap" (creature-value
        units compared directly against ``remaining`` raw damage
        points — a real unit mismatch) is removed rather than
        patched: pass 1's survivability check is dimensionally
        consistent (both sides are damage/life points), so the
        mismatched comparison is no longer needed at all.

        Phase 2c (docs/design/rules-foundation-sweep-tracker.md): the
        coverage/optimization passes themselves are extracted into
        ``ai.block_assignment.coverage_pass``/``optimize_pass`` — pure,
        side-effect-free functions with no dependency on ``self`` or a
        real ``GameState`` — so ``ai.turn_planner.CombatPlanner.
        _predict_blocks`` (which PREDICTS how an opponent will block
        during turn-planning EV comparisons) can drive the identical
        algorithm instead of maintaining its own independent
        must-block/trade-up/trade-even heuristic. This function is now
        a thin wrapper: it supplies the real cost/score/legality
        callables (``ai.clock.opportunity_cost``, ``_score_block_
        lifespan_delta``, ``_flying_ok``, ``_is_protected_piece``) and
        keeps everything that's genuinely specific to COMMITTING a
        real decision — the plating-futile skip (RC-2), the non-
        emergency double-block-if-needed extension, and logging.
        """
        from engine.cards import Keyword
        from ai.clock import opportunity_cost
        from ai.block_assignment import coverage_pass, optimize_pass

        valid_blockers = game.get_valid_blockers(self.player_idx)
        if not valid_blockers or not attackers:
            return {}

        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]
        total_incoming = sum(a.power or 0 for a in attackers)

        # Winning-state: if our untapped power >= opponent life next turn, don't block.
        # Spending blockers is wasteful when we have lethal on board already.
        my_untapped_power = sum(
            (c.power or 0) for c in me.creatures if not c.tapped
        )
        if my_untapped_power >= opp.life and total_incoming < me.life:
            return {}

        # RC-5: Race if clock math favours us (broader than the lethal-on-board
        # check above). Only fires when we survive this combat AND our
        # clock-to-kill is at least as fast as opp's post-combat clock.
        if self._racing_to_win(game, me, opp, attackers):
            return {}

        # Pre-compute board totals shared by every block branch
        # (coverage, optimization, and the per-decision log lines that
        # quote the lifespan-delta score).
        my_power_total = sum((c.power or 0) for c in me.creatures)
        opp_power_total = sum((c.power or 0) for c in opp.creatures)
        # Live board snapshot for opportunity_cost/creature_value calls
        # below — those functions require an explicit snapshot; using
        # the real board state here instead of a fictional default
        # matters most exactly when it's called: mid-emergency, with
        # real life totals far from a "comfortable" baseline.
        from ai.ev_evaluator import snapshot_from_game
        block_snap = snapshot_from_game(game, self.player_idx)

        id_to_attacker = {a.instance_id: a for a in attackers}
        id_to_blocker = {b.instance_id: b for b in valid_blockers}
        sorted_attackers = sorted(attackers, key=lambda a: a.power or 0, reverse=True)

        def _flying_ok(attacker, blocker):
            if Keyword.FLYING in attacker.keywords:
                return (Keyword.FLYING in blocker.keywords
                        or Keyword.REACH in blocker.keywords)
            return True

        def _min_blockers(attacker):
            # Menace (CR 702.111 / 509.1c): can't be blocked except by two
            # or more creatures. A single-blocker menace assignment is
            # illegal and the engine drops it, so coverage must field 2 (or
            # leave the attacker unblocked) — never exactly 1.
            return 2 if Keyword.MENACE in attacker.keywords else 1

        def _trample_overflow(attacker, chosen_blockers):
            # Trample (CR 702.19): a blocked trampler still connects for
            # power beyond its blockers' combined toughness. Coverage must
            # count that through-damage toward survival, or it stabilizes
            # one attacker too early and dies to overflow. Same shape as the
            # per-block model in _score_block_lifespan_delta.
            if Keyword.TRAMPLE not in attacker.keywords:
                return 0
            soaked = sum((b.toughness or 0) for b in chosen_blockers)
            return max(0, (attacker.power or 0) - soaked)

        def _cost_fn(blocker):
            # The audited fix: rank by ai.clock.opportunity_cost
            # (Phase 2a) ascending — the cheapest-to-lose blocker
            # is the FIRST choice for forced coverage, not a
            # candidate a raw-power veto excluded outright. A
            # 0-power creature with no future value now wins this
            # ranking instead of being banned from it.
            return opportunity_cost(blocker, me, block_snap)

        def _score_fn(attacker, blocker):
            return self._score_block_lifespan_delta(
                game, attacker, blocker,
                my_life=me.life, my_power=my_power_total,
                opp_power=opp_power_total,
            )

        # EMERGENCY: block when incoming damage is dangerous.
        # Triggers: lethal this turn, drop-below-5, or projected lethal across 2 turns
        # (the old single-attacker heuristic treated a 10/10 at life=20 as an emergency;
        #  replaced with a lookahead that only fires when next-turn math is lethal too).
        emergency = (total_incoming >= me.life
                     or (me.life - total_incoming <= EMERGENCY_BLOCK_LOW_LIFE
                         and total_incoming >= EMERGENCY_BLOCK_INCOMING_FLOOR)
                     or self._two_turn_lethal(game, me, opp, attackers))

        if emergency:
            # RC-2: if this attacker's power is dominated by equipment/aura
            # bonuses AND we can't remove those next turn, chumping is
            # futile — the plating rebinds. Skip unless skipping is
            # lethal NOW or lethal NEXT turn from a rebound swing.
            #
            # Derived from the attacker's own damage; no magic number.
            # Bug origin: s=50500 G1 T5 — Boros at 23 life facing 21/1
            # Memnite (double-Plating) DID NOT chump because
            # still_lethal_if_skipped was False (21 < 23). Boros took
            # 21, dropped to 2, died T6 to a rebound swing. Under this
            # derivation: 23 - 21 = 2 ≤ 21, so the rebound-swing-lethal
            # clause fires, chump assigned, Boros lives.
            plating_skipped_any = [False]

            def _skip_fn(attacker, unblocked_damage):
                equip_bonus = self._attacker_equipment_bonus(game, opp, attacker)
                if not (equip_bonus >= PLATING_REBOUND_EQUIP_BONUS
                        and not self._equipment_breakable(game, me)):
                    return False
                damage_if_skipped = unblocked_damage
                still_lethal_if_skipped = damage_if_skipped >= me.life
                rebound_swing_lethal_if_skipped = (
                    me.life - damage_if_skipped <= damage_if_skipped
                )
                if not still_lethal_if_skipped and not rebound_swing_lethal_if_skipped:
                    plating_skipped_any[0] = True
                    return True
                return False

            # ── PASS 1: coverage ────────────────────────────────────
            blocks, used = coverage_pass(
                sorted_attackers, valid_blockers,
                my_life=me.life, can_block_fn=_flying_ok, cost_fn=_cost_fn,
                stabilize_margin=EMERGENCY_BLOCK_STABILIZE_LIFE_GAIN,
                skip_fn=_skip_fn, min_blockers_fn=_min_blockers,
                overflow_fn=_trample_overflow,
            )

            # RC-2: if coverage skipped every attacker via the
            # plating-futile gate and assigned no blocks, accept the
            # damage rather than falling through to a value-driven
            # pass that would re-block the same plated attackers.
            if not blocks and plating_skipped_any[0]:
                return {}

            # ── PASS 2: optimization (swap-upgrade) ─────────────────
            # For each attacker pass 1 already committed a blocker to,
            # check whether a different unused blocker scores a
            # strictly better lifespan-delta (a clean kill / favorable
            # trade the cheap coverage pick couldn't pull off).
            # Deliberately bounded to the covered set — attackers pass
            # 1 left uncommitted (because coverage was already safe
            # without them) are NOT reconsidered here; that preserves
            # the "stop once stabilized" behaviour the old portfolio
            # cap was trying (with a unit-mismatched formula) to
            # express.
            blocks, used = optimize_pass(
                sorted_attackers, valid_blockers, blocks, used,
                can_block_fn=_flying_ok, score_fn=_score_fn,
                bounded_to_covered=True,
                protected_fn=self._is_protected_piece,
            )

            self._log_block_assignments(
                game, blocks, id_to_attacker, id_to_blocker,
                my_power_total, opp_power_total, tag="BLOCK-EMERGENCY")
            return blocks

        # ── Non-emergency: optimization only ───────────────────────
        # Exactly the old "normal path", except the categorical
        # 0-power-blocker and hard battle-cry vetoes are retired
        # (Phase 2b) — the positive-lifespan-delta threshold below is
        # the single gate, with the same soft protected-piece
        # preference every candidate pool in this function applies.
        def _double_block_if_needed(attacker, blocker_ids, used_set):
            best_blocker = id_to_blocker[blocker_ids[0]]
            a_tough = attacker.toughness or 0
            b_power = best_blocker.power or 0
            if b_power < a_tough and Keyword.DEATHTOUCH not in best_blocker.keywords:
                # Among the blockers that complete the kill, spend the one
                # that gives up least (`ai.clock.opportunity_cost`, the same
                # ranking coverage uses) — not the first in list order.
                adequate = [
                    b2 for b2 in valid_blockers
                    if b2.instance_id not in used_set
                    and _flying_ok(attacker, b2)
                    and b_power + (b2.power or 0) >= a_tough]
                if adequate:
                    b2 = min(adequate, key=_cost_fn)
                    blocker_ids.append(b2.instance_id)
                    used_set.add(b2.instance_id)

        blocks, used = optimize_pass(
            sorted_attackers, valid_blockers, {}, set(),
            can_block_fn=_flying_ok, score_fn=_score_fn,
            bounded_to_covered=False,
            protected_fn=self._is_protected_piece,
            on_assigned=_double_block_if_needed,
        )

        self._log_block_assignments(
            game, blocks, id_to_attacker, id_to_blocker,
            my_power_total, opp_power_total, tag="BLOCK")
        return blocks

    # ═══════════════════════════════════════════════════════════
    # RESPONSES — reuse existing ResponseDecider
    # ═══════════════════════════════════════════════════════════

    def decide_response(self, game, stack_item) -> Optional[Tuple["CardInstance", List[int]]]:
        self._response_decider.strategic_logger = self.strategic_logger
        return self._response_decider.decide_response(
            game, stack_item,
            pick_removal_target_fn=self._pick_best_removal_target
        )

    def _evaluate_stack_threat(self, game, stack_item) -> float:
        return self._response_decider.evaluate_stack_threat(game, stack_item)

    # ═══════════════════════════════════════════════════════════
    # TARGETING — simple heuristic
    # ═══════════════════════════════════════════════════════════

    def _enumerate_burn_targets(
        self, game, spell, damage: int,
    ) -> List[Tuple[int, float, str]]:
        """Enumerate every legal target a direct-damage spell can hit.

        Returns ``[(target_id, value, reason), …]`` where ``target_id``
        is ``-1`` for face, ``CardInstance.instance_id`` otherwise.
        The single scoring formula combines:

          * face value: ``damage × burn_face_mult`` (low-life
            multiplier when opp is at burn-range life; clock-multiplier
            penalty when we have no creatures to back up face damage).
          * creature value: ``permanent_threat(c, opp, game) +
            carrier_disrupt_bonus(c)`` when ``damage`` can kill the
            creature (toughness ≤ damage); otherwise the candidate is
            omitted.
          * planeswalker value: ``PLANESWALKER_BASE_VALUE + remaining
            loyalty × PLANESWALKER_LOYALTY_VALUE`` when ``damage`` kills
            the PW (loyalty ≤ damage); otherwise the partial-chip value
            ``min(damage, loyalty) × PLANESWALKER_LOYALTY_VALUE``
            (each loyalty knocked off removes one future activation
            worth that constant).

        PWs are enumerated only when the spell's oracle text permits a
        planeswalker target — "any target" (covers creature, PW, or
        player), or an explicit "planeswalker" mention (covers
        "creature or planeswalker" wording on Galvanic Discharge,
        Galvanic Blast, Tribal Flames, Unholy Heat, etc.).
        """
        from ai.permanent_threat import permanent_threat
        from ai.scoring_constants import (
            PLANESWALKER_BASE_VALUE,
            PLANESWALKER_LOYALTY_VALUE,
        )
        from ai.ev_evaluator import snapshot_from_game

        opp = game.players[1 - self.player_idx]
        me = game.players[self.player_idx]
        snap = snapshot_from_game(game, self.player_idx)
        t = spell.template
        # Typed fields populated at DB load time by
        # oracle_parser.parse_can_target_player/planeswalker — no
        # runtime oracle-text inspection needed here (CR 601.2c).
        can_hit_pw = t.can_target_planeswalker
        can_hit_player = t.can_target_player

        candidates: List[Tuple[int, float, str]] = []

        # ── Face (-1) — only when the spell can legally target a player ──
        if can_hit_player:
            face_val = damage * self.profile.burn_face_mult
            if opp.life <= self.profile.burn_low_life_threshold:
                face_val = damage * self.profile.burn_face_low_life_mult
            if not me.creatures and opp.life > self.profile.burn_low_life_threshold:
                # No clock → face damage is near-worthless until we deploy.
                face_val *= NO_CLOCK_FACE_VAL_MULTIPLIER
            candidates.append((
                -1, face_val,
                f"→ face ({damage} dmg, life {opp.life} → "
                f"{opp.life - damage}): face value {face_val:.2f}",
            ))

        # ── Opp creatures (only if killable by this damage) ──
        for c in opp.creatures:
            remaining_toughness = (c.toughness or 0) - getattr(
                c, "damage_marked", 0)
            if not (damage >= remaining_toughness > 0
                    or remaining_toughness <= 0):
                # Not killable — exclude from candidate set (cannot
                # reduce position value if the creature survives).
                continue
            val = permanent_threat(c, opp, game)
            # Equipment carrier bonus: killing a Plating-equipped
            # carrier strips the equipment off (CR 702.6e).
            val += self._carrier_disrupt_bonus(
                game, opp, c, snap,
                removal_destroys_artifact=False)
            candidates.append((
                c.instance_id, val,
                f"→ {c.name}: marginal threat {val:.2f} "
                f"({c.power}/{c.toughness} body) — better than "
                f"{damage} face dmg",
            ))

        # ── Opp planeswalkers (only when the spell can target PW) ──
        if can_hit_pw:
            for pw in opp.planeswalkers:
                loyalty = pw.loyalty_counters
                if loyalty <= 0:
                    continue
                # Single formula:
                #   - kill: full base + remaining loyalty value
                #   - chip: min(damage, loyalty) × loyalty value
                # Both terms derive from the existing PW constants —
                # no new magic numbers, no card names.
                if damage >= loyalty:
                    val = (PLANESWALKER_BASE_VALUE
                           + loyalty * PLANESWALKER_LOYALTY_VALUE)
                    reason = (f"→ {pw.name}: kill PW (loyalty "
                              f"{loyalty} ≤ {damage} dmg) — value "
                              f"{val:.2f}")
                else:
                    val = damage * PLANESWALKER_LOYALTY_VALUE
                    reason = (f"→ {pw.name}: chip PW ({damage} of "
                              f"{loyalty} loyalty) — value {val:.2f}")
                candidates.append((pw.instance_id, val, reason))

        return candidates

    def _choose_targets(self, game, spell) -> List[int]:
        """Choose targets for a spell."""
        from ai.ev_evaluator import snapshot_from_game
        t = spell.template
        tags = getattr(t, 'tags', set())
        opp = game.players[1 - self.player_idx]
        # Live snapshot so creature_value / threat_value reflect actual
        # board state, not a blank default board.
        snap = snapshot_from_game(game, self.player_idx)

        # Land destruction (typed field, parse-once): the target is the
        # opponent's scarcest color source — denying the only source of
        # a demanded color maximizes their pip deficit (ai/land_denial).
        if getattr(t, 'destroys_target_land', False):
            from ai.land_denial import choose_land_denial_target
            chosen = choose_land_denial_target(
                t, game, self.player_idx, snap)
            if chosen is not None:
                self._last_target_reason = (
                    f"scarcest color source ({chosen.name})")
                return [chosen.instance_id]
            # Compound artifact-or-land form with no affectable land:
            # fall back to the biggest artifact threat so the spell
            # stays live against artifact-only boards.
            if (t.land_destruction_data or {}).get('can_target_artifact'):
                from ai.permanent_threat import permanent_threat
                from engine.cards import CardType as _CT
                arts = [c for c in opp.battlefield
                        if _CT.ARTIFACT in c.template.card_types]
                if arts:
                    best = max(arts,
                               key=lambda c: permanent_threat(c, opp, game))
                    return [best.instance_id]
            return []

        # Burn spells FIRST — they can always target face as fallback
        from decks.card_knowledge_loader import get_burn_damage
        from engine.cards import Keyword as Kw2
        dmg = get_burn_damage(t.name)
        # Storm spells (Grapeshot) deal 1 damage × storm copies — always target face
        if Kw2.STORM in getattr(t, 'keywords', set()) and 'removal' in tags:
            return [-1]  # Grapeshot always goes face (storm copies auto-target)
        if dmg > 0:
            if dmg >= opp.life and t.can_target_player:
                return [-1]  # face = lethal AND legal to target player

            # M10 (Aggro Pattern D / Fix 4): enumerate the FULL candidate
            # set — face (when legal), opp creatures, AND opp planeswalkers
            # (when the spell can target a PW per oracle) — and pick by a
            # single comparator. The previous implementation iterated
            # `opp.creatures` only, so a 3-loyalty Teferi never appeared
            # as a candidate and Boros sent Galvanic Discharge to face
            # instead of killing the planeswalker.
            candidates = self._enumerate_burn_targets(game, spell, dmg)
            if not candidates:
                # No legal targets found — return empty list.  The caller
                # (turn-planner / cast loop) skips spells with no targets
                # rather than illegally directing them at a player.
                return []
            best_id, best_val, best_why = max(candidates, key=lambda x: x[1])
            self._last_target_reason = best_why
            return [best_id]

        # Removal (non-burn): target best opponent permanent
        # For creature-only removal: pick best creature
        # For "nonland permanent" removal: consider artifacts/enchantments too
        if 'removal' in tags and 'board_wipe' not in tags:
            t = spell.template
            can_hit_noncreature = (t.can_destroy_nonland_permanent
                                   or getattr(t, 'exile_hits_noncreature', False)
                                   or t.can_destroy_artifact)

            if can_hit_noncreature:
                # Evaluate all nonland permanents via marginal threat
                # plus the combo-engine disruption premium. The
                # premium is non-zero only when opp's gameplan is
                # combo-archetype AND the card is one of opp's
                # declared engines/payoffs AND opp's combo_clock is
                # inside the disruption window — see
                # ai/engine_disruption.py for the gating contract
                # and tests/test_combo_engine_disruption_premium.py
                # for the rule each constant encodes.
                from ai.permanent_threat import permanent_threat
                from ai.engine_disruption import engine_disruption_value
                nonland = [c for c in opp.battlefield if not c.template.is_land]
                if nonland:
                    best = max(nonland,
                               key=lambda c: (permanent_threat(c, opp, game)
                                              + engine_disruption_value(c, opp, game)))
                    return [best.instance_id]
                return []
            else:
                # Creature-only removal — pick the highest-threat
                # creature, not the highest-base-value one. Uses
                # `creature_threat_value` (oracle-driven amplifier
                # premiums for battle cry / "for each ..." scaling)
                # rather than `creature_value` (raw clock impact).
                # The base function caused removal to prefer Memnite
                # (1/1 vanilla, base 1.15) over Signal Pest (0/1
                # battle cry, base 1.00 / threat 2.15) because the
                # amplifier's effect on other attackers does not
                # appear in raw clock-impact math.
                #
                # `permanent_threat` (used by the burn branch above)
                # is also threat-aware but defined as a *marginal
                # position-value drop* — for a 0-power creature like
                # Signal Pest the drop is zero (position_value sees
                # only the body, not the amplifier). For creature-
                # only removal we prefer `creature_threat_value`,
                # which explicitly credits oracle amplifiers as
                # virtual power.
                #
                # See H_ACT_1 in
                # docs/diagnostics/2026-05-02_affinity_88pct_hypothesis_list.md
                # and the regression test in
                # tests/test_creature_removal_targets_threat_amplifiers.py.
                if opp.creatures:
                    best = max(opp.creatures,
                               key=lambda c: creature_threat_value(c, snap))
                    return [best.instance_id]
                return []

        # Exile effects (March of Otherworldly Light, etc.): target best
        # nonland permanent.  Blink spells are NOT opp-exile: their
        # "exile target creature you control ... return" is a self-blink
        # (same exclusion the card DB applies to the 'removal' tag) —
        # they fall through to the blink branch below.
        if spell.template.can_exile_permanent and 'blink' not in tags:
            from engine.cards import CardType
            nonland = [c for c in opp.battlefield if not c.template.is_land]
            if nonland:
                best = max(nonland, key=lambda c: c.template.cmc)
                return [best.instance_id]
            return []

        # Blink effects: a creature carrying a live pending EOT-exile
        # rider outranks any ETB retrigger — the blink permanently keeps
        # a body that is otherwise lost at end of turn (CR 400.7; see
        # the detriment-clearance EV term in _score_spell). Then best
        # ETB creature, then any creature.
        if 'blink' in tags:
            me = game.players[self.player_idx]
            rider_creatures = [c for c in self._pending_eot_exile_riders(game)
                               if c.template.is_creature]
            if rider_creatures:
                best = max(rider_creatures,
                           key=lambda c: creature_threat_value(c, snap))
                return [best.instance_id]
            etb_creatures = [c for c in me.creatures
                             if 'etb_value' in getattr(c.template, 'tags', set())]
            if etb_creatures:
                best = max(etb_creatures, key=lambda c: creature_value(c, snap))
                return [best.instance_id]
            elif me.creatures:
                best = max(me.creatures, key=lambda c: creature_value(c, snap))
                return [best.instance_id]

        # Reanimate: target best creature in our graveyard
        if 'reanimate' in tags:
            me = game.players[self.player_idx]
            gy_creatures = [c for c in me.graveyard if c.template.is_creature]
            if gy_creatures:
                best = max(gy_creatures,
                           key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
                return [best.instance_id]
            return []  # No targets = can't cast

        return []

    def _pick_best_removal_target(self, card, creatures, player,
                                   game, player_idx) -> Optional["CardInstance"]:
        """Pick the best target for a removal spell.

        Signature matches what ResponseDecider expects:
        (card, creatures_list, opponent_player, game, opponent_idx)

        Uses oracle-driven threat value so battle-cry / scaling creatures
        outrank raw P/T bodies. Burn removal filters targets it cannot kill.

        R3: Equipment carriers receive a tempo bonus on top of raw threat
        — killing the carrier strands the equipment unattached and forces
        opp to spend a re-equip activation (sorcery-speed mana payment +
        another turn of waiting). Without this, a removal spell may pick
        a higher-raw-power naked creature while leaving the Plating-
        wearing engine alive. Bonus is oracle-derived (no card names).
        """
        if not creatures:
            return None
        from ai.ev_evaluator import snapshot_from_game
        snap = snapshot_from_game(game, player_idx)
        candidates = list(creatures)
        # For burn removal, filter out creatures this spell cannot kill.
        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(card.template.name) if card.template else 0
        if dmg > 0:
            killable = [c for c in candidates
                        if ((c.toughness or 0) - getattr(c, 'damage_marked', 0)) <= dmg]
            # Fallback: if nothing is killable, keep original list so the
            # caller still gets something to target (the ResponseDecider
            # may still want to fire for triggered-damage purposes).
            if killable:
                candidates = killable

        # Removal that ALSO destroys the equipment artifact (Abrupt Decay,
        # Prismatic Ending at X≥2, Nature's Claim, etc.) doubles the value
        # of hitting a carrier — the artifact is gone, not just dropped.
        ct = card.template
        also_destroys_artifact = bool(ct and (ct.can_destroy_artifact
                                               or ct.can_destroy_nonland_permanent))

        def _rank(c) -> float:
            # Use `permanent_threat` (marginal-contribution via
            # snapshot recomputation) instead of `creature_threat_value`
            # (oracle-driven heuristic).  permanent_threat accounts
            # for the FULL position swing of removing the creature —
            # raw clock contribution + evasion + equipment bonuses
            # + scaling triggers — the same way the burn-target
            # picker and equip-target picker score.  Architectural
            # pattern: every threat-scoring decision in the AI now
            # uses the same primitive.
            from ai.permanent_threat import permanent_threat
            base = permanent_threat(c, player, game)
            return base + self._carrier_disrupt_bonus(
                game, player, c, snap,
                removal_destroys_artifact=also_destroys_artifact)

        return max(candidates, key=_rank)

    def _carrier_disrupt_bonus(self, game, opp, carrier, snap,
                                removal_destroys_artifact: bool = False) -> float:
        """Tempo bonus for removing a creature wearing equipment.

        Killing a carrier strips every attached equipment off (CR 702.6e
        — equipment falls off when its equipped creature leaves play).
        Opp must then re-pay the equip cost AND wait to activate the
        sorcery-speed equip ability, denying at least one combat turn
        of the equipment's pump contribution.

        The bonus is composed from two oracle-derived terms:
          * Pump-denial value: sum of '+X/+Y' contributions on attached
            equipment (with 'for each <qualifier>' scaling expanded
            against opp's current board), converted to threat units via
            the same `creature_clock_impact * 20.0` pipeline that
            `creature_threat_value` uses for virtual power.
          * Re-equip mana tempo: sum of `equip_cost` across attached
            equipment, converted via `mana_clock_impact * 20.0`. Re-
            attaching costs that mana on a future turn.

        If the removal spell also destroys the equipment artifact
        outright, the pump-denial term is doubled (the equipment is
        permanently gone, not just unattached).

        All detection is oracle-regex-driven; no card names. No
        magic-number weights — values fall out of `clock.py`.
        """
        attached_ids = set()
        for tag in carrier.instance_tags:
            if not tag.startswith('equipped_'):
                continue
            tail = tag.split('_', 1)[1]
            if tail.isdigit():
                attached_ids.add(int(tail))
        if not attached_ids:
            return 0.0

        from ai.clock import creature_clock_impact, mana_clock_impact

        pump_total = 0
        equip_cost_total = 0

        for perm in opp.battlefield:
            if perm.instance_id not in attached_ids:
                continue
            eq_oracle = (perm.template.oracle_text or '').lower()
            m = _EQUIP_BONUS_RE.search(eq_oracle)
            if m:
                base_pump = int(m.group(2))
                # Apply 'for each <qualifier>' scaling on opp's board.
                scale_match = re.search(
                    r'for each (artifact|creature|land|card)', eq_oracle
                )
                if scale_match:
                    kind = scale_match.group(1)
                    if kind == 'artifact':
                        count = sum(
                            1 for c in opp.battlefield
                            if 'artifact' in str(c.template.card_types).lower()
                        )
                    elif kind == 'creature':
                        count = len(opp.creatures)
                    elif kind == 'land':
                        count = len(
                            [c for c in opp.battlefield if c.template.is_land]
                        )
                    else:  # 'card' proxy
                        count = len(opp.battlefield)
                    pump_total += base_pump * count
                else:
                    pump_total += base_pump
            cost = getattr(perm.template, 'equip_cost', None)
            if cost is not None:
                equip_cost_total += cost

        if pump_total == 0 and equip_cost_total == 0:
            return 0.0

        # Convert virtual-power denial to threat units. Use carrier's
        # toughness so the impact reflects what an attack with that pump
        # would actually do (matches the formula used in
        # `creature_threat_value` for amplifier virtual power).
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(carrier.template, 'keywords', set())}
        tough = carrier.toughness or 0
        # Marginal clock impact of denying `pump_total` virtual power
        # for at least one combat turn.
        pump_impact = (
            creature_clock_impact(pump_total, tough, kws, snap)
            - creature_clock_impact(0, tough, kws, snap)
        ) * CLOCK_IMPACT_LIFE_SCALING
        # If removal also destroys the equipment, pump is permanently
        # denied — double-count to reflect the multi-turn loss.
        if removal_destroys_artifact:
            pump_impact *= 2.0

        # Re-equip tempo: mana spent on a sorcery-speed ability is
        # mana not available for a spell that turn.
        mana_tempo = equip_cost_total * mana_clock_impact(snap) * CLOCK_IMPACT_LIFE_SCALING

        return pump_impact + mana_tempo

    def _pending_eot_exile_riders(self, game) -> List["CardInstance"]:
        """Own battlefield permanents tracked by a LIVE delayed
        end-of-turn-exile rider (the Goryo's Vengeance / Sneak Attack /
        Through the Breach detriment shape).

        Live = the tracked object is still on the battlefield under the
        SAME battlefield entry — the CR 400.7 object-identity staleness
        check, mirroring what the engine applies at end-of-turn cleanup
        (engine/turn_manager.py).  Data source is the engine's rider
        registry (`game_state.register_end_of_turn_exile`), so the
        check is mechanic-driven: any delayed-EOT-exile effect × any
        permanent, no card names.
        """
        riders = []
        for card, controller, entry_seq in getattr(
                game, '_end_of_turn_exiles', ()):
            if (controller == self.player_idx
                    and getattr(card, 'zone', None) == 'battlefield'
                    and getattr(card, 'battlefield_entry_seq', 0)
                    == entry_seq):
                riders.append(card)
        return riders

    def _blink_would_forfeit_attack(self, card) -> bool:
        """True iff ``card`` can attack THIS turn and a blink would
        remove that capability.

        CR 400.7: the blinked permanent re-enters as a new object —
        summoning-sick, carrying only its printed keywords. A temporary
        haste grant (reanimation rider, pump effect) dies with the old
        object, so the new object cannot attack; printed haste survives
        re-entry, so the new object still can. Reasoned from object
        state (current attack capability + printed keywords), never
        from card identity.
        """
        if not getattr(card, 'can_attack', False):
            return False
        printed = getattr(card.template, 'keywords', None) or set()
        has_printed_haste = any(
            str(getattr(kw, 'value', kw)).lower() == 'haste'
            for kw in printed)
        return not has_printed_haste

    def _forfeited_attack_charge(self, card, snap) -> float:
        """EV price of the attack ``card`` would forfeit if blinked
        pre-combat: one combat step from the clock primitive
        (`ai/clock.forfeited_attack_clock_impact` — power kill-fraction
        + lifelink swing), converted to EV units via
        CLOCK_IMPACT_LIFE_SCALING like every other clock term in
        `_score_spell`. Keywords are the object's CURRENT keywords
        (temp grants included) — the forfeited attack would happen with
        those.
        """
        from ai.clock import forfeited_attack_clock_impact
        kws = {str(getattr(kw, 'value', kw)).lower()
               for kw in (getattr(card, 'keywords', None) or set())}
        return (forfeited_attack_clock_impact(card.power or 0, kws, snap)
                * CLOCK_IMPACT_LIFE_SCALING)

    def _presumed_reset_target(self, me, snap):
        """The creature a state-resetting spell (blink/flicker) would
        return, modelled the way the effect itself chooses: the highest
        `creature_threat_value` creature we control (see
        `engine/card_effects.py`'s blink handler, whose own choice
        function delegates to that same primitive). None when we
        control no creatures — nothing to reset."""
        if not me.creatures:
            return None
        return max(me.creatures,
                   key=lambda c: creature_threat_value(c, snap))

    def decide_optional_recast(self, game, card) -> bool:
        """Should an OPTIONAL free recast from exile be taken now?

        Rebound (CR 702.88b) and every other "you may cast that card"
        free recast is a choice, not a turn-based action. The engine
        establishes that the recast is legal; this decides whether to
        take it.

        Rule encoded: a recast that would return a creature to a state
        where it cannot attack this turn (CR 400.7 — a blink returns a
        NEW object, summoning-sick, keeping only its printed keywords,
        so a temporary haste grant does not survive) is DECLINED while
        that creature would otherwise attack, unless the recast's own
        EV covers the combat step it costs. The price is the shared
        clock primitive `_forfeited_attack_charge`
        (`ai/clock.forfeited_attack_clock_impact` x
        CLOCK_IMPACT_LIFE_SCALING) — the same charge the Main-1 blink
        gate in `_score_spell` uses; the value side is `_score_spell`
        itself, so no new scoring numbers enter here.

        A free recast that forfeits nothing is always taken: it costs
        no mana and no card.

        Root cause: docs/diagnostics/
        2026-08-27_dimir_overperformance_root_cause.md (win 8) — the
        rebound recast was taken at every upkeep, re-summoning-sicking
        a reanimated 7/7 that then attacked exactly once per game.
        """
        me = game.players[self.player_idx]
        opp = game.players[1 - self.player_idx]
        tags = getattr(card.template, 'tags', set()) or set()
        resets_state = ('blink' in tags
                        or getattr(card.template, 'has_exile_own_creature',
                                   False))
        if not resets_state:
            return True

        snap = snapshot_from_game(game, self.player_idx)
        target = self._presumed_reset_target(me, snap)
        if target is None or not self._blink_would_forfeit_attack(target):
            return True

        charge = self._forfeited_attack_charge(target, snap)
        if charge <= 0:
            return True
        return self._score_spell(card, snap, game, me, opp) > charge

    def _blink_reservation_penalty(self, me, snap, cost: int,
                                   exclude_instance_id, game) -> float:
        """Reservation charge for a cast that would strand a held blink
        while a live pending EOT-exile rider exists (RC-1 follow-up,
        docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md).

        The blink-clears-detriment credit prices the blink itself, but
        the line dies upstream if a competing cast in the same main
        phase spends the blink's last color source (replay evidence:
        the last white-capable source spent on a discard spell right
        after reanimating). Charge the forfeited clearance credit —
        the saved permanent's `creature_threat_value`, the SAME
        primitive the credit side uses — when this cast flips the held
        blink from castable to uncastable this turn. Zero when no
        rider is live, no blink is held, or capacity survives the
        cast. Mirrors `_holdback_penalty`'s off-color-first payment
        accounting; mechanic-driven, no card names.
        """
        if game is None or cost <= 0:
            return 0.0
        riders = self._pending_eot_exile_riders(game)
        if not riders:
            return 0.0
        blinks = [c for c in me.hand
                  if c.instance_id != exclude_instance_id
                  and 'blink' in getattr(c.template, 'tags', set())
                  and (c.template.is_instant or c.template.is_sorcery)]
        if not blinks:
            return 0.0
        bl = min(blinks, key=lambda c: c.template.cmc or 0).template
        bl_cmc = bl.cmc or 0

        def _castable(total_mana: int, by_color: dict) -> bool:
            if total_mana < bl_cmc:
                return False
            mc = bl.mana_cost
            for code, attr in (
                ('W', 'white'), ('U', 'blue'), ('B', 'black'),
                ('R', 'red'), ('G', 'green'),
            ):
                pips = getattr(mc, attr, 0) if mc is not None else 0
                if pips and by_color.get(code, 0) < pips:
                    return False
            return True

        by_color_now = dict(getattr(snap, 'my_mana_by_color', {}) or {})
        if not _castable(snap.my_mana, by_color_now):
            return 0.0  # blink already uncastable — nothing to strand

        # Post-cast capacity: pay the candidate from off-color mana
        # first (rational optimum), dipping into each blink color only
        # when off-color runs out — the same accounting
        # `_holdback_penalty` uses for held counters.
        by_color_after = {}
        for code, avail in by_color_now.items():
            off_color = max(0, snap.my_mana - avail)
            must_tap_from_color = max(0, cost - off_color)
            by_color_after[code] = max(0, avail - must_tap_from_color)
        if _castable(snap.my_mana - cost, by_color_after):
            return 0.0

        best_rider = max(riders,
                         key=lambda c: creature_threat_value(c, snap))
        return -creature_threat_value(best_rider, snap)

    def _has_high_threat_target(self, game, spell, snap=None) -> bool:
        """True if a removal spell has a target worth proactively casting for.

        Creatures use `creature_threat_value` (oracle-driven virtual
        power through the clock pipeline).  Noncreature permanents use
        the marginal-contribution formula in `ai.permanent_threat` —
        its value is in position-value units, so the same
        `big_creature_power` floor (a threat amount, not a raw P/T)
        applies to both branches uniformly.
        """
        opp = game.players[1 - self.player_idx]
        tags = getattr(spell.template, 'tags', set())
        if 'removal' not in tags:
            return False

        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(spell.template.name) if spell.template else 0
        prof = self.profile
        floor = float(prof.big_creature_power)  # e.g. 4.0 EV floor

        t = spell.template
        # Converge-conditioned removal (Prismatic Ending shape) can only
        # ever reach a mana value <= this manabase's achievable colors-
        # of-mana-spent ceiling — a huge, unreachable threat (Domain
        # payoffs carry a large printed mana value despite a small
        # discounted cast cost) is not justification to cast into a
        # guaranteed whiff. Same picker `pick_converge_x_value` uses at
        # cast time, consulted here so the decision to cast agrees with
        # what casting will actually deliver.
        converge_max_mv = None
        if getattr(t, 'has_converge', False):
            from engine.card_effects import converge_reachable_max_mv
            converge_max_mv = converge_reachable_max_mv(game, self.player_idx)

        for c in opp.creatures:
            if converge_max_mv is not None and (c.template.cmc or 0) > converge_max_mv:
                continue
            if dmg > 0:
                remaining = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
                if remaining > dmg:
                    continue
            if creature_threat_value(c, snap) >= floor:
                return True

        hits_noncreature = (t.can_destroy_nonland_permanent
                            or t.can_exile_permanent
                            or t.can_destroy_artifact
                            or t.can_destroy_enchantment)
        if hits_noncreature:
            from ai.permanent_threat import permanent_threat
            for perm in opp.battlefield:
                if perm.template.is_land or perm.template.is_creature:
                    continue
                if converge_max_mv is not None and (perm.template.cmc or 0) > converge_max_mv:
                    continue
                if permanent_threat(perm, opp, game) >= floor:
                    return True

        return False

    def _spell_requires_targets(self, spell) -> bool:
        """Check if a spell needs targets to be cast legally.

        IMPORTANT: Creatures with removal ETBs (Phlage, Bowmasters, Solitude)
        do NOT require targets — they can be cast for the body alone.
        The ETB targeting happens on resolution, not on cast.
        """
        t = spell.template
        tags = getattr(t, 'tags', set())

        # Creatures and planeswalkers never require targets to CAST.
        # Creature ETB targeting happens on resolution, not on cast.
        # Planeswalker loyalty abilities are activated after deployment.
        from engine.cards import CardType
        if t.is_creature or CardType.PLANESWALKER in t.card_types:
            return False

        # Classified land-destruction spells (typed field, parse-once):
        # "Destroy target land" cannot resolve without a target.
        if getattr(t, 'destroys_target_land', False):
            return True

        # Modal spells with draw mode don't require targets (can choose draw)
        if 'counterspell' in tags:
            if t.has_draw_effect and ('choose' in (t.oracle_text or '').lower() or '•' in (t.oracle_text or '')):
                return False  # modal spell with draw mode (Archmage's Charm)
            return True
        if 'removal' in tags and 'board_wipe' not in tags:
            return True
        # Exile effects that target opponent's permanents (March of Otherworldly Light, etc.)
        if t.can_exile_permanent:
            return True
        if 'blink' in tags:
            return True
        # Reanimate spells need a creature in the graveyard
        if 'reanimate' in tags:
            return True
        for ability in t.abilities:
            if ability.targets_required > 0:
                desc = ability.description.lower()
                if any(kw in desc for kw in ["destroy", "exile", "bounce",
                                              "return", "counter", "damage"]):
                    return True
        return False

    def _filter_legend_rule(self, player, spells):
        """Remove legendary permanents we already control."""
        from engine.cards import Supertype, CardType
        controlled = set()
        for c in player.battlefield:
            supertypes = getattr(c.template, 'supertypes', [])
            if Supertype.LEGENDARY in supertypes or CardType.PLANESWALKER in c.template.card_types:
                controlled.add(c.template.name)
        return [s for s in spells
                if s.template.name not in controlled
                or (Supertype.LEGENDARY not in getattr(s.template, 'supertypes', [])
                    and CardType.PLANESWALKER not in s.template.card_types)]

    # ═══════════════════════════════════════════════════════════
    # EQUIPMENT (compatibility with GameRunner)
    # ═══════════════════════════════════════════════════════════

    def _consider_equip(self, game, player):
        """Check if any unattached equipment should be attached.

        Returns the best equip action as a Play candidate, or None.
        Picks the equipment that gives the biggest damage boost and
        attaches it to the best attacker (evasion preferred).
        """
        from engine.cards import CardType, Keyword

        # Find unattached equipment we can afford to equip
        equipment = [c for c in player.battlefield
                     if CardType.ARTIFACT in c.template.card_types
                     and 'equipment' in getattr(c.template, 'tags', set())
                     and "equipment_unattached" in c.instance_tags]
        if not equipment:
            return None

        creatures = [c for c in player.creatures if not c.summoning_sick]
        if not creatures:
            return None

        # Available mana
        available_mana = (player.untapped_mana_capacity()
                          + player.mana_pool.total()
                          + player._tron_mana_bonus())

        results = []
        for equip in equipment:
            cost = equip.template.equip_cost
            if cost is None or cost > available_mana:
                continue

            # Score each creature as an equip target via the same
            # marginal-contribution formula the burn-target picker
            # uses: `permanent_threat(c, me, game)` — what does the
            # creature contribute to OUR position value?  Higher
            # threat-to-opp = better equip target because the equipment
            # amplifies whatever clock the creature is already
            # producing.  Evasion (flying / menace / trample) flows
            # through `permanent_threat` via the snapshot's
            # `my_evasion_power` field — the magic FLYING * 2.0,
            # MENACE * 1.5, TRAMPLE * 1.3 multipliers used to
            # approximate this and are now derived from
            # `position_value` directly.
            from ai.permanent_threat import permanent_threat
            def _equip_target_score(c):
                # Use OUR perspective for our own creatures
                return permanent_threat(c, player, game)

            best = max(creatures, key=_equip_target_score)

            # Score equipping like deploying a creature with the bonus power
            bonus = self._estimate_equip_bonus(equip, player)
            ev = bonus * self.profile.creature_value_mult

            # Bundle 3 A3 — same holdback gate as _score_spell. Equip
            # activation taps mana; it must respect held interaction.
            from ai.ev_evaluator import snapshot_from_game
            snap = snapshot_from_game(game, self.player_idx)
            opp = game.players[1 - self.player_idx]
            ev += self._holdback_penalty(
                player, opp, snap, cost=cost,
                exclude_instance_id=equip.instance_id, game=game)

            results.append(Play("equip", equip, [best.instance_id], ev,
                                f"Equip {equip.name} to {best.name} (EV={ev:.1f})"))

        if results:
            return max(results, key=lambda p: p.ev)
        return None

    @staticmethod
    def _estimate_equip_bonus(equip, player) -> float:
        """Estimate power bonus from equipping, derived from oracle text.

        Parses patterns like "+1/+0 for each artifact" or static "+2/+2".
        Returns the effective power grant as a float.
        """
        import re
        from engine.cards import CardType
        oracle = (equip.template.oracle_text or '').lower()

        # Dynamic: "+X/+Y for each artifact" or "+X/+Y for each artifact and/or enchantment"
        m = re.search(r'\+(\d+)/[+\-]\d+ for each (artifact|enchantment)', oracle)
        if m:
            per_bonus = int(m.group(1))
            if getattr(equip.template, 'has_artifact_or_enchantment_scaling', False):
                count = sum(1 for b in player.battlefield
                            if CardType.ARTIFACT in b.template.card_types
                            or CardType.ENCHANTMENT in b.template.card_types)
            elif 'artifact' in m.group(2):
                count = sum(1 for b in player.battlefield
                            if CardType.ARTIFACT in b.template.card_types)
            else:
                count = sum(1 for b in player.battlefield
                            if CardType.ENCHANTMENT in b.template.card_types)
            return per_bonus * count

        # Static: "gets +X/+Y" or "+X/+Y"
        m = re.search(r'\+(\d+)/[+\-]\d+', oracle)
        if m:
            return int(m.group(1))

        # Fallback
        return 2.0
