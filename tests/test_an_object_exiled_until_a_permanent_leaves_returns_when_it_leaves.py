"""An object exiled until a permanent leaves the battlefield returns when
that permanent leaves (CR 610.3, 610.3a, 610.3c, 400.7).

"When this creature enters, exile target creature an opponent controls
until this creature leaves the battlefield." The exile is a one-shot
effect; a second one-shot effect, created immediately after the source
leaves the battlefield by any route, returns the exiled card to the zone
it left -- to the battlefield under its owner's control, as a new object
(CR 610.3, 610.3c, 400.7). A source that has already left when the exile
would begin exiles nothing (CR 610.3a). A card that has left exile since
is a new object the return no longer finds, and a token that left the
battlefield has ceased to exist (CR 400.7, 111.8). The enter trigger takes
its target as it is put on the stack (CR 603.3d).

The defect: no enter family took the host, and the legacy parser leaves
the shape out on purpose, so the exile was never performed (Static
Prison, Banisher Priest and 60-odd others); Leyline Binding's name-keyed
handler exiled for good, with no return.

Class: 97 pool cards print an exile "until ~ leaves the battlefield"; 63
of them as an enter trigger exiling a target permanent, 50 of which the
enter carrier's linked-exile family takes (the rest wait on a target
qualifier left as residue, an instruction no family runs, or a second
enter trigger of the card no family takes). Registered: Leyline Binding
(Domain Zoo x4, 4c Omnath x4), Static Prison (Boros Energy x1; its energy
instruction waits on its own unit). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase


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


def _cast(game, card_db, name, lands, idx=0):
    for land in lands:
        _put(game, card_db, land, "battlefield", idx)
    card = _put(game, card_db, name, "hand", idx)
    assert game.cast_spell(idx, card, targets=[])
    while game.stack.items:
        game.resolve_stack()
    return card


def _priest(game, card_db):
    # "When this creature enters, exile target creature an opponent
    # controls until this creature leaves the battlefield."
    return _cast(game, card_db, "Banisher Priest", ["Plains"] * 3)


# ── The exile ──────────────────────────────────────────────────────────

def test_an_enter_trigger_exiles_its_target_until_its_source_leaves(card_db):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _priest(game, card_db)
    assert priest.zone == "battlefield"
    assert bears.zone == "exile"


def test_the_target_is_taken_as_the_trigger_is_put_on_the_stack(card_db):
    """CR 603.3d: the controller's pick among the legal choices -- here
    the AI's, the opposing creature worth most."""
    from engine.game_runner import AICallbacks
    game = _game(AICallbacks())
    small = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    big = _put(game, card_db, "Primeval Titan", "battlefield", 1)
    _priest(game, card_db)
    assert (small.zone, big.zone) == ("battlefield", "exile")


def test_with_no_legal_target_the_trigger_does_nothing(card_db):
    game = _game()
    mine = _put(game, card_db, "Grizzly Bears", "battlefield", 0)
    _priest(game, card_db)
    assert mine.zone == "battlefield"


def test_a_source_that_has_left_the_battlefield_exiles_nothing(card_db):
    """CR 610.3a: the trigger resolving after its source left."""
    from engine.effect_carrier import dispatch_etb
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _put(game, card_db, "Banisher Priest", "battlefield", 0)
    game.zone_mgr.move_card(game, priest, "battlefield", "hand")
    dispatch_etb(game, priest, 0)
    assert bears.zone == "battlefield"


# ── The return ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("dest", ["graveyard", "hand", "exile", "library"])
def test_the_exiled_permanent_returns_when_its_source_leaves_by_any_route(
        card_db, dest):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _priest(game, card_db)
    assert bears.zone == "exile"
    game.zone_mgr.move_card(game, priest, "battlefield", dest)
    assert bears.zone == "battlefield"


