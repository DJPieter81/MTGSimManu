"""The X-cost board-wipe gate must judge the wipe at the X the AI will
ACTUALLY pick (pick_wipe_x_value), not the max-affordable X — so a wipe
that resolves for X=0 and destroys nothing is held, even under pressure.

Rule: an X-MV board wipe ("destroy each artifact/creature/enchantment
with mana value <= X") must not be cast when its value-maximizing X
destroys nothing worthwhile. The enumeration gate derived its killable
set from `cap` (the max affordable X), so a single worthless in-budget
target (a power-0 high-MV enchantment) counted as a kill and let the
wipe through; the resolution-time picker then chose X=0 and destroyed
nothing — the sweeper thrown away (audit: Azorius Control vs Domain Zoo,
s56410 — Wrath of the Skies cast into a board whose only in-budget
target was Leyline Binding, "(X=0) destroying 0 permanents", while a
board-dominating Scion of Draco (out of budget) survived).

The gate and the engine picker (engine.cast_manager.pick_wipe_x_value)
must agree: the gate judges the wipe the AI will cast.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from ai.scoring_constants import X_BOARD_WIPE_WASTE_FLOOR
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _bf(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[controller].battlefield.append(c)
    return c


def _hand(game, card_db, name, controller):
    t = card_db.get_card(name)
    assert t is not None, f"missing {name}"
    c = CardInstance(template=t, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="hand")
    c._game_state = game
    game.players[controller].hand.append(c)
    return c


def test_x_wipe_held_when_picker_chosen_x_destroys_nothing_even_when_desperate(card_db):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Domain Zoo"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 12
    # Defender low on life and under a lethal clock → desperate life phase.
    game.players[0].life = 4
    for _ in range(8):  # 8 lands → Wrath base {W}{W}=2, X budget 6
        _bf(game, card_db, "Plains", controller=0)
    wrath = _hand(game, card_db, "Wrath of the Skies", controller=0)

    # Opp board: a board-dominating creature OUT of the X budget (so the
    # wipe cannot reach it) + a single in-budget but worthless target (a
    # power-0 enchantment). The value-maximizing X is 0 — the wipe kills
    # nothing worth its cost.
    big = card_db.get_card("Craterhoof Behemoth")  # MV 8 (> budget 6), a real clock
    if big is not None:
        c = CardInstance(template=big, owner=1, controller=1,
                         instance_id=game.next_instance_id(), zone="battlefield")
        c._game_state = game
        c.enter_battlefield()
        c.summoning_sick = False
        game.players[1].battlefield.append(c)
    _bf(game, card_db, "Leyline Binding", controller=1)  # MV 6, enchantment, power 0

    player = EVPlayer(player_idx=0, deck_name="Azorius Control", rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    ev = player._score_spell(wrath, snap, game, game.players[0], game.players[1])

    assert ev <= X_BOARD_WIPE_WASTE_FLOOR, (
        f"the wipe scored {ev:.2f} (> floor {X_BOARD_WIPE_WASTE_FLOOR:.2f}); it "
        "must be held — its value-maximizing X destroys nothing worthwhile, so "
        "casting it (for X=0) throws the sweeper away even under pressure")
