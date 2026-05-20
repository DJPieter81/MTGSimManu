"""Flashback-grant cards must not emit signal #17 when graveyard
flashback has already been granted this turn.

Mechanic (rules-anchored): a card whose oracle reads "Each instant
and sorcery card in your graveyard gains flashback until end of
turn" is a *static, until-end-of-turn* effect.  The first cast
already grants flashback to every eligible card in the graveyard
for the rest of the turn.  Casting a second copy in the same turn
re-grants the same flashback to the same cards — the redundant
cast contributes zero incremental value: graveyard cards already
have flashback, the second copy adds nothing the first did not
already provide.

Bug (seed 50000 Storm vs Eldrazi Tron T7): Storm cast Past in
Flames three times in one turn, burning two extra copies and 8
mana for no added storm-count progress, no extra flashback
granted, no extra fuel exposed.  Documented in
`docs/diagnostics/2026-04-28_storm_wasted_enablers.md` lines
49-62.

Generic mechanic (the test names the rule, not the card):
- The signal `flashback_combo_with_gy_fuel` (`ai/ev_evaluator.py`
  signal #17) fires for any spell tagged `'flashback' AND 'combo'`
  whose graveyard contains instant/sorcery fuel.
- A turn-scoped fact `flashback_granted_this_turn` records that the
  graveyard-wide flashback grant has already happened this turn.
- When the fact is set, signal #17 must NOT fire for any
  flashback-grant card — the second cast contributes nothing
  beyond what the first already provided.

Class: this rule covers any printing whose oracle grants flashback
to ALL graveyard instants/sorceries until end of turn (the static-
effect family) — not Snapcaster Mage's targeted single grant
(different mechanic — picks one card, the second Snapcaster picks
a different card).

Generalisation: any future flashback-grant card with the same
"each instant and sorcery card in your graveyard gains flashback"
oracle pattern is covered automatically — detection is
oracle-text-driven, no card names in source.
"""
from __future__ import annotations

import random

import pytest

from ai.ev_evaluator import _enumerate_this_turn_signals, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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
    getattr(game.players[controller],
            'library' if zone == 'library' else zone).append(card)
    return card


def _build_storm_pif_game(card_db, gy_fuel_count=3, mountains=8,
                          flashback_already_granted=False,
                          cantrip_in_hand=True):
    """Storm-side T7 main, mountains untapped, a *second* Past in
    Flames in hand, the *first* PiF resolved earlier this turn (so
    `flashback_granted_this_turn` is set on the player state and a
    PiF copy sits in graveyard with `has_flashback` already enabled
    on the GY rituals/cantrips).

    A cantrip ("Manamorphose") is included in hand by default so
    `_payoff_reachable_this_turn` returns True via branch (d).  This
    isolates the new "flashback-already-granted" gate as the load-
    bearing predicate — without it the existing payoff-reachable
    branch would already suppress the signal when storm_count > 0,
    masking the real test case."""
    game = GameState(rng=random.Random(0))
    for _ in range(mountains):
        _add(game, card_db, "Mountain", controller=0, zone="battlefield")
    pif_in_hand = _add(game, card_db, "Past in Flames", controller=0,
                       zone="hand")
    if cantrip_in_hand:
        # Cantrip-in-hand → `_payoff_reachable_this_turn` returns True
        # via branch (d) `_is_real_dig`. Without this, the existing
        # storm_count > 0 gate would already suppress signal #17 and
        # mask the test of the new flashback-already-granted predicate.
        _add(game, card_db, "Manamorphose", controller=0, zone="hand")
    fuel_cards = ["Pyretic Ritual", "Desperate Ritual",
                  "Reckless Impulse", "Wrenn's Resolve",
                  "Pyretic Ritual"]
    for i in range(gy_fuel_count):
        c = _add(game, card_db, fuel_cards[i % len(fuel_cards)],
                 controller=0, zone="graveyard")
        if flashback_already_granted:
            c.has_flashback = True
    if flashback_already_granted:
        # First PiF resolved this turn — leaves PiF copy in GY.
        _add(game, card_db, "Past in Flames", controller=0,
             zone="graveyard")
        # Engine-recorded turn-scoped fact: the graveyard-wide
        # flashback grant has already happened this turn.
        game.players[0].flashback_granted_this_turn = True
    _add(game, card_db, "Guide of Souls", controller=1,
         zone="battlefield")
    game.players[0].deck_name = "Ruby Storm"
    game.players[1].deck_name = "Eldrazi Tron"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 7
    game.players[0].lands_played_this_turn = 1
    # First PiF already cast — storm count reflects that.
    game.players[0].spells_cast_this_turn = 1 if flashback_already_granted else 0
    game._global_storm_count = (
        1 if flashback_already_granted else 0)
    game.players[0].life = 14
    game.players[1].life = 8
    return game, pif_in_hand


