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
    player holds open mana to cast on the opponent's turn.

    "Instant-speed" is castability, not card type: a plain instant
    (`is_instant`) OR any card with flash (`has_flash`) can be held up.
    Gating on `is_instant` alone silently dropped flash removal that is
    not a plain instant — evoke elementals (Solitude / Subtlety /
    Endurance) and flash enchantment removal (Leyline Binding) — so the
    holdback pricer tapped out the mana needed to cast them on the
    opponent's turn. (Root cause: docs/diagnostics/
    2026-08-20_domain_zoo_overperformance_root_cause.md.)
    """
    instant_speed = (getattr(template, "is_instant", False)
                     or getattr(template, "has_flash", False))
    if not instant_speed:
        return False
    tags = getattr(template, "tags", None) or set()
    return bool(_HELD_INTERACTION_TAGS & set(tags))
