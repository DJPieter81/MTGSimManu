"""Revolt (CR 702.139): "a permanent left the battlefield under your
control this turn". This condition is satisfied by ANY permanent leaving
its controller's battlefield to any zone — a fetchland crack
(land -> graveyard), a sacrifice, a bounce, an exile, or a creature death —
not only by a creature dying.

Rule under test
---------------
The engine must track a per-player, per-turn counter
``permanents_left_battlefield_this_turn`` that the ZoneManager funnel
(``move_card``) advances exactly once whenever a permanent leaves its
controller's battlefield. Revolt-conditioned effects (Fatal Push's
mana-value cap) read this counter, so revolt turns on after ANY permanent
leaves — the fetchland-crack case being the canonical one that a
creature-death-only signal misses.

Class size: every permanent that leaves any controller's battlefield this
turn — fetch cracks, sacrifice outlets, bounce, exile, blink, combat and
removal deaths. The whole "left the battlefield" class, every game.

Fixture carriers: a vanilla land and a vanilla mana-value-4 creature.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState


_VANILLA_LAND = CardTemplate(
    name="Test Fixture: Vanilla Land",
    card_types=[CardType.LAND],
    mana_cost=ManaCost(),
    supertypes=[], subtypes=[],
    power=None, toughness=None, loyalty=None,
    keywords=set(), abilities=[], color_identity=set(),
    produces_mana=["G"], enters_tapped=False,
    oracle_text="", tags=set(),
)

_MV4_CREATURE = CardTemplate(
    name="Test Fixture: MV4 Creature",
    card_types=[CardType.CREATURE],
    mana_cost=ManaCost(generic=3, white=1),  # mana value 4
    supertypes=[], subtypes=["Test"],
    power=3, toughness=3, loyalty=None,
    keywords=set(), abilities=[], color_identity=set(),
    produces_mana=[], enters_tapped=False,
    oracle_text="", tags=set(),
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


def test_revolt_active_after_any_permanent_leaves_battlefield(card_db):
    """A land leaving the battlefield (fetchland crack: land -> graveyard)
    must advance permanents_left_battlefield_this_turn even though no
    creature died."""
    game = GameState(rng=random.Random(0))
    land = _on_battlefield(game, _VANILLA_LAND, controller=0)

    assert game.players[0].permanents_left_battlefield_this_turn == 0
    assert game.players[0].creatures_died_this_turn == 0

    game.zone_mgr.move_card(game, land, "battlefield", "graveyard",
                            cause="fetchland sacrifice")

    assert game.players[0].permanents_left_battlefield_this_turn == 1, (
        "a permanent leaving the battlefield must advance the revolt counter"
    )
    # And it must NOT be miscounted as a creature death (morbid stays off).
    assert game.players[0].creatures_died_this_turn == 0, (
        "a land leaving the battlefield is not a creature death"
    )


def test_permanent_left_counter_credits_controller_not_opponent(card_db):
    """The counter belongs to the permanent's controller — the opponent's
    revolt does not turn on when your permanent leaves."""
    game = GameState(rng=random.Random(0))
    land = _on_battlefield(game, _VANILLA_LAND, controller=0)

    game.zone_mgr.move_card(game, land, "battlefield", "graveyard",
                            cause="sacrifice")

    assert game.players[0].permanents_left_battlefield_this_turn == 1
    assert game.players[1].permanents_left_battlefield_this_turn == 0


def test_permanent_left_counter_resets_at_turn_boundary(card_db):
    """The revolt counter is a per-turn tally — it clears at the turn-tracking
    reset so a prior turn's departure does not leak revolt into a later turn."""
    game = GameState(rng=random.Random(0))
    land = _on_battlefield(game, _VANILLA_LAND, controller=0)
    game.zone_mgr.move_card(game, land, "battlefield", "graveyard")
    assert game.players[0].permanents_left_battlefield_this_turn == 1

    game.players[0].reset_turn_tracking()
    assert game.players[0].permanents_left_battlefield_this_turn == 0


def test_fatal_push_kills_mv4_with_revolt(card_db):
    """Fatal Push's mv cap is 2 with no revolt and 4 once any permanent has
    left the battlefield this turn, so an mv-4 creature it could not destroy
    becomes a legal kill after a fetchland crack."""
    from engine.card_effects import (
        _fatal_push_mv_max,
        _FATAL_PUSH_BASE_MV,
        _FATAL_PUSH_REVOLT_MV,
    )

    game = GameState(rng=random.Random(0))
    mv4 = _on_battlefield(game, _MV4_CREATURE, controller=1)
    assert mv4.template.cmc == 4

    # No revolt yet: cap is the base (2), below the mv-4 target.
    cap_before = _fatal_push_mv_max(game, None, 0, None)
    assert cap_before == _FATAL_PUSH_BASE_MV
    assert mv4.template.cmc > cap_before, (
        "without revolt Fatal Push must not reach an mv-4 creature"
    )

    # A permanent leaves controller 0's battlefield (fetchland crack).
    land = _on_battlefield(game, _VANILLA_LAND, controller=0)
    game.zone_mgr.move_card(game, land, "battlefield", "graveyard",
                            cause="fetchland sacrifice")

    cap_after = _fatal_push_mv_max(game, None, 0, None)
    assert cap_after == _FATAL_PUSH_REVOLT_MV
    assert mv4.template.cmc <= cap_after, (
        "with revolt on, Fatal Push must reach the mv-4 creature"
    )
