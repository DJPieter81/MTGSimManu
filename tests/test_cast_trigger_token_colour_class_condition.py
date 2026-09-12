"""A cast-triggered token whose condition names a COLOUR CLASS ("whenever
you cast a colorless spell") fires for a spell of that class (CR 603.2).

`parse_cast_trigger_token` generalised the trigger across spell TYPE
(artifact, instant or sorcery, noncreature, ordinal) and returned no
trigger for any other qualifier, so "colorless spell" typed as nothing:
Glaring Fleshraker made zero Spawn in every Broodscale and Eldrazi Tron
replay (2026-09-08). Colourlessness is a property of the cast spell
(CR 105.2c — no colour, devoid included), read off the spell's colours
exactly as the type qualifiers are read off its card types.

Class: 12 pool cards with the colorless-cast condition. Card names are
fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    return card


def test_the_colour_class_condition_is_typed():
    from engine.oracle_parser import parse_cast_trigger_token
    spec = parse_cast_trigger_token(
        "Whenever you cast a colorless spell, create a 0/1 colorless Eldrazi "
        "Spawn creature token with \"Sacrifice this token: Add {C}.\"")
    assert spec is not None and spec["spell_types"] == frozenset({"colorless"})


def test_a_colorless_cast_makes_the_token_and_a_coloured_cast_does_not(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    source = _add(game, card_db, "Glaring Fleshraker", 0, "battlefield")
    for _ in range(5):
        _add(game, card_db, "Mountain", 0, "battlefield")
    _add(game, card_db, "Forest", 0, "battlefield")

    def _creatures():
        return len(game.players[0].creatures)

    before = _creatures()
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")            # red
    assert CastManager.cast_spell(game, 0, bolt, [-1])
    assert _creatures() == before, "a coloured spell must not trigger the colorless condition"
    game.stack.pop()

    devoid = _add(game, card_db, "Basking Broodscale", 0, "hand")      # devoid: colorless
    assert CastManager.cast_spell(game, 0, devoid, [])
    assert _creatures() == before + 1, (
        "a colorless (devoid) spell must create the Spawn on cast")
    game.stack.pop()

    rock = _add(game, card_db, "Mind Stone", 0, "hand")                # colorless artifact
    assert CastManager.cast_spell(game, 0, rock, [])
    assert _creatures() == before + 2
