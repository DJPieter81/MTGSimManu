"""EV-Based AI Player — clean replacement for AIPlayer.

Architecture: get legal plays → score each → pick best.
No concern pipeline. No GoalEngine. No hardcoded thresholds.

Each archetype has a strategy function that scores candidate plays.
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

DECK_ARCHETYPES = {
    "Boros Energy":       "aggro",
    "Domain Zoo":         "aggro",
    "Affinity":           "aggro",
    "Izzet Prowess":      "aggro",
    "Dimir Midrange":     "midrange",
    "4c Omnath":          "control",
    "Jeskai Blink":       "control",
    "Eldrazi Tron":       "ramp",
    "Ruby Storm":         "combo",
    "Amulet Titan":       "combo",
    "Goryo's Vengeance":  "combo",
    "Living End":         "combo",
}


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

    Interface matches what GameRunner expects from AIPlayer.
    """

    def __init__(self, player_idx: int, deck_name: str,
                 rng: random.Random = None):
        self.player_idx = player_idx
        self.deck_name = deck_name
        self.archetype = DECK_ARCHETYPES.get(deck_name, "midrange")
        self.rng = rng or random.Random()
        self._pw_activated_this_turn: Set[int] = set()
        self.strategic_logger = None

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

        # Filter legends we already control
        spells = self._filter_legend_rule(me, spells)

        candidates: List[Play] = []

        # Score land plays
        if lands and me.lands_played_this_turn < (1 + me.extra_land_drops):
            from engine.card_database import FETCH_LAND_COLORS
            safe_lands = [
                l for l in lands
                if l.name not in FETCH_LAND_COLORS or me.life > 1
                or l.name in {"Prismatic Vista", "Fabled Passage", "Evolving Wilds"}
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
            # Multi-mode cards like Drown in the Loch (counter OR destroy)
            # should still be considered for their non-counter mode.
            tags = getattr(spell.template, 'tags', set())
            if 'counterspell' in tags and 'removal' not in tags:
                continue

            # Skip reactive-only INSTANTS/SORCERIES unless dying.
            # Creatures in reactive_only (Endurance, Subtlety) can still be
            # cast for their body — a 3/4 blocker is valuable even without
            # the ETB triggering optimally.
            if spell.name in self._reactive_only:
                if not spell.template.is_creature:
                    if not (snap.am_dead_next or
                            (snap.opp_power >= 3 and snap.opp_clock <= 3)):
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

        # Only pass if best EV is very negative (all plays hurt us)
        if best.ev < -5.0:
            return None

        return (best.action, best.card, best.targets)

    # ═══════════════════════════════════════════════════════════
    # SCORING — per-archetype spell evaluation
    # ═══════════════════════════════════════════════════════════

    def _score_spell(self, card: "CardInstance", snap: EVSnapshot,
                     game: "GameState", me, opp) -> float:
        """Score a spell. The archetype determines what matters."""
        t = card.template
        tags = getattr(t, 'tags', set())
        cmc = t.cmc or 0

        # Start with creature/permanent value
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
                # Base penalty for 2-for-1 card disadvantage
                ev -= 6.0
                if 'removal' in tags:
                    best_val = self._best_removal_target_value(card, game, opp)
                    if best_val < 4.0:
                        ev -= 8.0  # not worth evoking for small creatures
                    else:
                        ev += best_val  # valuable target
                    # Under heavy pressure: evoking is more justified
                    if snap.opp_power >= snap.my_life - 3 and snap.opp_power > 0:
                        ev += 8.0  # survival evoke — worth the 2-for-1
                    elif snap.am_dead_next:
                        ev += 12.0  # absolutely must evoke to survive
                # Never evoke with no targets
                if snap.opp_creature_count == 0 and 'removal' in tags:
                    ev -= 20.0

        # ── Creature deployment ──
        is_creature = t.is_creature
        if is_creature and not is_evoke:
            ev += creature_value(card) * 1.5
            # Haste: immediate attack value
            from engine.cards import Keyword
            if Keyword.HASTE in getattr(t, 'keywords', set()):
                ev += (card.power or 0) * 1.0

        # ── Removal ──
        # Only penalize removal when NO creatures AND card isn't also a creature
        if 'removal' in tags and not is_evoke:
            best_target_val = self._best_removal_target_value(card, game, opp)
            if best_target_val > 0:
                ev += best_target_val * 1.2
            elif snap.opp_creature_count == 0 and not is_creature:
                ev -= 3.0  # pure removal with no targets = waste

        # ── Board wipe ──
        if 'board_wipe' in tags:
            opp_board = sum(creature_value(c) for c in opp.creatures)
            my_board = sum(creature_value(c) for c in me.creatures)
            if snap.opp_creature_count == 0:
                ev -= 15.0  # NEVER wrath an empty board
            else:
                ev += opp_board - my_board  # good if we clear more than we lose
                if snap.opp_creature_count >= 3:
                    ev += 5.0  # extra value for multi-kill

        # ── Burn (face damage) ──
        from decks.card_knowledge_loader import get_burn_damage
        burn_dmg = get_burn_damage(t.name)
        if burn_dmg > 0:
            if burn_dmg >= snap.opp_life:
                ev += 100.0  # lethal!
            elif self.archetype == "aggro":
                ev += burn_dmg * 1.5  # aggro values face damage
            else:
                # Non-aggro: prefer using as removal
                pass

        # ── Card draw / cantrips ──
        if 'cantrip' in tags or 'draw' in tags:
            # Base draw value scales with how much we need cards
            draw_val = 4.0
            if snap.my_hand_size <= 2:
                draw_val += 4.0  # desperately need cards
            elif snap.my_hand_size <= 4:
                draw_val += 2.0  # could use more gas
            oracle = (t.oracle_text or '').lower()
            if 'draw two' in oracle or 'draws two' in oracle:
                draw_val += 4.0
            if 'draw three' in oracle:
                draw_val += 7.0
            # Control/midrange values card draw more — needs to find answers
            if self.archetype in ("control", "midrange"):
                draw_val += 2.0
            ev += draw_val

        # ── Non-creature permanents (artifacts, enchantments, planeswalkers) ──
        from engine.cards import CardType
        if not is_creature and not t.is_land:
            if CardType.PLANESWALKER in t.card_types:
                ev += 6.0  # planeswalkers generate recurring value
            elif CardType.ARTIFACT in t.card_types and 'equipment' not in tags:
                ev += 2.0  # artifacts (medallions, etc.)
            elif CardType.ENCHANTMENT in t.card_types:
                ev += 2.0

        # ── Rituals (combo fuel) ──
        if 'ritual' in tags:
            if self.archetype == "combo":
                ev += 5.0  # rituals are critical for combo
            else:
                ev += 1.0  # marginal for non-combo

        # ── Past in Flames — bonus for rich graveyard ──
        if 'flashback' in tags and self.archetype == "combo":
            gy_rituals = sum(1 for c in me.graveyard if 'ritual' in getattr(c.template, 'tags', set()))
            gy_cantrips = sum(1 for c in me.graveyard if 'cantrip' in getattr(c.template, 'tags', set()))
            ev += gy_rituals * 2.0 + gy_cantrips * 1.0  # scale with GY fuel

        # ── Tutors (Wish) — find the finisher ──
        if 'tutor' in tags and self.archetype == "combo":
            # Wish finds the finisher from sideboard — critical combo piece
            # Value scales with storm count: higher storm = tutor is more urgent
            tutor_val = 6.0
            if me.spells_cast_this_turn >= 3:
                tutor_val += 8.0  # we're mid-chain, need to find finisher NOW
            elif me.spells_cast_this_turn >= 1:
                tutor_val += 4.0  # starting to chain, tutor is good
            ev += tutor_val

        # ── Cost reducers / engines ──
        if 'cost_reducer' in tags:
            if self.archetype == "combo":
                ev += 8.0  # engine is the most important piece
            else:
                ev += 3.0

        # ── ETB value ──
        if 'etb_value' in tags:
            ev += 3.0

        # ── Gameplan role bonuses ──
        if card.name in self._payoff_names:
            ev += 6.0
        elif card.name in self._engine_names:
            ev += 5.0
        elif card.name in self._fuel_names:
            if self.archetype == "combo":
                ev += 3.0

        # ── Archetype-specific adjustments ──
        ev += self._archetype_modifier(card, snap, game, me, opp)

        # ── Mana efficiency ──
        if snap.my_mana > 0 and cmc > 0:
            # Bonus for using mana efficiently (on-curve)
            ev += min(cmc / snap.my_mana, 1.0) * 2.0

        # ── Mana holdback penalty (control/midrange with instants in hand) ──
        if self.archetype in ("control", "midrange"):
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
                    ev -= 2.0  # penalty for tapping out with answers in hand

        # ── Storm finisher sequencing ──
        # Storm finishers (Grapeshot, Empty the Warrens) should be cast LAST
        # in the chain to maximize storm count.
        if self.archetype == "combo":
            from engine.cards import Keyword as Kw
            if Kw.STORM in getattr(t, 'keywords', set()):
                storm_copies = me.spells_cast_this_turn + 1
                if storm_copies >= snap.opp_life:
                    ev += 100.0  # lethal storm! Cast now!
                else:
                    # Check if we have more fuel to chain first
                    fuel_in_hand = sum(
                        1 for c in me.hand
                        if c.instance_id != card.instance_id
                        and not c.template.is_land
                        and game.can_cast(self.player_idx, c)
                        and 'storm' not in {kw.value if hasattr(kw, 'value') else str(kw)
                                             for kw in getattr(c.template, 'keywords', set())}
                    )
                    if fuel_in_hand > 0:
                        ev -= 20.0  # HOLD the finisher — cast fuel first
                    elif storm_copies >= 8:
                        ev += 15.0  # high storm count, fire for big tokens/damage
                    elif storm_copies >= 5:
                        ev += 5.0  # decent count, fire if no fuel left
                    else:
                        ev -= 30.0  # storm 1-4 with no fuel = waste the finisher

        # ── Survival mode: when facing lethal, boost survival plays ──
        if snap.am_dead_next:
            if 'removal' in tags and snap.opp_creature_count > 0:
                ev += 6.0  # removing the lethal threat
            if t.is_creature and (t.toughness or 0) >= 3:
                ev += 5.0  # deploying a blocker
            if 'board_wipe' in tags and snap.opp_creature_count > 0:
                ev += 8.0  # wrath saves us

        return ev

    def _archetype_modifier(self, card, snap: EVSnapshot,
                            game: "GameState", me, opp) -> float:
        """Per-archetype adjustments to spell EV."""
        t = card.template
        tags = getattr(t, 'tags', set())
        mod = 0.0

        if self.archetype == "aggro":
            # Aggro: deploy threats fast, burn face
            if t.is_creature and (t.cmc or 0) <= snap.turn_number:
                mod += 2.0  # on-curve creature
            if snap.opp_life <= 10:
                from decks.card_knowledge_loader import get_burn_damage
                if get_burn_damage(t.name) > 0:
                    mod += 3.0  # burn when opponent is low

        elif self.archetype == "midrange":
            # Midrange: interact first, deploy threats, grind value
            if 'removal' in tags and snap.opp_creature_count > 0:
                mod += 4.0  # PRIORITY: remove threats when they exist
                # Extra urgency when opponent has high-power creatures
                if snap.opp_power >= 4:
                    mod += 3.0
            if t.is_creature:
                if (t.cmc or 0) <= 3:
                    mod += 3.0  # cheap creatures should be deployed early
                if 'card_advantage' in tags:
                    mod += 3.0  # value creatures are great
                if t.has_flash:
                    mod += 2.0
            # Discard (Thoughtseize) is best early, but still good later
            if 'discard' in tags:
                if snap.turn_number <= 4:
                    mod += 4.0  # devastating early
                elif snap.opp_hand_size >= 3:
                    mod += 2.0  # still good if opponent has cards

        elif self.archetype == "control":
            # Control (Omnath/Jeskai): survive early, develop mana, deploy value engine
            cmc = t.cmc or 0

            # EARLY GAME (T1-6): removal is critical, cheap plays are priority
            if snap.turn_number <= 6:
                if 'removal' in tags and snap.opp_creature_count > 0:
                    mod += 6.0  # MUST answer early threats
                if cmc <= 2 and not t.is_land:
                    mod += 3.0  # cheap plays develop the board early
                # Planeswalkers are high priority early (Wrenn T3, Teferi T5)
                from engine.cards import CardType as CT
                if CT.PLANESWALKER in t.card_types:
                    mod += 4.0  # early PW = recurring value

            # MID GAME (T7-12): deploy payoffs, wraths, value
            elif snap.turn_number <= 12:
                if 'removal' in tags and snap.opp_creature_count > 0:
                    mod += 4.0
                if 'board_wipe' in tags and snap.opp_creature_count >= 2:
                    mod += 6.0
                if card.name in self._payoff_names:
                    mod += 6.0  # deploy Omnath/Solitude/Quantum Riddler
                if t.is_creature:
                    mod += 3.0  # ANY creature is valuable for blocking

            # LATE GAME (T13+): close it out
            else:
                if t.is_creature:
                    mod += 4.0  # deploy threats to close
                if card.name in self._payoff_names:
                    mod += 5.0

            # Card draw is always good for control — need to find answers
            if 'draw' in tags or 'cantrip' in tags:
                mod += 2.0

        elif self.archetype == "combo":
            # Combo (Storm): chain spells aggressively, build toward lethal
            if 'cantrip' in tags or 'draw' in tags:
                mod += 3.0  # dig for pieces / finisher
            if 'ritual' in tags:
                mod += 2.0  # rituals fuel the chain
            if me.spells_cast_this_turn >= 3:
                mod += 4.0  # mid-chain — every spell matters
            if me.spells_cast_this_turn >= 6:
                mod += 4.0  # deep chain — very close to lethal

        elif self.archetype == "ramp":
            # Ramp: develop mana, deploy fatties
            if 'mana_source' in tags or 'ramp' in tags:
                mod += 3.0
            if (t.cmc or 0) >= 5 and t.is_creature:
                mod += 4.0  # deploy finisher

        return mod

    def _score_land(self, land, me, spells, game) -> float:
        """Score a land play. Generally very high priority."""
        ev = 10.0  # lands are almost always correct to play

        # Untapped is much better when we have spells to cast
        has_castable_spells = any(
            (s.template.cmc or 0) <= len(me.untapped_lands) + 1
            for s in spells if not s.template.is_land
        )
        if not land.template.enters_tapped:
            ev += 5.0 if has_castable_spells else 2.0
        else:
            if has_castable_spells:
                ev -= 3.0  # tapped land when we need mana NOW is bad

        # Color fixing
        existing_colors = set()
        for l in me.lands:
            existing_colors.update(l.template.produces_mana)
        new_colors = set(land.template.produces_mana) - existing_colors
        ev += len(new_colors) * 4.0  # each new color is very valuable

        # Fetch lands: high priority for color fixing and deck thinning
        from engine.card_database import FETCH_LAND_COLORS
        if land.name in FETCH_LAND_COLORS:
            ev += 3.0  # fetches fix colors AND thin deck

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
                val *= 0.6  # 40% penalty for inefficient removal
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

            # Archetype-based attack threshold
            threshold = 0.0
            if self.archetype == "aggro":
                threshold = -1.0  # aggro pushes damage
            elif self.archetype == "control":
                threshold = 0.5  # control still needs to close games

            if attack_plan and score_delta > threshold:
                attack_ids = {vc.instance_id for vc in attack_plan}
                return [c for c in valid if c.instance_id in attack_ids]
        except Exception:
            pass

        # Fallback: attack with everything unless control is facing blockers
        if self.archetype == "control" and opp_blockers:
            return []  # control holds back vs blockers
        return valid  # everyone else attacks

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
        # Must check before removal, because burn cards often have removal tag
        # but can go face when no creatures exist (Lightning Bolt, Tribal Flames)
        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(t.name)
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
            # For aggro: face is worth ~1.5 per damage point when opp > 10
            # A creature kill is worth its creature_value
            face_val = dmg * 1.5 if self.archetype == "aggro" else dmg * 0.5
            if opp.life <= 10 and self.archetype == "aggro":
                face_val = dmg * 2.5  # burn is premium when opp is low

            if best_kill_id and best_kill_val > face_val:
                return [best_kill_id]  # kill the creature
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

        if 'counterspell' in tags:
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
