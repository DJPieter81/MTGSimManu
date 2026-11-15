"""A removal spell or removal enter trigger whose only opposing creatures
cannot be targeted is not cast for its removal.

"Exile up to one other target creature" chooses among the creatures its
controller may target (CR 115.1, 601.2c); a hexproof creature is not one of
them for an opponent's spell or ability (CR 702.11d). The AI's no-target
guards counted the opponent's creatures, not the creatures it could target,
so against a board of hexproof creatures (Domain Zoo: Leyline of the
Guildpact makes Scion of Draco grant hexproof to every creature) it evoked
Solitude, exiling a second card to pay, for an enter trigger with no legal
target (diagnosis 2026-11-15, Azorius Control vs Domain Zoo s50500 g1 t2).

The one targetability owner, `engine.target_solver.can_be_targeted`,
decides. Card names are fixture carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, opposing):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Domain Zoo"
    game.current_phase = Phase.MAIN1
    game.active_player = game.priority_player = 0
    game.turn_number = 3
    for _ in range(3):
        _add(game, card_db, "Plains", 0, "battlefield")
    for name in opposing:
        _add(game, card_db, name, 1, "battlefield")
    solitude = _add(game, card_db, "Solitude", 0, "hand")
    _add(game, card_db, "Supreme Verdict", 0, "hand")   # the evoke pitch
    return game, solitude


def _evoke_score(game, card):
    from ai.board_eval import Action, ActionType, evaluate_action
    return evaluate_action(game, player_idx=0,
                           action=Action(ActionType.EVOKE, {"card": card}))


def test_an_evoke_whose_only_targets_are_hexproof_is_not_made(card_db):
    game, solitude = _game(card_db, ["Thrun, the Last Troll", "Carnage Tyrant"])
    assert _evoke_score(game, solitude) < 0


def test_an_evoke_with_a_legal_target_is_still_weighed(card_db):
    """Regression: one targetable creature beside a hexproof one."""
    game, solitude = _game(card_db, ["Slippery Bogle", "Grizzly Bears"])
    from engine.target_solver import can_be_targeted
    bears = next(c for c in game.players[1].battlefield
                 if c.name == "Grizzly Bears")
    assert can_be_targeted(bears, solitude, 0)
    from ai.board_eval import _legal_removal_targets
    assert _legal_removal_targets(game, solitude, 0) == [bears]


def test_a_flash_evoke_whose_only_targets_are_hexproof_is_not_deployed(
        card_db):
    """The flash-deploy decision (`EVPlayer.decide_flash_deploy`, the
    path the Zoo replays took) prices the evoked removal the same way."""
    from ai.ev_player import EVPlayer
    game, solitude = _game(card_db, ["Thrun, the Last Troll",
                                     "Carnage Tyrant"])
    game.active_player = 1                      # the opponent's turn
    ai = EVPlayer(player_idx=0, deck_name="Azorius Control",
                  rng=random.Random(0))
    assert ai.decide_flash_deploy(game, [solitude]) is not solitude
