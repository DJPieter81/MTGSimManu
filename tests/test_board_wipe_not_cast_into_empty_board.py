"""A board wipe is never cast when it would destroy nothing.

Root cause (post-sweep control-execution investigation, 2026-08-25): the
X-cost board-wipe waste gate in `ai/ev_player.py::_gate_x_cost_board_wipe`
opened with

    opp_nonland = [c for c in opp.battlefield if not c.template.is_land]
    if not ('board_wipe' in tags and t.x_cost_data and opp_nonland):
        return None

so when the opponent controlled NO nonland permanents the gate declined to
apply at all. That is exactly backwards: an empty opposing board is not the
"gate does not apply" case, it is the MAXIMALLY wasteful case. The gate
already floors `kill_count == 0`; it simply never reached that check.

Observed consequence (4/5c Control vs Domain Zoo, seed 50500): on turn 4,
against a board with zero creatures, control cast its sweeper for X=0 and
destroyed 0 permanents — discarding its single best card against aggro for no
effect. Zoo then deployed and won on turn 9. This is why the matchup did not
move when earlier work made the sweeper castable: it was being cast, but into
an empty board.

Rule under test: a board wipe whose X-budget kills nothing is not chosen.
Mechanic-driven (kill count), no card names asserted.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.card_database import CardDatabase
from engine.game_state import GameState, Phase
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import X_BOARD_WIPE_WASTE_FLOOR

_DB = CardDatabase()


def _add(game, name, controller=0, zone="battlefield"):
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


def _control_board(opp_creatures=()):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.priority_player = 0
    game.current_phase = Phase.MAIN1
    game.turn_number = 7
    me, opp = game.players[0], game.players[1]
    me.deck_name = "4/5c Control"
    opp.deck_name = "Domain Zoo"
    me.life, opp.life = 13, 17
    for _ in range(4):
        _add(game, "Plains", 0)
    for nm in opp_creatures:
        _add(game, nm, 1)
    wipe = _add(game, "Wrath of the Skies", 0, "hand")
    return game, wipe


def test_x_cost_wipe_is_floored_against_an_empty_board():
    game, wipe = _control_board(opp_creatures=())
    me, opp = game.players[0], game.players[1]
    snap = snapshot_from_game(game, 0)
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    assert snap.opp_creature_count == 0, "fixture premise: empty opposing board"

    gated = ai._gate_x_cost_board_wipe(
        0.0, wipe.template, getattr(wipe.template, "tags", set()), snap, opp)
    assert gated is not None, (
        "the waste gate must APPLY when the opposing board is empty — that is "
        "the most wasteful case, not an inapplicable one")
    assert gated <= X_BOARD_WIPE_WASTE_FLOOR, (
        f"a wipe that kills nothing must be floored; got {gated}")


def test_wipe_is_not_the_chosen_play_against_an_empty_board():
    """End-to-end: the AI must not spend its sweeper on nothing."""
    game, wipe = _control_board(opp_creatures=())
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    decision = ai.decide_main_phase(game)
    if decision is not None:
        action, card, _targets = decision
        assert card.instance_id != wipe.instance_id, (
            "casting a board wipe that destroys 0 permanents discards the "
            "deck's best answer for no effect")


def test_wipe_still_allowed_when_it_actually_kills_something():
    """Control case — the fix must not disable sweepers wholesale."""
    # THREE real bodies, not one: the gate deliberately floors a one-kill
    # sweep on a low-power creature (that is spot removal, not a sweep), so a
    # single weak creature would floor for a legitimate reason and this control
    # case would prove nothing.
    game, wipe = _control_board(opp_creatures=(
        "Doorkeeper Thrull", "Doorkeeper Thrull", "Doorkeeper Thrull"))
    me, opp = game.players[0], game.players[1]
    snap = snapshot_from_game(game, 0)
    ai = EVPlayer(player_idx=0, deck_name="4/5c Control",
                  rng=random.Random(0))
    assert snap.opp_creature_count > 0, "fixture premise: a real target exists"
    gated = ai._gate_x_cost_board_wipe(
        0.0, wipe.template, getattr(wipe.template, "tags", set()), snap, opp)
    assert gated is None, (
        "with a killable target in range the gate must NOT floor the wipe — "
        "otherwise the fix would make control unable to sweep at all")
