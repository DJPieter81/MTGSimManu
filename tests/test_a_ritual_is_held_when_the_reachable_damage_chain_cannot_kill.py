"""A ritual is held when the turn's reachable damage chain cannot kill.

Decision principle (AI, from CR 106.4 and 500.4: ritual mana empties at the
end of the phase). With a damage payoff in reach -- in hand, or found by a
tutor the chain planner now models (`combo_chain.classify_card`) -- a
ritual was admitted whatever the chain could do. If the turn's best chain
ends on that payoff short of lethal (`ComboAssessment.is_ready`) and a later
turn remains, firing it spends the rituals for damage that does not win;
holding keeps them for a turn whose chain can. This holds at any storm count:
what is cast is spent either way. A token payoff's value is the next turn's
attack, which `is_ready` does not measure, so it is not judged here.

The defect (Ruby Storm vs Dimir Midrange s50000, game 1, Storm's turn 4):
with a same-turn Wish exiled by an impulse draw, two Desperate Rituals and a
flashback Past in Flames fired for about 5 damage into 17 life, and the
Wish expired uncast. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from ai.combo_calc import STORM_HARD_HOLD, assess_combo, card_combo_modifier
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
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
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db, *, sideboard, opp_life=20, storm=0, my_life=20,
          opp_creatures=()):
    g = GameState(rng=random.Random(0))
    for n in ("Mountain", "Mountain", "Ruby Medallion", "Ruby Medallion"):
        _add(g, card_db, n, "battlefield")
    ritual = _add(g, card_db, "Desperate Ritual", "hand")
    _add(g, card_db, "Wish", "hand")
    for n in sideboard:
        _add(g, card_db, n, "sideboard")
    for _ in range(20):
        _add(g, card_db, "Mountain", "library")
    for n in opp_creatures:
        _add(g, card_db, n, "battlefield", controller=1)
    g.players[0].deck_name = "Ruby Storm"
    g.players[1].deck_name = "Dimir Midrange"
    g.current_phase = Phase.MAIN1
    g.active_player = g.priority_player = 0
    g.turn_number = 5
    g.players[0].lands_played_this_turn = 1
    g.players[0].spells_cast_this_turn = storm
    g._global_storm_count = storm
    g.players[0].life = my_life
    g.players[1].life = opp_life
    return g, ritual


def _modifier(g, ritual):
    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    snap = snapshot_from_game(g, 0)
    a = assess_combo(g, 0, player.goal_engine, snap, None)
    return card_combo_modifier(ritual, a, snap, g.players[0], g, 0), a


def test_a_wish_route_short_of_lethal_holds_the_ritual(card_db):
    g, ritual = _game(card_db, sideboard=["Grapeshot"])
    mod, a = _modifier(g, ritual)
    assert a.best_chain.payoff_name == "Grapeshot" and not a.is_ready
    assert mod == STORM_HARD_HOLD


def test_mid_chain_too(card_db):
    g, ritual = _game(card_db, sideboard=["Grapeshot"], storm=1)
    assert _modifier(g, ritual)[0] == STORM_HARD_HOLD


def test_a_lethal_chain_through_the_tutor_fires(card_db):
    g, ritual = _game(card_db, sideboard=["Grapeshot"], opp_life=3)
    mod, a = _modifier(g, ritual)
    assert a.is_ready
    assert mod != STORM_HARD_HOLD


def test_with_no_later_turn_the_short_chain_fires(card_db):
    g, ritual = _game(card_db, sideboard=["Grapeshot"], my_life=2,
                      opp_creatures=["Grizzly Bears"])
    assert _modifier(g, ritual)[0] != STORM_HARD_HOLD


def test_a_token_payoff_is_not_judged_by_lethal_damage(card_db):
    g, ritual = _game(card_db, sideboard=["Empty the Warrens"])
    assert _modifier(g, ritual)[0] != STORM_HARD_HOLD
