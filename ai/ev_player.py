"""EV-Based AI Player — data-driven MTG decision engine.

Architecture: get legal plays → score each via StrategyProfile → pick best.
All weights in ai/strategy_profile.py. All card effects from oracle text.
Combat, blocking, and response decisions delegate to existing modules.
"""
from __future__ import annotations
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

# RC-2 — parse "equipped/enchanted creature gets +X/+Y" bonuses from
# oracle text. Detects Cranial Plating, Embercleave, Colossus Hammer,
# Ethereal Armor auras, etc., without naming any card.
_EQUIP_BONUS_RE = re.compile(
    r'(equipped|enchanted) creature gets \+(\d+)/\+(\d+)'
)

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
# Play representation
# ─────────────────────────────────────────────────────────────

class Play:
    """A candidate play with its EV score and lookahead reasoning."""
    __slots__ = ('action', 'card', 'targets', 'ev', 'reason',
                 'heuristic_ev', 'lookahead_ev', 'counter_pct', 'removal_pct', 'target_reason',
                 'no_signal')

    def __init__(self, action: str, card, targets: list, ev: float, reason: str, target_reason: str = ''):
        self.action = action  # "play_land", "cast_spell", "cycle"
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
        # archetype is a string from DECK_ARCHETYPE_OVERRIDES; some decks
        # (Ruby Storm) use "storm" which isn't an ArchetypeStrategy enum
        # value. Storm shares COMBO's mulligan semantics (need ritual +
        # cantrip + finisher), so alias it here. Previously defaulted to
        # MIDRANGE — which silently skipped the combo-ritual backup check
        # at mulligan.py:111, causing Storm to keep ritual-less hands.
        from ai.mulligan import MulliganDecider
        from ai.strategy_profile import ArchetypeStrategy
        _COMBO_ALIASES = {"storm"}  # strings treated as COMBO for mulligan
        if self.archetype in [e.value for e in ArchetypeStrategy]:
            arch_enum = ArchetypeStrategy(self.archetype)
        elif self.archetype in _COMBO_ALIASES:
            arch_enum = ArchetypeStrategy.COMBO
        else:
            arch_enum = ArchetypeStrategy.MIDRANGE
        self._mulligan_decider = MulliganDecider(arch_enum, self.goal_engine)

        # Response decider — reuse existing
        from ai.response import ResponseDecider
        from ai.turn_planner import TurnPlanner
        self._response_decider = ResponseDecider(
            player_idx, TurnPlanner(), self.strategic_logger)

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
        """Keep or mulligan. Delegates to MulliganDecider."""
        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]

        if cards_in_hand <= self.profile.mulligan_always_keep:
            self.mulligan_reason = f"only {cards_in_hand} cards — always keep"
            return True
        if len(lands) == 0:
            self.mulligan_reason = "0 lands"
            return False
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
                          excluded_cards: set = None) -> Optional[Tuple[str, "CardInstance", List[int]]]:
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

        legal = game.get_legal_plays(self.player_idx)
        if not legal:
            return None
        if excluded_cards:
            legal = [c for c in legal if c.instance_id not in excluded_cards]
            if not legal:
                return None

        lands = [c for c in legal if c.template.is_land]
        spells = [c for c in legal if not c.template.is_land]

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

        # Score land plays — lands compete with spells for priority
        if lands and me.lands_played_this_turn < (1 + me.extra_land_drops):
            from engine.card_database import FETCH_LAND_COLORS
            safe_lands = [
                l for l in lands
                if l.name not in FETCH_LAND_COLORS or me.life > 1
                or 'basic land' in (l.template.oracle_text or '').lower()  # free fetches
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
        # Accept "storm" as a COMBO alias — Ruby Storm is the only deck
        # with that archetype string and its _estimate_combo_chain math
        # (ritual-first simulation) is exactly what Storm needs. Same
        # alias pattern as the mulligan decider (see __init__).
        if self.goal_engine and self.archetype in ("combo", "storm"):
            from ai.ev_evaluator import _estimate_combo_chain
            can_kill, storm_count, damage, chain = _estimate_combo_chain(
                game, self.player_idx)
            if can_kill or storm_count >= 5:
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
                  and (c.template.power or 0) >= 5]
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
                    is_dying = snap.am_dead_next or (snap.opp_power >= prof.dying_opp_power
                                                     and snap.opp_clock_discrete <= prof.dying_opp_clock)
                    has_big_target = self._has_high_threat_target(game, spell, snap)
                    # has_big_target overrides control_patience: if a real
                    # threat is on board (oracle-driven threat floor), the
                    # reactive-only spell should fire proactively even for
                    # control decks that otherwise hold until late. Audit
                    # finding: Azorius Prismatic Ending sat in hand until
                    # Cranial Plating had already locked the game.
                    if is_dying or has_big_target:
                        pass  # allow through reactive-only gate
                    elif prof.control_patience and snap.opp_clock_discrete >= 4:
                        continue  # control: no pressure, no threat — hold
                    else:
                        continue  # non-control: no threat, not dying — hold

            ev = self._score_spell(spell, snap, game, me, opp)
            targets = self._choose_targets(game, spell)

            # Reanimate override: massive boost when big creature is in GY
            if reanimate_override and spell.instance_id == reanimate_override.instance_id:
                ev += 40.0  # force-cast reanimation when target ready

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

        if best.ev < self.profile.pass_threshold:
            return None

        self._last_played_target_reason = getattr(best, "target_reason", "")
        return (best.action, best.card, best.targets)

    # ═══════════════════════════════════════════════════════════
    # SCORING — per-archetype spell evaluation
    # ═══════════════════════════════════════════════════════════

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
        # Pass BHI for Bayesian-updated opponent response probabilities
        ev = compute_play_ev(card, snap, self.archetype, game, self.player_idx,
                             bhi=self.bhi)

        # ── Free cast bonus (generic) ──
        # Any spell offered for 0 effective mana (Ragavan exile, cascade,
        # suspend, Wish-style effects) represents pure card advantage.
        # Tag: _free_cast_opportunity set by whatever granted the cast.
        # Rule: ev >= 0 always (never skip a free spell that doesn't hurt).
        #       +1.5 bonus on top of projection to reflect tempo gain.
        if getattr(card, "_free_cast_opportunity", False):
            ev = max(ev, 0.0)  # floor: never negative
            ev += 1.5          # tempo: got it for free

        # ── Evoke overlay: projection doesn't model 2-card cost ──
        if ('evoke' in tags or 'evoke_pitch' in tags) and snap.my_mana < (t.cmc or 0):
            # Evoking costs an extra card — subtract its future clock value
            from ai.clock import card_clock_impact
            ev -= card_clock_impact(snap) * 15  # losing a card is significant
            # But if we're dying, evoking removal is still worth it
            if snap.am_dead_next:
                ev += 10.0
            elif snap.opp_creature_count == 0 and 'removal' in tags:
                ev -= 20.0  # never evoke removal with no targets

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

        # ── Fizzle detection: land-sacrifice spells without critical mass ──
        # Spells like Scapeshift ("Sacrifice any number of lands. Search
        # your library for up to that many land cards...") do nothing if
        # the controller has too few lands to sacrifice — the engine fizzles
        # the cast (engine/card_effects.py:scapeshift_resolve requires 4+
        # lands). Without this guard the AI burns 4 mana on a wasted cast
        # on T3, as seen in Amulet Titan vs Dimir traces.
        # Oracle-driven detection — no hardcoded card names.
        if 'sacrifice any number of lands' in t_oracle and 'search your library' in t_oracle:
            my_land_count = sum(1 for c in me.battlefield if c.template.is_land)
            # Rules constant matching engine threshold at
            # engine/card_effects.py:scapeshift_resolve.
            LAND_SACRIFICE_MIN_LANDS = 4
            if my_land_count < LAND_SACRIFICE_MIN_LANDS:
                # Force the score below pass_threshold so the AI holds the
                # spell until critical mass is available. Derivation: profile-
                # driven, no magic numbers — uses the existing gate constant.
                ev = min(ev, p.pass_threshold - 1.0)

        # ── Cascade patience gate (LE-A3) ──
        # Mirror of the Storm ritual patience gate (now in card_combo_modifier):
        # cascade spells in a reanimator shell get an unconditional +1.5
        # free-cast bonus (lines 440-442), but if the graveyard is too
        # thin the cascaded reanimate spell (Living End / Cascade Zenith
        # pattern) returns an empty or insufficient board. The cascade
        # enabler is then burned for no payoff.
        #
        # Gate fires when ALL of:
        #   1. Spell has the cascade keyword (oracle-parsed `is_cascade`).
        #   2. The deck is a graveyard-reanimator shell — i.e. its
        #      gameplan declares a FILL_RESOURCE goal with
        #      `resource_zone == "graveyard"`. Gameplan-declared signal,
        #      gathered by `_cascade_graveyard_target()`. Non-reanimator
        #      cascade decks (e.g. a hypothetical Cascade Zenith burn
        #      list with no FILL_RESOURCE/graveyard goal) return 0 and
        #      the gate does NOT fire — their cascade hit isn't gated
        #      on graveyard contents.
        #   3. Graveyard creature count < the gameplan's declared
        #      `resource_target`. No magic numbers — the number is the
        #      same threshold the gameplan uses to transition out of
        #      FILL_RESOURCE.
        #
        # When the gate fires, clamp EV below pass_threshold — a HARD
        # reduction in line with the Scapeshift fizzle gate above and the
        # Storm ritual patience gate (now in card_combo_modifier).  The
        # AI will hold the cascade enabler until the graveyard has critical mass.
        if getattr(t, 'is_cascade', False):
            fill_target = self._cascade_graveyard_target()
            if fill_target > 0 and snap.my_gy_creatures < fill_target:
                # Clamp matches the Scapeshift fizzle gate treatment
                # (line 478): profile.pass_threshold - 1.0. No extra
                # magic: the clamp is "just below pass_threshold" so
                # the spell is rejected by decide_main_phase but any
                # other legal play can still fire.
                ev = min(ev, p.pass_threshold - 1.0)

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
                    cheapest_finisher_cmc = min(
                        (f.template.cmc or 0)
                        for f in finishers_in_hand
                    )
                    candidate_cmc = t.cmc or 0
                    post_cast_mana = snap.my_mana - candidate_cmc
                    if post_cast_mana < cheapest_finisher_cmc:
                        # Finisher_unlock_chance: 1.0 when the
                        # finisher IS castable right now (mana >=
                        # cmc); else 0.0. Oracle-derived from
                        # current snapshot, no magic numbers.
                        finisher_unlock_chance = (
                            1.0 if snap.my_mana >= cheapest_finisher_cmc
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
        is_titan_like = (t.is_creature and (t.cmc or 0) >= 6
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
        AMULET_TITAN_MANA_BONUS = 4.0  # rules: 2 lands × 2 mana each
        from ai.clock import mana_clock_impact
        mana_impact = mana_clock_impact(snap)  # value per point of mana
        if is_amulet and has_titan_in_hand:
            # P(Titan lands in time) proxy: how many turns until we can
            # cast a 6-drop. If we're at 4+ lands, near-immediate.
            turns_to_cast = max(1, 6 - len(me.lands))
            # Discount by turns — Amulet benefit realized only once Titan lands.
            ev += (AMULET_TITAN_MANA_BONUS * mana_impact * 20.0) / turns_to_cast
        if is_titan_like and has_amulet_on_board:
            # Immediate payoff when Titan is being cast now.
            ev += AMULET_TITAN_MANA_BONUS * mana_impact * 20.0

        # ── Non-creature permanent overlay (Pattern B) ──
        from engine.cards import CardType
        if not t.is_creature and not t.is_instant and not t.is_sorcery:
            if CardType.PLANESWALKER in t.card_types:
                # Planeswalkers are sticky card-advantage engines. Each
                # loyalty activation ≈ one card's clock impact (draw,
                # removal, damage, tokens). Stickiness bonus: opp must
                # divert removal to kill them → effectively a 1-for-1
                # card exchange in our favor on the turn it resolves.
                # Derives from clock.card_clock_impact — no flat tiers.
                from ai.clock import card_clock_impact
                loyalty = t.loyalty or 3
                # Expected activations before death: loyalty-1 because the
                # enters-with-loyalty first use is net-0; subsequent uses
                # generate value. +1 accounts for the opp-removal cost.
                expected_activations = max(1, loyalty - 1) + 1
                card_val = card_clock_impact(snap) * 20.0  # scale to board-eval units
                ev += expected_activations * card_val
                # No additional per-oracle bumps: loyalty × card_val already
                # integrates over whatever the planeswalker actually does,
                # including the Teferi-pattern "untap lands" mana-advantage
                # (that's one activation per turn, already counted above).
            elif 'cost_reducer' in tags:
                # Saves ~1 mana per spell over the remaining game — derive
                # from card_clock_impact × turns_remaining rather than +4.
                from ai.clock import card_clock_impact, combat_clock, NO_CLOCK
                my_c = combat_clock(snap.my_power, snap.opp_life,
                                     snap.my_evasion_power, snap.opp_toughness)
                opp_c = combat_clock(snap.opp_power, snap.my_life,
                                      snap.opp_evasion_power, snap.my_toughness)
                turns = min(my_c, opp_c)
                if turns >= NO_CLOCK:
                    turns = 6.0  # rules constant: Modern midgame horizon
                turns = max(2.0, min(turns, 8.0))
                ev += turns * card_clock_impact(snap) * 20.0

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
                ev -= cmc * mana_clock_impact(snap) * 20.0

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
                    'for each' in oracle_lower
                    or 'for every' in oracle_lower
                    or 'cost {' in oracle_lower         # cost reducers
                    or 'whenever' in oracle_lower       # triggered
                    or 'when this' in oracle_lower      # ETB triggers
                    or 'when ' + t.name.lower() + ' enters' in oracle_lower
                    or '{t}:' in oracle_lower           # tap abilities
                )
                if not stacks:
                    from ai.clock import mana_clock_impact
                    cmc = t.cmc or 1
                    ev -= cmc * mana_clock_impact(snap) * 20.0

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
            waste_penalty = ((t.cmc or 0) * mana_clock_impact(snap) * 20.0
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
                waste_penalty = ((t.cmc or 0) * mana_clock_impact(snap) * 20.0
                                 + 1.0 + me_lost)
                ev -= waste_penalty

        # ── X-cost board wipe: hold when the X-budget can't meaningfully clear ──
        # Tuned through three passes:
        #   v1 (≥2 kills) — too strict: Azorius never wraths vs 1 Ragavan.
        #   v2 (≥3 power on single kill) — still too strict: 2-power Ragavan
        #       fails, but killing even a single attacking Ragavan is correct
        #       when the AI is dying.
        #   v3 (this): threshold drops to 2 power, and the whole gate is
        #       waived when we're at low life (≤10). Consolidates the
        #       "always fire when desperate" behaviour.
        if ('board_wipe' in tags and t.x_cost_data and opp.creatures):
            total_mana = snap.my_mana
            base_cost = t.cmc or 0
            x_budget = max(0, total_mana - base_cost)
            mult = (t.x_cost_data or {}).get('multiplier', 1) or 1
            effective_x = x_budget // mult
            killable = [c for c in opp.creatures
                        if (c.template.cmc or 0) <= effective_x]
            kill_count = len(killable)
            killable_power = sum((c.power or 0) for c in killable)
            desperate = snap.my_life <= 10
            if not desperate:
                if kill_count == 0:
                    return min(ev, -20.0)
                if kill_count == 1 and killable_power < 2:
                    return min(ev, -20.0)
            elif kill_count == 0:
                # Even desperate, zero kills is pure waste
                return min(ev, -20.0)

        # ── Blink/flicker hard gate: no legal target means the spell fizzles ──
        # Engine safely bails (Ephemerate returns early), but AI should never
        # score a mana-wasting fizzle as positive EV. Detect by oracle pattern
        # "target creature you control" on an instant/sorcery.
        oracle_lower_full = (t.oracle_text or '').lower()
        if ('blink' in tags or 'exile target creature you control' in oracle_lower_full) \
                and (t.is_instant or t.is_sorcery) \
                and len(me.creatures) == 0:
            return min(ev, -50.0)

        # ── Jeskai / blink M1 hold: prefer M2 blink so combat damage applies ──
        # If we hold a blink instant AND an ETB-value creature, and it is Main1,
        # slightly penalise the blink so the AI passes M1, swings, and casts in M2.
        if ('blink' in tags and (t.is_instant or t.is_sorcery)
                and game is not None
                and getattr(game, 'current_phase', None) is not None
                and 'MAIN1' in str(getattr(game, 'current_phase', ''))):
            etb_creatures = [c for c in me.creatures
                             if 'etb_value' in getattr(c.template, 'tags', set())]
            has_attackers = any(not getattr(c, 'summoning_sick', False)
                                and not getattr(c, 'tapped', False)
                                for c in me.creatures)
            if etb_creatures and has_attackers:
                ev -= 2.0  # wait for M2 so we keep combat damage

        # ── Noncreature-only counter dead vs creature-heavy opponents ──
        # Dovin's Veto / Negate can't target creature spells.
        # Gate positive EV when opponent's board is all creatures and hand
        # is likely all creatures too (aggro decks like Boros).
        oracle_lower = (t.oracle_text or '').lower()
        if ('counterspell' in tags and 'noncreature' in oracle_lower
                and snap.opp_creature_count >= 2
                and snap.opp_power >= 4
                and snap.opp_hand_size <= 3):
            # Opponent is an aggro deck running out of cards — counter is dead
            ev = min(ev, -3.0)

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
                    ev += premium * 0.5
                    if (t.cmc or 0) <= 1:
                        ev += 1.0

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
        if p.holdback_applies and not t.is_instant and not t.has_flash:
            ev += self._holdback_penalty(
                me, opp, snap, cost=t.cmc or 0,
                exclude_instance_id=card.instance_id)

        oracle_lower = (t.oracle_text or '').lower()
        phyrexian_count = oracle_lower.count('/p}')
        if phyrexian_count > 0:
            life_cost = phyrexian_count * 2
            ev -= life_cost / max(1, snap.my_life) * 10.0

        return ev

    def _holdback_penalty(self, me, opp, snap: EVSnapshot, cost: int,
                          exclude_instance_id: Optional[int] = None) -> float:
        """Mana-holdback penalty for a play that would tap out (Bundle 3).

        A1 — penalty scales by `counter_count × counter_cmc × opp_threat_prob`
              instead of the previous flat -2.0 so it actually gates a
              CMC-2 main-phase play when 2× Counterspell are held.
        A3 — extracted as a helper so `_score_cycling` and
              `_consider_equip` apply the same gate.
        A4 — opponent-spell-deck branch threshold kept at
              `opp_hand_size >= 4`. Iteration-2 revert: the Bundle 3
              lowered threshold (>=3) over-fired because 3-card post-
              discard hands are typically mostly lands, not threats.
              The stricter >=4 threshold matches pre-Bundle-3 behaviour
              and restores defender deployment.
        A5 — colored-aware: if the play taps out the LAST source of a
              held instant's color, the penalty is amplified (the
              interaction is uncastable, not merely tempo-delayed).

        Returns a non-positive penalty (0.0 means "no holdback").
        """
        p = self.profile
        if not p.holdback_applies:
            return 0.0

        # ── Holdback relevance gate ──────────────────────────────────
        # Original logic kept: opp creatures present OR opponent has a
        # full grip. Iteration-2 B3-Tune reverts A4's >=3 lowering back
        # to >=4: 3-card post-discard hands are typically mostly lands
        # (no real threat density) and the broader gate caused defender
        # decks to stall out against discard-heavy opponents.
        opp_has_spells = snap.opp_hand_size >= 4 and snap.opp_power == 0
        holdback_relevant = (snap.opp_power > 0
                             or snap.opp_hand_size >= 4
                             or opp_has_spells)
        if not holdback_relevant:
            return 0.0

        # ── Find held instant-speed interaction in hand ──────────────
        # Oracle/tag-driven (no card names). counter_cmc is the average
        # cost of held interaction — used to size the penalty.
        held_costs: list = []
        held_colors: set = set()
        for c in me.hand:
            if exclude_instance_id is not None \
                    and c.instance_id == exclude_instance_id:
                continue
            tmpl = c.template
            if not tmpl.is_instant:
                continue
            tags = getattr(tmpl, 'tags', set())
            if not ('removal' in tags or 'counterspell' in tags):
                continue
            held_costs.append(tmpl.cmc or 0)
            mc = tmpl.mana_cost
            for code, attr in (
                ('W', 'white'), ('U', 'blue'), ('B', 'black'),
                ('R', 'red'), ('G', 'green'),
            ):
                if getattr(mc, attr, 0) > 0:
                    held_colors.add(code)

        if not held_costs:
            return 0.0

        counter_count = len(held_costs)
        counter_cmc = sum(held_costs) / counter_count  # mean CMC

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

        # ── Lost-response-capacity model ─────────────────────────────
        # How many held responses could we cast with all our mana
        # untapped? With mana remaining after this play? The DELTA is
        # the capacity we'd lose by tapping out — that's what the
        # penalty pays for. Cheapest-first packing approximates the
        # opponent's worst-case sequence (we'd counter the cheapest
        # threats first to keep options open).
        sorted_costs = sorted(held_costs)
        def _capacity(mana: int) -> int:
            n, m = 0, mana
            for c in sorted_costs:
                if m >= c:
                    m -= c
                    n += 1
                else:
                    break
            return n

        cap_now = _capacity(snap.my_mana)
        cap_after = _capacity(max(0, snap.my_mana - cost))
        lost_capacity = max(0, cap_now - cap_after)

        # Penalty fires only when tapping out actually loses response
        # capacity — if we still have enough mana for every held
        # response, holdback is moot.
        if lost_capacity <= 0:
            return 0.0

        # Scale: counter_count × counter_cmc × opp_threat_prob ×
        # HELD_RESPONSE_VALUE_PER_CMC. Brief A1 specifies count (not
        # lost_capacity) — holding more counters means more value at
        # risk even if only one capacity is lost this turn (the second
        # counter still wants the mana on a future turn).
        # Iteration-2 B3-Tune: coefficient lowered 7.0 → 4.0. The
        # Bundle-3 value of 7.0 was calibrated against 2× Counterspell
        # held (2×2×1×7 = 28, gates a +20 EV play), but the single-
        # counter case (1×2×1×7 = 14) floored ordinary main-phase
        # plays, triggering a measurable defender-collapse in N=50
        # matrix (Jeskai -5pp, Dimir -6pp, AzCon WST -8pp after the
        # surrounding Affinity session fixes shipped). 4.0 is derived
        # from CONTROL's pass_threshold = -5.0: with 1 counter × 2
        # CMC × threat_prob 1.0 × 4.0 = -8 the gate still blocks a +5
        # main-phase play, but a +10 draw engine (EV 10 − 8 = +2 >
        # -5.0) remains castable. 2× Counterspell still scales to
        # 2×2×1×4 = -16 which keeps the Bundle-3 intent intact.
        HELD_RESPONSE_VALUE_PER_CMC = 4.0
        base_penalty = (counter_count * counter_cmc
                        * opp_threat_prob
                        * HELD_RESPONSE_VALUE_PER_CMC)

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
            # capacity penalty (same scale as base_penalty above —
            # uses the same Iteration-2 tuned coefficient of 4.0).
            base_penalty += (color_kills * counter_cmc
                             * opp_threat_prob
                             * HELD_RESPONSE_VALUE_PER_CMC)

        return -base_penalty

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
                # density). We only have direct removal/counter beliefs
                # here, so use them as a lower bound and add hand-size
                # weight for non-tracked threats.
                p_action = max(bhi.beliefs.p_removal,
                               bhi.beliefs.p_counter,
                               bhi.beliefs.p_burn)
                hand_factor = min(1.0, snap.opp_hand_size / 7.0)
                return max(0.1, min(1.0, p_action + 0.5 * hand_factor))
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
        hand_signal = min(1.0, snap.opp_hand_size / 7.0)
        clock_signal = 0.0
        if snap.my_life > 0 and snap.opp_power > 0:
            clock_signal = min(1.0, snap.opp_power / max(1, snap.my_life))
        return max(0.1, min(1.0,
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
        LAND_BASE_EV = 10.0              # mid-range of the spell EV scale
        LAND_UNTAPPED_USEFUL = 5.0       # land pays off this turn
        LAND_UNTAPPED_IDLE = 2.0         # land is stored mana, not spent now
        LAND_TAPPED_STALL = 10.0         # tapped-with-1-drop-in-hand: lose entire T1 tempo
        LAND_TAPPED_MINOR = 3.0          # tapped when we can still play most plays
        BOUNCE_LAND_AMULET_MANA = 8.0    # bounce+Amulet loop = +1 land-equiv mana / turn × residency
        RAMP_TO_BIG_NOW = 12.0           # this land lets us cast a 6+ CMC spell THIS turn
        RAMP_TO_BIG_SOON = 4.0           # on-curve ramp to big next turn
        COLOR_ENABLES_SPELL = 3.0        # each specific spell unlocked by a new colour
        NEW_COLOR_GENERIC = 4.0          # each colour added to mana base when hand needs it
        FETCH_FLEXIBILITY = 3.0          # fetch = choice of colour next turn

        ev = LAND_BASE_EV

        current_untapped = len(me.untapped_lands)
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

        effectively_tapped = land.template.enters_tapped and not has_untap_enabler
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
            (c for c in me.hand if c.template.is_creature and (c.template.cmc or 0) >= 6),
            None)
        if high_cmc_creature:
            target_cmc = high_cmc_creature.template.cmc or 6
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
        from engine.card_database import FETCH_LAND_COLORS
        is_fetch = land.name in FETCH_LAND_COLORS
        # Use FETCH_LAND_COLORS for fetch lands — template.produces_mana is not
        # reliably populated on CardInstances in game context for fetches
        land_produces = set(FETCH_LAND_COLORS[land.name]) if is_fetch else set(land.template.produces_mana)

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
            synergy_signals = 0
            for c in list(me.hand) + list(me.battlefield):
                if c is land:
                    continue
                c_oracle = (c.template.oracle_text or '').lower()
                if ('for each artifact' in c_oracle
                        or 'metalcraft' in c_oracle
                        or 'affinity for artifacts' in c_oracle):
                    synergy_signals += 1
            if synergy_signals > 0:
                # Rules constant: 1 power (or 1 mana) gained per synergy
                # card × residency × mana_clock_impact × 20 ≈ 4.
                SYNERGY_ARTIFACT_BONUS = 4.0
                ev += synergy_signals * SYNERGY_ARTIFACT_BONUS

        # Landfall: each trigger ≈ ETB effect value (life, damage, ramp)
        landfall_count = sum(1 for c in me.battlefield
                             if 'landfall' in (c.template.oracle_text or '').lower())
        if landfall_count > 0:
            triggers = 2 if is_fetch else 1
            ev += landfall_count * triggers * 3.0

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
                # Rules constants:
                #   TRON_MANA_ADVANTAGE: completed Tron = 7 colorless mana
                #     from 3 lands vs 3 from vanilla lands = +4/turn.
                #   TRON_ASSEMBLY_COST: approximate mana investment to
                #     continue playing Tron lands — no magic discount.
                TRON_MANA_ADVANTAGE = 4.0
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
                    expected_turns = 8.0  # rules constant: Modern avg game length
                expected_turns = max(2.0, min(expected_turns, 10.0))
                # Mana-clock impact gives value per point of mana advantage.
                mana_impact = mana_clock_impact(snap)
                completed_value = (TRON_MANA_ADVANTAGE * expected_turns
                                   * mana_impact * 20.0)
                # 20.0 scales mana_clock_impact (1/opp_life ~= 0.05) back to
                # board-eval units — same convention as creature_value().
                #
                # P(find missing piece(s) in remaining turns) from actual
                # library composition: count Tron pieces in library + tutors
                # (Sylvan Scrying, Expedition Map). No hardcoded magic.
                missing = 3 - after_count
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
        current_mana = len(me.untapped_lands) + me.mana_pool.total() + me._tron_mana_bonus()
        for spell in me.hand:
            if spell.template.is_land:
                continue
            oracle = (spell.template.oracle_text or '').lower()
            if 'landfall' not in oracle:
                continue
            if game.can_cast(self.player_idx, spell):
                ev -= 12.0  # defer land so creature resolves first
                break

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
        def _is_reanimate(oracle: str) -> bool:
            o = (oracle or '').lower()
            if 'from' not in o or 'graveyard' not in o:
                return False
            if ('return' in o and 'battlefield' in o) or (
                    'put' in o and 'battlefield' in o):
                # Exclude "from your hand ... to the battlefield"
                # (e.g., Reanimate vs Knight Errant): require
                # 'graveyard' to precede 'battlefield'.
                gy_idx = o.find('graveyard')
                bf_idx = o.find('battlefield', gy_idx)
                return gy_idx >= 0 and bf_idx >= 0
            return False

        zones = [me.hand, me.battlefield]
        # Library visibility is a simplification — in real play we
        # know our deck.  DeckKnowledge provides it when initialised.
        if self._dk is not None:
            zones.append(me.library)
        for zone in zones:
            for c in zone:
                if _is_reanimate(c.template.oracle_text):
                    self._reanimation_cache_turn = (
                        game.turn_number if game else -1)
                    self._reanimation_cache_val = True
                    return True

        self._reanimation_cache_turn = game.turn_number if game else -1
        self._reanimation_cache_val = False
        return False

    def _score_cycling(self, card, snap, game, me, opp) -> float:
        """Score cycling using clock-derived values.

        Cycling = draw 1 card + put creature in GY (for Living End).
        Constants calibrated so cycling outscores creature-casting when
        the gameplan requires GY filling before cascade.
        """
        # EV scaling constants (tuned against creature cast EV of ~15-20)
        CYCLING_CASCADE_BOOST = 8.0   # cascade in hand: cycling is primary action
        CYCLING_GY_URGENCY = 6.0      # GY < 3 creatures: need more before cascade
        CYCLING_GAMEPLAN_BOOST = 10.0  # gameplan says prefer_cycling
        CYCLING_FREE_COST_BONUS = 2.0  # pay life instead of mana
        CYCLING_CHEAP_COST_BONUS = 1.0 # mana cost <= 1

        from ai.clock import card_clock_impact

        # Drawing a card: future clock change
        ev = card_clock_impact(snap) * 20.0  # scale to match spell scores

        # Cycling creatures into GY: Living End-style reanimation gameplan.
        # Design: docs/design/ev_correctness_overhaul.md §2.E — the
        # "creature in graveyard = future reanimation target" bonus fires
        # ONLY when the deck has a visible reanimation path.  A dead
        # creature in Boros Energy's graveyard is not equity.
        if card.template.is_creature and self._has_reanimation_path(game, me):
            power = card.template.power or 0
            # Creature in GY = future reanimation target
            ev += (4.0 + power * 0.5)

        # Cycling cost: cheaper = better tempo
        cost_data = card.template.cycling_cost_data
        if cost_data:
            if cost_data.get('life', 0) > 0:
                ev += CYCLING_FREE_COST_BONUS  # free cycling (pay life instead of mana)
            elif cost_data.get('mana', 0) <= 1:
                ev += CYCLING_CHEAP_COST_BONUS  # cheap cycling

        # Cascade in hand: filling GY is urgent — MUST cycle before cascade
        has_cascade = any(getattr(c.template, 'is_cascade', False) for c in me.hand
                         if not c.template.is_land)
        if has_cascade:
            ev += CYCLING_CASCADE_BOOST  # cycling is the primary action before cascade
            # Count creatures already in GY — less urgency if GY is full
            from ai.predicates import count_gy_creatures
            gy_creatures = count_gy_creatures(me.graveyard)
            if gy_creatures < 3:
                ev += CYCLING_GY_URGENCY  # urgent: need more GY creatures before cascading

        # Gameplan prefer_cycling: massive boost (Living End, etc.)
        if self.goal_engine:
            current_goal = self.goal_engine.current_goal
            if current_goal and getattr(current_goal, 'prefer_cycling', False):
                ev += CYCLING_GAMEPLAN_BOOST  # cycling is THE gameplan, not optional

        # Bundle 3 A3 — same holdback gate as _score_spell. Cycling
        # taps lands too; it must respect held instant-speed interaction.
        cost_data = card.template.cycling_cost_data
        cycling_mana_cost = cost_data.get('mana', 0) if cost_data else 0
        ev += self._holdback_penalty(
            me, opp, snap, cost=cycling_mana_cost,
            exclude_instance_id=card.instance_id)

        return ev

    def _best_removal_target_value(self, removal, game, opp) -> float:
        """Find the most valuable creature this removal can kill.

        Accounts for mana efficiency: cheap removal on cheap threats
        is better than expensive removal on cheap threats.

        Uses creature_threat_value (oracle-driven, accounts for scaling/attack triggers)
        instead of creature_value (raw clock impact). This ensures Affinity Constructs
        with artifact affinity and other threat amplifiers are correctly prioritized.
        """
        if not opp.creatures:
            return 0.0
        removal_cmc = removal.template.cmc or 0
        best = 0.0
        for c in opp.creatures:
            val = creature_threat_value(c)
            # Penalize overkill: using 5-mana removal on a 1/1 is wasteful
            target_cmc = c.template.cmc or 0
            if removal_cmc > target_cmc + self.profile.removal_overkill_cmc_diff:
                val *= self.profile.removal_overkill_mult  # overkill penalty for inefficient removal
            if val > best:
                best = val
        return best

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
        for creature in valid:
            oracle = (creature.template.oracle_text or "").lower()
            if "discard a card" in oracle and "+1/+1" in oracle:
                prof = self.profile
                # Smart discard: protect removal/counters, discard excess lands/dupes/uncastable
                hand_lands = [c for c in me.hand if c.template.is_land]
                hand_spells = [c for c in me.hand if not c.template.is_land]
                board_names = {c.name for c in me.battlefield}
                protect_tags = {'removal', 'counterspell'}
                discardable = []

                # 1. Excess lands (keep 1 for next land drop)
                if len(hand_lands) >= 2 and len(me.lands) >= 3:
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
                if len(hand_spells) >= 3:
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
                            creature.plus_counters += 1
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
            oracle = (c.template.oracle_text or '').lower()
            # Damage-on-hit triggers (Ragavan, etc.)
            if 'combat damage to a player' in oracle:
                return True
            # On-attack triggers: battle cry, tapping permanents, draining, etc.
            # Use 'whenever this creature attacks' to avoid false positives like
            # 'whenever a creature attacks you, gain 1 life'.
            if 'whenever this creature attacks' in oracle:
                return True
            # Some legends use their own name: 'whenever [Name] attacks'
            # Detect by checking if the card name appears before 'attacks'
            cname = (c.template.name or '').lower().split(' //')[0].strip()
            if cname and f'whenever {cname} attacks' in oracle:
                return True
            return False
        total_power = sum(c.power for c in valid if (c.power or 0) > 0)
        if total_power >= opp.life:
            return [c for c in valid if _has_combat_value(c)]

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
            # A creature with a triggered combat-damage ability (oracle-detected)
            # has value even at 0 power (e.g. future designs). Pure 0-power
            # creatures with no such trigger are excluded from free_attackers.
            _oracle = (c.template.oracle_text or '').lower()
            _cname = (c.template.name or '').lower().split(' //')[0].strip()
            has_combat_trigger = (
                'combat damage to a player' in _oracle
                or 'whenever this creature attacks' in _oracle
                or (_cname and f'whenever {_cname} attacks' in _oracle)
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
            if deals_damage and (not can_die_to_block or is_evasive):
                free_attackers.append(c)
            else:
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
        is_desperate = me.life <= 6 and total_power > 0 and opp.life > 0

        # ── Anti-combo: vs spell-based decks, creature attacks are always right ──
        opp_is_spell_deck = opp_archetype in ('combo', 'storm')

        # CombatPlanner
        try:
            vboard = extract_virtual_board(game, self.player_idx)
            attack_plan, score_delta = self.combat_planner.plan_attack(vboard)

            threshold = self.profile.attack_threshold
            # When opponent is low, attack more aggressively to close the game
            if opp.life <= self.profile.burn_low_life_threshold and self.archetype in ('aggro', 'tempo'):
                threshold -= self.profile.aggro_closing_threshold_reduction

            # Post-board-refill aggression: Living End just resolved, opponent's
            # board was wiped, and our creatures came back (still summoning
            # sick this turn — they must wait until next turn to attack). On
            # that next turn, swing with everything to cash in the tempo swing.
            if getattr(me, 'aggression_boost_turns', 0) > 0:
                threshold -= 2.0

            # Racing: when we can kill in ~2 swings, be aggressive
            if is_racing:
                threshold -= 2.0

            # Anti-combo: opponent won't block with creatures, so attacks are free
            if opp_is_spell_deck:
                threshold -= 3.0

            # Bonus EV for combat damage / attack triggers the planner doesn't model
            trigger_bonus = 0.0
            if attack_plan:
                for vc in attack_plan:
                    c_oracle = (getattr(vc, 'oracle', None) or '').lower()
                    if 'combat damage to a player' in c_oracle:
                        trigger_bonus += 1.5  # Ragavan: Treasure + exile ≈ 1.5 EV
                    if 'whenever' in c_oracle and 'attacks' in c_oracle and '{e}' in c_oracle:
                        trigger_bonus += 0.5  # Guide of Souls energy

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
            c_oracle = (c.template.oracle_text or "").lower()
            has_combat_trigger = 'combat damage to a player' in c_oracle
            if has_combat_trigger and (c.power or 0) > 0:
                # e.g. Ragavan: attack if our power kills their best blocker (even trade gains trigger)
                killable = [b for b in opp_blockers if (c.power or 0) >= (b.toughness or 0)]
                if killable:
                    safe.append(c)

        # If racing, desperate, or vs combo, send everything even if risky.
        # Still exclude 0-power non-trigger creatures — they add no damage.
        if (is_racing or is_desperate or opp_is_spell_deck) and valid:
            return [c for c in valid if _has_combat_value(c)]

        return safe if safe else []

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
        oracle = (t.oracle_text or '').lower()
        if 'escape—' in oracle:  # em-dash U+2014
            return True
        if 'whenever this creature attacks' in oracle:
            return True
        name = (t.name or '').lower().split(' //')[0].strip()
        if name and f'whenever {name} attacks' in oracle:
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
            oracle = (card.template.oracle_text or '').lower()
            if 'removal' in tags and (
                'destroy target artifact' in oracle
                or 'destroy target enchantment' in oracle
                or 'destroy target nonland permanent' in oracle
                or 'destroy all artifacts' in oracle
            ):
                return True
        return False

    def decide_blockers(self, game, attackers) -> Dict[int, List[int]]:
        """Decide blocking assignments."""
        from ai.board_eval import evaluate_action, Action, ActionType
        from engine.cards import Keyword

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

        # EMERGENCY: block when incoming damage is dangerous
        # Triggers: lethal this turn, drop-below-5, or projected lethal across 2 turns
        # (the old single-attacker heuristic treated a 10/10 at life=20 as an emergency;
        #  replaced with a lookahead that only fires when next-turn math is lethal too).
        emergency = (total_incoming >= me.life
                     or (me.life - total_incoming <= 5 and total_incoming >= 3)
                     or self._two_turn_lethal(game, me, opp, attackers))
        if emergency:
            emergency_blocks: Dict[int, List[int]] = {}
            e_used: Set[int] = set()
            # Block biggest attackers with smallest blockers.
            # Two-pass: first try non-battle-cry, non-protected blockers;
            # fall back only if they are the only option. Preserves attack
            # amplification AND shields planeswalkers / escape creatures.
            def _blocker_candidates(attacker, excl):
                cands = []
                for b in valid_blockers:
                    if b.instance_id in excl:
                        continue
                    if Keyword.FLYING in attacker.keywords:
                        if (Keyword.FLYING not in b.keywords and
                                Keyword.REACH not in b.keywords):
                            continue
                    cands.append(b)
                unprotected = [b for b in cands
                               if not self._is_protected_piece(b)]
                return unprotected if unprotected else cands

            def _is_battle_cry(b):
                bo = (b.template.oracle_text or '').lower()
                return 'whenever this creature attacks' in bo

            sacrificed_value = 0.0
            plating_skipped_any = False
            for attacker in sorted(attackers, key=lambda a: a.power or 0, reverse=True):
                # RC-2: if this attacker's power is dominated by equipment/aura
                # bonuses AND we can't remove those next turn, chumping is
                # futile — the plating rebinds. Skip unless skipping is lethal.
                equip_bonus = self._attacker_equipment_bonus(game, opp, attacker)
                damage_so_far = sum(
                    (a.power or 0) for a in attackers
                    if a.instance_id in emergency_blocks
                )
                damage_if_skipped = total_incoming - damage_so_far
                still_lethal_if_skipped = damage_if_skipped >= me.life
                if (equip_bonus >= 3
                        and not self._equipment_breakable(game, me)
                        and not still_lethal_if_skipped):
                    plating_skipped_any = True
                    continue

                best_chump = None
                best_chump_val = 999
                cands = _blocker_candidates(attacker, e_used)
                # Prefer non-battle-cry blockers; only use battle cry sources as last resort
                non_bc = [b for b in cands if not _is_battle_cry(b)]
                pool = non_bc if non_bc else cands
                for blocker in pool:
                    val = creature_value(blocker)
                    if val < best_chump_val:
                        best_chump_val = val
                        best_chump = blocker
                if best_chump:
                    emergency_blocks[attacker.instance_id] = [best_chump.instance_id]
                    e_used.add(best_chump.instance_id)
                    sacrificed_value += creature_value(best_chump)
                    # Check if we've blocked enough to survive/stabilize
                    blocked_damage = sum(
                        a.power or 0 for a in attackers if a.instance_id in emergency_blocks
                    )
                    remaining = total_incoming - blocked_damage
                    # Portfolio cap: stop if we've sacrificed more creature_value than
                    # the damage we'd otherwise take. (Unless still lethal — that's
                    # handled by the stabilized check below which requires remaining<life.)
                    still_lethal = remaining >= me.life
                    if (not still_lethal
                            and sacrificed_value > max(remaining, 1.0)):
                        break
                    if remaining < me.life and (me.life - remaining > 5 or remaining == 0):
                        break  # stabilized
            # RC-2: if the emergency path skipped every attacker via the
            # plating-futile gate and assigned no blocks, accept the damage
            # rather than letting the non-emergency path re-block the same
            # plated attackers. Only triggers when skipping is not lethal.
            if emergency and not emergency_blocks and plating_skipped_any:
                return {}

            if emergency_blocks:
                # Log emergency blocking assignments with reasoning
                id_to_attacker = {a.instance_id: a for a in attackers}
                id_to_blocker  = {b.instance_id: b for b in valid_blockers}
                for atk_id, blk_ids in emergency_blocks.items():
                    atk = id_to_attacker.get(atk_id)
                    for blk_id in blk_ids:
                        blk = id_to_blocker.get(blk_id)
                        if atk and blk:
                            a_pow = atk.power or 0
                            b_pow = blk.power or 0
                            b_tou = blk.toughness or 0
                            a_tou = atk.toughness or 0
                            survives = a_pow < b_tou
                            kills    = b_pow >= a_tou
                            if kills and survives:
                                reason = "favorable trade"
                            elif kills:
                                reason = "trade (chump)"
                            else:
                                reason = "chump block"
                            game.log.append(
                                f"T{game.display_turn} P{self.player_idx+1}: "                                f"  [BLOCK-EMERGENCY] {blk.name} ({b_pow}/{b_tou}) "                                f"blocks {atk.name} ({a_pow}/{a_tou}) — {reason}"
                            )
                return emergency_blocks

        blocks: Dict[int, List[int]] = {}
        used: Set[int] = set()

        sorted_attackers = sorted(attackers, key=lambda a: a.power or 0, reverse=True)

        for attacker in sorted_attackers:
            best_blocker = None
            best_val = 0.0

            for blocker in valid_blockers:
                if blocker.instance_id in used:
                    continue
                if Keyword.FLYING in attacker.keywords:
                    if (Keyword.FLYING not in blocker.keywords and
                            Keyword.REACH not in blocker.keywords):
                        continue

                # Non-emergency filter: skip useless blocks.
                # (1) 0-power blocker that can't kill the attacker — saves face damage
                #     but costs a permanent with 0 combat equity.
                # (2) Battle-cry source — its ongoing attack value exceeds the damage saved
                #     unless the block is a clean kill.
                b_pow = blocker.power or 0
                a_tou = attacker.toughness or 0
                can_kill_attacker = b_pow >= a_tou or Keyword.DEATHTOUCH in blocker.keywords
                b_oracle = (blocker.template.oracle_text or '').lower()
                b_cname = (blocker.template.name or '').lower().split(' //')[0].strip()
                is_battle_cry = ('whenever this creature attacks' in b_oracle or
                                 (b_cname and f'whenever {b_cname} attacks' in b_oracle))
                if not can_kill_attacker:
                    if b_pow == 0:
                        continue  # 0-power non-kill = pure waste
                    if is_battle_cry:
                        continue  # battle cry source worth more attacking than chump-blocking
                    # (4) Protected pieces (planeswalkers, escape creatures)
                    #     — their persistent value exceeds single-turn damage
                    #     saved by chumping.
                    if self._is_protected_piece(blocker):
                        continue

                val = evaluate_action(
                    game, self.player_idx,
                    Action(ActionType.BLOCK, {'attacker': attacker, 'blocker': blocker}))
                if val > best_val:
                    best_val = val
                    best_blocker = blocker

            if best_blocker:
                blocks[attacker.instance_id] = [best_blocker.instance_id]
                used.add(best_blocker.instance_id)

                # Double-block if needed
                a_tough = attacker.toughness or 0
                b_power = best_blocker.power or 0
                if b_power < a_tough and Keyword.DEATHTOUCH not in best_blocker.keywords:
                    for b2 in valid_blockers:
                        if b2.instance_id in used:
                            continue
                        if Keyword.FLYING in attacker.keywords:
                            if (Keyword.FLYING not in b2.keywords and
                                    Keyword.REACH not in b2.keywords):
                                continue
                        if b_power + (b2.power or 0) >= a_tough:
                            blocks[attacker.instance_id].append(b2.instance_id)
                            used.add(b2.instance_id)
                            break

        # Log normal blocking assignments with reasoning
        if blocks:
            id_to_attacker = {a.instance_id: a for a in attackers}
            id_to_blocker  = {b.instance_id: b for b in valid_blockers}
            for atk_id, blk_ids in blocks.items():
                atk = id_to_attacker.get(atk_id)
                for blk_id in blk_ids:
                    blk = id_to_blocker.get(blk_id)
                    if atk and blk:
                        a_pow = atk.power or 0
                        b_pow = blk.power or 0
                        b_tou = blk.toughness or 0
                        a_tou = atk.toughness or 0
                        survives = a_pow < b_tou
                        kills    = b_pow >= a_tou
                        if kills and survives:
                            reason = "favorable trade"
                        elif kills:
                            reason = "trade (chump)"
                        else:
                            reason = "chump block"
                        game.log.append(
                            f"T{game.display_turn} P{self.player_idx+1}: "                            f"  [BLOCK] {blk.name} ({b_pow}/{b_tou}) "                            f"blocks {atk.name} ({a_pow}/{a_tou}) — {reason}"
                        )
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

    def _choose_targets(self, game, spell) -> List[int]:
        """Choose targets for a spell."""
        from ai.ev_evaluator import snapshot_from_game
        t = spell.template
        tags = getattr(t, 'tags', set())
        opp = game.players[1 - self.player_idx]
        # Live snapshot so creature_value / threat_value reflect actual
        # board state, not a blank default board.
        snap = snapshot_from_game(game, self.player_idx)

        # Burn spells FIRST — they can always target face as fallback
        from decks.card_knowledge_loader import get_burn_damage
        from engine.cards import Keyword as Kw2
        dmg = get_burn_damage(t.name)
        # Storm spells (Grapeshot) deal 1 damage × storm copies — always target face
        if Kw2.STORM in getattr(t, 'keywords', set()) and 'removal' in tags:
            return [-1]  # Grapeshot always goes face (storm copies auto-target)
        if dmg > 0:
            if dmg >= opp.life:
                return [-1]  # face = lethal, always go face

            # Pick the highest-threat killable creature via the marginal-
            # contribution formula: threat(c) = V_opp(B) - V_opp(B \ {c}).
            # Scaling (equipment bonuses, "for each artifact" bonuses,
            # synergy-denial) falls out naturally — removing a key enabler
            # strips every dependent bonus, which shows up as a larger
            # position-value drop. No per-synergy bolt-on, no battle-cry
            # premium, no archetype detection; the threat formula decides.
            from ai.permanent_threat import permanent_threat
            best_kill_val = 0.0
            best_kill_id = None
            best_kill_why = ""
            if opp.creatures:
                for c in opp.creatures:
                    remaining_toughness = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
                    if dmg >= remaining_toughness > 0 or remaining_toughness <= 0:
                        val = permanent_threat(c, opp, game)
                        # Equipment carrier bonus: if `c` is wearing
                        # equipment, killing it strips the equipment
                        # off (CR 702.6e) and forces opp to re-equip
                        # at sorcery speed.  Same bonus the response-
                        # path target picker (`_pick_best_removal_target`)
                        # applies; propagating to the burn-vs-creature
                        # decision so a Plating-equipped Memnite at 1
                        # toughness gets correctly prioritised over
                        # 3 face damage.
                        val += self._carrier_disrupt_bonus(
                            game, opp, c, snap,
                            removal_destroys_artifact=False)
                        if val > best_kill_val:
                            best_kill_val = val
                            best_kill_id = c.instance_id
                            best_kill_why = (f"marginal threat {val:.1f} "
                                             f"({c.power}/{c.toughness} body)")

            # Compare: is killing a creature worth more than face damage?
            face_val = dmg * self.profile.burn_face_mult
            if opp.life <= self.profile.burn_low_life_threshold:
                face_val = dmg * self.profile.burn_face_low_life_mult
            # Don't burn face with no board presence unless opponent is low
            me = game.players[self.player_idx]
            if not me.creatures and opp.life > self.profile.burn_low_life_threshold:
                face_val *= 0.1  # near-zero value without a clock

            # Prefer removing big creatures unless burn is near-lethal
            if best_kill_id and best_kill_val > face_val:
                _c = next((c for c in opp.creatures if c.instance_id == best_kill_id), None)
                self._last_target_reason = (f"→ {_c.name if _c else '?'}: "
                    f"{best_kill_why or 'killable'} — better than {dmg} face dmg")
                return [best_kill_id]  # kill the creature
            if best_kill_id:
                best_kill_card = next((c for c in opp.creatures
                                       if c.instance_id == best_kill_id), None)
                if (best_kill_card
                        and (best_kill_card.power or 0) >= self.profile.burn_kill_min_power
                        and opp.life > dmg * self.profile.burn_kill_life_ratio):
                    self._last_target_reason = (f"→ {best_kill_card.name}: "
                        f"({best_kill_why or self._target_why(best_kill_card)}) — life safe")
            _why_face = []
            if opp.life <= getattr(getattr(self, "profile", None), "burn_low_life_threshold", 8):
                _why_face.append("opponent low life")
            elif not game.players[self.player_idx].creatures:
                _why_face.append("no clock yet — build pressure")
            elif not best_kill_id:
                _why_face.append("no killable target")
            else:
                _why_face.append("face damage worth more")
            self._last_target_reason = f"→ face ({dmg} dmg, life {opp.life} → {opp.life - dmg}): {_why_face[0]}"
            return [-1]  # go face

        # Removal (non-burn): target best opponent permanent
        # For creature-only removal: pick best creature
        # For "nonland permanent" removal: consider artifacts/enchantments too
        if 'removal' in tags and 'board_wipe' not in tags:
            oracle = (spell.template.oracle_text or '').lower()
            can_hit_noncreature = ('nonland permanent' in oracle
                                   or 'nonland' in oracle
                                   or 'permanent' in oracle
                                   or 'artifact' in oracle)

            if can_hit_noncreature:
                # Evaluate all nonland permanents via marginal threat.
                from ai.permanent_threat import permanent_threat
                nonland = [c for c in opp.battlefield if not c.template.is_land]
                if nonland:
                    best = max(nonland,
                               key=lambda c: permanent_threat(c, opp, game))
                    return [best.instance_id]
                return []
            else:
                # Creature-only removal
                if opp.creatures:
                    best = max(opp.creatures, key=lambda c: creature_value(c, snap))
                    return [best.instance_id]
                return []

        # Exile effects (March of Otherworldly Light, etc.): target best nonland permanent
        oracle = (spell.template.oracle_text or '').lower()
        if 'exile target' in oracle:
            from engine.cards import CardType
            nonland = [c for c in opp.battlefield if not c.template.is_land]
            if nonland:
                best = max(nonland, key=lambda c: c.template.cmc)
                return [best.instance_id]
            return []

        # Blink effects: target our best ETB creature, fall back to any creature
        if 'blink' in tags:
            me = game.players[self.player_idx]
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
        oracle = ((card.template.oracle_text if card.template else '') or '').lower()
        also_destroys_artifact = (
            'destroy target artifact' in oracle
            or 'destroy target nonland permanent' in oracle
            or 'destroy target permanent' in oracle
        )

        def _rank(c) -> float:
            base = creature_threat_value(c, snap)
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
        ) * 20.0
        # If removal also destroys the equipment, pump is permanently
        # denied — double-count to reflect the multi-turn loss.
        if removal_destroys_artifact:
            pump_impact *= 2.0

        # Re-equip tempo: mana spent on a sorcery-speed ability is
        # mana not available for a spell that turn.
        mana_tempo = equip_cost_total * mana_clock_impact(snap) * 20.0

        return pump_impact + mana_tempo

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

        for c in opp.creatures:
            if dmg > 0:
                remaining = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
                if remaining > dmg:
                    continue
            if creature_threat_value(c, snap) >= floor:
                return True

        oracle = (spell.template.oracle_text or '').lower()
        hits_noncreature = ('target artifact' in oracle
                            or 'target enchantment' in oracle
                            or 'target nonland permanent' in oracle
                            or 'target noncreature' in oracle
                            or 'target permanent' in oracle)
        if hits_noncreature:
            from ai.permanent_threat import permanent_threat
            for perm in opp.battlefield:
                if perm.template.is_land or perm.template.is_creature:
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

        # Modal spells with draw mode don't require targets (can choose draw)
        oracle = (t.oracle_text or '').lower()
        if 'counterspell' in tags:
            if 'draw' in oracle and ('choose' in oracle or '•' in oracle):
                return False  # modal spell with draw mode (Archmage's Charm)
            return True
        if 'removal' in tags and 'board_wipe' not in tags:
            return True
        # Exile effects that target opponent's permanents (March of Otherworldly Light, etc.)
        if 'exile target' in oracle and ('artifact' in oracle or 'creature' in oracle or 'permanent' in oracle):
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
        available_mana = (len(player.untapped_lands)
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
                exclude_instance_id=equip.instance_id)

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
            if 'artifact and/or enchantment' in oracle or 'artifact or enchantment' in oracle:
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
