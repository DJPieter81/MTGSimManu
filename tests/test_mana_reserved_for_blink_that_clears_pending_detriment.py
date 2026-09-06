"""Casting another spell must not strand a blink that clears a rider.

The blink-clears-detriment fix made the AI *value* blinking its own
permanent off a pending end-of-turn-exile rider (Goryo's shape), but
the line's frequency stayed bounded by tap-out behavior: with the
rider live and the blink in hand, the AI would spend its last
blink-color source on a competing spell in the same main phase
(replay-evidenced: last white source spent on a discard spell right
after reanimating), so the blink could never be cast before the end
step exiled the body.

Mechanism under test: spell scoring charges a reservation penalty —
derived from the saved permanent's `creature_threat_value`, the same
primitive as the clearance credit — when casting the candidate would
make the held detriment-clearing blink unaffordable this turn. No
rider, or mana to spare, means no penalty.
"""
from __future__ import annotations

import random

from ai.ev_player import EVPlayer
from ai.ev_evaluator import snapshot_from_game
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
    if zone == "library":
        game.players[controller].library.append(card)
    else:
        getattr(game.players[controller], zone).append(card)
    return card


def _setup(card_db, with_rider=True, spare_source=False):
    """Reanimator shape: a reanimated body under a live EOT-exile
    rider, a 1-mana blink in hand, one W-capable source (plus an
    optional spare), and a competing 1-mana discard sorcery."""
    game = GameState(rng=random.Random(0))

    body = _add(game, card_db, "Archon of Cruelty", controller=0,
                zone="battlefield")
    if with_rider:
        game._end_of_turn_exiles.append(
            (body, 0, getattr(body, 'battlefield_entry_seq', 0)))

    blink = _add(game, card_db, "Ephemerate", controller=0, zone="hand")
    compete = _add(game, card_db, "Thoughtseize", controller=0, zone="hand")

    # One dual source covers either W (blink) or B (discard) — spending
    # it on the discard strands the blink.
    _gs = _add(game, card_db, "Godless Shrine", controller=0, zone="battlefield")
    _gs.tapped = False  # shocks enter tapped; model an untapped dual source
    if spare_source:
        _add(game, card_db, "Plains", controller=0, zone="battlefield")

    for _ in range(2):
        _add(game, card_db, "Mountain", controller=1, zone="battlefield")
    _add(game, card_db, "Memnite", controller=1, zone="hand")

    game.players[0].deck_name = "Goryo's Vengeance"
    game.players[1].deck_name = "Boros Energy"
    game.current_phase = Phase.MAIN1
    game.active_player = 0
    game.priority_player = 0
    game.turn_number = 3

    player = EVPlayer(player_idx=0, deck_name="Goryo's Vengeance",
                      rng=random.Random(0))
    snap = snapshot_from_game(game, 0)
    return game, player, snap, blink, compete


def test_competing_cast_charged_when_it_strands_the_blink(card_db):
    game, player, snap, blink, compete = _setup(card_db, with_rider=True)
    assert player._pending_eot_exile_riders(game), "rider must be live"

    penalty = player._blink_reservation_penalty(
        game.players[0], snap, cost=compete.template.cmc or 0,
        exclude_instance_id=compete.instance_id, game=game)
    assert penalty < 0.0, (
        f"spending the blink's last source priced at {penalty:.2f}; "
        "expected a negative reservation penalty — the planned blink's "
        "mana is not being reserved"
    )


def test_no_rider_means_no_reservation(card_db):
    game, player, snap, blink, compete = _setup(card_db, with_rider=False)

    penalty = player._blink_reservation_penalty(
        game.players[0], snap, cost=compete.template.cmc or 0,
        exclude_instance_id=compete.instance_id, game=game)
    assert penalty == 0.0


def test_spare_source_means_no_reservation(card_db):
    game, player, snap, blink, compete = _setup(card_db, with_rider=True,
                                                spare_source=True)

    penalty = player._blink_reservation_penalty(
        game.players[0], snap, cost=compete.template.cmc or 0,
        exclude_instance_id=compete.instance_id, game=game)
    assert penalty == 0.0, (
        f"got {penalty:.2f} with mana to spare — the reservation must "
        "only fire when the cast actually strands the blink"
    )
