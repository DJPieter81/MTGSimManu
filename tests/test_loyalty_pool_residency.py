"""A planeswalker's activation pool lasts as long as it survives, and it
survives only as long as its loyalty holds against attacking power.

`ai.ev_evaluator.expected_future_value` valued every planeswalker's pool as
at most its loyalty in activations and ignored the creatures facing it:

* A walker with a useful non-negative ability (a "+N" or "0") refills or
  holds its loyalty each turn, so its pool is bounded by how long it stays
  on the battlefield — not by its current loyalty.
* Creatures can attack planeswalkers (CR 508.1b), so a walker facing
  attacking power P with loyalty L survives about L / P turns, whatever
  its abilities.

Both terms derive from the snapshot's clocks and powers (no constants).
Class: every planeswalker. Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import expected_future_value, snapshot_from_game
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost
from engine.game_state import GameState

PLUS_WALKER = "Ugin, Eye of the Storms"


def _walker(game, card_db, controller, loyalty, name=PLUS_WALKER):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller, instance_id=game.next_instance_id(),
                     zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.loyalty_counters = loyalty
    game.players[controller].battlefield.append(c)
    return c


def _creature(game, controller, power):
    tmpl = CardTemplate(
        name=f"Beater{power}", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=power, toughness=power, loyalty=None, keywords=set(),
        abilities=[], color_identity=set(), produces_mana=[],
        enters_tapped=False, oracle_text="", tags=set())
    c = CardInstance(template=tmpl, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _value(game, walker, owner=0):
    return expected_future_value(walker, snapshot_from_game(game, owner))


def test_a_replenishing_walker_outlasts_its_current_loyalty(card_db):
    # Owner has a clock (the game will end), nothing attacks the walker:
    # a useful "+" ability keeps it ticking until the game ends, which is
    # longer than its current loyalty of 2.
    game = GameState(rng=random.Random(0))
    _creature(game, 0, power=2)          # owner's clock: 20 / 2 = 10 turns
    low = _walker(game, card_db, 0, loyalty=2)
    v_low = _value(game, low)
    low.loyalty_counters = 10
    v_ten = _value(game, low)
    assert v_low == v_ten > 0.0


def test_a_walker_facing_attackers_is_worth_less_than_the_same_walker_unopposed(card_db):
    game = GameState(rng=random.Random(0))
    _creature(game, 0, power=2)
    w = _walker(game, card_db, 0, loyalty=4)
    unopposed = _value(game, w)
    _creature(game, 1, power=4)          # attacks it down in one turn
    opposed = _value(game, w)
    assert 0.0 < opposed < unopposed


def test_more_loyalty_survives_attackers_longer(card_db):
    game = GameState(rng=random.Random(0))
    _creature(game, 0, power=2)
    _creature(game, 1, power=2)
    w = _walker(game, card_db, 0, loyalty=2)
    v2 = _value(game, w)
    w.loyalty_counters = 6
    v6 = _value(game, w)
    assert v6 > v2
