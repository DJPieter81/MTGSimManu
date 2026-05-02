"""
Unified Board Evaluation — one function for every decision.

Architecture:
  1. BoardAssessment — strategic snapshot derived from game state
  2. Action          — describes what's being considered (cost + benefit)
  3. evaluate_action — single entry point: is this action worth it?

Every binary decision in the engine reduces to:
    evaluate_action(game, player_idx, action) -> bool

No deck names. No turn brackets. No should_X functions.
Behavior emerges from board state.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Set, Optional, List

if TYPE_CHECKING:
    from engine.game_state import GameState
    from engine.cards import CardInstance


# ─────────────────────────────────────────────────────────────
# 1. Board Assessment — strategic snapshot
# ─────────────────────────────────────────────────────────────

@dataclass
class BoardAssessment:
    """Strategic snapshot derived entirely from game state."""

    # Clock (turns to win/lose, lower = faster)
    my_clock: float = 99.0
    opp_clock: float = 99.0

    # Derived pressure (0 = relaxed, 1 = must act now)
    pressure: float = 0.0

    # Life
    my_life: int = 20
    opp_life: int = 20

    # Mana
    mana_available: int = 0
    total_lands: int = 0

    # Board power
    my_power: int = 0
    opp_power: int = 0
    my_creature_count: int = 0
    opp_creature_count: int = 0

    # Hand analysis
    cheapest_spell_cmc: int = 99
    has_instant_in_hand: bool = False
    colors_available: Set[str] = field(default_factory=set)
    colors_missing: Set[str] = field(default_factory=set)


def assess_board(game: "GameState", player_idx: int) -> BoardAssessment:
    """Derive a BoardAssessment purely from game state."""
    a = BoardAssessment()
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]

    a.my_life = me.life
    a.opp_life = opp.life

    # Board power
    for c in me.creatures:
        a.my_power += _effective_power(c, game, player_idx)
        a.my_creature_count += 1
    for c in opp.creatures:
        a.opp_power += _effective_power(c, game, 1 - player_idx)
        a.opp_creature_count += 1

    # Mana
    a.mana_available = sum(1 for c in me.battlefield
                           if c.template.is_land and not c.tapped)
    if hasattr(me, 'mana_pool'):
        a.mana_available += me.mana_pool.total()
    a.total_lands = len(me.lands)

    # Clock
    if a.my_power > 0:
        a.my_clock = max(1.0, a.opp_life / a.my_power)
    if a.opp_power > 0:
        a.opp_clock = max(1.0, a.my_life / a.opp_power)

    # Pressure
    if a.opp_clock < 99 and a.my_clock < 99:
        a.pressure = _sigmoid(a.my_clock - a.opp_clock, 0.5)
    elif a.opp_clock < 99:
        a.pressure = 0.8
    else:
        a.pressure = 0.2

    # Colors available from untapped lands
    for c in me.battlefield:
        if c.template.is_land and not c.tapped:
            for color in c.template.produces_mana:
                a.colors_available.add(color)

    # Hand analysis
    needed_colors: Set[str] = set()
    for card in me.hand:
        if card.template.is_land or not card.template.is_spell:
            continue
        cmc = _effective_cmc(card, me)
        if cmc < a.cheapest_spell_cmc:
            a.cheapest_spell_cmc = cmc
        spell_colors = _spell_colors(card)
        needed_colors |= spell_colors
        if _is_instant(card):
            a.has_instant_in_hand = True

    a.colors_missing = needed_colors - a.colors_available
    return a


# ─────────────────────────────────────────────────────────────
# 2. Action — what's being considered
# ─────────────────────────────────────────────────────────────

class ActionType(Enum):
    """Every binary decision in the engine."""
    EVOKE = auto()           # Sacrifice for ETB vs wait for hard-cast
    DASH = auto()            # Haste+bounce vs permanent body
    COMBO_NOW = auto()       # Fire win condition vs keep building
    BLOCK = auto()           # Assign blocker vs take damage


@dataclass
class Action:
    """Describes a decision being evaluated."""
    action_type: ActionType
    # Context varies by action type — stored as dict for flexibility
    context: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 3. evaluate_action — the single entry point
# ─────────────────────────────────────────────────────────────

def evaluate_action(game: "GameState", player_idx: int, action: Action) -> float:
    """Evaluate any action. Returns score: positive = do it, negative = don't.
    
    For boolean decisions, caller checks: evaluate_action(...) > 0
    For comparative decisions (blocking), caller compares scores.
    
    Every decision flows through here. No should_X functions.
    """
    assessment = assess_board(game, player_idx)
    me = game.players[player_idx]

    if action.action_type == ActionType.EVOKE:
        return _eval_evoke(game, me, assessment, action.context,
                            player_idx=player_idx)

    elif action.action_type == ActionType.DASH:
        return _eval_dash(game, me, assessment, action.context,
                          player_idx=player_idx)

    elif action.action_type == ActionType.COMBO_NOW:
        return _eval_combo(game, me, assessment, action.context,
                           player_idx=player_idx)

    elif action.action_type == ActionType.BLOCK:
        return _eval_block(game, me, assessment, action.context,
                           player_idx=player_idx)

    return 0.0


# ─────────────────────────────────────────────────────────────
# 4. Evaluation implementations (private, all use same pattern)
#    Each returns: benefit - cost
# ─────────────────────────────────────────────────────────────


def _eval_evoke(game, me, a: BoardAssessment, ctx: dict,
                 player_idx: Optional[int] = None) -> float:
    """Evoke (sacrifice for ETB) vs wait to hard-cast?
    Returns positive = evoke, negative = wait.

    `player_idx` should be supplied by the caller; the legacy
    `me.index` fallback is unsafe because the player object does
    not carry its own index, causing opp_idx to default to 0
    (the WRONG opponent when me is player 1).  Bug: AzCon evoking
    Solitude on Affinity got opp = player[1] = AzCon itself,
    skipped the small-target gate, and returned +1.0.
    """
    card = ctx.get('card')
    if card is None:
        return 0.0

    cmc = card.template.cmc or 0
    tags = getattr(card.template, 'tags', set())
    oracle = (card.template.oracle_text or "").lower()

    # Resolve opponent index.  Prefer the explicit `player_idx`
    # parameter; fall back to `me.index` for legacy callers that
    # haven't been updated.  The fallback path is incorrect when
    # me does not carry an `index` attribute (Player has no such
    # attribute by default — see engine/game_state.py::Player).
    if player_idx is None:
        player_idx = getattr(me, 'index', 0)
    opp_idx = 1 - player_idx
    opp = game.players[opp_idx]
    
    # Stack-targeting ETBs (e.g. "target creature spell or planeswalker
    # spell" on Subtlety): the word "spell" after the target type means
    # the ETB targets the STACK, not the battlefield.  When the stack
    # contains no opponent spell of the matching type, the ETB fizzles
    # and the evoke pitch (often a free pitch counter) is wasted.
    # Class-wide rule: any printing whose oracle reads "target <type>
    # spell" is covered — the gate is oracle-driven, no card names.
    # Subtlety's wording is "target creature spell or planeswalker
    # spell" (one "target", two clauses); accept the chained "or
    # planeswalker spell" form too.
    targets_creature_spell = 'target creature spell' in oracle
    targets_pw_spell = (
        'target planeswalker spell' in oracle
        or 'or planeswalker spell' in oracle  # Subtlety-style chained clause
    )
    if (targets_creature_spell or targets_pw_spell) and 'removal' not in tags:
        # Walk the stack — does it contain an opponent spell whose type
        # matches what the ETB can target?  Self-controlled spells are
        # excluded (bouncing one's own spell is strict self-harm).
        from engine.stack import StackItemType
        from engine.cards import CardType
        opp_target_on_stack = False
        for item in game.stack.items:
            if item.controller != opp_idx:
                continue
            if item.item_type != StackItemType.SPELL:
                continue
            t = item.source.template
            ctypes = getattr(t, 'card_types', set())
            if targets_creature_spell and CardType.CREATURE in ctypes:
                opp_target_on_stack = True
                break
            if targets_pw_spell and CardType.PLANESWALKER in ctypes:
                opp_target_on_stack = True
                break
        if not opp_target_on_stack:
            return -10.0  # ETB fizzles → pitch cost is wasted

    # Battlefield-targeting ETBs (e.g. "target creature" — no "spell"):
    # if the opponent has no creatures, the ETB fizzles.
    elif 'target creature' in oracle and 'removal' not in tags:
        if not opp.creatures:
            return -10.0  # No valid targets, evoke would waste a card
    
    # Removal ETBs (Solitude, Fury): check if opponent has creatures
    if 'removal' in tags and card.template.is_creature:
        if not opp.creatures and not opp.battlefield:
            return -10.0  # No targets to remove
        # Removal ETBs that heal the opponent (oracle: "gains life equal to its power")
        # are a poor trade when the target is small: 2 cards spent to exile a 1/1
        # while healing the opponent. Only worthwhile against meaningful threats.
        heals_opponent = "gains life" in oracle and "power" in oracle
        if heals_opponent and opp.creatures:
            best_target = max(opp.creatures, key=lambda c: (c.power or 0, c.template.cmc))
            target_power = best_target.power or best_target.template.power or 0
            target_cmc = best_target.template.cmc or 0
            # Use per-deck thresholds for small-creature evoke check
            from ai.gameplan import _ARCHETYPE_THRESHOLDS, DecisionThresholds, get_gameplan
            gp = get_gameplan(getattr(me, 'deck_name', ''))
            archetype = gp.archetype if gp else 'midrange'
            th = _ARCHETYPE_THRESHOLDS.get(archetype, DecisionThresholds())
            if target_power <= th.evoke_skip_small_power and target_cmc <= th.evoke_skip_small_cmc:
                return -2.0  # Not worth evoking for small threats

    if a.mana_available >= cmc:
        # Can hard-cast NOW — but only if we have the right colors
        existing_colors = set()
        for land in me.lands:
            existing_colors.update(land.template.produces_mana)
        needed_colors = set()
        mc = card.template.mana_cost
        if mc.white > 0: needed_colors.add("W")
        if mc.blue > 0: needed_colors.add("U")
        if mc.black > 0: needed_colors.add("B")
        if mc.red > 0: needed_colors.add("R")
        if mc.green > 0: needed_colors.add("G")
        if needed_colors <= existing_colors:
            return -10.0  # Can actually hardcast — prefer body + ETB

    # Load evoke thresholds from archetype
    from ai.gameplan import _ARCHETYPE_THRESHOLDS, DecisionThresholds, get_gameplan
    gp = get_gameplan(getattr(me, 'deck_name', ''))
    archetype = gp.archetype if gp else 'midrange'
    th = _ARCHETYPE_THRESHOLDS.get(archetype, DecisionThresholds())

    # Check if we can actually hard-cast with correct COLORS, not just land count
    total_lands = len(me.lands)
    if total_lands >= cmc:
        existing_colors = set()
        for land in me.lands:
            existing_colors.update(land.template.produces_mana)
        needed_colors = set()
        mc = card.template.mana_cost
        if mc.white > 0: needed_colors.add("W")
        if mc.blue > 0: needed_colors.add("U")
        if mc.black > 0: needed_colors.add("B")
        if mc.red > 0: needed_colors.add("R")
        if mc.green > 0: needed_colors.add("G")
        has_all_colors = needed_colors <= existing_colors

        if has_all_colors:
            return a.pressure - th.evoke_hardcast_next_turn
        else:
            return a.pressure - th.evoke_wrong_colors

    # Can't hard-cast for multiple turns — evoke for the ETB value
    return 1.0


def _eval_dash(game, me, a: BoardAssessment, ctx: dict,
               player_idx: Optional[int] = None) -> float:
    """Dash (haste+bounce) vs hard-cast (permanent body)?
    Returns positive = dash, negative = hard-cast."""
    can_normal = ctx.get('can_normal', False)
    can_dash = ctx.get('can_dash', False)

    if can_dash and not can_normal:
        return 10.0  # Only option
    if not can_dash:
        return -10.0  # Can't dash

    if player_idx is None:
        player_idx = getattr(me, 'index', 0)
    opp = game.players[1 - player_idx]

    opp_has_blockers = any(c.can_block for c in opp.creatures)
    opp_threatening = len(opp.creatures) >= 2

    score = 0.0

    # Early game with empty opponent board: Dash for guaranteed haste damage
    if not opp_has_blockers and game.turn_number <= 3:
        score += 2.0  # Haste damage is huge in early turns

    if opp_has_blockers or opp_threatening:
        score += 1.0  # Dodge removal by bouncing back

    if a.pressure > 0.6:
        score += 0.5

    # Prefer permanent body only when they can block (body has blocking value)
    if opp_has_blockers:
        score -= 0.3

    return score


def _eval_combo(game, me, a: BoardAssessment, ctx: dict,
                player_idx: Optional[int] = None) -> float:
    """Fire win condition now vs keep building?
    Returns positive = go for it, negative = wait."""
    projected_damage = ctx.get('projected_damage', 0)
    if player_idx is None:
        player_idx = getattr(me, 'index', 0)
    opp = game.players[1 - player_idx]

    # Lethal projected — always go
    if projected_damage >= opp.life:
        return 10.0

    # Count combo resources by card mechanics
    hand = [c for c in me.hand if not c.template.is_land]
    bf = list(me.battlefield)
    gy = list(me.graveyard)

    cost_reducers = sum(1 for c in bf
                        if 'cost_reducer' in getattr(c.template, 'tags', set()))

    castable_rituals = sum(1 for c in hand
                           if 'ritual' in getattr(c.template, 'tags', set())
                           and game.can_cast(player_idx, c))

    has_extender = any(
        'flashback_enabler' in getattr(c.template, 'tags', set()) or
        'flashback' in getattr(c.template, 'tags', set()) or
        'tutor' in getattr(c.template, 'tags', set())
        for c in hand if game.can_cast(player_idx, c)
    )

    gy_rituals = sum(1 for c in gy
                     if 'ritual' in getattr(c.template, 'tags', set())
                     and getattr(c, 'has_flashback', False))

    castable_cantrips = sum(1 for c in hand
                            if 'cantrip' in getattr(c.template, 'tags', set())
                            and game.can_cast(player_idx, c))

    total_chain = castable_rituals + castable_cantrips
    total_with_gy = total_chain + gy_rituals

    # Score based on resources available
    score = -0.5  # Default: wait
    if castable_rituals >= 1:
        score = 1.0
    if cost_reducers >= 1 and total_chain >= 1:
        score = max(score, 1.5)
    if has_extender and total_with_gy >= 2:
        score = max(score, 1.0)
    if total_chain >= 2:
        score = max(score, 0.8)
    if a.pressure > 0.7:
        score = max(score, 0.5)  # Desperation attempt

    return score


def _eval_block(game, me, a: BoardAssessment, ctx: dict,
                player_idx: Optional[int] = None) -> float:
    """Block this attacker with this blocker?
    Returns positive = block, negative = don't.

    Score = damage_prevented + attacker_killed_value - blocker_lost_value.
    Blocker survives → free block → always positive.
    """
    from engine.cards import Keyword

    attacker = ctx.get('attacker')
    blocker = ctx.get('blocker')
    if not attacker or not blocker:
        return 0.0

    a_power = attacker.power or 0
    a_tough = attacker.toughness or 0
    b_power = blocker.power or 0
    b_tough = blocker.toughness or 0

    # --- Combat outcome ---
    has_deathtouch = Keyword.DEATHTOUCH in blocker.keywords
    has_first_strike = Keyword.FIRST_STRIKE in blocker.keywords
    attacker_has_deathtouch = Keyword.DEATHTOUCH in attacker.keywords
    attacker_has_first_strike = Keyword.FIRST_STRIKE in attacker.keywords

    # First strike: if blocker has it and kills attacker, blocker takes no damage
    if has_first_strike and (b_power >= a_tough or has_deathtouch):
        blocker_survives = True
        attacker_dies = True
    elif attacker_has_first_strike and (a_power >= b_tough or attacker_has_deathtouch):
        blocker_survives = False
        attacker_dies = (b_power >= a_tough or has_deathtouch)  # only if blocker survives to deal damage
        # Blocker dies before dealing damage
        attacker_dies = False
    else:
        blocker_survives = a_power < b_tough and not attacker_has_deathtouch
        attacker_dies = b_power >= a_tough or has_deathtouch

    # --- Damage prevented ---
    damage_prevented = a_power
    if Keyword.TRAMPLE in attacker.keywords:
        # Trample: excess damage over blocker toughness tramples through
        damage_prevented = min(a_power, b_tough)

    # --- Creature values (CMC-weighted) ---
    a_cmc = attacker.template.cmc or 0
    b_cmc = blocker.template.cmc or 0
    attacker_val = max(a_cmc, a_power + a_tough * 0.3) * 1.5
    blocker_val = max(b_cmc, b_power + b_tough * 0.3) * 1.5

    # --- Score: benefit - cost ---
    # Benefit 1: life saved (direct value, 1 point per life point)
    value = float(damage_prevented)

    # Scale life value by urgency — shorter opponent clock = each life point matters more
    if a.opp_clock < 99:
        value *= (1.0 + 1.0 / max(a.opp_clock, 1))
    # Scale by fraction of life this hit represents
    life_fraction = damage_prevented / max(a.my_life, 1)
    value *= (1.0 + life_fraction)
    if a.my_life <= damage_prevented:
        value = 100.0  # this block prevents lethal — always block

    # Benefit 2: killing the attacker
    if attacker_dies:
        value += attacker_val

    # Cost: losing the blocker
    if not blocker_survives:
        value -= blocker_val

    # Blocker survives = free block, always positive (value >= damage_prevented)
    # 1-for-1 trade: positive when attacker_val > blocker_val
    # Chump block: positive when damage_prevented > blocker_val

    # Keywords
    if has_deathtouch:
        value += 2.0  # deathtouch blocker always kills, extra value
    if has_first_strike and attacker_dies:
        value += 1.0  # kills before taking damage

    return value


# ─────────────────────────────────────────────────────────────
# 5. Value helpers
# ─────────────────────────────────────────────────────────────

def _life_value(life_points: int, a: BoardAssessment) -> float:
    """How much is N life worth? Scales with life scarcity and opponent's clock."""
    if a.my_life <= 0:
        return 999.0
    fraction = life_points / a.my_life
    if a.opp_clock < 99:
        scarcity = 1.0 / max(1.0, a.opp_clock)
    else:
        scarcity = 0.05
    return fraction * scarcity * 10.0


