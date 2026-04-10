"""Clock-Based Position Evaluation
===================================
Unified board evaluation based on game-theory principles.

Core idea: every game state is valued by the "clock differential" —
how many turns until I win minus how many turns until my opponent wins.
All creature values, keyword bonuses, and resource advantages are derived
from how they change this clock, not from arbitrary weights.

Units: turns of clock advantage. +1.0 means I'm one combat step ahead.
"""
from __future__ import annotations
import math
from typing import TYPE_CHECKING, Optional, Set

if TYPE_CHECKING:
    from ai.ev_evaluator import EVSnapshot
    from engine.cards import CardInstance

# Sentinel: no clock (no creatures / no win condition)
NO_CLOCK = 99.0


# ─────────────────────────────────────────────────────────────
# Clock arithmetic — turns to kill, derived from board state
# ─────────────────────────────────────────────────────────────

def combat_clock(power: int, opp_life: int,
                 evasion_power: int = 0,
                 opp_total_toughness: int = 0) -> float:
    """Turns to kill opponent via combat damage.

    Evasion power bypasses blockers entirely.  Non-evasive power must
    punch through blocker toughness (simplified: total toughness as a
    one-time wall, amortised per turn).

    Returns NO_CLOCK when there is no attack capability.
    """
    if power <= 0 or opp_life <= 0:
        return NO_CLOCK if power <= 0 else 1.0

    # Evasion damage lands every turn; ground damage is reduced by blockers
    # Simplified model: blockers absorb (total_toughness / 3) per turn on avg
    # (creatures regenerate via summoning sick replacements, ~3 turn cycle)
    blocker_absorption = opp_total_toughness / 3.0 if opp_total_toughness > 0 else 0
    ground_power = max(0, power - evasion_power)
    effective_ground = max(0, ground_power - blocker_absorption)
    effective_power = evasion_power + effective_ground

    if effective_power <= 0:
        return NO_CLOCK
    return max(1.0, math.ceil(opp_life / effective_power))


def life_as_resource(life: int, incoming_power: int) -> float:
    """Value of life total expressed as turns of survival.

    Low life is disproportionately valuable — going from 3→2 is much
    worse than 20→19 because it brings lethal closer.

    Returns a value where higher = more comfortable:
    - Dead: -100
    - 1 life with 5 power incoming: 0.2 turns (critical)
    - 20 life with 5 power incoming: 4.0 turns (comfortable)
    - 20 life with 0 power incoming: 10.0 (safe, life is a luxury)
    """
    if life <= 0:
        return -100.0
    if incoming_power <= 0:
        # No clock — life is a luxury resource, diminishing returns
        return min(10.0, life * 0.5)
    return life / incoming_power


# ─────────────────────────────────────────────────────────────
# Resource valuation — cards and mana as future clock changers
# ─────────────────────────────────────────────────────────────

def card_clock_impact(snap: "EVSnapshot") -> float:
    """How much one card in hand changes the clock (on average).

    Derived from game state: early game with mana to deploy = high impact.
    Late game with full board = diminishing impact.

    A card is worth roughly "average creature power / opponent life" turns,
    discounted by whether we have mana to cast it.
    """
    # Average creature in Modern: ~2.5 power, ~2.5 CMC
    avg_power = 2.5
    opp_life = max(1, snap.opp_life)
    base_impact = avg_power / opp_life  # ~0.125 at 20 life, ~0.5 at 5 life

    # Mana gating: cards are worth less if we can't cast them
    castable_fraction = min(1.0, snap.my_mana / 3.0) if snap.my_mana > 0 else 0.2

    return base_impact * castable_fraction


def mana_clock_impact(snap: "EVSnapshot") -> float:
    """How much one point of mana advantage changes the clock.

    Mana enables casting spells. Its value depends on what spells
    could be cast — approximated by game phase.
    """
    opp_life = max(1, snap.opp_life)
    # Mana → enables casting → roughly 1 power per mana spent → clock change
    return 1.0 / opp_life


# ─────────────────────────────────────────────────────────────
# Combo clock — turns until combo fires
# ─────────────────────────────────────────────────────────────

