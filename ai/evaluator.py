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
    if opp_life < my_life - 5:
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
        bonus += 2.0  # ETB creatures are worth blinking / protecting
    if 'card_advantage' in tags:
        bonus += 3.0  # draws cards = snowball engine
    if 'cost_reducer' in tags:
        bonus += 2.5  # enables cheaper spells = engine piece
    if 'token_maker' in tags:
        bonus += 1.5  # creates board presence over time
    if 'threat' in tags:
        bonus += 1.0  # tagged as a significant threat
    if 'combo' in tags:
        bonus += 2.0  # combo piece = high priority
    if 'protection' in tags:
        bonus += 1.0  # protects other pieces
    if 'equipment' in tags:
        bonus += 1.0  # force multiplier

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
        bonus += 2.5
    # Dies triggers make the creature costly to remove
    if has_dies_trigger:
        bonus += 1.5
    # Activated abilities = options every turn
    if has_activated:
        bonus += 1.5
    # Static abilities affect the whole board
    if has_static:
        bonus += 1.0
    # Upkeep triggers = recurring value
    if has_upkeep:
        bonus += 1.5

    # ── Oracle text patterns (what the effect magnitude IS) ──
    # These detect the POWER of abilities, not just their existence.
    import re

    # Draw effects: more cards drawn = more value
    draw_match = re.search(r'draw\s+(\w+)\s+card', oracle)
    if draw_match:
        word = draw_match.group(1)
        n = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
             'five': 5, 'six': 6, 'seven': 7}.get(word, 1)
        bonus += n * 1.0  # each card drawn is ~1 point

    # Repeatable draw ("whenever" + "draw")
    if 'whenever' in oracle and 'draw' in oracle:
        bonus += 2.0  # recurring card advantage

    # Damage to opponents/creatures on trigger
    dmg_match = re.search(r'deals?\s+(\d+)\s+damage', oracle)
    if dmg_match:
        dmg = int(dmg_match.group(1))
        if 'each opponent' in oracle or 'each player' in oracle:
            bonus += dmg * 0.8  # recurring damage
        elif has_etb or has_attack_trigger:
            bonus += dmg * 0.5  # trigger-based damage

    # Life gain effects
    life_match = re.search(r'gain\s+(\d+)\s+life', oracle)
    if life_match:
        life = int(life_match.group(1))
        bonus += life * 0.3  # life gain is moderate value

    # Token creation
    if 'create' in oracle and 'token' in oracle:
        bonus += 1.5

    # Search library (tutor effect)
    if 'search your library' in oracle:
        bonus += 2.0  # tutoring is powerful

    # Mana denial / lock effects
    if any(phrase in oracle for phrase in [
        "can\'t cast", "can\'t be cast", "nonbasic lands are",
        "opponents can\'t", "spells cost", "additional"
    ]):
        bonus += 3.0  # lock pieces are high priority targets

    # Recurring from graveyard (escape, flashback, undying, etc.)
    if any(phrase in oracle for phrase in [
        'escape', 'flashback', 'undying', 'persist',
        'return .* from .* graveyard'
    ]):
        bonus += 1.5  # hard to permanently remove

    # Self-pump / grows over time
    if any(phrase in oracle for phrase in [
        'put a +1/+1 counter', 'gets +', 'additional +'
    ]):
        bonus += 1.0  # scales over time

    # CMC scaling: expensive permanents that stuck are worth more
    cmc = getattr(template, 'cmc', 0) or 0
    if cmc >= 6:
        bonus += 1.5  # expensive = high-impact if it resolved
    elif cmc >= 4:
        bonus += 0.5

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
            perm_value += buff * 1.5  # equipment buff is extra value lost

    # Tempo bonus/penalty: cheap removal on expensive threat = tempo gain
    target_cmc = target.template.cmc or 1
    tempo_delta = (target_cmc - removal_cmc) * 0.5
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
        return 2  # fallback for energy-based removal

    # Domain-scaling damage (e.g. Tribal Flames)
    if 'domain' in tags:
        m = re.search(r'deals?.*damage.*equal', oracle)
        if m:
            return 5  # max domain in 5c decks

    # Check ability descriptions for destroy/exile
    for ab in template.abilities:
        desc = ab.description.lower()
        if 'destroy' in desc or 'exile' in desc:
            return 99
        if 'damage' in desc:
            nums = re.findall(r'(\d+)\s*damage', desc)
            if nums:
                return int(nums[0])

    # Fallback: parse oracle text directly
    if 'destroy' in oracle or 'exile' in oracle:
        return 99

    m = re.search(r'deals?\s+(\d+)\s+damage', oracle)
    if m:
        return int(m.group(1))

    # X damage spells
    if re.search(r'deals?\s+x\s+damage', oracle):
        return 3  # conservative estimate for X spells

    return 99  # default: assume destroy/exile


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
        value += power * 1.5 + toughness * 0.8

        # Keyword bonuses
        for kw in template.keywords:
            kw_name = kw.name.lower() if hasattr(kw, 'name') else str(kw).lower()
            value += _KW_VALUE.get(kw_name, 0.0)

        # Ability bonus: derived from mechanics, not a lookup table
        value += _ability_bonus(spell, template)

        # Role adjustment: beatdown wants threats more
        if role == Role.BEATDOWN:
            value *= 1.2
        elif role == Role.CONTROL and power <= 1:
            value *= 0.7  # small creatures less valuable for control

        # Haste bonus: immediate impact
        from engine.cards import Keyword
        if Keyword.HASTE in template.keywords:
            value += 1.1

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
            if spell_damage > 0 and spell_damage < 99:
                # Damage-based removal: only consider creatures we can kill
                killable = [
                    c for c in opp.creatures
                    if spell_damage >= (c.toughness or 0) - (getattr(c, 'damage_marked', 0) or 0)
                ]
                if not killable:
                    # Can't kill anything — this removal is useless right now
                    value -= 15.0
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
            value += best_target_val * 1.2  # was 0.7 — now properly values removal
            # Extra bonus for removing value engines that snowball
            if best_target_val >= 7.0:
                value += 3.0  # must-kill targets get extra priority
        elif opp.battlefield:
            # Can potentially hit non-creature permanents
            non_land_targets = [c for c in opp.battlefield if not c.template.is_land]
            if non_land_targets:
                value += 3.0
            else:
                value -= 5.0  # no valid targets, don't cast
        else:
            value -= 5.0  # no targets at all, don't cast

        # Control role values removal more
        if role == Role.CONTROL:
            value *= 1.4  # was 1.3

    if "board_wipe" in tags:
        my_creature_count = len(me.creatures)
        opp_creature_count = len(opp.creatures)
        # Good if opponent has more creatures
        net_destroyed = opp_creature_count - my_creature_count
        if net_destroyed > 0:
            value += net_destroyed * 3.0 + 2.0
        elif opp_creature_count >= 2:
            value += opp_creature_count * 2.0 - my_creature_count * 1.5

    # --- Card draw ---
    if "card_advantage" in tags or "cantrip" in tags:
        value += 2.0
        if role == Role.CONTROL:
            value += 1.5
        if "cantrip" in tags:
            value += 0.5  # replaces itself

    # --- Discard / disruption ---
    if "discard" in tags:
        if game.turn_number <= 4:
            value += 4.0  # early disruption is powerful
        else:
            value += 1.5

    # --- Silence effects (detected from 'silence' tag in oracle text) ---
    # Silence spells prevent the opponent from casting spells this turn.
    # During YOUR main phase, this is nearly useless because the opponent
    # can't cast sorceries on your turn anyway. It only blocks instants.
    # Should only be cast when protecting a key play.
    if "silence" in tags:
        # Check if we're about to deploy a high-value threat this turn
        has_key_threat_in_hand = any(
            "threat" in c.template.tags or (c.template.cmc or 0) >= 3
            for c in me.hand if c != spell and not c.template.is_land
        )
        opp_has_mana_for_response = (opp.available_mana_estimate >= 1)
        
        if has_key_threat_in_hand and opp_has_mana_for_response:
            # Protecting a key deployment — moderate value
            value += 3.0
        else:
            # Casting silence with nothing to protect is a waste of a card
            value -= 20.0

    # --- Counterspells ---
    if "counterspell" in tags:
        # Counterspells should NEVER be cast during main phase proactively.
        # They are only useful as responses (handled by decide_response).
        # During main phase evaluation, give them negative value to prevent
        # the AI from casting them with no targets.
        value -= 10.0

    # --- Blink spells (Ephemerate etc.) ---
    if "blink" in tags:
        # Blink spells should only be cast reactively to protect/retrigger ETB creatures.
        # During main phase proactive evaluation, heavily penalise unless we have
        # a high-value ETB creature on board.
        has_etb_creature = any("etb_value" in c.template.tags for c in me.creatures)
        if has_etb_creature:
            value += 2.5  # good to blink an ETB creature
        elif me.creatures:
            value -= 5.0  # creatures but no ETB value — hold it for protection
        else:
            value -= 15.0  # no creatures at all — completely useless right now

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
            prowess_bonus = len(prowess_creatures) * 2.5
            # Cheap spells are better for prowess chaining (more triggers per turn)
            if (template.cmc or 0) <= 1:
                prowess_bonus += len(prowess_creatures) * 1.5
            # Free spells (Mutagenic Growth, Lava Dart flashback) are premium
            if (template.cmc or 0) == 0:
                prowess_bonus += len(prowess_creatures) * 2.0
            value += prowess_bonus

    # --- Combo pieces ---
    if "combo" in tags:
        value += 5.5  # combo pieces are critical for combo decks

    # --- Ramp / mana ---
    if "ramp" in tags or "mana_source" in tags:
        if phase == GamePhase.EARLY:
            value += 4.0
        elif phase == GamePhase.MID:
            value += 2.0
        else:
            value += 0.5

    # --- Mana efficiency ---
    cmc = template.cmc or 0
    available = me.available_mana_estimate + me.mana_pool.total()
    if available > 0:
        efficiency = cmc / available
        if 0.6 <= efficiency <= 1.0:
            value += 1.1  # using mana well
        elif efficiency < 0.3 and cmc > 0:
            value += 0.5  # cheap spell, fine but not great mana use

    # --- Opportunity cost: mana left over for responses ---
    mana_after = available - cmc
    if mana_after < 0:
        value = -10.0  # can't cast
    elif mana_after == 0 and len(me.hand) > 1:
        # Tapping out — penalty if opponent might have threats
        if opp.creatures and any(
            c.template.is_instant and "removal" in c.template.tags
            for c in me.hand if c != spell
        ):
            value -= 2.0  # holding up removal is valuable

    return value


