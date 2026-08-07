"""Blink EV credits clearing a live pending end-of-turn-exile detriment.

Mechanic (CR 400.7): a delayed rider "exile it at the beginning of the
next end step" (Goryo's Vengeance / Sneak Attack / Through the Breach
shape) tracks a specific object.  Blinking the tracked permanent makes
it a new object and sheds the rider — the engine models this via
battlefield-entry object identity (PR #462,
tests/test_delayed_eot_exile_drops_on_zone_change.py).

This file pins the DECISION layer (RC-1 follow-up in
docs/diagnostics/2026-07-05_goryos_field_13pct_root_cause.md):

1. A blink spell flagged `reactive_only` by the deck's gameplan must
   still be enumerated and chosen PROACTIVELY on our own turn when it
   would clear a live pending EOT-exile rider on an own permanent —
   the window closes at our end step, so "hold it for protection"
   loses the body outright.
2. The blink's EV must credit the clearance, derived from the saved
   permanent's `creature_threat_value` (keeping the body past end of
   turn is worth its threat value — no new numerics).
3. Negative guard: with no pending detriment and no ETB gain, the
   reactive-only blink stays held (no forced blink).
4. Sequencing: the rider only fires at our end step, so in MAIN1 an
   attack-capable rider should swing first — the credit favours the
   post-combat (MAIN2) cast.

Class size: every delayed-EOT-exile effect × every blink spell
(Ephemerate, Teleportation-class, any future printing) — mechanic-
driven via the engine's rider registry and the oracle-derived 'blink'
tag; no card names in the implementation.
"""
from __future__ import annotations

import random

from ai.ev_evaluator import creature_threat_value, snapshot_from_game
from ai.ev_player import EVPlayer
from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.permanent_effects import PermanentEffects

DECK = "Goryo's Vengeance"       # gameplan flags its blink as reactive_only
BLINK = "Ephemerate"             # oracle-derived 'blink' tag, {W} instant
FATTY = "Griselbrand"            # reanimation target, no ETB-value trigger
WHITE_SOURCE = "Plains"


def _make_game():
    game = GameState(rng=random.Random(0))
    game.players[0].deck_name = DECK
    game.players[1].deck_name = "Boros Energy"
    game.active_player = 0
    game.current_phase = Phase.MAIN2
    return game


def _add(game, card_db, name, controller, zone):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card in DB: {name}"
    card = CardInstance(
        template=tmpl, owner=controller, controller=controller,
        instance_id=game.next_instance_id(), zone=zone,
    )
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[controller].battlefield.append(card)
    elif zone == "hand":
        game.players[controller].hand.append(card)
    elif zone == "graveyard":
        game.players[controller].graveyard.append(card)
    return card


def _setup(card_db, with_rider: bool):
    """P0: untapped white source + a blink spell in hand + a big
    creature on the battlefield — with or without a live pending
    EOT-exile rider on it."""
    game = _make_game()
    land = _add(game, card_db, WHITE_SOURCE, 0, "battlefield")
    land.tapped = False
    blink = _add(game, card_db, BLINK, 0, "hand")
    assert 'blink' in getattr(blink.template, 'tags', set()), (
        "fixture card must carry the oracle-derived 'blink' tag")

    if with_rider:
        body = _add(game, card_db, FATTY, 0, "graveyard")
        PermanentEffects.reanimate(
            game, 0, body, exile_at_eot=True, give_haste=True)
        assert body.zone == "battlefield"
        assert game._end_of_turn_exiles, "rider was not registered"
    else:
        body = _add(game, card_db, FATTY, 0, "battlefield")
        assert not game._end_of_turn_exiles

    return game, blink, body


