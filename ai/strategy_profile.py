"""Archetype Strategy Profiles — data-driven AI decision weights.

Replaces hardcoded `if archetype == "aggro": +3.0` style branches
in ev_player.py with lookup tables. Each archetype defines numerical
weights for every decision dimension.

Adding a new archetype or tuning an existing one requires changing
DATA, not code.

Also contains ArchetypeStrategy enum and DECK_ARCHETYPES mapping
(moved from ai_player.py).

Generalization patterns used:
- storm_scaling(storm, base, per_storm) replaces 4-tier if/elif ladders
- draw_extra_card_mult replaces separate draw_two/draw_three bonuses
- chain_fuel_base + chain_fuel_late_mult replaces cantrip/ritual early/late pairs
- phase_weights dict replaces 8 separate early/mid/late fields
- reducer_curve(storm) replaces pre/early/mid chain + first/early reducer bonuses
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
    "Neobrand":           ArchetypeStrategy.COMBO,
    "Domain Zoo":         ArchetypeStrategy.AGGRO,
    "Living End":         ArchetypeStrategy.COMBO,
    "Belcher":            ArchetypeStrategy.COMBO,
    "Dimir Midrange":     ArchetypeStrategy.MIDRANGE,
    "Izzet Prowess":      ArchetypeStrategy.AGGRO,
    "4c Omnath":          ArchetypeStrategy.MIDRANGE,
    "4/5c Control":       ArchetypeStrategy.CONTROL,
    "Azorius Control":    ArchetypeStrategy.CONTROL,
}


@dataclass
class StrategyProfile:
    """Numerical weights for all AI decision dimensions.

    Methods provide derived values so callers don't need if/elif ladders.
    """

    # ── Spell scoring weights ──
    creature_value_mult: float = 1.5
    removal_target_mult: float = 1.2
    burn_face_mult: float = 0.5
    burn_face_low_life_mult: float = 1.0
    burn_kill_min_power: int = 4
    burn_kill_life_ratio: float = 2.0
    card_draw_base: float = 4.0
    card_draw_empty_hand_bonus: float = 4.0   # hand <= 2
    card_draw_low_hand_bonus: float = 2.0     # hand <= 4
    card_draw_archetype_bonus: float = 0.0
    draw_extra_card_mult: float = 3.5         # per extra card drawn (draw 2 = +3.5, draw 3 = +7.0)
    ritual_bonus: float = 1.0
    etb_value_bonus: float = 3.0
    planeswalker_bonus: float = 6.0
    enchantment_bonus: float = 2.0
    artifact_bonus: float = 2.0
    card_advantage_creature_bonus: float = 3.0
    evoke_small_target_penalty: float = -8.0
    baseline_cast_bonus: float = 2.0
    mana_efficiency_mult: float = 2.0         # was hardcoded 2.0 in mana efficiency calc

    haste_damage_mult: float = 1.0        # immediate attack value per power

    # ── Mana holdback ──
    holdback_penalty: float = -2.0
    holdback_min_turn: int = 0
    holdback_applies: bool = True
    holdback_opp_clock_threshold: int = 10  # disable holdback when opp has no clock
    holdback_min_remaining_mana: int = 2    # penalize if remaining mana < this

    # ── Combat thresholds ──
    attack_threshold: float = 0.0
    aggro_closing_threshold_reduction: float = 2.0  # lower threshold when opp at low life

    # ── Survival mode ──
    survival_removal_bonus: float = 6.0
    survival_blocker_bonus: float = 5.0
    survival_wrath_bonus: float = 8.0

    # ── Thresholds ──
    mulligan_always_keep: int = 5          # keep at this many cards or fewer
    mulligan_bad_land_count: int = 6       # mulligan with this many or more lands
    empty_hand_threshold: int = 2          # hand size <= this triggers empty hand bonus
    low_hand_threshold: int = 4            # hand size <= this triggers low hand bonus
    big_creature_power: int = 4            # power >= this = "big creature" for removal/burn
    high_power_threshold: int = 3          # power >= this for high_power_creature_bonus
    dying_opp_power: int = 3               # opp_power >= this + clock <= 3 = "dying"
    dying_opp_clock: int = 3               # (paired with above)
    burn_low_life_threshold: int = 10      # opp life <= this for burn bonuses
    evoke_pressure_life_buffer: int = 3    # evoke pressure when opp_power >= life - this
    discard_early_turns: int = 3           # T1-N = "early" for discard bonus
    discard_min_opp_hand: int = 3          # late discard only if opp has >= this cards
    cheap_creature_cmc: int = 2            # CMC <= this = "cheap creature"
    control_cheap_spell_cmc: int = 2       # CMC <= this = "cheap play" in control phases
    wrath_min_creatures: int = 2           # wrath bonus needs >= this many opp creatures
    removal_overkill_cmc_diff: int = 2     # penalty when removal CMC > target CMC + this
    tutor_fuel_storm_cap: int = 6          # only penalize tutor fuel when storm < this

    # ── Creature context bonuses ──
    on_curve_creature_bonus: float = 2.0
    cheap_creature_bonus: float = 0.0
    empty_board_creature_bonus: float = 0.0
    flash_creature_bonus: float = 2.0
    high_power_creature_bonus: float = 0.0
    removal_vs_creatures_bonus: float = 4.0
    removal_vs_big_creatures_bonus: float = 3.0
    discard_early_bonus: float = 4.0
    discard_late_bonus: float = 2.0
    burn_low_life_bonus: float = 0.0

    # ── Combo chain (generalized) ──
    has_combo_chain: bool = False
    chain_fuel_base: float = 3.0       # base value for cantrip/ritual in chain
    chain_fuel_late_mult: float = 1.0  # multiplier on fuel value at storm >= 4
    chain_ritual_mana_starved: float = 8.0  # ritual value when mana <= 2 and storm >= 3
    ritual_storm_scaling: float = 0.5  # extra ritual value per storm count
    chain_depth_bonus: float = 4.0     # bonus per depth tier (storm 3+ and 6+)
    pre_chain_planeswalker_bonus: float = 0.0
    planeswalker_mid_chain_penalty: float = 0.0
    zero_mana_combo_bonus: float = 0.0

    # ── Storm patience (hold fuel until ready to go off) ──
    storm_patience: bool = False        # enable hold-fuel-until-ready logic
    storm_hold_penalty: float = -15.0   # penalty for casting ritual at storm=0 when NOT ready
    storm_go_off_bonus: float = 15.0    # bonus for starting the chain when ready to go lethal
    storm_min_fuel_to_go: int = 2       # minimum ritual/fuel cards in hand to consider going off (with reducer)
    storm_cantrip_while_waiting: float = 3.0  # cantrips are OK to cast while waiting (dig)
    storm_cantrip_vs_bowmasters: float = -3.0  # reduced cantrip value when opp has draw-punishers

    # ── Storm scaling (generalized) ──
    # storm_bonus(storm) = base + storm * per_storm, capped at cap
    tutor_base: float = 6.0
    tutor_storm_per: float = 2.0       # EV per storm count for tutors
    tutor_storm_cap: float = 20.0      # max storm bonus for tutors
    tutor_fuel_penalty_mult: float = -3.0

    finisher_hold_penalty: float = -20.0
    finisher_lethal_pct: float = 0.7       # damage_pct >= this → fire
    finisher_decent_pct: float = 0.4       # damage_pct >= this → decent
    finisher_high_bonus: float = 15.0      # bonus when >= lethal_pct
    finisher_mid_bonus: float = 5.0        # bonus when >= decent_pct
    finisher_low_penalty: float = -30.0    # penalty when below decent_pct
    lethal_burn_bonus: float = 100.0
    lethal_storm_bonus: float = 100.0

    # ── Past in Flames ──
    pif_gy_fuel_mult: float = 1.5      # EV per GY fuel card (rituals weighted 1.33x)
    pif_ritual_weight: float = 1.33    # ritual multiplier vs cantrips
    pif_redundant_penalty: float = -30.0
    pif_empty_gy_penalty: float = -20.0    # penalty when GY has < 2 instants/sorceries
    pif_wait_for_rituals_penalty: float = -5.0  # delay PiF when hand still has 2+ rituals to cast first
    pif_no_mana_penalty: float = -15.0   # penalty when not enough mana left to replay spells after PiF

    # ── Cost reducer (generalized curve) ──
    # reducer_ev(storm, fuel) computed by method
    reducer_base: float = 3.0          # base value when deployed
    reducer_combo_mult: float = 4.0    # multiplier for combo archetypes (pre-chain)
    reducer_first_bonus: float = 4.0   # first reducer on board
    reducer_early_turn_bonus: float = 6.0  # deployed T1-4
    reducer_no_fuel_mult: float = 0.3  # multiplier when no fuel in hand
    reducer_mid_chain: float = -5.0    # value during active chain (storm >= 5)
    reducer_early_chain: float = 4.0   # value at storm 1-4

    # ── Gameplan role bonuses ──
    payoff_bonus: float = 6.0
    engine_bonus: float = 5.0
    fuel_bonus: float = 3.0

    # ── Land scoring ──
    land_base_ev: float = 10.0
    land_untapped_castable_bonus: float = 5.0
    land_untapped_base_bonus: float = 2.0
    land_tapped_castable_penalty: float = -3.0
    land_new_color_bonus: float = 4.0
    land_fetch_bonus: float = 3.0
    land_landfall_trigger_value: float = 3.0
    land_landfall_defer_penalty: float = -12.0  # defer land when landfall creature is castable

    # ── Cycling (Living End etc.) ──
    cycling_creature_gy_value: float = 4.0     # creature in GY for reanimation
    cycling_power_scaling: float = 0.5         # per power point in GY
    cycling_life_pay_bonus: float = 2.0        # free cycling (pay life)
    cycling_cheap_bonus: float = 1.0           # mana cost <= 1
    cycling_cascade_ready_bonus: float = 3.0   # GY filling when cascade in hand

    # ── Pump (Psychic Frog etc.) ──
    pump_uncastable_cmc_buffer: int = 2        # discard cards with CMC > lands + this
    pump_extra_lands_threshold: int = 5        # discard extra lands when lands >= this
    pump_max_discards: int = 2                 # max cards to discard per pump

    # ── Wrath ──
    wrath_empty_board_penalty: float = -15.0
    wrath_multi_kill_bonus: float = 5.0

    # ── Pass threshold ──
    pass_threshold: float = -5.0

    # ── Scoring constants ──
    evoke_empty_board_penalty: float = -20.0
    removal_no_target_penalty: float = -3.0
    removal_overkill_mult: float = 0.6

    # ── Control phases (generalized) ──
    # phase_bonus(turn, card_role) uses phase_weights dict
    has_control_phases: bool = False
    # (removal, cheap_play, planeswalker, wrath, payoff, creature) per phase
    # Phase boundaries: early <= 6, mid <= 12, late > 12
    phase_early: Tuple[float, ...] = (4.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    phase_mid: Tuple[float, ...] = (0.0, 0.0, 0.0, 5.0, 0.0, 0.0)
    phase_late: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    # ── Evoke ──
    evoke_base_penalty: float = -6.0
    evoke_min_target_value: float = 4.0
    evoke_pressure_bonus: float = 8.0
    evoke_lethal_bonus: float = 12.0

    # ── Derived methods ──

    def tutor_storm_bonus(self, storm: int) -> float:
        """Storm-scaled tutor bonus: linear scaling with cap."""
        return min(storm * self.tutor_storm_per, self.tutor_storm_cap)

    def chain_fuel_value(self, storm: int) -> float:
        """Value of a fuel card (cantrip/ritual) at given storm count."""
        if storm <= 3:
            return self.chain_fuel_base
        return self.chain_fuel_base * self.chain_fuel_late_mult

    def chain_depth_value(self, storm: int) -> float:
        """Cumulative bonus for chain depth."""
        val = 0.0
        if storm >= 3:
            val += self.chain_depth_bonus
        if storm >= 6:
            val += self.chain_depth_bonus
        return val

    def draw_multi_bonus(self, extra_cards: int) -> float:
        """Bonus for drawing multiple cards. extra_cards = total drawn - 1."""
        return max(0, extra_cards) * self.draw_extra_card_mult

    def reducer_ev(self, storm: int, fuel_count: int,
                   on_board: int, turn: int) -> float:
        """Cost reducer EV based on game state."""
        if storm >= 5:
            return self.reducer_mid_chain
        if storm >= 1:
            return self.reducer_early_chain
        # Pre-chain
        base = self.reducer_base * self.reducer_combo_mult
        if fuel_count < 2:
            return base * self.reducer_no_fuel_mult
        ev = base
        if turn <= 4:
            ev += self.reducer_early_turn_bonus
        if on_board == 0:
            ev += self.reducer_first_bonus
        return ev

    def pif_gy_value(self, gy_rituals: int, gy_cantrips: int) -> float:
        """Past in Flames bonus from GY fuel."""
        return (gy_rituals * self.pif_ritual_weight +
                gy_cantrips) * self.pif_gy_fuel_mult

    def finisher_ev(self, damage_pct: float) -> float:
        """Storm finisher EV based on damage percentage of opponent's life."""
        if damage_pct >= self.finisher_lethal_pct:
            return self.finisher_high_bonus
        if damage_pct >= self.finisher_decent_pct:
            return self.finisher_mid_bonus
        return self.finisher_low_penalty

    def phase_bonus(self, turn: int, role_idx: int) -> float:
        """Control phase bonus. role_idx: 0=removal, 1=cheap, 2=pw, 3=wrath, 4=payoff, 5=creature."""
        if turn <= 6:
            return self.phase_early[role_idx] if role_idx < len(self.phase_early) else 0.0
        if turn <= 12:
            return self.phase_mid[role_idx] if role_idx < len(self.phase_mid) else 0.0
        return self.phase_late[role_idx] if role_idx < len(self.phase_late) else 0.0


