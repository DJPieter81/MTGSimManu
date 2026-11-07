"""A mana source the payment can tap counts toward the mana capacity.

`PlayerState.untapped_mana_capacity` and `available_mana_estimate` are the
AI's and the castability checks' view of the mana a player can spend; they
must agree with the payment owner (`ManaPayment.effective_produces_mana`).
A permanent whose mana ability is gated by a condition the payment
evaluates when it taps (metalcraft, CR 702.98) printed no static mana
fields, so the source list skipped it: the payment could tap it, the
estimate never counted it.

Affinity vs Domain Zoo s50000, game 1, turn 8: with Fiery Islet and a
Mox Opal under metalcraft (five artifacts) the AI saw 1 mana, never
activated an Engineered Explosives holding the counter that kills
Ragavan ({2}), and cast a second one instead. Card names are fixture
carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(card_db, names):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for n in names:
        card = CardInstance(template=card_db.get_card(n), owner=0,
                            controller=0, instance_id=game.next_instance_id(),
                            zone="battlefield")
        card._game_state = game
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[0].battlefield.append(card)
    return game


def test_a_source_live_under_its_condition_counts(card_db):
    game = _game(card_db, ["Fiery Islet", "Mox Opal", "Claws of Gix",
                           "Skateboard"])
    p = game.players[0]
    assert p.untapped_mana_capacity() == 2
    assert p.available_mana_estimate == 2


def test_a_source_whose_condition_fails_does_not(card_db):
    game = _game(card_db, ["Fiery Islet", "Mox Opal", "Claws of Gix"])
    p = game.players[0]
    assert p.untapped_mana_capacity() == 1


def test_the_estimate_agrees_with_what_the_payment_spends(card_db):
    """Two generic mana is payable exactly when the estimate says so."""
    from engine.mana import ManaCost
    game = _game(card_db, ["Fiery Islet", "Mox Opal", "Claws of Gix",
                           "Skateboard"])
    assert game.players[0].untapped_mana_capacity() >= 2
    assert game.tap_lands_for_mana(0, ManaCost(generic=2), card_name="x")


# ── The auditor (CR 605.1a) ──────────────────────────────────────────────

import pytest


@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f for f in audit.drain()
            if f["rule"] == "605.1a/mana_source_offered"
            and f["kind"] == "violation"]


def test_the_auditor_sees_a_live_source_left_out(card_db, audit,
                                                 monkeypatch):
    """Re-create the defect: the source list reads printed fields only."""
    from engine.player_state import PlayerState
    from engine.mana import ManaCost
    monkeypatch.setattr(PlayerState, "untapped_mana_sources", property(
        lambda self: [c for c in self.battlefield if not c.tapped and (
            c.template.is_land or c.template.mana_units
            or c.template.produces_mana)]))
    game = _game(card_db, ["Fiery Islet", "Mox Opal", "Claws of Gix",
                           "Skateboard"])
    game.tap_lands_for_mana(0, ManaCost(generic=2), card_name="x")
    assert _violations(audit)


def test_the_auditor_is_silent_when_every_live_source_is_offered(card_db,
                                                                 audit):
    from engine.mana import ManaCost
    game = _game(card_db, ["Fiery Islet", "Mox Opal", "Claws of Gix",
                           "Skateboard"])
    game.tap_lands_for_mana(0, ManaCost(generic=2), card_name="x")
    assert not _violations(audit)
