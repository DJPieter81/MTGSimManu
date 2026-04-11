"""
TurnPlanner + CombatPlanner — Holistic MTG Decision Engine
==========================================================
Replaces the per-phase independent decision-making with a unified
system that reasons about the entire turn as a coordinated sequence.

CombatPlanner:
  - Enumerates pruned attack configurations (not all 2^N)
  - Simulates likely blocking responses
  - Evaluates post-combat board state
  - Plans 2-turn lethal setups
  - Considers first-strike/deathtouch/trample interactions

TurnPlanner:
  - Considers main1 → combat → main2 as one optimization
  - Pre-combat removal awareness ("bolt their blocker, THEN attack")
  - Mana reservation ("hold up counter mana vs deploy threat")
  - Response planning ("counter now or deal with it later?")

All evaluation is done via lightweight "virtual board states" that
cheaply simulate outcomes without cloning the full GameState.
"""
from __future__ import annotations
import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate, Keyword

# ── Combat constants ──
# Structural: lethal = game over
LETHAL_BONUS = 100.0
# 2-turn lethal: strong but not game-over
TWO_TURN_LETHAL_BONUS = 15.0
# Trade values: derived from creature clock impact difference
TRADE_UP_BONUS = 2.0
TRADE_DOWN_PENALTY = -4.5
# Risk of tapping out vs open opponent mana
SHIELDS_DOWN_PENALTY = -2.5
# Computational budget (structural)
MAX_ATTACK_CONFIGS = 32
# Response thresholds: derived from creature_value() scale (~1-15).
# A 3/3 vanilla = ~3.0, a 3/3 flyer = ~4.3, a 6/6 trample = ~10.
# Counter if threat > cost of holding counter (opportunity cost of the card).
# Cheap counters (CMC≤2) have low opportunity cost → lower threshold.
COUNTER_THRESHOLD = 5.0         # ~3/3 with a keyword
COUNTER_CHEAP_THRESHOLD = 2.0   # cheap counter: counter almost anything
REMOVAL_RESPONSE_THRESHOLD = 4.0  # remove if creature ≥ ~3/3
BLINK_SAVE_THRESHOLD = 3.5       # save creature worth ≥ ~2/2 with keyword
# Pre-combat removal: killing a blocker enables ~3 extra damage = 3/20 clock gain × 20 ≈ 3
PRE_COMBAT_REMOVAL_BONUS = 2.5
# Holding up mana: value ≈ avg_threat × P(needing_response) ≈ 5 × 0.5 + counter_value
MANA_RESERVATION_WEIGHT = 5.0


# ═══════════════════════════════════════════════════════════════════
# Virtual Board State — lightweight simulation without game cloning
# ═══════════════════════════════════════════════════════════════════

@dataclass
class VirtualCreature:
    """Lightweight creature representation for combat simulation."""
    instance_id: int
    name: str
    power: int
    toughness: int
    keywords: Set[str]
    is_tapped: bool
    controller: int
    value: float  # pre-computed permanent value
    cmc: int = 0
    damage_marked: int = 0
    has_etb: bool = False

    @property
    def is_dead(self) -> bool:
        return self.damage_marked >= self.toughness

    def copy(self) -> "VirtualCreature":
        return VirtualCreature(
            instance_id=self.instance_id,
            name=self.name,
            power=self.power,
            toughness=self.toughness,
            keywords=set(self.keywords),
            is_tapped=self.is_tapped,
            controller=self.controller,
            value=self.value,
            cmc=self.cmc,
            damage_marked=self.damage_marked,
            has_etb=self.has_etb,
        )


@dataclass
class VirtualSpell:
    """Lightweight spell representation for turn planning."""
    instance_id: int
    name: str
    cmc: int
    tags: Set[str]
    is_instant: bool
    is_creature: bool
    power: int = 0
    toughness: int = 0
    keywords: Set[str] = field(default_factory=set)
    spell_value: float = 0.0  # pre-computed from evaluator
    damage: int = 0  # for burn spells
    has_etb: bool = False


@dataclass
class VirtualBoard:
    """Lightweight board state for simulation."""
    my_creatures: List[VirtualCreature]
    opp_creatures: List[VirtualCreature]
    my_life: int
    opp_life: int
    my_hand: List[VirtualSpell]
    my_mana: int
    opp_mana: int  # for threat assessment

    def copy(self) -> "VirtualBoard":
        return VirtualBoard(
            my_creatures=[c.copy() for c in self.my_creatures],
            opp_creatures=[c.copy() for c in self.opp_creatures],
            my_life=self.my_life,
            opp_life=self.opp_life,
            my_hand=list(self.my_hand),  # spells are immutable
            my_mana=self.my_mana,
            opp_mana=self.opp_mana,
        )

    def score(self) -> float:
        """Quick board evaluation using clock-derived values."""
        score = 0.0
        # Life differential (clock-based survival)
        score += _life_score(self.my_life) - _life_score(self.opp_life)
        # Board presence (creature values already clock-based)
        my_board = sum(c.value for c in self.my_creatures if not c.is_dead)
        opp_board = sum(c.value for c in self.opp_creatures if not c.is_dead)
        score += my_board - opp_board
        # Card advantage: each card ≈ avg creature power / opp_life in clock terms
        # ~2.5 power / 20 life × 20 (scale factor) ≈ 2.5 per card
        score += len(self.my_hand) * 2.5
        # Mana: each mana enables ~1 power of deployment / opp_life
        # ~1/20 × 20 ≈ 1.0, but discounted (can't always use all mana)
        score += self.my_mana * 0.3
        return score


