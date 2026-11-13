"""A sweep is activated when what it takes is worth more to the opponent
than to its controller, and a sunburst sweep's X buys the count worth most
(AI, from CR 701.8a, 107.3, 702.44a, 608.2h).

A sweep destroys every permanent its description admits, its controller's
as well as the opponent's (CR 701.8a). Its controller activates it when the
opponent's loss -- each permanent priced by what it is worth to its owner
(`permanent_threat`) -- outweighs its own, net of the activation's cost
(`ai.sweep_value`, `ai.activation_ev._sweep_plan`).

A sunburst sweep's counters are the colours of mana spent to cast it (CR
702.44a), so its X buys the mana value it will sweep. The engine asks the
caster for X (the X channel, `GameCallbacks.choose_x`); the AI pays for the
colour count, among those its untapped sources can make, whose sweep is
worth most now (`ai.sweep_value.sunburst_sweep_x`).

The defect (docs/diagnostics/2026-10-14_dimir_midrange_root_cause.md,
subsystem 3): no AI path activated a sweep -- a runner shortcut popped it --
and X was always the most mana the caster had.

Card names are fixture carriers: 24 pool activations sweep, 16 pool cards
have sunburst.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _sweep_candidates(game, ee):
    from ai.activation_ev import activation_candidates
    from ai.ev_evaluator import snapshot_from_game
    snap = snapshot_from_game(game, 0)
    return [c for c in activation_candidates(game, 0, snap)
            if c[0] is ee]


def _board(card_db, charges, mine, theirs):
    game = _game()
    ee = _add(game, card_db, "Engineered Explosives", 0)
    ee.other_counters["charge"] = charges
    for _ in range(2):
        _add(game, card_db, "Island", 0)
    for name in mine:
        _add(game, card_db, name, 0)
    for name in theirs:
        _add(game, card_db, name, 1)
    return game, ee


# ── The activation ────────────────────────────────────────────────────

def test_a_sweep_is_activated_when_the_opponent_loses_more(card_db):
    game, ee = _board(card_db, 2, mine=[],
                      theirs=["Grizzly Bears", "Grizzly Bears"])
    (cand,) = _sweep_candidates(game, ee)
    assert cand[3] > 0


def test_a_sweep_is_held_when_its_controller_loses_more(card_db):
    game, ee = _board(card_db, 2,
                      mine=["Grizzly Bears", "Grizzly Bears", "Mind Stone"],
                      theirs=["Grizzly Bears"])
    assert not _sweep_candidates(game, ee)


def test_a_sweep_that_hits_nothing_is_held(card_db):
    game, ee = _board(card_db, 3, mine=[], theirs=["Grizzly Bears"])
    assert not _sweep_candidates(game, ee)


# ── The X of a sunburst sweep ─────────────────────────────────────────

def _x_choice(card_db, lands, theirs, max_x):
    from ai.sweep_value import sunburst_sweep_x
    game = _game()
    for land in lands:
        _add(game, card_db, land, 0)
    for name in theirs:
        _add(game, card_db, name, 1)
    t = card_db.get_card("Engineered Explosives")
    return sunburst_sweep_x(game, 0, t, max_x)


def test_x_buys_the_mana_value_whose_sweep_is_worth_most(card_db):
    lands = ["Island", "Swamp", "Mountain"]
    assert _x_choice(card_db, lands, ["Elite Vanguard"], 3) == 1
    assert _x_choice(card_db, lands, ["Grizzly Bears", "Grizzly Bears"],
                     3) == 2


def test_x_buys_no_colour_the_sources_cannot_make(card_db):
    """Two colours of sources: a mana value 3 sweep is out of reach, so X
    stops at the best count it can colour."""
    assert _x_choice(card_db, ["Island", "Swamp", "Island"],
                     ["Grizzly Bears", "Centaur Courser"], 3) == 2


def test_the_engine_asks_the_caster_for_x(card_db):
    class _One:
        def __getattr__(self, name):
            from engine.callbacks import DefaultCallbacks
            return getattr(DefaultCallbacks(), name)

        def choose_x(self, game, player_idx, card, max_x):
            return 1

    game = _game()
    game.callbacks = _One()
    for land in ("Island", "Swamp", "Mountain"):
        _add(game, card_db, land, 0)
    for _ in range(5):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    ee = _add(game, card_db, "Engineered Explosives", 0, "hand")
    assert game.cast_spell(0, ee, [])
    while not game.stack.is_empty:
        game.resolve_stack()
    assert ee.other_counters.get("charge", 0) == 1
    assert sum(1 for l in game.players[0].lands if not l.tapped) == 2
