"""Damage a permanent's ability deals "to you" is dealt to its controller.

CR 109.5: "you" in an ability means that ability's controller. The lose
branch of the coin-flip transform trigger ("Whenever you cast an instant
or sorcery spell during your turn, flip a coin. If you lose the flip, ~
deals 1 damage to you.") is an instruction of the effect grammar: the
cast carrier resolves it, and its DAMAGE executor deals "to you" to the
trigger's controller through the one damage owner.

The defect: the legacy coin-flip handler (deleted when the grammar took
the flip, CR 705) dealt the damage to the permanent itself. Ruby Storm vs
Domain Zoo s52500, game 2, turn 5: at 2 life, Storm cast three spells
under Ral, lost three flips, and Ral took 3 damage and died while Storm's
life stayed at 2. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.constants import PLAYER_TARGET_OPPONENT
from engine.game_state import GameState, Phase

RAL = "Ral, Monsoon Mage // Ral, Leyline Prodigy"


class _AlwaysLose(random.Random):
    def choice(self, seq):
        return "lose" if "lose" in seq else super().choice(seq)


def _add(game, card_db, name, controller, zone="battlefield"):
    c = CardInstance(template=card_db.get_card(name), owner=controller,
                     controller=controller,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[controller], zone).append(c)
    return c


def _game(card_db):
    game = GameState(rng=_AlwaysLose(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    for _ in range(6):
        _add(game, card_db, "Mountain", 0)
    return game


def _ral(game, card_db):
    return _add(game, card_db, RAL, 0)


def _lose_flip(game, card_db):
    """Cast an instant during the controller's turn: the trigger flips,
    and the rigged coin loses."""
    bolt = _add(game, card_db, "Lightning Bolt", 0, "hand")
    assert game.cast_spell(0, bolt, [PLAYER_TARGET_OPPONENT])


def test_a_lost_flip_deals_its_damage_to_the_controller(card_db):
    game = _game(card_db)
    ral = _ral(game, card_db)
    life = game.players[0].life
    _lose_flip(game, card_db)
    assert game.players[0].life == life - 1
    assert ral.damage_marked == 0


def test_lost_flips_kill_the_controller_not_the_permanent(card_db):
    game = _game(card_db)
    ral = _ral(game, card_db)
    game.players[0].life = 2
    for _ in range(3):
        if game.game_over:
            break
        _lose_flip(game, card_db)
    game.check_state_based_actions()
    assert ral.zone == "battlefield"
    assert game.players[0].life <= 0
    assert game.game_over


# ── The auditor (CR 109.5) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f for f in audit.drain()
            if f["rule"] == "109.5/you_is_the_controller"
            and f["kind"] == "violation"]


def test_the_auditor_sees_the_damage_dealt_to_the_permanent(
        card_db, audit, monkeypatch):
    """Re-create the defect: the damage routed to the permanent."""
    from engine import damage
    real = damage.deal_damage

    def to_self(source, target, amount, **kw):
        return real(source, source, amount, **kw)
    monkeypatch.setattr(damage, "deal_damage", to_self)
    game = _game(card_db)
    _ral(game, card_db)
    _lose_flip(game, card_db)
    assert _violations(audit)


def test_the_auditor_is_silent_when_the_controller_takes_it(card_db, audit):
    game = _game(card_db)
    _ral(game, card_db)
    _lose_flip(game, card_db)
    assert not _violations(audit)
