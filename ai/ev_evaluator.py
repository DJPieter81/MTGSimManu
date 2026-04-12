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


# Life valuation is now in ai/clock.py: life_as_resource()


# ─────────────────────────────────────────────────────────────
# Creature value — clock-based, derived from game mechanics
# ─────────────────────────────────────────────────────────────

# Default snapshot for context-free creature valuation
# Represents "average mid-game board" — used when no game state available
_DEFAULT_SNAP = EVSnapshot(
    opp_life=20, opp_power=3, opp_creature_count=1,
    my_life=20, my_power=3, opp_toughness=3,
    opp_evasion_power=0,
)

def creature_value(card: "CardInstance") -> float:
    """Evaluate a creature's worth on the battlefield.

    Uses clock-based impact: how much does this creature change
    the turns-to-win calculation? Scaled to ~3-10 range for
    compatibility with targeting/blocking comparisons.
    """
    from ai.clock import creature_clock_impact_from_card
    # Clock impact is ~0.05-0.5; scale by 20 (opp_life) to get ~1-10 range
    return creature_clock_impact_from_card(card, _DEFAULT_SNAP) * 20.0


# ─────────────────────────────────────────────────────────────
# Per-archetype value functions
# ─────────────────────────────────────────────────────────────

# Archetype dispatcher — unified clock-based evaluation
def evaluate_board(snap: EVSnapshot, archetype: str = "midrange",
                   dk: Optional[DeckKnowledge] = None) -> float:
    """Evaluate a board state using clock-based position value.

    All archetypes use the same unified evaluation: clock differential
    + resource advantage. Archetype affects only combo/storm clock
    override. No arbitrary per-archetype weights.
    """
    from ai.clock import position_value
    return position_value(snap, archetype)


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

        # Token makers: project future token value as bonus power.
        # A token maker that attacks every turn generates ~1 token/turn.
        # Model as +2 projected power (conservative: tokens arrive next turn onwards).
        if 'token_maker' in tags:
            projected.my_power += 2
            projected.my_creature_count += 1

    # Reanimation — bring back best creature from graveyard
    if 'reanimate' in tags and game:
        me = game.players[player_idx]
        from engine.cards import CardType
        gy_creatures = [c for c in me.graveyard
                       if CardType.CREATURE in c.template.card_types]
        if gy_creatures:
            best = max(gy_creatures, key=lambda c: (c.template.power or 0) + (c.template.toughness or 0))
            p = best.template.power or 0
            tough = best.template.toughness or 0
            projected.my_power += p
            projected.my_toughness += tough
            projected.my_creature_count += 1
            projected.my_gy_creatures = max(0, projected.my_gy_creatures - 1)
            kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
                   for kw in getattr(best.template, 'keywords', set())}
            if kws & {'flying', 'menace', 'trample'}:
                projected.my_evasion_power += p
            if 'lifelink' in kws:
                projected.my_lifelink_power += p

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
        if snap.opp_creature_count == 0:
            # Opponent has no creatures — board wipe is pure waste
            projected.opp_life += 100
            # Still kills our own creatures (wrath is symmetric)
            projected.my_power = 0
            projected.my_creature_count = 0
            projected.my_evasion_power = 0
            projected.my_lifelink_power = 0
        else:
            projected.opp_power = 0
            projected.opp_creature_count = 0
            projected.opp_evasion_power = 0
            projected.my_power = 0
            projected.my_creature_count = 0
            projected.my_evasion_power = 0
            projected.my_lifelink_power = 0
            # Wrath also destroys artifacts/enchantments — bonus vs artifact-heavy boards
            if game:
                opp = game.players[1 - player_idx]
                opp_nonland = sum(1 for c in opp.battlefield if not c.template.is_land)
                if opp_nonland >= 4:
                    projected.opp_life += 5  # bonus: crippling a wide board

    # Burn damage to face
    if 'burn' in tags or ('damage' in (t.oracle_text or '').lower()):
        oracle = (t.oracle_text or '').lower()
        # Try to detect damage amount from oracle text
        from decks.card_knowledge_loader import get_burn_damage
        dmg = get_burn_damage(t.name)
        if dmg > 0:
            # Only project face damage if we have board presence or opponent is low
            if snap.my_creature_count > 0 or snap.opp_life <= 10:
                projected.opp_life -= dmg
            else:
                # No clock and opponent healthy — burn face has minimal value
                projected.opp_life -= dmg * 0.1

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

    # Prowess bonus: noncreature spells pump prowess creatures
    # Each prowess trigger = +1 power (or more for Slickshot +2/+0)
    # This extra combat damage isn't captured by the basic projection
    if not t.is_creature and game:
        me = game.players[player_idx]
        from engine.cards import Keyword as _Kw
        prowess_bonus = 0
        for creature in me.creatures:
            if _Kw.PROWESS in creature.keywords:
                prowess_bonus += 1
            else:
                c_oracle = (creature.template.oracle_text or '').lower()
                if 'noncreature spell' in c_oracle:
                    import re
                    pump = re.search(r'gets?\s+\+(\d+)/\+(\d+)', c_oracle)
                    if pump:
                        prowess_bonus += int(pump.group(1))
        if prowess_bonus > 0:
            projected.my_power += prowess_bonus
            # Prowess creatures are typically evasive (flying, haste)
            projected.my_evasion_power += prowess_bonus

    return projected


