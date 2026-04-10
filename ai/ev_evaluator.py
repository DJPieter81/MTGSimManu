"""EV-Based Board Evaluator — per-archetype value functions.

Core idea: each archetype has a different VALUE FUNCTION that scores
a board state. The decision loop evaluates each candidate play by
projecting the resulting board state and scoring it with the
archetype's value function.

No hardcoded thresholds. All decisions are EV comparisons:
  "Is the projected state after casting X better than the current state?"

Value is measured in "life-point equivalents" — +1.0 means roughly
being 1 life ahead in an otherwise equal position.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.game_state import GameState, PlayerState
    from engine.cards import CardInstance, CardTemplate

from ai.deck_knowledge import DeckKnowledge


# ─────────────────────────────────────────────────────────────
# Board snapshot — lightweight representation for EV calculation
# ─────────────────────────────────────────────────────────────

@dataclass
class EVSnapshot:
    """Lightweight board snapshot for EV calculations.

    All values are derived from game state — no hardcoded defaults.
    """
    my_life: int = 20
    opp_life: int = 20
    my_power: int = 0          # total power of my creatures
    opp_power: int = 0         # total power of opp creatures
    my_toughness: int = 0      # total toughness of my creatures
    opp_toughness: int = 0
    my_creature_count: int = 0
    opp_creature_count: int = 0
    my_hand_size: int = 0
    opp_hand_size: int = 0
    my_mana: int = 0           # untapped mana sources
    opp_mana: int = 0
    my_total_lands: int = 0
    opp_total_lands: int = 0
    turn_number: int = 1
    storm_count: int = 0
    my_gy_creatures: int = 0   # creatures in graveyard (for Living End, etc.)
    my_energy: int = 0
    # Keyword counts on my board
    my_evasion_power: int = 0  # power of creatures with flying/menace/trample
    my_lifelink_power: int = 0
    opp_evasion_power: int = 0
    # Cards drawn this turn
    cards_drawn_this_turn: int = 0

    @property
    def my_clock(self) -> float:
        """Turns until I kill opponent (lower = better for me)."""
        if self.my_power <= 0:
            return 99.0
        return max(1.0, math.ceil(self.opp_life / self.my_power))

    @property
    def opp_clock(self) -> float:
        """Turns until opponent kills me (lower = worse for me)."""
        if self.opp_power <= 0:
            return 99.0
        return max(1.0, math.ceil(self.my_life / self.opp_power))

    @property
    def has_lethal(self) -> bool:
        return self.my_power >= self.opp_life > 0

    @property
    def am_dead_next(self) -> bool:
        return self.opp_power >= self.my_life > 0


def snapshot_from_game(game: "GameState", player_idx: int) -> EVSnapshot:
    """Create an EVSnapshot from the live game state."""
    me = game.players[player_idx]
    opp = game.players[1 - player_idx]

    snap = EVSnapshot(
        my_life=me.life,
        opp_life=opp.life,
        my_hand_size=len(me.hand),
        opp_hand_size=len(opp.hand),
        my_mana=me.available_mana_estimate + me.mana_pool.total(),
        opp_mana=opp.available_mana_estimate,
        my_total_lands=len(me.lands),
        opp_total_lands=len(opp.lands),
        turn_number=game.turn_number,
        storm_count=me.spells_cast_this_turn,
        my_gy_creatures=sum(1 for c in me.graveyard if c.template.is_creature),
        my_energy=me.energy_counters,
        cards_drawn_this_turn=me.cards_drawn_this_turn,
    )

    for c in me.creatures:
        p = c.power if c.power else 0
        t = c.toughness if c.toughness else 0
        snap.my_power += max(0, p)
        snap.my_toughness += max(0, t)
        snap.my_creature_count += 1
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(c.template, 'keywords', set())}
        if kws & {'flying', 'menace', 'trample'}:
            snap.my_evasion_power += max(0, p)
        if 'lifelink' in kws:
            snap.my_lifelink_power += max(0, p)

    for c in opp.creatures:
        p = c.power if c.power else 0
        t = c.toughness if c.toughness else 0
        snap.opp_power += max(0, p)
        snap.opp_toughness += max(0, t)
        snap.opp_creature_count += 1
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(c.template, 'keywords', set())}
        if kws & {'flying', 'menace', 'trample'}:
            snap.opp_evasion_power += max(0, p)

    return snap


# ─────────────────────────────────────────────────────────────
# Life valuation — non-linear (low life is worth more)
# ─────────────────────────────────────────────────────────────

def _life_value(life: int) -> float:
    """Non-linear life valuation. Going from 3->2 is much worse than 20->19."""
    if life <= 0:
        return -100.0
    if life <= 3:
        return life * 4.0      # 12.0 at 3 life
    if life <= 7:
        return 12.0 + (life - 3) * 2.5  # 22.0 at 7 life
    if life <= 15:
        return 22.0 + (life - 7) * 1.0  # 30.0 at 15 life
    return 30.0 + (life - 15) * 0.3


# ─────────────────────────────────────────────────────────────
# Creature value — what a creature is worth on the battlefield
# ─────────────────────────────────────────────────────────────

def creature_value(card: "CardInstance") -> float:
    """Evaluate a creature's worth on the battlefield.

    Based on P/T, keywords, and abilities. Returns life-point equivalents.
    All weights come from ai/constants.py (KEYWORD_BONUSES, TAG_BONUSES).
    """
    from ai.constants import (
        CREATURE_POWER_MULT, CREATURE_TOUGHNESS_MULT,
        KEYWORD_BONUSES, TAG_BONUSES,
    )
    t = card.template
    p = card.power if card.power else 0
    tough = card.toughness if card.toughness else 0

    # Base: power + fractional toughness
    val = max(0, p) * CREATURE_POWER_MULT + max(0, tough) * CREATURE_TOUGHNESS_MULT

    # Keyword bonuses from constants dict
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(t, 'keywords', set())}
    for kw in kws:
        if kw in KEYWORD_BONUSES:
            val += KEYWORD_BONUSES[kw]
    # Special per-power keywords
    if 'lifelink' in kws:
        val += min(p, KEYWORD_BONUSES.get("lifelink_power_cap", 5)) * KEYWORD_BONUSES.get("lifelink_per_power", 0.5)
    if 'double_strike' in kws:
        val += max(0, p) * KEYWORD_BONUSES.get("double_strike_per_power", 1.0)

    # Tag-based ability bonuses from constants dict
    tags = getattr(t, 'tags', set())
    for tag, bonus in TAG_BONUSES.items():
        if tag in tags:
            val += bonus

    return val


# ─────────────────────────────────────────────────────────────
# Per-archetype value functions
# ─────────────────────────────────────────────────────────────

def evaluate_board_aggro(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Aggro value function. All weights from ai/constants.py."""
    from ai.constants import (
        AGGRO_DAMAGE_BONUS, AGGRO_MY_POWER_MULT, AGGRO_OPP_POWER_MULT,
        AGGRO_EVASION_BONUS, AGGRO_HAND_BONUS, AGGRO_LIFELINK_BONUS,
    )
    clock_advantage = (snap.opp_clock - snap.my_clock) * AGGRO_DAMAGE_BONUS
    board_power = snap.my_power * AGGRO_MY_POWER_MULT - snap.opp_power * AGGRO_OPP_POWER_MULT
    evasion_bonus = snap.my_evasion_power * AGGRO_EVASION_BONUS
    life_diff = _life_value(snap.my_life) - _life_value(snap.opp_life)
    hand_bonus = snap.my_hand_size * AGGRO_HAND_BONUS
    lifelink = snap.my_lifelink_power * AGGRO_LIFELINK_BONUS
    return clock_advantage + board_power + evasion_bonus + life_diff + hand_bonus + lifelink


