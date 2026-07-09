"""Central card-class registry.

Structural finding #2 (docs/proposals/2026-07-09_structural_findings.md):
scorers kept private allowlists of "cards that count" — the holdback
pricer matched removal|counterspell but not the cast-lock class, the
sideboard scorer had no cast-rate-denial family, the improvise gate
trusted a cache with silent gaps. Each miss shipped as a bug.

This module is the single home for class-membership predicates.
Membership derives from tags (which are themselves oracle-derived) and
card properties — never card names. Consumers import the predicate;
adding a mechanic class to the game is ONE edit here.

Growth path (add as consumers convert): hate-vs-mechanic classes
(chain / graveyard / artifact), chain-component class, blink class.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.cards import CardTemplate

# Tags whose holders are worth keeping mana open for on the
# opponent's turn: counterspells, removal, and turn-scoped cast-locks
# ('silence' — set from the "can't cast spells this turn" oracle
# clause). One set, every holdback/response consumer.
_HELD_INTERACTION_TAGS = frozenset({"counterspell", "removal", "silence"})


def is_held_interaction(template: "CardTemplate") -> bool:
    """True when the card is instant-speed interaction — something a
    player holds open mana to cast on the opponent's turn."""
    if not getattr(template, "is_instant", False):
        return False
    tags = getattr(template, "tags", None) or set()
    return bool(_HELD_INTERACTION_TAGS & set(tags))
