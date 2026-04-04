"""Combo readiness evaluator — abstracted go/wait decision framework.

This module answers: "Should I attempt my combo NOW, or wait?"

It is deck-agnostic. Any combo deck can use it by providing resource
counts and survival estimates. No card names appear anywhere.

Architecture:
  - combo_chain.py computes WHAT chains are possible (sequencing)
  - This module decides WHEN to act (timing)
  - The game loop handles spell-by-spell execution (already correct)

The GO/WAIT decision uses `potential_storm` — total castable spells
including graveyard with rebuy — NOT the chain simulator's limited
snapshot of hand-only sequences.

The same framework applies to:
  - Storm deciding when to go off
  - Goryo's deciding when to reanimate
  - Living End deciding when to cascade
  - Aggro deciding when to alpha strike
  - Control deciding when to tap out for a threat
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance


class ComboAction(Enum):
    """What the combo player should do this turn."""
    GO_NOW = "go_now"           # Execute the combo immediately
    WAIT_AND_DIG = "wait_dig"   # Cast cantrips/draw but save fuel
    WAIT_AND_HOLD = "wait_hold" # Pass, save everything for next turn
    DEPLOY_ENABLER = "deploy"   # Play a setup piece (reducer, engine)


@dataclass
class ComboReadiness:
    """Abstracted assessment of combo state — pure facts, no card names."""
    # Kill potential (from chain simulator — limited to hand-only view)
    best_damage_now: int        # max damage from chain simulator
    best_tokens_now: int        # max tokens from chain simulator

    # Resource-based potential (includes GY + rebuy — more accurate)
    potential_storm: int        # total castable spells (hand fuel + GY if rebuy)
    hand_fuel_count: int        # fuel cards in hand only
    graveyard_rebuy_count: int  # instants/sorceries in GY
    has_rebuy_engine: bool      # PiF or similar available

    # Opponent state
    opponent_life: int          # how much damage needed

    # Survival
    survival_turns: int         # estimated turns until we die (999 = safe)
    am_dead_next: bool          # will I die next turn?

    # Resources
    mana_now: int               # available mana this turn
    library_fuel_density: float # fraction of library that is useful (0.0-1.0)
    has_payoff: bool            # finisher in hand?
    has_reducer_deployed: bool  # cost reducer on board?
    has_reducer_in_hand: bool   # cost reducer available to deploy?

    # Chain simulator results (for execution, not timing)
    best_storm_now: int         # best storm from chain simulator
    chains_found: int           # number of chains found

    @property
    def can_probably_kill(self) -> bool:
        """Can we probably kill this turn using all resources?

        With rebuy engines (Past in Flames), we can recast graveyard spells,
        so we only need about 60% of opponent's life in potential storm.
        Without rebuy, we need the full amount.
        """
        if not self.has_payoff:
            return False
        # With rebuy engine: graveyard spells are effectively doubled
        effective_potential = self.potential_storm
        if self.has_rebuy_engine:
            effective_potential += self.graveyard_rebuy_count
        return effective_potential >= self.opponent_life * 0.7

    @property
    def is_lethal_chain(self) -> bool:
        """Did the chain simulator find a definitely-lethal sequence?"""
        return self.best_damage_now >= self.opponent_life

    @property
    def is_lethal_tokens(self) -> bool:
        """Can we create enough tokens to kill next turn?"""
        return self.best_tokens_now >= self.opponent_life

    @property
    def kill_fraction(self) -> float:
        """What fraction of opponent's life can potential_storm cover?"""
        if self.opponent_life <= 0:
            return 1.0
        return self.potential_storm / self.opponent_life