def estimate_opponent_response(card: "CardInstance", projected: EVSnapshot,
                               snap: EVSnapshot, game: "GameState" = None,
                               player_idx: int = 0,
                               bhi: "BayesianHandTracker" = None) -> EVSnapshot:
    """Estimate the board state after the opponent responds to our spell.

    Models the opponent's most likely response:
    1. Counter the spell (if they have mana for it) → revert to pre-cast state
    2. Remove the creature we just deployed → lose the creature
    3. Pass (no response) → projected state stands

    Uses opponent's open mana and deck archetype to estimate response
    probability. Does NOT require knowing the opponent's hand.

    Returns the projected snapshot after opponent's best response.
    """
    from ai.constants import (
        COUNTER_ESTIMATED_COST, REMOVAL_ESTIMATED_COST,
        DAMAGE_REMOVAL_EFF_HIGH_TOUGH, DAMAGE_REMOVAL_EFF_MID_TOUGH,
    )

    t = card.template
    tags = getattr(t, 'tags', set())

    # If opponent has no mana open, they can't respond
    if projected.opp_mana < 1:
        return projected

    # Estimate: can opponent counter this spell?
    can_counter = projected.opp_mana >= COUNTER_ESTIMATED_COST

    # "Can't be countered" — opponent can't counter these
    oracle = (t.oracle_text or '').lower()
    if "can't be countered" in oracle or "can\u2019t be countered" in oracle:
        can_counter = False

    # Response probabilities: use BHI posteriors if available, else static density.
    # BHI updates based on observed priority passes — if opponent has been passing
    # with mana up, P(counter) decreases.
    #
    # Threat-proportional scaling: opponents save counters for high-impact spells.
    # P(counter THIS) = P(has counter) × worthiness
    # worthiness = raw_delta / (raw_delta + avg_card_value)
    # This is derived from game theory: counter the spell that changes the
    # game state the most. Both terms from existing clock math.
    from ai.clock import card_clock_impact
    raw_delta = abs(evaluate_board(projected, "midrange") - evaluate_board(snap, "midrange"))
    avg_card_value = card_clock_impact(snap)
    if avg_card_value > 0 and raw_delta > 0:
        counter_worthiness = raw_delta / (raw_delta + avg_card_value)
    else:
        counter_worthiness = 1.0

    opp_hand_size = snap.opp_hand_size if snap.opp_hand_size > 0 else 5
    counter_probability = 0.0
    removal_probability = 0.0

    if bhi and bhi._initialized:
        # Use Bayesian-updated beliefs, scaled by spell worthiness
        if can_counter:
            counter_probability = bhi.get_counter_probability() * counter_worthiness
        if t.is_creature and projected.opp_mana >= REMOVAL_ESTIMATED_COST:
            removal_probability = bhi.get_removal_probability()
            # Toughness adjustments: high toughness reduces damage-based removal
            creature_toughness = t.toughness or 0
            if hasattr(card, 'toughness') and card.toughness is not None:
                creature_toughness = card.toughness
            exile_fraction = (bhi.get_exile_removal_probability()
                              / max(0.01, bhi.get_removal_probability()))
            damage_fraction = 1.0 - exile_fraction
            if creature_toughness >= 4:
                removal_probability *= (exile_fraction + damage_fraction * DAMAGE_REMOVAL_EFF_HIGH_TOUGH)
            elif creature_toughness >= 3:
                removal_probability *= (exile_fraction + damage_fraction * DAMAGE_REMOVAL_EFF_MID_TOUGH)
    elif game:
        # Fallback: static deck density (no BHI tracker available)
        opp = game.players[1 - player_idx]
        if can_counter and opp.counter_density > 0:
            counter_probability = (1.0 - (1.0 - opp.counter_density) ** opp_hand_size) * counter_worthiness
        if t.is_creature and projected.opp_mana >= REMOVAL_ESTIMATED_COST and opp.removal_density > 0:
            creature_toughness = t.toughness or 0
            if hasattr(card, 'toughness') and card.toughness is not None:
                creature_toughness = card.toughness
            exile_prob = 1.0 - (1.0 - opp.exile_density) ** opp_hand_size if opp.exile_density > 0 else 0.0
            damage_density = opp.removal_density - opp.exile_density
            damage_prob = 1.0 - (1.0 - max(0, damage_density)) ** opp_hand_size if damage_density > 0 else 0.0
            if creature_toughness >= 4:
                damage_prob *= DAMAGE_REMOVAL_EFF_HIGH_TOUGH
            elif creature_toughness >= 3:
                damage_prob *= DAMAGE_REMOVAL_EFF_MID_TOUGH
            removal_probability = min(1.0, exile_prob + damage_prob * (1.0 - exile_prob))

    # Scale down removal probability for cheap creatures — opponents rarely
    # waste premium removal on 0-1 CMC threats. Even if they do, the tempo
    # trade favors the cheap creature's controller.
    cmc = t.cmc or 0
    if cmc == 0:
        removal_probability *= 0.15
    elif cmc == 1:
        removal_probability *= 0.25
    elif cmc == 2:
        removal_probability *= 0.4  # was 0.6 — too aggressive for 2-drops

    # Evasion discount: creatures that can become unblockable/flying are harder
    # to remove via damage (opponent needs instant-speed removal not just blocks).
    # Check both innate evasion and oracle-derived evasion (e.g. Psychic Frog).
    card_oracle = (t.oracle_text or '').lower()
    has_innate_evasion = bool(
        getattr(t, 'keywords', set()) & {'flying', 'menace', 'trample', 'shadow'}
    )
    has_conditional_evasion = (
        'flying' in card_oracle and
        ('counter' in card_oracle or 'discard' in card_oracle or 'whenever' in card_oracle)
    )
    if has_innate_evasion or has_conditional_evasion:
        # Evasion means damage-based removal is less effective at stopping attacks.
        # Reduce only the damage-removal portion (exile still applies fully).
        if bhi and bhi._initialized:
            exile_frac = bhi.get_exile_removal_probability() / max(0.01, bhi.get_removal_probability()) if bhi.get_removal_probability() > 0 else 0.5
        else:
            exile_frac = 0.5
        damage_frac = 1.0 - exile_frac
        removal_probability *= (exile_frac + damage_frac * 0.5)  # halve damage-removal relevance

    # Compute expected value as weighted average of outcomes:
    # P(counter) * V(countered) + P(removal) * V(removed) + P(pass) * V(projected)

    if counter_probability <= 0 and removal_probability <= 0:
        return projected  # no response possible

    # Build the "countered" state: spell fizzles, we lose the mana and card
    countered = EVSnapshot(
        my_life=snap.my_life,
        opp_life=snap.opp_life,
        my_power=snap.my_power,
        opp_power=snap.opp_power,
        my_toughness=snap.my_toughness,
        opp_toughness=snap.opp_toughness,
        my_creature_count=snap.my_creature_count,
        opp_creature_count=snap.opp_creature_count,
        my_hand_size=snap.my_hand_size - 1,  # card is gone
        opp_hand_size=snap.opp_hand_size - 1,  # they used a counter
        my_mana=max(0, snap.my_mana - (t.cmc or 0)),  # mana spent
        opp_mana=max(0, snap.opp_mana - COUNTER_ESTIMATED_COST),
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

    # Build the "removed" state: creature resolves (ETB fires, tokens created),
    # then dies to instant-speed removal. Tokens from ETB PERSIST — they're
    # already on the battlefield before removal resolves. Only the creature itself
    # is subtracted, not any tokens it already created.
    if t.is_creature:
        kws = {kw.value if hasattr(kw, 'value') else str(kw).lower()
               for kw in getattr(t, 'keywords', set())}
        evasion_sub = max(0, t.power or 0) if (kws & {'flying', 'menace', 'trample'}) else 0
        lifelink_sub = max(0, t.power or 0) if 'lifelink' in kws else 0
        # Token power stays on the board — only the creature itself is removed
        token_power = 0
        token_count = 0
    else:
        evasion_sub = lifelink_sub = token_power = token_count = 0

    removed = EVSnapshot(
        my_life=projected.my_life,
        opp_life=projected.opp_life,
        my_power=projected.my_power - max(0, t.power or 0) - token_power if t.is_creature else projected.my_power,
        opp_power=projected.opp_power,
        my_toughness=projected.my_toughness - max(0, t.toughness or 0) if t.is_creature else projected.my_toughness,
        opp_toughness=projected.opp_toughness,
        my_creature_count=projected.my_creature_count - 1 - token_count if t.is_creature else projected.my_creature_count,
        opp_creature_count=projected.opp_creature_count,
        my_hand_size=projected.my_hand_size,
        opp_hand_size=projected.opp_hand_size - 1,  # opponent used removal card
        my_mana=projected.my_mana,
        opp_mana=max(0, projected.opp_mana - REMOVAL_ESTIMATED_COST),
        my_total_lands=projected.my_total_lands,
        opp_total_lands=projected.opp_total_lands,
        turn_number=projected.turn_number,
        storm_count=projected.storm_count,
        my_gy_creatures=projected.my_gy_creatures + (1 if t.is_creature else 0),
        my_energy=projected.my_energy,
        my_evasion_power=projected.my_evasion_power - evasion_sub,
        my_lifelink_power=projected.my_lifelink_power - lifelink_sub,
        opp_evasion_power=projected.opp_evasion_power,
        cards_drawn_this_turn=projected.cards_drawn_this_turn,
    )

    # Weighted expected snapshot
    pass_probability = 1.0 - counter_probability - removal_probability
    pass_probability = max(0, pass_probability)

    # Blend the snapshots by probability
    def blend(field: str) -> float:
        v_pass = getattr(projected, field)
        v_counter = getattr(countered, field) if counter_probability > 0 else v_pass
        v_remove = getattr(removed, field) if removal_probability > 0 else v_pass
        return (pass_probability * v_pass
                + counter_probability * v_counter
                + removal_probability * v_remove)

    return EVSnapshot(
        my_life=int(blend('my_life')),
        opp_life=int(blend('opp_life')),
        my_power=int(blend('my_power')),
        opp_power=int(blend('opp_power')),
        my_toughness=int(blend('my_toughness')),
        opp_toughness=int(blend('opp_toughness')),
        my_creature_count=int(blend('my_creature_count')),
        opp_creature_count=int(blend('opp_creature_count')),
        my_hand_size=int(blend('my_hand_size')),
        opp_hand_size=int(blend('opp_hand_size')),
        my_mana=int(blend('my_mana')),
        opp_mana=int(blend('opp_mana')),
        my_total_lands=int(blend('my_total_lands')),
        opp_total_lands=int(blend('opp_total_lands')),
        turn_number=projected.turn_number,
        storm_count=int(blend('storm_count')),
        my_gy_creatures=int(blend('my_gy_creatures')),
        my_energy=int(blend('my_energy')),
        my_evasion_power=int(blend('my_evasion_power')),
        my_lifelink_power=int(blend('my_lifelink_power')),
        opp_evasion_power=int(blend('opp_evasion_power')),
        cards_drawn_this_turn=int(blend('cards_drawn_this_turn')),
    )



# ═══════════════════════════════════════════════════════════════════
# Combo Chain Evaluator — lookahead for storm/ritual chains
# ═══════════════════════════════════════════════════════════════════

def _estimate_combo_chain(game, player_idx: int, first_card=None):
    """Simulate casting all chainable spells from hand to estimate kill potential.

    Returns (can_kill: bool, storm_count: int, total_damage: int, chain: list[str])

    Models:
    - Rituals: spend CMC, gain 3R (net +1)
    - Cost reducers on battlefield: -1 to instant/sorcery costs
    - Draw spells: draw 2 cards (may find more rituals)
    - Finishers: Grapeshot (storm copies), Empty the Warrens (tokens)
    """
    me = game.players[player_idx]

    # Count cost reducers on battlefield
    reducers = sum(1 for c in me.battlefield
                   if getattr(c.template, 'is_cost_reducer', False))

    # Available mana
    mana = len(me.untapped_lands) + me.mana_pool.total()

    # Partition hand into categories
    rituals = []
    draws = []
    finishers = []
    other_spells = []

    for c in me.hand:
        if c.template.is_land:
            continue
        tags = getattr(c.template, 'tags', set())
        name = c.name
        cmc = max(0, (c.template.cmc or 0) - reducers)

        if 'ritual' in tags:
            rituals.append((name, cmc, 3))  # name, cost, mana produced
        elif name in ('Grapeshot', 'Empty the Warrens'):
            finishers.append((name, cmc))
        elif 'cantrip' in tags or 'card_advantage' in tags or name in (
                'Reckless Impulse', "Wrenn's Resolve", 'Glimpse the Impossible',
                'Expressive Iteration', 'Valakut Awakening // Valakut Stoneforge'):
            draws.append((name, cmc))
        elif 'instant_speed' in tags or not c.template.is_creature:
            other_spells.append((name, cmc))

    # Simulate the chain
    storm = 0
    chain = []
    if first_card and first_card.name not in [r[0] for r in rituals] + [d[0] for d in draws] + [f[0] for f in finishers]:
        return False, 0, 0, []

    # Cast rituals first (net positive mana)
    for name, cost, produced in sorted(rituals, key=lambda r: r[1]):
        if mana >= cost:
            mana = mana - cost + produced
            storm += 1
            chain.append(name)

    # Cast draw spells (may chain into more gas)
    for name, cost in sorted(draws, key=lambda d: d[1]):
        if mana >= cost:
            mana -= cost
            storm += 1
            chain.append(name)
            # Draw spells find ~1 more castable spell on average
            mana += 1  # approximate: drawn card is often a ritual or free spell

    # Cast other cheap spells for storm count
    for name, cost in sorted(other_spells, key=lambda s: s[1]):
        if cost <= 1 and mana >= cost:
            mana -= cost
            storm += 1
            chain.append(name)

    # Can we cast a finisher?
    for name, cost in finishers:
        if mana >= cost:
            storm += 1
            chain.append(name)
            if name == 'Grapeshot':
                total_damage = storm  # each storm copy deals 1
                can_kill = total_damage >= game.players[1 - player_idx].life
                return can_kill, storm, total_damage, chain
            elif name == 'Empty the Warrens':
                tokens = storm * 2
                return tokens >= 6, storm, tokens, chain  # 6+ goblins is usually enough

    return False, storm, 0, chain


def compute_play_ev(card: "CardInstance", snap: EVSnapshot, archetype: str,
                    game: "GameState" = None, player_idx: int = 0,
                    dk: Optional[DeckKnowledge] = None,
                    detailed: bool = False,
                    bhi: "BayesianHandTracker" = None):
    """Compute the expected value of casting a spell using 1-ply lookahead.

    EV = E[V(state_after_play_and_response)] - V(current_state)

    If detailed=True, returns (ev, info_dict) with projection breakdown.
    Otherwise returns ev as a float.
    """
    current_value = evaluate_board(snap, archetype, dk)

    # Project state after casting
    projected = _project_spell(card, snap, dk, game, player_idx)
    projected_value = evaluate_board(projected, archetype, dk)

    # Model opponent response (counter, removal, or pass)
    post_response = estimate_opponent_response(card, projected, snap, game, player_idx, bhi=bhi)
    after_value = evaluate_board(post_response, archetype, dk)

    ev = after_value - current_value

    # Low-CMC ETB-value creature floor: prevents the removal-projection from
    # shoving a 2-CMC creature with tangible on-board value (Psychic Frog,
    # Orcish Bowmasters, etc.) into deep-negative territory, which made Dimir
    # pass T2 even when the alternative was doing nothing. Floor at -2.0.
    t = card.template
    tags = getattr(t, 'tags', set())
    if (t.is_creature and (t.cmc or 0) <= 2
            and ('etb_value' in tags or 'removal' in tags or 'card_advantage' in tags)):
        ev = max(ev, -2.0)

    # Combo chain boost: if this card starts a lethal storm/combo chain,
    # boost EV massively so the AI prioritizes starting the chain
    if game and archetype == "combo":
        tags = getattr(card.template, 'tags', set())
        is_chain_starter = ('ritual' in tags or 'cantrip' in tags or
                           'card_advantage' in tags or 'cost_reducer' in tags or
                           card.name in ('Grapeshot', 'Empty the Warrens',
                                        'Past in Flames', 'Wish'))
        if is_chain_starter:
            can_kill, storm_count, damage, chain = _estimate_combo_chain(
                game, player_idx, first_card=card)
            if can_kill:
                ev += 50.0  # massive boost — this starts a lethal chain
            elif storm_count >= 4:
                ev += 15.0  # good chain even if not lethal
            elif storm_count >= 2:
                ev += 5.0   # moderate chain

    if not detailed:
        return ev

    # Recover response probabilities — mirrors estimate_opponent_response scaling
    from ai.constants import (
        COUNTER_ESTIMATED_COST, REMOVAL_ESTIMATED_COST,
        DAMAGE_REMOVAL_EFF_HIGH_TOUGH, DAMAGE_REMOVAL_EFF_MID_TOUGH,
    )
    counter_pct = 0.0
    removal_pct = 0.0
    t = card.template
    oracle = (t.oracle_text or '').lower()
    can_counter = (projected.opp_mana >= COUNTER_ESTIMATED_COST
                   and "can't be countered" not in oracle
                   and "can\u2019t be countered" not in oracle)
    opp_hand = snap.opp_hand_size if snap.opp_hand_size > 0 else 5
    if game:
        opp = game.players[1 - player_idx]
        if can_counter and opp.counter_density > 0:
            counter_pct = 1.0 - (1.0 - opp.counter_density) ** opp_hand
        if t.is_creature and projected.opp_mana >= REMOVAL_ESTIMATED_COST and opp.removal_density > 0:
            creature_toughness = t.toughness or 0
            if hasattr(card, 'toughness') and card.toughness is not None:
                creature_toughness = card.toughness
            exile_prob = 1.0 - (1.0 - opp.exile_density) ** opp_hand if opp.exile_density > 0 else 0.0
            damage_density = opp.removal_density - opp.exile_density
            damage_prob = 1.0 - (1.0 - max(0, damage_density)) ** opp_hand if damage_density > 0 else 0.0
            if creature_toughness >= 4:
                damage_prob *= DAMAGE_REMOVAL_EFF_HIGH_TOUGH
            elif creature_toughness >= 3:
                damage_prob *= DAMAGE_REMOVAL_EFF_MID_TOUGH
            removal_pct = min(1.0, exile_prob + damage_prob * (1.0 - exile_prob))
            # Apply same CMC scaling as estimate_opponent_response
            cmc = t.cmc or 0
            if cmc == 0:
                removal_pct *= 0.15
            elif cmc == 1:
                removal_pct *= 0.25
            elif cmc == 2:
                removal_pct *= 0.4
            # Evasion discount: conditional or innate flying/evasion
            has_innate_evasion = bool(
                getattr(t, 'keywords', set()) & {'flying', 'menace', 'trample', 'shadow'}
            )
            has_conditional_evasion = (
                'flying' in oracle and
                ('counter' in oracle or 'discard' in oracle or 'whenever' in oracle)
            )
            if has_innate_evasion or has_conditional_evasion:
                exile_frac = opp.exile_density / max(0.01, opp.removal_density)
                damage_frac = 1.0 - exile_frac
                removal_pct *= (exile_frac + damage_frac * 0.5)

    return ev, {
        'current_value': current_value,
        'projected_value': projected_value,
        'raw_delta': projected_value - current_value,
        'after_response_value': after_value,
        'response_discount': (projected_value - current_value) - ev,
        'counter_pct': counter_pct,
        'removal_pct': removal_pct,
    }


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
            from ai.clock import life_as_resource
            life_before = life_as_resource(snap.my_life, snap.opp_power)
            life_after = life_as_resource(max(0, snap.my_life - damage_taken), snap.opp_power)
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
