"""Bug A — 0-cost creature cast with no this-turn enabler.

Design: docs/design/ev_correctness_overhaul.md §2.A

When an Affinity pilot has Ornithopter in hand but none of the
this-turn enablers (no Mox Opal for metalcraft, no Cranial Plating
in hand to equip, no sacrifice outlet in play), casting the
0-cost creature delivers zero same-turn value — the body is summon-
sick, doesn't block, and exposes a card to removal.

Under the EV-baseline fix, such casts score at or below
pass_threshold because the state-after-cast is reachable next turn
at equivalent cost, so the cast's marginal value is the exposure
cost (≤ 0).  The AI should hold the Ornithopter.

Observed in replays/boros_rarakkyo_vs_affinity_s63000_bo3.txt:54
(Affinity T1 cast Ornithopter with no metalcraft, no Plating,
no sacrifice outlet — EV ≈ 0.0 pre-fix, AI casts).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import compute_play_ev, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


@pytest.fixture(scope="module")
def card_db():
    return CardDatabase()


def _add_to_battlefield(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="battlefield",
    )
    card._game_state = game
    card.enter_battlefield()
    card.summoning_sick = False
    game.players[controller].battlefield.append(card)
    return card


def _add_to_hand(game, card_db, name, controller):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone="hand",
    )
    card._game_state = game
    game.players[controller].hand.append(card)
    return card


def _setup_affinity_t1_main(game):
    """Put the game in Affinity's T1 main phase with the land drop
    already used."""
    game.players[0].deck_name = "Affinity"
    game.players[1].deck_name = "Dimir Midrange"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 1
    game.players[0].lands_played_this_turn = 1


class TestZeroCostCreatureNoEnablerHeld:
    """Affinity: Ornithopter should not be cast on T1 when nothing
    on the battlefield or in hand turns it into same-turn value."""

    def test_ornithopter_not_cast_when_no_enabler_available(
            self, card_db):
        """T1 after land drop: hand has Ornithopter and a non-synergy
        artifact that shares no this-turn interaction.  No metalcraft
        (need 3 artifacts — we have 1 land + 0 artifacts), no Plating
        to equip, no sacrifice outlet on board, no affinity payoff in
        hand.  AI must defer the Ornithopter cast."""
        game = GameState(rng=random.Random(0))
        # Artifact land counts as 1 artifact — still short of metalcraft's
        # 3-artifact threshold and provides no same-turn signal.
        _add_to_battlefield(game, card_db, "Darksteel Citadel", controller=0)
        _add_to_hand(game, card_db, "Ornithopter", controller=0)
        # Welding Jar is a cheap artifact with no this-turn interaction
        # for Ornithopter — no equipment payoff, no sacrifice-outlet.
        _add_to_hand(game, card_db, "Welding Jar", controller=0)
        _setup_affinity_t1_main(game)

        player = EVPlayer(player_idx=0, deck_name="Affinity",
                          rng=random.Random(0))
        decision = player.decide_main_phase(game)

        cast_ornithopter = (
            decision is not None
            and decision[0] == "cast_spell"
            and decision[1].name == "Ornithopter"
        )
        assert not cast_ornithopter, (
            f"AI chose {decision!r} — casting Ornithopter on T1 with "
            f"no same-turn enabler (no metalcraft, no Plating, no sac "
            f"outlet, no affinity payoff).  Under the EV-baseline fix, "
            f"a cast with no this-turn signal should defer because the "
            f"post-cast state is reachable next turn at equivalent cost."
        )

    def test_ornithopter_ev_at_or_below_zero_without_enabler(
            self, card_db):
        """Tighter spec: the cast's EV itself must be ≤ 0 under the
        fix — there is no this-turn value to earn, so the exposure-
        cost baseline dominates.  Current implementation returns
        EV ≈ -0.04 (just above the pass threshold), which still gets
        cast.  Post-fix EV should be strictly below the highest-
        archetype pass_threshold of -3.0 (MIDRANGE), ensuring no
        archetype keeps this cast when no signal fires."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Darksteel Citadel", controller=0)
        ornithopter = _add_to_hand(game, card_db, "Ornithopter",
                                    controller=0)
        _add_to_hand(game, card_db, "Welding Jar", controller=0)
        _setup_affinity_t1_main(game)

        snap = snapshot_from_game(game, 0)
        ev = compute_play_ev(ornithopter, snap, archetype="combo",
                             game=game, player_idx=0)

        # COMBO pass_threshold is -5.0.  MIDRANGE is -3.0.  A deferrable
        # no-signal cast must score strictly below the most permissive
        # threshold so no archetype casts it.  Current EV ≈ -0.04 fails.
        PASS_THRESHOLD_UPPER_BOUND = -3.0
        assert ev < PASS_THRESHOLD_UPPER_BOUND, (
            f"Ornithopter with no same-turn signal scored EV={ev:.3f}, "
            f"which exceeds the most permissive archetype pass_threshold "
            f"({PASS_THRESHOLD_UPPER_BOUND}).  Under the EV-baseline "
            f"fix, a deferrable cast with no this-turn signal should "
            f"score below every archetype's pass_threshold so the cast "
            f"is deferred regardless of deck."
        )
