"""CR 121.1 / CR 603 ETB "draw N" — a permanent whose own enter-the-
battlefield trigger draws a fixed number of cards must add exactly N
cards to its controller's hand when it enters through the real
resolution ETB pipeline.

The mechanic is routed through the generic draw-N ETB branch in
`engine.oracle_resolver.resolve_etb_from_oracle` (no per-card handler).
Thought Monitor ("When this creature enters, draw two cards.") is the
fixture carrier; the assertion is on the mechanic (N cards enter hand),
not the card. A companion assertion confirms the Affinity cost discount
is still parsed so the card lands early enough to matter.
"""
from __future__ import annotations

import random

import pytest

from engine.card_effects import EFFECT_REGISTRY, EffectTiming
from engine.cards import CardInstance, CardTemplate, CardType, ManaCost, Keyword
from engine.game_state import GameState


def _game_with_library(n_cards=10, controller=0):
    game = GameState(rng=random.Random(0))
    filler = CardTemplate(
        name="Filler", card_types=[CardType.CREATURE],
        mana_cost=ManaCost(generic=1), supertypes=[], subtypes=[],
        power=1, toughness=1, loyalty=None, keywords=set(), abilities=[],
        color_identity=set(), produces_mana=[], enters_tapped=False,
        oracle_text="", tags=set(),
    )
    game.players[controller].library = [
        CardInstance(template=filler, owner=controller, controller=controller,
                     instance_id=game.next_instance_id(), zone="library")
        for _ in range(n_cards)
    ]
    return game


class TestEtbDrawNAddsNCardsToHand:

    def test_thought_monitor_etb_draws_two_via_resolution_path(self, card_db):
        """Real DB card, full `_handle_permanent_etb` pipeline — no
        dedicated handler, so the generic draw-N branch must fire."""
        tmpl = card_db.get_card("Thought Monitor")
        assert not EFFECT_REGISTRY.has_handler(tmpl.name, EffectTiming.ETB), (
            "Thought Monitor must have no dedicated ETB handler — its "
            "draw-two must come from the generic draw-N ETB path"
        )

        game = _game_with_library(controller=0)
        card = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="stack")
        card._game_state = game
        # Mirror ResolutionManager: enter the battlefield, then fire ETB.
        card.controller = 0
        card.enter_battlefield()
        game.players[0].battlefield.append(card)

        before = len(game.players[0].hand)
        game._handle_permanent_etb(card, 0)
        assert len(game.players[0].hand) - before == 2

    def test_affinity_cost_discount_still_applies(self, card_db):
        """The Affinity discount at mana_payment.py:181-187 keys off
        Keyword.AFFINITY — confirm it still fires so the ETB-draw body
        lands cheaply (part C's second requirement). Behavioural: with
        enough artifacts the {6}{U} body is payable from a single blue
        source; without them it is not."""
        tmpl = card_db.get_card("Thought Monitor")
        assert Keyword.AFFINITY in tmpl.keywords

        def _bf(game, name, controller=0):
            c = CardInstance(
                template=card_db.get_card(name), owner=controller,
                controller=controller, instance_id=game.next_instance_id(),
                zone="battlefield")
            c._game_state = game
            game.players[controller].battlefield.append(c)
            return c

        # With 6 artifacts, generic 6 -> 0; only {U} remains.
        game = GameState(rng=random.Random(0))
        for _ in range(6):
            _bf(game, "Ornithopter")
        island = _bf(game, "Island")
        hand_card = CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game.next_instance_id(), zone="hand")
        game.players[0].hand.append(hand_card)

        paid = game.tap_lands_for_mana(0, tmpl.mana_cost,
                                       card_name="Thought Monitor")
        assert paid, (
            "Affinity discount failed: {6}{U} with 6 artifacts should be "
            "payable from a single blue source"
        )

        # Control: with NO artifacts the discount cannot apply, so a lone
        # Island cannot pay {6}{U}.
        game2 = GameState(rng=random.Random(1))
        _bf(game2, "Island")
        game2.players[0].hand.append(CardInstance(
            template=tmpl, owner=0, controller=0,
            instance_id=game2.next_instance_id(), zone="hand"))
        assert not game2.tap_lands_for_mana(
            0, tmpl.mana_cost, card_name="Thought Monitor"), (
            "with no artifacts, {6}{U} must not be payable from one land"
        )
