"""Board evaluator helpers must require explicit `player_idx`.

Rule encoded by these tests:

    *Board evaluator functions (`_eval_evoke`, `_eval_dash`,
     `_eval_combo`) raise TypeError when called without an explicit
     `player_idx` keyword argument.*

Background
----------
Prior to this change, the three private helpers in `ai/board_eval.py`
defended a `player_idx is None` branch by reading
``getattr(me, 'player_idx', 0)`` as a silent backup.  The fallback
worked by coincidence — `PlayerState` happens to carry
``player_idx`` — but it papered over caller bugs: any future call
that omitted the keyword would silently route through player 0
regardless of the player intended.

The latent failure mode was documented in
``docs/diagnostics/2026-05-02_me_index_sister_bug_audit.md`` and
escalated to P0 in
``docs/proposals/2026-05-03_p0_p1_backlog.md`` (P0-D, latent).

The principled fix is to make `player_idx` a required keyword
argument with no default.  Callers that omit it must fail loudly
at the call site, not silently misread the board.  Each helper
takes `player_idx` as a keyword-only argument so positional
mistakes also surface as `TypeError`.

These three tests assert the contract.  Each test is RED on the
pre-fix code (the helper silently returned a fallback score) and
GREEN once the fallback is dropped.
"""
from __future__ import annotations

import random

import pytest

from ai.board_eval import (
    BoardAssessment,
    _eval_combo,
    _eval_dash,
    _eval_evoke,
)
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


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
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _two_player_state(card_db):
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Azorius Control"
    game.players[0].life = 20
    game.players[1].life = 20
    game.current_phase = Phase.MAIN1
    game.turn_number = 1
    return game


class TestEvalEvokeRequiresPlayerIdx:
    """Rule: `_eval_evoke` raises TypeError when `player_idx` is omitted."""

    def test_eval_evoke_raises_when_player_idx_omitted(self, card_db):
        """Calling `_eval_evoke` without `player_idx` must raise
        TypeError — no silent fallback, no default value."""
        game = _two_player_state(card_db)
        solitude = _add(game, card_db, "Solitude",
                        controller=1, zone="hand")
        me = game.players[1]
        a = BoardAssessment()

        with pytest.raises(TypeError):
            _eval_evoke(game, me, a, {'card': solitude})

    def test_eval_evoke_raises_when_player_idx_is_none(self, card_db):
        """Passing `player_idx=None` explicitly must also raise —
        the parameter is required, not nullable."""
        game = _two_player_state(card_db)
        solitude = _add(game, card_db, "Solitude",
                        controller=1, zone="hand")
        me = game.players[1]
        a = BoardAssessment()

        with pytest.raises(TypeError):
            _eval_evoke(game, me, a, {'card': solitude},
                        player_idx=None)


class TestEvalDashRequiresPlayerIdx:
    """Rule: `_eval_dash` raises TypeError when `player_idx` is omitted."""

    def test_eval_dash_raises_when_player_idx_omitted(self, card_db):
        game = _two_player_state(card_db)
        dasher = _add(game, card_db, "Memnite", controller=1, zone="hand")
        me = game.players[1]
        a = BoardAssessment()
        ctx = {'card': dasher, 'can_normal': True, 'can_dash': True}

        with pytest.raises(TypeError):
            _eval_dash(game, me, a, ctx)

    def test_eval_dash_raises_when_player_idx_is_none(self, card_db):
        game = _two_player_state(card_db)
        dasher = _add(game, card_db, "Memnite", controller=1, zone="hand")
        me = game.players[1]
        a = BoardAssessment()
        ctx = {'card': dasher, 'can_normal': True, 'can_dash': True}

        with pytest.raises(TypeError):
            _eval_dash(game, me, a, ctx, player_idx=None)


class TestEvalComboRequiresPlayerIdx:
    """Rule: `_eval_combo` raises TypeError when `player_idx` is omitted."""

    def test_eval_combo_raises_when_player_idx_omitted(self, card_db):
        game = _two_player_state(card_db)
        me = game.players[1]
        a = BoardAssessment()
        ctx = {'projected_damage': 6}

        with pytest.raises(TypeError):
            _eval_combo(game, me, a, ctx)

    def test_eval_combo_raises_when_player_idx_is_none(self, card_db):
        game = _two_player_state(card_db)
        me = game.players[1]
        a = BoardAssessment()
        ctx = {'projected_damage': 6}

        with pytest.raises(TypeError):
            _eval_combo(game, me, a, ctx, player_idx=None)
