"""Proactive blink credits re-triggering an on-board ETB-value creature.

Blinking your own ETB-value creature (Ephemerate on Solitude) re-fires its
ETB — re-exiling an opponent creature and gaining life while keeping the body:
a clean 2-for-1 and the whole premise of the Jeskai Blink "grind_value" plan.

Bug: the proactive scorer (`ev_player._score_spell` blink branch) handled
fizzle / M1-hold / Goryo's-rider clearance but gave NO credit for the ETB
re-trigger, so Ephemerate scored ~0 and was never cast in the main phase.
The credit constant (`BLINK_ETB_RETRIGGER_BONUS`) already existed but was
wired only into the reactive response path.

Rule under test: with a blink spell in hand and an on-board `etb_value`
creature to re-trigger, the blink scores strictly positive (above the
no-etb-creature baseline). Class: any blink/flicker spell × any ETB-value
creature. No card names in the scorer.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, db, name, controller, zone):
    t = db.get_card(name)
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


def _score_ephemerate(card_db, with_etb_creature: bool) -> float:
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN2  # avoid the M1-hold penalty branch
    game.turn_number = 5
    p = game.players[0]

    # Mana to cast Ephemerate ({W}).
    _add(game, card_db, "Plains", 0, "battlefield")

    if with_etb_creature:
        # Solitude: an etb_value creature whose ETB exiles a creature.
        _add(game, card_db, "Solitude", 0, "battlefield")
        # An opponent creature for the re-fired ETB to target.
        _add(game, card_db, "Ragavan, Nimble Pilferer", 1, "battlefield")
    else:
        # A vanilla body with no meaningful ETB.
        _add(game, card_db, "Ragavan, Nimble Pilferer", 0, "battlefield")

    eph = _add(game, card_db, "Ephemerate", 0, "hand")
    player = EVPlayer(player_idx=0, deck_name="Jeskai Blink",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    return player._score_spell(eph, snap, game, p, game.players[1])


def test_proactive_blink_of_etb_value_creature_scores_positive(card_db):
    ev_with = _score_ephemerate(card_db, with_etb_creature=True)
    assert ev_with > 0.0, (
        f"Ephemerate blinking an on-board etb_value creature (Solitude) must "
        f"score positive for the ETB re-trigger; got {ev_with:.2f}")


def test_blink_etb_credit_beats_no_etb_creature_baseline(card_db):
    ev_with = _score_ephemerate(card_db, with_etb_creature=True)
    ev_without = _score_ephemerate(card_db, with_etb_creature=False)
    assert ev_with > ev_without, (
        f"blinking an ETB-value creature must score above blinking a vanilla "
        f"body: with-ETB={ev_with:.2f} vs baseline={ev_without:.2f}")