def _life_score(life: int) -> float:
    """Life valuation using clock-based survival turns."""
    from ai.clock import life_as_resource
    # Use average incoming power of 3 for context-free evaluation
    # Scale by 5 to match the ~0-30 range the VirtualBoard.score() expects
    return life_as_resource(life, 3) * 5.0


# ═══════════════════════════════════════════════════════════════════
# CombatPlanner — Deep combat reasoning
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CombatResult:
    """Result of simulating a combat configuration."""
    attackers: List[VirtualCreature]
    blocks: Dict[int, List[int]]  # attacker_id -> [blocker_ids]
    my_dead: List[VirtualCreature]
    opp_dead: List[VirtualCreature]
    damage_to_opp: int
    damage_to_me: int
    life_gained: int  # from lifelink
    post_board: VirtualBoard
    score: float  # post-combat board score
    is_lethal: bool
    two_turn_lethal: bool  # sets up lethal next turn


class CombatPlanner:
    """Evaluates combined attack configurations against likely blocks.

    Key principles:
    1. Never evaluate attackers independently — the COMBINED attack matters
    2. Simulate what the opponent would ACTUALLY block (using their logic)
    3. Consider the post-combat board state, not just damage dealt
    4. Plan 2-turn lethal: even if not lethal now, is the setup worth it?
    """

    # Tunable parameters — imported from ai/constants.py
    BOARD_WIPE_THRESHOLD = 0.1  # don't attack if we lose >30% more value than them

    def plan_attack(self, board: VirtualBoard) -> Tuple[List[VirtualCreature], float]:
        """Find the best attack configuration.

        Returns (attackers_list, expected_score_delta).
        Empty list means "don't attack".
        """
        valid_attackers = [c for c in board.my_creatures
                          if not c.is_tapped and c.power > 0]
        if not valid_attackers:
            return [], 0.0

        # Quick lethal check: if total power >= opp life, attack with everything
        total_power = sum(c.power for c in valid_attackers)
        if total_power >= board.opp_life:
            return valid_attackers, LETHAL_BONUS

        opp_blockers = [c for c in board.opp_creatures
                        if not c.is_tapped]

        # No blockers? Attack with everything (free damage)
        if not opp_blockers:
            return valid_attackers, sum(c.power for c in valid_attackers) * 1.0

        # Generate pruned attack configurations
        configs = self._generate_attack_configs(valid_attackers, opp_blockers, board)

        # Evaluate each configuration
        best_config = []
        best_score = 0.0  # baseline: don't attack at all
        baseline_score = board.score()

        for config in configs:
            if not config:
                continue
            result = self._simulate_combat(config, opp_blockers, board)
            delta = result.score - baseline_score

            # Bonus for lethal
            if result.is_lethal:
                delta += LETHAL_BONUS

            # Bonus for 2-turn lethal setup
            if result.two_turn_lethal:
                delta += TWO_TURN_LETHAL_BONUS

            # Penalty if we're trading down badly
            my_lost_value = sum(c.value for c in result.my_dead)
            opp_lost_value = sum(c.value for c in result.opp_dead)
            if my_lost_value > opp_lost_value * 1.5 and not result.is_lethal:
                delta += TRADE_DOWN_PENALTY

            # Bonus for trading up
            if opp_lost_value > my_lost_value * 1.2:
                delta += TRADE_UP_BONUS

            # Shields down penalty: if we tap our creatures and opponent
            # has mana up for tricks, we're vulnerable.
            # Scale penalty by how much damage we're dealing — high damage
            # attacks are worth the risk of tapping out.
            if board.opp_mana >= 2:
                # Only count non-vigilance, non-token creatures for shields-down.
                # Tokens and small creatures (value < 3.0) are expendable.
                tapped_value = sum(c.value for c in config
                                   if "vigilance" not in c.keywords
                                   and c.value >= 3.0)
                total_attack_power = sum(c.power for c in config)
                if tapped_value > 5.0:
                    # Reduce penalty when dealing significant damage
                    damage_ratio = min(total_attack_power / max(board.opp_life, 1), 1.0)
                    scaled_penalty = SHIELDS_DOWN_PENALTY * (1.0 - damage_ratio * 0.6)
                    delta += scaled_penalty

            # Aggression bonus: attacks become more valuable as opponent's life drops.
            # In real MTG, players push damage aggressively when opponent is low.
            total_attack_power = sum(c.power for c in config)
            if board.opp_life <= 8:
                delta += total_attack_power * 0.8  # push hard for lethal range
            elif board.opp_life <= 12:
                delta += total_attack_power * 0.4  # moderate aggression
            elif board.opp_life <= 16:
                delta += total_attack_power * 0.15  # slight aggression

            if delta > best_score:
                best_score = delta
                best_config = config

        return best_config, best_score

    def _generate_attack_configs(self, attackers: List[VirtualCreature],
                                  blockers: List[VirtualCreature],
                                  board: VirtualBoard) -> List[List[VirtualCreature]]:
        """Generate pruned attack configurations.

        Instead of all 2^N, we use heuristics:
        1. All evasive creatures (flying with no flying blockers, menace with <2 blockers)
        2. All creatures that can't be profitably blocked
        3. Subsets that set up lethal
        4. Individual high-value attackers
        """
        configs: List[List[VirtualCreature]] = []

        # Categorize attackers
        evasive = []
        safe = []  # can't be killed by any blocker
        risky = []  # can be killed

        for a in attackers:
            if self._has_evasion(a, blockers):
                evasive.append(a)
            elif not any(b.power >= a.toughness for b in blockers):
                safe.append(a)
            else:
                risky.append(a)

        # Config 1: All evasive + all safe (guaranteed damage, no risk)
        if evasive or safe:
            configs.append(evasive + safe)

        # Config 2: Everything (alpha strike)
        configs.append(list(attackers))

        # Config 3: Evasive only
        if evasive:
            configs.append(list(evasive))

        # Config 4: Each risky creature individually (test if any single attack is worth it)
        for r in risky:
            configs.append(evasive + safe + [r])

        # Config 5: Pairs of risky creatures (force multi-block decisions)
        if len(risky) >= 2:
            for i in range(min(len(risky), 4)):
                for j in range(i + 1, min(len(risky), 4)):
                    configs.append(evasive + safe + [risky[i], risky[j]])

        # Config 6: Lethal-seeking — find minimum attackers for lethal
        power_sorted = sorted(attackers, key=lambda c: c.power, reverse=True)
        cumulative = 0
        lethal_set = []
        for a in power_sorted:
            lethal_set.append(a)
            cumulative += a.power
            if cumulative >= board.opp_life:
                configs.append(list(lethal_set))
                break

        # Config 7: Just the biggest creature (pressure without overcommitting)
        if attackers:
            biggest = max(attackers, key=lambda c: c.power)
            configs.append([biggest])

        # Deduplicate by attacker ID sets
        seen = set()
        unique_configs = []
        for config in configs:
            key = frozenset(c.instance_id for c in config)
            if key not in seen and config:
                seen.add(key)
                unique_configs.append(config)

        return unique_configs[:MAX_ATTACK_CONFIGS]

    def _has_evasion(self, creature: VirtualCreature,
                      blockers: List[VirtualCreature]) -> bool:
        """Check if a creature has effective evasion against these blockers."""
        if "flying" in creature.keywords:
            flying_blockers = [b for b in blockers
                              if "flying" in b.keywords or "reach" in b.keywords]
            if not flying_blockers:
                return True
        if "menace" in creature.keywords:
            if len(blockers) < 2:
                return True
        # Unblockable effects would go here
        return False

    def _simulate_combat(self, attackers: List[VirtualCreature],
                          opp_blockers: List[VirtualCreature],
                          board: VirtualBoard) -> CombatResult:
        """Simulate combat with intelligent blocking from opponent's perspective.

        The opponent will:
        1. Always block lethal attacks
        2. Trade up when possible (block with cheaper creature)
        3. Double-block to kill threats they can't handle 1v1
        4. Chump-block only when necessary to survive
        """
        sim_board = board.copy()
        sim_attackers = [c.copy() for c in attackers]
        sim_blockers = [c.copy() for c in opp_blockers]

        # Simulate opponent's blocking decisions
        blocks = self._predict_blocks(sim_attackers, sim_blockers, sim_board)

        # Resolve combat damage
        damage_to_opp = 0
        damage_to_me = 0
        life_gained = 0
        my_dead = []
        opp_dead = []

        for attacker in sim_attackers:
            blocker_ids = blocks.get(attacker.instance_id, [])
            actual_blockers = [b for b in sim_blockers if b.instance_id in blocker_ids]

            if actual_blockers:
                # Blocked — assign damage
                remaining_power = attacker.power
                has_deathtouch = "deathtouch" in attacker.keywords
                has_trample = "trample" in attacker.keywords

                for blocker in actual_blockers:
                    if remaining_power <= 0:
                        break
                    # Deathtouch: 1 damage is lethal
                    lethal = 1 if has_deathtouch else blocker.toughness - blocker.damage_marked
                    dmg_to_blocker = min(lethal, remaining_power)
                    blocker.damage_marked += dmg_to_blocker
                    remaining_power -= dmg_to_blocker

                    # Blocker deals damage back
                    attacker.damage_marked += blocker.power
                    if "deathtouch" in blocker.keywords and blocker.power > 0:
                        attacker.damage_marked = max(attacker.damage_marked, attacker.toughness)

                # Trample overflow
                if has_trample and remaining_power > 0:
                    damage_to_opp += remaining_power

                # Check deaths
                if attacker.is_dead:
                    my_dead.append(attacker)
                for b in actual_blockers:
                    if b.is_dead:
                        opp_dead.append(b)

                # Deathtouch ensures blocker death
                if has_deathtouch:
                    for b in actual_blockers:
                        if b.damage_marked > 0 and not b.is_dead:
                            b.damage_marked = b.toughness
                            if b not in opp_dead:
                                opp_dead.append(b)
            else:
                # Unblocked — damage to player
                damage_to_opp += attacker.power

            # Lifelink
            if "lifelink" in attacker.keywords:
                life_gained += attacker.power

        # Build post-combat board
        post = sim_board.copy()
        post.opp_life -= damage_to_opp
        post.my_life += life_gained

        dead_my_ids = {c.instance_id for c in my_dead}
        dead_opp_ids = {c.instance_id for c in opp_dead}
        post.my_creatures = [c for c in post.my_creatures if c.instance_id not in dead_my_ids]
        post.opp_creatures = [c for c in post.opp_creatures if c.instance_id not in dead_opp_ids]

        # Check lethal
        is_lethal = post.opp_life <= 0

        # Check 2-turn lethal: can surviving attackers kill next turn?
        surviving_power = sum(c.power for c in post.my_creatures
                             if not c.is_tapped or "vigilance" in c.keywords)
        two_turn_lethal = surviving_power >= post.opp_life and post.opp_life > 0

        return CombatResult(
            attackers=attackers,
            blocks=blocks,
            my_dead=my_dead,
            opp_dead=opp_dead,
            damage_to_opp=damage_to_opp,
            damage_to_me=damage_to_me,
            life_gained=life_gained,
            post_board=post,
            score=post.score(),
            is_lethal=is_lethal,
            two_turn_lethal=two_turn_lethal,
        )

    def _predict_blocks(self, attackers: List[VirtualCreature],
                         blockers: List[VirtualCreature],
                         board: VirtualBoard) -> Dict[int, List[int]]:
        """Predict how the opponent would block.

        Opponent blocking priorities:
        1. Block lethal damage (must-block)
        2. Trade up (kill attacker with cheaper blocker)
        3. Trade even (kill attacker, lose blocker of similar value)
        4. Double-block to kill a threat that can't be handled 1v1
        5. Chump-block only to prevent lethal
        """
        blocks: Dict[int, List[int]] = {}
        used_blockers: Set[int] = set()
        total_incoming = sum(a.power for a in attackers)

        # Sort attackers by threat (highest power first)
        sorted_attackers = sorted(attackers, key=lambda a: a.power, reverse=True)

        # Phase 1: Must-block to survive
        if total_incoming >= board.opp_life:
            damage_needed_to_prevent = total_incoming - board.opp_life + 1
            prevented = 0
            for attacker in sorted_attackers:
                if prevented >= damage_needed_to_prevent:
                    break
                # Find cheapest blocker that can block this
                available = [b for b in blockers if b.instance_id not in used_blockers
                            and self._can_block(b, attacker)]
                if available:
                    best = min(available, key=lambda b: b.value)
                    blocks[attacker.instance_id] = [best.instance_id]
                    used_blockers.add(best.instance_id)
                    prevented += attacker.power

        # Phase 2: Profitable trades (kill attacker, lose less value)
        for attacker in sorted_attackers:
            if attacker.instance_id in blocks:
                continue
            available = [b for b in blockers if b.instance_id not in used_blockers
                        and self._can_block(b, attacker)]
            for blocker in sorted(available, key=lambda b: b.value):
                # Can this blocker kill the attacker?
                can_kill = (blocker.power >= attacker.toughness or
                           "deathtouch" in blocker.keywords)
                # Is it a trade-up? (we lose less value)
                if can_kill and blocker.value < attacker.value * 0.9:
                    blocks[attacker.instance_id] = [blocker.instance_id]
                    used_blockers.add(blocker.instance_id)
                    break

        # Phase 3: Even trades (kill attacker, lose similar value)
        for attacker in sorted_attackers:
            if attacker.instance_id in blocks:
                continue
            available = [b for b in blockers if b.instance_id not in used_blockers
                        and self._can_block(b, attacker)]
            for blocker in sorted(available, key=lambda b: b.value):
                can_kill = (blocker.power >= attacker.toughness or
                           "deathtouch" in blocker.keywords)
                survives = attacker.power < blocker.toughness
                if can_kill and (survives or blocker.value <= attacker.value * 1.1):
                    blocks[attacker.instance_id] = [blocker.instance_id]
                    used_blockers.add(blocker.instance_id)
                    break

        # Phase 4: Double-block high-value threats
        for attacker in sorted_attackers:
            if attacker.instance_id in blocks:
                continue
            if attacker.value < 4.0:
                continue  # only double-block high-value threats
            available = [b for b in blockers if b.instance_id not in used_blockers
                        and self._can_block(b, attacker)]
            if len(available) >= 2:
                # Find cheapest pair that can kill the attacker
                available.sort(key=lambda b: b.value)
                for i in range(len(available)):
                    for j in range(i + 1, len(available)):
                        combined_power = available[i].power + available[j].power
                        combined_value = available[i].value + available[j].value
                        if (combined_power >= attacker.toughness and
                                combined_value < attacker.value * 1.3):
                            blocks[attacker.instance_id] = [
                                available[i].instance_id,
                                available[j].instance_id
                            ]
                            used_blockers.add(available[i].instance_id)
                            used_blockers.add(available[j].instance_id)
                            break
                    if attacker.instance_id in blocks:
                        break

        return blocks

    def _can_block(self, blocker: VirtualCreature, attacker: VirtualCreature) -> bool:
        """Check if a blocker can legally block an attacker."""
        if "flying" in attacker.keywords:
            if "flying" not in blocker.keywords and "reach" not in blocker.keywords:
                return False
        if "menace" in attacker.keywords:
            return True  # menace needs 2 blockers, handled in double-block
        return True


