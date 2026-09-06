"""A target bounded by X ("target … with mana value X or less") is legal
only up to the X the caster can actually pay (CR 601.2b/c: X is chosen
before targets, and the affordable X bounds the legal targets), and the
cast pays the X the chosen target needs.

19 Modern cards bound a target by X. Before this, the solver captured only
a numeric ceiling, so an X-bound target was unbounded: an X-removal spell
was cast at a mana-value-12 permanent with X=1 and resolved doing nothing
(Azorius Blink vs Domain Zoo s50000 — March of Otherworldly Light, G1 T3
and twice in G2), and the AI's exile-target branch picked the HIGHEST
mana-value permanent regardless of what X could reach.

Card names below are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
from engine.game_state import GameState, Phase
from engine.target_solver import (enumerate_legal_targets,
                                  has_legal_target_for_spell, parse)


def _put(game, card_db, name, controller, zone, tapped=False):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
        game.players[controller].battlefield.append(c)
    else:
        game.players[controller].hand.append(c)
    return c


def _game(card_db, lands=2):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(lands):
        _put(game, card_db, "Plains", 0, "battlefield")
    return game


def test_x_bound_requirement_is_bounded_by_the_affordable_x(card_db):
    game = _game(card_db)
    big = _put(game, card_db, "Scion of Draco", 1, "battlefield")       # MV 12
    small = _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")  # MV 1
    reqs = parse(card_db.get_card("March of Otherworldly Light").oracle_text)
    req = next(r for r in reqs if r.zone == "battlefield")
    legal = enumerate_legal_targets(game, 0, req, x_ceiling=1)
    assert small in legal and big not in legal
    unbounded = enumerate_legal_targets(game, 0, req)
    assert big in unbounded, "with no ceiling supplied the bound is not applied"


def test_x_removal_is_not_castable_when_no_target_fits_the_affordable_x(card_db):
    game = _game(card_db, lands=2)               # X ≤ 1 after {W}
    _put(game, card_db, "Scion of Draco", 1, "battlefield")
    march = _put(game, card_db, "March of Otherworldly Light", 0, "hand")
    assert not game.can_cast(0, march)


def test_x_removal_is_castable_when_a_target_fits(card_db):
    game = _game(card_db, lands=2)
    _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    march = _put(game, card_db, "March of Otherworldly Light", 0, "hand")
    assert game.can_cast(0, march)


def test_affordable_x_is_derived_from_the_cost_and_capacity(card_db):
    game = _game(card_db, lands=4)
    t = card_db.get_card("March of Otherworldly Light")   # {X}{W}
    assert CastManager.affordable_x(game, 0, t) == 3


def test_ai_picks_a_target_the_affordable_x_can_reach(card_db):
    from ai.ev_player import EVPlayer
    game = _game(card_db, lands=2)
    _put(game, card_db, "Scion of Draco", 1, "battlefield")
    small = _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    march = _put(game, card_db, "March of Otherworldly Light", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Azorius Blink", rng=random.Random(0))
    assert ai._choose_targets(game, march) == [small.instance_id]


def test_cast_pays_the_x_the_chosen_target_needs(card_db):
    game = _game(card_db, lands=4)               # could pay X=3
    small = _put(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    march = _put(game, card_db, "March of Otherworldly Light", 0, "hand")
    assert game.cast_spell(0, march, [small.instance_id])
    assert any("(X=1)" in line for line in game.log), game.log[-5:]
    assert sum(1 for l in game.players[0].battlefield
               if l.template.is_land and not l.tapped) == 2, "only {X=1}{W} tapped"
