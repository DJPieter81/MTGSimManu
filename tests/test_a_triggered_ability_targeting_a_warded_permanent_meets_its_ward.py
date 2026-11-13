"""A triggered ability that targets an opposing permanent with ward is
countered unless its controller pays the ward cost (CR 702.21a, 603.3d).

Ward is a triggered ability of the warded permanent: "whenever this
permanent becomes the target of a spell or ability an opponent controls,
counter it unless that player pays <cost>". A triggered ability becomes
an ability with targets as it is put on the stack, so its targets meet
ward then; an unpaid ward counters the whole ability.

The defect: the trigger carriers bound a triggered ability's targets
(`effect_carrier.put_on_stack`) and resolved it without asking ward; the
only ward check on a trigger ran for a name-keyed enter handler with
cast-time targets. A cast trigger exiled a Ward {4} permanent for free.
The AI's trigger target pick likewise aimed at a warded permanent whose
ward it could not pay, a pick that only gets the ability countered.

Class: every triggered ability a carrier puts on the stack with a
target, against the pool's warded permanents. Registered: Kappa Cannoneer
(Affinity x4, Pinnacle Affinity x4), Sire of Seven Deaths (Eldrazi Ramp
x2), Colossal Skyturtle (Living End x1). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.callbacks import DefaultCallbacks
from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from tests.test_ward_framework import (
    _AlwaysPayCallbacks, _AssertNotOfferedCallbacks, _land,
)


def _game(callbacks=None):
    game = GameState(rng=random.Random(0), callbacks=callbacks)
    game.active_player = game.priority_player = 0
    game.current_phase = Phase.MAIN1
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


def _cast_colorless_spell(game, card_db, spare=0):
    """Cast a four-mana colourless spell off exactly four Wastes, with
    `spare` more mana left over: the cast triggers the exile."""
    for _ in range(4):
        _put(game, card_db, "Wastes", "battlefield", 0)
    if spare:
        _land(game, controller=0, n=spare)
    spell = _put(game, card_db, "Thought-Knot Seer", "hand", 0)
    assert game.cast_spell(0, spell, targets=[])
    while game.stack.items:
        game.resolve_stack()


def _ugin_and_warded(game, card_db):
    # "Whenever you cast a colorless spell, exile up to one target
    # permanent that's one or more colors."
    _put(game, card_db, "Ugin, Eye of the Storms", "battlefield", 0)
    return _put(game, card_db, "Kappa Cannoneer", "battlefield", 1)  # Ward {4}


# ── The rule ───────────────────────────────────────────────────────────

def test_an_unpaid_ward_counters_the_trigger_that_targets_it(card_db):
    game = _game(_AssertNotOfferedCallbacks())        # no mana for {4}
    warded = _ugin_and_warded(game, card_db)
    _cast_colorless_spell(game, card_db)
    assert warded.zone == "battlefield"
    assert any("countered by Kappa Cannoneer's ward" in line
               for line in game.log)


def test_a_paid_ward_lets_the_trigger_resolve(card_db):
    game = _game(_AlwaysPayCallbacks())
    warded = _ugin_and_warded(game, card_db)
    _cast_colorless_spell(game, card_db, spare=4)
    assert warded.zone == "exile"
    assert all(c.tapped for c in game.players[0].battlefield
               if c.template.is_land)


class _AimAtAndDecline(DefaultCallbacks):
    """Aims every trigger at one permanent and declines every payment."""

    def __init__(self, aim):
        self.aim = aim

    def choose_trigger_targets(self, game, player_idx, source, spec, req,
                               players, permanents, x_value=0):
        return [c for c in permanents if c is self.aim]

    def decide_optional_cost(self, game, player_idx, opt):
        return False


def test_a_reflexive_trigger_meets_the_ward_of_its_target(card_db):
    """The reflexive ability a parent creates ("when you do", CR 603.12)
    takes its targets as it is put on the stack: an unpaid ward counters
    it. Ajani's 0: a Cat, then damage equal to its creatures to any
    target -- here the warded creature, whose {4} its controller
    declines."""
    from tests.test_a_reflexive_trigger_follows_the_instruction_that_creates_it import (  # noqa: E501
        _add, _ajani, _game as _ajani_game)
    game = _ajani_game(card_db)
    ajani = _ajani(game, card_db)
    _add(game, card_db, "Monastery Swiftspear", 0)
    warded = _add(game, card_db, "Kappa Cannoneer", 1)
    game.callbacks = _AimAtAndDecline(warded)
    assert game.activate_planeswalker(0, ajani, "zero")
    assert warded.damage_marked == 0
    assert any("countered by Kappa Cannoneer's ward" in line
               for line in game.log)


# ── The AI's pick ──────────────────────────────────────────────────────

def test_the_ai_aims_a_trigger_away_from_a_ward_it_cannot_pay(card_db):
    from engine.game_runner import AICallbacks
    game = _game(AICallbacks())
    warded = _ugin_and_warded(game, card_db)          # worth more ...
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    _cast_colorless_spell(game, card_db)              # ... but unpayable
    assert (warded.zone, bears.zone) == ("battlefield", "exile")


# ── Auditor (CR 702.21a) ───────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_a_trigger_resolving_through_an_unpaid_ward(
        card_db, audit, monkeypatch):
    from engine import effect_carrier
    monkeypatch.setattr(effect_carrier, "_ward_gate",            # the defect
                        lambda game, source, controller, ids: (True, set()),
                        raising=False)
    game = _game(_AssertNotOfferedCallbacks())
    _ugin_and_warded(game, card_db)
    _cast_colorless_spell(game, card_db)
    assert "702.21a/ward_paid" in _violations(audit)


def test_the_audit_is_silent_when_the_ward_is_paid(card_db, audit):
    game = _game(_AlwaysPayCallbacks())
    _ugin_and_warded(game, card_db)
    _cast_colorless_spell(game, card_db, spare=4)
    assert "702.21a/ward_paid" not in _violations(audit)
