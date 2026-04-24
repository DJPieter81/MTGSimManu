"""Bundle 3 — A4 (REVERTED by Iteration-2 B3-Tune): holdback threshold
for spell-deck opponents triggers at hand-size >= 4, NOT >= 3.

Bundle-3 A4 briefly lowered the threshold to >=3, but the defender-
collapse investigation (post-Affinity session, N=50 matrix) showed
the lowered threshold over-fired: 3-card post-discard hands are
typically mostly lands, not threats, and the broad gate caused
defender decks to stall out against discard-heavy opponents
(Jeskai -5pp, Dimir -6pp, AzCon WST -8pp).

This test now pins the REVERT: a 3-card spell-deck grip with 0 power
must NOT trigger holdback. The stricter >=4 threshold is restored.
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
    """Iteration-2 B3-Tune reverts Bundle-3 A4. The spell-deck branch
    requires opp_hand_size >= 4. A 3-card hand (post-discard, mostly
    lands) must NOT trigger holdback — defenders need to deploy
    answers against discard-heavy opponents."""

    def test_three_card_spelldeck_opp_does_not_trigger_holdback(self, card_db):
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
        # hand_size = exactly 3 — below the restored >= 4 threshold.
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
        penalty = player._holdback_penalty(
            me, opp, snap, cost=augur.template.cmc or 0,
            exclude_instance_id=augur.instance_id,
        )

        assert penalty == 0.0, (
            f"3-card spell-deck opp with 0 power triggered holdback "
            f"penalty {penalty:.2f}; expected 0.0 after A4 revert. "
            f"Threshold must be restored to opp_hand_size >= 4."
        )
