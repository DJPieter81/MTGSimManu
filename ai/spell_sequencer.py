"""Spell sequencer — generic role-based ordering for combo turns.

When the goal is EXECUTE_PAYOFF, spells must be cast in a specific
order to maximize effect. This module assigns each card a ROLE based
on its tags, then orders roles so enablers come before finishers.

No card names appear in this module. Roles are derived entirely from
tags assigned by the card_database classifier.

Role ordering (lower = cast first):
  0. REDUCER   — cost reducers (make everything cheaper)
  1. FUEL      — mana producers (rituals, mana sources)
  2. DRAW      — cantrips, card draw (dig for more fuel)
  3. TUTOR     — search effects (find missing pieces)
  4. REBUY     — graveyard replay (Past in Flames, flashback engines)
  5. FINISHER  — payoff cards (storm finishers, combo kills)

Within a role, cards are sorted by mana efficiency (cheapest first).
"""

from __future__ import annotations
from enum import IntEnum
from typing import List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance


class SpellRole(IntEnum):
    """Role of a spell in a combo sequence. Lower = cast first."""
    REDUCER = 0
    FUEL = 1
    DRAW = 2
    TUTOR = 3
    REBUY = 4
    FINISHER = 5
    OTHER = 6  # non-combo cards (creatures, interaction)


def classify_role(card: "CardInstance") -> SpellRole:
    """Assign a role to a card based on its tags. No card names used.

    Priority order matters — a card with both 'ritual' and 'cantrip'
    (like Manamorphose) is classified as FUEL because mana production
    is more important than card draw for sequencing.

    Cost reducer creatures (like Ral) are classified as REDUCER.
    They should be deployed early to enable cheaper chaining.
    """
    tags = getattr(card.template, 'tags', set())

    # 1. Cost reducers always come first (including creatures like Ral)
    if 'cost_reducer' in tags:
        return SpellRole.REDUCER

    # 2. Mana producers (rituals) — fuel for the chain
    if 'ritual' in tags:
        return SpellRole.FUEL
    if 'mana_source' in tags and not card.template.is_creature and not card.template.is_land:
        return SpellRole.FUEL

    # 3. Tutors — find missing pieces
    if 'tutor' in tags:
        return SpellRole.TUTOR

    # 4. Rebuy engines — replay graveyard (must have flashback + combo)
    if 'flashback' in tags and 'combo' in tags:
        return SpellRole.REBUY

    # 5. Cantrips / draw — dig for fuel
    if 'cantrip' in tags or 'draw' in tags or 'card_advantage' in tags:
        return SpellRole.DRAW

    # 6. Combo cards without other roles = finisher
    if 'combo' in tags:
        return SpellRole.FINISHER

    # 7. Everything else (creatures, interaction, etc.)
    return SpellRole.OTHER


def sequence_hand(cards: List["CardInstance"]) -> List[Tuple["CardInstance", SpellRole]]:
    """Sort cards by role order, then by CMC within each role.

    Returns list of (card, role) tuples in optimal cast order.
    """
    classified = [(card, classify_role(card)) for card in cards]
    # Sort by role (lower first), then by CMC (cheaper first)
    classified.sort(key=lambda x: (x[1].value, x[0].template.cmc))
    return classified


def _effective_cost(card: "CardInstance", medallion_count: int) -> int:
    """Compute effective mana cost after Medallion reductions."""
    from engine.cards import Color
    cmc = card.template.cmc or 0
    if medallion_count > 0 and (card.template.is_instant or card.template.is_sorcery):
        if Color.RED in card.template.color_identity:
            cmc = max(0, cmc - medallion_count)
    return cmc


def next_spell_to_cast(
    castable: List["CardInstance"],
    available_mana: int,
    has_reducer_on_board: bool,
    graveyard_spell_count: int,
    opponent_life: int,
    am_dead_next: bool,
    medallion_count: int = 0,
) -> Optional[Tuple["CardInstance", SpellRole, str]]:
    """Pick the next spell to cast from the sequenced hand.

    Returns (card, role, reasoning) or None if nothing should be cast.

    Key invariants:
      1. Enablers (reducer, fuel, draw, tutor, rebuy) before finishers
      2. Finishers only fire when all enablers are exhausted
      3. When dead next turn, cast everything in role order (desperation)
      4. Reducer creatures are deployed like any other reducer
    """
    sequenced = sequence_hand(castable)

    if not sequenced:
        return None

    # Separate by role
    by_role = {}
    for card, role in sequenced:
        by_role.setdefault(role, []).append(card)

    # Dead next turn — cast in role order (still sequence correctly)
    if am_dead_next:
        for card, role in sequenced:
            if _effective_cost(card, medallion_count) <= available_mana:
                return (card, role, f"Desperation — dead next turn, casting {role.name}")
        return None

    # Deploy reducer if not yet on board (includes creatures like Ral)
    if SpellRole.REDUCER in by_role and not has_reducer_on_board:
        for card in by_role[SpellRole.REDUCER]:
            if _effective_cost(card, medallion_count) <= available_mana:
                return (card, SpellRole.REDUCER, "Deploy cost reducer before chaining")

    # Cast fuel (rituals) — they generate mana for the rest
    if SpellRole.FUEL in by_role:
        for card in by_role[SpellRole.FUEL]:
            if _effective_cost(card, medallion_count) <= available_mana:
                return (card, SpellRole.FUEL, "Cast fuel to build mana for chain")

    # Cast draw (cantrips) — dig for more fuel
    if SpellRole.DRAW in by_role:
        for card in by_role[SpellRole.DRAW]:
            if _effective_cost(card, medallion_count) <= available_mana:
                return (card, SpellRole.DRAW, "Cast cantrip to dig for fuel")

    # Cast tutor — find missing pieces
    if SpellRole.TUTOR in by_role:
        for card in by_role[SpellRole.TUTOR]:
            if _effective_cost(card, medallion_count) <= available_mana:
                return (card, SpellRole.TUTOR, "Cast tutor to find combo pieces")

    # Cast rebuy — replay graveyard (only if GY has enough spells)
    if SpellRole.REBUY in by_role and graveyard_spell_count >= 3:
        for card in by_role[SpellRole.REBUY]:
            if _effective_cost(card, medallion_count) <= available_mana:
                return (card, SpellRole.REBUY,
                        f"Cast rebuy engine — {graveyard_spell_count} spells in graveyard")

    # Finisher — only if no other productive spells remain
    if SpellRole.FINISHER in by_role:
        # Check if there are any non-finisher, non-other spells we could cast
        # REBUY counts as productive even with empty GY (it adds storm and
        # may enable further chains after the flashback spells resolve)
        has_productive_nonfin = any(
            role not in (SpellRole.FINISHER, SpellRole.OTHER)
            and _effective_cost(card, medallion_count) <= available_mana
            for card, role in sequenced
        )
        if not has_productive_nonfin:
            for card in by_role[SpellRole.FINISHER]:
                if _effective_cost(card, medallion_count) <= available_mana:
                    return (card, SpellRole.FINISHER,
                            "All enablers exhausted — firing finisher")

    # Nothing productive to cast — hold and pass
    return None
