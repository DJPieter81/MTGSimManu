"""Saga entry bookkeeping (CR 714.2a).

"As a Saga enters the battlefield, its controller puts a lore counter on
it" — and the chapter whose number that counter reaches (chapter I)
triggers at once.  Later counters are added as the controller's precombat
main phase begins (CR 714.2b); that advance, the chapter effects, and the
final-chapter sacrifice live in `GameRunner._process_saga_chapters`.

This module owns the ENTRY half so the zone-transfer ETB fan-out and the
runner's fixture fallback (a Saga that reached the battlefield without
the funnel) attach chapter I through one code path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .cards import CardInstance
    from .game_state import GameState

LORE_COUNTER = "lore"


def is_saga(card: "CardInstance") -> bool:
    return "Saga" in (card.template.subtypes or [])


def saga_enters(game: "GameState", card: "CardInstance",
                controller: int) -> Optional[str]:
    """Put the first lore counter on an entering Saga and attach an
    ability-grant chapter I.  Returns the granted ability text (or None
    when chapter I is a one-shot effect or the card is not a Saga).

    Idempotent for a Saga that already carries a lore counter — the
    fan-out and the runner fallback may both reach a card.
    """
    if not is_saga(card):
        return None
    if card.other_counters is None:
        card.other_counters = {}
    if card.other_counters.get(LORE_COUNTER, 0) >= 1:
        return None
    from .oracle_parser import parse_saga_chapters, extract_granted_ability
    card.other_counters[LORE_COUNTER] = 1
    chapters = parse_saga_chapters(card.template.oracle_text or "")
    # Chapter I fires as the Saga enters; an ability-grant chapter I
    # attaches its ability now.  (Mana abilities granted this way are
    # already reflected in the land's produces_mana parse; the stored
    # text is inert for them.)
    granted = extract_granted_ability(chapters.get(1))
    if granted is not None:
        card.granted_abilities.append(granted)
        game.log.append(f"T{game.display_turn} P{controller+1}: "
                        f"{card.name} Ch.I: gains \"{granted}\"")
    return granted
