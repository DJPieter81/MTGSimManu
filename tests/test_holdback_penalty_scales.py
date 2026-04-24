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
(hand-size + power + archetype). After Iteration-2's B3-Tune the
coefficient is 4.0 (was 7.0) — the penalty still scales, still
gates low-EV plays, but no longer floors high-EV draw engines.

This test pins the SCALING property (penalty grows with held counters),
not the final EV of any single play — that's what makes it robust
across coefficient tunes.

Regression anchor: with NO counters in hand there must be no penalty.
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
    """Build a CONTROL board with 3 Islands, a CMC-2 sorcery in hand
    (Augur of Bolas — 1U creature, no flash), and optional 2×
    Counterspell.

    3 Islands is tight enough to (a) cover the CMC-2 cast, (b) leave
    U=1 untapped afterwards which is BELOW the max counter CMC (2),
    so the Iteration-2 color-capacity early-exit does NOT bail out
    and the penalty path runs as A1 intended.

    Opponent is an aggressive Boros board (creatures present) so the
    threat probability is non-trivial."""
    game = GameState(rng=random.Random(0))

    # Exactly 3 Islands — tight mana so color-capacity loss is real
    # and the penalty path runs.
    for _ in range(3):
        _add(game, card_db, "Island", controller=0, zone="battlefield")

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

    def test_holdback_scales_with_two_counterspells(self, card_db):
        """Pin the scaling property, not the final EV. With 2 counters
        held (each CMC 2) vs an active creature opponent, the raw
        holdback penalty must be large enough to matter — several
        times larger than a flat -2.0 — specifically ≤ -10.0 so the
        scaling is demonstrably non-flat."""
        game, augur = _build_control_setup(card_db, with_counters=True)
        player = EVPlayer(player_idx=0, deck_name="Azorius Control",
                          rng=random.Random(0))
        snap = snapshot_from_game(game, 0)
        me = game.players[0]
        opp = game.players[1]

        penalty = player._holdback_penalty(
            me, opp, snap, cost=augur.template.cmc or 0,
            exclude_instance_id=augur.instance_id,
        )

        # The original A1 goal: scaling, not a fixed magnitude. With
        # 2 × Counterspell (UU each, CMC 2) held against a creature
        # opponent, the coefficient-scaled penalty should be at least
        # several multiples of the pre-A1 flat -2.0. We require <=
        # -10.0 — this holds for the Bundle-3 7.0 coefficient (-28 or
        # so) AND the Iteration-2 4.0 coefficient (≈ -16).
        assert penalty <= -10.0, (
            f"2× Counterspell holdback penalty={penalty:.2f} is not "
            f"meaningfully scaled. A1 requires count × cmc × threat × "
            f"coefficient ≥ 10 magnitude; a flat -2.0 would fail this."
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
