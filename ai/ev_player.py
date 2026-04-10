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
            return True
        if len(lands) == 0 or len(lands) >= self.profile.mulligan_bad_land_count:
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
                    prof = self.profile
                    is_dying = snap.am_dead_next or (snap.opp_power >= prof.dying_opp_power
                                                     and snap.opp_clock <= prof.dying_opp_clock)
                    has_big_target = ('removal' in tags and
                                     any((c.power or 0) >= prof.big_creature_power
                                         for c in opp.creatures))
                    if not is_dying and not has_big_target:
                        continue

            ev = self._score_spell(spell, snap, game, me, opp)
            targets = self._choose_targets(game, spell)

            # Spells that need targets but have none = skip
            if self._spell_requires_targets(spell) and not targets:
                continue

            candidates.append(Play("cast_spell", spell, targets, ev,
                                   f"{spell.name} (EV={ev:.1f})"))

        # Consider equipping unattached equipment
        equip_play = self._consider_equip(game, me)
        if equip_play:
            candidates.append(equip_play)

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

        # Delve-aware effective CMC: Murktide Regent costs 7 face but 2 with
        # a full graveyard. Score it at the effective cost so it gets on-curve
        # bonuses and deploys T3-5 instead of T10.
        effective_cmc = cmc
        if getattr(t, 'has_delve', False):
            gy_spells = sum(1 for c in me.graveyard
                           if c.template.is_instant or c.template.is_sorcery)
            mc = t.mana_cost
            colored_cost = (cmc - getattr(mc, 'generic', 0)) if mc else 2
            generic = max(0, cmc - colored_cost)
            effective_cmc = max(colored_cost, cmc - min(gy_spells, generic))

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
                    if snap.opp_power >= snap.my_life - p.evoke_pressure_life_buffer and snap.opp_power > 0:
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
                ev += (card.power or 0) * p.haste_damage_mult

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
                if snap.opp_creature_count >= p.wrath_min_creatures + 1:
                    ev += p.wrath_multi_kill_bonus

        # ── Burn (face damage) ──
        from decks.card_knowledge_loader import get_burn_damage
        burn_dmg = get_burn_damage(t.name)
        if burn_dmg > 0:
            if burn_dmg >= snap.opp_life:
                ev += p.lethal_burn_bonus  # lethal!
            elif p.burn_face_mult > 0:
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
            if snap.my_hand_size <= p.empty_hand_threshold:
                draw_val += p.card_draw_empty_hand_bonus
            elif snap.my_hand_size <= p.low_hand_threshold:
                draw_val += p.card_draw_low_hand_bonus
            oracle = (t.oracle_text or '').lower()
            if 'draw three' in oracle:
                draw_val += p.draw_multi_bonus(2)  # 3 cards = 2 extra
            elif 'draw two' in oracle or 'draws two' in oracle:
                draw_val += p.draw_multi_bonus(1)  # 2 cards = 1 extra
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
            # With storm patience, ritual_bonus only applies mid-chain (storm > 0)
            if p.storm_patience and me.spells_cast_this_turn == 0:
                pass  # Handled by patience gate in _archetype_modifier
            else:
                ev += p.ritual_bonus

        # ── Past in Flames — bonus for rich graveyard ──
        if 'flashback' in tags and p.pif_gy_fuel_mult > 0:
            storm = me.spells_cast_this_turn

            # (a) Redundancy: don't cast a second PiF if one already resolved this turn
            if storm >= 2:
                pif_in_gy = any(
                    'flashback' in getattr(c.template, 'tags', set())
                    for c in me.graveyard
                    if c.instance_id != card.instance_id
                )
                if pif_in_gy:
                    return p.pif_redundant_penalty

            # (b) Self-replay prevention: don't cast PiF from GY via its own flashback
            #     (wastes 5 mana to do the same thing again)
            if card.zone == "graveyard":
                return p.pif_redundant_penalty

            # (c) Count GY fuel (instants/sorceries, not PiF itself)
            gy_rituals = sum(1 for c in me.graveyard
                             if 'ritual' in getattr(c.template, 'tags', set())
                             and (c.template.is_instant or c.template.is_sorcery))
            gy_cantrips = sum(1 for c in me.graveyard
                              if 'cantrip' in getattr(c.template, 'tags', set())
                              and (c.template.is_instant or c.template.is_sorcery)
                              and 'flashback' not in getattr(c.template, 'tags', set()))
            gy_fuel_total = gy_rituals + gy_cantrips

            # (d) Patience: hold PiF at storm=0 — it's useless before rituals fill GY
            if p.storm_patience and storm == 0:
                ev += p.storm_hold_penalty
            # (e) Empty GY: heavy penalty if nothing useful to replay
            elif gy_fuel_total < 2:
                ev += p.pif_empty_gy_penalty
            else:
                # PiF value scales with GY fuel — more fuel = more storm count gain
                ev += p.pif_gy_value(gy_rituals, gy_cantrips)
                # Mid-chain: cast rituals from hand FIRST, then PiF
                hand_rituals = sum(1 for c in me.hand
                                   if c.instance_id != card.instance_id
                                   and 'ritual' in getattr(c.template, 'tags', set())
                                   and (c.template.is_instant or c.template.is_sorcery))
                if hand_rituals >= 2:
                    ev += p.pif_wait_for_rituals_penalty
                # (f) Mana check: PiF is useless if we can't afford GY spells after
                reducers = sum(1 for c in me.battlefield
                               if 'cost_reducer' in getattr(c.template, 'tags', set()))
                pif_cost = max(0, (card.template.cmc or 4) - reducers)
                mana_after_pif = snap.my_mana - pif_cost
                # Need at least 1 mana to replay a ritual (with reducer) or 2 (without)
                min_replay_cost = 1 if reducers > 0 else 2
                if mana_after_pif < min_replay_cost:
                    ev += p.pif_no_mana_penalty

        # ── Tutors (Wish) — find the finisher ──
        if 'tutor' in tags and p.tutor_base > 0:
            storm = me.spells_cast_this_turn
            # Storm patience: don't Wish at storm=0 (save for mid-chain)
            if p.storm_patience and storm == 0:
                ev += p.storm_hold_penalty
            else:
                tutor_val = p.tutor_base
                tutor_val += p.tutor_storm_bonus(storm)
                # Hold Wish if we have actual chain fuel (rituals/cantrips) in hand or GY flashback
                all_fuel_sources = list(me.hand) + [g for g in me.graveyard if getattr(g, 'has_flashback', False)]
                chain_fuel = sum(1 for c in all_fuel_sources if c.instance_id != card.instance_id
                                and not c.template.is_land and game.can_cast(self.player_idx, c)
                                and ('ritual' in getattr(c.template, 'tags', set())
                                     or 'cantrip' in getattr(c.template, 'tags', set())))
                if chain_fuel > 0 and storm < p.tutor_fuel_storm_cap:
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
            fuel_in_hand = sum(1 for c in me.hand
                               if c.instance_id != card.instance_id
                               and not c.template.is_land
                               and any(ft in getattr(c.template, 'tags', set())
                                       for ft in ('ritual', 'cantrip', 'draw')))
            reducers_on_board = sum(1 for c in me.battlefield
                                    if 'cost_reducer' in getattr(c.template, 'tags', set()))
            ev += p.reducer_ev(storm, fuel_in_hand, reducers_on_board, snap.turn_number)

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
            ev += min(effective_cmc / snap.my_mana, 1.0) * p.mana_efficiency_mult

        # ── Mana holdback penalty ──
        # Don't hold mana when opponent has no clock (opp_clock >= 10)
        if p.holdback_applies and snap.turn_number >= p.holdback_min_turn and snap.opp_clock < p.holdback_opp_clock_threshold:
            has_instant = any(
                c.template.is_instant and (
                    'removal' in getattr(c.template, 'tags', set()) or
                    'counterspell' in getattr(c.template, 'tags', set())
                )
                for c in me.hand if c.instance_id != card.instance_id
            )
            if has_instant and not t.is_instant:
                remaining_mana = snap.my_mana - cmc
                if remaining_mana < p.holdback_min_remaining_mana:
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
                        damage_pct = storm_copies / max(1, snap.opp_life)
                        ev += p.finisher_ev(damage_pct)

        # ── Survival mode: when facing lethal, boost survival plays ──
        # Scale by threat size — big creatures need answers more urgently
        if snap.am_dead_next:
            avg_opp_power = snap.opp_power / max(1, snap.opp_creature_count)
            power_scale = min(1.0, avg_opp_power / 3.0)
            if 'removal' in tags and snap.opp_creature_count > 0:
                ev += p.survival_removal_bonus * power_scale
            if t.is_creature and (t.toughness or 0) >= 3:
                ev += p.survival_blocker_bonus * power_scale
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
            # Delve-aware effective CMC for on-curve check
            ecmc = t.cmc or 0
            if getattr(t, 'has_delve', False):
                gy_spells = sum(1 for c in me.graveyard
                               if c.template.is_instant or c.template.is_sorcery)
                mc = t.mana_cost
                cc = (ecmc - getattr(mc, 'generic', 0)) if mc else 2
                ecmc = max(cc, ecmc - min(gy_spells, max(0, ecmc - cc)))
            if ecmc <= snap.turn_number:
                mod += p.on_curve_creature_bonus
            if ecmc <= p.cheap_creature_cmc:
                mod += p.cheap_creature_bonus
            if snap.my_creature_count == 0:
                mod += p.empty_board_creature_bonus
            if t.has_flash:
                mod += p.flash_creature_bonus
            if (t.power or 0) >= p.high_power_threshold:
                mod += p.high_power_creature_bonus
            if 'card_advantage' in tags:
                mod += p.card_advantage_creature_bonus

        # ── Removal bonuses ──
        if 'removal' in tags and snap.opp_creature_count > 0:
            mod += p.removal_vs_creatures_bonus
            if snap.opp_power >= p.big_creature_power:
                mod += p.removal_vs_big_creatures_bonus

        # ── Discard (Thoughtseize) ──
        if 'discard' in tags:
            if snap.turn_number <= p.discard_early_turns:
                mod += p.discard_early_bonus
            elif snap.opp_hand_size >= p.discard_min_opp_hand:
                mod += p.discard_late_bonus

        # ── Burn at low life ──
        if snap.opp_life <= p.burn_low_life_threshold and p.burn_low_life_bonus > 0:
            from decks.card_knowledge_loader import get_burn_damage
            if get_burn_damage(t.name) > 0:
                mod += p.burn_low_life_bonus

        # ── Control phase-based ──
        # role_idx: 0=removal, 1=cheap_play, 2=planeswalker, 3=wrath, 4=payoff, 5=creature
        if p.has_control_phases:
            turn = snap.turn_number
            cmc_val = t.cmc or 0
            from engine.cards import CardType
            if 'removal' in tags and snap.opp_creature_count > 0:
                mod += p.phase_bonus(turn, 0)
            if cmc_val <= p.control_cheap_spell_cmc and not t.is_land:
                mod += p.phase_bonus(turn, 1)
            if CardType.PLANESWALKER in t.card_types:
                mod += p.phase_bonus(turn, 2)
            if 'board_wipe' in tags and snap.opp_creature_count >= p.wrath_min_creatures:
                mod += p.phase_bonus(turn, 3)
            if card.name in self._payoff_names:
                mod += p.phase_bonus(turn, 4)
            if t.is_creature:
                mod += p.phase_bonus(turn, 5)

        # ── Combo chain sequencing ──
        if p.has_combo_chain:
            storm = me.spells_cast_this_turn
            mana = snap.my_mana

            # ── Storm patience: hold rituals until ready to go off ──
            # At storm=0 (nothing cast yet this turn), check if we have enough
            # resources to commit to a combo turn. If not, hold rituals.
            # Once any spell is cast (storm >= 1), rituals fire freely —
            # this models "cantrip → see what we draw → chain if good".
            if p.storm_patience and storm == 0 and 'ritual' in tags:
                # Count fuel in hand (rituals + cantrips, not counting this one)
                fuel_in_hand = sum(
                    1 for c in me.hand
                    if c.instance_id != card.instance_id
                    and not c.template.is_land
                    and any(ft in getattr(c.template, 'tags', set())
                            for ft in ('ritual', 'cantrip', 'draw'))
                )
                reducers = sum(
                    1 for c in me.battlefield
                    if 'cost_reducer' in getattr(c.template, 'tags', set())
                )
                # GY fuel via Past in Flames
                has_pif = any(c.name == 'Past in Flames' for c in me.hand)
                gy_fuel = 0
                if has_pif:
                    gy_fuel = sum(
                        1 for c in me.graveyard
                        if (c.template.is_instant or c.template.is_sorcery)
                        and 'ritual' in getattr(c.template, 'tags', set())
                    )
                total_fuel = fuel_in_hand + gy_fuel + 1  # +1 for this ritual
                has_finisher_access = any(
                    c.name in ('Grapeshot', 'Empty the Warrens', 'Wish')
                    for c in me.hand
                )
                # GO: enough fuel + finisher access + mana to start
                min_fuel = p.storm_min_fuel_to_go if reducers > 0 else p.storm_min_fuel_to_go + 2
                can_go = (has_finisher_access
                          and total_fuel >= min_fuel
                          and mana >= (1 if reducers > 0 else 2))
                # Desperation: dying or close
                if snap.am_dead_next and fuel_in_hand >= 1:
                    can_go = True
                # Opp at low life
                if has_finisher_access and snap.opp_life <= total_fuel and total_fuel >= 2:
                    can_go = True

                if can_go:
                    mod += p.storm_go_off_bonus
                else:
                    mod += p.storm_hold_penalty
                    return mod

            is_actual_cantrip = (('cantrip' in tags or 'draw' in tags)
                                and 'flashback' not in tags)  # PiF isn't a cantrip
            if is_actual_cantrip:
                if mana <= 2 and storm >= 3 and 'ritual' in tags:
                    pass  # rituals get priority below when mana-starved
                elif p.storm_patience and storm == 0:
                    # Cantrips while waiting: dig for pieces
                    # Reduced value vs Bowmasters
                    opp_has_draw_punisher = any(
                        c.name in ('Orcish Bowmasters',)
                        for c in opp.creatures
                    )
                    if opp_has_draw_punisher:
                        mod += p.storm_cantrip_vs_bowmasters
                    else:
                        mod += p.storm_cantrip_while_waiting
                else:
                    mod += p.chain_fuel_value(storm)
            if 'ritual' in tags:
                if mana <= 2 and storm >= 3:
                    mod += p.chain_ritual_mana_starved
                elif storm <= 2:
                    mod += p.chain_fuel_value(storm)
                else:
                    mod += p.chain_fuel_value(storm) + storm * p.ritual_storm_scaling
                # If mid-chain but no finisher access, reduce commitment
                # (don't dump all rituals when we can't close the game)
                if p.storm_patience and storm >= 1:
                    has_finisher = any(
                        c.name in ('Grapeshot', 'Empty the Warrens', 'Wish')
                        for c in me.hand
                        if c.instance_id != card.instance_id
                    )
                    if not has_finisher:
                        mod -= p.chain_fuel_value(storm) * 0.5  # halve the bonus
            mod += p.chain_depth_value(storm)
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

        # Landfall deferral: if a landfall creature is in hand and castable
        # with CURRENT mana (without this land), defer the land play so the
        # creature resolves first — then the land triggers landfall.
        # e.g., with 4 mana and Omnath in hand: cast Omnath THEN play land
        # for +4 life from landfall.
        current_mana = len(me.untapped_lands) + me.mana_pool.total() + me._tron_mana_bonus()
        for spell in me.hand:
            if spell.template.is_land:
                continue
            oracle = (spell.template.oracle_text or '').lower()
            if 'landfall' not in oracle:
                continue
            # Check if this landfall creature is castable with current mana
            if game.can_cast(self.player_idx, spell):
                # Defer the land — make the spell get played first
                ev += p.land_landfall_defer_penalty
                break

        return ev

    def _score_cycling(self, card, snap, game, me, opp) -> float:
        """Score a cycling activation. Cycling costs 1-2 mana, puts card in GY, draws 1."""
        p = self.profile
        ev = p.card_draw_base  # cycling draws a card

        # Cycling creatures into GY is the Living End gameplan
        if card.template.is_creature:
            ev += p.cycling_creature_gy_value
            power = card.template.power or 0
            ev += power * p.cycling_power_scaling

        # Cycling cost matters — cheaper is better
        cost_data = card.template.cycling_cost_data
        if cost_data:
            if cost_data.get('life', 0) > 0:
                ev += p.cycling_life_pay_bonus
            elif cost_data.get('mana', 0) <= 1:
                ev += p.cycling_cheap_bonus

        # If we have a cascade spell in hand, cycling to fill GY is urgent
        has_cascade = any(getattr(c.template, 'is_cascade', False) for c in me.hand
                         if not c.template.is_land)
        if has_cascade:
            ev += p.cycling_cascade_ready_bonus

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
            # When opponent is low, attack more aggressively to close the game
            if opp.life <= self.profile.burn_low_life_threshold and self.archetype in ('aggro', 'tempo'):
                threshold -= self.profile.aggro_closing_threshold_reduction

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
            if opp.life <= self.profile.burn_low_life_threshold:
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

            # Prefer evasive creatures (flying), then highest power
            best = max(creatures, key=lambda c: (
                1 if Keyword.FLYING in c.keywords else 0,
                c.power or 0
            ))

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
