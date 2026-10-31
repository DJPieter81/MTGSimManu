"""A target requirement keeps the qualifiers it prints (CR 115.1, 601.2c).

"Target nonbasic land" admits only a land that is not basic; "target card
in a graveyard other than a basic land card" admits any card in a
graveyard but a basic land; "target <type> you control" and "target
<type> an opponent controls" admit only one controller's objects. The
legality owner (`engine.target_solver`) read the type word and dropped the
qualifiers around it: Field of Ruin and Demolition Field's "destroy target
nonbasic land an opponent controls" admitted the caster's own basics,
Surgical Extraction admitted a basic land card, and the "you control" /
"an opponent controls" of 127 single-type and 102 permanent targets
admitted either side (Leyline Binding, Domain Zoo and 4c Omnath main deck
x4, could exile its caster's own permanent).

The rules auditor restates the qualifiers from the requirement's printed
phrase, not from the parsed fields.

Card names are fixture carriers.
"""
from __future__ import annotations

import random

import pytest

from engine.cards import CardInstance
from engine.game_state import GameState, Phase
from engine.target_solver import enumerate_legal_targets, parse


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


def _game():
    game = GameState(rng=random.Random(0))
    game.active_player = 0
    game.current_phase = Phase.MAIN1
    return game


# ── The parse ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, supertype, scope", [
    ("Destroy target nonbasic land.", "nonbasic", "any"),
    ("Destroy target nonbasic land an opponent controls.", "nonbasic",
     "opponent"),
    ("Return target land you control to its owner's hand.", None, "you"),
    ("Destroy target artifact an opponent controls.", None, "opponent"),
    ("Exile target nonland permanent an opponent controls.", None,
     "opponent"),
    ("Return target permanent you control to its owner's hand.", None,
     "you"),
    ("Destroy target land.", None, "any"),
])
def test_a_single_type_target_keeps_its_supertype_and_controller(
        text, supertype, scope):
    (req,) = parse(text)
    assert (req.supertype, req.owner_scope) == (supertype, scope)


def test_other_than_a_basic_land_card_excludes_basic_lands(card_db):
    (req,) = parse(card_db.get_card("Surgical Extraction").oracle_text)
    assert req.zone == "graveyard" and req.supertype == "nonbasic"


# ── The legality owner ────────────────────────────────────────────────

def test_a_nonbasic_land_an_opponent_controls_is_the_only_choice(card_db):
    game = _game()
    _add(game, card_db, "Forest", 1)
    pool = _add(game, card_db, "Breeding Pool", 1)
    _add(game, card_db, "Stomping Ground", 0)
    (req,) = parse("Destroy target nonbasic land an opponent controls.")
    assert enumerate_legal_targets(game, 0, req) == [pool]


def test_a_basic_land_card_in_a_graveyard_is_no_target(card_db):
    game = _game()
    _add(game, card_db, "Forest", 1, "graveyard")
    bolt = _add(game, card_db, "Lightning Bolt", 1, "graveyard")
    surgical = _add(game, card_db, "Surgical Extraction", 0, "hand")
    (req,) = parse(surgical.template.oracle_text)
    assert enumerate_legal_targets(game, 0, req, exclude=surgical,
                                   source=surgical) == [bolt]


def test_a_cast_at_a_basic_land_card_is_refused(card_db):
    game = _game()
    _add(game, card_db, "Swamp", 0)
    forest = _add(game, card_db, "Forest", 1, "graveyard")
    surgical = _add(game, card_db, "Surgical Extraction", 0, "hand")
    assert not game.cast_spell(0, surgical, [forest.instance_id])


# ── The auditor (CR 601.2c, from the printed phrase) ──────────────────

@pytest.fixture
def audit(monkeypatch):
    from engine import rules_audit
    monkeypatch.setenv("MTG_RULES_AUDIT", "1")
    rules_audit.reset()
    yield rules_audit
    rules_audit.reset()


def _findings(audit):
    return [f for f in audit.drain()
            if f["rule"] == "601.2c/cast_target_admitted"]


def test_the_audit_records_a_basic_land_card_cast_at(card_db, audit,
                                                     monkeypatch):
    from engine.cast_manager import CastManager
    game = _game()
    _add(game, card_db, "Swamp", 0)
    forest = _add(game, card_db, "Forest", 1, "graveyard")
    _add(game, card_db, "Lightning Bolt", 1, "graveyard")    # castable
    surgical = _add(game, card_db, "Surgical Extraction", 0, "hand")
    monkeypatch.setattr(CastManager, "targets_admitted",
                        staticmethod(lambda *a, **k: True))   # the defect
    monkeypatch.setattr(CastManager, "announced_targets",
                        staticmethod(lambda g, i, c, t: list(t or [])))
    assert game.cast_spell(0, surgical, [forest.instance_id])
    assert _findings(audit)


def test_the_audit_is_silent_for_a_nonbasic_card(card_db, audit):
    game = _game()
    _add(game, card_db, "Swamp", 0)
    bolt = _add(game, card_db, "Lightning Bolt", 1, "graveyard")
    surgical = _add(game, card_db, "Surgical Extraction", 0, "hand")
    assert game.cast_spell(0, surgical, [bolt.instance_id])
    assert not _findings(audit)


def test_the_audit_reads_the_caster_not_the_cards_owner(card_db, audit):
    """A card cast under another player's permission (an impulse, Ragavan's
    exile) is controlled by its caster: "target creature you control" is
    the caster's creature, whoever owns the card."""
    game = _game()
    _add(game, card_db, "Plains", 0)
    mine = _add(game, card_db, "Grizzly Bears", 0)
    theirs = CardInstance(template=card_db.get_card("Ephemerate"), owner=1,
                          controller=1, instance_id=game.next_instance_id(),
                          zone="hand")
    theirs._game_state = game
    game.players[0].hand.append(theirs)
    assert game.cast_spell(0, theirs, [mine.instance_id])
    assert not _findings(audit)
