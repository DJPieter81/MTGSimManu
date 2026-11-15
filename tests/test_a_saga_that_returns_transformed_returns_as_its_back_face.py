"""A Saga that exiles itself and returns transformed returns as its back face.

CR 714.2b, 712, 400.7: "Exile this Saga, then return it to the battlefield
transformed under your control" exiles the card -- it leaves the battlefield,
so it keeps nothing an effect gave it as a permanent -- and returns it as a
new object with its back face up. The engine owns that one shape
(`oracle_resolver._transform_permanent(..., returns_as_new_object=True)`,
through the zone funnel).

The legacy chapter pass instead removed the Saga from the battlefield list
by hand, appended it to exile with no zone-funnel cleanup and created a
4/4 haste token in its place. So a colour a Leyline of the Guildpact had set
on it followed it into exile (the rules auditor's
`400.7/effect_characteristic_off_battlefield`, six times in the
2026-11-07 n=60 matrix, Domain Zoo vs Boros Ponza s79500), and the back
face never existed. 28 pool Sagas print the sentence. Card names are
fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine import rules_audit
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

SAGA = "The Legend of Roku // Avatar Roku"


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    rules_audit.set_context(seed=1, deck1="A", deck2="B")
    yield rules_audit
    rules_audit.reset()


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _add(game, card_db, name, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[0], zone).append(card)
    return card


def _final_chapter(game, card_db, saga):
    from engine.game_runner import GameRunner
    from engine.saga import LORE_COUNTER
    saga.other_counters[LORE_COUNTER] = 2
    GameRunner(card_db)._process_saga_chapters(game, 0)


def test_the_saga_returns_as_its_own_back_face(card_db):
    game = _game(card_db)
    saga = _add(game, card_db, SAGA)
    _final_chapter(game, card_db, saga)
    assert saga.zone == "battlefield" and saga.is_transformed
    assert not [c for c in game.players[0].battlefield
                if getattr(c, "is_token", False)]


def test_it_is_a_new_object_that_keeps_nothing_from_before(audit, card_db):
    game = _game(card_db)
    _add(game, card_db, "Leyline of the Guildpact")
    saga = _add(game, card_db, SAGA)
    game.continuous_effects.recalculate(game)
    entry = saga.battlefield_entry_seq
    _final_chapter(game, card_db, saga)
    game.continuous_effects.recalculate(game)
    assert saga.battlefield_entry_seq > entry
    rules = {f["rule"] for f in audit.drain() if f["kind"] == "violation"}
    assert "400.7/effect_characteristic_off_battlefield" not in rules
