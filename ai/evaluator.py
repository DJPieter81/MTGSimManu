"""
Board Evaluator — Goal-Oriented MTG AI Core
=============================================
A single evaluate() function that scores any game state from a player's
perspective.  Every AI decision becomes: "which legal action maximises
evaluate(resulting_state) − evaluate(current_state)?"

Dimensions
----------
1. Life differential (non-linear)
2. Board presence  (actual P/T, keywords, mana, equipment state)
3. Card advantage   (hand size + quality, graveyard-as-resource)
4. Tempo / mana efficiency
5. Role assessment  (beatdown ↔ control slider)
6. Opponent modelling (deck knowledge, cards seen, mana held up)

All weights are tuned so that the evaluation is in "life-point equivalents":
+1.0 ≈ being 1 life ahead in an otherwise equal position.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple
from enum import Enum
import math

from ai.scoring_constants import (
    ABILITY_BONUS_CARD_ADVANTAGE,
    ABILITY_BONUS_COMBO,
    ABILITY_BONUS_COST_REDUCER,
    ABILITY_BONUS_EQUIPMENT,
    ABILITY_BONUS_ETB_VALUE,
    ABILITY_BONUS_PROTECTION,
    ABILITY_BONUS_THREAT,
    ABILITY_BONUS_TOKEN_MAKER,
    ABILITY_TYPE_ACTIVATED,
    ABILITY_TYPE_ATTACK_TRIGGER,
    ABILITY_TYPE_DIES_TRIGGER,
    ABILITY_TYPE_STATIC,
    ABILITY_TYPE_UPKEEP,
    ARTIFACT_EQUIPMENT_ACTIVE_VALUE,
    ARTIFACT_EQUIPMENT_IDLE_VALUE,
    ARTIFACT_GENERIC_PER_CMC,
    ARTIFACT_MANA_ROCK_VALUE,
    BLINK_ETB_AVAILABLE_BONUS,
    BLINK_NO_CREATURES_PENALTY,
    BLINK_NO_ETB_PENALTY,
    CANTRIP_REPLACEMENT_BONUS,
    CARD_DRAW_BASE_VALUE,
    CARD_DRAW_CONTROL_BONUS,
    COMBO_PIECE_SPELL_BONUS,
    COUNTERSPELL_PROACTIVE_PENALTY,
    CREATURE_POWER_VALUE,
    CREATURE_TOUGHNESS_VALUE,
    DISCARD_EARLY_GAME_VALUE,
    DISCARD_LATE_GAME_VALUE,
    ENCHANTMENT_PER_CMC,
    EQUIPMENT_BUFF_VALUE_MULTIPLIER,
    EQUIPPED_CREATURE_VULNERABILITY_BONUS,
    HASTE_IMMEDIATE_BONUS,
    HIGH_CMC_RESIDENCY_BONUS,
    HIGH_CMC_THRESHOLD,
    LAND_BASE_VALUE,
    LAND_PER_COLOR_BONUS,
    LAND_UNTAPPED_BONUS,
    LIFE_BAND_CRITICAL_MAX,
    LIFE_BAND_DANGER_MAX,
    LIFE_BAND_NORMAL_MAX,
    LIFE_DANGER_BAND_BASE,
    LIFE_DEAD_PENALTY,
    LIFE_DIMINISHING_BAND_BASE,
    LIFE_NORMAL_BAND_BASE,
    LIFE_PER_POINT_CRITICAL,
    LIFE_PER_POINT_DANGER,
    LIFE_PER_POINT_DIMINISHING,
    LIFE_PER_POINT_NORMAL,
    MANA_EFFICIENCY_GOOD_BONUS,
    MANA_EFFICIENCY_GOOD_HIGH,
    MANA_EFFICIENCY_GOOD_LOW,
    MANA_EFFICIENCY_LOW_BONUS,
    MANA_EFFICIENCY_LOW_THRESHOLD,
    MID_CMC_RESIDENCY_BONUS,
    MID_CMC_THRESHOLD,
    ORACLE_DRAW_VALUE_PER_CARD,
    ORACLE_LIFE_GAIN_PER_POINT,
    ORACLE_LOCK_PIECE_BONUS,
    ORACLE_RECUR_BONUS,
    ORACLE_RECURRING_DAMAGE_PER_POINT,
    ORACLE_RECURRING_DRAW_BONUS,
    ORACLE_SCALE_OVER_TIME_BONUS,
    ORACLE_TOKEN_CREATION_BONUS,
    ORACLE_TRIGGER_DAMAGE_PER_POINT,
    ORACLE_TUTOR_BONUS,
    PLANESWALKER_BASE_VALUE,
    PLANESWALKER_LOYALTY_VALUE,
    PROWESS_PER_CREATURE_BASE,
    PROWESS_PER_CREATURE_CHEAP_BONUS,
    PROWESS_PER_CREATURE_FREE_BONUS,
    RAMP_EARLY_GAME_BONUS,
    RAMP_LATE_GAME_BONUS,
    RAMP_MID_GAME_BONUS,
    REMOVAL_MUST_KILL_BONUS,
    REMOVAL_MUST_KILL_THRESHOLD,
    REMOVAL_NO_LETHAL_PENALTY,
    REMOVAL_NO_TARGET_PENALTY,
    REMOVAL_NONCREATURE_FALLBACK_BONUS,
    REMOVAL_TARGET_VALUE_MULTIPLIER,
    REMOVAL_TEMPO_DELTA_PER_CMC,
    ROLE_BEATDOWN_THREAT_MULTIPLIER,
    ROLE_CONTROL_REMOVAL_MULTIPLIER,
    ROLE_CONTROL_SMALL_CREATURE_DAMPER,
    ROLE_LIFE_BEATDOWN_GAP,
    SILENCE_NO_TARGET_PENALTY,
    SILENCE_PROTECT_BONUS,
    SILENCE_THREAT_CMC_THRESHOLD,
    SPELL_DAMAGE_DESTROY_EXILE_SENTINEL,
    SPELL_DAMAGE_DOMAIN_MAX,
    SPELL_DAMAGE_ENERGY_FALLBACK,
    SPELL_DAMAGE_X_SPELL_FALLBACK,
    TAP_OUT_HOLDING_REMOVAL_PENALTY,
    UNCASTABLE_PENALTY,
    WIPE_FAVOURABLE_FLAT_BONUS,
    WIPE_MY_CREATURE_LOSS_VALUE,
    WIPE_NET_DESTROYED_VALUE,
    WIPE_OPP_CREATURE_VALUE,
)

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate


# ═══════════════════════════════════════════════════════════════════
# Role assessment — "Who's the beatdown?"
# ═══════════════════════════════════════════════════════════════════

class Role(Enum):
    BEATDOWN = "beatdown"
    CONTROL = "control"

def assess_role(game: "GameState", player_idx: int) -> Role:
    """Determine if we should be attacking or defending.
    Compare total board power: higher power = beatdown."""
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]
    my_power = sum(c.power or 0 for c in me.creatures)
    opp_power = sum(c.power or 0 for c in opp.creatures)
    my_life = me.life
    opp_life = opp.life
    # Beatdown if: more board power, or opponent lower life, or aggro archetype
    if my_power > opp_power + 2:
        return Role.BEATDOWN
    if opp_life < my_life - ROLE_LIFE_BEATDOWN_GAP:
        return Role.BEATDOWN
    if my_power < opp_power - 2:
        return Role.CONTROL
    # Default: deck with faster clock is beatdown
    return Role.BEATDOWN if my_power >= opp_power else Role.CONTROL


# ═══════════════════════════════════════════════════════════════════
# Constants & keyword value table
# ═══════════════════════════════════════════════════════════════════

# Keyword → approximate "life-point equivalent" bonus on a creature
# Derived from limited/constructed card evaluation heuristics
_KW_VALUE: Dict[str, float] = {
    "flying":        2.0,
    "trample":       1.0,
    "lifelink":      2.5,
    "haste":         1.5,
    "deathtouch":    2.0,
    "first_strike":  1.5,
    "double_strike": 3.0,
    "hexproof":      2.0,
    "indestructible": 3.5,
    "menace":        1.0,
    "reach":         0.5,
    "vigilance":     1.0,
    "undying":       2.0,
    "annihilator":   4.0,
    "prowess":       1.5,
    "flash":         0.5,
    "cascade":       3.0,
}


def _ability_bonus(card, template=None) -> float:
    """Derive bonus value from a permanent's abilities and tags.

    Replaces _NOTABLE_PERMANENTS. Values what the card DOES beyond
    raw stats and keywords — ETB effects, attack triggers, static
    abilities, activated abilities, and synergy engine potential.

    Works on both CardInstance (on battlefield) and CardTemplate
    (during spell evaluation). Pass template explicitly when
    evaluating a spell not yet on the battlefield.
    """
    if template is None:
        template = card.template if hasattr(card, 'template') else card

    tags = getattr(template, 'tags', set())
    abilities = getattr(template, 'abilities', [])
    oracle = (getattr(template, 'oracle_text', '') or '').lower()
    bonus = 0.0

    # ── Tag-based value (what the card IS) ──
    if 'etb_value' in tags:
        bonus += ABILITY_BONUS_ETB_VALUE
    if 'card_advantage' in tags:
        bonus += ABILITY_BONUS_CARD_ADVANTAGE
    if 'cost_reducer' in tags:
        bonus += ABILITY_BONUS_COST_REDUCER
    if 'token_maker' in tags:
        bonus += ABILITY_BONUS_TOKEN_MAKER
    if 'threat' in tags:
        bonus += ABILITY_BONUS_THREAT
    if 'combo' in tags:
        bonus += ABILITY_BONUS_COMBO
    if 'protection' in tags:
        bonus += ABILITY_BONUS_PROTECTION
    if 'equipment' in tags:
        bonus += ABILITY_BONUS_EQUIPMENT

    # ── Ability-type value (what the card DOES each turn) ──
    from engine.cards import AbilityType
    has_etb = False
    has_attack_trigger = False
    has_dies_trigger = False
    has_activated = False
    has_static = False
    has_upkeep = False

    for ab in abilities:
        if ab.ability_type == AbilityType.ETB:
            has_etb = True
        elif ab.ability_type == AbilityType.ATTACK:
            has_attack_trigger = True
        elif ab.ability_type == AbilityType.DIES:
            has_dies_trigger = True
        elif ab.ability_type == AbilityType.ACTIVATED:
            has_activated = True
        elif ab.ability_type == AbilityType.STATIC:
            has_static = True
        elif ab.ability_type == AbilityType.UPKEEP:
            has_upkeep = True

    # Attack triggers generate value every combat — recurring advantage
    if has_attack_trigger:
        bonus += ABILITY_TYPE_ATTACK_TRIGGER
    # Dies triggers make the creature costly to remove
    if has_dies_trigger:
        bonus += ABILITY_TYPE_DIES_TRIGGER
    # Activated abilities = options every turn
    if has_activated:
        bonus += ABILITY_TYPE_ACTIVATED
    # Static abilities affect the whole board
    if has_static:
        bonus += ABILITY_TYPE_STATIC
    # Upkeep triggers = recurring value
    if has_upkeep:
        bonus += ABILITY_TYPE_UPKEEP

    # ── Oracle text patterns (what the effect magnitude IS) ──
    # These detect the POWER of abilities, not just their existence.
    import re

    # Draw effects: more cards drawn = more value
    draw_match = re.search(r'draw\s+(\w+)\s+card', oracle)
    if draw_match:
        word = draw_match.group(1)
        # Word→int map for "draw <word> card[s]" — each entry is the
        # rules-defined cardinal of the English word, not a tuning value.
        n = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,  # magic-allow: english word→int rules mapping
             'five': 5, 'six': 6, 'seven': 7}.get(word, 1)  # magic-allow: english word→int rules mapping
        bonus += n * ORACLE_DRAW_VALUE_PER_CARD

    # Repeatable draw ("whenever" + "draw")
    if 'whenever' in oracle and 'draw' in oracle:
        bonus += ORACLE_RECURRING_DRAW_BONUS

    # Damage to opponents/creatures on trigger
    dmg_match = re.search(r'deals?\s+(\d+)\s+damage', oracle)
    if dmg_match:
        dmg = int(dmg_match.group(1))
        if 'each opponent' in oracle or 'each player' in oracle:
            bonus += dmg * ORACLE_RECURRING_DAMAGE_PER_POINT
        elif has_etb or has_attack_trigger:
            bonus += dmg * ORACLE_TRIGGER_DAMAGE_PER_POINT

    # Life gain effects
    life_match = re.search(r'gain\s+(\d+)\s+life', oracle)
    if life_match:
        life = int(life_match.group(1))
        bonus += life * ORACLE_LIFE_GAIN_PER_POINT

    # Token creation
    if 'create' in oracle and 'token' in oracle:
        bonus += ORACLE_TOKEN_CREATION_BONUS

    # Search library (tutor effect)
    if 'search your library' in oracle:
        bonus += ORACLE_TUTOR_BONUS

    # Mana denial / lock effects
    if any(phrase in oracle for phrase in [
        "can\'t cast", "can\'t be cast", "nonbasic lands are",
        "opponents can\'t", "spells cost", "additional"
    ]):
        bonus += ORACLE_LOCK_PIECE_BONUS

    # Recurring from graveyard (escape, flashback, undying, etc.)
    if any(phrase in oracle for phrase in [
        'escape', 'flashback', 'undying', 'persist',
        'return .* from .* graveyard'
    ]):
        bonus += ORACLE_RECUR_BONUS

    # Self-pump / grows over time
    if any(phrase in oracle for phrase in [
        'put a +1/+1 counter', 'gets +', 'additional +'
    ]):
        bonus += ORACLE_SCALE_OVER_TIME_BONUS

    # CMC scaling: expensive permanents that stuck are worth more
    cmc = getattr(template, 'cmc', 0) or 0
    if cmc >= HIGH_CMC_THRESHOLD:
        bonus += HIGH_CMC_RESIDENCY_BONUS
    elif cmc >= MID_CMC_THRESHOLD:
        bonus += MID_CMC_RESIDENCY_BONUS

    return bonus


def estimate_permanent_value(card: "CardInstance",
                             controller: "PlayerState",
                             game: "GameState",
                             player_idx: int) -> float:
    """Estimate the value of a single permanent on the battlefield."""
    return _permanent_value(card, controller, game, player_idx)


def estimate_removal_value(target: "CardInstance",
                           removal_cmc: int,
                           target_controller: "PlayerState",
                           game: "GameState",
                           target_player_idx: int) -> float:
    """Estimate the value of removing a specific permanent.

    Returns a positive number = good for the player casting removal.
    Accounts for:
      - The permanent's board value (including equipment buffs)
      - Tempo: mana efficiency of the trade
      - Cascading effects: equipment falling off, tokens dying, etc.
    """
    perm_value = _permanent_value(target, target_controller, game, target_player_idx)

    # Equipment cascade: if this creature carries equipment tags,
    # removing it also neutralises the equipment buff
    if target.instance_tags:
        # The equipment itself stays on board but is now useless until re-equipped
        # So the "lost value" includes the buff portion
        base_power = target.template.power or 0
        actual_power = target.power or 0
        buff = actual_power - base_power
        if buff > 0:
            perm_value += buff * EQUIPMENT_BUFF_VALUE_MULTIPLIER

    # Tempo bonus/penalty: cheap removal on expensive threat = tempo gain
    target_cmc = target.template.cmc or 1
    tempo_delta = (target_cmc - removal_cmc) * REMOVAL_TEMPO_DELTA_PER_CMC
    perm_value += tempo_delta

    return perm_value


def _estimate_spell_damage_for_eval(spell: "CardInstance") -> int:
    """Estimate how much damage a removal spell deals.

    Returns 99 for destroy/exile effects, the numeric damage for burn spells,
    or 0 if the spell doesn't deal damage directly.
    Used by estimate_spell_value to gate removal scoring on lethality.

    Derived entirely from oracle text and tags — no card-name lookups.
    """
    import re
    template = spell.template
    tags = getattr(template, 'tags', set())
    oracle = (getattr(template, 'oracle_text', '') or '').lower()

    # Energy-scaling damage (e.g. Galvanic Discharge)
    if 'energy_scaling' in tags or 'energy' in tags:
        # Conservative estimate: base damage from oracle + assume 0 energy
        m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
        if m:
            return int(m.group(1))
        return SPELL_DAMAGE_ENERGY_FALLBACK

    # Domain-scaling damage (e.g. Tribal Flames)
    if 'domain' in tags:
        m = re.search(r'deals?.*damage.*equal', oracle)
        if m:
            return SPELL_DAMAGE_DOMAIN_MAX

    # Check ability descriptions for destroy/exile
    for ab in template.abilities:
        desc = ab.description.lower()
        if 'destroy' in desc or 'exile' in desc:
            return SPELL_DAMAGE_DESTROY_EXILE_SENTINEL
        if 'damage' in desc:
            nums = re.findall(r'(\d+)\s*damage', desc)
            if nums:
                return int(nums[0])

    # Fallback: parse oracle text directly
    if 'destroy' in oracle or 'exile' in oracle:
        return SPELL_DAMAGE_DESTROY_EXILE_SENTINEL

    m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
    if m:
        return int(m.group(1))

    # X damage spells
    if re.search(r'deals?\s+x\s+damage', oracle):
        return SPELL_DAMAGE_X_SPELL_FALLBACK

    return SPELL_DAMAGE_DESTROY_EXILE_SENTINEL  # default: assume destroy/exile


def estimate_spell_value(spell: "CardInstance",
                         game: "GameState",
                         player_idx: int) -> float:
    """Estimate the value of casting a spell (creature, instant, sorcery, etc.).

    Considers:
      - What the spell does (creature stats, removal, card draw, etc.)
      - Mana efficiency
      - Current board context (do I need threats or answers?)
      - Opportunity cost (mana spent = options lost)
    """
    me  = game.players[player_idx]
    opp = game.players[1 - player_idx]
    template = spell.template
    tags = template.tags
    role = assess_role(game, player_idx)
    phase = _game_phase(game.turn_number, game, player_idx)

    value = 0.0

    # --- Creatures: board presence ---
    if template.is_creature:
        power = template.power or 0
        toughness = template.toughness or 0
        value += power * CREATURE_POWER_VALUE + toughness * CREATURE_TOUGHNESS_VALUE

        # Keyword bonuses
        for kw in template.keywords:
            kw_name = kw.name.lower() if hasattr(kw, 'name') else str(kw).lower()
            value += _KW_VALUE.get(kw_name, 0.0)

        # Ability bonus: derived from mechanics, not a lookup table
        value += _ability_bonus(spell, template)

        # Role adjustment: beatdown wants threats more
        if role == Role.BEATDOWN:
            value *= ROLE_BEATDOWN_THREAT_MULTIPLIER
        elif role == Role.CONTROL and power <= 1:
            value *= ROLE_CONTROL_SMALL_CREATURE_DAMPER

        # Haste bonus: immediate impact
        from engine.cards import Keyword
        if Keyword.HASTE in template.keywords:
            value += HASTE_IMMEDIATE_BONUS

    # --- Removal: opponent board reduction ---
    # v2: Significantly increased removal value to compete with creature deployment.
    # In real MTG, removing a Ragavan T1 is the highest-priority play for any
    # deck with Lightning Bolt. The old 0.7 multiplier made removal score ~4-5
    # while creatures scored 8-15+, causing the AI to always deploy instead.
    if "removal" in tags:
        if opp.creatures:
            # ── Lethality gate for damage-based removal ──
            # Estimate spell damage to check if it can actually kill anything.
            spell_damage = _estimate_spell_damage_for_eval(spell)
            if spell_damage > 0 and spell_damage < SPELL_DAMAGE_DESTROY_EXILE_SENTINEL:
                # Damage-based removal: only consider creatures we can kill
                killable = [
                    c for c in opp.creatures
                    if spell_damage >= (c.toughness or 0) - (getattr(c, 'damage_marked', 0) or 0)
                ]
                if not killable:
                    # Can't kill anything — this removal is useless right now
                    value -= REMOVAL_NO_LETHAL_PENALTY
                    # Skip the removal bonus entirely
                    return value
                eval_targets = killable
            else:
                eval_targets = list(opp.creatures)

            # Value = best target we could actually remove
            best_target_val = max(
                _permanent_value(c, opp, game, 1 - player_idx)
                for c in eval_targets
            )
            value += best_target_val * REMOVAL_TARGET_VALUE_MULTIPLIER
            # Extra bonus for removing value engines that snowball
            if best_target_val >= REMOVAL_MUST_KILL_THRESHOLD:
                value += REMOVAL_MUST_KILL_BONUS
        elif opp.battlefield:
            # Can potentially hit non-creature permanents
            non_land_targets = [c for c in opp.battlefield if not c.template.is_land]
            if non_land_targets:
                value += REMOVAL_NONCREATURE_FALLBACK_BONUS
            else:
                value -= REMOVAL_NO_TARGET_PENALTY
        else:
            value -= REMOVAL_NO_TARGET_PENALTY

        # Control role values removal more
        if role == Role.CONTROL:
            value *= ROLE_CONTROL_REMOVAL_MULTIPLIER

    if "board_wipe" in tags:
        my_creature_count = len(me.creatures)
        opp_creature_count = len(opp.creatures)
        # Good if opponent has more creatures
        net_destroyed = opp_creature_count - my_creature_count
        if net_destroyed > 0:
            value += net_destroyed * WIPE_NET_DESTROYED_VALUE + WIPE_FAVOURABLE_FLAT_BONUS
        elif opp_creature_count >= 2:
            value += opp_creature_count * WIPE_OPP_CREATURE_VALUE - my_creature_count * WIPE_MY_CREATURE_LOSS_VALUE

    # --- Card draw ---
    if "card_advantage" in tags or "cantrip" in tags:
        value += CARD_DRAW_BASE_VALUE
        if role == Role.CONTROL:
            value += CARD_DRAW_CONTROL_BONUS
        if "cantrip" in tags:
            value += CANTRIP_REPLACEMENT_BONUS

    # --- Discard / disruption ---
    if "discard" in tags:
        # Clock-derived early-game (replaces literal `turn_number <= 4`).
        # Discard is "powerful" when neither side has a clock yet — the
        # opp's hand still contains their game plan to be ripped apart.
        from ai.clock import is_early_game
        from ai.ev_evaluator import snapshot_from_game
        if is_early_game(snapshot_from_game(game, player_idx)):
            value += DISCARD_EARLY_GAME_VALUE
        else:
            value += DISCARD_LATE_GAME_VALUE

    # --- Silence effects (detected from 'silence' tag in oracle text) ---
    # Silence spells prevent the opponent from casting spells this turn.
    # During YOUR main phase, this is nearly useless because the opponent
    # can't cast sorceries on your turn anyway. It only blocks instants.
    # Should only be cast when protecting a key play.
    if "silence" in tags:
        # Check if we're about to deploy a high-value threat this turn
        has_key_threat_in_hand = any(
            "threat" in c.template.tags or (c.template.cmc or 0) >= SILENCE_THREAT_CMC_THRESHOLD
            for c in me.hand if c != spell and not c.template.is_land
        )
        opp_has_mana_for_response = (opp.available_mana_estimate >= 1)

        if has_key_threat_in_hand and opp_has_mana_for_response:
            # Protecting a key deployment — moderate value
            value += SILENCE_PROTECT_BONUS
        else:
            # Casting silence with nothing to protect is a waste of a card
            value -= SILENCE_NO_TARGET_PENALTY

    # --- Counterspells ---
    if "counterspell" in tags:
        # Counterspells should NEVER be cast during main phase proactively.
        # They are only useful as responses (handled by decide_response).
        # During main phase evaluation, give them negative value to prevent
        # the AI from casting them with no targets.
        value -= COUNTERSPELL_PROACTIVE_PENALTY

    # --- Blink spells (Ephemerate etc.) ---
    if "blink" in tags:
        # Blink spells should only be cast reactively to protect/retrigger ETB creatures.
        # During main phase proactive evaluation, heavily penalise unless we have
        # a high-value ETB creature on board.
        has_etb_creature = any("etb_value" in c.template.tags for c in me.creatures)
        if has_etb_creature:
            value += BLINK_ETB_AVAILABLE_BONUS
        elif me.creatures:
            value -= BLINK_NO_ETB_PENALTY
        else:
            value -= BLINK_NO_CREATURES_PENALTY

    # --- Prowess / spell-trigger synergy ---
    # When we have creatures with prowess (or similar spell-triggered abilities),
    # noncreature spells are more valuable because they pump those creatures.
    # Detected from the Keyword.PROWESS on creature templates.
    if not template.is_creature and not template.is_land:
        from engine.cards import Keyword as Kw
        prowess_creatures = [
            c for c in me.creatures
            if Kw.PROWESS in c.template.keywords
        ]
        if prowess_creatures:
            # Each prowess creature gets +1/+1 per noncreature spell
            prowess_bonus = len(prowess_creatures) * PROWESS_PER_CREATURE_BASE
            # Cheap spells are better for prowess chaining (more triggers per turn)
            if (template.cmc or 0) <= 1:
                prowess_bonus += len(prowess_creatures) * PROWESS_PER_CREATURE_CHEAP_BONUS
            # Free spells (Mutagenic Growth, Lava Dart flashback) are premium
            if (template.cmc or 0) == 0:
                prowess_bonus += len(prowess_creatures) * PROWESS_PER_CREATURE_FREE_BONUS
            value += prowess_bonus

    # --- Combo pieces ---
    if "combo" in tags:
        value += COMBO_PIECE_SPELL_BONUS

    # --- Ramp / mana ---
    if "ramp" in tags or "mana_source" in tags:
        if phase == GamePhase.EARLY:
            value += RAMP_EARLY_GAME_BONUS
        elif phase == GamePhase.MID:
            value += RAMP_MID_GAME_BONUS
        else:
            value += RAMP_LATE_GAME_BONUS

    # --- Mana efficiency ---
    cmc = template.cmc or 0
    available = me.available_mana_estimate + me.mana_pool.total()
    if available > 0:
        efficiency = cmc / available
        if MANA_EFFICIENCY_GOOD_LOW <= efficiency <= MANA_EFFICIENCY_GOOD_HIGH:
            value += MANA_EFFICIENCY_GOOD_BONUS
        elif efficiency < MANA_EFFICIENCY_LOW_THRESHOLD and cmc > 0:
            value += MANA_EFFICIENCY_LOW_BONUS

    # --- Opportunity cost: mana left over for responses ---
    mana_after = available - cmc
    if mana_after < 0:
        value = -UNCASTABLE_PENALTY
    elif mana_after == 0 and len(me.hand) > 1:
        # Tapping out — penalty if opponent might have threats
        if opp.creatures and any(
            c.template.is_instant and "removal" in c.template.tags
            for c in me.hand if c != spell
        ):
            value -= TAP_OUT_HOLDING_REMOVAL_PENALTY

    return value


def _life_value(life: int) -> float:
    """Non-linear life valuation.

    Life below ~5 is worth much more per point (you're about to die).
    Life above ~15 has diminishing returns (you're safe).
    Uses a log-ish curve.
    """
    if life <= 0:
        return -LIFE_DEAD_PENALTY
    if life <= LIFE_BAND_CRITICAL_MAX:
        return life * LIFE_PER_POINT_CRITICAL  # each point is critical
    if life <= LIFE_BAND_DANGER_MAX:
        return LIFE_DANGER_BAND_BASE + (life - LIFE_BAND_CRITICAL_MAX) * LIFE_PER_POINT_DANGER
    if life <= LIFE_BAND_NORMAL_MAX:
        return LIFE_NORMAL_BAND_BASE + (life - LIFE_BAND_DANGER_MAX) * LIFE_PER_POINT_NORMAL
    # Diminishing returns above LIFE_BAND_NORMAL_MAX
    return LIFE_DIMINISHING_BAND_BASE + (life - LIFE_BAND_NORMAL_MAX) * LIFE_PER_POINT_DIMINISHING



def _permanent_value(card: "CardInstance",
                     controller: "PlayerState",
                     game: "GameState",
                     player_idx: int) -> float:
    """Value a single permanent on the battlefield."""
    from engine.cards import CardType, Keyword

    template = card.template
    value = 0.0

    # --- Creatures ---
    if template.is_creature:
        # Use ACTUAL power/toughness (includes equipment, counters, buffs)
        power = card.power or 0
        toughness = card.toughness or 0
        value += power * CREATURE_POWER_VALUE + toughness * CREATURE_TOUGHNESS_VALUE

        # Keywords
        for kw in card.keywords:
            kw_name = kw.name.lower() if hasattr(kw, 'name') else str(kw).lower()
            value += _KW_VALUE.get(kw_name, 0.0)

        # Equipment tags: the creature is carrying equipment, making it a
        # high-priority target.  We don't double-count the stats (already
        # in actual P/T) but note the vulnerability.
        if card.instance_tags:
            value += EQUIPPED_CREATURE_VULNERABILITY_BONUS

    # --- Lands ---
    elif template.is_land:
        value += LAND_BASE_VALUE
        if template.produces_mana:
            value += len(template.produces_mana) * LAND_PER_COLOR_BONUS
        if not card.tapped:
            value += LAND_UNTAPPED_BONUS

    # --- Artifacts (non-creature) ---
    elif CardType.ARTIFACT in template.card_types and not template.is_creature:
        if "mana_source" in template.tags or template.produces_mana:
            value += ARTIFACT_MANA_ROCK_VALUE
        elif 'equipment' in template.tags:
            # Equipment: check if any creature carries an instance_tag
            # from this equipment (generic detection, no card-name lookup)
            equipped = any(
                c.instance_tags for c in controller.creatures
            ) if controller.creatures else False
            if equipped:
                value += ARTIFACT_EQUIPMENT_ACTIVE_VALUE
            else:
                value += ARTIFACT_EQUIPMENT_IDLE_VALUE
        else:
            value += template.cmc * ARTIFACT_GENERIC_PER_CMC

    # --- Enchantments ---
    elif CardType.ENCHANTMENT in template.card_types:
        value += template.cmc * ENCHANTMENT_PER_CMC

    # --- Planeswalkers ---
    elif CardType.PLANESWALKER in template.card_types:
        value += PLANESWALKER_BASE_VALUE + (card.loyalty_counters or 0) * PLANESWALKER_LOYALTY_VALUE

    # --- Ability bonus (derived from mechanics, not card names) ---
    value += _ability_bonus(card, template)

    return value


