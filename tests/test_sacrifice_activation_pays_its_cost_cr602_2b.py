"""An activated ability's cost is paid before its effect (CR 602.2b), and
the engine decides no activation the activation path can already run.

`GameRunner._activate_sacrifice_abilities` scanned oracle text for
"Sacrifice this: <effect>" and fired the effect on a set of in-engine
heuristics — paying NOTHING: Mind Stone was sacrificed for a card with no
mana open (every Tron replay, 2026-09-08). The generic activation path
(`engine/activation.py` + `ai/activation_ev.py`) already parses, prices
and charges the draw, graveyard-exile, land-tutor and damage classes, so
the engine was double-activating them for free and choosing on the AI's
behalf.

Rules pinned: a self-sacrifice ability whose effect the activation path
can execute is left to it (the AI decides, the cost is charged there); the
residual shapes the engine still runs pay their parsed mana and tap cost
first and are refused when they cannot (CR 601.2h). Card names are fixture
carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_runner import GameRunner
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(template=tmpl, owner=controller, controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "library":
        game.players[controller].library.append(card)
    return card


def _game(turn=8):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.turn_number = turn
    return game


def test_a_class_the_activation_path_can_run_is_left_to_it(card_db):
    # Mind Stone's "{1}, {T}, Sacrifice: draw a card" parses as DRAW_N with
    # a {1} cost. The engine heuristic used to fire it on hand size alone —
    # here with NO mana at all.
    game = _game()
    stone = _add(game, card_db, "Mind Stone", 0, "battlefield")
    for _ in range(3):
        _add(game, card_db, "Island", 0, "library")
    _add(game, card_db, "Island", 0, "hand")           # hand ≤ 2 → the old trigger
    runner = GameRunner(card_db=card_db)
    runner._activate_sacrifice_abilities(game, 0)
    assert stone in game.players[0].battlefield, (
        "the engine sacrificed a parsed, priced ability for free — that "
        "activation belongs to the AI through the activation path")


def test_a_residual_shape_pays_its_parsed_cost_or_is_not_activated(card_db):
    # Engineered Explosives: "{2}, Sacrifice: destroy each nonland permanent
    # with mana value equal to the number of charge counters" — a shape the
    # activation path cannot execute yet, so the engine heuristic still owns
    # it. It must pay {2}.
    def _board(lands):
        game = _game()
        ee = _add(game, card_db, "Engineered Explosives", 0, "battlefield")
        ee.other_counters["charge"] = 1
        for _ in range(lands):
            _add(game, card_db, "Island", 0, "battlefield")
        victim = _add(game, card_db, "Bonesplitter", 1, "battlefield")     # MV 1
        return game, ee, victim

    game, ee, victim = _board(lands=0)
    GameRunner(card_db=card_db)._activate_sacrifice_abilities(game, 0)
    assert ee in game.players[0].battlefield and victim in game.players[1].battlefield, (
        "with no mana the {2} cost cannot be paid — the ability is not activated")

    game, ee, victim = _board(lands=2)
    GameRunner(card_db=card_db)._activate_sacrifice_abilities(game, 0)
    assert ee not in game.players[0].battlefield, "with {2} open it is activated"
    assert victim not in game.players[1].battlefield
    assert all(l.tapped for l in game.players[0].lands), "the {2} was paid"
