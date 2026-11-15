"""A sacrifice outlet converts creatures into lethal damage through the
activation owner, and the runner makes no damage activation of its own.

"Sacrifice a creature: ~ deals N damage to any target" is an activated
ability (CR 602): its cost is paid as it is activated (the sacrificed
creature dies, CR 602.2b / 701.21), its damage is dealt through the damage
owner on resolution (CR 120.3), and a player at 0 life loses by a
state-based action (CR 704.5a). At the end step the AI converts its
creatures into damage only when the outlets it controls can, between them,
deal the opponent lethal damage (`ai.activation_ev.lethal_damage_outlet_plan`);
short of lethal nothing is sacrificed there -- the main phase's EV owns
every non-lethal activation, aimed by `ai.damage_targets`.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game():
    from engine.game_runner import AICallbacks
    game = GameState(rng=random.Random(0), callbacks=AICallbacks())
    game.current_phase = Phase.END_STEP
    game.active_player = 0
    return game


def _put(game, card_db, idx, name):
    c = CardInstance(template=card_db.get_card(name), owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[idx].battlefield.append(c)
    return c


def _outlet_board(card_db, opp_life, victims=3):
    game = _game()
    _put(game, card_db, 0, "Goblin Bombardment")
    bodies = [_put(game, card_db, 0, "Memnite") for _ in range(victims)]
    game.players[1].life = opp_life
    return game, bodies


def test_a_sacrifice_outlet_converts_creatures_into_lethal_damage_through_the_activation_owner(card_db, game_runner, monkeypatch):
    from engine import damage
    dealt = []
    real = damage.deal_damage
    monkeypatch.setattr(damage, "deal_damage",
                        lambda src, tgt, n, **k: (dealt.append(n),
                                                  real(src, tgt, n, **k))[1])
    game, bodies = _outlet_board(card_db, opp_life=3)
    died = []
    real_move = game.zone_mgr.move_card_to_graveyard
    monkeypatch.setattr(game.zone_mgr, "move_card_to_graveyard",
                        lambda g, card, **k: (died.append(card),
                                              real_move(g, card, **k))[1])
    game_runner._activate_lethal_damage_outlets(game, 0)
    assert game.game_over and game.winner == 0
    assert game.players[1].life == 0
    assert dealt == [1, 1, 1]                       # through the damage owner
    assert set(died) == set(bodies)                 # costs through the funnel
    assert game._activations_this_game == 3         # through ActivationManager


def test_short_of_lethal_nothing_is_sacrificed(card_db, game_runner):
    game, bodies = _outlet_board(card_db, opp_life=4)
    game_runner._activate_lethal_damage_outlets(game, 0)
    assert not game.game_over
    assert game.players[1].life == 4
    assert all(b.zone == "battlefield" for b in bodies)


def test_the_plan_counts_every_outlet_cost_it_must_pay(card_db):
    """Each activation spends one victim its cost admits and its own mana:
    a {1}{R} outlet with two lands activates once, whatever the bodies."""
    from ai.activation_ev import lethal_damage_outlet_plan
    game = _game()
    rites = _put(game, card_db, 0, "Blood Rites")       # {1}{R}, sac: 2 damage
    for _ in range(2):
        _put(game, card_db, 0, "Mountain")
    for _ in range(3):
        _put(game, card_db, 0, "Memnite")
    game.players[1].life = 2
    plan = lethal_damage_outlet_plan(game, 0)
    assert [(p, a.index) for p, a in plan] == [(rites, plan[0][1].index)]
    game.players[1].life = 3
    assert lethal_damage_outlet_plan(game, 0) == []


def test_the_runner_never_fires_a_tap_pinger_itself(card_db, game_runner):
    """A "{T}: this creature deals N damage to any target" creature is
    activated by the AI's main-phase choice, aimed by its one owner -- the
    runner's post-main-phase sweep no longer taps it for an engine-picked
    target off the stack."""
    game = _game()
    game.current_phase = Phase.MAIN1
    pinger = _put(game, card_db, 0, "Prodigal Pyromancer")
    game_runner._activate_tap_abilities(game, active=0)
    assert not pinger.tapped
    assert game.players[1].life == 20
