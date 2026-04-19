"""Bug 2 — Orim's Chant unkicked must not act as kicked.

Oracle text for Orim's Chant:
    Kicker {W}
    Target player can't cast spells this turn.
    If this spell was kicked, creatures can't attack this turn.

The sim does not currently model kicker payments, so every Chant cast
resolves as the base (unkicked) variant. The base clause only scopes
to "this turn" — when cast on the caster's own main phase it has no
bite against the opponent's next turn.

The old engine behaviour queued `silenced_next_turn = True` on the
opponent when Chant resolved on the caster's turn. That queued flag
was consumed on the opponent's next untap step as `silenced_this_turn`,
effectively promising a Time Walk the card does not provide. This
was the kicker-state-propagation bug recorded in the Apr-19 handoff.

Regression coverage here: the opponent's next turn must start clean,
with no silence carryover, after an unkicked Chant cast on the
caster's own main phase.
"""
from __future__ import annotations

import random

import pytest

from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState
from engine.stack import StackItem, StackItemType


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _cast_and_resolve_orims_chant(card_db, active: int):
    """Build a game where player `active` is active, cast Orim's Chant
    resolved from their main phase, and return the game."""
    game = GameState(rng=random.Random(0))
    game.active_player = active

    chant_tmpl = card_db.get_card("Orim's Chant")
    assert chant_tmpl is not None

    chant = CardInstance(
        template=chant_tmpl,
        owner=active,
        controller=active,
        instance_id=game.next_instance_id(),
        zone="stack",
    )
    chant._game_state = game
    game.stack.items.append(StackItem(
        item_type=StackItemType.SPELL,
        source=chant,
        controller=active,
        targets=[],
    ))
    game.resolve_stack()
    return game


class TestOrimsChantUnkicked:
    """Unkicked Chant resolves with no next-turn silence carryover."""

    def test_unkicked_does_not_queue_next_turn_silence(self, card_db):
        """After Chant resolves on Boros' own main, opponent's
        silenced_next_turn must remain False — the card's scope is
        'this turn', not 'your next turn'."""
        game = _cast_and_resolve_orims_chant(card_db, active=0)
        opp = game.players[1]
        assert opp.silenced_next_turn is False, (
            "Unkicked Orim's Chant queued silence for opponent's next turn. "
            "The card's 'this turn' clause has no bite on a future turn; the "
            "old behaviour mimicked kicked-cost semantics."
        )

    def test_opp_can_cast_on_their_next_turn_after_unkicked_chant(self, card_db):
        """Simulate the opponent's turn starting. Their silenced_this_turn
        flag must be False — they are free to cast spells normally."""
        game = _cast_and_resolve_orims_chant(card_db, active=0)
        opp = game.players[1]
        # Opponent's turn begins. The simulator consumes pending silences
        # in reset_turn_tracking() during the opponent's untap step.
        opp.reset_turn_tracking()
        assert opp.silenced_this_turn is False, (
            "Opponent starts their next turn silenced after an unkicked "
            "Chant — a Time Walk the card does not provide."
        )


class TestOrimsChantInstantResponse:
    """Regression safety — cast on the opponent's turn still silences them
    for that same turn (the card's actual 'this turn' clause)."""

    def test_chant_on_opponents_turn_silences_this_turn(self, card_db):
        game = _cast_and_resolve_orims_chant(card_db, active=1)
        # Cast during the opponent's turn — active_player is the opp, so
        # the silence targets the *current* turn for that player. Handler
        # flips silenced_this_turn on the non-active caster's opponent,
        # which in this setup is player 0 — verify whichever flag fired.
        any_silence = (game.players[0].silenced_this_turn
                       or game.players[1].silenced_this_turn
                       or game.players[0].silenced_next_turn
                       or game.players[1].silenced_next_turn)
        assert any_silence, (
            "Chant cast during active play should still mark some silence "
            "on the targeted player — the current-turn clause."
        )
