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
    """
    t = card.template
    p = card.power if card.power else 0
    tough = card.toughness if card.toughness else 0

    # Base: power + fractional toughness
    val = max(0, p) * 1.0 + max(0, tough) * 0.3

    # Keyword bonuses
    kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
           for kw in getattr(t, 'keywords', set())}
    if 'flying' in kws:
        val += 2.0
    if 'trample' in kws:
        val += 1.0
    if 'lifelink' in kws:
        val += min(p, 5) * 0.5  # scales with power but caps
    if 'haste' in kws:
        val += 1.5
    if 'deathtouch' in kws:
        val += 2.0
    if 'first_strike' in kws:
        val += 1.5
    if 'double_strike' in kws:
        val += max(0, p) * 1.0  # effectively doubles power
    if 'hexproof' in kws:
        val += 2.0
    if 'indestructible' in kws:
        val += 3.0
    if 'menace' in kws:
        val += 1.0
    if 'vigilance' in kws:
        val += 1.0
    if 'undying' in kws:
        val += 2.0
    if 'annihilator' in kws:
        val += 4.0
    if 'prowess' in kws:
        val += 1.5
    if 'cascade' in kws:
        val += 3.0

    # Tag-based ability bonuses (ETB value, card advantage, etc.)
    tags = getattr(t, 'tags', set())
    if 'etb_value' in tags:
        val += 2.0
    if 'card_advantage' in tags:
        val += 3.0
    if 'cost_reducer' in tags:
        val += 2.5
    if 'token_maker' in tags:
        val += 1.5

    return val


# ─────────────────────────────────────────────────────────────
# Per-archetype value functions
# ─────────────────────────────────────────────────────────────

def evaluate_board_aggro(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Aggro value function.

    value = damage potential + (board power * survival turns) - opponent threat
    Aggro wants to maximize damage output and minimize game length.
    """
    # Damage clock differential — how far ahead am I in the race?
    my_clock = snap.my_clock
    opp_clock = snap.opp_clock
    clock_advantage = (opp_clock - my_clock) * 3.0  # being 1 turn faster = +3

    # Board power — raw damage potential
    board_power = snap.my_power * 1.5 - snap.opp_power * 0.5

    # Evasion bonus — evasive damage is more reliable
    evasion_bonus = snap.my_evasion_power * 0.5

    # Life differential (non-linear)
    life_diff = _life_value(snap.my_life) - _life_value(snap.opp_life)

    # Hand size — aggro cares less about cards, more about board
    hand_bonus = snap.my_hand_size * 0.3

    # Lifelink recovery
    lifelink = snap.my_lifelink_power * 0.3

    return clock_advantage + board_power + evasion_bonus + life_diff + hand_bonus + lifelink


def evaluate_board_midrange(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Midrange value function.

    value = board_advantage + card_advantage + life_stability
    Midrange wants to trade resources efficiently and grind.
    """
    # Board advantage — creature quality matters more than quantity
    board_val = 0.0
    board_val += snap.my_power * 1.0 - snap.opp_power * 1.2  # slightly overweight opp threats
    board_val += snap.my_creature_count * 0.5 - snap.opp_creature_count * 0.5

    # Card advantage — midrange thrives on cards in hand
    card_advantage = (snap.my_hand_size - snap.opp_hand_size) * 1.5

    # Life differential (non-linear)
    life_diff = _life_value(snap.my_life) - _life_value(snap.opp_life)

    # Mana advantage
    mana_bonus = (snap.my_total_lands - snap.opp_total_lands) * 0.5

    # Clock differential — moderate weight
    my_clock = snap.my_clock
    opp_clock = snap.opp_clock
    clock_advantage = (opp_clock - my_clock) * 1.5

    return board_val + card_advantage + life_diff + mana_bonus + clock_advantage


def evaluate_board_control(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Control value function.

    value = threats_answered + (payoff_potential * probability_of_casting)
    Control wants to survive, answer threats, then deploy a finisher.
    """
    # Opponent's board should be EMPTY — penalize their presence heavily
    opp_threat_penalty = -snap.opp_power * 2.0 - snap.opp_creature_count * 1.0

    # My board — modest bonus (control has few creatures but they're high-value)
    my_board = snap.my_power * 1.5 + snap.my_creature_count * 0.5

    # Card advantage — control NEEDS cards
    card_advantage = (snap.my_hand_size - snap.opp_hand_size) * 2.0
    card_advantage += snap.my_hand_size * 0.5  # absolute hand size matters

    # Life — control trades life as a resource but needs to stay alive
    life_val = _life_value(snap.my_life) * 1.2 - _life_value(snap.opp_life) * 0.5

    # Mana advantage — control needs mana more than others
    mana_bonus = snap.my_total_lands * 0.8

    return opp_threat_penalty + my_board + card_advantage + life_val + mana_bonus


def evaluate_board_combo(snap: EVSnapshot, dk: Optional[DeckKnowledge] = None) -> float:
    """Combo value function.

    value = combo_proximity * lethal_damage_potential
    Combo wants to assemble pieces and fire the combo.
    """
    # Storm count — how close to lethal combo?
    storm_val = snap.storm_count * 2.0

    # Survival — combo needs to not die
    if snap.am_dead_next:
        return -50.0  # can't combo if dead

    life_buffer = _life_value(snap.my_life) * 0.5

    # Hand size — combo needs cards (fuel)
    hand_val = snap.my_hand_size * 2.0

    # Board — combo doesn't care much about creatures, but engines matter
    # Cost reducers on board are very valuable (handled via card tags)
    board_val = snap.my_power * 0.3

    # Mana — combo needs mana to chain spells
    mana_val = snap.my_mana * 1.0

    # Cards drawn this turn — combo is chaining
    chain_val = snap.cards_drawn_this_turn * 1.5

    # Graveyard creatures (for Living End / reanimator)
    gy_val = snap.my_gy_creatures * 1.0

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

    # Rituals — add mana
    if 'ritual' in tags:
        projected.my_mana += 1  # net +1 mana for most rituals

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

    Accounts for what we might draw next turn and future mana development.
    """
    # Current board value
    current = evaluate_board(snap, archetype, dk)

    # Future value: next turn we get +1 mana, +1 card
    future_snap = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=snap.my_hand_size + 1,  # draw step
        opp_hand_size=snap.opp_hand_size + 1,
        my_mana=snap.my_total_lands + 1,  # assume land drop
        opp_mana=snap.opp_total_lands + 1,
        my_total_lands=snap.my_total_lands + 1,
        opp_total_lands=snap.opp_total_lands + 1,
        turn_number=snap.turn_number + 1,
        storm_count=0,
        my_gy_creatures=snap.my_gy_creatures,
        my_energy=snap.my_energy,
        my_evasion_power=snap.my_evasion_power,
        my_lifelink_power=snap.my_lifelink_power,
        opp_evasion_power=snap.opp_evasion_power,
    )

    # Opponent gets to attack — we take damage
    if snap.opp_power > 0:
        future_snap.my_life = max(0, snap.my_life - snap.opp_power)
        # But lifelink heals us on our attack
        future_snap.my_life += snap.my_lifelink_power

    future = evaluate_board(future_snap, archetype, dk)

    # Discount future by 0.8 (uncertainty)
    return current + 0.8 * (future - current)


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
