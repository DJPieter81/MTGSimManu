"""A land's haste-granting activation is a parse-once typed field
(`CardTemplate.grants_haste_activation`), never an oracle re-scan.

Primeval Titan's search once ranked the lands it fetched inside the engine
(`card_effects._primeval_titan_search`: bounce lands, then lands with a
damage ability, then haste-granting lands, with a haste land swapped in for
a second tapped mana land). Unit TU retired that handler: which lands a
search finds is its searcher's choice (CR 701.19a), the AI's one land
valuation (`ai.activation_ev._choose_land_delivery`), pinned in
`tests/test_a_search_finds_the_cards_its_description_admits.py`. The typed
field stays, read by the effect-spec equivalence row.
"""
from __future__ import annotations

_HASTE_LAND = "Hanweir Battlements // Hanweir, the Writhing Township"
_TAPPED_MANA_LAND = "Crumbling Vestige"


def test_grants_haste_activation_is_a_parse_once_typed_field(card_db):
    """The field is populated from the activated-ability classify pass,
    never by re-scanning oracle text at runtime."""
    assert card_db.get_card(_HASTE_LAND).grants_haste_activation is True
    assert card_db.get_card(_TAPPED_MANA_LAND).grants_haste_activation is False
    assert card_db.get_card("Mountain").grants_haste_activation is False
