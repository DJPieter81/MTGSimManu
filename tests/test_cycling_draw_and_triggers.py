"""Cycling mechanics — draw replacement and global battlefield triggers.

CR 702.28: Cycling is an activated ability.  Pay the cycling cost,
discard this card: draw a card.  Some permanents have "whenever you
cycle a card" triggered abilities that fire each time any card is
cycled by their controller.

Two mechanics under test:

1. cycle_discards_and_draws_replacement
   - Card leaves hand and enters graveyard (the discard half).
   - Exactly one card is drawn from library (the draw half).
   - Net hand size: unchanged (−1 discard +1 draw).

2. global_cycling_trigger_fires_when_any_card_is_cycled
   - A battlefield permanent whose oracle text matches
     "whenever you cycle another card" must resolve its effect
     each time any OTHER card is cycled by the same controller.
   - Mechanism under test: `activate_cycling` fires
     `has_cycling_watch_trigger` effects from the controller's
     battlefield after moving the card to the graveyard.
   - Observable effects: damage to each opponent (damage-subtype)
     or life gain (lifegain-subtype).
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState


# ─── helpers ──────────────────────────────────────────────────────────────────


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card missing from DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _add_to_library(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card missing from DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="library",
    )
    card._game_state = game
    game.players[controller].library.append(card)
    return card


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"card missing from DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


# ─── test class ───────────────────────────────────────────────────────────────


class TestCyclingDrawAndTriggers:

    # ── 1. basic discard + draw ──────────────────────────────────────────────

    def test_cycling_discards_card_and_draws_replacement(self, card_db):
        """CR 702.28a: pay cycling cost, discard this card, draw a card.

        Mechanic: the cycled card must leave the hand and enter the
        graveyard; exactly one card must be drawn from the library to
        replace it.  Net hand size is unchanged (−1 + 1 = 0 delta).
        """
        game = GameState(rng=random.Random(1))
        # Street Wraith cycles by paying 2 life — no mana required.
        wraith = _add_to_hand(game, card_db, "Street Wraith", controller=0)
        bolt = _add_to_library(game, card_db, "Lightning Bolt", controller=0)

        hand_before = len(game.players[0].hand)
        life_before = game.players[0].life

        ok = game.activate_cycling(0, wraith)

        assert ok, "activate_cycling returned False for a legal cycling activation"

        # Discard half: Street Wraith must be in graveyard.
        gy = [c.name for c in game.players[0].graveyard]
        assert "Street Wraith" in gy, (
            f"Cycled card must move from hand to graveyard (CR 702.28a). "
            f"Graveyard = {gy!r}."
        )

        # Street Wraith must not remain in hand.
        hand = [c.name for c in game.players[0].hand]
        assert "Street Wraith" not in hand, (
            f"Cycled card must leave the hand. Hand = {hand!r}."
        )

        # Draw half: exactly one card drawn.
        assert len(game.players[0].hand) == hand_before, (
            f"Net hand size must be unchanged (−1 discard +1 draw). "
            f"Before={hand_before}, after={len(game.players[0].hand)}."
        )

        # The drawn card must come from library (library shrank by 1).
        assert len(game.players[0].library) == 0, (
            "Library had 1 card; after cycling the draw should empty it."
        )

        # Life cost paid (Street Wraith cycles for 2 life).
        assert game.players[0].life == life_before - 2, (
            f"Street Wraith's cycling cost is 2 life. "
            f"Before={life_before}, after={game.players[0].life}."
        )

    # ── 2. global cycling-watch trigger ─────────────────────────────────────

    def test_global_cycling_trigger_fires_when_any_card_is_cycled(self, card_db):
        """Battlefield permanents with 'whenever you cycle another card'
        must fire each time any card (other than themselves) is cycled
        by their controller (CR 702.28c class — global cycling watchers).

        Mechanism: a card whose oracle text matches the cycling-watch
        pattern must have `has_cycling_watch_trigger=True` on its
        template; `activate_cycling` must scan the controller's
        battlefield after the cycling action and resolve each watcher's
        effect.

        Test vehicle: a cycling-watch creature that deals 1 damage to
        each opponent.  After cycling one card, the opponent's life
        total must decrease by exactly 1.
        """
        # Drannith Stinger: "Whenever you cycle another card, this
        # creature deals 1 damage to each opponent."
        stinger_tmpl = card_db.get_card("Drannith Stinger")
        assert stinger_tmpl is not None, "Drannith Stinger must be in the card DB"

        # Confirm the template field is parsed.
        assert stinger_tmpl.has_cycling_watch_trigger, (
            "Drannith Stinger's oracle text ('Whenever you cycle another "
            "card, this creature deals 1 damage to each opponent.') "
            "must set has_cycling_watch_trigger=True on its template. "
            "Current value: False.  The oracle_parser is not detecting "
            "the cycling-watch pattern."
        )

        game = GameState(rng=random.Random(2))
        # Stinger on battlefield (controller=0).
        _add_to_battlefield(game, card_db, "Drannith Stinger", controller=0)

        # Street Wraith in hand (cycled for 2 life, no mana needed).
        _add_to_hand(game, card_db, "Street Wraith", controller=0)
        # Something to draw so the cycle draw doesn't mill.
        _add_to_library(game, card_db, "Lightning Bolt", controller=0)

        opp_life_before = game.players[1].life

        game.activate_cycling(0, game.players[0].hand[0])  # cycle Street Wraith

        assert game.players[1].life == opp_life_before - 1, (
            f"Drannith Stinger's cycling-watch trigger must deal 1 damage "
            f"to the opponent when any card is cycled (CR 702.28c class). "
            f"Opponent life before={opp_life_before}, "
            f"after={game.players[1].life}.  "
            f"Expected {opp_life_before - 1}. "
            f"The cycling-watch trigger was not fired by activate_cycling."
        )

    def test_cycling_watch_trigger_life_gain_fires_on_cycle(self, card_db):
        """'Whenever you cycle another card, you gain 1 life' must fire
        each time any card is cycled (life-gain subtype of the
        cycling-watch mechanic).

        Mechanic name: cycling-watch-lifegain (CR 702.28c family).
        """
        healer_tmpl = card_db.get_card("Drannith Healer")
        assert healer_tmpl is not None, "Drannith Healer must be in the card DB"

        assert healer_tmpl.has_cycling_watch_trigger, (
            "Drannith Healer's oracle text ('Whenever you cycle another "
            "card, you gain 1 life.') must set has_cycling_watch_trigger=True. "
            "Current: False."
        )

        game = GameState(rng=random.Random(3))
        _add_to_battlefield(game, card_db, "Drannith Healer", controller=0)
        _add_to_hand(game, card_db, "Street Wraith", controller=0)
        _add_to_library(game, card_db, "Lightning Bolt", controller=0)
        game.players[0].life = 10  # set a known starting life total

        game.activate_cycling(0, game.players[0].hand[0])

        # Life: −2 from Street Wraith cycling cost, +1 from Drannith Healer trigger.
        expected = 10 - 2 + 1
        assert game.players[0].life == expected, (
            f"Drannith Healer cycling-watch trigger must gain 1 life. "
            f"Expected life={expected} (10 − 2 cycle cost + 1 trigger). "
            f"Got {game.players[0].life}."
        )
