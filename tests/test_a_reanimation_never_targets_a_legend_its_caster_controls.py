"""A reanimation never targets a legendary creature its caster controls.

The legend rule (CR 704.5j): when a player controls two or more legendary
permanents with the same name, they choose one and put the rest into
their owners' graveyards. Returning a second copy of a legendary creature
its caster already controls returns nothing that stays. The main phase
already keeps such a cast out of the spells it considers
(`EVPlayer._filter_legend_rule`); the reanimation target pick did not ask
the same owner.

The defect: Goryo's Vengeance goldfish s70000, turn 5. With the blinked
Atraxa in play, Goryo's Vengeance returned a second Atraxa. The legend
rule removed one, and the hasty copy was exiled at end of turn. Card names
are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone, controller=0):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = False
    getattr(game.players[controller], zone).append(c)
    return c


def _targets(card_db, graveyard):
    from ai.ev_player import EVPlayer
    g = GameState(rng=random.Random(0))
    for _ in range(3):
        _add(g, card_db, "Swamp", "battlefield")
    _add(g, card_db, "Atraxa, Grand Unifier", "battlefield")
    goryo = _add(g, card_db, "Goryo's Vengeance", "hand")
    found = {n: _add(g, card_db, n, "graveyard") for n in graveyard}
    g.players[0].deck_name = "Goryo's Vengeance"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    player = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                      rng=random.Random(0))
    return player._pick_targets(g, goryo), found


def test_the_other_legend_is_returned_not_a_second_copy(card_db):
    targets, found = _targets(card_db, ["Atraxa, Grand Unifier",
                                        "Griselbrand"])
    assert targets == [found["Griselbrand"].instance_id]


def test_only_a_copy_of_a_controlled_legend_is_no_target(card_db):
    targets, _found = _targets(card_db, ["Atraxa, Grand Unifier"])
    assert targets == []
