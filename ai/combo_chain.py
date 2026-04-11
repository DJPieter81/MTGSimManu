"""Combo chain arithmetic — pure mana/storm counting.

This module answers ONE question: "What spell sequences are physically
possible given my hand, mana, and board?"

It returns FACTS (possible chains with their outcomes). It makes NO
decisions about which chain is better — that belongs in spell_decision.py.

Design principle #8: Separate Arithmetic from Decisions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional, List, Tuple, Dict, Set
from dataclasses import dataclass, field
from itertools import permutations, combinations

if TYPE_CHECKING:
    from engine.cards import CardInstance
    from engine.game_state import GameState


# ─── Mana production now derived from card.template.ritual_mana ───
# (populated by oracle_parser.py at card load time)


# ─── Data structures ───

@dataclass
class CardRole:
    """What a card does mechanically in a chain. No preferences."""
    name: str
    effective_cost: int       # cost after reductions
    mana_produced: int        # mana added to pool (0 for non-rituals)
    draws_card: bool          # does this cantrip?
    is_cost_reducer: bool     # deploys a permanent that reduces future costs
    has_storm: bool           # has the Storm keyword
    is_payoff: bool           # is this a combo payoff (from goal's card_roles)
    deals_direct_damage: bool # does this deal damage without needing combat
    is_arcane: bool = False   # has Arcane subtype (can receive splice)
    splice_cost: int = 0      # cost to splice onto an Arcane spell (0 = no splice)
    splice_mana: int = 0      # mana produced when spliced (from ritual_mana)


@dataclass
class ChainOutcome:
    """The factual outcome of casting a specific sequence of spells."""
    sequence: List[str]       # card names in cast order
    mana_trace: List[int]     # mana remaining after each cast
    final_mana: int           # mana left at end
    storm_count: int          # number of spells cast
    cards_drawn: int          # cards drawn during chain
    medallions_after: int     # cost reducers on board after chain
    payoff_name: Optional[str]  # name of payoff if chain includes one (last spell)
    payoff_deals_damage: bool   # does the payoff deal direct damage?
    payoff_has_storm: bool      # does the payoff have storm keyword?

    @property
    def storm_damage(self) -> int:
        """Direct damage if payoff is a storm damage spell (e.g. Grapeshot)."""
        if self.payoff_has_storm and self.payoff_deals_damage:
            return self.storm_count  # storm copies + original = storm_count damage
        return 0

    @property
    def storm_tokens(self) -> int:
        """Tokens created if payoff is a storm token spell (e.g. Empty the Warrens)."""
        if self.payoff_has_storm and not self.payoff_deals_damage:
            return self.storm_count * 2  # 2 goblins per copy
        return 0

def classify_card(card, available_mana: int, medallion_count: int,
                  payoff_names: Set[str]) -> Optional[CardRole]:
    """Classify what a card does mechanically. No preferences."""
    from engine.cards import Keyword, Color

    t = card.template
    tags = getattr(t, 'tags', set())
    cmc = t.cmc or 0
    oracle = (getattr(t, 'oracle_text', '') or '').lower()

    # Medallion reduction for red instants/sorceries
    reduction = 0
    if (t.is_instant or t.is_sorcery) and Color.RED in t.color_identity:
        reduction = medallion_count
    effective_cost = max(0, cmc - reduction)

    # Mana production (from oracle-derived template property)
    ritual_data = getattr(t, 'ritual_mana', None)
    mana_produced = ritual_data[1] if ritual_data else 0
    draws = ('cantrip' in tags) or (ritual_data and ritual_data[0] == 'any')

    # Cost reducer (permanent that reduces future costs)
    is_reducer = 'cost_reducer' in tags and not t.is_instant and not t.is_sorcery
    if is_reducer:
        effective_cost = cmc  # reducers don't benefit from themselves

    # Storm keyword
    has_storm = Keyword.STORM in getattr(t, 'keywords', set())

    # Is this a payoff for the combo?
    is_payoff = t.name in payoff_names or has_storm

    # Direct damage detection from oracle text
    deals_direct = ('damage' in oracle and
                    ('target' in oracle or 'each' in oracle or 'any' in oracle))

    # Splice onto Arcane: detect from template properties (set by oracle parser)
    is_arcane = getattr(t, 'is_arcane', False)
    splice_cost = getattr(t, 'splice_cost', None) or 0
    splice_mana = ritual_data[1] if (ritual_data and splice_cost > 0) else 0

    return CardRole(
        name=t.name,
        effective_cost=effective_cost,
        mana_produced=mana_produced,
        draws_card=draws,
        is_cost_reducer=is_reducer,
        has_storm=has_storm,
        is_payoff=is_payoff,
        deals_direct_damage=deals_direct,
        is_arcane=is_arcane,
        splice_cost=splice_cost,
        splice_mana=splice_mana,
    )


# ─── Chain simulation (pure arithmetic) ───

def _simulate_sequence(
    sequence: List[Tuple["CardInstance", CardRole]],
    starting_mana: int,
    starting_medallions: int,
    base_storm: int = 0,
    all_classified: List[Tuple["CardInstance", CardRole]] = None,
) -> Optional[ChainOutcome]:
    """Simulate casting spells in order. Returns None if sequence is unaffordable.

    base_storm: spells already cast this turn (from game._global_storm_count).
    all_classified: full classified hand for splice-onto-Arcane checks.
    """
    from engine.cards import Color

    mana = starting_mana
    storm = base_storm
    medallions = starting_medallions
    names = []
    mana_trace = []
    cards_drawn = 0
    payoff_name = None
    payoff_damage = False
    payoff_storm = False

    # Track which card instance_ids are in the sequence (consumed)
    seq_ids = {id(card) for card, _ in sequence}

    # Find spliceable cards NOT in the sequence (they stay in hand)
    spliceable = []
    if all_classified:
        spliceable = [(c, r) for c, r in all_classified
                       if r.splice_cost > 0 and r.splice_mana > 0
                       and id(c) not in seq_ids]

    for card, role in sequence:
        # Recalculate cost with current medallion count
        t = card.template
        reduction = 0
        if (t.is_instant or t.is_sorcery) and Color.RED in t.color_identity:
            reduction = medallions
        cost = max(0, (t.cmc or 0) - reduction)

        if cost > mana:
            return None  # can't afford — chain breaks

        mana -= cost
        ritual_data = getattr(t, 'ritual_mana', None)
        if ritual_data:
            mana += ritual_data[1]  # (color, amount) -> add amount

        # ── Splice onto Arcane: if this spell is Arcane, splice spliceable
        # cards from hand. Splicing adds their effect (mana) without
        # consuming the card. Pay splice cost (reduced by medallions).
        if role.is_arcane and spliceable:
            for splice_card, splice_role in spliceable:
                s_reduction = 0
                if Color.RED in splice_card.template.color_identity:
                    s_reduction = medallions
                splice_eff_cost = max(0, splice_role.splice_cost - s_reduction)
                if splice_eff_cost <= mana:
                    mana -= splice_eff_cost
                    mana += splice_role.splice_mana
                    # Splice doesn't count as casting a spell (no storm increment)

        if role.is_cost_reducer:
            medallions += 1
        if role.draws_card:
            cards_drawn += 1

        storm += 1
        names.append(t.name)
        mana_trace.append(mana)

        # Track payoff (last payoff in sequence wins)
        if role.is_payoff:
            payoff_name = t.name
            payoff_damage = role.deals_direct_damage
            payoff_storm = role.has_storm

    if not names:
        return None

    return ChainOutcome(
        sequence=names,
        mana_trace=mana_trace,
        final_mana=mana,
        storm_count=storm,
        cards_drawn=cards_drawn,
        medallions_after=medallions,
        payoff_name=payoff_name,
        payoff_deals_damage=payoff_damage,
        payoff_has_storm=payoff_storm,
    )


def find_all_chains(
    hand: List["CardInstance"],
    available_mana: int,
    medallion_count: int,
    payoff_names: Set[str],
    base_storm: int = 0,
) -> List[ChainOutcome]:
    """Find ALL viable chains from the given hand and mana.

    Returns a list of ChainOutcomes — every castable sequence.
    The caller decides which one (if any) to execute.

    For hands <= 7 cards: exhaustive search (finisher always last).
    For larger hands: greedy heuristic only.
    """
    # Classify cards
    classified = []
    for card in hand:
        role = classify_card(card, available_mana, medallion_count, payoff_names)
        if role:
            classified.append((card, role))

    if not classified:
        return []

    # Separate fuel from payoffs
    fuel = [(c, r) for c, r in classified if not r.is_payoff]
    payoffs = [(c, r) for c, r in classified if r.is_payoff]

    # Check if any cards have splice (for passing full list to simulator)
    has_splice = any(r.splice_cost > 0 and r.splice_mana > 0 for _, r in classified)
    ac = classified if has_splice else None  # all_classified for splice checks

    results = []

    if len(fuel) <= 7:
        # Exhaustive: try all SUBSETS of fuel (not just full set),
        # each in all permutations, each with payoff appended at end.
        # This finds short viable chains (e.g. Ritual→Grapeshot)
        # that full-set permutations miss when mana is tight.
        seen = set()
        for k in range(1, len(fuel) + 1):
            for subset in combinations(fuel, k):
                for perm in permutations(subset):
                    name_key = tuple(r.name for _, r in perm)
                    if name_key in seen:
                        continue
                    seen.add(name_key)

                    # Fuel-only chain (no payoff)
                    fuel_result = _simulate_sequence(list(perm), available_mana,
                                                     medallion_count, base_storm,
                                                     all_classified=ac)
                    if fuel_result:
                        results.append(fuel_result)

                    # Fuel + each payoff at end
                    for pay_card, pay_role in payoffs:
                        full_seq = list(perm) + [(pay_card, pay_role)]
                        outcome = _simulate_sequence(full_seq, available_mana,
                                                     medallion_count, base_storm,
                                                     all_classified=ac)
                        if outcome:
                            results.append(outcome)

        # Also try payoff-only (no fuel) if affordable
        for pay_card, pay_role in payoffs:
            outcome = _simulate_sequence([(pay_card, pay_role)], available_mana,
                                         medallion_count, base_storm,
                                         all_classified=ac)
            if outcome:
                results.append(outcome)
    else:
        # Greedy: cost reducers → rituals → cantrips → other → each payoff
        cost_reducers = [(c, r) for c, r in fuel if r.is_cost_reducer]
        rituals = [(c, r) for c, r in fuel if r.mana_produced > 0 and not r.is_cost_reducer]
        cantrips = [(c, r) for c, r in fuel if r.draws_card and r.mana_produced == 0]
        other = [(c, r) for c, r in fuel
                 if not r.is_cost_reducer and r.mana_produced == 0 and not r.draws_card]

        rituals.sort(key=lambda x: x[1].mana_produced - x[1].effective_cost, reverse=True)
        cantrips.sort(key=lambda x: x[1].effective_cost)
        other.sort(key=lambda x: x[1].effective_cost)

        base = cost_reducers + rituals + cantrips + other

        # Fuel-only
        fuel_result = _simulate_sequence(base, available_mana, medallion_count,
                                         base_storm, all_classified=ac)
        if fuel_result:
            results.append(fuel_result)

        # With each payoff
        for pay_card, pay_role in payoffs:
            outcome = _simulate_sequence(base + [(pay_card, pay_role)],
                                         available_mana, medallion_count,
                                         base_storm, all_classified=ac)
            if outcome:
                results.append(outcome)

    return results


def what_is_missing(
    hand: List["CardInstance"],
    available_mana: int,
    medallion_count: int,
    payoff_names: Set[str],
) -> Dict[str, bool]:
    """Report what the combo is missing. Pure facts, no decisions.

    Returns a dict of booleans:
      has_payoff: is there a payoff card in hand?
      has_fuel: are there rituals/cantrips to build storm?
      has_reducer: is there a cost reducer in hand or on board?
      can_cast_anything: is there any spell we can afford?
      reducer_deployed: is a cost reducer already on the battlefield?
    """
    from engine.cards import Keyword

    roles = [classify_card(c, available_mana, medallion_count, payoff_names) for c in hand]
    roles = [r for r in roles if r is not None]

    has_payoff = any(r.is_payoff for r in roles)
    has_fuel = any(r.mana_produced > 0 for r in roles)
    has_reducer_in_hand = any(r.is_cost_reducer for r in roles)
    can_cast = any(r.effective_cost <= available_mana for r in roles)
    reducer_deployed = medallion_count > 0

    return {
        'has_payoff': has_payoff,
        'has_fuel': has_fuel,
        'has_reducer_in_hand': has_reducer_in_hand,
        'can_cast_anything': can_cast,
        'reducer_deployed': reducer_deployed,
    }
