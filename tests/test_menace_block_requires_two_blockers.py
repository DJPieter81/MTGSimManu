"""A menace attacker must be blocked by 2+ creatures, or left unblocked —
never single-blocked.

Menace (CR 702.111): a creature with menace can't be blocked except by two or
more creatures. The joint block-assignment `coverage_pass` assigned exactly
ONE cheapest blocker per attacker with no menace awareness, so a defender
holding two legal blockers would single-block a menace attacker; the engine
then drops that illegal one-blocker assignment entirely (CR 509.1c) and the
attacker connects for full damage — preventable lethal.

Rule under test: given a menace attacker and >=2 available legal blockers at
lethal life, `decide_blockers` assigns it 2+ blockers (surviving), never
exactly 1. Mechanic-driven (menace keyword), no card names.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance, Keyword
from engine.game_state import GameState, Phase
from engine.card_database import CardDatabase
from ai.ev_player import EVPlayer

_DB = CardDatabase()


def _add(game, name, controller, zone):
    t = _DB.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller],
            "battlefield" if zone == "battlefield" else zone).append(c)
    return c


def test_menace_attacker_never_single_blocked():
    game = GameState(rng=random.Random(0))
    game.active_player = 1  # opponent attacks
    game.current_phase = Phase.DECLARE_BLOCKERS
    game.turn_number = 6
    defender = game.players[0]
    defender.life = 2
    defender.deck_name = "4/5c Control"
    game.players[1].deck_name = "Domain Zoo"

    # Defender has two legal blockers that can double-block and survive.
    w1 = _add(game, "Wall of Omens", 0, "battlefield")
    w2 = _add(game, "Wall of Omens", 0, "battlefield")
    # A menace attacker on the opponent's side.
    atk = _add(game, "Boggart Brute", 1, "battlefield")  # 3/2 menace
    assert Keyword.MENACE in atk.keywords, "fixture must have menace"

    ai = EVPlayer(player_idx=0, deck_name="4/5c Control", rng=random.Random(0))
    blocks = ai.decide_blockers(game, [atk])

    assigned = blocks.get(atk.instance_id, []) if blocks else []
    assert len(assigned) != 1, (
        f"a menace attacker must get 2+ blockers or none, never exactly 1 "
        f"(the engine drops a 1-blocker menace block → lethal); got "
        f"{len(assigned)} blocker(s)")
    # With two walls available at lethal life, it should actually block (2).
    assert len(assigned) == 2, (
        f"defender at 2 life with two walls should double-block the lethal "
        f"menace attacker to survive; got {len(assigned)}")
