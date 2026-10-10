"""Warp is cast from the rules (CR 702.185a).

"Warp [cost]" means "You may cast this card from your hand by paying
[cost] rather than its mana cost" and "If this spell's warp cost was paid,
exile the permanent this spell becomes at the beginning of the next end
step. Its owner may cast this card after the current turn has ended for as
long as it remains exiled." Nothing in the rule asks for an artifact: the
engine required one to warp from hand and to cast the exiled card again,
and charged the warp cost for the re-cast, which the rule never allows
(the card is cast from exile for its mana cost).
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    c = CardInstance(template=card_db.get_card(name), owner=idx,
                     controller=idx, instance_id=game.next_instance_id(),
                     zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _islands(game, card_db, n):
    return [_put(game, card_db, "Island", "battlefield") for _ in range(n)]


def test_a_warp_spell_is_cast_for_its_warp_cost_with_no_artifact(card_db):
    game = _game()
    _islands(game, card_db, 2)                 # {1}{U}: the warp cost only
    riddler = _put(game, card_db, "Quantum Riddler", "hand")
    assert game.can_cast(0, riddler)
    assert game.cast_spell(0, riddler)
    assert riddler.zone == "stack"
    assert all(land.tapped for land in game.players[0].lands)


def test_a_warped_permanent_is_exiled_at_the_next_end_step(card_db):
    game = _game()
    _islands(game, card_db, 2)
    riddler = _put(game, card_db, "Quantum Riddler", "hand")
    assert game.cast_spell(0, riddler)
    game.resolve_stack()
    assert riddler.zone == "battlefield"
    game.end_of_turn_cleanup()
    assert riddler.zone == "exile"


def test_the_exiled_card_is_cast_for_its_mana_cost_after_this_turn(card_db):
    """Its owner may cast it from exile after the current turn has ended,
    for its mana cost: not this turn, and not for the warp cost."""
    game = _game()
    lands = _islands(game, card_db, 5)
    riddler = _put(game, card_db, "Quantum Riddler", "hand")
    for land in lands[2:]:
        land.tapped = True                     # only the warp cost is open
    assert game.cast_spell(0, riddler)
    game.resolve_stack()
    game.end_of_turn_cleanup()
    assert riddler.zone == "exile"
    for land in lands:
        land.tapped = False
    assert not game.can_cast(0, riddler)       # the current turn
    game.turn_number = 7                       # the owner's next turn
    assert game.can_cast(0, riddler)           # five lands: {3}{U}{U}
    for land in lands[2:]:
        land.tapped = True
    assert not game.can_cast(0, riddler)       # the warp cost is no option
