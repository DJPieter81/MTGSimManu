"""Spell bounce oracle pattern.

Covers instants/sorceries that say "return target nonland permanent
to its owner's hand." Canonical case: Sink into Stupor.

Shape of the invariant:

    Spell with `return target nonland permanent to its owner's hand`
    resolves → exactly one nonland permanent on opponent's
    battlefield moves to opponent's hand. Lands are NOT valid
    targets (the previous named handler targeted any permanent).
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


def _put_on_bf(game, card_db, name, controller):
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
    return card


def _resolve_spell(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    spell = CardInstance(
        template=tmpl,
        owner=controller,
        controller=controller,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    spell._game_state = game
    from engine.oracle_resolver import resolve_spell_from_oracle
    return resolve_spell_from_oracle(game, spell, controller)


class TestSinkIntoStuporBounce:
    """Sink into Stupor returns target nonland permanent to hand."""

    def test_bounces_opposing_creature(self, card_db):
        game = GameState(rng=random.Random(0))
        cat = _put_on_bf(game, card_db, "Scion of Draco", 1)

        fired = _resolve_spell(game, card_db, "Sink into Stupor // Soporific Springs", 0)

        assert fired
        assert cat not in game.players[1].battlefield
        assert cat in game.players[1].hand

    def test_never_targets_lands(self, card_db):
        """Oracle says 'nonland permanent' — mountains/islands must stay."""
        game = GameState(rng=random.Random(0))
        land = _put_on_bf(game, card_db, "Mountain", 1)
        cat = _put_on_bf(game, card_db, "Scion of Draco", 1)

        _resolve_spell(game, card_db, "Sink into Stupor // Soporific Springs", 0)

        assert land in game.players[1].battlefield, (
            f"Land was illegally bounced. BF: "
            f"{[c.name for c in game.players[1].battlefield]}"
        )
        assert cat not in game.players[1].battlefield

    def test_empty_battlefield_no_op(self, card_db):
        game = GameState(rng=random.Random(0))
        # Only lands opp-side; pattern should find no valid target.
        _put_on_bf(game, card_db, "Island", 1)

        _resolve_spell(game, card_db, "Sink into Stupor // Soporific Springs", 0)

        # Hand empty, land stays.
        assert game.players[1].hand == []
        assert len(game.players[1].battlefield) == 1
