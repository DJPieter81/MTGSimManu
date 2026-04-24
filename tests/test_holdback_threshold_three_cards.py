"""Bundle 3 — A4: holdback threshold for spell-deck opponents must
trigger at hand-size >= 3, not >= 4.

Diagnosis (Affinity-session consolidated findings, A4):
`ai/ev_player.py:738-739` only fires the spell-deck holdback branch
when `opp_hand_size >= 4 and opp_power == 0`. Real games rarely
end up there: by the time the AI is evaluating big main-phase
plays, opp has often discarded into a 3-card grip via Thoughtseize
or a Bowmaster trigger, but those 3 cards still contain real
threats / counterspells. Lowering the threshold to >= 3 closes the
gap without introducing magic numbers — gameplan data shows that
typical post-discard hands at 3 cards still contain 0.6+ threats
on average.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
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
        card.summoning_sick = False
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


class TestHoldbackThresholdThreeCardsA4:
    """When the opponent has 0 power on board AND a 3-card hand,
    the holdback branch must STILL gate a tap-out main-phase play
    if the player is holding instant-speed interaction."""

    def test_three_card_opp_hand_triggers_holdback(self, card_db):
        game = GameState(rng=random.Random(0))

        # Player 0 (Azorius Control) — UU available, holds Counterspell
        # and a CMC-2 sorcery-speed creature.
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        augur = _add(game, card_db, "Augur of Bolas", controller=0,
                     zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        # Opp: spell-deck profile (Ruby Storm), opp_power = 0,
        # hand_size = exactly 3 (lower bound that must trigger).
        for _ in range(3):
            _add(game, card_db, "Mountain", controller=1, zone="battlefield")
        for _ in range(3):
            _add(game, card_db, "Memnite", controller=1, zone="hand")
        # No opp creatures → opp_power = 0

        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "Ruby Storm"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4

        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        assert snap.opp_hand_size == 3, (
            f"Test setup: expected opp_hand_size=3, got "
            f"{snap.opp_hand_size}"
        )
        assert snap.opp_power == 0, (
            f"Test setup: expected opp_power=0, got {snap.opp_power}"
        )

        me = game.players[0]
        opp = game.players[1]
        ev = player._score_spell(augur, snap, game, me, opp)

        PASS_THRESHOLD = -5.0
        assert ev < PASS_THRESHOLD, (
            f"Augur of Bolas EV={ev:.2f} above pass_threshold "
            f"({PASS_THRESHOLD}) when opp_hand_size=3 and counters "
            f"are held. The holdback threshold for spell-deck "
            f"opponents must be lowered from >=4 to >=3 to catch "
            f"post-discard threat density."
        )