def test_it_returns_under_its_owners_control_as_a_new_object(card_db):
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    seq = bears.battlefield_entry_seq
    priest = _priest(game, card_db)
    game.zone_mgr.move_card(game, priest, "battlefield", "graveyard")
    assert bears in game.players[1].battlefield
    assert bears.controller == 1
    assert bears.battlefield_entry_seq == seq + 1
    assert bears.summoning_sick


def test_a_blinked_source_returns_what_it_exiled_and_exiles_anew(card_db):
    """The source leaves and a new object enters: the exiled card comes
    back, and the new object's enter trigger takes a target of its own."""
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _priest(game, card_db)
    assert bears.zone == "exile"
    game._blink_permanent(priest, 0)
    assert bears.zone == "exile"          # back, then exiled again
    game.zone_mgr.move_card(game, priest, "battlefield", "graveyard")
    assert bears.zone == "battlefield"


def test_a_card_that_left_exile_since_is_not_returned(card_db):
    """CR 400.7: once it has left exile it is a new object."""
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _priest(game, card_db)
    game.zone_mgr.move_card(game, bears, "exile", "graveyard")
    game.zone_mgr.move_card(game, bears, "graveyard", "exile")
    game.zone_mgr.move_card(game, priest, "battlefield", "graveyard")
    assert bears.zone == "exile"


def test_a_token_exiled_this_way_does_not_return(card_db):
    """CR 111.8: a token that left the battlefield has ceased to exist."""
    game = _game()
    token = game.create_token(1, "creature", power=2, toughness=2)[0]
    priest = _priest(game, card_db)
    assert token not in game.players[1].battlefield
    game.zone_mgr.move_card(game, priest, "battlefield", "graveyard")
    assert token not in game.players[1].battlefield


# ── The carrier and the class ──────────────────────────────────────────

def test_the_enter_carrier_takes_a_host_holding_the_linked_exile(card_db):
    from engine.effect_carrier import etb_plan
    for name in ("Banisher Priest", "Leyline Binding", "Prayer of Binding"):
        plan = etb_plan(card_db.get_card(name).effects.faces[0])
        assert plan is not None and [f for _, f in plan] == ["enter_exile"]


def test_a_host_with_an_instruction_the_family_does_not_run_is_refused(
        card_db):
    # Static Prison: "... You get {E}{E}." -- no executor gives energy yet.
    from engine.effect_carrier import etb_plan
    assert etb_plan(card_db.get_card("Static Prison").effects.faces[0]) \
        is None


def test_the_registered_member_returns_what_it_exiled(card_db):
    """Leyline Binding: the typed enter trigger, not a handler that
    exiled for good."""
    from engine.card_effects import EFFECT_REGISTRY, EffectTiming
    assert not EFFECT_REGISTRY.has_handler("Leyline Binding",
                                           EffectTiming.ETB)
    game = _game()
    bears = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    binding = _cast(game, card_db, "Leyline Binding",
                    ["Plains", "Island", "Swamp", "Mountain", "Forest",
                     "Plains"])
    assert bears.zone == "exile"
    game.zone_mgr.move_card(game, binding, "battlefield", "graveyard")
    assert bears.zone == "battlefield"


# ── Auditor (CR 610.3) ─────────────────────────────────────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _violations(audit):
    return [f["rule"] for f in audit.drain() if f["kind"] == "violation"]


def test_the_audit_records_an_exiled_card_left_in_exile(card_db, audit,
                                                        monkeypatch):
    from engine.zone_manager import ZoneManager
    monkeypatch.setattr(ZoneManager, "_return_linked",           # the defect
                        lambda self, game, key: None, raising=False)
    game = _game()
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _priest(game, card_db)
    game.zone_mgr.move_card(game, priest, "battlefield", "graveyard")
    assert "610.3/linked_return" in _violations(audit)


def test_the_audit_is_silent_when_the_card_returns(card_db, audit):
    game = _game()
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    priest = _priest(game, card_db)
    game.zone_mgr.move_card(game, priest, "battlefield", "graveyard")
    assert "610.3/linked_return" not in _violations(audit)
