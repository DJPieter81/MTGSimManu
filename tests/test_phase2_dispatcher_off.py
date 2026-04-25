"""Phase-2c.1 architectural invariant: state-query gate falls through.

In Phase 2a/2b the dispatcher was either entirely off (parity) or
entirely on (regression).  Phase 2c.1 narrows routing to *lethal-now*
chains only — combo categories without a lethal chain in
`find_all_chains` MUST score identically to the legacy path.

This test pins that invariant: with the flag artificially toggled
on/off, scoring a non-lethal-now combo card returns the same number.
The numeric value itself is irrelevant — the requirement is parity.
"""
from __future__ import annotations

import random

import pytest

import ai.outcome_ev as outcome_ev
from ai.ev_evaluator import snapshot_from_game
from ai.ev_player import EVPlayer
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


def test_score_spell_via_outcome_stub_returns_none():
    """The Phase-1 dispatcher stub still returns None (only the
    builder is wired in commit 1).  This pins the public surface."""
    result = outcome_ev.score_spell_via_outcome(
        None, None, None, None, None, None, None, None,
    )
    assert result is None


def test_no_lethal_chain_flag_on_off_parity(card_db, monkeypatch):
    """Phase-2c.1 invariant: when no lethal-this-turn chain exists,
    `_score_spell` returns the same value with the flag ON or OFF.

    This guards against the Phase-2b regression where flipping the
    flag changed scoring for all combo categories indiscriminately.
    """
    game = GameState(rng=random.Random(0))
    for _ in range(2):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    for _ in range(2):
        _add(game, card_db, "Plains", controller=1, zone="battlefield")
    ritual = _add(game, card_db, "Pyretic Ritual", controller=0,
                  zone="hand")  # storm=0, no Grapeshot → no lethal

    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3
    game.players[0].lands_played_this_turn = 1
    game.players[0].spells_cast_this_turn = 0
    game._global_storm_count = 0
    game.players[0].life = 20
    game.players[1].life = 20

    player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    me = game.players[0]
    opp = game.players[1]

    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', False)
    score_off = player._score_spell(ritual, snap, game, me, opp)

    monkeypatch.setattr(outcome_ev, 'OUTCOME_DIST_COMBO', True)
    score_on = player._score_spell(ritual, snap, game, me, opp)

    assert abs(score_off - score_on) < 1e-9, (
        "Phase-2c.1 invariant violated: with no lethal chain, "
        f"flag-ON score ({score_on:.6f}) must equal flag-OFF score "
        f"({score_off:.6f}) — the gate should fall through to legacy."
    )
