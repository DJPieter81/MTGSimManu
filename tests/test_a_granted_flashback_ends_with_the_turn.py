"""A flashback granted "until end of turn" ends with the turn, and with the
object (CR 611.2a, 514.2, 400.7).

"Each instant and sorcery card in your graveyard gains flashback until end
of turn" and "target instant or sorcery card in your graveyard gains
flashback until end of turn" grant an ability for a duration. The effect
ends in the cleanup step (CR 514.2). A card that leaves the graveyard
becomes a new object with no memory of it (CR 400.7). Printed flashback is
the card's own and never ends.

The engine's rule (`engine.cast_manager.grant_flashback`,
`end_flashback_grants`): a grant records the turn it was made. A
cleanup-clock subscriber ends every grant, and a zone change ends the
moved card's grant. A card with printed flashback keeps it.

The defect: Past in Flames and Snapcaster Mage set a permanent flag with no
duration. A card in the graveyard when Past in Flames resolved kept
flashback for the rest of the game. Ruby Storm vs Boros Energy (seed 50001,
T5): a Hex Magic milled into the graveyard on T4 was flashed back on T5,
with no Past in Flames cast that turn.

Card names are fixture carriers: each is one member of its class.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone="battlefield"):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(6):
        _add(game, card_db, "Mountain", 0)
    for p in (0, 1):
        for _ in range(4):
            _add(game, card_db, "Island", p, "library")
    return game


def _resolve_all(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def _past_in_flames(game, card_db):
    """Lightning Bolt in the graveyard, then Past in Flames resolves."""
    bolt = _add(game, card_db, "Lightning Bolt", 0, "graveyard")
    pif = _add(game, card_db, "Past in Flames", 0, "hand")
    assert game.cast_spell(0, pif)
    _resolve_all(game)
    assert bolt.has_flashback
    return bolt


def _cleanup(game):
    from engine.turn_clock import Clock, ClockEvent, emit
    emit(game, ClockEvent(Clock.CLEANUP, game.active_player))


def test_a_granted_flashback_ends_in_the_cleanup_step(card_db):
    game = _game(card_db)
    bolt = _past_in_flames(game, card_db)
    _cleanup(game)
    assert not bolt.has_flashback


def test_a_targeted_grant_ends_in_the_cleanup_step(card_db):
    """Snapcaster Mage's enter trigger: the same duration."""
    game = _game(card_db)
    for _ in range(2):
        _add(game, card_db, "Island", 0)
    bolt = _add(game, card_db, "Lightning Bolt", 0, "graveyard")
    snap = _add(game, card_db, "Snapcaster Mage", 0, "hand")
    assert game.cast_spell(0, snap)
    _resolve_all(game)
    assert bolt.has_flashback
    _cleanup(game)
    assert not bolt.has_flashback


def test_a_card_that_leaves_the_graveyard_is_a_new_object_without_the_grant(
        card_db):
    game = _game(card_db)
    bolt = _past_in_flames(game, card_db)
    game.zone_mgr.move_card(game, bolt, "graveyard", "hand", cause="test")
    game.zone_mgr.move_card(game, bolt, "hand", "graveyard", cause="test")
    assert not bolt.has_flashback


def test_printed_flashback_is_the_cards_own_and_never_ends(card_db):
    game = _game(card_db)
    looting = _add(game, card_db, "Faithless Looting", 0, "hand")
    assert game.cast_spell(0, looting)
    _resolve_all(game)
    assert looting.zone == "graveyard"
    _past_in_flames(game, card_db)
    _cleanup(game)
    assert looting.has_flashback


def test_a_card_whose_grant_ended_cannot_be_cast_from_the_graveyard(card_db):
    game = _game(card_db)
    bolt = _past_in_flames(game, card_db)
    _cleanup(game)
    game.turn_number += 1
    assert not game.can_cast(0, bolt)


# ── The auditor (CR 611.2a, 702.33a) ──────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "611.2a/flashback_grant"]


def test_the_audit_records_a_flashback_cast_on_a_grant_from_an_earlier_turn(
        card_db, audit, monkeypatch):
    from engine import cast_manager
    monkeypatch.setattr(cast_manager, "end_flashback_grants",
                        lambda game: None)                    # the defect
    game = _game(card_db)
    bolt = _past_in_flames(game, card_db)
    _cleanup(game)
    game.turn_number += 1
    from engine.constants import PLAYER_TARGET_OPPONENT
    assert game.cast_spell(0, bolt, [PLAYER_TARGET_OPPONENT])
    assert _findings(audit)


def test_the_audit_is_silent_on_a_flashback_cast_the_same_turn(card_db, audit):
    from engine.constants import PLAYER_TARGET_OPPONENT
    game = _game(card_db)
    bolt = _past_in_flames(game, card_db)
    assert game.cast_spell(0, bolt, [PLAYER_TARGET_OPPONENT])
    assert not _findings(audit)
