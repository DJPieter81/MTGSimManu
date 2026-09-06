"""`ai.clock.opportunity_cost` is the one primitive that prices what the
board loses by spending a permanent (chump block, forced sacrifice,
discard). It must include what a MANA source gives up — its production —
and, when the permanent is half of an unbounded mana engine (CR 726.4
shortcut material), the engine's whole allowance. Without those terms a
zero-power mana creature priced like a vanilla 0/2, so it was the first
creature thrown in front of an attacker at 16 life and the first fed to a
sacrifice cost — one turn after the engine it completes had been tutored.

Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.clock import opportunity_cost
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState


def _bf(game, card_db, name, controller=0):
    t = card_db.get_card(name)
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def test_a_mana_creature_costs_more_to_spend_than_an_equal_body(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Grizzly Bears", controller=1)  # opp has a clock
    druid = _bf(game, card_db, "Devoted Druid")        # 0/2, taps for G
    thopter = _bf(game, card_db, "Ornithopter")        # 0/2, no mana
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    assert opportunity_cost(druid, me, snap) > opportunity_cost(thopter, me, snap)


def test_an_engine_member_costs_more_to_spend_than_a_bigger_vanilla_body(card_db):
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Grizzly Bears", controller=1)
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    bear = _bf(game, card_db, "Grizzly Bears")
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    assert opportunity_cost(druid, me, snap) > opportunity_cost(bear, me, snap)


def test_sacrifice_victim_choice_is_the_opportunity_cost_primitive(card_db):
    """One owner for "what the board gives up": the sacrifice chooser must
    rank by the same primitive the blocker path uses."""
    from ai.activation_ev import choose_sacrifice_victim
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Grizzly Bears", controller=1)
    druid = _bf(game, card_db, "Devoted Druid")
    _bf(game, card_db, "Vizier of Remedies")
    bear = _bf(game, card_db, "Grizzly Bears")
    thopter = _bf(game, card_db, "Ornithopter")
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    legal = [druid, bear, thopter]
    assert choose_sacrifice_victim(game, 0, legal) is min(
        legal, key=lambda c: opportunity_cost(c, me, snap))


def test_engine_loss_counts_every_engine_the_permanent_frees(card_db):
    """Spending the replacement source breaks EVERY untapper it frees;
    spending one of three untappers breaks one engine. The loss is the
    engine allowance per engine lost, not a flat membership flag."""
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Grizzly Bears", controller=1)
    druids = [_bf(game, card_db, "Devoted Druid") for _ in range(3)]
    vizier = _bf(game, card_db, "Vizier of Remedies")
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    assert opportunity_cost(vizier, me, snap) > 2 * opportunity_cost(druids[0], me, snap)


def test_sacrifice_prefers_a_fresh_body_over_any_engine_piece(card_db):
    from ai.activation_ev import choose_sacrifice_victim
    game = GameState(rng=random.Random(0))
    _bf(game, card_db, "Grizzly Bears", controller=1)
    druids = [_bf(game, card_db, "Devoted Druid") for _ in range(3)]
    vizier = _bf(game, card_db, "Vizier of Remedies")
    body = _bf(game, card_db, "Shang-Chi, Master of Kung Fu")
    body.summoning_sick = True
    pick = choose_sacrifice_victim(game, 0, druids + [vizier, body])
    assert pick is body