def evaluate_readiness(
    me: "object",  # PlayerState
    opp: "object",  # PlayerState
    chains: list,
    status: dict,
    available_mana: int,
    medallion_count: int,
    opp_clock: int,
    am_dead_next: bool,
    payoff_names: set,
) -> ComboReadiness:
    """Build a ComboReadiness from game state. Pure facts, no decisions."""

    # Best chain outcomes (from chain simulator)
    best_damage = 0
    best_tokens = 0
    best_storm = 0
    for chain in chains:
        if chain.storm_damage > best_damage:
            best_damage = chain.storm_damage
        if chain.storm_tokens > best_tokens:
            best_tokens = chain.storm_tokens
        if chain.storm_count > best_storm:
            best_storm = chain.storm_count

    # Library fuel density
    lib = me.library
    if lib:
        fuel_in_lib = sum(1 for c in lib if _is_fuel(c, payoff_names))
        fuel_density = fuel_in_lib / len(lib)
    else:
        fuel_density = 0.0

    # Graveyard rebuy count
    gy_spells = sum(1 for c in me.graveyard
                    if c.template.is_instant or c.template.is_sorcery)

    # Has rebuy engine (any card tagged flashback + combo)
    has_rebuy = any(
        'flashback' in getattr(c.template, 'tags', set()) and
        'combo' in getattr(c.template, 'tags', set())
        for c in me.hand
    ) or any(
        'flashback' in getattr(c.template, 'tags', set()) and
        'combo' in getattr(c.template, 'tags', set())
        for c in me.battlefield
    )

    # Hand fuel count
    hand_fuel = sum(1 for c in me.hand if _is_fuel(c, payoff_names))

    # Potential storm: the key metric for timing decisions
    # Includes GY spells when rebuy engine is available
    potential = hand_fuel + (gy_spells if has_rebuy else 0)

    return ComboReadiness(
        best_damage_now=best_damage,
        best_tokens_now=best_tokens,
        potential_storm=potential,
        hand_fuel_count=hand_fuel,
        graveyard_rebuy_count=gy_spells,
        has_rebuy_engine=has_rebuy,
        opponent_life=opp.life,
        survival_turns=opp_clock,
        am_dead_next=am_dead_next,
        mana_now=available_mana,
        library_fuel_density=fuel_density,
        has_payoff=status.get('has_payoff', False),
        has_reducer_deployed=status.get('reducer_deployed', False),
        has_reducer_in_hand=status.get('has_reducer_in_hand', False),
        best_storm_now=best_storm,
        chains_found=len(chains),
    )


def decide_go_or_wait(readiness: ComboReadiness) -> ComboAction:
    """Abstracted go/wait decision. No card names, no deck-specific logic.

    Uses potential_storm (hand + GY with rebuy) for timing decisions,
    NOT the chain simulator's limited hand-only storm count.

    Decision tree (priority order):
    1. Chain simulator found lethal? → GO (certain kill)
    2. Resource count says probably lethal? → GO
    3. Dead next turn? → GO (anything beats dying)
    4. Lethal tokens and survive to attack? → GO
    5. Need to deploy enabler? → DEPLOY
    6. Under pressure with decent resources? → GO
    7. Otherwise → DIG
    """
    # 1. Chain simulator found a guaranteed lethal sequence
    if readiness.is_lethal_chain:
        return ComboAction.GO_NOW

    # 2. Resource counting says we can probably kill
    if readiness.can_probably_kill:
        return ComboAction.GO_NOW

    # 3. Dead next turn — go with whatever we have
    if readiness.am_dead_next:
        return ComboAction.GO_NOW

    # 4. Lethal tokens and we survive to attack
    if readiness.is_lethal_tokens and readiness.survival_turns >= 2:
        return ComboAction.GO_NOW

    # 5. Need to deploy enabler (cost reducer not yet on board)
    if readiness.has_reducer_in_hand and not readiness.has_reducer_deployed:
        return ComboAction.DEPLOY_ENABLER

    # 6. Under pressure with meaningful resources
    if readiness.survival_turns <= 3 and readiness.kill_fraction >= 0.5:
        return ComboAction.GO_NOW

    # 7. Have rebuy engine and graveyard is stocked — go
    if readiness.has_rebuy_engine and readiness.graveyard_rebuy_count >= 3 \
            and readiness.has_payoff:
        return ComboAction.GO_NOW

    # 8. Have payoff and decent storm potential — just go (turn 5+)
    if readiness.has_payoff and readiness.potential_storm >= 8:
        return ComboAction.GO_NOW

    # 9. Otherwise dig for more resources
    return ComboAction.WAIT_AND_DIG


def _is_fuel(card: "CardInstance", payoff_names: set) -> bool:
    """Is this card useful fuel for a combo chain?"""
    t = card.template
    if t.is_land:
        return False
    tags = getattr(t, 'tags', set())
    # Rituals, cantrips, cost reducers, tutors, combo pieces
    return bool(tags & {'ritual', 'cantrip', 'cost_reducer', 'tutor', 'combo',
                        'mana_source', 'card_advantage'})
