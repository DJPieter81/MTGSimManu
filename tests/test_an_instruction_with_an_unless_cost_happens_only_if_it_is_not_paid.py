"""An instruction with an "unless" cost happens only if that cost is not
paid (CR 118.12, 107.14, 701.21a).

"At the beginning of your first main phase, sacrifice this enchantment
unless you pay {E}" means "you may pay {E}; if you don't, sacrifice this
enchantment" (CR 118.12). As the trigger resolves its controller is
offered the cost; paid, the sacrifice does not happen. A cost its payer
cannot pay is not paid: paying {E} removes an energy counter, so it needs
one (CR 107.14). "Sacrifice ~" moves the ability's source, while it is
still the object the ability came from, to its owner's graveyard (CR
701.21a, 400.7), and what it exiled until it left returns (CR 610.3, unit
XL).

The defect: the grammar refused an energy "unless" cost, no evaluator
offered any "unless" cost, and no executor sacrificed an ability's own
source. Static Prison stayed on the battlefield for nothing.

Class: 17 pool cards print "sacrifice ~ unless <player> pays <cost>" (13
with a mana cost, 4 with energy). Registered: Static Prison (Boros Energy
x1). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from tests.test_ward_framework import (
    _AlwaysPayCallbacks, _AssertNotOfferedCallbacks, _NeverPayCallbacks,
)


def _game(callbacks=None, phase=Phase.MAIN1):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = game.priority_player = 0
    game.current_phase = phase
    game.turn_number = 5
    return game


def _put(game, card_db, name, zone, idx=0):
    tmpl = card_db.get_card(name)
    assert tmpl is not None, f"missing card: {name}"
    c = CardInstance(template=tmpl, owner=idx, controller=idx,
                     instance_id=game.next_instance_id(), zone=zone)
    c._game_state = game
    if zone == "battlefield":
        c.enter_battlefield()
        c.summoning_sick = False
    getattr(game.players[idx], zone).append(c)
    return c


def _step(game, step):
    from engine.turn_clock import Clock, ClockEvent, emit
    emit(game, ClockEvent(Clock.STEP_BEGINS, 0, step=step))


def _prison(game, card_db, energy):
    prison = _put(game, card_db, "Static Prison", "battlefield", 0)
    game.players[0].energy_counters = energy
    return prison


# ── The rule ───────────────────────────────────────────────────────────

def test_a_paid_unless_cost_stops_the_instruction(card_db):
    game = _game(_AlwaysPayCallbacks())
    prison = _prison(game, card_db, energy=1)
    _step(game, "main1")
    assert prison.zone == "battlefield"
    assert game.players[0].energy_counters == 0


def test_an_unpaid_unless_cost_lets_the_instruction_happen(card_db):
    game = _game(_NeverPayCallbacks())
    prison = _prison(game, card_db, energy=1)
    _step(game, "main1")
    assert prison.zone == "graveyard"
    assert game.players[0].energy_counters == 1


def test_a_cost_its_payer_cannot_pay_is_not_paid(card_db):
    game = _game(_AssertNotOfferedCallbacks())
    prison = _prison(game, card_db, energy=0)
    _step(game, "main1")
    assert prison.zone == "graveyard"


def test_a_mana_unless_cost_is_offered_the_same_way(card_db):
    # "At the beginning of your upkeep, sacrifice this creature unless you
    # pay {U}."
    for callbacks, zone in ((_AlwaysPayCallbacks(), "battlefield"),
                            (_NeverPayCallbacks(), "graveyard")):
        game = _game(callbacks, Phase.UPKEEP)
        island = _put(game, card_db, "Island", "battlefield", 0)
        drifter = _put(game, card_db, "Drifter il-Dal", "battlefield", 0)
        _step(game, "upkeep")
        assert drifter.zone == zone
        assert island.tapped == (zone == "battlefield")


def test_an_unless_cost_another_player_pays_is_never_offered_to_the_controller(
        card_db):
    # "Whenever an opponent casts a creature spell, that player loses 2
    # life unless they pay {2}": the payer is the player the trigger names
    # (CR 118.12), so the controller is never the one offered the cost.
    # The payer is left unbound by the grammar, so the host is refused.
    from engine.effect_carrier import cast_plan
    assert cast_plan(card_db.get_card("Isolation Cell").effects.faces[0]) \
        is None


def test_what_the_sacrificed_source_exiled_returns(card_db):
    game = _game(_NeverPayCallbacks())
    for _ in range(3):
        _put(game, card_db, "Plains", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    prison = _put(game, card_db, "Static Prison", "hand", 0)
    assert game.cast_spell(0, prison, targets=[])
    while game.stack.items:
        game.resolve_stack()
    assert bears.zone == "exile"
    _step(game, "main1")
    assert prison.zone == "graveyard"
    assert bears.zone == "battlefield"


# ── The AI's decision ──────────────────────────────────────────────────

def test_the_ai_pays_to_keep_what_its_source_holds_exiled(card_db):
    from engine.game_runner import AICallbacks
    game = _game(AICallbacks())
    for _ in range(3):
        _put(game, card_db, "Plains", "battlefield", 0)
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    prison = _put(game, card_db, "Static Prison", "hand", 0)
    assert game.cast_spell(0, prison, targets=[])
    while game.stack.items:
        game.resolve_stack()
    _step(game, "main1")
    assert (prison.zone, bears.zone) == ("battlefield", "exile")
    assert game.players[0].energy_counters == 1


# ── Auditor (CR 118.12) ────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_a_payment_not_taken(card_db, audit, monkeypatch):
    from engine.player_state import PlayerState
    monkeypatch.setattr(PlayerState, "spend_energy",             # the defect
                        lambda self, amount: True, raising=False)
    game = _game(_AlwaysPayCallbacks())
    _prison(game, card_db, energy=1)
    _step(game, "main1")
    assert "118.12/unless_paid" in _violations(audit)


def test_the_audit_is_silent_when_the_payment_is_taken(card_db, audit):
    game = _game(_AlwaysPayCallbacks())
    _prison(game, card_db, energy=1)
    _step(game, "main1")
    assert "118.12/unless_paid" not in _violations(audit)
