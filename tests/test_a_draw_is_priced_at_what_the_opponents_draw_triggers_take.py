"""A projected draw is priced at what the opponent's draw triggers take.

Decision principle (AI): a spell's projected draws are the draws its typed
instructions make -- a printed count, or "that many" bound to an earlier
instruction of the same spell -- and each costs the life the opponent's
draw-triggered abilities take (`ai.bhi.opp_static_damage_per_card_event`).
A cast whose projection draws its own caster to 0 or less sits at the
game-ending floor (`-LETHAL_THREAT`, the existing M1-AI rule).

The draw count came from a runtime regex over oracle text that read only
"draw <number> cards", so "draw that many" (57 pool cards) projected no
draw and no tax: Ruby Storm at 2 life cast Hex Magic ("exile all the cards
from your hand, then draw that many") into Orcish Bowmasters and died
(Bo3 replay Ruby Storm vs Dimir Midrange s50000, game 1). The count is now
read from the typed spell (`ai.draw_projection.projected_draws`); the regex
stays only as the fallback for a shape it cannot count. Card names are
fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(card_db, life=20, punisher="Orcish Bowmasters"):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    game.players[0].deck_name = "Ruby Storm"
    game.players[0].life = life
    if punisher:
        _add(game, card_db, punisher, 1, "battlefield")
    for _ in range(4):
        _add(game, card_db, "Mountain", 0, "battlefield")
        _add(game, card_db, "Island", 0, "library")
    return game


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
    getattr(game.players[controller], zone).append(card)
    return card


def _life_after(game, card):
    from ai.ev_evaluator import _project_spell, snapshot_from_game
    snap = snapshot_from_game(game, 0)
    return _project_spell(card, snap, None, game, 0).my_life


def test_a_bound_draw_count_is_the_hand_its_instruction_moves(card_db):
    from ai.draw_projection import projected_draws
    game = _game(card_db)
    hex_ = _add(game, card_db, "Hex Magic", 0, "hand")
    for _ in range(3):
        _add(game, card_db, "Mountain", 0, "hand")
    assert projected_draws(hex_, game.players[0].hand) == 3


def test_a_printed_draw_count_is_read_from_the_typed_spell(card_db):
    from ai.draw_projection import projected_draws
    game = _game(card_db)
    morphose = _add(game, card_db, "Manamorphose", 0, "hand")
    assert projected_draws(morphose, game.players[0].hand) == 1


def test_each_projected_draw_costs_the_opponents_draw_tax(card_db):
    game = _game(card_db, life=10)
    hex_ = _add(game, card_db, "Hex Magic", 0, "hand")
    for _ in range(3):
        _add(game, card_db, "Mountain", 0, "hand")
    assert _life_after(game, hex_) == 10 - 3


def test_the_tax_is_each_punisher_on_the_board(card_db):
    """Two life a draw under a lose-2 punisher (Sheoldred): another deck's
    cantrip is priced the same way."""
    game = _game(card_db, life=10, punisher="Sheoldred, the Apocalypse")
    hex_ = _add(game, card_db, "Hex Magic", 0, "hand")
    _add(game, card_db, "Mountain", 0, "hand")
    assert _life_after(game, hex_) == 10 - 2


def test_no_punisher_no_tax(card_db):
    game = _game(card_db, life=10, punisher=None)
    hex_ = _add(game, card_db, "Hex Magic", 0, "hand")
    _add(game, card_db, "Mountain", 0, "hand")
    assert _life_after(game, hex_) == 10


def test_a_cast_that_draws_its_caster_to_zero_is_at_the_game_ending_floor(
        card_db):
    """The replayed line: 2 life, Bowmasters, Hex Magic over two cards."""
    from ai.ev_evaluator import compute_play_ev, snapshot_from_game
    from ai.scoring_constants import LETHAL_THREAT
    game = _game(card_db, life=2)
    hex_ = _add(game, card_db, "Hex Magic", 0, "hand")
    for _ in range(2):
        _add(game, card_db, "Mountain", 0, "hand")
    snap = snapshot_from_game(game, 0)
    assert compute_play_ev(hex_, snap, "combo", game, 0) \
        == -LETHAL_THREAT