def _life_value(life: int) -> float:
    """Non-linear life valuation.

    Life below ~5 is worth much more per point (you're about to die).
    Life above ~15 has diminishing returns (you're safe).
    Uses a log-ish curve.
    """
    if life <= 0:
        return -50.0  # dead
    if life <= 3:
        return life * 3.0  # each point is critical
    if life <= 7:
        return 9.0 + (life - 3) * 2.0  # still dangerous
    if life <= 15:
        return 17.0 + (life - 7) * 1.0  # normal range
    # Diminishing returns above 15
    return 25.0 + (life - 15) * 0.3



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
        value += power * 1.5 + toughness * 0.8

        # Keywords
        for kw in card.keywords:
            kw_name = kw.name.lower() if hasattr(kw, 'name') else str(kw).lower()
            value += _KW_VALUE.get(kw_name, 0.0)

        # Equipment tags: the creature is carrying equipment, making it a
        # high-priority target.  We don't double-count the stats (already
        # in actual P/T) but note the vulnerability.
        if card.instance_tags:
            value += 2.0  # extra value because removing it also wastes equipment

    # --- Lands ---
    elif template.is_land:
        value += 1.0  # base land value
        if template.produces_mana:
            value += len(template.produces_mana) * 0.3
        if not card.tapped:
            value += 0.5  # untapped land = options

    # --- Artifacts (non-creature) ---
    elif CardType.ARTIFACT in template.card_types and not template.is_creature:
        if "mana_source" in template.tags or template.produces_mana:
            value += 2.0  # mana rock
        elif 'equipment' in template.tags:
            # Equipment: check if any creature carries an instance_tag
            # from this equipment (generic detection, no card-name lookup)
            equipped = any(
                c.instance_tags for c in controller.creatures
            ) if controller.creatures else False
            if equipped:
                value += 4.0  # actively boosting a creature
            else:
                value += 1.0  # sitting idle, needs equip
        else:
            value += template.cmc * 0.5  # generic artifact

    # --- Enchantments ---
    elif CardType.ENCHANTMENT in template.card_types:
        value += template.cmc * 0.8

    # --- Planeswalkers ---
    elif CardType.PLANESWALKER in template.card_types:
        value += 4.0 + (card.loyalty_counters or 0) * 0.5

    # --- Ability bonus (derived from mechanics, not card names) ---
    value += _ability_bonus(card, template)

    return value


