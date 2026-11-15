"""A modal spell is cast only with modes its controller may choose (CR
601.2b-c, 700.2a).

A modal spell's modes are chosen as it is cast, then a target for each
target the chosen modes require. A mode whose targets can't be chosen
can't be chosen, and a spell with fewer modes its controller may choose
than its header asks for can't be cast. The engine knows a mode's targets
only from its typed requirements: each counted target word a clause prints
is one requirement (the effect grammar's F11). So a mode that prints a
target its typed instructions do not carry has a target the engine cannot
check, and it is never chosen.

The defect: such a mode read as needing no target. It was always legal,
the engine's completion chose it when nothing else was, and it resolved as
nothing. Untimely Malfunction ("change the target of target spell or
ability with a single target") was cast with no target at all: an illegal
cast that still counted for storm and set off cast triggers.

Class: 96 pool modal spells print 124 such modes (18 print only such
modes). Registered: Kozilek's Command (Eldrazi Tron x4, Eldrazi Ramp x4,
Broodscale Bloodchief x4), Thraben Charm (Boros Energy x2, Hollow One
sideboard x2), Untimely Malfunction (Ruby Storm sideboard x1, fetched by
Wish). Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.stack import StackItem, StackItemType


def _game():
    game = GameState(rng=random.Random(0))
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
    if zone != "stack":
        getattr(game.players[idx], zone).append(c)
    return c


def _malfunction(game, card_db):
    """Untimely Malfunction in hand with its mana: mode 0 destroys target
    artifact, mode 1 changes the target of target spell (a target the
    typed host does not carry), mode 2 stops one or two target creatures
    blocking."""
    for _ in range(2):
        _put(game, card_db, "Mountain", "battlefield", 0)
    return _put(game, card_db, "Untimely Malfunction", "hand", 0)


def _opposing_spell_on_the_stack(game, card_db):
    bolt = _put(game, card_db, "Lightning Bolt", "stack", 1)
    game.stack.push(StackItem(item_type=StackItemType.SPELL, source=bolt,
                              controller=1, targets=[], description=""))
    return bolt


# ── The mode legality owner (engine/modal_spell) ──────────────────────

def test_a_mode_that_prints_an_untyped_target_is_never_legal(card_db):
    from engine import modal_spell
    game = _game()
    for name, untyped in (("Untimely Malfunction", 1),
                          ("Kozilek's Command", 3),
                          ("Thraben Charm", 2)):
        card = _put(game, card_db, name, "hand", 0)
        assert untyped in modal_spell.untyped_target_modes(card.template)
        assert untyped not in modal_spell.legal_modes(game, card, 0, [])


def test_a_mode_with_an_untyped_target_is_not_read_as_needing_none(card_db):
    from engine import modal_spell
    from engine.target_solver import parse
    um = card_db.get_card("Untimely Malfunction")
    assert not modal_spell.has_untargeted_mode(um)
    reqs = parse(um.oracle_text)
    assert modal_spell.required_targets(um, reqs) == reqs
    # A typed mode that needs no target still does (Boros Charm's
    # "permanents you control gain indestructible").
    assert modal_spell.has_untargeted_mode(card_db.get_card("Boros Charm"))


def test_a_mode_whose_printed_target_is_untyped_is_never_chosen(card_db):
    game = _game()
    a = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    b = _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    um = _malfunction(game, card_db)
    assert game.cast_spell(0, um, targets=[a.instance_id, b.instance_id])
    assert game.stack.items[-1].modes_chosen == [2]


# ── The cast (CR 601.2b-c) ────────────────────────────────────────────

def test_a_spell_with_no_mode_its_controller_may_choose_is_not_cast(card_db):
    game = _game()
    um = _malfunction(game, card_db)
    assert not game.can_cast(0, um)
    assert not game.cast_spell(0, um, targets=[])
    assert um.zone == "hand" and game._global_storm_count == 0
    assert game.players[0].spells_cast_this_turn == 0


def test_a_spell_whose_only_open_mode_prints_an_untyped_target_is_not_cast(
        card_db):
    """Only a spell is on the stack: the one mode it could open is the
    mode whose target the engine cannot check."""
    game = _game()
    bolt = _opposing_spell_on_the_stack(game, card_db)
    um = _malfunction(game, card_db)
    assert not game.can_cast(0, um)
    assert not game.cast_spell(0, um, targets=[bolt.instance_id])
    assert um.zone == "hand" and len(game.stack.items) == 1


def test_targets_that_open_no_mode_are_not_an_announcement(card_db):
    """An artifact is in play, but the cast names no target: the spell is
    not cast with no mode, or with a mode its targets do not open."""
    game = _game()
    thopter = _put(game, card_db, "Ornithopter", "battlefield", 1)
    um = _malfunction(game, card_db)
    assert game.can_cast(0, um)
    ok = game.cast_spell(0, um, targets=[])
    if ok:
        item = game.stack.items[-1]
        assert item.modes_chosen == [0]
        assert item.targets == [thopter.instance_id]
    else:
        assert um.zone == "hand" and game._global_storm_count == 0


# ── Pins: typed modes are chosen as before ─────────────────────────────

def test_a_typed_mode_is_chosen_with_its_target(card_db):
    game = _game()
    thopter = _put(game, card_db, "Ornithopter", "battlefield", 1)
    um = _malfunction(game, card_db)
    assert game.cast_spell(0, um, targets=[thopter.instance_id])
    assert game.stack.items[-1].modes_chosen == [0]
    while game.stack.items:
        game.resolve_stack()
    assert thopter.zone == "graveyard"


def test_a_typed_mode_that_needs_no_target_still_opens_a_cast(card_db):
    game = _game()
    for _ in range(2):
        _put(game, card_db, "Mountain", "battlefield", 0)
    _put(game, card_db, "Plains", "battlefield", 0)
    charm = _put(game, card_db, "Boros Charm", "hand", 0)
    assert game.can_cast(0, charm)
    assert game.cast_spell(0, charm, targets=[])
    assert set(game.stack.items[-1].modes_chosen) <= {0, 1}


def test_a_choose_two_spell_takes_two_modes_its_controller_may_choose(
        card_db):
    from engine import modal_spell
    game = _game()
    _put(game, card_db, "Grizzly Bears", "battlefield", 1)
    for _ in range(4):
        _put(game, card_db, "Wastes", "battlefield", 0)
    command = _put(game, card_db, "Kozilek's Command", "hand", 0)
    assert game.can_cast(0, command)
    legal = modal_spell.legal_modes(game, command, 0, [])
    assert 3 not in legal and len(legal) >= 2


# ── Auditor (CR 700.2a), restated from the raw typed host ─────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _rules(audit):
    return [f["rule"] for f in audit.drain()]


def test_the_audit_records_a_mode_performed_with_an_untyped_target(
        card_db, audit, monkeypatch):
    from engine import modal_spell
    monkeypatch.setattr(modal_spell, "untyped_target_modes",
                        lambda *a, **k: frozenset())          # the defect
    game = _game()
    um = _malfunction(game, card_db)
    assert game.cast_spell(0, um, targets=[])
    while game.stack.items:
        game.resolve_stack()
    assert "700.2a/modes_chosen" in _rules(audit)


def test_the_audit_is_silent_when_a_typed_mode_is_performed(card_db, audit):
    game = _game()
    thopter = _put(game, card_db, "Ornithopter", "battlefield", 1)
    um = _malfunction(game, card_db)
    assert game.cast_spell(0, um, targets=[thopter.instance_id])
    while game.stack.items:
        game.resolve_stack()
    assert "700.2a/modes_chosen" not in _rules(audit)