# ═══════════════════════════════════════════════════════════════════
# Archetype profiles — only override what differs from defaults
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
    high_power_creature_bonus=3.0,     # Murktide gets priority
    on_curve_creature_bonus=3.0,       # deploy on curve
    cheap_creature_bonus=3.0,
    discard_early_bonus=5.0,           # T1 Thoughtseize more urgent
    card_draw_archetype_bonus=2.0,
    holdback_penalty=-2.0,
    survival_removal_bonus=7.0,
    survival_blocker_bonus=6.0,
    pass_threshold=-3.0,
)

CONTROL = StrategyProfile(
    has_control_phases=True,
    phase_early=(6.0, 3.0, 4.0, 0.0, 0.0, 0.0),
    phase_mid=(0.0, 0.0, 0.0, 6.0, 6.0, 3.0),
    phase_late=(0.0, 0.0, 0.0, 0.0, 5.0, 4.0),
    card_draw_archetype_bonus=2.0,
    holdback_penalty=-2.0,
    burn_face_mult=0.0,  # Control saves burn spells as removal, not face damage
)

COMBO = StrategyProfile(
    has_combo_chain=True,
    ritual_bonus=5.0,
    reducer_base=3.0,
    reducer_combo_mult=4.0,
    reducer_mid_chain=-5.0,
    reducer_early_chain=4.0,
    reducer_early_turn_bonus=6.0,
    reducer_first_bonus=4.0,
    pre_chain_planeswalker_bonus=4.0,
    zero_mana_combo_bonus=4.0,
    chain_fuel_base=5.0,
    chain_fuel_late_mult=0.6,
    chain_depth_bonus=4.0,
    planeswalker_mid_chain_penalty=-8.0,
    holdback_applies=False,
    attack_threshold=0.0,
    # Storm patience OFF by default for combo — only Ruby Storm enables it
    # (Goryo's, Amulet Titan, Living End don't use storm count)
    storm_patience=False,
    card_draw_archetype_bonus=0.0,
)

# Storm-specific override: Ruby Storm uses storm_patience
STORM = StrategyProfile(
    has_combo_chain=True,
    ritual_bonus=5.0,
    reducer_base=3.0,
    reducer_combo_mult=4.0,
    reducer_mid_chain=-5.0,
    reducer_early_chain=4.0,
    reducer_early_turn_bonus=6.0,
    reducer_first_bonus=4.0,
    pre_chain_planeswalker_bonus=4.0,
    zero_mana_combo_bonus=4.0,
    chain_fuel_base=5.0,
    chain_fuel_late_mult=0.6,
    chain_depth_bonus=4.0,
    planeswalker_mid_chain_penalty=-8.0,
    holdback_applies=False,
    attack_threshold=0.0,
    storm_patience=True,
    storm_hold_penalty=-15.0,
    storm_go_off_bonus=15.0,
    storm_min_fuel_to_go=2,
    storm_cantrip_while_waiting=3.0,
    storm_cantrip_vs_bowmasters=-3.0,
    card_draw_archetype_bonus=0.0,
)

RAMP = StrategyProfile(
    on_curve_creature_bonus=2.0,
    holdback_applies=False,
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
