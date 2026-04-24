"""Bundle 3 — A1: holdback penalty must scale with the cost of the
held interaction, not be a flat -2.0.

Diagnosis (Affinity-session consolidated findings, A1):
`ai/ev_player.py:735-752` applies a flat `-2.0` penalty when the
controller would tap out while holding instant-speed interaction
(removal / counterspell). For CONTROL whose `pass_threshold = -5.0`,
a CMC-2 spell whose base EV is +5 lands at +3 — well above the gate
— and is happily cast. The control deck taps out, the opponent
follows up with a real threat, and the held Counterspell rots.

Fix: scale the penalty by `counter_count × counter_cmc × opp_threat_prob`,
where `opp_threat_prob` is derived from BHI / opp aggression
(hand-size + power + archetype). Penalty must be large enough that
holding 2× Counterspell against a creature deck blocks a CMC-2
non-instant main-phase play (EV after penalty must drop BELOW
pass_threshold = -5.0 for a starting EV around +5).

Regression anchor: with NO counters in hand the same play must
still score positive — the penalty fires only when interaction is
held.
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


def _build_control_setup(card_db, *, with_counters: bool):
    """Build a CONTROL board with 4 Islands + 1 Steam Vents (UU+ UU+R),
    a CMC-2 sorcery in hand (Augur of Bolas — 1U creature, no flash),
    and optional 2× Counterspell.

    Opponent is an aggressive Boros board (creatures present) so the
    threat probability is non-trivial."""
    game = GameState(rng=random.Random(0))

    # Lands — 5 untapped sources, all blue (Steam Vents = U/R)
    for _ in range(4):
        _add(game, card_db, "Island", controller=0, zone="battlefield")
    _add(game, card_db, "Steam Vents", controller=0, zone="battlefield")

    # The CMC-2 candidate: Augur of Bolas (1U, sorcery-speed creature)
    augur = _add(game, card_db, "Augur of Bolas", controller=0, zone="hand")

    if with_counters:
        _add(game, card_db, "Counterspell", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

    # Opponent: an aggro creature board to make holdback relevant.
    _add(game, card_db, "Memnite", controller=1, zone="battlefield")
    _add(game, card_db, "Ornithopter", controller=1, zone="battlefield")
    # Opponent hand has cards (so they can deploy more)
    for _ in range(3):
        _add(game, card_db, "Memnite", controller=1, zone="hand")

    game.players[0].deck_name = "Azorius Control"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 4
    return game, augur


class TestHoldbackPenaltyScalesA1:
    """Holdback penalty must be large enough to gate a CMC-2 main-phase
    play when 2× Counterspell are held against an active creature
    opponent. Without counters, no penalty is applied."""

    def test_holdback_blocks_play_with_two_counterspells(self, card_db):
        game, augur = _build_control_setup(card_db, with_counters=True)
        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        ev = player._score_spell(augur, snap, game, me, opp)

        # CONTROL pass_threshold = -5.0
        # Holding 2 × Counterspell (each CMC 2) against a live creature
        # opponent must scale the penalty enough to drop the play below
        # the gate, otherwise the AI taps out and the held counters rot.
        PASS_THRESHOLD = -5.0
        assert ev < PASS_THRESHOLD, (
            f"Augur of Bolas EV={ev:.2f} above CONTROL pass_threshold "
            f"({PASS_THRESHOLD}) while holding 2× Counterspell vs an "
            f"active creature opponent. Flat -2.0 holdback can't gate "
            f"a CMC-2 play; penalty must scale by "
            f"counter_count × counter_cmc × opp_threat_prob."
        )

    def test_no_penalty_without_held_interaction(self, card_db):
        """Regression anchor — without held counterspells/removal the
        same play must remain castable (EV above pass_threshold and
        positive when the projection is favourable)."""
        game, augur = _build_control_setup(card_db, with_counters=False)
        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        ev = player._score_spell(augur, snap, game, me, opp)

        PASS_THRESHOLD = -5.0
        assert ev > PASS_THRESHOLD, (
            f"Augur of Bolas EV={ev:.2f} below pass_threshold even "
            f"though no interaction is held — holdback must NOT fire "
            f"when there's nothing to hold up."
        )
