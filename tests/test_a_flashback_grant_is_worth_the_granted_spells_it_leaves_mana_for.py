"""A graveyard flashback grant is worth the granted spells it leaves mana for.

"Each instant and sorcery card in your graveyard gains flashback until end
of turn" lets those cards be cast this turn for their mana cost (CR
702.34a). The grant lasts only this turn, so its whole value is the
granted spells the player can still pay for after the grant resolves.
With no graveyard spell affordable from the mana left, the grant does
nothing, and the card is spent for nothing.

The gate (`ai.ev_evaluator._enumerate_this_turn_signals`, the
`flashback_combo_with_gy_fuel` signal) asked only whether the graveyard
held an instant or sorcery. The defect: Ruby Storm goldfish s70000, turn 4.
Storm had 3 mana and Wrenn's Resolve in the graveyard; Past in Flames,
costing 3 with Ral's reduction, was cast at 0.00 and left 0 mana. The same
happened on turns 5 and 7, and the kill came on turn 8. Card names are
fixture carriers.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase

SIGNAL = "flashback_combo_with_gy_fuel"


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


def _game(card_db, mountains, graveyard):
    g = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(g, card_db, "Mountain", "battlefield")
    pif = _add(g, card_db, "Past in Flames", "hand")
    for n in graveyard:
        _add(g, card_db, n, "graveyard")
    g.players[0].deck_name = "Ruby Storm"
    g.players[1].deck_name = "Boros Energy"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 4
    g.players[0].lands_played_this_turn = 1
    g._global_storm_count = 0
    return g, pif


def _signals(g, card):
    return _enumerate_this_turn_signals(card, snapshot_from_game(g, 0), g, 0)


def test_no_granted_spell_affordable_after_the_grant_is_no_signal(card_db):
    """4 mana: the grant costs 4, and every granted ritual costs 2."""
    g, pif = _game(card_db, 4, ["Desperate Ritual", "Pyretic Ritual"])
    assert SIGNAL not in _signals(g, pif)


def test_a_granted_spell_the_mana_left_pays_for_is_a_signal(card_db):
    """6 mana: 2 left after the grant pays a granted ritual."""
    g, pif = _game(card_db, 6, ["Desperate Ritual", "Pyretic Ritual"])
    assert SIGNAL in _signals(g, pif)


def test_a_cost_reducer_in_play_does_not_make_the_grant_a_signal(card_db):
    """The replayed board: a cost reducer in play, the chain-continuation
    shape. The grant still leaves no granted spell affordable."""
    g, pif = _game(card_db, 3, ["Wrenn's Resolve"])
    _add(g, card_db, "Ruby Medallion", "battlefield")
    _add(g, card_db, "Wish", "hand")                  # a payoff in reach
    _add(g, card_db, "Grapeshot", "sideboard")
    assert _signals(g, pif) == []


def test_the_grant_is_held_rather_than_cast_for_nothing(card_db):
    from ai.ev_player import EVPlayer
    g, _pif = _game(card_db, 4, ["Desperate Ritual", "Pyretic Ritual"])
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    chosen = player.decide_main_phase(g)
    assert chosen is None or chosen[1].name != "Past in Flames"
