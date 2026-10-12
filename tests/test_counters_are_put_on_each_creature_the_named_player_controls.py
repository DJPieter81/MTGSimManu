"""Counters are put on each creature the named player controls (CR 122.1,
608.2c).

"Put N +1/+1 counter(s) on each [other] creature <you control | target
player controls>" puts that many counters on every creature that player
controls as it resolves -- through the one counter owner
(`CardInstance.add_plus_counters`, which applies the counter
replacements). A target player is chosen as the spell is cast (CR
601.2c, 115.1).

The defect: the dispatcher had no counters executor, and no legacy path
read the shape on a spell, so Inspiring Roar resolved as nothing and
Practiced Offense (Domain Zoo and Hollow One main deck x2) put no
counters at all.

Card names are fixture carriers: 124 pool cards put +1/+1 counters on
each creature a player controls.
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
    for _ in range(4):
        _add(game, card_db, "Plains", 0)
        _add(game, card_db, "Island", 0, "library")
        _add(game, card_db, "Island", 1, "library")
    mine = [_add(game, card_db, "Grizzly Bears", 0) for _ in range(2)]
    theirs = [_add(game, card_db, "Grizzly Bears", 1)]
    return game, mine, theirs


def _resolve(game):
    while not game.stack.is_empty:
        game.resolve_stack()


def test_each_creature_you_control_gets_a_counter(card_db):
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert [c.plus_counters for c in mine] == [1, 1]
    assert [c.plus_counters for c in theirs] == [0]


def test_the_counters_go_through_the_counter_owner(card_db, monkeypatch):
    """A counter replacement (CR 614.1) applies: the owner is called once
    per creature, with the printed amount."""
    calls = []
    real = CardInstance.add_plus_counters

    def spy(self, n, game=None, source=None):
        calls.append((self.instance_id, n))
        return real(self, n, game, source)
    monkeypatch.setattr(CardInstance, "add_plus_counters", spy)
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert sorted(calls) == sorted((c.instance_id, 1) for c in mine)


# ── The auditor (CR 122.1) ────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain() if f["rule"] == "122.1/counters_placed"]


def test_the_audit_records_a_creature_left_without_its_counter(
        card_db, audit, monkeypatch):
    monkeypatch.setattr(CardInstance, "add_plus_counters",
                        lambda self, n, game=None, source=None: None)
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert _findings(audit)


def test_the_audit_is_silent_when_every_creature_gets_its_counter(card_db,
                                                                  audit):
    game, mine, theirs = _game(card_db)
    roar = _add(game, card_db, "Inspiring Roar", 0, "hand")
    assert game.cast_spell(0, roar, [])
    _resolve(game)
    assert not _findings(audit)
