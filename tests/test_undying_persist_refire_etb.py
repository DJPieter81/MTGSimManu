"""Undying / Persist return must re-fire the creature's ETB.

When a creature returns to the battlefield via undying (CR 702.94) or
persist (CR 702.78) it is a NEW object entering the battlefield, so its
enter-the-battlefield triggers fire again. The `_creature_dies` undying and
persist branches re-added the instance but never invoked
`_handle_permanent_etb`, unlike `reanimate()` — so a returned Geralf's
Messenger dealt no life loss, a returned Kitchen Finks gained no life, etc.

Rule under test: the ETB pipeline is invoked for the returned creature.
Generic: every ETB creature × undying/persist. No card names in the engine.
"""
from __future__ import annotations

import random

from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from engine.cards import CardInstance, Keyword
from engine.permanent_effects import PermanentEffects


def _mk(game, db, name, owner, zone):
    t = db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=owner, controller=owner,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[owner],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _run(card_db, card_name, keyword):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    creature = _mk(game, card_db, card_name, 0, "battlefield")
    assert keyword in creature.keywords, f"fixture {card_name} must have {keyword}"

    etb_calls = []
    orig = game._handle_permanent_etb

    def _spy(card, controller, *a, **k):
        etb_calls.append(card)
        return orig(card, controller, *a, **k)

    game._handle_permanent_etb = _spy
    PermanentEffects._creature_dies(game, creature)
    return game, creature, etb_calls


def test_undying_return_refires_etb(card_db):
    game, creature, etb_calls = _run(card_db, "Geralf's Messenger",
                                     Keyword.UNDYING)
    assert creature in game.players[0].battlefield, "undying returns the creature"
    assert creature in etb_calls, (
        "undying return must invoke the ETB pipeline for the returned creature")


def test_persist_return_refires_etb(card_db):
    game, creature, etb_calls = _run(card_db, "Kitchen Finks",
                                     Keyword.PERSIST)
    assert creature in game.players[0].battlefield, "persist returns the creature"
    assert creature in etb_calls, (
        "persist return must invoke the ETB pipeline for the returned creature")
