"""A cast that draws more cards than its caster's library holds loses.

CR 121.4, 704.5b: a player who attempted to draw from an empty library
loses the game the next time state-based actions are checked. So a spell
whose own resolution draws more cards than its caster's library holds is a
game-ending cast against its caster, like one whose draw tax kills it (unit
DT): it sits at the game-ending floor (`-LETHAL_THREAT`). The snapshot
carries the library's size (`EVSnapshot.my_library`).

The defect (Ruby Storm vs Living End s56500, Storm's turn 5): Storm chained
digs with its library running out, holding a castable Wish, and lost by
casting a Manamorphose with no card left to draw. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, zone):
    c = CardInstance(template=card_db.get_card(name), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
    getattr(game.players[0], zone).append(c)
    return c


def _game(card_db, library):
    g = GameState(rng=random.Random(0))
    g.active_player = 0
    g.current_phase = Phase.MAIN1
    for _ in range(3):
        _add(g, card_db, "Mountain", "battlefield")
    for _ in range(library):
        _add(g, card_db, "Mountain", "library")
    for _ in range(5):
        c = CardInstance(template=card_db.get_card("Island"), owner=1,
                         controller=1, instance_id=g.next_instance_id(),
                         zone="library")
        g.players[1].library.append(c)
    g.players[0].deck_name = "Ruby Storm"
    return g


def _ev(game, card):
    from ai.ev_evaluator import compute_play_ev, snapshot_from_game
    snap = snapshot_from_game(game, 0)
    return compute_play_ev(card, snap, "combo", game, 0)


def test_drawing_from_an_empty_library_is_at_the_game_ending_floor(card_db):
    from ai.scoring_constants import LETHAL_THREAT
    g = _game(card_db, library=0)
    spell = _add(g, card_db, "Manamorphose", "hand")
    assert _ev(g, spell) == -LETHAL_THREAT


def test_drawing_more_than_the_library_holds_is_at_the_floor(card_db):
    from ai.scoring_constants import LETHAL_THREAT
    g = _game(card_db, library=1)
    spell = _add(g, card_db, "Wrenn's Resolve", "hand")   # exiles, no draw
    divination = _add(g, card_db, "Divination", "hand")    # draws two
    assert _ev(g, divination) == -LETHAL_THREAT
    assert _ev(g, spell) != -LETHAL_THREAT


def test_drawing_exactly_the_library_is_not_the_floor(card_db):
    from ai.scoring_constants import LETHAL_THREAT
    g = _game(card_db, library=1)
    spell = _add(g, card_db, "Manamorphose", "hand")
    assert _ev(g, spell) != -LETHAL_THREAT


def test_the_snapshot_carries_the_library_size(card_db):
    from ai.ev_evaluator import EVSnapshot, snapshot_from_game
    assert snapshot_from_game(_game(card_db, library=3), 0).my_library == 3
    assert EVSnapshot().my_library is None
