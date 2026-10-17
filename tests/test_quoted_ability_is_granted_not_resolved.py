"""A quoted ability in a loyalty line is an ability the line GRANTS — to an
emblem, a token or a permanent — not an effect of the line (CR 113.1a,
114.4). "You get an emblem with 'At the beginning of your end step, create
three 1/1 Cats'" creates an emblem; it does not create the Cats now.

Rules pinned:
* a loyalty line whose only effect is inside quotes is not typed as an
  executable clause (emblems are unmodelled, so the line is refused);
* the clause typer reads the line with its quoted abilities removed, so
  no emblem line anywhere in the pool resolves its quoted ability;
* a line whose own effect sits outside the quotes still types (an
  emblem line that "then creates" tokens runs its token half).
Oracle strings are fixture carriers only.
"""
from __future__ import annotations

import re

from engine.cards import LoyaltyEffectKind
from engine.oracle_parser import parse_loyalty_abilities


def _typed(card_db, text):
    return card_db._type_loyalty_clauses("Fixture Walker", parse_loyalty_abilities(text, 4))


def test_an_emblem_lines_quoted_ability_is_not_its_effect(card_db):
    ab = _typed(card_db, '[−6]: You get an emblem with "At the beginning of your '
                         'upkeep, draw a card."')["minus"]
    assert ab.effect_kind is not LoyaltyEffectKind.CLAUSE


def test_no_emblem_line_in_the_pool_resolves_its_quoted_ability(card_db):
    wrong = [t.name for t in card_db.cards.values()
             for ab in list((t.loyalty_abilities or {}).values())
             + list((t.back_face_loyalty_abilities or {}).values())
             if ab.effect_kind is LoyaltyEffectKind.CLAUSE
             and re.sub(r'"[^"]*"', '', ab.text.lower()).strip(" .")
             == "you get an emblem with"]
    assert wrong == []


def test_an_effect_outside_the_quotes_still_types(card_db):
    ab = _typed(card_db, '[+1]: Draw a card.')["plus"]
    assert ab.effect_kind is LoyaltyEffectKind.CLAUSE
