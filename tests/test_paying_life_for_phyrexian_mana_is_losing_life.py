"""Paying life for Phyrexian mana is losing that much life (CR 107.4f,
119.4).

A Phyrexian mana symbol can be paid with one mana of its colour or with 2
life (CR 107.4f). Paying life is losing that much life (CR 119.4): it
counts toward the life a player lost this turn, which spectacle and every
"if a player lost life this turn" condition read.

The defect: the cast procedure wrote the life total directly (and wrote it
back when the mana payment failed), so the payment never counted as life
lost. It now pays the mana first and then the life, through the one life
owner (`engine.damage.lose_life`), so a refused payment changes nothing.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase


def _add(game, card_db, name, controller, zone):
    card = CardInstance(template=card_db.get_card(name), owner=controller,
                        controller=controller,
                        instance_id=game.next_instance_id(), zone=zone)
    card._game_state = game
    if zone == "battlefield":
        card.enter_battlefield()
        card.summoning_sick = False
    getattr(game.players[controller], zone).append(card)
    return card


def _game(card_db, lands):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for name in lands:
        _add(game, card_db, name, 0, "battlefield")
    for _ in range(4):
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    return game


def _shot(game, card_db):
    """Gut Shot ({R/P}) with no red source: 2 life."""
    return _add(game, card_db, "Gut Shot", 0, "hand")


def test_life_paid_for_a_phyrexian_symbol_is_life_lost(card_db):
    game = _game(card_db, ["Island"])
    assert game.cast_spell(0, _shot(game, card_db), [PLAYER_TARGET_OPPONENT])
    assert game.players[0].life == 18
    assert game.players[0].life_lost_this_turn == 2


def test_a_refused_payment_changes_neither_life_nor_life_lost(
        card_db, monkeypatch):
    game = _game(card_db, ["Island"])
    shot = _shot(game, card_db)
    monkeypatch.setattr(game, "tap_lands_for_mana",
                        lambda *a, **k: False)
    game.cast_spell(0, shot, [PLAYER_TARGET_OPPONENT])
    assert game.players[0].life == 20
    assert game.players[0].life_lost_this_turn == 0


# ── The auditor (CR 119.4) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "119.4/life_paid_is_lost"]


def test_the_audit_records_life_paid_that_was_not_lost(card_db, audit,
                                                       monkeypatch):
    from engine import damage
    game = _game(card_db, ["Island"])
    shot = _shot(game, card_db)

    def write_only(g, idx, amount):                       # the defect
        g.players[idx].life -= amount
    monkeypatch.setattr(damage, "lose_life", write_only)
    assert game.cast_spell(0, shot, [PLAYER_TARGET_OPPONENT])
    assert _findings(audit)


def test_the_audit_is_silent_when_the_life_is_lost(card_db, audit):
    game = _game(card_db, ["Island"])
    assert game.cast_spell(0, _shot(game, card_db), [PLAYER_TARGET_OPPONENT])
    assert not _findings(audit)
