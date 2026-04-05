"""EV-Based AI Player — data-driven MTG decision engine.

Architecture: get legal plays → score each via StrategyProfile → pick best.
All weights in ai/strategy_profile.py. All card effects from oracle text.
Combat, blocking, and response decisions delegate to existing modules.
"""
from __future__ import annotations
import random
from typing import Dict, List, Optional, Tuple, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate, Keyword
    from engine.stack import StackItem

from ai.deck_knowledge import DeckKnowledge
from ai.ev_evaluator import (
    EVSnapshot, snapshot_from_game, evaluate_board, creature_value,
    _life_value,
)

# ─────────────────────────────────────────────────────────────
# Archetype detection
# ─────────────────────────────────────────────────────────────

# Archetype detection — single source of truth in strategy_profile.py
def _get_archetype(deck_name: str) -> str:
    from ai.strategy_profile import DECK_ARCHETYPES, ArchetypeStrategy
    arch = DECK_ARCHETYPES.get(deck_name)
    return arch.value if arch else "midrange"


# ─────────────────────────────────────────────────────────────
# Play representation
# ─────────────────────────────────────────────────────────────

class Play:
    """A candidate play with its EV score."""
    __slots__ = ('action', 'card', 'targets', 'ev', 'reason')

    def __init__(self, action: str, card, targets: list, ev: float, reason: str):
        self.action = action  # "play_land", "cast_spell", "cycle"
        self.card = card
        self.targets = targets
        self.ev = ev
        self.reason = reason


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
        from ai.ai_player import ArchetypeStrategy
        arch_enum = ArchetypeStrategy(self.archetype) if self.archetype in [e.value for e in ArchetypeStrategy] else ArchetypeStrategy.MIDRANGE
        self._mulligan_decider = MulliganDecider(arch_enum, self.goal_engine)

        # Response decider — reuse existing
        from ai.response import ResponseDecider
        from ai.turn_planner import TurnPlanner
        self._response_decider = ResponseDecider(
            player_idx, TurnPlanner(), self.strategic_logger)

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

        if cards_in_hand <= 5:
            return True
        if len(lands) == 0 or len(lands) >= 6:
            return False
        return self._mulligan_decider.decide(hand, cards_in_hand)

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
            # - It's removal with a high-value target (4+ power creature)
            if spell.name in self._reactive_only:
                if not spell.template.is_creature:
                    is_dying = snap.am_dead_next or (snap.opp_power >= 3 and snap.opp_clock <= 3)
                    has_big_target = ('removal' in tags and
                                     any((c.power or 0) >= 4 for c in opp.creatures))
                    if not is_dying and not has_big_target:
                        continue

            ev = self._score_spell(spell, snap, game, me, opp)
            targets = self._choose_targets(game, spell)

            # Spells that need targets but have none = skip
            if self._spell_requires_targets(spell) and not targets:
                continue

            candidates.append(Play("cast_spell", spell, targets, ev,
                                   f"{spell.name} (EV={ev:.1f})"))

        if not candidates:
            return None

        # Sort by EV, pick the best
        candidates.sort(key=lambda p: p.ev, reverse=True)
        best = candidates[0]

        if best.ev < self.profile.pass_threshold:
            return None

        return (best.action, best.card, best.targets)

    # ═══════════════════════════════════════════════════════════
    # SCORING — per-archetype spell evaluation
    # ═══════════════════════════════════════════════════════════

    def _score_spell(self, card: "CardInstance", snap: EVSnapshot,
                     game: "GameState", me, opp) -> float:
        """Score a spell using the archetype's strategy profile."""
        t = card.template
        tags = getattr(t, 'tags', set())
        cmc = t.cmc or 0
        p = self.profile  # strategy profile

        ev = 0.0

        # ── Evoke detection ──
        # If card has evoke and we can't hardcast it (not enough mana),
        # we're evoking — this costs us 2 cards (spell + pitched card).
        # Worth it if the target is valuable OR we're under heavy pressure.
        is_evoke = False
        if 'evoke' in tags or 'evoke_pitch' in tags:
            hardcast_cost = cmc
            if snap.my_mana < hardcast_cost:
                is_evoke = True
                ev += p.evoke_base_penalty
                if 'removal' in tags:
                    best_val = self._best_removal_target_value(card, game, opp)
                    if best_val < p.evoke_min_target_value:
                        ev += p.evoke_small_target_penalty
                    else:
                        ev += best_val
                    if snap.opp_power >= snap.my_life - 3 and snap.opp_power > 0:
                        ev += p.evoke_pressure_bonus
                    elif snap.am_dead_next:
                        ev += p.evoke_lethal_bonus
                # Never evoke with no targets
                if snap.opp_creature_count == 0 and 'removal' in tags:
                    ev += p.evoke_empty_board_penalty

        # ── Creature deployment ──
        is_creature = t.is_creature
        if is_creature and not is_evoke:
            ev += creature_value(card) * p.creature_value_mult
            # Haste: immediate attack value
            from engine.cards import Keyword
            if Keyword.HASTE in getattr(t, 'keywords', set()):
                ev += (card.power or 0) * 1.0

        # ── Removal ──
        # Only penalize removal when NO creatures AND card isn't also a creature
        if 'removal' in tags and not is_evoke:
            best_target_val = self._best_removal_target_value(card, game, opp)
            if best_target_val > 0:
                ev += best_target_val * p.removal_target_mult
            elif snap.opp_creature_count == 0 and not is_creature:
                ev += p.removal_no_target_penalty  # pure removal with no targets

        # ── Board wipe ──
        if 'board_wipe' in tags:
            opp_board = sum(creature_value(c) for c in opp.creatures)
            my_board = sum(creature_value(c) for c in me.creatures)
            if snap.opp_creature_count == 0:
                ev += p.wrath_empty_board_penalty
            else:
                ev += opp_board - my_board
                if snap.opp_creature_count >= 3:
                    ev += p.wrath_multi_kill_bonus

        # ── Burn (face damage) ──
        from decks.card_knowledge_loader import get_burn_damage
        burn_dmg = get_burn_damage(t.name)
        if burn_dmg > 0:
            if burn_dmg >= snap.opp_life:
                ev += p.lethal_burn_bonus  # lethal!
            elif p.burn_face_mult > 0.5:
                ev += burn_dmg * p.burn_face_mult
            else:
                # Non-aggro: prefer using as removal
                pass

        # ── Card draw / cantrips ──
        oracle = (t.oracle_text or '').lower()
        is_draw = 'cantrip' in tags or 'draw' in tags or ('draw' in oracle and 'card' in oracle)
        if is_draw:
            # Base draw value scales with how much we need cards
            draw_val = p.card_draw_base
            if snap.my_hand_size <= 2:
                draw_val += p.card_draw_empty_hand_bonus
            elif snap.my_hand_size <= 4:
                draw_val += p.card_draw_low_hand_bonus
            oracle = (t.oracle_text or '').lower()
            if 'draw two' in oracle or 'draws two' in oracle:
                draw_val += p.draw_two_bonus
            if 'draw three' in oracle:
                draw_val += p.draw_three_bonus
            draw_val += p.card_draw_archetype_bonus
            ev += draw_val

        # ── Non-creature permanents (artifacts, enchantments, planeswalkers) ──
        from engine.cards import CardType
        if not is_creature and not t.is_land:
            if CardType.PLANESWALKER in t.card_types:
                ev += p.planeswalker_bonus
            elif CardType.ARTIFACT in t.card_types and 'equipment' not in tags:
                ev += p.artifact_bonus
            elif CardType.ENCHANTMENT in t.card_types:
                ev += p.enchantment_bonus

        # ── Rituals (combo fuel) — only actual ritual spells, not creatures with "add mana" text ──
        if 'ritual' in tags and (t.is_instant or t.is_sorcery):
            ev += p.ritual_bonus

        # ── Past in Flames — bonus for rich graveyard ──
        if 'flashback' in tags and p.pif_gy_ritual_mult > 0:
            # Check if a flashback-granting spell was already cast this turn
            # Only block if PiF was cast THIS turn (spells_cast > 0 means we're chaining)
            # and there's already a flashback-granter in GY from this chain
            if me.spells_cast_this_turn >= 2:
                pif_in_gy = any(
                    'flashback' in getattr(c.template, 'tags', set())
                    for c in me.graveyard
                )
                if pif_in_gy:
                    return p.pif_redundant_penalty
            gy_rituals = sum(1 for c in me.graveyard if 'ritual' in getattr(c.template, 'tags', set()))
            gy_cantrips = sum(1 for c in me.graveyard if 'cantrip' in getattr(c.template, 'tags', set()))
            ev += gy_rituals * p.pif_gy_ritual_mult + gy_cantrips * p.pif_gy_cantrip_mult

        # ── Tutors (Wish) — find the finisher ──
        if 'tutor' in tags and p.tutor_base > 0:
            tutor_val = p.tutor_base
            storm = me.spells_cast_this_turn
            if storm >= 8:
                tutor_val += p.tutor_storm_8_bonus
            elif storm >= 5:
                tutor_val += p.tutor_storm_5_bonus
            elif storm >= 3:
                tutor_val += p.tutor_storm_3_bonus
            elif storm >= 1:
                tutor_val += p.tutor_storm_1_bonus
            # Hold Wish if we have actual chain fuel (rituals/cantrips) in hand or GY flashback
            all_fuel_sources = list(me.hand) + [g for g in me.graveyard if getattr(g, 'has_flashback', False)]
            chain_fuel = sum(1 for c in all_fuel_sources if c.instance_id != card.instance_id
                            and not c.template.is_land and game.can_cast(self.player_idx, c)
                            and ('ritual' in getattr(c.template, 'tags', set())
                                 or 'cantrip' in getattr(c.template, 'tags', set())))
            if chain_fuel > 0 and storm < 6:
                tutor_val += chain_fuel * p.tutor_fuel_penalty_mult
            ev += tutor_val

        # ── Cost reducers / engines ──
        # Only for cards that ACTUALLY reduce other spells' costs (check oracle),
        # not cards with domain self-reduction (Scion, Leyline Binding)
        oracle_lower = (t.oracle_text or '').lower()
        is_real_reducer = ('cost_reducer' in tags and
                           'cost' in oracle_lower and 'less' in oracle_lower and
                           t.domain_reduction == 0)
        if is_real_reducer:
            storm = me.spells_cast_this_turn
            # Count fuel spells in hand (rituals, cantrips, draw)
            fuel_in_hand = sum(1 for c in me.hand
                               if c.instance_id != card.instance_id
                               and not c.template.is_land
                               and any(ft in getattr(c.template, 'tags', set())
                                       for ft in ('ritual', 'cantrip', 'draw')))
            if storm == 0:
                if fuel_in_hand >= 2:
                    ev += p.cost_reducer_pre_chain
                    if snap.turn_number <= 4 and p.cost_reducer_pre_chain > 5:
                        ev += p.early_reducer_bonus
                    medallions_on_board = sum(1 for c in me.battlefield
                                              if 'cost_reducer' in getattr(c.template, 'tags', set()))
                    if medallions_on_board == 0 and p.cost_reducer_pre_chain > 5:
                        ev += p.first_reducer_bonus
                else:
                    # No fuel — reducer is a dead investment, small value only
                    ev += p.cost_reducer_pre_chain * 0.3
            elif storm >= 5:
                ev += p.cost_reducer_mid_chain
            else:
                ev += p.cost_reducer_early_chain

        # ── ETB value ──
        if 'etb_value' in tags:
            ev += p.etb_value_bonus

        # ── Gameplan role bonuses ──
        if card.name in self._payoff_names:
            ev += p.payoff_bonus
        elif card.name in self._engine_names:
            ev += p.engine_bonus
        elif card.name in self._fuel_names:
            ev += p.fuel_bonus

        # ── Archetype-specific adjustments ──
        ev += self._archetype_modifier(card, snap, game, me, opp)

        # ── Mana efficiency ──
        if cmc == 0 and p.zero_mana_combo_bonus > 0:
            ev += p.zero_mana_combo_bonus
        elif snap.my_mana > 0 and cmc > 0:
            ev += min(cmc / snap.my_mana, 1.0) * 2.0

        # ── Mana holdback penalty ──
        # Don't hold mana when opponent has no clock (opp_clock >= 10)
        if p.holdback_applies and snap.turn_number >= p.holdback_min_turn and snap.opp_clock < 10:
            has_instant = any(
                c.template.is_instant and (
                    'removal' in getattr(c.template, 'tags', set()) or
                    'counterspell' in getattr(c.template, 'tags', set())
                )
                for c in me.hand if c.instance_id != card.instance_id
            )
            if has_instant and not t.is_instant:
                remaining_mana = snap.my_mana - cmc
                if remaining_mana < 2:
                    ev += p.holdback_penalty

        # ── Storm finisher sequencing ──
        # Storm finishers (Grapeshot, Empty the Warrens) should be cast LAST
        # in the chain to maximize storm count.
        if p.finisher_hold_penalty != 0:
            from engine.cards import Keyword as Kw
            if Kw.STORM in getattr(t, 'keywords', set()):
                storm_copies = me.spells_cast_this_turn + 1
                if storm_copies >= snap.opp_life:
                    ev += p.lethal_storm_bonus  # lethal storm!
                else:
                    # Count castable fuel in hand + GY flashback (only if we have mana)
                    gy_flashback = [g for g in me.graveyard
                                    if getattr(g, 'has_flashback', False)
                                    and game.can_cast(self.player_idx, g)]
                    fuel_available = sum(
                        1 for c in list(me.hand) + gy_flashback
                        if c.instance_id != card.instance_id
                        and not c.template.is_land
                        and game.can_cast(self.player_idx, c)
                        and 'storm' not in {kw.value if hasattr(kw, 'value') else str(kw)
                                             for kw in getattr(c.template, 'keywords', set())}
                    )
                    if fuel_available > 0:
                        ev += p.finisher_hold_penalty
                    else:
                        # Scale bonus by how close to lethal (vs actual opp life, not 20)
                        damage_pct = storm_copies / max(1, snap.opp_life)
                        if damage_pct >= 0.7:
                            ev += p.finisher_storm_8_bonus  # 70%+ of lethal = fire
                        elif damage_pct >= 0.4:
                            ev += p.finisher_storm_5_bonus  # 40%+ = decent
                        else:
                            ev += p.finisher_low_storm_penalty  # waste

        # ── Survival mode: when facing lethal, boost survival plays ──
        if snap.am_dead_next:
            if 'removal' in tags and snap.opp_creature_count > 0:
                ev += p.survival_removal_bonus
            if t.is_creature and (t.toughness or 0) >= 3:
                ev += p.survival_blocker_bonus
            if 'board_wipe' in tags and snap.opp_creature_count > 0:
                ev += p.survival_wrath_bonus

        return ev

    def _archetype_modifier(self, card, snap: EVSnapshot,
                            game: "GameState", me, opp) -> float:
        """Per-archetype adjustments using strategy profile data."""
        t = card.template
        tags = getattr(t, 'tags', set())
        p = self.profile
        mod = 0.0

        # ── Creature bonuses ──
        if t.is_creature:
            if (t.cmc or 0) <= snap.turn_number:
                mod += p.on_curve_creature_bonus
            if (t.cmc or 0) <= 2:
                mod += p.cheap_creature_bonus
            if snap.my_creature_count == 0:
                mod += p.empty_board_creature_bonus
            if t.has_flash:
                mod += p.flash_creature_bonus
            if (t.power or 0) >= 3:
                mod += p.high_power_creature_bonus
            if 'card_advantage' in tags:
                mod += p.card_advantage_creature_bonus

        # ── Removal bonuses ──
        if 'removal' in tags and snap.opp_creature_count > 0:
            mod += p.removal_vs_creatures_bonus
            if snap.opp_power >= 4:
                mod += p.removal_vs_big_creatures_bonus

        # ── Discard (Thoughtseize) ──
        if 'discard' in tags:
            if snap.turn_number <= 3:
                mod += p.discard_early_bonus
            elif snap.opp_hand_size >= 3:
                mod += p.discard_late_bonus

        # ── Burn at low life ──
        if snap.opp_life <= 10 and p.burn_low_life_bonus > 0:
            from decks.card_knowledge_loader import get_burn_damage
            if get_burn_damage(t.name) > 0:
                mod += p.burn_low_life_bonus

        # ── Control phase-based ──
        if p.has_control_phases:
            cmc_val = t.cmc or 0
            from engine.cards import CardType
            if snap.turn_number <= 6:
                if 'removal' in tags and snap.opp_creature_count > 0:
                    mod += p.early_removal_bonus
                if cmc_val <= 2 and not t.is_land:
                    mod += p.early_cheap_play_bonus
                if CardType.PLANESWALKER in t.card_types:
                    mod += p.early_planeswalker_bonus
            elif snap.turn_number <= 12:
                if 'board_wipe' in tags and snap.opp_creature_count >= 2:
                    mod += p.mid_wrath_bonus
                if card.name in self._payoff_names:
                    mod += p.mid_payoff_bonus
                if t.is_creature:
                    mod += p.mid_creature_bonus
            else:
                if t.is_creature:
                    mod += p.late_creature_bonus
                if card.name in self._payoff_names:
                    mod += p.late_payoff_bonus

        # ── Combo chain sequencing ──
        if p.has_combo_chain:
            storm = me.spells_cast_this_turn
            mana = snap.my_mana
            if 'cantrip' in tags or 'draw' in tags:
                if mana <= 2 and storm >= 3 and 'ritual' in tags:
                    pass  # rituals get priority below when mana-starved
                else:
                    mod += p.cantrip_early_chain if storm <= 3 else p.cantrip_late_chain
            if 'ritual' in tags:
                if mana <= 2 and storm >= 3:
                    mod += 8.0  # MANA-STARVED: ritual produces mana to keep going!
                elif storm <= 2:
                    mod += p.ritual_early_chain
                else:
                    mod += p.ritual_late_chain + storm * p.ritual_storm_scaling
            if storm >= 3:
                mod += p.chain_mid_bonus
            if storm >= 6:
                mod += p.chain_deep_bonus
            from engine.cards import CardType as CT2
            if CT2.PLANESWALKER in t.card_types:
                if storm == 0:
                    mod += p.pre_chain_planeswalker_bonus
                elif storm >= 3:
                    mod += p.planeswalker_mid_chain_penalty

        return mod

    def _score_land(self, land, me, spells, game) -> float:
        """Score a land play. Generally very high priority."""
        p = self.profile
        ev = p.land_base_ev

        has_castable_spells = any(
            (s.template.cmc or 0) <= len(me.untapped_lands) + 1
            for s in spells if not s.template.is_land
        )
        if not land.template.enters_tapped:
            ev += p.land_untapped_castable_bonus if has_castable_spells else p.land_untapped_base_bonus
        else:
            if has_castable_spells:
                ev += p.land_tapped_castable_penalty

        existing_colors = set()
        for l in me.lands:
            existing_colors.update(l.template.produces_mana)
        new_colors = set(land.template.produces_mana) - existing_colors
        ev += len(new_colors) * p.land_new_color_bonus

        from engine.card_database import FETCH_LAND_COLORS
        is_fetch = land.name in FETCH_LAND_COLORS
        if is_fetch:
            ev += p.land_fetch_bonus

        # Landfall value — fetch lands trigger landfall twice (fetch ETB + fetched land ETB)
        landfall_count = sum(1 for c in me.battlefield
                             if 'landfall' in (c.template.oracle_text or '').lower())
        if landfall_count > 0:
            triggers = 2 if is_fetch else 1
            ev += landfall_count * triggers * p.land_landfall_trigger_value

        return ev

    def _score_cycling(self, card, snap, game, me, opp) -> float:
        """Score a cycling activation. Cycling costs 1-2 mana, puts card in GY, draws 1."""
        p = self.profile
        ev = p.card_draw_base  # cycling draws a card

        # Cycling creatures into GY is the Living End gameplan
        if card.template.is_creature:
            ev += 4.0  # creature in GY = future Living End value
            # Bigger creatures are better in GY for Living End
            power = card.template.power or 0
            ev += power * 0.5

        # Cycling cost matters — cheaper is better
        cost_data = card.template.cycling_cost_data
        if cost_data:
            if cost_data.get('life', 0) > 0:
                ev += 2.0  # free cycling (pay life, not mana) — great tempo
            elif cost_data.get('mana', 0) <= 1:
                ev += 1.0  # cheap cycling

        # If we have a cascade spell in hand, cycling to fill GY is urgent
        has_cascade = any(getattr(c.template, 'is_cascade', False) for c in me.hand
                         if not c.template.is_land)
        if has_cascade:
            ev += 3.0  # cascade is ready — fill GY fast

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
            if removal_cmc > target_cmc + 2:
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
                discardable = [c for c in me.hand
                               if not c.template.is_land
                               and c.template.cmc > len(me.untapped_lands) + 2]
                if len(me.lands) >= 5:
                    extra_lands = [c for c in me.hand if c.template.is_land]
                    discardable.extend(extra_lands[:2])
                pumps = min(len(discardable), 2)
                for i in range(pumps):
                    card_to_discard = discardable[i]
                    if card_to_discard in me.hand:
                        me.hand.remove(card_to_discard)
                        card_to_discard.zone = "graveyard"
                        me.graveyard.append(card_to_discard)
                        creature.temp_power_mod += 1
                        creature.temp_toughness_mod += 1
                break

        # Lethal: alpha strike
        total_power = sum(c.power for c in valid if c.power and c.power > 0)
        if total_power >= opp.life:
            return valid

        # No blockers = free damage. Always attack into an empty board.
        opp_blockers = game.get_valid_blockers(1 - self.player_idx)
        if not opp_blockers and valid:
            return valid

        # CombatPlanner
        try:
            vboard = extract_virtual_board(game, self.player_idx)
            attack_plan, score_delta = self.combat_planner.plan_attack(vboard)

            threshold = self.profile.attack_threshold

            if attack_plan and score_delta > threshold:
                attack_ids = {vc.instance_id for vc in attack_plan}
                return [c for c in valid if c.instance_id in attack_ids]
        except Exception:
            pass

        # Fallback: attack with everything unless control is facing blockers
        return valid

    def decide_blockers(self, game, attackers) -> Dict[int, List[int]]:
        """Decide blocking assignments."""
        from ai.board_eval import evaluate_action, Action, ActionType
        from engine.cards import Keyword

        valid_blockers = game.get_valid_blockers(self.player_idx)
        if not valid_blockers or not attackers:
            return {}

        me = game.players[self.player_idx]
        total_incoming = sum(a.power or 0 for a in attackers)

        # EMERGENCY: if incoming damage is lethal, we MUST block
        # Use cheapest blockers first to preserve high-value creatures
        if total_incoming >= me.life:
            emergency_blocks: Dict[int, List[int]] = {}
            e_used: Set[int] = set()
            # Block biggest attackers with smallest blockers
            for attacker in sorted(attackers, key=lambda a: a.power or 0, reverse=True):
                best_chump = None
                best_chump_val = 999
                for blocker in valid_blockers:
                    if blocker.instance_id in e_used:
                        continue
                    if Keyword.FLYING in attacker.keywords:
                        if (Keyword.FLYING not in blocker.keywords and
                                Keyword.REACH not in blocker.keywords):
                            continue
                    val = creature_value(blocker)
                    if val < best_chump_val:
                        best_chump_val = val
                        best_chump = blocker
                if best_chump:
                    emergency_blocks[attacker.instance_id] = [best_chump.instance_id]
                    e_used.add(best_chump.instance_id)
                    # Check if we've blocked enough to survive
                    blocked_damage = sum(
                        a.power or 0 for a in attackers if a.instance_id in emergency_blocks
                    )
                    if total_incoming - blocked_damage < me.life:
                        break  # we survive, stop blocking
            if emergency_blocks:
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
        t = spell.template
        tags = getattr(t, 'tags', set())
        opp = game.players[1 - self.player_idx]

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

            # Find best creature we can kill
            best_kill_val = 0.0
            best_kill_id = None
            if opp.creatures:
                for c in opp.creatures:
                    if dmg >= (c.toughness or 0):
                        val = creature_value(c)
                        if val > best_kill_val:
                            best_kill_val = val
                            best_kill_id = c.instance_id

            # Compare: is killing a creature worth more than face damage?
            face_val = dmg * self.profile.burn_face_mult
            if opp.life <= 10:
                face_val = dmg * self.profile.burn_face_low_life_mult

            # Prefer removing big creatures unless burn is near-lethal
            if best_kill_id and best_kill_val > face_val:
                return [best_kill_id]  # kill the creature
            if best_kill_id:
                best_kill_card = next((c for c in opp.creatures
                                       if c.instance_id == best_kill_id), None)
                if (best_kill_card
                        and (best_kill_card.power or 0) >= self.profile.burn_kill_min_power
                        and opp.life > dmg * self.profile.burn_kill_life_ratio):
                    return [best_kill_id]  # big threat, burn not near lethal
            return [-1]  # go face

        # Removal (non-burn): target best opponent creature
        if 'removal' in tags and 'board_wipe' not in tags:
            if opp.creatures:
                best = max(opp.creatures, key=lambda c: creature_value(c))
                return [best.instance_id]
            return []

        # Blink effects: target our best ETB creature
        if 'blink' in tags:
            me = game.players[self.player_idx]
            etb_creatures = [c for c in me.creatures
                             if 'etb_value' in getattr(c.template, 'tags', set())]
            if etb_creatures:
                best = max(etb_creatures, key=lambda c: creature_value(c))
                return [best.instance_id]

        return []

    def _pick_best_removal_target(self, card, creatures, player,
                                   game, player_idx) -> Optional["CardInstance"]:
        """Pick the best target for a removal spell.

        Signature matches what ResponseDecider expects:
        (card, creatures_list, opponent_player, game, opponent_idx)
        """
        if not creatures:
            return None
        return max(creatures, key=lambda c: creature_value(c))

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
        if 'blink' in tags:
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
        """Check if any equipment should be attached."""
        from engine.cards import CardType
        equipment = [c for c in player.battlefield
                     if CardType.ARTIFACT in c.template.card_types
                     and 'equipment' in getattr(c.template, 'tags', set())
                     and not c.attached_to]
        creatures = [c for c in player.creatures if not c.summoning_sick]
        if equipment and creatures:
            equip = equipment[0]
            best = max(creatures, key=lambda c: c.power or 0)
            return ("equip", equip, [best.instance_id])
        return None
