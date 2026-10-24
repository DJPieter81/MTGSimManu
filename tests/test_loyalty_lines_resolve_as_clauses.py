"""A loyalty ability's effect resolves like any other effect text
(CR 606.1: a loyalty ability is an activated ability; CR 608.2).

Printed loyalty lines that match none of the dedicated `LoyaltyEffectKind`
branches are typed once at load as a clause (a template built from the
line's own text by the card database's parser pipeline). When the shared
clause owner (`engine/clause_resolver.py`) can run that clause, the line is
`LoyaltyEffectKind.CLAUSE` and resolves through it; otherwise it stays
UNCLASSIFIED and is refused before its loyalty is paid.

Card names are fixture carriers only.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance, LoyaltyEffectKind
from engine.game_state import GameState, Phase
from engine.planeswalker_manager import PlaneswalkerManager

TOKEN_WALKER = "Grist, the Hunger Tide"        # [+1]: create a 1/1 Insect token …
EMBLEM_WALKER = "Kaito, Bane of Nightmares"     # [+1]: you get an emblem … (no handler)


def _put(game, card_db, name, controller, zone, loyalty=None):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        if loyalty is not None:
            c.loyalty_counters = loyalty
    getattr(game.players[controller], zone).append(c)
    return c


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def test_a_loyalty_line_with_a_resolvable_clause_is_typed_as_a_clause(card_db):
    ab = card_db.get_card(TOKEN_WALKER).loyalty_abilities["plus"]
    assert ab.effect_kind is LoyaltyEffectKind.CLAUSE
    assert ab.clause is not None
    assert ab.clause.oracle_text.lower().startswith("create a 1/1")


def test_a_clause_loyalty_line_resolves_through_the_clause_owner(card_db):
    game = _game()
    for _ in range(5):
        _put(game, card_db, "Forest", 0, "library")
    walker = _put(game, card_db, TOKEN_WALKER, 0, "battlefield", loyalty=3)
    before = len(game.players[0].creatures)
    assert PlaneswalkerManager.activate_planeswalker(game, 0, walker, "plus")
    assert walker.loyalty_counters == 4
    assert len(game.players[0].creatures) == before + 1


def test_a_line_no_clause_handler_accepts_stays_refused_and_unpaid(card_db):
    ab = card_db.get_card(EMBLEM_WALKER).loyalty_abilities["plus"]
    assert ab.effect_kind is LoyaltyEffectKind.UNCLASSIFIED
    game = _game()
    walker = _put(game, card_db, EMBLEM_WALKER, 0, "battlefield", loyalty=4)
    assert not PlaneswalkerManager.activate_planeswalker(game, 0, walker, "plus")
    assert walker.loyalty_counters == 4


def test_clause_lines_are_offered_to_the_ai_menu(card_db):
    game = _game()
    walker = _put(game, card_db, TOKEN_WALKER, 0, "battlefield", loyalty=3)
    assert "plus" in PlaneswalkerManager.resolvable_ability_slots(walker)


def test_the_clause_typing_covers_a_class_of_loyalty_lines(card_db):
    clause_lines = [
        (t.name, slot)
        for t in card_db.cards.values()
        for attr in ("loyalty_abilities", "back_face_loyalty_abilities")
        for slot, a in (getattr(t, attr, None) or {}).items()
        if a.effect_kind is LoyaltyEffectKind.CLAUSE
    ]
    assert len(clause_lines) >= 100, len(clause_lines)