def evaluate_board_midrange(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Midrange value function. All weights from ai/constants.py."""
    from ai.constants import (
        MIDRANGE_MY_POWER_MULT, MIDRANGE_OPP_POWER_MULT,
        MIDRANGE_CREATURE_COUNT_MULT, MIDRANGE_CARD_ADVANTAGE_MULT,
        MIDRANGE_MANA_MULT, MIDRANGE_CLOCK_MULT,
    )
    board_val = (snap.my_power * MIDRANGE_MY_POWER_MULT
                 - snap.opp_power * MIDRANGE_OPP_POWER_MULT
                 + (snap.my_creature_count - snap.opp_creature_count) * MIDRANGE_CREATURE_COUNT_MULT)
    card_advantage = (snap.my_hand_size - snap.opp_hand_size) * MIDRANGE_CARD_ADVANTAGE_MULT
    life_diff = _life_value(snap.my_life) - _life_value(snap.opp_life)
    mana_bonus = (snap.my_total_lands - snap.opp_total_lands) * MIDRANGE_MANA_MULT
    clock_advantage = (snap.opp_clock - snap.my_clock) * MIDRANGE_CLOCK_MULT
    return board_val + card_advantage + life_diff + mana_bonus + clock_advantage


def evaluate_board_control(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Control value function. All weights from ai/constants.py."""
    from ai.constants import (
        CONTROL_OPP_POWER_PENALTY, CONTROL_OPP_CREATURE_PENALTY,
        CONTROL_MY_POWER_MULT, CONTROL_MY_CREATURE_MULT,
        CONTROL_HAND_DIFF_MULT, CONTROL_HAND_SIZE_MULT,
        CONTROL_MY_LIFE_MULT, CONTROL_OPP_LIFE_MULT, CONTROL_MANA_MULT,
    )
    opp_threat = -snap.opp_power * CONTROL_OPP_POWER_PENALTY - snap.opp_creature_count * CONTROL_OPP_CREATURE_PENALTY
    my_board = snap.my_power * CONTROL_MY_POWER_MULT + snap.my_creature_count * CONTROL_MY_CREATURE_MULT
    card_adv = ((snap.my_hand_size - snap.opp_hand_size) * CONTROL_HAND_DIFF_MULT
                + snap.my_hand_size * CONTROL_HAND_SIZE_MULT)
    life_val = _life_value(snap.my_life) * CONTROL_MY_LIFE_MULT - _life_value(snap.opp_life) * CONTROL_OPP_LIFE_MULT
    mana_bonus = snap.my_total_lands * CONTROL_MANA_MULT
    return opp_threat + my_board + card_adv + life_val + mana_bonus


def evaluate_board_combo(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Combo value function. All weights from ai/constants.py."""
    from ai.constants import (
        COMBO_STORM_BASE, COMBO_STORM_ACCELERATION, COMBO_STORM_THRESHOLD,
        COMBO_LIFE_MULT, COMBO_HAND_MULT, COMBO_BOARD_POWER_MULT,
        COMBO_MANA_POOL_MULT, COMBO_CARDS_DRAWN_MULT, COMBO_GY_CREATURE_MULT,
        DEAD_LIFE_VALUE,
    )
    storm_val = snap.storm_count * COMBO_STORM_BASE
    if snap.storm_count >= COMBO_STORM_THRESHOLD:
        storm_val += snap.storm_count * COMBO_STORM_ACCELERATION
    if snap.am_dead_next:
        return DEAD_LIFE_VALUE
    life_buffer = _life_value(snap.my_life) * COMBO_LIFE_MULT
    hand_val = snap.my_hand_size * COMBO_HAND_MULT
    board_val = snap.my_power * COMBO_BOARD_POWER_MULT
    mana_val = snap.my_mana * COMBO_MANA_POOL_MULT
    chain_val = snap.cards_drawn_this_turn * COMBO_CARDS_DRAWN_MULT
    gy_val = snap.my_gy_creatures * COMBO_GY_CREATURE_MULT
    return storm_val + life_buffer + hand_val + board_val + mana_val + chain_val + gy_val


# Archetype dispatcher
_ARCHETYPE_EVALUATORS = {
    "aggro": evaluate_board_aggro,
    "midrange": evaluate_board_midrange,
    "control": evaluate_board_control,
    "combo": evaluate_board_combo,
}


def evaluate_board(snap: EVSnapshot, archetype: str = "midrange",
                   dk: Optional[DeckKnowledge] = None) -> float:
    """Evaluate a board state using the archetype-specific value function."""
    fn = _ARCHETYPE_EVALUATORS.get(archetype, evaluate_board_midrange)
    return fn(snap, dk)


# ─────────────────────────────────────────────────────────────
# Spell EV estimation — what's a spell worth to cast right now?
# ─────────────────────────────────────────────────────────────

def estimate_spell_ev(card: "CardInstance", snap: EVSnapshot,
                      archetype: str, dk: Optional[DeckKnowledge] = None,
                      game: "GameState" = None, player_idx: int = 0) -> float:
    """Estimate the EV of casting a spell.

    This projects what the board looks like after casting and computes
    the difference: EV = evaluate(after) - evaluate(before).
    """
    before = evaluate_board(snap, archetype, dk)
    after_snap = _project_spell(card, snap, dk, game, player_idx)
    after = evaluate_board(after_snap, archetype, dk)
    return after - before


def _project_spell(card: "CardInstance", snap: EVSnapshot,
                   dk: Optional[DeckKnowledge] = None,
                   game: "GameState" = None, player_idx: int = 0) -> EVSnapshot:
    """Project the board state after casting a spell (without mutating game state)."""
    t = card.template
    tags = getattr(t, 'tags', set())
    projected = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=snap.my_hand_size - 1,  # we cast it from hand
        opp_hand_size=snap.opp_hand_size,
        my_mana=max(0, snap.my_mana - (t.cmc or 0)),
        opp_mana=snap.opp_mana,
        my_total_lands=snap.my_total_lands,
        opp_total_lands=snap.opp_total_lands,
        turn_number=snap.turn_number,
        storm_count=snap.storm_count + 1,
        my_gy_creatures=snap.my_gy_creatures,
        my_energy=snap.my_energy,
        my_evasion_power=snap.my_evasion_power,
        my_lifelink_power=snap.my_lifelink_power,
        opp_evasion_power=snap.opp_evasion_power,
        cards_drawn_this_turn=snap.cards_drawn_this_turn,
    )

    # Creature deployment
    if t.is_creature:
        p = t.power if t.power else 0
        tough = t.toughness if t.toughness else 0

        # Handle scaling creatures (domain, delirium, etc.)
        if game and player_idx is not None:
            from engine.cards import CardInstance as CI
            # Check if card has dynamic power (domain, etc.)
            # Use the card's actual power if it's already a CardInstance
            if hasattr(card, 'power') and card.power is not None:
                p = card.power
            if hasattr(card, 'toughness') and card.toughness is not None:
                tough = card.toughness

        projected.my_power += max(0, p)
        projected.my_toughness += max(0, tough)
        projected.my_creature_count += 1

        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(t, 'keywords', set())}
        if kws & {'flying', 'menace', 'trample'}:
            projected.my_evasion_power += max(0, p)
        if 'lifelink' in kws:
            projected.my_lifelink_power += max(0, p)

    # Removal — kills best opponent creature
    if 'removal' in tags and not 'board_wipe' in tags:
        if snap.opp_creature_count > 0 and game:
            opp = game.players[1 - player_idx]
            # Target the highest-power creature we can kill
            best_target_power = 0
            for c in opp.creatures:
                cp = c.power if c.power else 0
                if cp > best_target_power:
                    best_target_power = cp
            projected.opp_power = max(0, projected.opp_power - best_target_power)
            projected.opp_creature_count = max(0, projected.opp_creature_count - 1)

    # Board wipe — kills all creatures
    if 'board_wipe' in tags:
        projected.opp_power = 0
        projected.opp_creature_count = 0
        projected.opp_evasion_power = 0
        projected.my_power = 0
        projected.my_creature_count = 0
        projected.my_evasion_power = 0
        projected.my_lifelink_power = 0

    # Burn damage to face
    if 'burn' in tags or ('damage' in (t.oracle_text or '').lower()):
        oracle = (t.oracle_text or '').lower()
        # Try to detect damage amount from oracle text
        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(t.name)
        if dmg > 0:
            # Can go face — reduce opponent life
            projected.opp_life -= dmg

    # Card draw
    if 'cantrip' in tags or 'draw' in tags:
        projected.my_hand_size += 1  # net 0 since we already subtracted 1
        projected.cards_drawn_this_turn += 1
        # If draws more than 1 card
        oracle = (t.oracle_text or '').lower()
        if 'draw two' in oracle or 'draws two' in oracle:
            projected.my_hand_size += 1
            projected.cards_drawn_this_turn += 1
        elif 'draw three' in oracle or 'draws three' in oracle:
            projected.my_hand_size += 2
            projected.cards_drawn_this_turn += 2

    # Rituals — add mana (net positive: Pyretic Ritual costs 2, produces 3)
    if 'ritual' in tags:
        # Most rituals produce 3 mana for 2 cost = net +1
        # Manamorphose produces 2 for 2 = net 0 but draws a card
        # We already subtracted the cost above, so add the gross production
        projected.my_mana += 3  # Pyretic/Desperate produce 3R
        # Manamorphose produces 2 + draws (already handled by cantrip)
        if 'cantrip' in tags:
            projected.my_mana -= 1  # Manamorphose only produces 2

    # ETB life gain (e.g., Omnath, Thragtusk)
    if 'etb_value' in tags and 'lifelink' not in tags:
        oracle = (t.oracle_text or '').lower()
        if 'gain' in oracle and 'life' in oracle:
            # Estimate: most ETB life gain is 2-4
            projected.my_life += 3

    # Energy producers
    if 'energy' in tags:
        projected.my_energy += 2  # conservative estimate

    return projected


def estimate_pass_ev(snap: EVSnapshot, archetype: str,
                     dk: Optional[DeckKnowledge] = None) -> float:
    """EV of passing (doing nothing this decision point).

    Passing means we waste mana this turn. The opponent develops their board
    while we stand still. This should be a PENALTY, not a bonus.

    The only reason to pass is if all available plays are actively harmful.
    """
    current = evaluate_board(snap, archetype, dk)

    # Passing wastes mana — penalty proportional to unused mana
    # Having 3 mana and passing is worse than having 1 mana and passing
    mana_waste_penalty = -snap.my_mana * 0.5

    # Opponent develops: they get another turn to attack and deploy
    opp_development_penalty = 0.0
    if snap.opp_power > 0:
        # We'll take a hit from their creatures
        damage_taken = snap.opp_power - snap.my_lifelink_power
        if damage_taken > 0:
            life_before = _life_value(snap.my_life)
            life_after = _life_value(max(0, snap.my_life - damage_taken))
            opp_development_penalty = -(life_before - life_after) * 0.3

    # Combo decks: passing is especially bad — they need to chain spells NOW
    combo_penalty = 0.0
    if archetype == "combo":
        combo_penalty = -snap.my_mana * 1.0  # wasting mana is terrible for combo
        if snap.my_hand_size >= 5:
            combo_penalty -= 2.0  # full hand + doing nothing = bad

    return current + mana_waste_penalty + opp_development_penalty + combo_penalty


# ─────────────────────────────────────────────────────────────
# Future value estimation with deck composition
# ─────────────────────────────────────────────────────────────

def estimate_future_value(snap: EVSnapshot, archetype: str,
                          dk: Optional[DeckKnowledge] = None,
                          turns_ahead: int = 2) -> float:
    """Estimate future value by considering what we'll likely draw.

    Uses deck composition math when DeckKnowledge is available.
    Otherwise falls back to current board projection.
    """
    if dk is None or dk.deck_size == 0:
        return evaluate_board(snap, archetype)

    # What fraction of our deck is lands vs spells?
    land_density = dk.category_density(dk._land_names)
    spell_density = 1.0 - land_density

    # Expected draws over turns_ahead turns
    draws = turns_ahead

    # Project: each turn we likely get ~land_density lands and ~spell_density spells
    projected = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=int(snap.my_hand_size + draws * spell_density),
        opp_hand_size=snap.opp_hand_size + draws,
        my_mana=int(snap.my_total_lands + draws * land_density),
        opp_mana=snap.opp_total_lands + draws,
        my_total_lands=int(snap.my_total_lands + draws * land_density),
        opp_total_lands=snap.opp_total_lands + draws,
        turn_number=snap.turn_number + turns_ahead,
        storm_count=0,
        my_gy_creatures=snap.my_gy_creatures,
        my_energy=snap.my_energy,
        my_evasion_power=snap.my_evasion_power,
        my_lifelink_power=snap.my_lifelink_power,
        opp_evasion_power=snap.opp_evasion_power,
    )

    # Combat damage over turns
    for _ in range(turns_ahead):
        projected.my_life = max(0, projected.my_life - snap.opp_power)
        projected.opp_life = max(0, projected.opp_life - snap.my_power)
        projected.my_life += snap.my_lifelink_power

    discount = 0.8 ** turns_ahead
    return discount * evaluate_board(projected, archetype, dk)
