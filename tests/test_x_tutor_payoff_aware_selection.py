"""An X-cost creature tutor must be payoff-aware in both its X and its EV.

The Green Sun's Zenith shape ("search your library for a creature card with
mana value X or less, put it onto the battlefield") was cast at whatever X the
current mana allowed, fetching whatever fit — the cast EV never looked at what
the tutor could actually deliver at that X.

Live bug this pins (docs/diagnostics/2026-08-26_amulet_titan_rediagnosis.md,
2 of 12 walked losses): the X-tutor cast at X=2, X=4, X=4 all fetching the
same 1-mana body in one game, burning the deck's 4-of payoff-access below the
cost of its 6-mana payoff while that payoff sat reachable in the library; in
another game X=3 fetched the 1-drop on T4 and the payoff arrived one turn too
late.

Rules under test (mechanic-phrased; card names below are fixture carriers
loaded from the real DB — Green Sun's Zenith, Arboreal Grazer, Primeval
Titan):
  1. Cast EV is conditioned on the BEST fetchable target at the chosen X
     (deck-list knowledge of the controller's own library is legitimate),
     not a flat tutor bonus — a tutor that can deliver nothing is withheld.
  2. Every point of X above the best fetchable target's cost is mana buying
     nothing and is charged against the cast.
  3. The early ramp line survives: a small-X fetch of a ramp body that
     accelerates the plan (land still in hand to put onto the battlefield)
     remains the chosen line — the gate must not strangle the turn-1/2
     opening.
  4. The chosen X is the cheapest X that delivers the intended target —
     never pay X=8 for a 6-drop or X=4 for a 1-drop.
  5. (hold) When the deliverable body is small, the payoff above it is
     still in the library and reachable within the game's horizon, and
     this is the last in-hand access, the tutor is held rather than burned.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from ai.scoring_constants import PATIENCE_GATE_REJECT_SENTINEL

_DB = CardDatabase()


def _add(game, name, controller, zone, tapped=False):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
        c.tapped = tapped
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def _game(n_forests, turn, library_names, hand_extra=()):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = turn
    me, opp = game.players[0], game.players[1]
    me.deck_name = "Amulet Titan"
    opp.deck_name = "Boros Energy"
    me.life = 18
    opp.life = 20
    for _ in range(n_forests):
        _add(game, "Forest", 0, "battlefield")
    tutor = _add(game, "Green Sun's Zenith", 0, "hand")
    for nm in hand_extra:
        _add(game, nm, 0, "hand")
    for nm in library_names:
        _add(game, nm, 0, "library")
    # padding so the library is not trivially empty
    for _ in range(8):
        _add(game, "Forest", 0, "library")
    for _ in range(3):
        _add(game, "Mountain", 1, "battlefield")
    return game, tutor


def _score(game, tutor):
    ai = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                  rng=random.Random(0))
    ai._init_deck_knowledge(game)
    if ai.goal_engine:
        ai.goal_engine.check_transition(game, 0)
    snap = snapshot_from_game(game, 0)
    return ai._score_spell(tutor, snap, game,
                           game.players[0], game.players[1])


def test_an_x_tutor_is_not_cast_at_large_x_for_a_small_body_when_a_bigger_payoff_is_reachable():
    """Rule 5 (the replay bug): mid-game, five untapped lands, the only
    deliverable body within budget is a 1-drop, the 6-mana payoff sits in
    the library, no other in-hand access, no land in hand to make the ramp
    body count, no opposing clock forcing action — burning the last access
    on the small body must sit in the reject band."""
    game, tutor = _game(
        n_forests=5, turn=5,
        library_names=["Arboreal Grazer", "Primeval Titan"])
    ev = _score(game, tutor)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"the X-tutor scored {ev:.2f} for a 1-drop body while the 6-mana "
        f"payoff was still reachable in the library — the last payoff-access "
        f"copy must be held, not burned below the payoff's cost")


def test_an_x_tutor_charges_the_gap_between_x_and_the_best_fetchable_target():
    """Rule 2: X minus the delivered target's mana value is spent mana buying
    nothing. The shared X-picker's net-value function must charge that gap:
    the same 1-drop delivered at X=4 is strictly worse than at X=1, and a
    gapless cast is credited exactly its delivered mana value."""
    from engine.cast_manager import creature_tutor_x_net_value
    assert creature_tutor_x_net_value(4, 1) < creature_tutor_x_net_value(1, 1), (
        "casting at X=4 for a 1-cost body must score below casting at X=1 "
        "for the same body — the 3-mana gap buys nothing")
    assert creature_tutor_x_net_value(6, 6) == 6, (
        "a gapless cast (X equal to the delivered target's mana value) is "
        "credited the full delivered mana value, uncharged")


def test_early_small_x_ramp_fetch_remains_the_chosen_line():
    """Rule 3: turn 2, two lands, a land still in hand — fetching the 1-cost
    ramp body at X=1 accelerates the plan and must NOT be clamped into the
    reject band, even though the 6-mana payoff is also in the library."""
    game, tutor = _game(
        n_forests=2, turn=2,
        library_names=["Arboreal Grazer", "Primeval Titan"],
        hand_extra=["Forest"])
    ev = _score(game, tutor)
    assert ev > PATIENCE_GATE_REJECT_SENTINEL, (
        f"the X-tutor scored {ev:.2f} on the turn-2 ramp line — the payoff "
        f"hold must not strangle a small-X fetch that accelerates the plan")


def test_the_chosen_x_is_the_cheapest_that_delivers_the_target():
    """Rule 4: the X-picker chooses the cheapest X that delivers the best
    fetchable target — X equals the target's mana value, not the budget."""
    from engine.cast_manager import pick_creature_tutor_x_value

    # Budget 8 with a 6-drop in the library: X must be 6, not 8.
    game, tutor = _game(
        n_forests=9, turn=7,
        library_names=["Arboreal Grazer", "Primeval Titan"])
    best_x, target, _top = pick_creature_tutor_x_value(
        game, 0, 8, tutor.template)
    assert target is not None and (target.template.cmc or 0) == 6, (
        f"with budget 8 the best deliverable is the 6-drop; got "
        f"{target.name if target else None}")
    assert best_x == 6, (
        f"the cheapest X delivering a 6-mana target is 6, got X={best_x}")

    # Budget 4 with only a 1-drop deliverable: X must be 1, not 4.
    best_x, target, _top = pick_creature_tutor_x_value(
        game, 0, 4, tutor.template)
    assert target is not None and (target.template.cmc or 0) == 1, (
        f"with budget 4 only the 1-drop is deliverable; got "
        f"{target.name if target else None}")
    assert best_x == 1, (
        f"the cheapest X delivering a 1-mana target is 1, got X={best_x}")


def test_an_x_tutor_that_can_deliver_nothing_is_withheld():
    """Rule 1: EV is conditioned on delivery — a library with no fetchable
    creature within any affordable X makes the cast a fizzle; it must sit
    in the reject band, not collect a flat tutor bonus."""
    game, tutor = _game(n_forests=5, turn=5, library_names=[])
    ev = _score(game, tutor)
    assert ev <= PATIENCE_GATE_REJECT_SENTINEL, (
        f"the X-tutor scored {ev:.2f} with nothing fetchable at any "
        f"affordable X — delivery-conditioned EV must withhold the fizzle")
