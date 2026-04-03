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

    # ═══════════════════════════════════════════════════════════════
    # MULLIGAN  (pre-game — no board to evaluate, stays heuristic)
    # ═══════════════════════════════════════════════════════════════

    def decide_mulligan(self, hand: List["CardInstance"],
                        cards_in_hand: int) -> bool:
        """Return True to keep, False to mulligan.
        v5: Uses GoalEngine's gameplan-aware mulligan when available."""
        from engine.cards import CardType

        lands  = [c for c in hand if c.template.is_land]
        spells = [c for c in hand if not c.template.is_land]
        land_count = len(lands)

        hand_names = [c.name for c in hand]

        # Always keep at 5 or fewer
        if cards_in_hand <= 5:
            if self.strategic_logger:
                self.strategic_logger.log_mulligan(
                    self.player_idx, self.deck_name, hand_names, cards_in_hand,
                    True, f"Auto-keep at {cards_in_hand} cards")
            return True

        # Auto-mulligan unplayable hands
        if land_count == 0 or land_count >= 6:
            if self.strategic_logger:
                reason = "No lands" if land_count == 0 else f"{land_count} lands (flood)"
                self.strategic_logger.log_mulligan(
                    self.player_idx, self.deck_name, hand_names, cards_in_hand,
                    False, reason)
            return False

        # v5: Use GoalEngine mulligan if available
        if self.goal_engine:
            keep = self.goal_engine.decide_mulligan(hand, cards_in_hand)
            if self.strategic_logger:
                reason = self._mulligan_reason(hand, lands, spells, keep)
                self.strategic_logger.log_mulligan(
                    self.player_idx, self.deck_name, hand_names, cards_in_hand,
                    keep, reason)
            return keep

        # Legacy fallback: generic tag-based mulligan
        keep = self._mulligan_generic(hand, lands, spells, cards_in_hand)

        if self.strategic_logger:
            reason = self._mulligan_reason(hand, lands, spells, keep)
            self.strategic_logger.log_mulligan(
                self.player_idx, self.deck_name, hand_names, cards_in_hand,
                keep, reason)
        return keep

    def _mulligan_reason(self, hand, lands, spells, keep) -> str:
        """Generate a human-readable mulligan reason."""
        land_count = len(lands)
        cheap = sum(1 for s in spells if (s.template.cmc or 0) <= 2)
        interaction = sum(1 for s in spells if 'removal' in s.template.tags or 'counterspell' in s.template.tags)
        if keep:
            parts = [f"{land_count} lands"]
            if cheap: parts.append(f"{cheap} cheap spells")
            if interaction: parts.append(f"{interaction} interaction")
            return f"Keepable: {', '.join(parts)}"
        else:
            issues = []
            if land_count <= 1: issues.append("too few lands")
            if land_count >= 5: issues.append("too many lands")
            if cheap == 0: issues.append("no early plays")
            return f"Mulligan: {', '.join(issues)}" if issues else "Suboptimal hand"

    # --- mulligan helpers (unchanged, these are fine as heuristics) ---

    def _mulligan_generic(self, hand, lands, spells, cards_in_hand) -> bool:
        land_count = len(lands)
        if land_count == 1 and cards_in_hand == 7:
            if self.archetype == ArchetypeStrategy.AGGRO:
                return sum(1 for s in spells if s.template.cmc <= 2) >= 4
            return False
        if land_count >= 5 and cards_in_hand == 7:
            return False
        if self.archetype == ArchetypeStrategy.COMBO:
            has_piece = any("combo" in c.template.tags for c in spells)
            if land_count >= 2 and has_piece:
                return True
            return cards_in_hand <= 6 or land_count >= 2
        if self.archetype == ArchetypeStrategy.AGGRO:
            return 1 <= land_count <= 3 and sum(1 for s in spells if s.template.cmc <= 2) >= 2
        if self.archetype == ArchetypeStrategy.CONTROL:
            if land_count >= 3:
                return sum(1 for s in spells
                           if "removal" in s.template.tags or
                           "counterspell" in s.template.tags) >= 1
            return False
        if 2 <= land_count <= 4:
            return sum(1 for s in spells if s.template.cmc <= 3) >= 2
        return cards_in_hand <= 6


    def choose_cards_to_bottom(self, hand: List["CardInstance"],
                                count: int) -> List["CardInstance"]:
        if count <= 0:
            return []
        # v5: Use GoalEngine card scoring if available
        if self.goal_engine:
            scored = [(c, self.goal_engine.card_keep_score(c, hand)) for c in hand]
        else:
            scored = [(c, self._card_keep_score(c, hand)) for c in hand]
        scored.sort(key=lambda x: x[1])
        return [c for c, _ in scored[:count]]

    def _card_keep_score(self, card, hand) -> float:
        """Score a card for mulligan. Higher = more valuable to keep."""
        score = 0.0
        t = card.template
        lands_in_hand = sum(1 for c in hand if c.template.is_land)

        if t.is_land:
            score += 10.0 if lands_in_hand <= 3 else 2.0
            if t.produces_mana:
                score += len(t.produces_mana) * 0.5
        else:
            score += max(0, 5 - t.cmc)
            if "removal" in t.tags:   score += 3.0
            if "threat" in t.tags:    score += 2.0
            if "early_play" in t.tags:
                score += 4.0 if self.archetype == ArchetypeStrategy.AGGRO else 2.0
            if "combo" in t.tags:
                score += 5.0 if self.archetype == ArchetypeStrategy.COMBO else 1.0
            if "counterspell" in t.tags:
                score += 3.0 if self.archetype in (ArchetypeStrategy.CONTROL,
                                                     ArchetypeStrategy.TEMPO) else 1.0

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

        # Psychic Frog: discard cards to pump +1/+1 per discard before attacking
        me = game.players[self.player_idx]
        frogs = [c for c in valid if c.template.name == "Psychic Frog"]
        if frogs:
            frog = frogs[0]
            opponent = game.players[1 - self.player_idx]
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
                    frog.temp_power_mod += 1
                    frog.temp_toughness_mod += 1
                    game.log.append(f"T{game.turn_number} P{self.player_idx+1}: "
                                    f"Psychic Frog discards {card_to_discard.name} "
                                    f"(now {frog.power}/{frog.toughness})")

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
    # STACK RESPONSES — evaluator-driven
    # ═══════════════════════════════════════════════════════════════

    def decide_response(self, game, stack_item) -> Optional[Tuple["CardInstance", List[int]]]:
        """v4: Enhanced response decisions using TurnPlanner.

        Uses holistic threat assessment that considers:
        - Threat value vs response cost
        - What we lose by spending the response card
        - Whether we can deal with it later (post-resolution)
        - Mana implications for the rest of the turn
        - Blink value (save creature + re-trigger ETB)
        """
        from ai.evaluator import estimate_spell_value, estimate_permanent_value
        from engine.cards import CardType, Keyword

        player = game.players[self.player_idx]
        instants = [c for c in player.hand
                    if (c.template.is_instant or c.template.has_flash)
                    and game.can_cast(self.player_idx, c)]
        if not instants:
            if self.strategic_logger:
                self.strategic_logger.log_no_response(
                    self.player_idx, stack_item.source.name, game,
                    "No castable instants in hand")
            return None

        threat = self._evaluate_stack_threat(game, stack_item)

        # ── v4: Try TurnPlanner's evaluate_response first ──
        try:
            from ai.turn_planner import extract_virtual_board, VirtualSpell
            vboard = extract_virtual_board(game, self.player_idx)

            # Build virtual spell for the threat
            src = stack_item.source
            threat_vspell = VirtualSpell(
                instance_id=src.instance_id,
                name=src.name,
                cmc=src.template.cmc or 0,
                tags=set(src.template.tags),
                is_instant=src.template.is_instant or src.template.has_flash,
                is_creature=src.template.is_creature,
                power=src.template.power or 0,
                toughness=src.template.toughness or 0,
                keywords=set(),
                spell_value=threat,
                damage=0,
                has_etb="etb_value" in src.template.tags,
            )

            # Build virtual spells for our responses
            v_responses = []
            for inst in instants:
                v_resp = VirtualSpell(
                    instance_id=inst.instance_id,
                    name=inst.name,
                    cmc=inst.template.cmc or 0,
                    tags=set(inst.template.tags),
                    is_instant=True,
                    is_creature=False,
                    power=0, toughness=0,
                    keywords=set(),
                    spell_value=estimate_spell_value(inst, game, self.player_idx),
                    damage=0,
                    has_etb=False,
                )
                v_responses.append(v_resp)

            result = self.turn_planner.evaluate_response(
                vboard, threat, threat_vspell, v_responses)

            if result:
                v_spell, reasoning = result
                # Map back to real card
                for inst in instants:
                    if inst.instance_id == v_spell.instance_id:
                        # TurnPlanner response reasoning is internal — don't log
                        # Pick targets for the response
                        targets = self._choose_response_targets(
                            game, inst, stack_item)
                        if self.strategic_logger:
                            self.strategic_logger.log_response(
                                self.player_idx, inst.name,
                                stack_item.source.name, game,
                                f"TurnPlanner: threat value {threat:.1f}, responding with {inst.name}")
                        return (inst, targets)

        except Exception as e:
            pass  # TurnPlanner response failed silently — fall through to legacy

        # ── Legacy fallback ──
        for instant in instants:
            tags = instant.template.tags

            # Counterspell: counter the spell if threat is high enough
            if "counterspell" in tags and stack_item.source.template.is_spell:
                response_value = threat  # countering removes the threat
                cost = instant.template.cmc
                # Worth it if threat value > cost of our card
                # Lowered thresholds: aggro creatures are worth countering
                if response_value >= 3.0 or (response_value >= 1.5 and cost <= 2):
                    if self.strategic_logger:
                        self.strategic_logger.log_response(
                            self.player_idx, instant.name,
                            stack_item.source.name, game,
                            f"Counter: threat value {response_value:.1f} vs cost {cost}. Worth countering.")
                    return (instant, [stack_item.source.instance_id])

            # Blink response: protect our creature from removal on the stack
            if "blink" in tags:
                # Check if the stack item is targeting one of our creatures
                if hasattr(stack_item, 'targets') and stack_item.targets:
                    me = game.players[self.player_idx]
                    my_creature_ids = {c.instance_id for c in me.creatures}
                    targeted_own = [tid for tid in stack_item.targets if tid in my_creature_ids]
                    if targeted_own:
                        # Our creature is being targeted — blink it to save it
                        return (instant, targeted_own[:1])
                # Also use proactively if opponent cast removal and we have ETB creatures
                if "removal" in stack_item.source.template.tags:
                    me = game.players[self.player_idx]
                    etb_creatures = [c for c in me.creatures
                                     if "etb_value" in c.template.tags]
                    if etb_creatures:
                        best = max(etb_creatures,
                                   key=lambda c: estimate_permanent_value(
                                       c, me, game, self.player_idx))
                        return (instant, [best.instance_id])

            # Instant-speed removal: use if there's a good target
            if "removal" in tags:
                opponent = game.players[1 - self.player_idx]
                if opponent.creatures:
                    target = self._pick_best_removal_target(
                        instant, opponent.creatures, opponent,
                        game, 1 - self.player_idx)
                    if target:
                        from ai.evaluator import estimate_removal_value
                        val = estimate_removal_value(
                            target, instant.template.cmc,
                            opponent, game, 1 - self.player_idx)
                        if val >= 3.0:  # worth spending a card
                            return (instant, [target.instance_id])

        # No response found
        if self.strategic_logger:
            self.strategic_logger.log_no_response(
                self.player_idx, stack_item.source.name, game,
                f"Threat value {threat:.1f} not worth responding to, or no suitable response")
        return None

    def _choose_response_targets(self, game, instant, stack_item):
        """Choose targets for a response spell based on its type."""
        tags = instant.template.tags
        if "counterspell" in tags:
            return [stack_item.source.instance_id]
        if "blink" in tags:
            # Blink our best ETB creature, or the targeted creature
            me = game.players[self.player_idx]
            if hasattr(stack_item, 'targets') and stack_item.targets:
                my_creature_ids = {c.instance_id for c in me.creatures}
                targeted_own = [tid for tid in stack_item.targets if tid in my_creature_ids]
                if targeted_own:
                    return targeted_own[:1]
            # Fallback: blink best ETB creature
            etb_creatures = [c for c in me.creatures if "etb_value" in c.template.tags]
            if etb_creatures:
                from ai.evaluator import estimate_permanent_value
                best = max(etb_creatures,
                           key=lambda c: estimate_permanent_value(c, me, game, self.player_idx))
                return [best.instance_id]
            if me.creatures:
                from ai.evaluator import estimate_permanent_value
                best = max(me.creatures,
                           key=lambda c: estimate_permanent_value(c, me, game, self.player_idx))
                return [best.instance_id]
            return []
        if "removal" in tags:
            opponent = game.players[1 - self.player_idx]
            if opponent.creatures:
                target = self._pick_best_removal_target(
                    instant, opponent.creatures, opponent,
                    game, 1 - self.player_idx)
                if target:
                    return [target.instance_id]
            return []
        return self._choose_targets(game, instant)

    def _evaluate_stack_threat(self, game, stack_item) -> float:
        """Evaluate how threatening a stack item is (0-10 scale)."""
        from ai.evaluator import estimate_spell_value, estimate_permanent_value

        source = stack_item.source
        template = source.template
        threat = 0.0

        # Base threat from CMC (expensive spells tend to be more impactful)
        threat += min(template.cmc, 5) * 0.5

        # Board wipes are devastating if we have creatures
        if "board_wipe" in template.tags:
            my_creatures = len(game.players[self.player_idx].creatures)
            if my_creatures >= 2:
                threat += 6.0 + my_creatures

        # Combo pieces are game-ending
        if "combo" in template.tags:
            threat += 7.0

        # Big creatures — use effective power, not template power
        # Domain creatures (Nishoba Brawler, Territorial Kavu) have base power 0
        # but their actual power is equal to domain count (typically 4-5)
        effective_power = template.power or 0
        if effective_power == 0 and template.is_creature:
            # Check for domain/star power creatures
            if any(kw in (template.tags or set()) for kw in ('domain', 'domain_power')):
                effective_power = 5  # Assume full domain
            elif source.name in ('Nishoba Brawler', 'Territorial Kavu', 'Scion of Draco'):
                effective_power = 5  # Known domain creatures
            elif template.toughness and template.toughness >= 3:
                effective_power = template.toughness - 1  # Reasonable estimate
        
        # Also check for known high-value creatures regardless of power
        HIGH_VALUE_CREATURES = {
            'Ragavan, Nimble Pilferer': 5.0,  # Generates massive value over time
            'Orcish Bowmasters': 4.0,  # Kills small creatures + grows army
            'Psychic Frog': 3.5,  # Card filtering + grows
            'Murktide Regent': 5.0,  # Huge flyer
            'Omnath, Locus of Creation': 6.0,  # Value engine
            'Phlage, Titan of Fire\'s Fury': 5.0,  # ETB damage + recurring
        }
        if source.name in HIGH_VALUE_CREATURES:
            threat = max(threat, HIGH_VALUE_CREATURES[source.name])
        
        if template.is_creature and effective_power >= 4:
            threat += effective_power * 0.8
        elif template.is_creature and effective_power >= 2:
            threat += effective_power * 0.6  # Smaller creatures still worth noting

        # Direct damage spells (burn) — evaluate based on damage and our life total
        BURN_DAMAGE = {
            'Lightning Bolt': 3, 'Tribal Flames': 5, 'Galvanic Discharge': 3,
            'Lightning Helix': 3, 'Lava Spike': 3, 'Rift Bolt': 3,
            'Boros Charm': 4, 'Searing Blaze': 3,
        }
        if source.name in BURN_DAMAGE or 'burn' in template.tags or 'damage' in (template.tags or set()):
            face_dmg = BURN_DAMAGE.get(source.name, 3)  # default 3 for unknown burn
            my_life = game.players[self.player_idx].life
            # Threat scales with how much of our life it takes
            life_pct = face_dmg / max(my_life, 1)
            threat += face_dmg * 1.0  # base: 1 threat per damage
            if life_pct >= 0.25:  # 25%+ of our life = very threatening
                threat += 3.0
            if my_life <= face_dmg:  # lethal burn!
                threat += 10.0

        # Phlage ETB damage (3 damage + 3 life gain for opponent)
        if source.name == 'Phlage, Titan of Fire\'s Fury':
            threat = max(threat, 5.0)  # Always significant

        # Removal targeting our stuff
        if "removal" in template.tags:
            me = game.players[self.player_idx]
            if me.creatures:
                best = max(me.creatures,
                           key=lambda c: estimate_permanent_value(
                               c, me, game, self.player_idx))
                threat += estimate_permanent_value(
                    best, me, game, self.player_idx) * 0.5

        # Cascade = Living End incoming
        if 'cascade' in getattr(source.template, 'tags', set()):
            threat += 8.0
        # Reanimate = huge creature incoming
        if 'reanimate' in getattr(source.template, 'tags', set()):
            threat += 8.0

        return threat

