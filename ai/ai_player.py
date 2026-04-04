"""MTG AI Player — v5 (Unified Gameplan))
====================================
Every deck declares a Gameplan: an ordered sequence of strategic Goals.
The GoalEngine drives all main-phase decisions through a unified loop:

    1. Assess board state (clock, resources, threats)
    2. Check overrides (lethal, survival, goal transition)
    3. Score each legal play against the active goal's priorities
    4. Return the highest-priority play

The same loop handles aggro curving out, combo assembling pieces,
midrange grinding value, and control holding up answers.

Fallback: if no gameplan is registered for a deck, the legacy
TurnPlanner + evaluator pipeline handles decisions.
"""
from __future__ import annotations
import random
from typing import Dict, List, Optional, Tuple, Set, TYPE_CHECKING
from enum import Enum
from engine.game_state import Phase

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate, CardType, Keyword
    from engine.stack import StackItem


# ═══════════════════════════════════════════════════════════════════
# Archetype definitions (used by evaluator for role assessment)
# ═══════════════════════════════════════════════════════════════════

class ArchetypeStrategy(Enum):
    AGGRO = "aggro"
    MIDRANGE = "midrange"
    CONTROL = "control"
    COMBO = "combo"
    TEMPO = "tempo"
    RAMP = "ramp"


DECK_ARCHETYPES = {
    "Boros Energy":       ArchetypeStrategy.AGGRO,
    "Jeskai Blink":       ArchetypeStrategy.TEMPO,
    "Ruby Storm":         ArchetypeStrategy.COMBO,
    "Affinity":           ArchetypeStrategy.AGGRO,
    "Eldrazi Tron":       ArchetypeStrategy.RAMP,
    "Amulet Titan":       ArchetypeStrategy.COMBO,
    "Goryo's Vengeance":  ArchetypeStrategy.COMBO,
    "Neobrand":           ArchetypeStrategy.COMBO,
    "Domain Zoo":         ArchetypeStrategy.AGGRO,
    "Living End":         ArchetypeStrategy.COMBO,
    "Belcher":            ArchetypeStrategy.COMBO,
    "Dimir Midrange":     ArchetypeStrategy.MIDRANGE,
    "Izzet Prowess":      ArchetypeStrategy.AGGRO,
    "4c Omnath":          ArchetypeStrategy.MIDRANGE,
}

# Card name sets for combo sequencing