class TestBlinkClearsPendingEotDetriment:

    def test_reactive_blink_enumerated_and_chosen_when_own_permanent_has_pending_eot_exile(
            self, card_db):
        """A reactive_only blink must fire proactively when an own
        permanent carries a live pending EOT-exile rider: the play is
        enumerated (not filtered by the reactive-only hold gate) and
        chosen as the turn's best play."""
        game, blink, body = _setup(card_db, with_rider=True)
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        decision = player.decide_main_phase(game)

        cast_names = [p.card.name for p in player._last_candidates
                      if p.action == "cast_spell"]
        assert blink.name in cast_names, (
            f"blink was not enumerated as a cast candidate despite a live "
            f"pending EOT-exile rider on an own permanent — the "
            f"reactive_only gate suppressed the deck's primary keep-the-"
            f"body line. Candidates: {cast_names}"
        )
        assert decision is not None, (
            "AI passed with a live pending EOT-exile detriment clearable "
            "by a blink in hand and mana open"
        )
        action, card, _targets = decision
        assert action == "cast_spell" and card.name == blink.name, (
            f"expected the blink cast to win the EV ranking (keeping the "
            f"body past end of turn ≈ its threat value); got "
            f"{action}: {getattr(card, 'name', card)}"
        )

    def test_blink_ev_credits_detriment_clearance_by_saved_threat_value(
            self, card_db):
        """The EV term itself: same board, same spell — the version
        where the body carries a live rider must score strictly higher,
        and the credit must be at least the saved permanent's
        creature_threat_value (derivation, not an arbitrary bonus)."""
        game_r, blink_r, body_r = _setup(card_db, with_rider=True)
        player_r = EVPlayer(player_idx=0, deck_name=DECK,
                            rng=random.Random(0))
        snap_r = snapshot_from_game(game_r, 0)
        ev_with = player_r._score_spell(
            blink_r, snap_r, game_r, game_r.players[0], game_r.players[1])

        game_n, blink_n, _body_n = _setup(card_db, with_rider=False)
        player_n = EVPlayer(player_idx=0, deck_name=DECK,
                            rng=random.Random(0))
        snap_n = snapshot_from_game(game_n, 0)
        ev_without = player_n._score_spell(
            blink_n, snap_n, game_n, game_n.players[0], game_n.players[1])

        credit = creature_threat_value(body_r, snap_r)
        assert credit > 0
        assert ev_with >= ev_without + credit, (
            f"blink EV must credit clearing a live pending EOT-exile "
            f"rider with the saved permanent's threat value "
            f"({credit:.2f}); got with-rider={ev_with:.2f} vs "
            f"no-rider={ev_without:.2f}"
        )

    def test_no_forced_blink_without_pending_detriment_or_etb_gain(
            self, card_db):
        """Negative guard: same board with NO rider and no ETB-value
        target — the reactive_only blink stays held; the AI must not
        burn it for nothing."""
        game, blink, _body = _setup(card_db, with_rider=False)
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))

        decision = player.decide_main_phase(game)

        if decision is not None:
            action, card, _targets = decision
            assert not (action == "cast_spell"
                        and card.name == blink.name), (
                "reactive_only blink was force-cast with no pending "
                "detriment to clear and no ETB value to retrigger"
            )

    def test_credit_defers_precombat_blink_when_rider_can_still_attack(
            self, card_db):
        """Sequencing: the rider fires at OUR end step, so blinking in
        MAIN1 forfeits the haste attack for zero clearance gain (the
        window stays open through MAIN2).  The MAIN1 score must be
        strictly below the MAIN2 score for the same board."""
        game, blink, body = _setup(card_db, with_rider=True)
        assert body.can_attack, "fixture rider must be attack-capable"
        player = EVPlayer(player_idx=0, deck_name=DECK,
                          rng=random.Random(0))
        me, opp = game.players[0], game.players[1]

        game.current_phase = Phase.MAIN2
        snap = snapshot_from_game(game, 0)
        ev_main2 = player._score_spell(blink, snap, game, me, opp)

        game.current_phase = Phase.MAIN1
        snap = snapshot_from_game(game, 0)
        ev_main1 = player._score_spell(blink, snap, game, me, opp)

        assert ev_main1 < ev_main2, (
            f"pre-combat blink of an attack-capable rider must score "
            f"below the post-combat cast (swing first, then keep the "
            f"body); got MAIN1={ev_main1:.2f} vs MAIN2={ev_main2:.2f}"
        )
