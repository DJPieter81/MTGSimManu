"""Bundle 3 — A3: holdback penalty must also gate cycling and equip
activations, not only spell casts.

Diagnosis (Affinity-session consolidated findings, A3):
`ai/ev_player.py:_score_cycling` (CYCLING) and `_consider_equip`
(EQUIPMENT activation) currently have ZERO holdback coverage. The
control AI happily taps its last U source to cycle Curator of
Mysteries, or its last available mana to attach Cranial Plating —
even with a Counterspell in hand vs an aggressive opponent.

Fix: extract a helper `_holdback_penalty(me, opp, snap, cost)` and
call it from `_score_spell`, `_score_cycling`, and `_consider_equip`.
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


def _add(game, card_db, name, controller, zone, summoning_sick=False):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = summoning_sick
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


class TestHoldbackCoversCyclingA3:
    """Cycling Curator of Mysteries (cost 1U) on a board where the
    only untapped U source IS that single Island must score below
    pass_threshold when a Counterspell is held against an active
    creature opponent — same gate as _score_spell."""

    def test_cycling_blocked_when_taps_last_response_source(self, card_db):
        game = GameState(rng=random.Random(0))

        # Exactly 2 Islands: enough for cycling cost {1U} (1 generic +
        # 1 U) and enough for Counterspell {UU}. After cycling both
        # Islands are tapped → no UU left for the held Counterspell.
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")

        # Curator of Mysteries — cycling cost {1}{U}
        curator = _add(game, card_db, "Curator of Mysteries", controller=0,
                       zone="hand")
        # The held interaction
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        # Aggressive opponent (Affinity creatures) — holdback relevant
        _add(game, card_db, "Memnite", controller=1, zone="battlefield")
        _add(game, card_db, "Ornithopter", controller=1, zone="battlefield")
        for _ in range(3):
            _add(game, card_db, "Memnite", controller=1, zone="hand")

        game.players[0].deck_name = "Azorius Control"
        game.players[1].deck_name = "Affinity"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4

        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        ev = player._score_cycling(curator, snap, game, me, opp)

        PASS_THRESHOLD = -5.0
        assert ev < PASS_THRESHOLD, (
            f"Cycling Curator of Mysteries scored EV={ev:.2f} above "
            f"CONTROL pass_threshold ({PASS_THRESHOLD}) even though "
            f"it would tap the last U source while Counterspell is "
            f"held. _score_cycling needs the same _holdback_penalty "
            f"as _score_spell."
        )


class TestHoldbackCoversEquipA3:
    """Activating equip {1} on Cranial Plating must score below
    pass_threshold when the activation taps the last response source
    needed for a held Counterspell."""

    def test_equip_blocked_when_taps_last_response_source(self, card_db):
        game = GameState(rng=random.Random(0))

        # Exactly 2 Islands → can pay equip {1} (any 1 mana) AND can
        # cast Counterspell {UU}, but not both. Equipping uses 1 →
        # one Island left → can't pay Counterspell's UU.
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")

        # Plating (unattached) + a creature target on board
        plating = _add(game, card_db, "Cranial Plating", controller=0,
                       zone="battlefield")
        plating.instance_tags.add("equipment_unattached")
        _add(game, card_db, "Ornithopter", controller=0, zone="battlefield")

        # Held Counterspell
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        # Aggressive opponent
        _add(game, card_db, "Memnite", controller=1, zone="battlefield")
        _add(game, card_db, "Ornithopter", controller=1, zone="battlefield")
        for _ in range(3):
            _add(game, card_db, "Memnite", controller=1, zone="hand")

        # MIDRANGE has holdback_applies=True; pass_threshold = -3.0
        game.players[0].deck_name = "Dimir Midrange"
        game.players[1].deck_name = "Affinity"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 4

        player = EVPlayer(player_idx=0, deck_name="Dimir Midrange",
                          rng=random.Random(0))
        me = game.players[0]

        equip_play = player._consider_equip(game, me)
        assert equip_play is not None, (
            "_consider_equip returned None — test setup error: "
            "Plating + Ornithopter on board, equip cost 1, available "
            "mana = 2."
        )

        # Profile pass_threshold for MIDRANGE = -3.0; the equip score
        # must drop below it once the holdback penalty is applied
        # (the activation taps the last U source for the Counterspell).
        PASS_THRESHOLD = player.profile.pass_threshold
        assert equip_play.ev < PASS_THRESHOLD, (
            f"Equip Cranial Plating scored EV={equip_play.ev:.2f} "
            f"above pass_threshold ({PASS_THRESHOLD}) even though it "
            f"taps the last U source while Counterspell is held. "
            f"_consider_equip needs the _holdback_penalty hook."
        )
