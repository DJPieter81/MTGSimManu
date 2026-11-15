"""A hand attack is aimed at its caster only when binning has value.

"Target player reveals their hand. You choose a nonland card from it.
That player discards that card." may target its own caster, and a
reanimator does that to put a creature its reanimation spell can return
into the graveyard (`EVPlayer._self_fill_value`). The choice compared the
self-fill value with the opponent-side denial value
(`ai.hand_denial.hand_denial_value`). The denial value is the denied card's
value minus the average-card credit, so it is negative when the opponent's
hand holds nothing worth taking. A self-fill value of 0 then beat it, and
the spell hit its own hand with nothing to bin.

The defect: Grixis Reanimator goldfish s70001, turn 4. The hand held
Thoughtseize, Cling to Dust and Fatal Push, with no creature. Thoughtseize
targeted its own hand ("bin the reanimation target") and discarded Cling to
Dust. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_SELF
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone, controller=0):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, my_hand, opp_hand):
    g = GameState(rng=random.Random(0))
    for _ in range(3):
        _add(g, card_db, "Swamp", "battlefield")
    for n in my_hand:
        _add(g, card_db, n, "hand")
    for n in opp_hand:
        _add(g, card_db, n, "hand", controller=1)
    for _ in range(10):
        _add(g, card_db, "Swamp", "library")
        _add(g, card_db, "Island", "library", controller=1)
    g.players[0].deck_name = "Grixis Reanimator"
    g.players[1].deck_name = "Azorius Control"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 7
    return g


def _targets(g):
    from ai.ev_player import EVPlayer
    player = EVPlayer(player_idx=0, deck_name="Grixis Reanimator",
                      rng=random.Random(0))
    seize = next(c for c in g.players[0].hand if c.name == "Thoughtseize")
    return player._choose_targets(g, seize)


def test_nothing_to_bin_is_never_a_reason_to_target_the_caster(card_db):
    g = _game(card_db, ["Thoughtseize", "Cling to Dust", "Fatal Push"],
              ["Island", "Island", "Plains"])
    assert PLAYER_TARGET_SELF not in _targets(g)


def test_a_returnable_creature_is_still_binned_from_the_casters_hand(
        card_db):
    """The reanimator line stays: a creature the reanimation spell in
    hand can return, an empty graveyard, an opponent with nothing to
    take."""
    g = _game(card_db, ["Thoughtseize", "Archon of Cruelty", "Persist"],
              ["Island", "Island", "Plains"])
    assert _targets(g) == [PLAYER_TARGET_SELF]
