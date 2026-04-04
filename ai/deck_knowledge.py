"""Deck Composition Math — what a player knows about their deck.

Each player knows their full 60-card decklist (composition, not order).
This module provides hypergeometric draw probability calculations and
deck composition tracking that drives EV-based decisions.

Key principle: no hardcoded thresholds. All decisions use calculated
probabilities and expected values from known deck composition.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardInstance, CardTemplate


@dataclass
class DeckKnowledge:
    """What a player knows about their deck composition."""

    # Full 60-card decklist: card_name -> count
    full_decklist: Dict[str, int] = field(default_factory=dict)

    # Cards that have been seen (drawn, milled, exiled, etc.)
    # card_name -> count_seen
    seen_counts: Dict[str, int] = field(default_factory=dict)

    def record_seen(self, card_name: str):
        """Record that a copy of this card has been seen (drawn, revealed, etc.)."""
        self.seen_counts[card_name] = self.seen_counts.get(card_name, 0) + 1

    def record_returned(self, card_name: str):
        """Record that a card was returned to the library (e.g., bottom after mulligan)."""
        if card_name in self.seen_counts and self.seen_counts[card_name] > 0:
            self.seen_counts[card_name] -= 1

    @property
    def remaining(self) -> Dict[str, int]:
        """Cards still in library (known composition, unknown order)."""
        result = {}
        for name, total in self.full_decklist.items():
            remaining = total - self.seen_counts.get(name, 0)
            if remaining > 0:
                result[name] = remaining
        return result

    @property
    def deck_size(self) -> int:
        """Number of cards remaining in library."""
        return sum(self.remaining.values())

    def copies_remaining(self, card_name: str) -> int:
        """How many copies of a specific card are still in the library."""
        total = self.full_decklist.get(card_name, 0)
        seen = self.seen_counts.get(card_name, 0)
        return max(0, total - seen)

    def probability_of_drawing(self, card_name: str, draws: int) -> float:
        """Hypergeometric: P(drawing at least 1 copy in N draws).

        Uses the complement method: P(at least 1) = 1 - P(miss all draws).
        """
        copies = self.copies_remaining(card_name)
        total = self.deck_size
        if total == 0 or copies == 0 or draws <= 0:
            return 0.0
        if copies >= total:
            return 1.0
        # P(miss) = C(total-copies, draws) / C(total, draws)
        # Computed iteratively to avoid overflow
        p_miss = 1.0
        for i in range(min(draws, total)):
            p_miss *= (total - copies - i) / (total - i)
            if p_miss <= 0:
                return 1.0
        return 1.0 - p_miss

    def probability_of_drawing_any(self, card_names: List[str], draws: int) -> float:
        """P(drawing at least 1 copy of ANY of the named cards in N draws)."""
        total_copies = sum(self.copies_remaining(n) for n in card_names)
        total = self.deck_size
        if total == 0 or total_copies == 0 or draws <= 0:
            return 0.0
        if total_copies >= total:
            return 1.0
        p_miss = 1.0
        for i in range(min(draws, total)):
            p_miss *= (total - total_copies - i) / (total - i)
            if p_miss <= 0:
                return 1.0
        return 1.0 - p_miss

    def expected_lands_in_next(self, draws: int, land_names: Set[str] = None) -> float:
        """Expected number of lands in the next N draws."""
        if land_names is None:
            land_names = set()
            for name in self.full_decklist:
                # Heuristic: land cards typically don't have mana costs
                # This will be refined by the caller who knows card templates
                if name in self._land_names:
                    land_names.add(name)
        total_lands = sum(self.copies_remaining(n) for n in land_names)
        total = self.deck_size
        if total == 0:
            return 0.0
        return draws * (total_lands / total)

    def category_density(self, card_names: Set[str]) -> float:
        """Fraction of remaining library that matches the given card names."""
        total = self.deck_size
        if total == 0:
            return 0.0
        matching = sum(self.copies_remaining(n) for n in card_names)
        return matching / total

    # Cached land names (set by init_from_decklist)
    _land_names: Set[str] = field(default_factory=set)

    @classmethod
    def from_decklist(cls, decklist: Dict[str, int],
                      land_names: Set[str] = None) -> "DeckKnowledge":
        """Create DeckKnowledge from a decklist dict."""
        dk = cls(full_decklist=dict(decklist))
        if land_names:
            dk._land_names = land_names
        return dk

    @classmethod
    def from_game_state(cls, player, decklist: Dict[str, int]) -> "DeckKnowledge":
        """Create DeckKnowledge from a player's current game state.

        Marks all cards NOT in the library as 'seen'.
        """
        dk = cls(full_decklist=dict(decklist))

        # Identify land names from cards on battlefield
        dk._land_names = set()
        for card in player.battlefield:
            if card.template.is_land:
                dk._land_names.add(card.name)
        for name in decklist:
            # Also check library for land classification
            for card in player.library:
                if card.name == name and card.template.is_land:
                    dk._land_names.add(name)
                    break

        # Count cards in non-library zones as "seen"
        seen = {}
        for zone in [player.hand, player.graveyard, player.exile]:
            for card in zone:
                seen[card.name] = seen.get(card.name, 0) + 1
        for card in player.battlefield:
            seen[card.name] = seen.get(card.name, 0) + 1

        dk.seen_counts = seen
        return dk
