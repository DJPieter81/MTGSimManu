"""An artifact that taps for mana pays a cost one way, not two.

Improvise (CR 702.126a) lets its caster tap an untapped artifact to pay
{1} of a spell's generic cost; an artifact's mana ability (CR 605.1a) also
taps it. Either way the artifact is tapped once, so it pays one mana, not
two.

`CastManager.can_cast` counted every untapped artifact toward improvise
on top of a mana total that already held the artifacts that tap for mana,
and the payment could tap a mana rock for improvise and lose its coloured
mana. A cast that could not be paid was attempted: the payment tapped five
artifacts for improvise, then failed, and they stayed tapped (Boros Energy
vs Affinity s50000, turn 3: Kappa Cannoneer). Card names are fixture
carriers.
"""
from __future__ import annotations

import random

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


def _game(card_db, board, hand="Kappa Cannoneer"):
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for n in board:
        card = CardInstance(template=card_db.get_card(n), owner=0,
                            controller=0, instance_id=game.next_instance_id(),
                            zone="battlefield")
        card._game_state = game
        card.enter_battlefield()
        card.summoning_sick = False
        game.players[0].battlefield.append(card)
    spell = CardInstance(template=card_db.get_card(hand), owner=0,
                         controller=0, instance_id=game.next_instance_id(),
                         zone="hand")
    spell._game_state = game
    game.players[0].hand.append(spell)
    return game, spell


# Island + Mind Stone make 2; three more artifacts improvise 3: 5 < 6.
SHORT = ["Island", "Mind Stone", "Claws of Gix", "Claws of Gix",
         "Skateboard"]
# One more artifact makes it 6.
ENOUGH = SHORT + ["Ornithopter"]


def test_a_mana_rock_is_not_counted_again_for_improvise(card_db):
    game, spell = _game(card_db, SHORT)
    assert not game.can_cast(0, spell)


def test_enough_with_every_artifact_counted_once(card_db):
    game, spell = _game(card_db, ENOUGH)
    assert game.can_cast(0, spell)
    assert game.cast_spell(0, spell)


def test_the_rock_pays_its_mana_and_the_others_improvise(card_db):
    """With exactly enough, the payment taps the rock for its mana and the
    remaining artifacts for improvise: everything ends tapped, nothing
    is left unpaid."""
    game, spell = _game(card_db, ENOUGH)
    assert game.cast_spell(0, spell)
    assert all(c.tapped for c in game.players[0].battlefield)


def test_a_cast_that_cannot_be_paid_taps_nothing(card_db):
    game, spell = _game(card_db, SHORT)
    game.cast_spell(0, spell)
    assert not any(c.tapped for c in game.players[0].battlefield)


# ── The auditor (CR 702.126a) ────────────────────────────────────────────

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
            if f["rule"] == "702.126a/improvise_pays_once"
            and f["kind"] == "violation"]


def test_the_auditor_sees_a_mana_rock_tapped_for_improvise(card_db, audit,
                                                          monkeypatch):
    """Re-create the defect: the payment does not know the rock taps for
    mana, so improvise taps it."""
    from engine.player_state import PlayerState
    real = PlayerState.untapped_mana_sources
    monkeypatch.setattr(PlayerState, "untapped_mana_sources", property(
        lambda self: [c for c in real.fget(self) if c.template.is_land]))
    game, spell = _game(card_db, ENOUGH + ["Ornithopter"])
    game.cast_spell(0, spell)
    assert _violations(audit)


def test_the_auditor_is_silent_when_each_artifact_pays_once(card_db, audit):
    game, spell = _game(card_db, ENOUGH)
    game.cast_spell(0, spell)
    assert not _violations(audit)