# ─────────────────────────────────────────────────────────────
# 6. Board helpers
# ─────────────────────────────────────────────────────────────

def _effective_power(card: "CardInstance", game: "GameState",
                     player_idx: int) -> int:
    power = card.template.power or 0
    from engine.cards import DOMAIN_POWER_CREATURES
    if card.name in DOMAIN_POWER_CREATURES:
        power = _count_domain(game, player_idx)
    return max(0, power)


def _count_domain(game: "GameState", player_idx: int) -> int:
    subtypes = set()
    for c in game.players[player_idx].battlefield:
        if c.template.is_land:
            for st in getattr(c.template, "subtypes", []):
                if st in {"Plains", "Island", "Swamp", "Mountain", "Forest"}:
                    subtypes.add(st)
    return min(len(subtypes), 5)


def _effective_cmc(card: "CardInstance", player) -> int:
    cmc = card.template.cmc or 0
    if hasattr(player, 'effective_cmc_overrides') and card.name in player.effective_cmc_overrides:
        cmc = player.effective_cmc_overrides[card.name]
    return cmc


def _spell_colors(card: "CardInstance") -> Set[str]:
    from ai.mana_planner import COLOR_MAP
    colors = set()
    mc = card.template.mana_cost
    for code, attr in COLOR_MAP.items():
        if getattr(mc, attr, 0) > 0:
            colors.add(code)
    return colors


def _is_instant(card: "CardInstance") -> bool:
    from engine.card_database import CardType
    card_types = card.template.card_types
    if card_types and CardType.INSTANT in card_types:
        return True
    if hasattr(card.template, 'is_instant') and card.template.is_instant:
        return True
    return False


def _sigmoid(x: float, steepness: float = 1.0) -> float:
    return 1.0 / (1.0 + math.exp(-steepness * x))
