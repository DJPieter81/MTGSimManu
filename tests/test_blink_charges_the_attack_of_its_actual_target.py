"""A pre-combat blink is charged the attack of the creature it will actually
return (CR 400.7).

The blinked permanent re-enters as a new object: summoning-sick, with only
its printed keywords. A temporary haste — a Dash cast, a reanimation or
pump grant — dies with the old object, so blinking that creature in our
first main phase forfeits its attack this turn. The forfeit charge in
`EVPlayer._score_spell` priced only "presumed" targets (end-of-turn riders
and `etb_value` creatures). The engine's blink handler, though, returns the
highest-threat creature, so a dashed attacker with no ETB was blinked
uncharged. Replay: Jeskai Blink vs Broodscale Bloodchief s50000 G2 T5 —
Dash Ragavan, Ephemerate on it pre-combat (scored −0.03, cast), no attack,
then Ragavan chump-blocks and dies.

Rule: the charge includes the creature the blink resolves on — the same
`_presumed_reset_target` choice the engine's handler makes. Card names are
fixture carriers only.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _put(game, card_db, name, controller, zone, sick=False):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = sick
    getattr(game.players[controller], zone).append(c)
    return c


def _score_blink(card_db, phase, dashed=True):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = phase
    game.turn_number = 5
    game.players[0].deck_name = "Jeskai Blink"
    game.players[1].deck_name = "Broodscale Bloodchief"
    for n in ("Hallowed Fountain", "Elegant Parlor"):
        _put(game, card_db, n, 0, "battlefield")
    for _ in range(4):
        _put(game, card_db, "Forest", 1, "battlefield")
    for _ in range(8):
        _put(game, card_db, "Plains", 0, "library")
    attacker = _put(game, card_db, "Ragavan, Nimble Pilferer", 0,
                    "battlefield", sick=dashed)
    attacker._dashed = dashed
    blink = _put(game, card_db, "Ephemerate", 0, "hand")
    ai = EVPlayer(player_idx=0, deck_name="Jeskai Blink", rng=random.Random(0))
    ai._init_deck_knowledge(game)
    snap = snapshot_from_game(game, 0)
    assert ai._presumed_reset_target(game.players[0], snap) is attacker
    return ai._score_spell(blink, snap, game, game.players[0],
                           game.players[1]), ai, attacker, snap


def test_a_precombat_blink_is_charged_the_attack_of_the_creature_it_returns(card_db):
    ev, ai, attacker, snap = _score_blink(card_db, Phase.MAIN1)
    assert ai._blink_would_forfeit_attack(attacker)
    charge = ai._forfeited_attack_charge(attacker, snap)
    assert charge > 0
    assert ev <= -charge / 2, (ev, charge)


def test_the_same_blink_after_combat_is_not_charged(card_db):
    ev_main2, *_ = _score_blink(card_db, Phase.MAIN2)
    ev_main1, *_ = _score_blink(card_db, Phase.MAIN1)
    assert ev_main2 > ev_main1


def test_a_creature_that_cannot_attack_now_forfeits_nothing_to_a_blink(card_db):
    # A creature that cannot attack this turn forfeits nothing by being
    # blinked, so the charge is never applied to it.
    ev_sick, ai, attacker, snap = _score_blink(card_db, Phase.MAIN1,
                                               dashed=False)
    attacker.summoning_sick = True
    assert not ai._blink_would_forfeit_attack(attacker)
