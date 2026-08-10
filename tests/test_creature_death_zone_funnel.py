"""Creature death must route the battlefield→graveyard zone mutation
through zone_manager.move_card — the single sanctioned funnel.

Rule under test
---------------
CR 701.12 "Destroy": when a creature dies, its battlefield→graveyard
zone change must go through ``ZoneManager.move_card``.  Any raw
``.zone =`` assignment in the normal death path bypasses the funnel's
bookkeeping (list mutation, cleanup, logging), making it impossible to
hook the transition in a single place for future replacements/triggers.

Class size: every creature that dies (SBA 704.5g/h, removal, combat
damage) hits this path — the entire creature-death class, every game.

Fix: ``PermanentEffects._creature_dies`` must call
``game.zone_mgr.move_card(game, creature, "battlefield", "graveyard")``
for the normal (non-undying, non-persist) death path instead of
assigning ``.zone`` and appending to the graveyard list directly.

Fixture carrier: a vanilla 2/2 creature with no replacement effects.
"""
from __future__ import annotations

import random
from unittest.mock import patch

from engine.cards import CardInstance, CardTemplate, Keyword
from engine.cards import ManaCost, CardType
from engine.game_state import GameState


_VANILLA = CardTemplate(
    name="Test Fixture: Vanilla Creature",
    card_types=[CardType.CREATURE],
    mana_cost=ManaCost(generic=2),
    supertypes=[], subtypes=["Test"],
    power=2, toughness=2, loyalty=None,
    keywords=set(), abilities=[], color_identity=set(),
    produces_mana=[], enters_tapped=False,
    oracle_text="",
    tags=set(),
)


def _on_battlefield(game, template, controller):
    card = CardInstance(
        template=template, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def test_creature_death_routes_through_zone_manager(card_db):
    """Normal creature death must call zone_manager.move_card for
    the battlefield→graveyard transition rather than raw .zone= assignment."""
    game = GameState(rng=random.Random(0))
    creature = _on_battlefield(game, _VANILLA, controller=0)

    with patch.object(game.zone_mgr, 'move_card', wraps=game.zone_mgr.move_card) as mock:
        game._creature_dies(creature)

    zone_changes = [(c.args[2], c.args[3]) for c in mock.call_args_list]
    assert ('battlefield', 'graveyard') in zone_changes, (
        "_creature_dies did not call zone_manager.move_card('battlefield', 'graveyard') "
        "— raw .zone= assignment bypasses the funnel; zone_changes seen: "
        + repr(zone_changes)
    )


def test_creature_death_lands_in_graveyard(card_db):
    """After _creature_dies, creature must be in the owner's graveyard
    and absent from the battlefield — regardless of which code path moves it."""
    game = GameState(rng=random.Random(0))
    creature = _on_battlefield(game, _VANILLA, controller=0)

    game._creature_dies(creature)

    assert creature in game.players[0].graveyard, "creature not in graveyard after death"
    assert creature not in game.players[0].battlefield, "creature still on battlefield"
    assert creature.zone == "graveyard", (
        f"creature.zone is {creature.zone!r}, expected 'graveyard'"
    )


def test_creature_death_increments_died_this_turn(card_db):
    """_creature_dies must increment creatures_died_this_turn on the
    controller's PlayerState (used by revolt, morbid, etc.)."""
    game = GameState(rng=random.Random(0))
    creature = _on_battlefield(game, _VANILLA, controller=0)
    before = game.players[0].creatures_died_this_turn

    game._creature_dies(creature)

    assert game.players[0].creatures_died_this_turn == before + 1
