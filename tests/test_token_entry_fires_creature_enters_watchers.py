"""A creature token entering the battlefield is a permanent entering —
it must fire "whenever another creature you control enters" watchers.

Rule (CR 603.6a / 111.10): a token entering the battlefield is a
permanent-entering event, identical to a nontoken creature for trigger
purposes. Every "whenever a[nother] creature you control enters" payoff
(Guide of Souls, Impact Tremors, Cruel Celebrant, Zulaport Cutthroat,
Ocelot Pride, Cathars' Crusade, ...) must see the token.

The bug: create_token built the token, called enter_battlefield() (pure
state), and appended it — but never called _handle_permanent_etb, so no
ETB trigger or creature-enters watcher fired for tokens (the undying/
persist re-entry paths in the same file DO call it). Every token was
invisible to these payoffs (audit: Boros Energy vs 4c Omnath, s55622 —
Guide of Souls never gained life/energy off Cat/Elemental/Warrior
tokens; every trigger came from a hardcast creature).

Card names are fixture carriers; the rule is that token entry fires
creature-enters watchers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState


def _bf(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    game.players[controller].battlefield.append(c)
    if t.is_creature:
        game.players[controller].creatures.append(c)
    return c


def test_token_entry_triggers_creature_enters_lifegain_watcher(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    watcher = _bf(game, card_db, "Guide of Souls", 0)
    assert watcher.template.has_another_creature_enters_trigger

    life_before = game.players[0].life
    energy_before = game.players[0].energy_counters

    # Create one creature token under the watcher's controller.
    game.create_token(0, "creature", count=1, power=1, toughness=1)

    assert game.players[0].life == life_before + 1, (
        "Guide of Souls must gain 1 life when a creature token enters "
        f"(life {game.players[0].life}, expected {life_before + 1})"
    )
    assert game.players[0].energy_counters == energy_before + 1, (
        "Guide of Souls must get 1 energy when a creature token enters "
        f"(energy {game.players[0].energy_counters}, expected {energy_before + 1})"
    )


def test_multiple_tokens_each_trigger_the_watcher(card_db):
    """N tokens entering fire the watcher N times."""
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    _bf(game, card_db, "Guide of Souls", 0)
    life_before = game.players[0].life

    game.create_token(0, "creature", count=3, power=1, toughness=1)

    assert game.players[0].life == life_before + 3, (
        "three tokens must fire the creature-enters watcher three times "
        f"(life {game.players[0].life}, expected {life_before + 3})"
    )
