"""Storm regression — Ruby Medallion T2 cast must not be deferred.

Surfaced by the 2026-04-26 Storm pro-player audit (F5.1):
docs/history/audits/2026-04-26_storm_pro_audit.md.

`_enumerate_this_turn_signals` returns no signal for Ruby Medallion
on T2 because:
  - It's not a creature (no `creature_body_with_power` signal).
  - It has no `{T}: Add` (no `mana_source` signal).
  - It has a static "spells you cast cost {1} less" — not a `whenever`
    trigger, so the recurring-engine regex misses it.
  - The `combo_continuation` signal fires only when storm_count > 0
    OR a reducer is already on the battlefield — neither holds when
    deploying the FIRST reducer.

Net: no signal → AI flags `deferral=True` → ev_player.py:417-422
filters Medallion out of candidates → AI casts +0.1 Manamorphose
instead of +11.0 Medallion (trace: replays/audit_storm_vs_dimir_
midrange_s60510.txt:64-72).

Fix: add a signal for cost-reducer permanents whenever the hand
contains a non-land spell that the reducer would benefit. Generic
by construction — applies to Goblin Electromancer (Izzet Prowess),
Sapphire Medallion, Baral, and any future cost-discount engine.

Regression anchor: a cost-reducer with EMPTY hand has nothing to
reduce — must still defer, since deploying with no future spells
to discount provides zero this-turn value.
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


def _setup_storm_t2_main(game):
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Dimir Midrange"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 2
    game.players[0].lands_played_this_turn = 1


class TestMedallionNotDeferredT2:
    """Ruby Medallion deployed on T2 with rituals in hand provides
    immediate this-turn value: every subsequent ritual costs {1}
    less. Deferring loses a turn of cost-reduction headroom."""

    def test_medallion_signals_when_hand_has_spells(self, card_db):
        """`_enumerate_this_turn_signals` must list at least one
        signal for Ruby Medallion when hand contains a non-land
        spell that the reducer would discount."""
        game = GameState(rng=random.Random(0))
        for _ in range(2):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        medallion = _add_to_hand(game, card_db, "Ruby Medallion",
                                  controller=0)
        # Hand also has a ritual to discount.
        _add_to_hand(game, card_db, "Pyretic Ritual", controller=0)
        _setup_storm_t2_main(game)

        snap = snapshot_from_game(game, 0)
        signals = _enumerate_this_turn_signals(
            medallion, snap, game, 0, archetype="storm")
        assert signals, (
            "Ruby Medallion returned no this-turn signal — AI will "
            "defer the cast.  Cost-reducer permanents provide "
            "immediate value when the hand contains non-land spells "
            "they would discount.  Add a cost-reducer signal that "
            "fires when cost_reducer-tagged permanents are deployed "
            "with same-color spells in hand."
        )

    def test_medallion_chosen_on_t2_with_ritual_in_hand(self, card_db):
        """End-to-end: with 2 lands in play and Medallion + Pyretic
        Ritual in hand on T2, AI must cast Medallion (priority engine
        deployment), not pass."""
        game = GameState(rng=random.Random(0))
        for _ in range(2):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        _add_to_hand(game, card_db, "Ruby Medallion", controller=0)
        # Several rituals + cantrips in hand for the Medallion to
        # discount on later turns.
        _add_to_hand(game, card_db, "Pyretic Ritual", controller=0)
        _add_to_hand(game, card_db, "Desperate Ritual", controller=0)
        _add_to_hand(game, card_db, "Reckless Impulse", controller=0)
        _setup_storm_t2_main(game)

        player = EVPlayer(player_idx=0, deck_name="Ruby Storm",
                          rng=random.Random(0))
        decision = player.decide_main_phase(game)
        cast_medallion = (
            decision is not None
            and decision[0] == "cast_spell"
            and decision[1].name == "Ruby Medallion"
        )
        assert cast_medallion, (
            f"AI chose {decision!r} — passed instead of casting Ruby "
            f"Medallion on T2.  Cost-reducer engines start saving "
            f"mana on the turn they resolve; deferring loses a turn "
            f"of mana headroom for future ritual chains."
        )


class TestMedallionEmptyHandStillDefers:
    """Regression anchor: a cost-reducer with no spells in hand to
    discount has nothing to do this turn beyond entering the
    battlefield.  Must still defer to preserve the no_signal
    framework's spec — the cost-reducer signal must check for at
    least one castable target."""

    def test_medallion_no_spells_in_hand_defers(self, card_db):
        game = GameState(rng=random.Random(0))
        for _ in range(2):
            _add_to_battlefield(game, card_db, "Mountain", controller=0)
        medallion = _add_to_hand(game, card_db, "Ruby Medallion",
                                  controller=0)
        # Hand is just Medallion — nothing to discount.
        _setup_storm_t2_main(game)

        snap = snapshot_from_game(game, 0)
        signals = _enumerate_this_turn_signals(
            medallion, snap, game, 0, archetype="storm")
        # Medallion alone in hand: no spell to discount, no value
        # from immediate deployment beyond a stranded permanent.
        # Must NOT emit cost_reducer_active.
        assert 'cost_reducer_active' not in signals, (
            f"Medallion with empty hand emitted cost_reducer_active "
            f"signal: {signals}.  The signal must require at least "
            f"one non-land spell in hand for the reducer to discount."
        )