def _signals(game, card, archetype="storm"):
    snap = snapshot_from_game(game, 0)
    return _enumerate_this_turn_signals(card, snap, game, 0, archetype)


class TestPifSuppressedAfterFlashbackGranted:
    """Anchor: the *first* PiF cast still emits the signal.  The
    *second* (and third, fourth, ...) must not — flashback was
    already granted to the same graveyard cards by the first
    resolution, so subsequent casts contribute zero incremental
    value."""

    def test_first_pif_still_emits_signal(self, card_db):
        """Regression anchor: the *first* PiF cast in a turn keeps
        emitting signal #17 — this is the existing behaviour from
        `test_storm_pif_flashback_signal.py`.  The fix must not
        regress it."""
        game, pif = _build_storm_pif_game(
            card_db, gy_fuel_count=3, flashback_already_granted=False)
        sig = _signals(game, pif, archetype="storm")
        assert 'flashback_combo_with_gy_fuel' in sig, (
            f"First PiF cast should still emit "
            f"`flashback_combo_with_gy_fuel`. Got signals: {sig}.  "
            f"This anchor protects the existing PR-X behaviour."
        )

    def test_second_pif_does_not_emit_signal_when_already_granted(
            self, card_db):
        """Failing pre-fix: signal #17 fires regardless of whether
        flashback was already granted this turn.  Post-fix: the
        engine-recorded fact `flashback_granted_this_turn` (set on
        first PiF resolution) suppresses signal #17 on subsequent
        flashback-grant casts in the same turn."""
        game, pif = _build_storm_pif_game(
            card_db, gy_fuel_count=3, flashback_already_granted=True)
        sig = _signals(game, pif, archetype="storm")
        assert 'flashback_combo_with_gy_fuel' not in sig, (
            f"Past in Flames emitted `flashback_combo_with_gy_fuel` "
            f"after flashback was already granted earlier this "
            f"turn.  The granted flashback is until-end-of-turn — "
            f"a second cast re-grants it to the same cards (zero "
            f"incremental value).  Suppress signal #17 when "
            f"`me.flashback_granted_this_turn` is True.  Got "
            f"signals: {sig}"
        )

    def test_second_pif_routes_to_deferral_when_flashback_already_granted(
            self, card_db):
        """End-to-end: after flashback was already granted this
        turn, the AI must treat the second PiF cast as deferrable
        (no this-turn signal).  `compute_play_ev` returns a
        deferral verdict (`info['deferral'] == True`) and the
        ev_player.py deferral filter routes the cast to pass.

        Pre-fix: signal #17 fires regardless, deferral=False,
        exposure-cost is bypassed, PiF gets cast even when redundant.
        Post-fix: signal #17 suppressed by the engine flag,
        compute_play_ev returns deferral=True, the redundant cast
        is filtered out at decide_main_phase."""
        from ai.ev_evaluator import compute_play_ev
        game, pif = _build_storm_pif_game(
            card_db, gy_fuel_count=3, flashback_already_granted=True)
        snap = snapshot_from_game(game, 0)
        _, info = compute_play_ev(pif, snap, "storm", game, 0,
                                  detailed=True)
        assert info['deferral'] is True, (
            f"compute_play_ev returned deferral=False for a "
            f"redundant Past in Flames cast (flashback was already "
            f"granted this turn).  Expected deferral=True so the "
            f"ev_player.py deferral filter routes the cast to pass. "
            f"Signals: {info.get('this_turn_signals', '?')}"
        )
        assert 'flashback_combo_with_gy_fuel' not in info.get(
                'this_turn_signals', []), (
            f"Signal `flashback_combo_with_gy_fuel` fired for a "
            f"redundant cast.  The engine flag "
            f"`flashback_granted_this_turn` must suppress it. Got "
            f"signals: {info.get('this_turn_signals', '?')}"
        )
