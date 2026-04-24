"""Iteration-2 B3-Tune — calibrate Bundle 3's holdback coefficient.

Background (defender-collapse investigation, post-Affinity session).
After the 7 Affinity fixes landed on main the N=50 matrix showed
defenders got markedly worse: Jeskai -5 pp, Dimir -6 pp, Azorius
Control (WST) -8 pp. A read-only regression pass isolated
`HELD_RESPONSE_VALUE_PER_CMC = 7.0` at `ai/ev_player.py:869` as too
aggressive: a single Counterspell (count=1, cmc=2, threat_prob=1.0)
multiplied by 7.0 yields a -14 penalty that floors nearly every
main-phase play for a control deck — the defender just passes instead
of deploying answers.

Fix (surgical, no new subsystems):
1. Lower the coefficient 7.0 → 4.0. Math: 1×2×1.0×4.0 = -8 correctly
   gates a +5 main-phase play (below CONTROL pass_threshold -5.0) but
   leaves a +10 draw engine castable.
2. Early-exit in `_holdback_penalty`: if the candidate play doesn't
   lose color capacity (remaining colored sources after cast still
   cover every held counter's color cost via max counter CMC), return
   0.0. Prevents penalising colorless plays that free up U/B sources.
3. Revert A4's threshold: `opp_hand_size >= 3` → `>= 4`. 3-card post-
   discard hands are mostly lands; the stricter threshold matches
   pre-Bundle-3 behaviour and is more selective.

Each test below maps to one item above. Written FIRST per Option C.
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


class TestBundle3CoefficientCalibration:
    """Coefficient lowered 7.0 → 4.0; must still gate the 2-counter
    case but must not floor the 1-counter main-phase case."""

    def test_two_counterspells_still_gate_tapout_play(self, card_db):
        """Regression anchor for A1 — with 2× Counterspell held, an
        aggressive opponent, and TIGHT mana (no spare U after cast),
        the base holdback penalty must still be substantial (≤ -15)
        so that a low-EV main-phase play gets gated.

        Setup: 3 Islands — U=3, casting CMC-2 leaves 1 U which is <
        max counter cmc (2); color-capacity early-exit does NOT bail
        out. Math: 2 counters × CMC 2 × threat_prob 1.0 × coeff 4.0 =
        -16 base penalty (plus A5 amplifier when color is killed).

        We assert the penalty magnitude directly (not the net EV of
        any specific card) — this pins the scaling property
        independently of how the evaluator weighs the candidate. Pre-
        fix with coeff 7.0 this was ≈ -28; post-fix with 4.0 it's
        ≈ -16; either way ≤ -15."""
        game = GameState(rng=random.Random(0))
        # Exactly 3 Islands — U=3, casting CMC-2 leaves 1 U remaining
        # which is < max counter cmc (2), so color-capacity early-exit
        # does NOT fire. The penalty path runs as intended.
        for _ in range(3):
            _add(game, card_db, "Island", controller=0, zone="battlefield")

        augur = _add(game, card_db, "Augur of Bolas", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

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
        me, opp = game.players[0], game.players[1]

        penalty = player._holdback_penalty(
            me, opp, snap, cost=augur.template.cmc or 0,
            exclude_instance_id=augur.instance_id,
        )

        # Required magnitude: 2 × 2 × 1.0 × 4.0 = 16. Allow a small
        # float margin (≤ -15.0). Pre-fix (coeff 7.0, ≈ -28) and
        # post-fix (coeff 4.0, ≈ -16) both satisfy this.
        assert penalty <= -15.0, (
            f"With 2× Counterspell held + tight U mana + aggressive "
            f"opp, holdback penalty={penalty:.2f}; expected ≤ -15.0 "
            f"(the A1 scaling 2×2×1.0×4.0 = 16 magnitude must still "
            f"gate low-EV plays post-tune)."
        )

    def test_single_counterspell_penalty_smaller_after_tune(self, card_db):
        """Regression target for the tune — with ONE Counterspell held
        against a live creature opponent (threat_prob ≈ 1.0) and tight
        mana (lost_capacity=1), the raw penalty must shrink from the
        pre-fix -14 to the post-fix -8 so defenders can still deploy.

        Pre-fix math: 1 × 2 × 1.0 × 7.0 = -14 (plus possible A5
        amplifier for -28 in worst case).
        Post-fix math: 1 × 2 × 1.0 × 4.0 = -8.

        Asserting the penalty itself lies in the range (-10, 0) pins
        the coefficient value and fails pre-fix regardless of how the
        _score_spell base EV shifts."""
        game = GameState(rng=random.Random(0))
        # Exactly 3 Islands → cap_now=1, cap_after=_capacity(1)=0,
        # lost_capacity=1 so the penalty path is exercised.
        for _ in range(3):
            _add(game, card_db, "Island", controller=0, zone="battlefield")

        augur = _add(game, card_db, "Augur of Bolas", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        # Aggressive opp so holdback IS relevant (threat_prob → 1.0).
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
        me, opp = game.players[0], game.players[1]

        penalty = player._holdback_penalty(
            me, opp, snap, cost=augur.template.cmc or 0,
            exclude_instance_id=augur.instance_id,
        )
        # 1 × 2 × 1.0 × 4.0 = 8 → -8; pre-fix coeff 7.0 would give -14
        # (or -28 with A5 amplifier). Require penalty to be strictly
        # greater (less negative) than -10 to pin the tune.
        assert penalty > -10.0, (
            f"Single-counter holdback penalty={penalty:.2f}; after the "
            f"coefficient tune 7.0→4.0 it must be no worse than -10 "
            f"(expected ≈ -8 for 1×2×1.0×4.0). Pre-fix value was -14."
        )
        # And it must still be negative — the gate fires at all.
        assert penalty < 0, (
            f"Single-counter holdback penalty={penalty:.2f}; the gate "
            f"must still fire (negative) when a counter is held."
        )


class TestHoldbackColorCapacityEarlyExit:
    """When the candidate play preserves enough colored mana to still
    cast every held counter (max held counter CMC is covered by
    remaining colored sources), `_holdback_penalty` must return 0.0 —
    no generic-mana penalty, no A5 color-kill amplifier."""

    def test_color_capacity_preserved_bails_out(self, card_db):
        """2 Islands + 4 Mountains, 2× Counterspell (UU each) in hand,
        candidate is a CMC-3 colorless artifact (Chromatic Lantern).

        Generic-mana ledger: my_mana=6, cost=3 → cap_now=2 counters,
        cap_after=_capacity(3)=1 counter, lost_capacity=1. Pre-fix
        the penalty fires at 2×2×1.0×7.0 = -28 (plus A5 amplifier).

        Color ledger: after paying the cast with the 3 Mountains, we
        still hold 2 Islands untapped → U=2, which is ≥ max held
        counter CMC (2). The held Counterspells remain castable; the
        fix must bail out BEFORE computing the penalty.
        """
        game = GameState(rng=random.Random(0))
        for _ in range(2):
            _add(game, card_db, "Island", controller=0, zone="battlefield")
        for _ in range(4):
            _add(game, card_db, "Mountain", controller=0, zone="battlefield")

        lantern = _add(game, card_db, "Chromatic Lantern", controller=0,
                       zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

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
        me, opp = game.players[0], game.players[1]

        penalty = player._holdback_penalty(
            me, opp, snap, cost=lantern.template.cmc or 0,
            exclude_instance_id=lantern.instance_id,
        )
        assert penalty == 0.0, (
            f"Chromatic Lantern (CMC-3 colorless) with 2 Islands + 4 "
            f"Mountains + 2× Counterspell got holdback penalty "
            f"{penalty:.2f}; expected 0.0 because U capacity (2) >= "
            f"max held counter CMC (2) after the cast. Early-exit "
            f"must inspect snap.my_mana_by_color, not only generic "
            f"lost_capacity."
        )


class TestA4ThresholdReverted:
    """A4 (Bundle 3) lowered the spell-deck branch to opp_hand_size>=3;
    the defender-collapse investigation shows that's too broad — 3-card
    post-discard hands are mostly lands. Revert to >=4 so the stricter
    pre-Bundle-3 behaviour returns."""

    def test_three_card_spelldeck_opp_no_longer_triggers(self, card_db):
        """Control with 2 Islands + Counterspell + CMC-2 play; opp is a
        spell deck (Ruby Storm) with 0 power on board and EXACTLY a
        3-card hand. After the revert, the holdback gate no longer
        considers this a threat-holding grip, so the penalty must be
        0."""
        game = GameState(rng=random.Random(0))
        _add(game, card_db, "Island", controller=0, zone="battlefield")
        _add(game, card_db, "Island", controller=0, zone="battlefield")

        augur = _add(game, card_db, "Augur of Bolas", controller=0, zone="hand")
        _add(game, card_db, "Counterspell", controller=0, zone="hand")

        for _ in range(3):
            _add(game, card_db, "Mountain", controller=1, zone="battlefield")
        for _ in range(3):
            _add(game, card_db, "Memnite", controller=1, zone="hand")

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

        me, opp = game.players[0], game.players[1]
        penalty = player._holdback_penalty(
            me, opp, snap, cost=augur.template.cmc or 0,
            exclude_instance_id=augur.instance_id,
        )
        assert penalty == 0.0, (
            f"3-card spell-deck opp with 0 power triggered holdback "
            f"penalty {penalty:.2f}; expected 0.0 after A4 revert. "
            f"Threshold must be restored to opp_hand_size >= 4."
        )
