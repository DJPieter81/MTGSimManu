"""An instant whose effect restricts the OPPONENT for "this turn" — "target
player can't cast spells this turn" (silence), "creatures can't attack this
turn", "prevent all combat damage that would be dealt this turn" (fog) — has
no value on the caster's own turn: the restriction expires before the
opponent acts. 30 Modern instants carry the shape.

Two defects, both from Azorius Control vs Domain Zoo s50000 G1 (T9–T11):
the runner's imprint hook auto-fired the Isochron Scepter copy of Orim's
Chant in its controller's main phase three turns running ("silences P2
this turn" on P1's turn — the Scepter-Chant lock only works cast in the
opponent's upkeep), and the scorer credits such a card the same on either
turn. The restriction kind is parsed once at load into
`CardTemplate.turn_scoped_restriction`; the AI defers the hand-cast on the
caster's own turn (no this-turn signal), and the imprint hook fires a
"can't cast spells" copy in the OPPONENT's upkeep instead of its
controller's main phase. Card names are fixture carriers only.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.oracle_parser import parse_turn_scoped_restriction


class TestParse:
    def test_silence_shape(self):
        assert parse_turn_scoped_restriction(
            "Target player can't cast spells this turn.") == "no_spells"

    def test_fog_shape(self):
        assert parse_turn_scoped_restriction(
            "Prevent all combat damage that would be dealt this turn.") == "fog"

    def test_no_attacks_shape(self):
        assert parse_turn_scoped_restriction(
            "Creatures can't attack this turn.") == "no_attacks"

    def test_unrelated_instant_is_none(self):
        assert parse_turn_scoped_restriction(
            "Counter target spell unless its controller pays {1}.") is None


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
    elif zone == "hand":
        game.players[controller].hand.append(c)
    else:
        getattr(game.players[controller], zone).append(c)
    return c


def test_silence_has_no_this_turn_signal_on_the_casters_own_turn(card_db):
    from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(3):
        _put(game, card_db, "Plains", 0, "battlefield")
    _put(game, card_db, "Grizzly Bears", 1, "battlefield")
    for _ in range(4):
        _put(game, card_db, "Grizzly Bears", 1, "hand")
    chant = _put(game, card_db, "Orim's Chant", 0, "hand")
    snap = snapshot_from_game(game, 0)
    assert _enumerate_this_turn_signals(chant, snap, game, 0, "control") == []
    game.active_player = 1   # the opponent's turn: the restriction bites
    snap = snapshot_from_game(game, 0)
    assert _enumerate_this_turn_signals(chant, snap, game, 0, "control") != []


def test_imprinted_silence_fires_in_the_opponents_upkeep_not_its_controllers_main(card_db):
    from engine.game_runner import GameRunner
    from engine.card_database import CardDatabase
    game = GameState(rng=random.Random(0))
    for _ in range(3):
        _put(game, card_db, "Plains", 0, "battlefield")
    scepter = _put(game, card_db, "Isochron Scepter", 0, "battlefield")
    chant = _put(game, card_db, "Orim's Chant", 0, "exile")
    chant.instance_tags.add("on_scepter")
    scepter.instance_tags.add("imprint:Orim's Chant")
    for _ in range(3):
        _put(game, card_db, "Grizzly Bears", 1, "hand")
    runner = GameRunner(card_db)
    # Controller's own main phase: the lock must NOT fire.
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    runner._process_imprint_copy_activations(game, 0, timing="own_main")
    assert not any("silences" in line for line in game.log), game.log[-3:]
    assert not scepter.tapped
    # Opponent's upkeep: the holder (player 0) fires the lock.
    game.active_player = 1
    game.current_phase = Phase.UPKEEP
    runner._process_imprint_copy_activations(game, 0, timing="opp_upkeep")
    assert any("silences" in line for line in game.log), game.log[-3:]
    assert scepter.tapped
