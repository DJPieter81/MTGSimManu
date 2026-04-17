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
                 'heuristic_ev', 'lookahead_ev', 'counter_pct', 'removal_pct', 'target_reason')

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

        # Mulligan decider — reuse existing
        from ai.mulligan import MulliganDecider
        from ai.strategy_profile import ArchetypeStrategy
        arch_enum = ArchetypeStrategy(self.archetype) if self.archetype in [e.value for e in ArchetypeStrategy] else ArchetypeStrategy.MIDRANGE
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
        if self.goal_engine and self.archetype == "combo":
            from ai.ev_evaluator import _estimate_combo_chain
            can_kill, storm_count, damage, chain = _estimate_combo_chain(
                game, self.player_idx)
            if can_kill or storm_count >= 5:
                # Force goal to last phase (EXECUTE_PAYOFF / CLOSE_GAME)
                while self.goal_engine.current_goal_idx < len(self.goal_engine.gameplan.goals) - 1:
                    self.goal_engine.advance_goal(game, f"Combo kill detected (storm={storm_count})")

        # REANIMATE PRIORITY OVERRIDE: if hand has reanimate spell AND
        # graveyard has a creature with power >= 5, force-cast it immediately
        reanimate_override = None
        from engine.cards import CardType
        gy_big = [c for c in me.graveyard
                  if CardType.CREATURE in c.template.card_types
                  and (c.template.power or 0) >= 5]
        if gy_big:
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
                    # Control patience: control archetypes hold reactive
                    # spells until there's *real* pressure (opp_clock <= 3).
                    # Without this, Orim's Chant / Prismatic Ending /
                    # Supreme Verdict get cast proactively on empty boards
                    # and wind up in losses.
                    if (prof.control_patience and not is_dying
                            and snap.opp_clock_discrete >= 4):
                        continue
                    if not is_dying and not has_big_target:
                        continue

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

        # Sort by EV, pick the best
        candidates.sort(key=lambda p: p.ev, reverse=True)
        self._last_candidates = candidates
        best = candidates[0]

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

        # ── Combo sequencing overlay ──
        ev += self._combo_modifier(card, snap, game, me, opp)

        # ── Amulet + Titan ramp combo ──
        # Generic detection: if we hold Primeval Titan (or any 6-mana "when
        # this creature enters, search for two lands" creature) AND this card
        # is Amulet of Vigor, the acceleration is enormous — each Amulet +
        # bounce-land loop effectively doubles our ramp, enabling Titan 1-2
        # turns earlier. `_combo_modifier` skips ramp archetypes, so wire it
        # here. Similarly bump the Titan itself when Amulet is already down.
        t_oracle = (t.oracle_text or '').lower()
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

        # ── Board wipe hard gate ──
        # Empty-board wrath is pure waste (we self-wipe for nothing). Mark
        # as structurally-rejected via a rules-constant sentinel below the
        # pass_threshold in any archetype profile.
        if 'board_wipe' in tags and snap.opp_creature_count == 0:
            WRATH_WASTE_SENTINEL = -50.0  # rules: forces pass under any profile
            return min(ev, WRATH_WASTE_SENTINEL)

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
        # real target-value comes from scaling equipment (CP, Nettlecyst)
        # or stax pieces. Use `_permanent_threat_value` — the same oracle
        # helper that drives `_choose_targets` — to score the best hit.
        # Detection is purely oracle-driven (target artifact/enchantment/
        # nonland permanent); no card names.
        if ('removal' in tags and not 'board_wipe' in tags
                and not t.is_creature):
            o_lower = (t.oracle_text or '').lower()
            hits_noncreature = ('target artifact' in o_lower
                                or 'target enchantment' in o_lower
                                or 'target nonland permanent' in o_lower
                                or 'target noncreature' in o_lower)
            if hits_noncreature:
                from engine.cards import CardType
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
                               key=lambda c: self._permanent_threat_value(c, opp, snap))
                    tv = self._permanent_threat_value(best, opp, snap)
                    # Scale roughly on par with the creature-threat overlay:
                    # CP on a 5-artifact board → tv=7 → +3.5 EV, which
                    # outranks a typical 2-CMC deploy.
                    ev += tv * 0.5

        # ── Mana holdback: penalize tapping out when we hold instants ──
        # Trigger holdback when: opp has creatures, OR opp is a spell/combo deck
        # with hand cards (holdback for counterspells even vs creatureless opponents)
        opp_has_spells = snap.opp_hand_size >= 3 and snap.opp_power == 0
        holdback_relevant = snap.opp_power > 0 or snap.opp_hand_size >= 4 or opp_has_spells
        if p.holdback_applies and holdback_relevant:
            cmc = t.cmc or 0
            has_instant = any(
                c.template.is_instant and (
                    'removal' in getattr(c.template, 'tags', set()) or
                    'counterspell' in getattr(c.template, 'tags', set())
                )
                for c in me.hand if c.instance_id != card.instance_id
            )
            if has_instant and not t.is_instant and not t.has_flash:
                remaining_mana = snap.my_mana - cmc
                if remaining_mana < 2:
                    ev -= 2.0  # tapping out loses instant-speed interaction

        oracle_lower = (t.oracle_text or '').lower()
        phyrexian_count = oracle_lower.count('/p}')
        if phyrexian_count > 0:
            life_cost = phyrexian_count * 2
            ev -= life_cost / max(1, snap.my_life) * 10.0

        return ev

    def _combo_modifier(self, card, snap: EVSnapshot,
                        game: "GameState", me, opp) -> float:
        """Combo chain sequencing — logic the projection can't capture.

        All values derived from clock impact:
        - Lethal storm = game over = max value
        - Fuel value = storm_count / opp_life (fraction of kill per spell)
        - Hold penalty = negative of going-off value (opportunity cost)
        """
        t = card.template
        tags = getattr(t, 'tags', set())
        p = self.profile

        if not p.has_combo_chain:
            return 0.0

        from engine.cards import Keyword as Kw
        mod = 0.0
        storm = me.spells_cast_this_turn
        mana = snap.my_mana
        opp_life = max(1, snap.opp_life)

        # Helper: count fuel sources and check for finisher/PiF access
        def _count_fuel():
            return sum(1 for c in me.hand if c.instance_id != card.instance_id
                       and not c.template.is_land
                       and any(ft in getattr(c.template, 'tags', set())
                               for ft in ('ritual', 'cantrip', 'draw')))

        def _has_finisher():
            return any(Kw.STORM in getattr(c.template, 'keywords', set())
                       or 'tutor' in getattr(c.template, 'tags', set())
                       for c in me.hand if c.instance_id != card.instance_id)

        def _has_flashback_combo():
            return any('flashback' in getattr(c.template, 'tags', set())
                       and 'combo' in getattr(c.template, 'tags', set())
                       for c in me.hand if c.instance_id != card.instance_id)

        # ── Storm patience: hold rituals at storm=0 until ready ──
        if p.storm_patience and storm == 0 and 'ritual' in tags:
            fuel = _count_fuel()
            reducers = sum(1 for c in me.battlefield
                           if 'cost_reducer' in getattr(c.template, 'tags', set()))
            has_pif = _has_flashback_combo()
            gy_fuel = 0
            if has_pif:
                gy_fuel = sum(1 for c in me.graveyard
                              if (c.template.is_instant or c.template.is_sorcery)
                              and 'ritual' in getattr(c.template, 'tags', set()))
            total_fuel = fuel + gy_fuel + 1
            has_finisher = _has_finisher()
            min_fuel = p.storm_min_fuel_to_go if reducers > 0 else p.storm_min_fuel_to_go + 2

            # Draw spells (Reckless Impulse, Wrenn's Resolve) — note they
            # alone are NOT a substitute for finisher access. The previous
            # draw-proxy gate ("has_draw + reducer + 3+ fuel → go") was
            # overly optimistic: at storm=0 with no finisher or PiF in
            # hand, committing 3+ rituals speculatively hoping to draw
            # Grapeshot averages ~25% hit rate in a 50-card deck with
            # 4 finishers. Audit seed storm_vs_boros Game 1 showed this
            # burning 10 spells for 0 damage on T3, then losing T6.
            # The principled gates below (finisher-in-hand, am_dead_next,
            # opp_life ≤ fuel with finisher) are the only safe greenlights.
            has_draw = any(
                any(dt in getattr(c.template, 'tags', set())
                    for dt in ('cantrip', 'card_advantage'))
                for c in me.hand if c.instance_id != card.instance_id
                and not c.template.is_land
            )

            can_go = ((has_finisher or has_pif) and total_fuel >= min_fuel
                      and mana >= (1 if reducers > 0 else 2))
            if snap.am_dead_next and fuel >= 1:
                can_go = True
            if (has_finisher or has_pif) and opp_life <= total_fuel and total_fuel >= 2:
                can_go = True

            # Value of going off = expected storm / opp_life (fraction of kill)
            # Scaled to match spell scoring range (~10-20 for good plays)
            go_value = total_fuel / opp_life * 20.0
            if can_go:
                mod += max(go_value, 10.0)
            else:
                mod -= go_value
                return mod

        # ── Finisher-access gate for mid-chain ──
        if p.storm_patience and storm >= 1 and 'ritual' in tags:
            if not _has_finisher() and not _has_flashback_combo():
                # Waive the penalty when we're about to die: am_dead_next
                # catches "dies to combat this turn" but misses "dies next
                # turn to scheduled damage" (opp_clock ≤ 2). Under time
                # pressure, building fuel is still valuable — a cantrip
                # drawn off the ritual might hit a finisher. Same "hail
                # Mary" shape as the chain-credit dual-gate in ev_evaluator.
                if not snap.am_dead_next and snap.opp_clock_discrete > 2:
                    # Wasting rituals without finisher access (reduced from 20 to match PiF fix)
                    mod -= (storm + 2) / opp_life * 5.0

        # ── Cantrips while waiting (storm=0): dig for pieces ──
        is_cantrip = ('cantrip' in tags or 'draw' in tags) and 'flashback' not in tags
        if is_cantrip and p.storm_patience and storm == 0:
            # Draw punisher check from oracle text
            opp_has_draw_punisher = any(
                'draw' in (c.template.oracle_text or '').lower()
                and 'opponent' in (c.template.oracle_text or '').lower()
                and 'damage' in (c.template.oracle_text or '').lower()
                for c in opp.creatures
            )
            # Cantrip value = P(finding missing piece) ≈ 1/cards_remaining
            dig_value = 1.0 / max(1, len(me.library)) * opp_life
            mod += -dig_value if opp_has_draw_punisher else dig_value * 3.0

        # ── Storm finisher: cast LAST to maximize storm count ──
        if Kw.STORM in getattr(t, 'keywords', set()):
            storm_copies = storm + 1
            if storm_copies >= opp_life:
                # Lethal = game over. Value = position swing from losing to winning.
                mod += 100.0
            else:
                gy_flashback = [g for g in me.graveyard
                                if getattr(g, 'has_flashback', False)
                                and game.can_cast(self.player_idx, g)]
                fuel_available = sum(
                    1 for c in list(me.hand) + gy_flashback
                    if c.instance_id != card.instance_id
                    and not c.template.is_land
                    and game.can_cast(self.player_idx, c)
                    and Kw.STORM not in getattr(c.template, 'keywords', set())
                )
                if fuel_available > 0:
                    # Each fuel spell adds 1 storm copy = 1/opp_life clock change
                    # Holding is worth fuel_available * (1/opp_life) more damage
                    mod -= fuel_available / opp_life * 40.0
                else:
                    # No fuel: fire only if lethal. Otherwise holding the finisher
                    # for a real chain is strictly better than dealing 1-2 damage.
                    if storm_copies >= opp_life:
                        mod += storm_copies / opp_life * 40.0
                    else:
                        mod -= (opp_life - storm_copies) / opp_life * 20.0

        # ── Flashback-granting spells (Past in Flames etc.) ──
        if 'flashback' in tags and 'combo' in tags and t.is_sorcery:
            if storm >= 2:
                pif_in_gy = any('flashback' in getattr(c.template, 'tags', set())
                                for c in me.graveyard if c.instance_id != card.instance_id)
                if pif_in_gy:
                    return -100.0 / opp_life  # redundant, waste of mana
            if card.zone == "graveyard" and not getattr(card, '_cast_with_flashback', False):
                return -100.0 / opp_life  # don't replay from GY (unless flashback)

            gy_fuel = sum(1 for c in me.graveyard
                          if (c.template.is_instant or c.template.is_sorcery)
                          and any(ft in getattr(c.template, 'tags', set())
                                  for ft in ('ritual', 'cantrip')))
            if p.storm_patience and storm == 0:
                if gy_fuel >= 4:
                    # GY is loaded — PiF at storm=0 IS the combo.
                    # Ritual→PiF→flashback 4+ spells→Grapeshot = lethal
                    mod += gy_fuel / opp_life * 15.0
                else:
                    mod -= gy_fuel / opp_life * 5.0  # hold pre-chain
            elif gy_fuel < 2:
                mod -= 10.0 / opp_life  # empty GY, not worth it
            else:
                # PiF value = GY spells it unlocks × their storm contribution
                mod += gy_fuel / opp_life * 30.0
                hand_rituals = sum(1 for c in me.hand
                                   if c.instance_id != card.instance_id
                                   and 'ritual' in getattr(c.template, 'tags', set())
                                   and (c.template.is_instant or c.template.is_sorcery))
                if hand_rituals >= 2:
                    mod -= hand_rituals / opp_life * 10.0  # cast hand rituals first
                reducers = sum(1 for c in me.battlefield
                               if 'cost_reducer' in getattr(c.template, 'tags', set()))
                pif_cost = max(0, (t.cmc or 4) - reducers)
                if snap.my_mana - pif_cost < (1 if reducers > 0 else 2):
                    mod -= 10.0 / opp_life  # can't afford replays

        # ── Tutor sequencing ──
        if 'tutor' in tags:
            if p.storm_patience and storm == 0:
                mod -= 5.0 / opp_life  # hold pre-chain
            else:
                # Tutor value = it finds the finisher, enabling storm/opp_life kill
                mod += (storm + 1) / opp_life * 5.0
                # Hold tutor if we have castable fuel (cast fuel first for more storm)
                chain_fuel = sum(1 for c in me.hand
                                 if c.instance_id != card.instance_id
                                 and not c.template.is_land
                                 and game.can_cast(self.player_idx, c)
                                 and ('ritual' in getattr(c.template, 'tags', set())
                                      or 'cantrip' in getattr(c.template, 'tags', set())))
                if chain_fuel > 0 and storm < 6:
                    mod -= chain_fuel / opp_life * 10.0

        # ── Cost reducer timing ──
        oracle = (t.oracle_text or '').lower()
        is_reducer = ('cost_reducer' in tags and 'cost' in oracle
                      and 'less' in oracle and t.domain_reduction == 0)
        if is_reducer:
            fuel = _count_fuel()
            existing = sum(1 for c in me.battlefield
                           if 'cost_reducer' in getattr(c.template, 'tags', set()))
            if storm >= 3:
                mod -= 3.0 / opp_life  # mid-chain, deploy spells not reducers
            elif fuel > 0 or storm == 0:
                # Reducer value = future mana saved × fuel count
                # First reducer saves 1 mana per future spell = fuel / opp_life
                saved_per_spell = 1.0
                future_spells = fuel + 3  # hand fuel + expected draws
                mod += saved_per_spell * future_spells / opp_life * 15.0
                if existing > 0:
                    mod *= 0.5  # diminishing returns on 2nd reducer

        return mod

    def _score_land(self, land, me, spells, game) -> float:
        """Score a land play using clock-derived values.

        Land value = mana enables spells → spells change clock.
        Higher priority than most spells (mana is fundamental).
        """
        from ai.clock import card_clock_impact
        snap = snapshot_from_game(game, self.player_idx)

        # Base: a land is always valuable (mana = future clock changes)
        # ~10 because spells typically score 5-15 and we want lands first
        ev = 10.0

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
            ev += 5.0 if has_castable_spells else 2.0
        else:
            if has_castable_spells:
                if current_untapped == 0 and has_one_drops:
                    ev -= 10.0
                else:
                    ev -= 3.0

        # Amulet + bounce-land mana loop: the bounce land returns a land, which
        # re-triggers the Amulet untap → net +1 mana/turn. Detect via oracle.
        # Value raised from +4 → +8 after session-3 matrix showed Amulet Titan
        # still at 23.8% (unchanged from pre-fix 23%); the base signal wasn't
        # loud enough to affect play sequencing.
        if has_untap_enabler:
            land_oracle = (land.template.oracle_text or '').lower()
            is_bounce_land = (
                "return a land you control to its owner's hand" in land_oracle
                or "return an untapped land you control to its owner's hand" in land_oracle
            )
            if is_bounce_land:
                ev += 8.0

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
                ev += 12.0   # enables big creature this turn
            elif effective_mana_after >= target_cmc - 2:
                ev += 4.0    # on-curve ramp to big creature next turn

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
        ev += len(new_colors) * 4.0

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
                ev += 3.0

        if is_fetch:
            ev += 3.0  # fetch flexibility

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

        # Cycling creatures into GY: Living End gameplan
        if card.template.is_creature:
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
            gy_creatures = sum(1 for c in me.graveyard if c.template.is_creature)
            if gy_creatures < 3:
                ev += CYCLING_GY_URGENCY  # urgent: need more GY creatures before cascading

        # Gameplan prefer_cycling: massive boost (Living End, etc.)
        if self.goal_engine:
            current_goal = self.goal_engine.current_goal
            if current_goal and getattr(current_goal, 'prefer_cycling', False):
                ev += CYCLING_GAMEPLAN_BOOST  # cycling is THE gameplan, not optional

        return ev

    def _best_removal_target_value(self, removal, game, opp) -> float:
        """Find the most valuable creature this removal can kill.

        Accounts for mana efficiency: cheap removal on cheap threats
        is better than expensive removal on cheap threats.
        """
        if not opp.creatures:
            return 0.0
        removal_cmc = removal.template.cmc or 0
        best = 0.0
        for c in opp.creatures:
            val = creature_value(c)
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
            # board was wiped, our creatures came back with summoning sickness
            # gone. Swing with everything to cash in the tempo swing.
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
        biggest_attacker_power = max((a.power or 0 for a in attackers), default=0)

        # Winning-state: if our untapped power >= opponent life next turn, don't block.
        # Spending blockers is wasteful when we have lethal on board already.
        my_untapped_power = sum(
            (c.power or 0) for c in me.creatures if not c.tapped
        )
        if my_untapped_power >= opp.life and total_incoming < me.life:
            return {}

        # EMERGENCY: block when incoming damage is dangerous
        # Triggers: lethal, would drop below 5 life, or single attacker > half our life
        emergency = (total_incoming >= me.life
                     or (me.life - total_incoming <= 5 and total_incoming >= 3)
                     or biggest_attacker_power >= me.life // 2)
        if emergency:
            emergency_blocks: Dict[int, List[int]] = {}
            e_used: Set[int] = set()
            # Block biggest attackers with smallest blockers.
            # Two-pass: first try non-battle-cry blockers; fall back to battle cry
            # only if they are the only option. Preserves attack amplification.
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
                return cands

            def _is_battle_cry(b):
                bo = (b.template.oracle_text or '').lower()
                return 'whenever this creature attacks' in bo

            for attacker in sorted(attackers, key=lambda a: a.power or 0, reverse=True):
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
                    # Check if we've blocked enough to survive/stabilize
                    blocked_damage = sum(
                        a.power or 0 for a in attackers if a.instance_id in emergency_blocks
                    )
                    remaining = total_incoming - blocked_damage
                    if remaining < me.life and (me.life - remaining > 5 or remaining == 0):
                        break  # stabilized
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

            # Find best creature we can kill — factoring in strategic priority.
            # Attack-trigger sources (battle cry etc.) are high priority even when
            # their raw creature_value is low: killing them removes ongoing damage
            # amplification on every future attack.
            # Equipment carries scaling value beyond its base P/T.
            best_kill_val = 0.0
            best_kill_id = None
            best_kill_why = ""
            if opp.creatures:
                for c in opp.creatures:
                    remaining_toughness = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
                    if dmg >= remaining_toughness > 0 or remaining_toughness <= 0:
                        val = creature_value(c, snap)
                        c_oracle = (c.template.oracle_text or '').lower()
                        c_cname = (c.template.name or '').lower().split(' //')[0].strip()
                        why_parts = []
                        # Attack-trigger premium: battle cry, Ragavan-style triggers
                        if ('whenever this creature attacks' in c_oracle
                                or (c_cname and f'whenever {c_cname} attacks' in c_oracle)):
                            val += 5.0
                            why_parts.append("removes attack trigger")
                        elif 'battle cry' in c_oracle:
                            val += 5.0
                            why_parts.append("removes battle cry pump")
                        elif 'deals combat damage to a player' in c_oracle:
                            val += 5.0
                            why_parts.append("stops combat-damage engine")
                        # Threat premium: high-power creatures that will kill us soon
                        if (c.power or 0) >= 4:
                            val += (c.power or 0) * 0.5
                            why_parts.append(f"high threat {c.power}/{c.toughness}")
                        if not why_parts:
                            why_parts.append(f"{c.power}/{c.toughness} body")
                        if val > best_kill_val:
                            best_kill_val = val
                            best_kill_id = c.instance_id
                            best_kill_why = ", ".join(why_parts)

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
                # Evaluate all nonland permanents — equipment is high priority
                from engine.cards import CardType
                nonland = [c for c in opp.battlefield if not c.template.is_land]
                if nonland:
                    best = max(nonland, key=lambda c: self._permanent_threat_value(c, opp, snap))
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

    def _permanent_threat_value(self, perm, opp, snap=None) -> float:
        """Evaluate how threatening an opponent's permanent is.

        Creatures: use creature_value().
        Scaling equipment (Cranial Plating, Nettlecyst): value = artifact
          count on opp's board + 2 — oracle-detected via the regex
          `+N/+N for each (artifact|creature|land)`, so any future
          scaling equipment is handled without a card-name list.
        Planeswalkers: high value. Stax: high value. Other: CMC proxy.
        """
        from engine.cards import CardType
        t = perm.template

        if t.is_creature:
            return creature_value(perm, snap)

        oracle = (t.oracle_text or '').lower()
        tags = getattr(t, 'tags', set())

        # Scaling equipment / enchantments — `+N/+N for each <permanent>`
        # is the oracle fingerprint. Value is driven by opponent's current
        # permanent count, not by CMC.
        m = re.search(r'\+\w+/\+\w+\s+for each (artifact|creature|land)', oracle)
        if m:
            counter_type = m.group(1)
            if counter_type == 'artifact':
                count = sum(1 for c in opp.battlefield
                            if CardType.ARTIFACT in c.template.card_types)
            elif counter_type == 'creature':
                count = len(opp.creatures)
            else:
                count = sum(1 for c in opp.battlefield if c.template.is_land)
            # Even unattached, the equip-about-to-fire threat is count + 2
            # (CMC-ish anchor so a 1-artifact board still ranks CP ≥ 3).
            return float(count + 2)

        # Static-boost equipment without a scaling clause: CMC proxy + 2
        if 'equipment' in tags or 'pump' in tags:
            return (t.cmc or 0) + 2.0

        # Planeswalkers
        if CardType.PLANESWALKER in t.card_types:
            return 8.0 + (getattr(perm, 'loyalty_counters', 0) or 0)

        # Stax/lock pieces
        if 'stax' in tags:
            return 7.0

        # Mana sources
        if getattr(t, 'produces_mana', None):
            return 3.0

        # Default: CMC proxy
        return (t.cmc or 0) + 1.0

    def _pick_best_removal_target(self, card, creatures, player,
                                   game, player_idx) -> Optional["CardInstance"]:
        """Pick the best target for a removal spell.

        Signature matches what ResponseDecider expects:
        (card, creatures_list, opponent_player, game, opponent_idx)

        Uses oracle-driven threat value so battle-cry / scaling creatures
        outrank raw P/T bodies. Burn removal filters targets it cannot kill.
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
        return max(candidates, key=lambda c: creature_threat_value(c, snap))

    def _has_high_threat_target(self, game, spell, snap=None) -> bool:
        """True if a removal spell has a target worth proactively casting for.

        Threat value is oracle-driven via `creature_threat_value` (for
        creatures) and `_permanent_threat_value` (for artifacts /
        enchantments like scaling equipment). `prof.big_creature_power`
        is reused as the EV floor, not as a raw P/T cap.

        Considers BOTH creatures and noncreature permanents so that a
        nonland-permanent-hitting spell (Leyline Binding, Force of Vigor,
        Wear // Tear) correctly releases from the reactive_only gate when
        Cranial Plating / Nettlecyst / similar scaling equipment hits the
        battlefield.
        """
        opp = game.players[1 - self.player_idx]
        tags = getattr(spell.template, 'tags', set())
        if 'removal' not in tags:
            return False

        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(spell.template.name) if spell.template else 0
        prof = self.profile
        floor = float(prof.big_creature_power)  # e.g. 4.0 EV floor

        # Creature threats (battle cry, scaling, big body)
        for c in opp.creatures:
            if dmg > 0:
                remaining = (c.toughness or 0) - getattr(c, 'damage_marked', 0)
                if remaining > dmg:
                    continue
            if creature_threat_value(c, snap) >= floor:
                return True

        # Noncreature-permanent threats — scaling equipment (CP, Nettlecyst),
        # planeswalkers, stax pieces. Only applies if the spell can actually
        # hit them (oracle mentions target artifact / enchantment / nonland /
        # noncreature / permanent).
        oracle = (spell.template.oracle_text or '').lower()
        hits_noncreature = ('target artifact' in oracle
                            or 'target enchantment' in oracle
                            or 'target nonland permanent' in oracle
                            or 'target noncreature' in oracle
                            or 'target permanent' in oracle)
        if hits_noncreature:
            for perm in opp.battlefield:
                if perm.template.is_land or perm.template.is_creature:
                    continue
                if self._permanent_threat_value(perm, opp, snap) >= floor:
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

            # Score each creature as an equip target. Evasion multiplies
            # the value since unblocked damage compounds harder than raw
            # power — a CP-attached flier is typically unblockable, while
            # a ground creature of equal size often chump-blocked.
            def _equip_target_score(c):
                base = (c.power or 0) + (c.toughness or 0) * 0.3
                if Keyword.FLYING in c.keywords:
                    return base * 2.0
                if Keyword.MENACE in c.keywords:
                    return base * 1.5
                if Keyword.TRAMPLE in c.keywords:
                    return base * 1.3
                return base

            best = max(creatures, key=_equip_target_score)

            # Score equipping like deploying a creature with the bonus power
            bonus = self._estimate_equip_bonus(equip, player)
            ev = bonus * self.profile.creature_value_mult

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
