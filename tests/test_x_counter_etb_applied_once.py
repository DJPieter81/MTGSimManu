"""An X-cost permanent that enters with X +1/+1 counters receives them
exactly once, and X is the X that was paid (CR 107.3, 601.2b).

`spell_resolution` placed the parsed `plus1_counters` X on entry with no
check for a dedicated ETB handler (the guard existed for charge counters
only), and the one dedicated handler in the pool added its own counters
again — reading X not from the cast but from an unrelated game counter
(the opponent's library searches). The creature entered one counter too
big every time and drew the wrong number of cards.

Rule: the counters are placed once, by whichever owner the template
declares (the generic X-counter branch when there is no dedicated ETB
handler, the handler otherwise), and X is `item.x_value`. Card names
are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.cast_manager import CastManager
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
    return card


def _resolve_all(game):
    while not game.stack.is_empty:
        game.resolve_stack()
        game.check_state_based_actions()


def test_an_x_counter_permanent_with_a_dedicated_handler_enters_with_x_counters_once(card_db):
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(4):
        _add(game, card_db, "Island", 0, "battlefield")          # UU + X=2
    for _ in range(3):
        game.players[0].library.append(
            CardInstance(template=card_db.get_card("Island"), owner=0, controller=0,
                         instance_id=game.next_instance_id(), zone="library"))
    # The unrelated counter the handler used to read X from.
    game.players[1].library_searches_this_game = 5
    wst = _add(game, card_db, "Wan Shi Tong, Librarian", 0, "hand")
    base_p = wst.template.power or 0
    hand_before = len(game.players[0].hand) - 1
    assert CastManager.cast_spell(game, 0, wst, [])
    x = game.stack.top.x_value
    assert x == 2, f"fixture: four Islands pay UU + X=2 (got X={x})"
    _resolve_all(game)
    assert wst in game.players[0].battlefield
    assert wst.plus_counters == x, (
        f"entered with {wst.plus_counters} +1/+1 counters for X={x} — placed twice, "
        f"or X read from the wrong source")
    assert wst.power == base_p + x
    assert len(game.players[0].hand) == hand_before + x // 2, "draws half X, rounded down"


def test_the_engine_places_x_counters_before_a_dedicated_handler_reads_them(card_db):
    """"Enters with X +1/+1 counters" is the permanent's own entry effect
    (CR 107.3, 614.1c); a dedicated ETB handler is what the card does
    AFTER it has entered. The first fix of this file made the handler the
    owner of the counters, so a handler that only READS them (the
    Ballista shape: spend the counters as damage) found zero, the
    creature entered as a 0/0 and died before its handler could act —
    Creatures Toolbox's infinite-mana outlet cast for X=40 dealt nothing
    (replays s60203 L424-427, s60205 L819-822, 2026-09-12).

    Rule: the engine places X counters on entry for every X-counter
    permanent; a handler may spend or read them, never place them again.
    """
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    for _ in range(4):
        _add(game, card_db, "Island", 0, "battlefield")          # {X}{X} with X=2
    ballista = _add(game, card_db, "Walking Ballista", 0, "hand")
    opp_life = game.players[1].life
    assert CastManager.cast_spell(game, 0, ballista, [])
    x = game.stack.top.x_value
    assert x == 2, f"fixture: four Islands pay X=2 (got X={x})"
    _resolve_all(game)
    assert any("Walking Ballista enters with 2 +1/+1 counter" in line
               for line in game.log), "the engine did not place the X counters"
    # The handler spends the counters as damage on an empty opposing
    # board: exactly X reaches the opponent, none is lost.
    assert game.players[1].life == opp_life - x, (
        f"opponent at {game.players[1].life}: the handler saw "
        f"{'no' if game.players[1].life == opp_life else 'the wrong'} counters")
