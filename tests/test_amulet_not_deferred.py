"""Phase 9b — Amulet of Vigor cast on T1 must not be deferred.

Phase 6 follow-up from
docs/experiments/2026-04-20_phase6_matrix_validation.md.

Amulet of Vigor's oracle: "Whenever a permanent you control enters
tapped, untap it."  This is a recurring trigger that fires every
time we play a tapped permanent (especially Amulet Titan's bounce
lands like Simic Growth Chamber).  Casting Amulet T1 starts the
"untap-tapped-permanents" engine on T2 and onward — every turn we
play an Amulet → +1 mana via untap.

Phase 1's deferral baseline + signal framework misses Amulet
because:
  - It's not a creature (no `creature_body_with_power` signal).
  - It has no `{T}: Add` line (no `mana_source` signal).
  - Its trigger is on OTHERS entering tapped (no self-ETB signal).
  - No equipment / threshold-enabler / removal / draw / haste etc.

Net: Amulet's signal list is empty → AI defers.  In a real game
that means T1 passes with Amulet in hand, then T2 plays its bounce
land tapped (no Amulet to untap it), losing a turn of ramp value.

Fix: add a `recurring_engine_trigger` signal that fires for any
permanent whose oracle has a "whenever ... enters" / "whenever you
cast" / "at the beginning of ..." trigger producing a beneficial
effect.  Casting NOW starts the engine NOW; casting next turn
delays the engine by one turn — meaningful same-turn signal.

Regression anchor: Cranial Plating with no carrier should still
defer (its `{B}{B}: Attach` is an activated ability, not a recurring
trigger; Phase 1 / Phase 2 specs require this defers).
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
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


def _setup_amulet_t1_main(game):
    game.players[0].deck_name = "Amulet Titan"
    game.players[1].deck_name = "Dimir Midrange"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 1
    game.players[0].lands_played_this_turn = 1


class TestAmuletNotDeferredT1:
    """Amulet of Vigor's recurring "untap on enter tapped" engine
    is positive same-turn value: every future bounce-land cycle
    untaps + replays for net mana."""

    def test_amulet_signals_recurring_engine(self, card_db):
        """`_enumerate_this_turn_signals` must list Amulet of Vigor
        with at least one signal — empty list ⇒ deferred ⇒ T1 pass."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Forest", controller=0)
        amulet = _add_to_hand(game, card_db, "Amulet of Vigor",
                              controller=0)
        _setup_amulet_t1_main(game)

        snap = snapshot_from_game(game, 0)
        signals = _enumerate_this_turn_signals(
            amulet, snap, game, 0, archetype="ramp")
        assert signals, (
            f"Amulet of Vigor returned no this-turn signal — AI will "
            f"defer the cast.  Recurring-engine triggers ('whenever "
            f"a permanent enters tapped, untap it') start the engine "
            f"sooner the earlier we cast.  Add a recurring-engine "
            f"signal so AI casts Amulet on T1."
        )

    def test_amulet_chosen_on_t1_with_mana(self, card_db):
        """End-to-end: with 1 land in play and Amulet (CMC 1) in hand,
        AI must cast Amulet T1.  Currently passes."""
        game = GameState(rng=random.Random(0))
        _add_to_battlefield(game, card_db, "Forest", controller=0)
        amulet = _add_to_hand(game, card_db, "Amulet of Vigor",
                              controller=0)
        # Add a bounce land in hand so the future-value of Amulet is
        # legible from the snapshot (we will play it untapped via
        # Amulet on a later turn).
        _add_to_hand(game, card_db, "Simic Growth Chamber", controller=0)
        _setup_amulet_t1_main(game)

        player = EVPlayer(player_idx=0, deck_name="Amulet Titan",
                          rng=random.Random(0))
        decision = player.decide_main_phase(game)
        cast_amulet = (
            decision is not None
            and decision[0] == "cast_spell"
            and decision[1].name == "Amulet of Vigor"
        )
        assert cast_amulet, (
            f"AI chose {decision!r} — passed instead of casting Amulet "
            f"on T1.  Amulet's recurring untap-on-enter trigger is "
            f"the engine; deferring it loses a turn of ramp value."
        )


class TestPlatingStillDefers:
    """Regression anchor — Phase 1's Plating-no-carrier deferral
    must NOT be re-broken by the recurring-engine signal.  Plating's
    activated ability `{B}{B}: Attach` is NOT a recurring trigger.
    Without a carrier on board, Plating still has no this-turn
    value and must defer."""

    def test_plating_no_carrier_still_defers(self, card_db):
        game = GameState(rng=random.Random(0))
        for _ in range(3):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        plating = _add_to_hand(game, card_db, "Cranial Plating",
                                controller=0)
        # No creature on battlefield.
        game.players[0].deck_name = "Affinity"
        game.players[1].deck_name = "Dimir Midrange"
        game.current_phase = Phase.MAIN1
        game.active_player = 0
        game.priority_player = 0
        game.turn_number = 3
        game.players[0].lands_played_this_turn = 1

        snap = snapshot_from_game(game, 0)
        signals = _enumerate_this_turn_signals(
            plating, snap, game, 0, archetype="combo")
        assert not signals, (
            f"Plating with no carrier emitted signals {signals} — "
            f"Phase 1's deferral spec requires no signal here.  The "
            f"recurring-engine signal must NOT fire on activated "
            f"abilities (`:`-style), only on triggered abilities "
            f"(`whenever ...`, `at the beginning of ...`)."
        )
