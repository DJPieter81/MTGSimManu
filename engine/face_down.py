"""Face-down permanents (CR 708).

A card put onto the battlefield face down -- manifested (CR 701.40a), one of
the two cards manifest dread looks at (CR 701.62a) -- has no characteristics
but those its face-down status gives it: it is a 2/2 creature with no name,
mana cost, colour, subtypes, supertypes or text (CR 708.2a). Once it leaves
the battlefield it is the card itself again, revealed (CR 708.9).

While it is on the battlefield the permanent shows the face-down template
below, the way a modal double-faced card played as its back face shows that
face (`CardInstance._front_template`); the zone funnel puts the card's own
template back as it leaves (`ZoneManager._cleanup_leaving_battlefield`).
Every characteristic the engine reads comes off the template, so the
face-down permanent has no ability of its card: no keyword, no enter or
dies trigger, no activated ability.
"""
from __future__ import annotations

from typing import Any, Optional

from .cards import CardTemplate, CardType
from .mana import ManaCost

# CR 708.2a: one set of characteristics for every face-down permanent.
_FACE_DOWN: Optional[CardTemplate] = None

# How a log or replay board shows a permanent with no name. Display only:
# every rule reads the empty name (CR 708.2a).
FACE_DOWN_LABEL = "face-down 2/2"


def shown_name(card: Any) -> str:
    """`card`'s name as a log shows it: its name, or `FACE_DOWN_LABEL` for
    a face-down permanent, which has none."""
    if getattr(card, "face_down", False) and not card.name:
        return FACE_DOWN_LABEL
    return card.name


def face_down_template(card: Any) -> CardTemplate:
    """The characteristics `card` has on the battlefield face down
    (CR 708.2a): a 2/2 creature and nothing else. The same for every card."""
    global _FACE_DOWN
    if _FACE_DOWN is None:
        _FACE_DOWN = CardTemplate(
            name="", card_types=[CardType.CREATURE], mana_cost=ManaCost(),
            power=2, toughness=2, oracle_text="", tags={"creature"})
    return _FACE_DOWN