class AIPlayer:
    """Goal-oriented AI player.  All generic decisions go through the
    evaluator; only combo-specific sequencing is hand-written.

    v4: Integrated TurnPlanner + CombatPlanner for holistic turn reasoning.
    Combat decisions use combined attack configurations instead of per-creature scoring.
    Main phase decisions consider pre-combat removal sequencing.
    Response decisions use multi-factor threat assessment.
    """

    def __init__(self, player_idx: int, deck_name: str,
                 rng: random.Random = None):
        self.player_idx = player_idx
        self.deck_name  = deck_name
        self.archetype  = DECK_ARCHETYPES.get(deck_name,
                                               ArchetypeStrategy.MIDRANGE)
        self.rng = rng or random.Random()
        self._pw_activated_this_turn: Set[int] = set()
        self._turn_plan = None  # cached TurnPlan for current turn
        self._turn_plan_turn = -1  # turn number when plan was computed

        # Initialize planners
        from ai.turn_planner import TurnPlanner, CombatPlanner
        self.turn_planner = TurnPlanner()
        self.combat_planner = CombatPlanner()

        # Initialize the unified Gameplan engine
        from ai.gameplan import create_goal_engine
        self.goal_engine = create_goal_engine(deck_name)  # None if no plan registered

        # Strategic logger — injected externally (by ReplayGenerator or test harness)
        self.strategic_logger = None

        # Mulligan decider — extracted to ai/mulligan.py
        from ai.mulligan import MulliganDecider
        self._mulligan_decider = MulliganDecider(self.archetype, self.goal_engine)

        # Response decider — extracted to ai/response.py
        from ai.response import ResponseDecider
        self._response_decider = ResponseDecider(
            player_idx, self.turn_planner, self.strategic_logger)

    # ═══════════════════════════════════════════════════════════════
    # MULLIGAN  — delegates to MulliganDecider (ai/mulligan.py)
    # ═══════════════════════════════════════════════════════════════

    def decide_mulligan(self, hand: List["CardInstance"],
                        cards_in_hand: int) -> bool:
        """Return True to keep, False to mulligan.
        Delegates to MulliganDecider (ai/mulligan.py)."""
        from ai.mulligan import MulliganDecider

        lands = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]
        hand_names = [c.name for c in hand]

        # Always keep at 5 or fewer
        if cards_in_hand <= 5:
            if self.strategic_logger:
                self.strategic_logger.log_mulligan(
                    self.player_idx, self.deck_name, hand_names, cards_in_hand,
                    True, f"Auto-keep at {cards_in_hand} cards")
            return True

        # Auto-mulligan unplayable hands
        land_count = len(lands)
        if land_count == 0 or land_count >= 6:
            if self.strategic_logger:
                reason = "No lands" if land_count == 0 else f"{land_count} lands (flood)"
                self.strategic_logger.log_mulligan(
                    self.player_idx, self.deck_name, hand_names, cards_in_hand,
                    False, reason)
            return False

        # Delegate to MulliganDecider
        keep = self._mulligan_decider.decide(hand, cards_in_hand)

        if self.strategic_logger:
            reason = MulliganDecider.reason(hand, lands, spells, keep)
            self.strategic_logger.log_mulligan(
                self.player_idx, self.deck_name, hand_names, cards_in_hand,
                keep, reason)
        return keep

    def choose_cards_to_bottom(self, hand: List["CardInstance"],
                                count: int) -> List["CardInstance"]:
        """Delegate to MulliganDecider."""
        return self._mulligan_decider.choose_cards_to_bottom(hand, count)

    # ═══════════════════════════════════════════════════════════════
    # MAIN PHASE — top-level decision
    # ═══════════════════════════════════════════════════════════════

    def decide_main_phase(self, game: "GameState", excluded_cards: set = None) -> Optional[Tuple[str, "CardInstance", List[int]]]:
        """v5: Unified Gameplan-driven main phase decisions.

        All decks (combo, aggro, midrange, control) use the same GoalEngine
        loop. The GoalEngine selects the best play based on:
          1. Current goal priorities (deck-specific)
          2. Board state assessment (dynamic)
          3. Override checks (lethal, survival, opportunistic)

        Falls back to TurnPlanner + evaluator if no gameplan is registered.
        """
        from engine.game_state import Phase
        from engine.cards import CardType

        player = game.players[self.player_idx]

        # ── v5: GoalEngine unified decision loop ──
        if self.goal_engine:
            try:
                result = self.goal_engine.choose_action(
                    game, self.player_idx, excluded_cards)
                if result:
                    action_type, card, targets = result
                    if action_type == "play_land":
                        return ("play_land", card, [])
                    elif action_type == "cycle":
                        return ("cycle", card, [])
                    elif action_type == "cast_spell":
                        # Use GoalEngine's targets, but fall back to legacy targeting
                        # if the engine returned empty targets for a spell that needs them
                        if self._spell_requires_targets(card) and not targets:
                            targets = self._choose_targets(game, card)
                        if self._spell_requires_targets(card) and not targets:
                            pass  # skip this spell, fall through
                        else:
                            return ("cast_spell", card, targets)
                # GoalEngine returned None — no good plays available.
                # Do NOT fall through to legacy, as it would bypass
                # reactive_only, combo_piece, and other GoalEngine filters.
                return None
            except Exception as e:
                import sys
                print(f"[GoalEngine ERROR] {type(e).__name__}: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
                # Re-raise so bugs are caught immediately during development.
                # In production, wrap the game runner in its own try/except.
                raise

        # ── Legacy fallback for unregistered decks ──
        legal_plays = game.get_legal_plays(self.player_idx)
        if not legal_plays:
            return None

        if excluded_cards:
            legal_plays = [c for c in legal_plays if c.instance_id not in excluded_cards]
            if not legal_plays:
                return None

        lands  = [c for c in legal_plays if c.template.is_land]
        spells = [c for c in legal_plays if not c.template.is_land]

        # Play a land first
        if lands and player.lands_played_this_turn < (1 + player.extra_land_drops):
            # Filter out fetchlands that would kill us (auto-crack pays 1 life)
            from engine.card_database import FETCH_LAND_COLORS
            no_life_fetches = {"Prismatic Vista", "Fabled Passage", "Evolving Wilds", "Terramorphic Expanse"}
            safe_lands = [
                l for l in lands
                if l.name not in FETCH_LAND_COLORS
                or l.name in no_life_fetches
                or player.life > 1
            ]
            if safe_lands:
                land = self._choose_land_to_play(player, safe_lands, spells, game, self.player_idx)
                if land:
                    return ("play_land", land, [])

        # TurnPlanner holistic sequencing
        try:
            if self._turn_plan_turn != game.turn_number:
                from ai.turn_planner import extract_virtual_board
                vboard = extract_virtual_board(game, self.player_idx)
                self._turn_plan = self.turn_planner.plan_turn(vboard, game.turn_number)
                self._turn_plan_turn = game.turn_number

            # Pre-filter spells through legend rule to avoid wasting cards
            legend_safe = self._filter_legend_rule(player, spells)
            legend_safe_ids = {s.instance_id for s in legend_safe}

            if (self._turn_plan and self._turn_plan.pre_combat_actions
                    and game.current_phase == Phase.MAIN1):
                action_type, spell_id = self._turn_plan.pre_combat_actions[0]
                if spell_id in legend_safe_ids:
                    for spell in legend_safe:
                        if spell.instance_id == spell_id:
                            targets = self._choose_targets(game, spell)
                            if self._spell_requires_targets(spell) and not targets:
                                break
                            self._turn_plan.pre_combat_actions.pop(0)
                            return ("cast_spell", spell, targets)
                else:
                    # Skip this planned action — it would violate legend rule
                    self._turn_plan.pre_combat_actions.pop(0)

            if (self._turn_plan and self._turn_plan.post_combat_actions
                    and game.current_phase == Phase.MAIN2):
                action_type, spell_id = self._turn_plan.post_combat_actions[0]
                if spell_id in legend_safe_ids:
                    for spell in legend_safe:
                        if spell.instance_id == spell_id:
                            targets = self._choose_targets(game, spell)
                            if self._spell_requires_targets(spell) and not targets:
                                break
                            self._turn_plan.post_combat_actions.pop(0)
                            return ("cast_spell", spell, targets)
                else:
                    # Skip this planned action — it would violate legend rule
                    self._turn_plan.post_combat_actions.pop(0)

        except Exception:
            pass

        # Evaluator-driven spell selection
        filtered_spells = self._filter_legend_rule(player, spells)
        if filtered_spells:
            spell = self._choose_spell_to_cast(game, player, filtered_spells)
            if spell:
                targets = self._choose_targets(game, spell)
                if self._spell_requires_targets(spell) and not targets:
                    remaining = [s for s in filtered_spells if s is not spell]
                    while remaining:
                        spell = self._choose_spell_to_cast(game, player, remaining)
                        if not spell:
                            break
                        targets = self._choose_targets(game, spell)
                        if not self._spell_requires_targets(spell) or targets:
                            return ("cast_spell", spell, targets)
                        remaining = [s for s in remaining if s is not spell]
                else:
                    return ("cast_spell", spell, targets)

        # Consider equipping
        equip_action = self._consider_equip(game, player)
        if equip_action:
            return equip_action

        return None

    # ═══════════════════════════════════════════════════════════════
    # SPELL SELECTION — evaluator-driven
    # ═══════════════════════════════════════════════════════════════

    def _choose_land_to_play(self, player, lands, spells, game=None, player_idx=0):
        """Choose which land to play using the unified ManaPlanner."""
        if not lands:
            return None
        if game is not None:
            from ai.mana_planner import analyze_mana_needs, choose_best_land
            needs = analyze_mana_needs(game, player_idx)
            turn = getattr(game, 'turn_number', 1)
            library = game.players[player_idx].library
            return choose_best_land(lands, needs, turn=turn, library=library)
        # Fallback: basic color matching
        needed: Dict[str, int] = {}
        for spell in spells:
            cost = spell.template.mana_cost
            for color, count in [("W", cost.white), ("U", cost.blue),
                                  ("B", cost.black), ("R", cost.red),
                                  ("G", cost.green)]:
                if count > 0:
                    needed[color] = needed.get(color, 0) + count
        best, best_score = None, -1
        for land in lands:
            score = 0.0
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

    def _filter_legend_rule(self, player, spells):
        """Remove legendary permanents we already control from the spell list.
        Avoids wasting cards to the legend rule."""
        from engine.cards import Supertype
        controlled_legends = set()
        for c in player.battlefield:
            # Check supertypes list for Supertype.LEGENDARY
            supertypes = getattr(c.template, 'supertypes', [])
            is_leg = Supertype.LEGENDARY in supertypes if supertypes else False
            if is_leg:
                controlled_legends.add(c.template.name)
            # Also check planeswalkers (unique by name in modern rules)
            from engine.cards import CardType
            if CardType.PLANESWALKER in c.template.card_types:
                controlled_legends.add(c.template.name)

        filtered = []
        for spell in spells:
            supertypes = getattr(spell.template, 'supertypes', [])
            is_legendary = Supertype.LEGENDARY in supertypes if supertypes else False
            # Planeswalkers are effectively unique
            from engine.cards import CardType
            if CardType.PLANESWALKER in spell.template.card_types:
                is_legendary = True

            if is_legendary and spell.template.name in controlled_legends:
                continue  # skip — we already control this legend
            filtered.append(spell)
        return filtered

    def _spell_requires_targets(self, spell) -> bool:
        """Check if a spell requires targets to be cast legally.
        Uses tags and ability descriptions instead of hardcoded card names."""
        template = spell.template
        tags = template.tags

        # Tag-based: these categories always need targets
        if "counterspell" in tags:
            return True
        if "removal" in tags and "board_wipe" not in tags:
            # Targeted removal (not board wipes) needs a target
            return True
        if "blink" in tags:
            # Blink spells need a creature to target
            return True

        # Ability-based: check if any ability explicitly requires targets
        for ability in template.abilities:
            if ability.targets_required > 0:
                desc = ability.description.lower()
                if any(kw in desc for kw in ["destroy", "exile", "bounce",
                                              "return", "blink", "flicker",
                                              "counter", "damage"]):
                    return True

        return False

    def _choose_spell_to_cast(self, game, player, spells):
        """Pick the spell that produces the best expected game state."""
        from ai.evaluator import estimate_spell_value, assess_role, Role

        opponent = game.players[1 - self.player_idx]
        role = assess_role(game, self.player_idx)
        available_mana = len(player.untapped_lands) + player.mana_pool.total()

        scored = []
        for spell in spells:
            val = estimate_spell_value(spell, game, self.player_idx)

            # Mana reservation: if we have instant-speed answers, penalise
            # tapping out for a sorcery-speed spell
            if not (spell.template.is_instant or spell.template.has_flash):
                instant_answers = [
                    c for c in player.hand
                    if (c.template.is_instant or c.template.has_flash)
                    and ("removal" in c.template.tags or
                         "counterspell" in c.template.tags)
                    and c is not spell
                ]
                if instant_answers and opponent.creatures:
                    from engine.cards import Keyword as Kw, CardType as CT
                    eff_cost = spell.template.cmc
                    if Kw.AFFINITY in spell.template.keywords:
                        eff_cost = max(0, eff_cost - sum(
                            1 for c in player.battlefield
                            if CT.ARTIFACT in c.template.card_types))
                    cheapest_answer = min(c.template.cmc for c in instant_answers)
                    mana_after = available_mana - eff_cost
                    if mana_after < cheapest_answer:
                        val -= 3.0  # tapping out when we have answers is risky

            scored.append((spell, val))

        scored.sort(key=lambda x: x[1], reverse=True)
        if scored and scored[0][1] > 0:
            return scored[0][0]
        return None

    # ═══════════════════════════════════════════════════════════════
    # TARGETING — evaluator-driven
    # ═══════════════════════════════════════════════════════════════

    def _choose_targets(self, game, spell) -> List[int]:
        """Choose targets for a spell using the evaluator."""
        from ai.evaluator import estimate_removal_value, estimate_permanent_value

        targets = []
        opp_idx = 1 - self.player_idx
        opp = game.players[opp_idx]

        for ability in spell.template.abilities:
            if ability.targets_required <= 0:
                continue

            desc = ability.description.lower()

            # --- Blink spells: target OWN creatures (not opponent's) ---
            if "blink" in spell.template.tags and "exile" in desc:
                me = game.players[self.player_idx]
                if me.creatures:
                    # Prefer creatures with ETB value (tag-based, not hardcoded)
                    etb_creatures = [c for c in me.creatures
                                     if "etb_value" in c.template.tags]
                    if etb_creatures:
                        best = max(etb_creatures,
                                   key=lambda c: estimate_permanent_value(
                                       c, me, game, self.player_idx))
                        targets.append(best.instance_id)
                    else:
                        # No ETB creatures — blink the most valuable to protect it
                        best = max(me.creatures,
                                   key=lambda c: estimate_permanent_value(
                                       c, me, game, self.player_idx))
                        targets.append(best.instance_id)
                continue  # Don't fall through to removal targeting

            # --- Destroy / exile / damage a creature ---
            if "creature" in desc and ("destroy" in desc or "exile" in desc or "damage" in desc):
                target = self._pick_best_removal_target(
                    spell, opp.creatures, opp, game, opp_idx)
                if target:
                    targets.append(target.instance_id)

            # --- Destroy / exile artifact ---
            elif "artifact" in desc and ("destroy" in desc or "exile" in desc):
                artifacts = [c for c in opp.battlefield
                             if not c.template.is_land and
                             any(str(ct) == "CardType.ARTIFACT" or
                                 (hasattr(ct, 'name') and ct.name == "ARTIFACT")
                                 for ct in c.template.card_types)]
                if artifacts:
                    target = self._pick_best_removal_target(
                        spell, artifacts, opp, game, opp_idx)
                    if target:
                        targets.append(target.instance_id)

            # --- Destroy / exile enchantment ---
            elif "enchantment" in desc and ("destroy" in desc or "exile" in desc):
                enchantments = [c for c in opp.battlefield
                                if any(str(ct) == "CardType.ENCHANTMENT" or
                                       (hasattr(ct, 'name') and ct.name == "ENCHANTMENT")
                                       for ct in c.template.card_types)]
                if enchantments:
                    target = max(enchantments,
                                 key=lambda c: estimate_permanent_value(
                                     c, opp, game, opp_idx))
                    targets.append(target.instance_id)

            # --- Destroy / exile nonland permanent ---
            elif "permanent" in desc or "nonland" in desc:
                nonlands = [c for c in opp.battlefield if not c.template.is_land]
                if nonlands:
                    target = self._pick_best_removal_target(
                        spell, nonlands, opp, game, opp_idx)
                    if target:
                        targets.append(target.instance_id)

            # --- "any target" / burn spells ---
            elif "player" in desc or "any" in desc or "target" in desc:
                # Decide: go face or remove a creature?
                if opp.creatures:
                    best_creature = self._pick_best_removal_target(
                        spell, opp.creatures, opp, game, opp_idx)
                    creature_val = estimate_removal_value(
                        best_creature, spell.template.cmc,
                        opp, game, opp_idx) if best_creature else 0

                    # Face damage value: proportional to how close to lethal
                    damage = self._spell_damage(spell, game)
                    face_val = damage * (20.0 / max(opp.life, 1))

                    if creature_val > face_val:
                        targets.append(best_creature.instance_id)
                    else:
                        targets.append(-1)  # go face
                else:
                    targets.append(-1)  # no creatures, go face

        return targets

    def _pick_best_removal_target(self, spell, candidates, controller,
                                   game, controller_idx):
        """Pick the removal target that removes the most value from the board.

        Uses the evaluator's permanent value function — naturally handles
        equipment, buffs, keywords, notable cards, everything.
        """
        from ai.evaluator import estimate_removal_value

        if not candidates:
            return None

        removal_cmc = spell.template.cmc
        damage = self._spell_damage(spell, game)

        best, best_val = None, -999
        for c in candidates:
            # Can we actually kill it?
            if damage < 99:
                actual_toughness = c.toughness or 0
                remaining = actual_toughness - (getattr(c, 'damage_marked', 0) or 0)
                if damage < remaining:
                    continue  # can't kill it with damage, skip
                    # (destroy/exile effects use damage=99)

            val = estimate_removal_value(c, removal_cmc, controller,
                                          game, controller_idx)
            if val > best_val:
                best_val = val
                best = c

        # If damage-based removal can't kill anything, return None.
        # Do NOT fall back to targeting the highest-value creature —
        # wasting a removal spell on something that survives is card disadvantage.
        return best

    def _spell_damage(self, spell, game=None) -> int:
        """Estimate how much damage a spell deals.  99 = destroy/exile."""
        name = spell.template.name
        damage_map = {
            "Lightning Bolt": 3, "Lava Dart": 1, "Unholy Heat": 6,
            "Orcish Bowmasters": 1,
            "Tribal Flames": 5, "Grapeshot": 1,
        }
        if name in damage_map:
            return damage_map[name]
        # Dynamic damage for energy-based removal
        if name == "Galvanic Discharge":
            # Use actual player energy from game state if available
            energy = 0
            if game:
                energy = game.players[self.player_idx].energy_counters
            else:
                energy = getattr(self, '_current_energy', 0)
            return 2 + min(energy, 5)
        # Check ability descriptions for damage amounts
        for ab in spell.template.abilities:
            desc = ab.description.lower()
            if "destroy" in desc or "exile" in desc:
                return 99
            if "damage" in desc:
                for word in desc.split():
                    try:
                        return int(word)
                    except ValueError:
                        continue
        return 99  # default: assume destroy/exile

    # ═══════════════════════════════════════════════════════════════
    # EQUIP — evaluator-driven
    # ═══════════════════════════════════════════════════════════════

    def _consider_equip(self, game, player):
        """Consider equipping unattached equipment to a creature.

        Uses the evaluator: equipping is worth it if the buff value
        exceeds the mana cost opportunity cost.
        """
        from ai.evaluator import estimate_permanent_value

        # Find unattached equipment on our battlefield
        unattached = [
            c for c in player.battlefield
            if c.template.equip_cost is not None
            and "equipment_unattached" in c.instance_tags
        ]
        if not unattached or not player.creatures:
            return None

        available_mana = len(player.untapped_lands) + player.mana_pool.total()

        best_action = None
        best_value = 0.0

        for equip in unattached:
            cost = equip.template.equip_cost
            if available_mana < cost:
                continue

            # Find the best creature to equip to
            for creature in player.creatures:
                # Skip creatures that already have this equipment
                equip_tag = None
                if "Cranial Plating" in equip.template.name:
                    equip_tag = "cranial_plating_equipped"
                elif "Nettlecyst" in equip.template.name:
                    equip_tag = "nettlecyst_equipped"

                if equip_tag and equip_tag in creature.instance_tags:
                    continue  # already equipped

                # Estimate value: how much does equipping improve this creature?
                current_val = estimate_permanent_value(
                    creature, player, game, self.player_idx)

                # Simulate the buff
                from engine.cards import CardType
                artifact_count = sum(
                    1 for c in player.battlefield
                    if CardType.ARTIFACT in c.template.card_types)

                # Cranial Plating: +N/+0 where N = artifact count
                # Nettlecyst: +N/+N where N = artifact count
                buff_power = artifact_count
                buff_value = buff_power * 1.5  # power is worth ~1.5 per point
                if "Nettlecyst" in equip.template.name:
                    buff_value += artifact_count * 0.8  # toughness too

                # Prefer creatures with evasion (flying, menace) for equipment
                from engine.cards import Keyword
                if Keyword.FLYING in creature.keywords:
                    buff_value *= 1.5
                if Keyword.MENACE in creature.keywords:
                    buff_value *= 1.3
                if Keyword.TRAMPLE in creature.keywords:
                    buff_value *= 1.2

                # Subtract opportunity cost of mana
                net_value = buff_value - cost * 0.5

                if net_value > best_value:
                    best_value = net_value
                    best_action = ("equip", equip, [creature.instance_id])

        return best_action

    # ═══════════════════════════════════════════════════════════════
    # COMBAT — evaluator-driven
    # ═══════════════════════════════════════════════════════════════

    def decide_attackers(self, game) -> List["CardInstance"]:
        """Decide which creatures to attack with using the CombatPlanner.

        v4: Uses holistic combined-attack evaluation instead of per-creature scoring.
        Considers all attack configurations, simulates likely blocking responses,
        and evaluates post-combat board states.
        """
        from ai.turn_planner import extract_virtual_board
        from engine.cards import Keyword

        valid = game.get_valid_attackers(self.player_idx)
        if not valid:
            if self.strategic_logger:
                self.strategic_logger.log_attack(
                    self.player_idx, [], game, "No valid attackers available")
            return []

        # Discard-pump creatures: detect from oracle text ("Discard a card: ...+1/+1")
        # Pre-combat pump maximizes damage dealt this turn.
        me = game.players[self.player_idx]
        for creature in valid:
            oracle = (creature.template.oracle_text or "").lower()
            if "discard a card" in oracle and "+1/+1" in oracle:
                discardable = [c for c in me.hand
                               if not c.template.is_land
                               and c.template.cmc > len(me.untapped_lands) + 2]
                land_count = len([c for c in me.battlefield if c.template.is_land])
                if land_count >= 5:
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
                        game.log.append(f"T{game.turn_number} P{self.player_idx+1}: "
                                        f"{creature.name} discards {card_to_discard.name} "
                                        f"(now {creature.power}/{creature.toughness})")
                break  # Only pump one creature per combat

        opponent = game.players[1 - self.player_idx]
        me = game.players[self.player_idx]

        # Lethal check: if total power >= opponent life, attack with everything
        total_power = sum(c.power for c in valid if c.power and c.power > 0)
        if total_power >= opponent.life:
            if self.strategic_logger:
                names = [c.name for c in valid]
                self.strategic_logger.log_attack(
                    self.player_idx, names, game,
                    f"Lethal on board: {total_power} power >= {opponent.life} life. Alpha strike.")
            return valid

        # ── Role-aware combat modifiers ──
        # Consult GoalEngine state for strategic context
        attack_threshold = 0.0  # default: only attack if score_delta > 0
        role_reason = ""
        if self.goal_engine:
            if self.goal_engine.turning_the_corner:
                attack_threshold = -2.0  # accept slightly unfavorable trades to close
                role_reason = "Turned the corner — attacking aggressively. "
            elif self.goal_engine.on_fallback_plan:
                attack_threshold = -1.5  # Plan B: attack with whatever we have
                role_reason = "Plan B — attacking with available bodies. "
            elif self.goal_engine._role_cache is not None:
                from ai.evaluator import Role
                if self.goal_engine._role_cache == Role.CONTROL:
                    attack_threshold = 1.5  # control: only attack if clearly profitable
                    role_reason = "Control role — conservative attacks only. "
                elif self.goal_engine._role_cache == Role.BEATDOWN:
                    attack_threshold = -0.5  # beatdown: push damage
                    role_reason = "Beatdown role — pushing damage. "

        # ── v4: Use CombatPlanner for holistic attack evaluation ──
        try:
            vboard = extract_virtual_board(game, self.player_idx)
            attack_plan, score_delta = self.combat_planner.plan_attack(vboard)

            if not attack_plan or score_delta <= attack_threshold:
                # CombatPlanner says don't attack (adjusted for role)
                if self.strategic_logger and role_reason:
                    self.strategic_logger.log_attack(
                        self.player_idx, [], game,
                        f"{role_reason}But CombatPlanner score {score_delta:.1f} below threshold {attack_threshold:.1f} — holding.")
                return []

            # Map virtual creatures back to real CardInstances
            attack_ids = {vc.instance_id for vc in attack_plan}
            real_attackers = [c for c in valid if c.instance_id in attack_ids]

            if real_attackers:
                if self.strategic_logger:
                    names = [c.name for c in real_attackers]
                    held = [c.name for c in valid if c not in real_attackers]
                    reason = f"{role_reason}CombatPlanner: attack score delta {score_delta:.1f} (threshold {attack_threshold:.1f})"
                    if held:
                        reason += f". Holding back {', '.join(held)} as blockers."
                    self.strategic_logger.log_attack(
                        self.player_idx, names, game, reason,
                        alternatives=[f"Hold all (score 0)"] if score_delta > 0 else [])
                return real_attackers
        except Exception as e:
            # Fallback to legacy per-creature evaluation if planner fails
            pass  # Silently fall through to legacy

        # ── Legacy fallback: per-creature evaluation ──
        from ai.evaluator import estimate_attack_value
        opp_blockers = game.get_valid_blockers(1 - self.player_idx)
        attackers = []
        for creature in valid:
            val = estimate_attack_value(
                creature, opponent, opp_blockers, me.life,
                game, self.player_idx)
            if val > 0:
                attackers.append((creature, val))
        attackers.sort(key=lambda x: x[1], reverse=True)
        result = [c for c, v in attackers]
        if self.strategic_logger:
            if result:
                names = [c.name for c in result]
                held = [c.name for c in valid if c not in result]
                reason = f"Legacy evaluator: attacking with profitable creatures"
                if held:
                    reason += f". Holding back {', '.join(held)}."
                self.strategic_logger.log_attack(
                    self.player_idx, names, game, reason)
            else:
                self.strategic_logger.log_attack(
                    self.player_idx, [], game,
                    "No profitable attacks — holding back all creatures")
        return result

    def decide_blockers(self, game, attackers) -> Dict[int, List[int]]:
        """Decide how to block using the evaluator."""
        from ai.board_eval import evaluate_action, Action, ActionType
        from engine.cards import Keyword

        valid_blockers = game.get_valid_blockers(self.player_idx)
        if not valid_blockers or not attackers:
            return {}

        me = game.players[self.player_idx]
        blocks: Dict[int, List[int]] = {}
        used: Set[int] = set()

        # Sort attackers by threat (highest power first)
        sorted_attackers = sorted(attackers,
                                  key=lambda a: a.power or 0, reverse=True)

        for attacker in sorted_attackers:
            best_blocker = None
            best_val = 0.0  # only block if value > 0

            for blocker in valid_blockers:
                if blocker.instance_id in used:
                    continue
                # Can't block flyers without flying/reach
                if Keyword.FLYING in attacker.keywords:
                    if (Keyword.FLYING not in blocker.keywords and
                            Keyword.REACH not in blocker.keywords):
                        continue

                val = evaluate_action(
                    game, self.player_idx, Action(ActionType.BLOCK, {'attacker': attacker, 'blocker': blocker}))
                if val > best_val:
                    best_val = val
                    best_blocker = blocker

            if best_blocker:
                blocks[attacker.instance_id] = [best_blocker.instance_id]
                used.add(best_blocker.instance_id)

                # Double-block if single blocker can't kill attacker
                a_tough = attacker.toughness or 0
                b_power = best_blocker.power or 0
                if b_power < a_tough and Keyword.DEATHTOUCH not in best_blocker.keywords:
                    # Look for a second blocker
                    for b2 in valid_blockers:
                        if b2.instance_id in used:
                            continue
                        if Keyword.FLYING in attacker.keywords:
                            if (Keyword.FLYING not in b2.keywords and
                                    Keyword.REACH not in b2.keywords):
                                continue
                        combined = b_power + (b2.power or 0)
                        if combined >= a_tough:
                            blocks[attacker.instance_id].append(b2.instance_id)
                            used.add(b2.instance_id)
                            break

        return blocks

    # ═══════════════════════════════════════════════════════════════
    # STACK RESPONSES — delegates to ResponseDecider (ai/response.py)
    # ═══════════════════════════════════════════════════════════════

    def decide_response(self, game, stack_item) -> Optional[Tuple["CardInstance", List[int]]]:
        """Delegate to ResponseDecider."""
        # Update logger reference (may have been set after __init__)
        self._response_decider.strategic_logger = self.strategic_logger
        return self._response_decider.decide_response(
            game, stack_item,
            pick_removal_target_fn=self._pick_best_removal_target
        )

    def _evaluate_stack_threat(self, game, stack_item) -> float:
        """Delegate to ResponseDecider."""
        return self._response_decider.evaluate_stack_threat(game, stack_item)