def combo_clock(snap: "EVSnapshot") -> float:
    """Turns until a combo deck can win.

    Based on storm count, hand size (fuel), mana, and graveyard
    (for reanimation combos).
    """
    # Mid-chain: storm count directly measures proximity to kill
    if snap.storm_count >= 10:
        return 1.0  # likely lethal this turn
    if snap.storm_count >= 5:
        return 1.0  # close to lethal

    # Pre-chain: estimate turns to assemble
    # Need: fuel in hand (2-3 cards), mana (2-3), and a finisher
    fuel_ready = min(snap.my_hand_size, 5)  # cap at 5 useful cards
    mana_ready = min(snap.my_mana, 5)

    # Reanimation combos: creature in GY is a key resource
    reanimate_ready = min(snap.my_gy_creatures, 2)

    # Rough estimate: turns = (resources needed - resources available)
    # A combo needs ~8 "resource points" (cards + mana + setup)
    resources = fuel_ready + mana_ready + reanimate_ready + snap.storm_count
    deficit = max(0, 8 - resources)

    if deficit == 0:
        return 1.0  # ready to go off
    return min(NO_CLOCK, 1.0 + deficit)


# ─────────────────────────────────────────────────────────────
# Creature clock impact — what one creature contributes
# ─────────────────────────────────────────────────────────────

def creature_clock_impact(power: int, toughness: int,
                          keywords: Set[str],
                          snap: "EVSnapshot") -> float:
    """Clock impact of a single creature on the battlefield.

    Replaces the old creature_value() which used arbitrary keyword weights.
    All values derived from how the creature changes the combat clock.
    """
    opp_life = max(1, snap.opp_life)
    if power <= 0 and not keywords:
        return toughness * 0.05  # pure blocker, tiny clock value

    # Base clock impact: fraction of kill per turn
    # A 3/3 vs 20 life = 0.15 turns per combat step
    base = power / opp_life

    # Keywords modify clock through game mechanics:

    # Flying/menace/trample: evasion bypasses blockers → power connects reliably
    # Without evasion, some damage is absorbed by blockers
    has_evasion = keywords & {"flying", "menace", "trample"}
    if has_evasion and snap.opp_creature_count > 0:
        # Evasion is worth more when opponent has blockers
        # Ground creatures lose ~30% damage to blockers on average
        base *= 1.3

    # Haste: immediate attack the turn it enters = one extra combat step
    if "haste" in keywords:
        base += power / opp_life

    # Lifelink: extends my survival clock
    if "lifelink" in keywords and snap.opp_power > 0:
        # Each attack gains life = extends survival by power/opp_power turns
        life_extension = power / max(1, snap.opp_power)
        base += life_extension * 0.5  # half weight (offensive + defensive)

    # Deathtouch: forces opponent to sacrifice a creature to block
    if "deathtouch" in keywords and snap.opp_creature_count > 0:
        # Effectively removes a blocker = improves ground clock
        avg_opp_power = snap.opp_power / max(1, snap.opp_creature_count)
        base += avg_opp_power / opp_life * 0.5

    # Double strike: effectively doubles power for clock
    if "double_strike" in keywords:
        base += power / opp_life

    # First strike: survives combat more often, preserving clock contribution
    if "first_strike" in keywords and snap.opp_creature_count > 0:
        base *= 1.15

    # Hexproof/indestructible: harder to remove = clock contribution persists
    if "hexproof" in keywords or "indestructible" in keywords:
        # Removal-proof creature's clock value is more reliable
        base *= 1.25

    # Vigilance: attacks without tapping = also blocks
    if "vigilance" in keywords and snap.opp_power > 0:
        block_value = min(toughness, snap.opp_power) / max(1, snap.my_life)
        base += block_value * 0.3

    # Reach: blocks flyers
    if "reach" in keywords and snap.opp_evasion_power > 0:
        base += min(toughness, snap.opp_evasion_power) / max(1, snap.my_life) * 0.3

    # Undying: dies and comes back = double the clock contribution
    if "undying" in keywords:
        base *= 1.5

    # Annihilator: forces opponent to sacrifice permanents
    if "annihilator" in keywords:
        # Devastating — removes opponent's clock AND resources
        base += snap.opp_creature_count * 0.3 / max(1, opp_life)
        base += 2.0 / opp_life  # sacrifice permanents

    # Prowess: gains +1/+0 per noncreature spell, estimate ~1 trigger/turn
    if "prowess" in keywords:
        base += 1.0 / opp_life

    # Cascade: casts a free spell = roughly another creature's worth
    if "cascade" in keywords:
        base += 2.5 / opp_life

    # Toughness as blocking value: absorbs opponent damage
    if toughness > 0 and snap.opp_power > 0:
        block_value = min(toughness, snap.opp_power) / max(1, snap.my_life)
        base += block_value * 0.15  # minor defensive contribution

    return base


