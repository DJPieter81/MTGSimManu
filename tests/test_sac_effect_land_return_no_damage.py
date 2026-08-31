"""A sacrifice ability that returns land cards from the graveyard to
the battlefield does not, by itself, deal damage.

Live bug this pins (replay audit, docs artifact "Aftermath Analyst's
Phantom Damage" — Amulet Titan vs Living End, seed 57007):
`GameRunner._resolve_sac_effect`'s generic land-return branch
(``'return' in effect_text and 'land' in effect_text``) dealt 1
damage to the opponent PER LAND RETURNED, with no basis in any card's
oracle text — Aftermath Analyst's real ability ("{3}{G}, Sacrifice
this creature: Return all land cards from your graveyard to the
battlefield tapped.") has no damage clause at all. The branch fired
twice in one game, draining 6 life the card does not grant and
pulling the win a turn earlier than the real board state supported.

Card names below are fixture carriers; the rule under test is the
mechanic (land-return sac effects don't deal damage unless their own
oracle text says so — none in the current pool do).
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_runner import GameRunner
from engine.game_state import GameState


def _graveyard_land(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _battlefield_creature(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    return card


def test_returning_lands_from_graveyard_deals_no_damage(card_db):
    game = GameState(rng=random.Random(0))
    _graveyard_land(game, card_db, "Forest", 0)
    _graveyard_land(game, card_db, "Mountain", 0)
    sac_source = _battlefield_creature(game, card_db, "Aftermath Analyst", 0)

    opp_life_before = game.players[1].life

    runner = GameRunner(card_db, rng=random.Random(0))
    runner._resolve_sac_effect(
        game, 0, sac_source,
        "return all land cards from your graveyard to the battlefield tapped."
    )

    assert game.players[1].life == opp_life_before, (
        f"opponent life changed from {opp_life_before} to "
        f"{game.players[1].life} — a land-return sac effect must not "
        f"deal damage on its own"
    )
    bf_land_names = {c.name for c in game.players[0].battlefield
                     if c.template.is_land}
    assert bf_land_names == {"Forest", "Mountain"}, (
        f"both graveyard lands must still return to the battlefield "
        f"(only the fabricated damage is the bug): {bf_land_names}"
    )
