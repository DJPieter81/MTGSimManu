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

from ai.scoring_constants import (
    PURE_BLOCKER_TOUGHNESS_VALUE,
    EVASION_VS_BLOCKERS_MULTIPLIER,
    FIRST_STRIKE_SURVIVAL_MULTIPLIER,
    REMOVAL_RESISTANT_MULTIPLIER,
    UNDYING_RECURSION_MULTIPLIER,
    KEYWORD_HALF_WEIGHT,
    KEYWORD_MINOR_WEIGHT,
    TOUGHNESS_DEFENSIVE_WEIGHT,
    ANNIHILATOR_CHIP_PER_OPP_CREATURE,
    ANNIHILATOR_BASE_SAC,
    PROWESS_TRIGGER_PER_TURN,
    CASCADE_FREE_SPELL_VALUE,
    ETB_VALUE_BONUS,
    TOKEN_MAKER_BONUS,
    AVG_CREATURE_POWER,
)

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
    # Cap at no-threat value so deploying small creatures never makes
    # opponent's survival score INCREASE (was: 1-power → 20.0 > no-threat 10.0)
    no_threat_cap = min(10.0, life * 0.5)
    return min(no_threat_cap, life / incoming_power)


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
    # AVG_CREATURE_POWER is the centralized "average Modern creature"
    # baseline (~2.5 power, ~2.5 CMC), shared with cascade/token bonuses.
    opp_life = max(1, snap.opp_life)
    base_impact = AVG_CREATURE_POWER / opp_life  # ~0.125 at 20 life, ~0.5 at 5 life

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
# Game-phase predicate — derived from clock state, not turn count
# ─────────────────────────────────────────────────────────────

EARLY_GAME_CLOCK_THRESHOLD: float = 4.0
"""Derived: number of remaining turns of clock above which the game is
classified as "early."

Symmetric across both players via min(my_clock, opp_clock) — the game
is early iff *neither* side is within striking distance. Replaces the
hard-coded `turn_number <= 4` heuristic in bhi.py / evaluator.py /
gameplan.py — those checks ignored board state entirely, so a fast
aggro deck on T2 with lethal in 2 still triggered "early game" hold
rates and bonuses, mis-modelling the actual decision.

Threshold of 4 chosen to match the original turn-counter heuristic on
average game pacing (early-game discard / hold-rate decisions kicked
in through ~T4 of a typical Modern game). Past 4 turns of clock on
both sides, the average Modern board has resolved or is about to.
"""


def is_early_game(snap: "EVSnapshot") -> bool:
    """Early-game predicate, derived from board state instead of turn count.

    Returns True iff *both* sides' clocks exceed
    `EARLY_GAME_CLOCK_THRESHOLD` turns. A fast deck on a slow board
    is correctly classified as mid-game by T2 once the clock collapses;
    a Tron-style board with no creatures stays in "early game" past T6
    if neither side is pressuring.

    Uses the existing `EVSnapshot.my_clock` / `opp_clock` properties
    (continuous turns-to-lethal) — no separate clock primitive needed.
    """
    return min(snap.my_clock, snap.opp_clock) > EARLY_GAME_CLOCK_THRESHOLD


# ─────────────────────────────────────────────────────────────
# Combo clock — turns until combo fires
# ─────────────────────────────────────────────────────────────

# Per-archetype-subtype resource-assembly target.  The default (Storm /
# Amulet Titan / generic combo) needs ~8 "resource points" — 2-3 cards
# of fuel, 2-3 mana, and engine setup — before it can fire.  Cascade-
# reanimator decks (Living End et al.) have a cheaper win condition: a
# single 3-mana cascade spell + ~3 graveyard creatures resolves the
# combo in one shot.  That is ~6 resource points, not 8.
#
# Subtype strings are loaded from the gameplan JSON
# (`archetype_subtype` field) and plumbed through via
# `EVSnapshot.archetype_subtype`.  New subtypes register in this table;
# we do NOT branch on card or deck names.
_COMBO_ASSEMBLY_TARGET = {
    "storm": 8,                 # Ruby Storm — 8 resource points (default)
    "cascade_reanimator": 6,    # Living End style — cascade + GY fuel
}
_COMBO_ASSEMBLY_DEFAULT = 8     # fallback when archetype_subtype is missing
                                # (Amulet Titan, Goryo's Vengeance, etc.)


