"""Board-eval helpers must read the *correct* opponent when the
caller threads `player_idx` through.

Reference: PR ~#210 (commit 03ccff3) fixed `_eval_evoke` by plumbing
`player_idx` through `evaluate_action`, but left a defensive
`getattr(me, 'index', 0)` fallback in three private helpers
(`_eval_evoke`, `_eval_dash`, `_eval_combo`).  PR ~#262 escalated
this and made `player_idx` a required keyword-only argument so the
fallback can no longer mask a forgotten argument at the call site.
The TypeError contract is asserted in
`tests/test_board_eval_player_idx_required.py`.

This file complements that: it asserts the *correctness* contract.
When the caller correctly threads `player_idx`, the helper must
read the actual opponent's board, not the caller's own.  Phrased
mechanic-first: "opponent resolution in `_eval_*` does not
collapse to self when me is player 1".
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
    """Build a 2-player state where each side has a distinct board.

    Player 0 has a 1/1 vanilla creature.  Player 1 has none.  This
    asymmetry is the discriminator: any helper that mistakes "me" for
    "opp" will read the wrong creature count.
    """
    game = GameState(rng=random.Random(0))
    # P0 board: a single 1-power vanilla creature
    _add(game, card_db, "Memnite", controller=0, zone="battlefield")
    # P1 board: empty (will receive evoke fixture below)
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Azorius Control"
    game.players[0].life = 20
    game.players[1].life = 20
    game.current_phase = Phase.MAIN1
    game.turn_number = 1
    return game


class TestEvalEvokeOpponentResolution:
    """Rule: `_eval_evoke` must read the actual opponent's board,
    not the caller's own, when `player_idx` is supplied explicitly."""

    def test_eval_evoke_reads_correct_opponent_when_me_is_player_one(
            self, card_db):
        """Repro of the original bug class:

        - me = player 1 (AzCon-style controller of evoke removal).
        - opponent = player 0, has 1 creature (Memnite).
        - Caller passes `player_idx=1` (the correct value).

        With the index attribute fixed, the helper resolves
        opp_idx = 0 and reads P0's board: Memnite is a 1/1 vanilla,
        Solitude's small-target heuristic engages, the function
        returns -2.0.

        The original bug returned -10.0 because opp_idx collapsed
        to me's index, reading P1's empty creature list and
        triggering the no-targets sentinel.  We assert the helper
        no longer returns the no-targets sentinel — it correctly
        sees Memnite on P0's side.
        """
        game = _two_player_state(card_db)
        # Solitude on AzCon's (player 1) hand
        solitude = _add(game, card_db, "Solitude",
                        controller=1, zone="hand")
        me = game.players[1]
        a = BoardAssessment()
        a.mana_available = 0  # force the evoke path (no hardcast)
        # Verify the discriminator before the call: Memnite is a
        # 1/1 vanilla on P0, P1 has no creatures.
        assert len(game.players[0].creatures) == 1
        assert len(game.players[1].creatures) == 0

        # Call with the correct `player_idx`.  The helper must read
        # P0's board (Memnite present), not P1's (empty).
        score = _eval_evoke(game, me, a, {'card': solitude},
                            player_idx=1)

        # Wrong-opp path returned -10.0 (no-targets sentinel from
        # reading P1's empty board).  Correct-opp path returns -2.0
        # (small-target gate from reading P0's Memnite).
        assert score != -10.0, (
            f"_eval_evoke returned -10.0 (no-targets sentinel) when "
            f"me=player 1, player_idx=1.  This means opp_idx "
            f"resolved to me itself and read me's empty creature "
            f"list — the helper is computing 1 - player_idx wrong.\n"
            f"P0 creatures: {[c.name for c in game.players[0].creatures]}\n"
            f"P1 creatures: {[c.name for c in game.players[1].creatures]}"
        )


class TestEvalDashOpponentResolution:
    """Rule: `_eval_dash` must read the actual opponent's board for
    blocker assessment, not the caller's own."""

    def test_eval_dash_reads_correct_opponent_when_me_is_player_one(
            self, card_db):
        """Repro of the original bug class:

        - me = player 1.
        - opponent = player 0, has 2 creatures (potential blockers).
        - Caller passes `player_idx=1`.

        With opp_idx resolved correctly to 0, the helper sees 2 P0
        creatures, triggers `opp_threatening = True`, and the score
        reflects the dodge-removal bonus.

        The original bug saw opp = me (empty) and missed the bonus
        entirely, returning a near-zero score.  We assert the
        bonus is engaged.
        """
        game = _two_player_state(card_db)
        # Add a second P0 creature so opp_threatening becomes True
        _add(game, card_db, "Ornithopter", controller=0, zone="battlefield")
        # me (P1) has a dashable creature in hand; identity doesn't
        # matter for the opp-resolution check.
        dasher = _add(game, card_db, "Memnite", controller=1, zone="hand")
        game.turn_number = 5  # past the early-game haste-bonus window

        me = game.players[1]
        a = BoardAssessment()

        ctx = {'card': dasher, 'can_normal': True, 'can_dash': True}
        score = _eval_dash(game, me, a, ctx, player_idx=1)

        # opp has 2 creatures → opp_threatening = True → +1.0 bonus.
        # Wrong-opp path: opp = me (empty) → no bonus.  Correct-opp
        # path returns at least the threatening-board bonus magnitude.
        assert score >= 1.0 - 0.3, (  # 1.0 (threaten) - 0.3 (block-pen)
            f"_eval_dash returned {score:.2f}; expected the "
            f"threatening-opponent bonus (+1.0) to engage when P0 "
            f"has 2 creatures.  The helper is reading me's empty "
            f"board instead of opp's board.\n"
            f"P0 creatures: {len(game.players[0].creatures)}\n"
            f"P1 creatures: {len(game.players[1].creatures)}"
        )


class TestEvalComboOpponentResolution:
    """Rule: `_eval_combo` must read the actual opponent's life total
    when checking for lethal."""

    def test_eval_combo_reads_correct_opp_life_when_me_is_player_one(
            self, card_db):
        """Repro of the original bug class:

        - me = player 1, life = 20.
        - opponent = player 0, life = 5 (lethal-able).
        - projected_damage = 6 (lethal vs P0, NOT lethal vs P1).
        - Caller passes `player_idx=1`.

        With opp_idx resolved correctly to 0, opp.life = 5,
        projected_damage (6) >= opp.life (5), the lethal gate fires
        and the score is the sentinel 10.0.

        The original bug saw opp = me (life=20) and skipped the
        lethal gate entirely, returning a resource-derived score
        bounded above by 1.5.  The lethal sentinel (10.0) is
        unreachable from the resource path, so its presence proves
        the function read the correct opponent's life total.
        """
        game = _two_player_state(card_db)
        game.players[0].life = 5   # opp is at lethal threshold
        game.players[1].life = 20  # me is healthy

        me = game.players[1]
        a = BoardAssessment()
        a.opp_life = 5  # consistency with assessment

        ctx = {'projected_damage': 6}  # lethal vs P0 only
        score = _eval_combo(game, me, a, ctx, player_idx=1)

        # The lethal sentinel (10.0) is unreachable from the
        # resource-counting path; its presence proves the function
        # read the correct opponent's life total.
        assert score >= 10.0, (
            f"_eval_combo returned {score:.2f}; expected the lethal "
            f"sentinel (10.0) because projected_damage=6 >= "
            f"P0.life=5.  Wrong-opp path resolved opp to me "
            f"(life=20), missing the lethal gate.\n"
            f"P0.life={game.players[0].life}, "
            f"P1.life={game.players[1].life}"
        )
