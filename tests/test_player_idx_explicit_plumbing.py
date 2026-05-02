"""Player-idx fallback in board-eval helpers must read the *correct*
opponent when the explicit `player_idx` argument is omitted.

Reference: PR ~#210 (commit 03ccff3) fixed `_eval_evoke` by plumbing
`player_idx` through `evaluate_action`, but left a defensive
`getattr(me, 'index', 0)` fallback in three private helpers
(`_eval_evoke`, `_eval_dash`, `_eval_combo`).  The `Player` /
`PlayerState` class never carries an `index` attribute (its index
field is named `player_idx`), so the fallback silently returned 0
regardless of which player called.  When `me` was player 1, that
yielded `opp_idx = 1 - 0 = 1 = me itself` — every downstream read
hit the wrong board.

The bug class is structural: any board-eval helper that resolves
the opponent index from `me` alone must use the actual attribute
the player object carries.  PlayerState.player_idx exists from
construction (engine/game_state.py), so the correct fallback is
`getattr(me, 'player_idx', 0)`.

Rule encoded by these tests: a board-eval helper invoked with
`player_idx=None` must still read the *opponent's* state, not the
caller's own state.  Phrased mechanic-first: "opponent resolution
in `_eval_*` does not collapse to self when me is player 1".
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
    not the caller's own, even when `player_idx` is omitted."""

    def test_eval_evoke_reads_correct_opponent_when_me_is_player_one(
            self, card_db):
        """Pre-fix repro:

        - me = player 1 (AzCon-style controller of evoke removal).
        - opponent = player 0, has 1 creature (Memnite).
        - Caller omits `player_idx` (legacy path).

        Bug: fallback `getattr(me, 'index', 0)` returns 0, so
        opp_idx = 1 - 0 = 1 = me.  `opp.creatures` reads me's empty
        board → the "removal target exists" gate sees zero creatures
        and returns -10 (no-targets sentinel).

        Post-fix: fallback reads `me.player_idx` (= 1), opp_idx = 0,
        `opp.creatures` correctly returns the 1-power Memnite, the
        small-target heuristic engages, and the function returns the
        principled value (specifically -2.0 for the small-vanilla
        gate, NOT -10.0).

        Distinguishing values: -10.0 (wrong opp = empty) vs -2.0
        (correct opp + small-target gate).  The test asserts the
        correct-path return value is reachable from the fallback.
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

        # Call the helper WITHOUT explicit player_idx — the legacy
        # path that triggers the fallback.
        score = _eval_evoke(game, me, a, {'card': solitude},
                            player_idx=None)

        # Pre-fix: fallback returned 0, opp = me (empty), gate at
        # lines 217-218 returned -10.0.
        # Post-fix: fallback returns me.player_idx (=1), opp = P0
        # (has Memnite), gate proceeds and the small-target check
        # returns -2.0 (Solitude has 'gains life equal to its
        # power' oracle text → heals_opponent gate active).
        assert score != -10.0, (
            f"_eval_evoke returned -10.0 (no-targets sentinel) when "
            f"me=player 1.  This means the fallback resolved opp_idx "
            f"to me itself and read me's empty creature list.  "
            f"PlayerState carries `player_idx`, not `index`; the "
            f"fallback must use `getattr(me, 'player_idx', 0)`.\n"
            f"P0 creatures: {[c.name for c in game.players[0].creatures]}\n"
            f"P1 creatures: {[c.name for c in game.players[1].creatures]}"
        )


class TestEvalDashOpponentResolution:
    """Rule: `_eval_dash` must read the actual opponent's board for
    blocker assessment, not the caller's own."""

    def test_eval_dash_reads_correct_opponent_when_me_is_player_one(
            self, card_db):
        """Pre-fix repro:

        - me = player 1.
        - opponent = player 0, has 2 creatures (potential blockers).
        - Caller omits `player_idx`.

        Bug: fallback returns 0, opp_idx = 1 = me, `opp.creatures`
        reads me's empty board → `opp_has_blockers = False`,
        `opp_threatening = False` → score reflects "empty board"
        (early-game haste bonus active).

        Post-fix: fallback reads `me.player_idx` (=1), opp_idx = 0,
        sees 2 P0 creatures → `opp_threatening = True`, score
        reflects the dodge-removal bonus.

        The two paths diverge by at least the threatening-board
        bonus magnitude (1.0).  Test asserts the post-fix path
        (which sees opp creatures) is taken.
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
        score = _eval_dash(game, me, a, ctx, player_idx=None)

        # opp has 2 creatures → opp_threatening = True → +1.0 bonus.
        # Pre-fix: opp = me (empty) → no bonus.  Post-fix score must
        # reflect the threatening-opp signal.
        assert score >= 1.0 - 0.3, (  # 1.0 (threaten) - 0.3 (block-pen)
            f"_eval_dash returned {score:.2f}; expected the "
            f"threatening-opponent bonus (+1.0) to engage when P0 "
            f"has 2 creatures.  The fallback is reading me's empty "
            f"board instead of opp's board.\n"
            f"P0 creatures: {len(game.players[0].creatures)}\n"
            f"P1 creatures: {len(game.players[1].creatures)}"
        )


class TestEvalComboOpponentResolution:
    """Rule: `_eval_combo` must read the actual opponent's life total
    when checking for lethal."""

    def test_eval_combo_reads_correct_opp_life_when_me_is_player_one(
            self, card_db):
        """Pre-fix repro:

        - me = player 1, life = 20.
        - opponent = player 0, life = 5 (lethal-able).
        - projected_damage = 6 (lethal vs P0, NOT lethal vs P1).
        - Caller omits `player_idx`.

        Bug: fallback returns 0, opp_idx = 1 = me, `opp.life = 20`
        → 6 < 20, lethal gate skipped, score derives from resources
        only (returns < 10.0).

        Post-fix: fallback reads `me.player_idx` (=1), opp_idx = 0,
        `opp.life = 5` → 6 >= 5, lethal gate fires, score = 10.0.

        The lethal sentinel (10.0) is the cleanest possible
        discriminator: it is unreachable from the resource path.
        """
        game = _two_player_state(card_db)
        game.players[0].life = 5   # opp is at lethal threshold
        game.players[1].life = 20  # me is healthy

        me = game.players[1]
        a = BoardAssessment()
        a.opp_life = 5  # consistency with assessment

        ctx = {'projected_damage': 6}  # lethal vs P0 only
        score = _eval_combo(game, me, a, ctx, player_idx=None)

        # The lethal sentinel (10.0) is unreachable from the
        # resource-counting fallback path; its presence proves the
        # function read the correct opponent's life total.
        assert score >= 10.0, (
            f"_eval_combo returned {score:.2f}; expected the lethal "
            f"sentinel (10.0) because projected_damage=6 >= "
            f"P0.life=5.  Pre-fix the fallback resolved opp to me "
            f"(life=20), missing the lethal gate.\n"
            f"P0.life={game.players[0].life}, "
            f"P1.life={game.players[1].life}"
        )