# ═══════════════════════════════════════════════════════════════════
# TurnPlanner — Full-turn sequencing optimization
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TurnPlan:
    """A complete plan for one turn."""
    pre_combat_actions: List[Tuple[str, int]]  # (action_type, spell_id)
    attack_config: List[int]  # creature instance_ids to attack with
    post_combat_actions: List[Tuple[str, int]]
    expected_score: float
    reasoning: str  # human-readable explanation


class TurnPlanner:
    """Plans the entire turn as a coordinated sequence.

    Key insight: main phase 1, combat, and main phase 2 are NOT independent.
    The best play often involves sequencing actions across phases:
    - "Bolt their blocker in main 1, THEN attack with everything"
    - "Don't deploy a creature pre-combat — hold up counter mana"
    - "Attack first to see what they block, THEN cast creature in main 2"

    Tunable parameters — imported from ai/constants.py
    """
    INFORMATION_BONUS = 0.3  # bonus for attacking first (see blocks before committing)

    def __init__(self):
        self.combat_planner = CombatPlanner()

    def plan_turn(self, board: VirtualBoard,
                  game_turn: int = 5) -> TurnPlan:
        """Plan the optimal sequence for this turn.

        Evaluates multiple strategies:
        1. Deploy threats, then attack (aggro)
        2. Remove blockers, then attack (tempo)
        3. Attack first, deploy in main 2 (information)
        4. Hold up mana, don't attack (control)
        5. All-in attack with pre-combat pump/removal (lethal-seeking)
        """
        strategies = []

        # Strategy 1: Aggressive — deploy creature pre-combat, then attack
        strat1 = self._evaluate_deploy_then_attack(board, game_turn)
        strategies.append(strat1)

        # Strategy 2: Tempo — remove blocker pre-combat, then attack
        strat2 = self._evaluate_remove_then_attack(board, game_turn)
        strategies.append(strat2)

        # Strategy 3: Information — attack first, deploy in main 2
        strat3 = self._evaluate_attack_then_deploy(board, game_turn)
        strategies.append(strat3)

        # Strategy 4: Control — hold up mana, minimal/no attack
        strat4 = self._evaluate_hold_up_mana(board, game_turn)
        strategies.append(strat4)

        # Strategy 5: Lethal-seeking — use everything to try to kill
        strat5 = self._evaluate_lethal_push(board, game_turn)
        strategies.append(strat5)

        # Pick the best strategy
        best = max(strategies, key=lambda s: s.expected_score)
        return best

    def evaluate_response(self, board: VirtualBoard,
                          threat_value: float,
                          threat_spell: Optional[VirtualSpell],
                          available_responses: List[VirtualSpell]) -> Optional[Tuple[VirtualSpell, str]]:
        """Decide whether and how to respond to a spell on the stack.

        Considers:
        - Threat value vs response cost
        - What we lose by spending the response card
        - Whether we can deal with it later (post-resolution)
        - Mana implications for the rest of the turn
        """
        if not available_responses:
            return None

        best_response = None
        best_net_value = 0.0

        for response in available_responses:
            net_value = 0.0
            reasoning = ""

            if "counterspell" in response.tags:
                # Counter: removes the threat entirely
                net_value = threat_value - response.cmc * 0.5
                # Cheap counters are more efficient
                if response.cmc <= 2:
                    threshold = COUNTER_CHEAP_THRESHOLD
                else:
                    threshold = COUNTER_THRESHOLD

                if threat_value < threshold:
                    continue  # not worth countering

                # Consider: will we need this counter later?
                # If opponent has bigger threats in deck, maybe save it
                remaining_mana_after = board.my_mana - response.cmc
                if remaining_mana_after <= 0 and len(board.my_hand) > 2:
                    net_value -= 1.5  # tapping out to counter is risky

                reasoning = f"Counter {threat_spell.name if threat_spell else 'spell'} (threat={threat_value:.1f})"

            elif "removal" in response.tags:
                # Instant removal: can we kill it after it resolves?
                if threat_spell and threat_spell.is_creature:
                    # Will this removal kill it?
                    if response.damage >= threat_spell.toughness or response.damage == 0:
                        net_value = threat_value * 0.8 - response.cmc * 0.3
                    else:
                        continue  # removal won't kill it

                    if net_value < REMOVAL_RESPONSE_THRESHOLD:
                        continue

                    reasoning = f"Remove {threat_spell.name} after resolution"
                else:
                    continue  # can't remove non-creatures with creature removal

            elif "blink" in response.tags:
                # Blink: save our creature from their removal
                if threat_spell and "removal" in threat_spell.tags:
                    # Find our most valuable creature that might be targeted
                    if board.my_creatures:
                        best_creature = max(board.my_creatures, key=lambda c: c.value)
                        if best_creature.value >= BLINK_SAVE_THRESHOLD:
                            net_value = best_creature.value * 0.7
                            if best_creature.has_etb:
                                net_value += 3.0  # bonus: re-trigger ETB
                            reasoning = f"Blink {best_creature.name} to save from removal"
                            if best_creature.has_etb:
                                reasoning += f" + re-trigger ETB"

            if net_value > best_net_value:
                best_net_value = net_value
                best_response = (response, reasoning)

        return best_response

    # ───────────────────────────────────────────────────────────
    # Strategy evaluators
    # ───────────────────────────────────────────────────────────

    def _evaluate_deploy_then_attack(self, board: VirtualBoard,
                                      turn: int) -> TurnPlan:
        """Strategy: Deploy best creature in main 1, then attack."""
        sim = board.copy()
        pre_actions = []
        reasoning_parts = []

        # Find best creature to deploy
        creatures = [s for s in sim.my_hand if s.is_creature and s.cmc <= sim.my_mana]
        if creatures:
            best = max(creatures, key=lambda s: s.spell_value)
            # Deploy it
            new_creature = VirtualCreature(
                instance_id=best.instance_id,
                name=best.name,
                power=best.power,
                toughness=best.toughness,
                keywords=best.keywords,
                is_tapped=False,
                controller=0,
                value=best.spell_value,
                cmc=best.cmc,
                has_etb=best.has_etb,
            )
            # Haste: can attack immediately
            if "haste" not in best.keywords:
                new_creature.is_tapped = True  # summoning sickness (can't attack)

            sim.my_creatures.append(new_creature)
            sim.my_mana -= best.cmc
            sim.my_hand = [s for s in sim.my_hand if s.instance_id != best.instance_id]
            pre_actions.append(("cast_creature", best.instance_id))
            reasoning_parts.append(f"Deploy {best.name}")

        # Now plan combat with the updated board
        attack_config, combat_score = self.combat_planner.plan_attack(sim)
        attack_ids = [c.instance_id for c in attack_config]

        score = sim.score() + combat_score
        reasoning_parts.append(f"Attack with {len(attack_config)} creatures")

        return TurnPlan(
            pre_combat_actions=pre_actions,
            attack_config=attack_ids,
            post_combat_actions=[],
            expected_score=score,
            reasoning=" → ".join(reasoning_parts) if reasoning_parts else "Deploy then attack",
        )

    def _evaluate_remove_then_attack(self, board: VirtualBoard,
                                      turn: int) -> TurnPlan:
        """Strategy: Remove a blocker in main 1, then attack."""
        sim = board.copy()
        pre_actions = []
        reasoning_parts = []

        # Find removal spells
        removal = [s for s in sim.my_hand
                   if "removal" in s.tags and s.cmc <= sim.my_mana]

        if removal and sim.opp_creatures:
            # Find the best target: the blocker that most constrains our attacks
            best_removal = None
            best_target = None
            best_improvement = 0.0

            for spell in removal:
                for target in sim.opp_creatures:
                    # Can this removal kill the target?
                    if spell.damage > 0 and spell.damage < target.toughness:
                        continue  # won't kill it

                    # Simulate: remove the target, then plan combat
                    test_board = sim.copy()
                    test_board.opp_creatures = [c for c in test_board.opp_creatures
                                                 if c.instance_id != target.instance_id]
                    test_board.my_mana -= spell.cmc
                    test_board.my_hand = [s for s in test_board.my_hand
                                          if s.instance_id != spell.instance_id]

                    _, combat_after = self.combat_planner.plan_attack(test_board)
                    _, combat_before = self.combat_planner.plan_attack(sim)

                    improvement = combat_after - combat_before + target.value * 0.5
                    improvement += PRE_COMBAT_REMOVAL_BONUS

                    if improvement > best_improvement:
                        best_improvement = improvement
                        best_removal = spell
                        best_target = target

            if best_removal and best_target and best_improvement > 2.0:
                sim.opp_creatures = [c for c in sim.opp_creatures
                                     if c.instance_id != best_target.instance_id]
                sim.my_mana -= best_removal.cmc
                sim.my_hand = [s for s in sim.my_hand
                               if s.instance_id != best_removal.instance_id]
                pre_actions.append(("cast_removal", best_removal.instance_id))
                reasoning_parts.append(f"Remove {best_target.name} with {best_removal.name}")

        # Plan combat after removal
        attack_config, combat_score = self.combat_planner.plan_attack(sim)
        attack_ids = [c.instance_id for c in attack_config]

        score = sim.score() + combat_score
        reasoning_parts.append(f"Attack with {len(attack_config)} creatures")

        return TurnPlan(
            pre_combat_actions=pre_actions,
            attack_config=attack_ids,
            post_combat_actions=[],
            expected_score=score,
            reasoning=" → ".join(reasoning_parts) if reasoning_parts else "Remove blocker then attack",
        )

    def _evaluate_attack_then_deploy(self, board: VirtualBoard,
                                      turn: int) -> TurnPlan:
        """Strategy: Attack first (get info), then deploy in main 2."""
        sim = board.copy()
        reasoning_parts = []

        # Plan combat with current board
        attack_config, combat_score = self.combat_planner.plan_attack(sim)
        attack_ids = [c.instance_id for c in attack_config]
        reasoning_parts.append(f"Attack with {len(attack_config)} creatures first")

        # After combat, deploy best creature in main 2
        post_actions = []
        creatures = [s for s in sim.my_hand if s.is_creature and s.cmc <= sim.my_mana]
        if creatures:
            best = max(creatures, key=lambda s: s.spell_value)
            post_actions.append(("cast_creature", best.instance_id))
            reasoning_parts.append(f"Deploy {best.name} in main 2")
            combat_score += best.spell_value * 0.9  # slight discount for delayed deploy

        score = sim.score() + combat_score + self.INFORMATION_BONUS

        return TurnPlan(
            pre_combat_actions=[],
            attack_config=attack_ids,
            post_combat_actions=post_actions,
            expected_score=score,
            reasoning=" → ".join(reasoning_parts) if reasoning_parts else "Attack then deploy",
        )

    def _evaluate_hold_up_mana(self, board: VirtualBoard,
                                turn: int) -> TurnPlan:
        """Strategy: Hold up mana for responses, minimal attack."""
        sim = board.copy()
        reasoning_parts = []

        # Check if we have instant-speed interaction
        instants = [s for s in sim.my_hand if s.is_instant]
        has_counter = any("counterspell" in s.tags for s in instants)
        has_removal = any("removal" in s.tags for s in instants)

        if not instants:
            # No interaction to hold up — this strategy is bad
            return TurnPlan(
                pre_combat_actions=[],
                attack_config=[],
                post_combat_actions=[],
                expected_score=sim.score() - 5.0,  # penalty for doing nothing
                reasoning="No interaction available — bad strategy",
            )

        # Only attack with evasive/safe creatures
        evasive = [c for c in sim.my_creatures
                   if not c.is_tapped and c.power > 0
                   and ("flying" in c.keywords or "menace" in c.keywords)]
        attack_ids = [c.instance_id for c in evasive]

        score = sim.score()
        # Bonus for holding up mana
        if has_counter:
            score += MANA_RESERVATION_WEIGHT * 1.5
            reasoning_parts.append("Hold up counter mana")
        if has_removal:
            score += MANA_RESERVATION_WEIGHT
            reasoning_parts.append("Hold up removal mana")

        # Small bonus for evasive attacks that don't tap us out
        if evasive:
            score += sum(c.power for c in evasive) * 0.5
            reasoning_parts.append(f"Attack with {len(evasive)} evasive creatures")

        return TurnPlan(
            pre_combat_actions=[],
            attack_config=attack_ids,
            post_combat_actions=[],
            expected_score=score,
            reasoning=" + ".join(reasoning_parts) if reasoning_parts else "Hold up mana",
        )

    def _evaluate_lethal_push(self, board: VirtualBoard,
                               turn: int) -> TurnPlan:
        """Strategy: Use everything to try to kill the opponent."""
        sim = board.copy()
        pre_actions = []
        reasoning_parts = []

        # Calculate total potential damage
        creature_damage = sum(c.power for c in sim.my_creatures
                             if not c.is_tapped and c.power > 0)

        # Add burn spells
        burn = [s for s in sim.my_hand
                if "removal" in s.tags and s.damage > 0 and s.cmc <= sim.my_mana]
        burn_damage = sum(s.damage for s in burn)
        total_mana_for_burn = sum(s.cmc for s in burn)

        # Can we kill with creatures + burn?
        total_potential = creature_damage + burn_damage
        if total_potential < sim.opp_life:
            # Not lethal — this strategy is bad
            return TurnPlan(
                pre_combat_actions=[],
                attack_config=[],
                post_combat_actions=[],
                expected_score=sim.score() - 10.0,
                reasoning="Not enough damage for lethal push",
            )

        # Remove blockers with burn first if needed
        blockers = [c for c in sim.opp_creatures if not c.is_tapped]
        if blockers:
            # Use burn on blockers to clear the way
            remaining_burn = list(burn)
            for blocker in sorted(blockers, key=lambda b: b.value, reverse=True):
                if not remaining_burn:
                    break
                for spell in remaining_burn:
                    if spell.damage >= blocker.toughness and spell.cmc <= sim.my_mana:
                        sim.opp_creatures = [c for c in sim.opp_creatures
                                             if c.instance_id != blocker.instance_id]
                        sim.my_mana -= spell.cmc
                        remaining_burn.remove(spell)
                        pre_actions.append(("cast_removal", spell.instance_id))
                        reasoning_parts.append(f"Burn {blocker.name}")
                        break

        # Attack with everything
        valid = [c for c in sim.my_creatures if not c.is_tapped and c.power > 0]
        attack_ids = [c.instance_id for c in valid]
        reasoning_parts.append(f"Alpha strike with {len(valid)} creatures")

        # Burn face with remaining burn
        for spell in [s for s in sim.my_hand
                      if "removal" in s.tags and s.damage > 0
                      and s.cmc <= sim.my_mana
                      and s.instance_id not in [a[1] for a in pre_actions]]:
            pre_actions.append(("cast_burn_face", spell.instance_id))
            reasoning_parts.append(f"Burn face with {spell.name}")

        score = sim.score() + self.combat_planner.LETHAL_BONUS

        return TurnPlan(
            pre_combat_actions=pre_actions,
            attack_config=attack_ids,
            post_combat_actions=[],
            expected_score=score,
            reasoning=" → ".join(reasoning_parts) if reasoning_parts else "Lethal push",
        )