def combo_clock(snap: "EVSnapshot") -> float:
    """Turns until a combo deck can win.

    Based on storm count, hand size (fuel), mana, and graveyard
    (for reanimation combos).

    The resource-assembly target varies by archetype sub-type so that
    cheaper combos (cascade-reanimator: 3 mana + ~3 GY creatures +
    cascade spell) are not under-estimated as slow 8-resource Storm
    plans.  See `_COMBO_ASSEMBLY_TARGET`.
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

    # Resource-assembly target, archetype-routed.  Unknown / missing
    # subtype → default 8-resource model (Storm-parity, regression-safe
    # for Amulet Titan / Goryo's Vengeance).
    subtype = getattr(snap, "archetype_subtype", None)
    needed = _COMBO_ASSEMBLY_TARGET.get(subtype, _COMBO_ASSEMBLY_DEFAULT)

    # Rough estimate: turns = (resources needed - resources available)
    resources = fuel_ready + mana_ready + reanimate_ready + snap.storm_count
    deficit = max(0, needed - resources)

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
        return toughness * PURE_BLOCKER_TOUGHNESS_VALUE

    # Base clock impact: fraction of kill per turn.
    # A 3/3 vs 20 life = 0.15 turns per combat step.
    base = power / opp_life

    # Flying / menace / trample bypass blockers; multiplier from
    # EVASION_VS_BLOCKERS_MULTIPLIER (ground attackers lose ~30%).
    has_evasion = keywords & {"flying", "menace", "trample"}
    if has_evasion and snap.opp_creature_count > 0:
        base *= EVASION_VS_BLOCKERS_MULTIPLIER

    # Haste: immediate attack the turn it enters = one extra combat step.
    if "haste" in keywords:
        base += power / opp_life

    # Lifelink: each attack gains life = extends survival by
    # power/opp_power turns; weighted by KEYWORD_HALF_WEIGHT
    # (offensive + defensive contributions partially redundant).
    if "lifelink" in keywords and snap.opp_power > 0:
        life_extension = power / max(1, snap.opp_power)
        base += life_extension * KEYWORD_HALF_WEIGHT

    # Deathtouch: effectively removes a blocker = improves ground clock,
    # weighted by KEYWORD_HALF_WEIGHT (blocker can be re-deployed).
    if "deathtouch" in keywords and snap.opp_creature_count > 0:
        avg_opp_power = snap.opp_power / max(1, snap.opp_creature_count)
        base += avg_opp_power / opp_life * KEYWORD_HALF_WEIGHT

    # Double strike: effectively doubles power for clock.
    if "double_strike" in keywords:
        base += power / opp_life

    # First strike: survives combat more often, preserving clock.
    if "first_strike" in keywords and snap.opp_creature_count > 0:
        base *= FIRST_STRIKE_SURVIVAL_MULTIPLIER

    # Hexproof / indestructible: removal-proof clock is more reliable.
    if "hexproof" in keywords or "indestructible" in keywords:
        base *= REMOVAL_RESISTANT_MULTIPLIER

    # Vigilance: attacks without tapping = also blocks; defensive
    # contribution at KEYWORD_MINOR_WEIGHT (offensive clock dominates).
    if "vigilance" in keywords and snap.opp_power > 0:
        block_value = min(toughness, snap.opp_power) / max(1, snap.my_life)
        base += block_value * KEYWORD_MINOR_WEIGHT

    # Reach: blocks flyers; same minor defensive bracket as vigilance.
    if "reach" in keywords and snap.opp_evasion_power > 0:
        base += min(toughness, snap.opp_evasion_power) / max(1, snap.my_life) * KEYWORD_MINOR_WEIGHT

    # Undying: dies and comes back = ~1.5× clock contribution.
    if "undying" in keywords:
        base *= UNDYING_RECURSION_MULTIPLIER

    # Annihilator: forced sacrifice — board chip + per-trigger sac.
    if "annihilator" in keywords:
        base += snap.opp_creature_count * ANNIHILATOR_CHIP_PER_OPP_CREATURE / max(1, opp_life)
        base += ANNIHILATOR_BASE_SAC / opp_life

    # Prowess: ~1 noncreature spell per turn = ~1 trigger.
    if "prowess" in keywords:
        base += PROWESS_TRIGGER_PER_TURN / opp_life

    # Cascade: free spell of CMC < caster ≈ another small creature.
    if "cascade" in keywords:
        base += CASCADE_FREE_SPELL_VALUE / opp_life

    # Implicit toughness blocking value (no keyword required).
    if toughness > 0 and snap.opp_power > 0:
        block_value = min(toughness, snap.opp_power) / max(1, snap.my_life)
        base += block_value * TOUGHNESS_DEFENSIVE_WEIGHT

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
        base += ETB_VALUE_BONUS / opp_life
    if "card_advantage" in tags:
        base += card_clock_impact(snap)  # draws a card = future clock change
    if "token_maker" in tags:
        base += TOKEN_MAKER_BONUS / opp_life

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

    # Combat clocks — use on-board power only. Persistent (recurring-
    # trigger) tokens are credited further below as an additive
    # position-value term, NOT through combat_clock. Clock is
    # nonlinear in power (opp_life / power), so stuffing expected-
    # future tokens into it compounds the bonus — see
    # docs/proposals/recurring_token_ev.md §5 risk note.
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

    # Persistent (recurring-trigger token) power contribution.
    # Expected damage = persistent_power × urgency_factor (fraction of
    # residency we actually survive). Converted to life-point units via
    # mana_clock_impact × 20 — same scale the clock_diff / card_value /
    # mana_value terms use. No clock non-linearity; linear additive.
    persistent_value = (snap.persistent_power * snap.urgency_factor
                        * mana_clock_impact(snap) * 20.0)

    # Artifact-count resource (design: docs/design/ev_correctness_overhaul.md §4).
    # Each artifact is worth roughly +1 virtual power to decks that
    # actually scale with artifact count: "+1/+0 per artifact" equipment,
    # affinity cost reduction, metalcraft activation.  Gated on the
    # scaling_active flag so Zoo / Burn / generic decks never accrue
    # this bonus.  Differential formulation (my − opp) mirrors clock_diff.
    artifact_diff = 0
    if snap.my_artifact_scaling_active:
        artifact_diff += snap.my_artifact_count
    if snap.opp_artifact_scaling_active:
        artifact_diff -= snap.opp_artifact_count
    # Convert each marginal artifact into life-point units via the same
    # mana_clock_impact × 20 scaling used by card_value / mana_value — a
    # rules-derived "value per power point" rather than a tuning constant.
    artifact_value = artifact_diff * mana_clock_impact(snap) * 20.0

    return (clock_diff + card_value + mana_value + life_advantage
            + persistent_value + artifact_value)
