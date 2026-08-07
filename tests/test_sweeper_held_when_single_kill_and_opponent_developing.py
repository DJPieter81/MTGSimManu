"""An X-cost sweeper must be held at kill_count==1 vs a developing opponent.

A board wipe's value curve rises with the opponent's board. The X-wipe
gate clamped one-kill sweeps only when the lone victim had power < 2,
so a 2-power token was judged "meaningful" and the AI spent its
sweeper as one-shot spot removal on turn 3 — while the aggro opponent
held a full grip — then died to the unanswered swarm two turns later
(replay: the Azorius-vs-Boros 10% probe, diagnostic
docs/diagnostics/2026-07-06_azorius_aggro_defense_root_cause.md).

Rule under test: a one-kill sweep while the opponent's hand is at or
above the development threshold (the existing
OPP_HAND_FULL_HOLDBACK_THRESHOLD signal) is clamped; the same sweep
against an empty-ish grip stays allowed (nothing better is coming);
desperation (PANIC/LETHAL) keeps its escape hatch.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from ai.scoring_constants import X_BOARD_WIPE_WASTE_FLOOR
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _setup(card_db, opp_hand_cards):
    """3 lands (x_budget=1 after base WW): only the cmc-0 2-power
    token is reachable — kill_count==1 at the cap. A cmc-2 threat
    sits out of reach."""
    game = GameState(rng=random.Random(0))

    for _ in range(3):
        _add(game, card_db, "Plains", controller=0, zone="battlefield")
    wipe = _add(game, card_db, "Wrath of the Skies", controller=0,
                zone="hand")

    # Reachable single kill: 2-power, cmc 0 (a +1/+1 counter lifts the
    # 1/1 body to 2 power — defeats the old killable_power<2 bar).
    tok = _add(game, card_db, "Memnite", controller=1, zone="battlefield")
    tok.plus_counters += 1
    assert tok.power == 2
    # Out-of-reach engine (cmc 2).
    _add(game, card_db, "Voice of Victory", controller=1,
         zone="battlefield")
    for _ in range(opp_hand_cards):
        _add(game, card_db, "Memnite", controller=1, zone="hand")
    _add(game, card_db, "Mountain", controller=1, zone="battlefield")

    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3

    player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    assert snap.my_life == 20  # healthy: not the desperation branch
    return game, player, snap, wipe


def _gate(player, game, snap, wipe):
    t = wipe.template
    return player._gate_x_cost_board_wipe(
        10.0, t, getattr(t, 'tags', set()), snap,
        game.players[1])


def test_one_kill_sweep_held_while_opponent_develops(card_db):
    game, player, snap, wipe = _setup(card_db, opp_hand_cards=5)
    clamped = _gate(player, game, snap, wipe)
    assert clamped is not None and clamped <= X_BOARD_WIPE_WASTE_FLOOR, (
        f"gate returned {clamped} for a one-kill sweep vs a full grip — "
        "the sweeper's development opportunity cost is not priced"
    )


def test_one_kill_sweep_allowed_when_opponent_out_of_gas(card_db):
    game, player, snap, wipe = _setup(card_db, opp_hand_cards=1)
    assert _gate(player, game, snap, wipe) is None, (
        "one-kill sweep vs an empty-ish grip must stay allowed — "
        "no bigger board is coming"
    )


def test_desperation_escape_hatch_unchanged(card_db):
    game, player, snap, wipe = _setup(card_db, opp_hand_cards=5)
    game.players[0].life = 3
    snap = snapshot_from_game(game, 0)
    assert _gate(player, game, snap, wipe) is None, (
        "at PANIC/LETHAL life a reachable kill must stay castable"
    )