# ═══════════════════════════════════════════════════════════════════
# Board extraction — convert real game state to virtual board
# ═══════════════════════════════════════════════════════════════════

def extract_virtual_board(game: "GameState", player_idx: int) -> VirtualBoard:
    """Convert a real GameState into a VirtualBoard for planning."""
    from ai.evaluator import _permanent_value, estimate_spell_value
    from engine.cards import Keyword

    me = game.players[player_idx]
    opp = game.players[1 - player_idx]

    def to_virtual_creature(card, controller_idx) -> VirtualCreature:
        controller = game.players[controller_idx]
        kw_set = set()
        for kw in card.keywords:
            kw_name = kw.name.lower() if hasattr(kw, 'name') else str(kw).lower()
            kw_set.add(kw_name)
        return VirtualCreature(
            instance_id=card.instance_id,
            name=card.name,
            power=card.power or 0,
            toughness=card.toughness or 0,
            keywords=kw_set,
            is_tapped=card.tapped,
            controller=controller_idx,
            value=_permanent_value(card, controller, game, controller_idx),
            cmc=card.template.cmc or 0,
            has_etb="etb_value" in card.template.tags,
        )

    def to_virtual_spell(card) -> VirtualSpell:
        kw_set = set()
        for kw in card.template.keywords:
            kw_name = kw.name.lower() if hasattr(kw, 'name') else str(kw).lower()
            kw_set.add(kw_name)
        return VirtualSpell(
            instance_id=card.instance_id,
            name=card.name,
            cmc=card.template.cmc or 0,
            tags=set(card.template.tags),
            is_instant=card.template.is_instant or card.template.has_flash,
            is_creature=card.template.is_creature,
            power=card.template.power or 0,
            toughness=card.template.toughness or 0,
            keywords=kw_set,
            spell_value=estimate_spell_value(card, game, player_idx),
            damage=_spell_damage(card),
            has_etb="etb_value" in card.template.tags,
        )

    my_creatures = [to_virtual_creature(c, player_idx) for c in me.creatures]
    opp_creatures = [to_virtual_creature(c, 1 - player_idx) for c in opp.creatures]
    my_hand = [to_virtual_spell(c) for c in me.hand if not c.template.is_land]

    return VirtualBoard(
        my_creatures=my_creatures,
        opp_creatures=opp_creatures,
        my_life=me.life,
        opp_life=opp.life,
        my_hand=my_hand,
        my_mana=me.available_mana_estimate + me.mana_pool.total(),
        opp_mana=opp.available_mana_estimate + opp.mana_pool.total(),
    )


def _spell_damage(card) -> int:
    """Extract damage amount from a spell (for burn/removal spells)."""
    from decks.card_knowledge_loader import get_burn_damage
    known = get_burn_damage(card.template.name)
    if known > 0:
        return known
    if "removal" in card.template.tags:
        return 99  # generic removal kills anything
    return 0
