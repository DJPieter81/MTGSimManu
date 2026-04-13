"""Archetype Strategy Profiles — streamlined AI decision parameters.

Most AI scoring is now clock-based (ai/clock.py + projection in ev_evaluator.py).
StrategyProfile retains only the parameters that can't be derived from
game mechanics: combo sequencing flags, burn targeting preferences,
land scoring weights, cycling/pump config, and pass thresholds.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple


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
    "Domain Zoo":         ArchetypeStrategy.AGGRO,
    "Living End":         ArchetypeStrategy.COMBO,
    "Dimir Midrange":     ArchetypeStrategy.MIDRANGE,
    "Izzet Prowess":      ArchetypeStrategy.AGGRO,
    "4c Omnath":          ArchetypeStrategy.MIDRANGE,
    "4/5c Control":       ArchetypeStrategy.CONTROL,
    "Azorius Control":    ArchetypeStrategy.CONTROL,
    "Azorius Control (WST)": ArchetypeStrategy.CONTROL,
    "Pinnacle Affinity":  ArchetypeStrategy.AGGRO,
}


@dataclass
class StrategyProfile:
    """AI decision parameters that can't be derived from clock mechanics.

    Clock-based projection handles creature/removal/burn/draw scoring.
    What remains: combo sequencing, burn mode, land/cycling config,
    combat thresholds, and mulligan rules.
    """

    # ── Burn targeting ──
    burn_face_mult: float = 0.5           # face burn value multiplier (0 for control)
    burn_face_low_life_mult: float = 1.0  # multiplier when opp is low
    burn_kill_min_power: int = 4          # min power to prefer killing over face
    burn_kill_life_ratio: float = 2.0     # kill if life > damage × this
    burn_low_life_threshold: int = 10     # opp life ≤ this for burn bonuses

    # ── Mana holdback ──
    holdback_applies: bool = True         # whether to hold mana for instants

    # ── Combat ──
    attack_threshold: float = -0.5        # slightly negative: attack when trades are close
    aggro_closing_threshold_reduction: float = 2.0
    # Opp won't block with creatures whose power exceeds attacker.power * this ratio
    # (trading up forfeits offensive value the bigger creature would generate).
    block_threat_power_ratio: float = 2.0

    # ── Mulligan ──
    mulligan_always_keep: int = 5
    mulligan_bad_land_count: int = 6

    # ── Board assessment thresholds ──
    big_creature_power: int = 4           # "big creature" for removal priority
    dying_opp_power: int = 3              # opp_power >= this + clock ≤ 3 = dying
    dying_opp_clock: int = 3

    # ── Removal ──
    removal_overkill_cmc_diff: int = 2
    removal_overkill_mult: float = 0.6

    # ── Combo chain ──
    has_combo_chain: bool = False
    storm_patience: bool = False
    storm_min_fuel_to_go: int = 2

    # ── Burn target comparison ──
    creature_value_mult: float = 1.5

    # ── Pump (Psychic Frog etc.) ──
    pump_uncastable_cmc_buffer: int = 2
    pump_extra_lands_threshold: int = 5
    pump_max_discards: int = 2

    # ── Pass threshold ──
    pass_threshold: float = -5.0


# ═══════════════════════════════════════════════════════════════════
# Archetype profiles — only override what differs from defaults
# ═══════════════════════════════════════════════════════════════════

AGGRO = StrategyProfile(
    burn_face_mult=1.5,
    burn_face_low_life_mult=2.5,
    holdback_applies=False,
    attack_threshold=-1.0,
)

MIDRANGE = StrategyProfile(
    pass_threshold=-3.0,
)

CONTROL = StrategyProfile(
    burn_face_mult=0.0,
)

COMBO = StrategyProfile(
    has_combo_chain=True,
    holdback_applies=False,
)

STORM = StrategyProfile(
    has_combo_chain=True,
    storm_patience=True,
    storm_min_fuel_to_go=2,
    holdback_applies=False,
)

RAMP = StrategyProfile(
    holdback_applies=False,
)

TEMPO = StrategyProfile()


PROFILES = {
    "aggro": AGGRO,
    "midrange": MIDRANGE,
    "control": CONTROL,
    "combo": COMBO,
    "storm": STORM,
    "ramp": RAMP,
    "tempo": TEMPO,
}

# Per-deck archetype overrides (deck_name -> archetype key in PROFILES)
DECK_ARCHETYPE_OVERRIDES = {
    "Ruby Storm": "storm",
}


def get_profile(archetype: str) -> StrategyProfile:
    """Get the strategy profile for an archetype."""
    return PROFILES.get(archetype, MIDRANGE)
