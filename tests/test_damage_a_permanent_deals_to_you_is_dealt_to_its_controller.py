"""Damage a permanent's ability deals "to you" is dealt to its controller.

CR 109.5: "you" in an ability means that ability's controller. The lose
branch of the coin-flip transform trigger ("Whenever you cast an instant
or sorcery spell during your turn, flip a coin. If you lose the flip, ~
deals 1 damage to you.") is performed by the engine's coin-flip handler
(`oracle_resolver._handle_coin_flip_transform`), because the effect
grammar refuses "flip a coin".

The defect: the handler dealt the damage to the permanent itself (its
docstring read the clause as "deals 1 damage to it"). Ruby Storm vs
Domain Zoo s52500, game 2, turn 5: at 2 life, Storm cast three spells
under Ral, lost three flips, and Ral took 3 damage and died while Storm's
life stayed at 2. Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase

RAL = "Ral, Monsoon Mage // Ral, Leyline Prodigy"


class _AlwaysLose(random.Random):
    def choice(self, seq):
        return "lose" if "lose" in seq else super().choice(seq)


def _game():
    game = GameState(rng=_AlwaysLose(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


def _ral(game, card_db):
    c = CardInstance(template=card_db.get_card(RAL), owner=0, controller=0,
                     instance_id=game.next_instance_id(), zone="battlefield")
    c._game_state = game
    c.enter_battlefield()
    c.summoning_sick = False
    game.players[0].battlefield.append(c)
    return c


def _lose_flip(game, ral):
    from engine.oracle_resolver import _handle_coin_flip_transform
    _handle_coin_flip_transform(game, 0, ral)


def test_a_lost_flip_deals_its_damage_to_the_controller(card_db):
    game = _game()
    ral = _ral(game, card_db)
    life = game.players[0].life
    _lose_flip(game, ral)
    assert game.players[0].life == life - 1
    assert ral.damage_marked == 0


def test_lost_flips_kill_the_controller_not_the_permanent(card_db):
    game = _game()
    ral = _ral(game, card_db)
    game.players[0].life = 2
    for _ in range(3):
        _lose_flip(game, ral)
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
    game = _game()
    _lose_flip(game, _ral(game, card_db))
    assert _violations(audit)


def test_the_auditor_is_silent_when_the_controller_takes_it(card_db, audit):
    game = _game()
    _lose_flip(game, _ral(game, card_db))
    assert not _violations(audit)
