"""The cast-trigger census records a cast trigger that no owner performs,
and only such a trigger (CR 603.2).

`603.2/cast_trigger_unresolved` is the census row of a typed cast head the
cast carrier does not take. A cast-triggered counter ("whenever a player
casts a spell, if no mana was spent to cast it, counter that spell") is
performed by its own owner from the typed `cast_counter` field (unit LK),
so it is no unresolved trigger.

The defect: the census reported every cast-triggered counter as
unresolved, so Vexing Bauble (Boros Energy, Eldrazi Ramp SB) was a row in
every audited arm although it counters the spells it names.

Card names are fixture carriers: 6 pool cards print a cast-triggered
counter.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(4):
        _add(game, card_db, "Mountain", 0)
    return game


@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _census(audit):
    return {(f["rule"], f["key"]) for f in audit.drain()
            if f.get("kind") == "census"}


def test_a_cast_triggered_counter_its_owner_performs_is_no_census_row(
        card_db, audit):
    game = _game(card_db)
    _add(game, card_db, "Vexing Bauble", 1)
    spell = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert game.cast_spell(0, spell, [-2])
    assert ("603.2/cast_trigger_unresolved", "Vexing Bauble") \
        not in _census(audit)


def test_a_cast_trigger_no_owner_performs_is_still_a_census_row(card_db,
                                                                audit):
    """Pinnacle Emissary's token carries a static ability no token
    instruction makes yet: its cast trigger is untaken, and recorded."""
    game = _game(card_db)
    _add(game, card_db, "Pinnacle Emissary", 0)
    spell = _add(game, card_db, "Welding Jar", 0, "hand")
    assert game.cast_spell(0, spell, [])
    assert ("603.2/cast_trigger_unresolved", "Pinnacle Emissary") \
        in _census(audit)
