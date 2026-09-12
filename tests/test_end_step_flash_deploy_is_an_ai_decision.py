"""End-step flash deployment is the AI's decision, not the engine's.

`GameRunner._cast_instant_removal` cast EVERY castable flash creature in
the non-active player's hand at the end step — no legend-rule check, no
valuation. A second copy of a legendary flash creature was cast into its
own legend rule twice in one match (Broodscale vs Azorius Blink s50000).

Rule: the window asks the AI (`decide_flash_deploy`) which flash creature,
if any, to deploy; the AI respects the legend rule it already applies to
main-phase casts and deploys only a positive-EV body. The engine casts what
it is handed and nothing else. Card names are fixture carriers.
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
    return card


def _window(card_db, twin_on_battlefield: bool):
    from ai.ev_player import EVPlayer
    game = GameState(rng=random.Random(0))
    game.current_phase = Phase.END_STEP
    game.active_player = 0
    _add(game, card_db, "Mountain", 0, "battlefield")
    for _ in range(5):
        _add(game, card_db, "Island", 1, "battlefield")
    if twin_on_battlefield:
        _add(game, card_db, "Wan Shi Tong, Librarian", 1, "battlefield")
    flash = _add(game, card_db, "Wan Shi Tong, Librarian", 1, "hand")
    active_ai = EVPlayer(player_idx=0, deck_name="Boros Energy", rng=random.Random(0))
    responder_ai = EVPlayer(player_idx=1, deck_name="Dimir Midrange", rng=random.Random(0))
    return game, GameRunner(card_db=card_db), active_ai, responder_ai, flash


def test_a_legendary_flash_creature_is_not_deployed_into_its_own_legend_rule(card_db):
    game, runner, active_ai, responder_ai, flash = _window(card_db, twin_on_battlefield=True)
    runner._cast_instant_removal(game, responder_ai, active_ai,
                                 context="end_step", max_instants=3)
    assert flash in game.players[1].hand, (
        "a second copy of a legendary permanent was cast into the legend rule")


def test_the_window_casts_exactly_what_the_ai_hands_it(card_db):
    game, runner, active_ai, responder_ai, flash = _window(card_db, twin_on_battlefield=False)
    asked = []

    def _decline(g, candidates):
        asked.append([c.name for c in candidates])
        return None

    responder_ai.decide_flash_deploy = _decline
    runner._cast_instant_removal(game, responder_ai, active_ai,
                                 context="end_step", max_instants=3)
    assert asked and asked[0] == ["Wan Shi Tong, Librarian"], "the AI must be asked"
    assert flash in game.players[1].hand, "the AI declined — the engine casts nothing"

    responder_ai.decide_flash_deploy = lambda g, candidates: candidates[0]
    runner._cast_instant_removal(game, responder_ai, active_ai,
                                 context="end_step", max_instants=3)
    assert flash in game.players[1].battlefield, "the AI's choice is cast"


def test_the_ai_deploys_a_positive_ev_flash_body_at_the_end_step(card_db):
    game, runner, active_ai, responder_ai, flash = _window(card_db, twin_on_battlefield=False)
    assert responder_ai.decide_flash_deploy(game, [flash]) is flash
