"""Archetype Strategy Profiles — data-driven AI decision weights.

Replaces hardcoded `if archetype == "aggro": +3.0` style branches
in ev_player.py with lookup tables. Each archetype defines numerical
weights for every decision dimension.

Adding a new archetype or tuning an existing one requires changing
DATA, not code.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StrategyProfile:
    """Numerical weights for all AI decision dimensions."""

    # ── Spell scoring weights ──
    creature_value_mult: float = 1.5     # how much to weight creature_value()
    removal_target_mult: float = 1.2     # how much to weight removal target value
    burn_face_mult: float = 0.5          # weight for face damage (non-lethal)
    burn_face_low_life_mult: float = 1.0 # weight for face damage when opp <= 10
    card_draw_base: float = 4.0          # base EV for draw spells
    card_draw_empty_hand_bonus: float = 4.0  # bonus when hand <= 2
    card_draw_low_hand_bonus: float = 2.0    # bonus when hand <= 4
    card_draw_archetype_bonus: float = 0.0   # extra draw value for this archetype
    ritual_bonus: float = 1.0            # EV bonus for ritual spells
    cost_reducer_pre_chain: float = 3.0  # EV for cost reducers before combo
    cost_reducer_mid_chain: float = 3.0  # EV for cost reducers during combo
    etb_value_bonus: float = 3.0         # ETB creature bonus
    planeswalker_bonus: float = 6.0      # planeswalker deployment bonus
    baseline_cast_bonus: float = 2.0     # baseline "casting is good" bonus
    zero_mana_combo_bonus: float = 0.0   # bonus for 0-mana spells (combo only)

    # ── Mana holdback ──
    holdback_penalty: float = -2.0       # penalty for tapping out with instants
    holdback_min_turn: int = 0           # don't apply holdback before this turn
    holdback_applies: bool = True        # does this archetype hold mana?

    # ── Combat thresholds ──
    attack_threshold: float = 0.0        # CombatPlanner score needed to attack
    empty_board_always_attack: bool = True  # attack into no blockers?

    # ── Survival mode ──
    survival_removal_bonus: float = 6.0
    survival_blocker_bonus: float = 5.0
    survival_wrath_bonus: float = 8.0

    # ── Archetype modifier weights ──
    on_curve_creature_bonus: float = 2.0
    cheap_creature_bonus: float = 0.0
    empty_board_creature_bonus: float = 0.0
    flash_creature_bonus: float = 2.0
    high_power_creature_bonus: float = 0.0  # bonus for power >= 3
    removal_vs_creatures_bonus: float = 4.0
    removal_vs_big_creatures_bonus: float = 3.0  # extra when opp power >= 4
    discard_early_bonus: float = 4.0     # T1-3 Thoughtseize
    discard_late_bonus: float = 2.0      # T4+ with cards in opp hand
    burn_low_life_bonus: float = 0.0     # extra burn when opp <= 10

    # ── Combo-specific ──
    chain_mid_bonus: float = 0.0         # bonus per spell at storm 3+
    chain_deep_bonus: float = 0.0        # bonus per spell at storm 6+
    cantrip_early_chain: float = 3.0     # cantrip value early in chain
    cantrip_late_chain: float = 3.0      # cantrip value late in chain
    ritual_early_chain: float = 2.0
    ritual_late_chain: float = 2.0
    planeswalker_mid_chain_penalty: float = 0.0  # penalty for PW during chain

    # ── Control phase-based (early T1-6, mid T7-12, late T13+) ──
    early_removal_bonus: float = 4.0
    early_cheap_play_bonus: float = 0.0
    early_planeswalker_bonus: float = 0.0
    mid_wrath_bonus: float = 5.0
    mid_payoff_bonus: float = 0.0
    mid_creature_bonus: float = 0.0
    late_creature_bonus: float = 0.0
    late_payoff_bonus: float = 0.0

    # ── Evoke ──
    evoke_base_penalty: float = -6.0
    evoke_min_target_value: float = 4.0
    evoke_pressure_bonus: float = 8.0
    evoke_lethal_bonus: float = 12.0


# ═══════════════════════════════════════════════════════════════════
# Archetype profiles
# ═══════════════════════════════════════════════════════════════════

AGGRO = StrategyProfile(
    burn_face_mult=1.5,
    burn_face_low_life_mult=2.5,
    burn_low_life_bonus=3.0,
    on_curve_creature_bonus=3.0,
    cheap_creature_bonus=2.0,
    empty_board_creature_bonus=3.0,
    holdback_applies=False,
    attack_threshold=-1.0,
    card_draw_archetype_bonus=0.0,
)

MIDRANGE = StrategyProfile(
    removal_vs_creatures_bonus=4.0,
    removal_vs_big_creatures_bonus=3.0,
    flash_creature_bonus=2.0,
    high_power_creature_bonus=0.0,
    cheap_creature_bonus=3.0,
    discard_early_bonus=4.0,
    card_draw_archetype_bonus=2.0,
    holdback_penalty=-2.0,
)

CONTROL = StrategyProfile(
    early_removal_bonus=6.0,
    early_cheap_play_bonus=3.0,
    early_planeswalker_bonus=4.0,
    mid_wrath_bonus=6.0,
    mid_payoff_bonus=6.0,
    mid_creature_bonus=3.0,
    late_creature_bonus=4.0,
    late_payoff_bonus=5.0,
    card_draw_archetype_bonus=2.0,
    holdback_penalty=-2.0,
)

COMBO = StrategyProfile(
    ritual_bonus=5.0,
    cost_reducer_pre_chain=12.0,
    cost_reducer_mid_chain=-5.0,
    zero_mana_combo_bonus=4.0,
    chain_mid_bonus=4.0,
    chain_deep_bonus=4.0,
    cantrip_early_chain=5.0,
    cantrip_late_chain=3.0,
    ritual_early_chain=2.0,
    ritual_late_chain=3.0,
    planeswalker_mid_chain_penalty=-8.0,
    holdback_applies=False,
    attack_threshold=0.0,
    card_draw_archetype_bonus=0.0,
)

RAMP = StrategyProfile(
    on_curve_creature_bonus=2.0,
    holdback_applies=False,
    # Ramp-specific: mana sources and fatties
)

TEMPO = StrategyProfile(
    flash_creature_bonus=3.0,
    removal_vs_creatures_bonus=3.0,
    holdback_penalty=-1.0,
)


PROFILES = {
    "aggro": AGGRO,
    "midrange": MIDRANGE,
    "control": CONTROL,
    "combo": COMBO,
    "ramp": RAMP,
    "tempo": TEMPO,
}


def get_profile(archetype: str) -> StrategyProfile:
    """Get the strategy profile for an archetype."""
    return PROFILES.get(archetype, MIDRANGE)
