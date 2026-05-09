"""AI must suspend Living End on T2 when no cascade enabler is in hand.

Living End loop break (2026-05-09): with the engine extended to
enumerate suspend in get_legal_plays, EVPlayer must score the
suspend Play and prefer it over passing when the cascade route
is unreachable this turn.

Mechanic-level rule:
  Suspend X is a positive-EV play when:
    1. _payoff_reachable_this_turn(X, snap, game, idx) returns False
       (no cascade enabler / tutor / storm finisher in hand) — i.e.
       there is no faster route to the same payoff this turn.
    2. The suspend resolution turn (current_turn + N counters + 1)
       is within the opponent's projected turns_to_lethal — i.e.
       there is time for the suspend to matter.

When both gates pass, suspend EV exceeds pass_threshold and the AI
returns ("suspend", X, []).

Class-size: every Modern suspend deck uses the same gate (Living
End, Ancestral Vision, Crashing Footfalls, Restore Balance, Wheel
of Fate, Lotus Bloom).  The fix is keyword-driven; no card names.

These tests are RED until the suspend-scoring path lands in
ai/ev_player.py.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_player import EVPlayer
from engine.card_database import CardDatabase
from engine.cards import CardInstance, Keyword
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
    game.players[controller].battlefield.append(card) \
        if zone == "battlefield" else \
        getattr(game.players[controller], zone).append(card)
    return card


def _setup_living_end_t2(game):
    game.players[0].deck_name = "Living End"
    game.players[1].deck_name = "Affinity"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 2
    game.players[0].lands_played_this_turn = 1


class TestSuspendChosenWithoutCascadeEnabler:
    """Living End in hand, no cascade enabler in hand, 4 black mana
    available → AI must eventually suspend during the main phase.
    Pre-fix: legal_plays does not return Living End so suspend is
    never a candidate and the AI lets Living End sit in hand all
    game.  Post-fix: AI's main-phase action sequence includes a
    suspend of Living End."""

    def test_ai_suspends_living_end_when_only_play_available(
            self, card_db):
        """Living End is the ONLY card in hand other than mana
        sources.  Without suspend enumeration, AI's only option is
        pass.  Post-fix: AI suspends on the first call."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield")
        for _ in range(2):
            _add(game, card_db, "Memnite", controller=1,
                 zone="battlefield")
        living_end = _add(game, card_db, "Living End",
                          controller=0, zone="hand")
        _setup_living_end_t2(game)

        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))
        decision = player.decide_main_phase(game)
        assert decision is not None, (
            "AI passed instead of acting.  With Living End in hand "
            "and 4 black mana available, suspend is the only "
            "positive-EV action and must be chosen over passing.")
        assert decision[0] == "suspend", (
            f"AI chose {decision[0]!r} instead of suspend.  Living "
            f"End is the only non-land card in hand; the only legal "
            f"non-pass play is suspend.  Engine must enumerate it "
            f"and AI must score it positively.")
        assert decision[1] is living_end, (
            f"AI suspended {decision[1].name!r} but the only "
            f"suspend-payable card in hand is Living End.")

    def test_ai_eventually_suspends_living_end_in_main_phase(
            self, card_db):
        """End-to-end: Living End + cyclers in hand, no cascade
        enabler.  Cycling first is fine; the AI's main-phase action
        sequence must include a suspend of Living End by the time
        priority resolves to a pass."""
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield")
        for _ in range(2):
            _add(game, card_db, "Memnite", controller=1,
                 zone="battlefield")
        living_end = _add(game, card_db, "Living End",
                          controller=0, zone="hand")
        _add(game, card_db, "Street Wraith", controller=0, zone="hand")
        _add(game, card_db, "Architects of Will", controller=0,
             zone="hand")
        _setup_living_end_t2(game)

        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))

        # Drive the main phase: call decide_main_phase, execute the
        # action, repeat until pass.  Mirrors engine/game_runner.py's
        # main-phase loop minus side-effect plumbing we don't need.
        actions = []
        for _ in range(8):  # safety cap
            decision = player.decide_main_phase(game)
            if decision is None:
                break
            action, card, _targets = decision
            actions.append((action, card.name))
            if action == "cycle":
                game.activate_cycling(0, card)
            elif action == "suspend":
                game.suspend_card(0, card)
            elif action == "play_land":
                game.play_land(0, card)
            else:
                break  # unexpected action; stop to surface in assertion

        suspended_living_end = any(
            a == "suspend" and n == "Living End"
            for a, n in actions
        )
        assert suspended_living_end, (
            f"AI's main-phase action sequence was {actions!r} — no "
            f"suspend of Living End.  With Living End in hand, "
            f"4 black mana, and no cascade enabler, the AI must "
            f"suspend Living End at some point in the main phase.  "
            f"It can cycle first (positive EV), but it must not "
            f"end the turn with Living End still in hand."
        )


class TestSuspendDeferredWhenCascadeEnablerInHand:
    """Living End in hand AND Shardless Agent in hand → AI must
    prefer cascade route, not suspend.  Cascade resolves T3 (faster
    than suspend's T5), so the suspend gate must defer."""

    def test_ai_does_not_suspend_when_cascade_enabler_in_hand(
            self, card_db):
        game = GameState(rng=random.Random(0))
        for _ in range(4):
            _add(game, card_db, "Swamp", controller=0,
                 zone="battlefield")
        _add(game, card_db, "Living End", controller=0, zone="hand")
        # Cascade enabler present — _payoff_reachable_this_turn
        # returns True for cyclers in hand, suspend gate must not
        # fire because the faster route exists.
        _add(game, card_db, "Shardless Agent", controller=0,
             zone="hand")
        _add(game, card_db, "Street Wraith", controller=0, zone="hand")
        _setup_living_end_t2(game)

        player = EVPlayer(player_idx=0, deck_name="Living End",
                          rng=random.Random(0))
        decision = player.decide_main_phase(game)
        # Either cast Shardless Agent (cascade) or cycle Street
        # Wraith (graveyard fill).  NOT suspend.
        if decision is not None:
            assert decision[0] != "suspend", (
                f"AI suspended Living End even though Shardless "
                f"Agent is in hand.  The cascade route resolves on "
                f"T3 (faster than suspend's T5) and is the higher-"
                f"EV line.  The suspend gate must check "
                f"_payoff_reachable_this_turn first and only fire "
                f"when no cascade enabler is available."
            )