def creature_clock_impact_from_card(card: "CardInstance",
                                     snap: "EVSnapshot") -> float:
    """Convenience: compute clock impact from a CardInstance."""
    t = card.template
    p = card.power if card.power else 0
    tough = card.toughness if card.toughness else 0
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(t, 'keywords', set())}

    base = creature_clock_impact(p, tough, kws, snap)

    # Tag-based abilities add value through game effects:
    tags = getattr(t, 'tags', set())
    opp_life = max(1, snap.opp_life)
    if "etb_value" in tags:
        base += 2.0 / opp_life  # ETB = extra effect worth ~2 damage
    if "card_advantage" in tags:
        base += card_clock_impact(snap)  # draws a card = future clock change
    if "token_maker" in tags:
        base += 1.5 / opp_life  # creates an extra body

    return base


# ─────────────────────────────────────────────────────────────
# Position value — the unified board evaluation
# ─────────────────────────────────────────────────────────────

def position_value(snap: "EVSnapshot", archetype: str = "midrange") -> float:
    """Unified board evaluation. Replaces 4 archetype-specific evaluators.

    Returns clock differential + resource advantage.
    Higher = better position for the player.
    """
    # Dead check
    if snap.my_life <= 0:
        return -100.0
    if snap.opp_life <= 0:
        return 100.0

    # Combat clocks
    my_clock = combat_clock(
        snap.my_power, snap.opp_life,
        snap.my_evasion_power, snap.opp_toughness
    )
    opp_clock = combat_clock(
        snap.opp_power, snap.my_life,
        snap.opp_evasion_power, snap.my_toughness
    )

    # Combo decks: override my_clock with combo-specific clock
    if archetype in ("combo", "storm"):
        combo_c = combo_clock(snap)
        # Use the faster of combat clock and combo clock
        my_clock = min(my_clock, combo_c)

    # Clock differential: positive = I'm winning the race
    clock_diff = opp_clock - my_clock

    # Normalize clock diff to prevent extreme values when one clock is NO_CLOCK
    if my_clock >= NO_CLOCK and opp_clock >= NO_CLOCK:
        clock_diff = 0.0  # neither player has a clock — stalled
    elif my_clock >= NO_CLOCK:
        # I have no clock, opponent does → I'm losing; worse as opp gets faster
        clock_diff = -opp_clock
    elif opp_clock >= NO_CLOCK:
        # Opponent has no clock, I do → I'm winning; better as I get faster
        # Invert: lower my_clock = bigger advantage
        clock_diff = 20.0 / my_clock  # cap near 20 when I have lethal

    # Resource advantage: cards and mana as future clock changes
    card_diff = snap.my_hand_size - snap.opp_hand_size
    card_value = card_clock_impact(snap) * card_diff

    mana_diff = snap.my_mana - snap.opp_mana
    mana_value = mana_clock_impact(snap) * max(0, mana_diff)

    # Survival margin: how comfortable is my life total?
    survival = life_as_resource(snap.my_life, snap.opp_power)
    opp_survival = life_as_resource(snap.opp_life, snap.my_power)
    life_advantage = survival - opp_survival

    # Lifelink: extends survival
    if snap.my_lifelink_power > 0 and snap.opp_power > 0:
        lifelink_turns = snap.my_lifelink_power / max(1, snap.opp_power)
        life_advantage += lifelink_turns * 0.3

    return clock_diff + card_value + mana_value + life_advantage
