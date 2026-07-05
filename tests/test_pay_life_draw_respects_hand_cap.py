"""Pay-life draw activations stop at the cleanup hand cap.

CR 514.1: at cleanup the active player discards down to maximum hand
size.  A pay-life-draw activation (Griselbrand-class "Pay N life:
Draw N cards") taken while the hand is already at or above
MAX_HAND_SIZE converts life directly into cards that are discarded
at cleanup — a pure life loss with zero card-advantage, observed as
the 14-life suicide line in
docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md RC-4
(replay: pay 7, draw 7, pay 7, draw 7, discard 10 to hand size, die
on the crack-back).

Rule encoded: the activation loop's hand bound is MAX_HAND_SIZE —
the first activation from a small hand still fires (digging for
protection is the ability's purpose); re-activation on a full hand
does not.

Class size: every repeatable pay-life-draw activated ability parsed
into the 'pay_life_draw' tag — mechanic-driven, no card names in the
engine change.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.constants import MAX_HAND_SIZE
from engine.game_state import GameState
from engine.game_runner import GameRunner


def _bf_creature(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    game.players[controller].battlefield.append(card)
    return card


def _hand_card(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="hand",
    )
    game.players[controller].hand.append(card)
    return card


def _library_fill(game, card_db, name, controller, count):
    tmpl = card_db.get_card(name)
    for _ in range(count):
        card = CardInstance(
            template=tmpl,
            owner=controller,
            controller=controller,
            instance_id=game.next_instance_id(),
            zone="library",
        )
        game.players[controller].library.append(card)


class TestPayLifeDrawRespectsHandCap:

    def _setup(self, card_db, hand_size):
        game = GameState(rng=random.Random(0))
        game.players[0].life = 40  # life is not the binding constraint
        creature = _bf_creature(game, card_db, "Griselbrand", 0)
        creature.summoning_sick = False
        for _ in range(hand_size):
            _hand_card(game, card_db, "Swamp", 0)
        _library_fill(game, card_db, "Swamp", 0, 30)
        return game

    def test_activates_when_hand_below_cap(self, card_db):
        """Digging from a small hand is the ability's purpose — the
        gate must not block the first activation."""
        game = self._setup(card_db, hand_size=2)
        runner = GameRunner.__new__(GameRunner)
        runner._activate_pay_life_draw(game, 0)
        assert len(game.players[0].hand) > 2, (
            "pay-life draw did not fire from a small hand"
        )

    def test_no_activation_at_or_above_hand_cap(self, card_db):
        """Paying life while the hand is already at MAX_HAND_SIZE
        buys only cleanup discards (CR 514.1) — pure life loss."""
        game = self._setup(card_db, hand_size=MAX_HAND_SIZE)
        life_before = game.players[0].life
        runner = GameRunner.__new__(GameRunner)
        runner._activate_pay_life_draw(game, 0)
        assert game.players[0].life == life_before, (
            "paid life to draw past the cleanup hand cap"
        )

    def test_activation_loop_stops_once_cap_reached(self, card_db):
        """A single activation may overshoot the cap (draw 7 from a
        small hand); the LOOP must not re-activate on the now-full
        hand."""
        game = self._setup(card_db, hand_size=2)
        life_before = game.players[0].life
        runner = GameRunner.__new__(GameRunner)
        runner._activate_pay_life_draw(game, 0)
        life_spent = life_before - game.players[0].life
        assert life_spent <= 7, (
            f"re-activated on a full hand: spent {life_spent} life "
            f"(hand ended at {len(game.players[0].hand)})"
        )
