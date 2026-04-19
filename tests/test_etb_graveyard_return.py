"""ETB graveyard-return oracle pattern.

Covers the "When this creature enters, return target card from your
graveyard to your hand" class of effects. Eternal Witness is the
canonical example; the pattern is generic enough that any future card
sharing that oracle shape (Archaeomancer, Greenwarden-style) should be
caught by the same resolver branch.

Shape of the invariant:

    Creature with "enters... return target card from your graveyard
    to your hand" ETBs → exactly one non-land card moves from GY to
    hand. The log names the returned card.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _put_in_graveyard(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="graveyard",
    )
    card._game_state = game
    game.players[controller].graveyard.append(card)
    return card


def _etb(game, card_db, name, controller):
    """Put a creature onto the battlefield and fire its ETB chain."""
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    game.players[controller].battlefield.append(card)
    game._handle_permanent_etb(card, controller)
    return card


class TestEternalWitnessEtb:
    """Eternal Witness ETB returns a non-land card from GY to hand."""

    def test_returns_card_to_hand(self, card_db):
        game = GameState(rng=random.Random(0))
        buried = _put_in_graveyard(game, card_db, "Lightning Bolt", 0)
        hand_before = list(game.players[0].hand)
        gy_before = list(game.players[0].graveyard)
        assert buried in gy_before

        _etb(game, card_db, "Eternal Witness", 0)

        assert buried not in game.players[0].graveyard, (
            f"{buried.name} still in GY after Eternal Witness ETB. "
            f"GY now: {[c.name for c in game.players[0].graveyard]}"
        )
        assert buried in game.players[0].hand, (
            f"{buried.name} not in hand after Eternal Witness ETB. "
            f"Hand now: {[c.name for c in game.players[0].hand]}"
        )
        assert len(game.players[0].hand) == len(hand_before) + 1

    def test_log_names_returned_card(self, card_db):
        game = GameState(rng=random.Random(0))
        _put_in_graveyard(game, card_db, "Lightning Bolt", 0)

        _etb(game, card_db, "Eternal Witness", 0)

        witness_lines = [l for l in game.log if "Eternal Witness" in l]
        assert witness_lines, f"no Eternal Witness log line: {game.log}"
        return_lines = [l for l in witness_lines if "Lightning Bolt" in l]
        assert return_lines, (
            f"Eternal Witness log does not name the returned card. "
            f"Lines: {witness_lines}"
        )

    def test_prefers_nonland_over_land(self, card_db):
        """With a land and a nonland in GY, Eternal Witness returns
        the nonland — lands are recoverable via fetches/ramp, spells
        are not."""
        game = GameState(rng=random.Random(0))
        _put_in_graveyard(game, card_db, "Mountain", 0)
        spell = _put_in_graveyard(game, card_db, "Lightning Bolt", 0)

        _etb(game, card_db, "Eternal Witness", 0)

        assert spell in game.players[0].hand, (
            f"Eternal Witness returned a land with a spell available. "
            f"Hand: {[c.name for c in game.players[0].hand]}"
        )

    def test_empty_graveyard_no_op(self, card_db):
        game = GameState(rng=random.Random(0))
        assert game.players[0].graveyard == []

        _etb(game, card_db, "Eternal Witness", 0)

        assert game.players[0].graveyard == []
        assert not any(c.name == "Lightning Bolt"
                       for c in game.players[0].hand)
